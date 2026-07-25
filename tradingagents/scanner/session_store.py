"""Analysis-session records shared by every trigger path (dashboard_plan_v1.md §4).

An "analysis session" is one trade-decision run — single ticker or a multi-ticker
comparison — stored as ``data/dashboard/analyses/<id>.json`` with an optional
``<id>.jsonl`` transcript beside it. The dashboard's Result/History/Log tabs read
this directory and nothing else, so anything that writes a session here shows up
there regardless of how it was triggered: the web button (headless ``claude -p``),
a Telegram message, or a plain CLI run.

This module is that single touchpoint. ``scripts/build_dashboard.py`` uses it for
the web path; ``scripts/session_log.py`` exposes it as a CLI for the others.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANALYSES_DIR = os.path.join(_REPO_ROOT, "data", "dashboard", "analyses")

VALID_TYPES = ("single", "compare")
VALID_STATUSES = ("queued", "running", "done", "error")


def new_session_id(tickers: list[str]) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "-".join(t.lower() for t in tickers[:3])
    return f"{ts}-{slug}"


def session_path(session_id: str) -> str:
    return os.path.join(ANALYSES_DIR, f"{session_id}.json")


def transcript_path(session_id: str) -> str:
    return os.path.join(ANALYSES_DIR, f"{session_id}.jsonl")


def transcript_rel(session_id: str) -> str:
    """Path as stored in the session record — relative to data/dashboard/."""
    return f"analyses/{session_id}.jsonl"


def save_session(session: dict) -> None:
    os.makedirs(ANALYSES_DIR, exist_ok=True)
    with open(session_path(session["id"]), "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False, indent=2)


def get_session(session_id: str) -> dict | None:
    path = session_path(session_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def list_sessions(limit: int = 50) -> list[dict]:
    files = glob.glob(os.path.join(ANALYSES_DIR, "*.json"))
    files.sort(key=os.path.getmtime, reverse=True)
    out = []
    for f in files[:limit]:
        try:
            with open(f, encoding="utf-8") as fh:
                out.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def build_session(
    tickers: list[str],
    *,
    session_id: str | None = None,
    type_: str | None = None,
    source: str = "dashboard",
    requested_by: str = "web",
    requested_at: str | None = None,
    completed_at: str | None = None,
    status: str = "done",
    decisions: dict | None = None,
    report_markdown: str = "",
    transcript: bool = False,
    error: str | None = None,
) -> dict:
    """Assemble a §4.1-shaped record. Keeping the shape in one place is the point:
    every writer goes through here, so the dashboard never meets a stray schema."""
    session_id = session_id or new_session_id(tickers)
    return {
        "id": session_id,
        "type": type_ or ("compare" if len(tickers) > 1 else "single"),
        "tickers": tickers,
        "source": source,
        "requested_by": requested_by,
        "requested_at": requested_at,
        "completed_at": completed_at,
        "status": status,
        "decisions": decisions if decisions is not None else {},
        "report_markdown": report_markdown,
        "transcript_path": transcript_rel(session_id) if transcript else None,
        "error": error,
    }


def session_decisions(tickers: list[str], since: str | None = None) -> dict:
    """Journal decisions (decision+conviction) per ticker logged by *this* session.

    ``since`` is the run's start timestamp: only entries logged at/after it count.
    Without that cutoff a run that logged nothing would silently inherit an old
    verdict from a previous day and badge the session with it.
    """
    try:
        from tradingagents.scanner import journal
        entries = [e for e in journal.load_entries() if e.get("type") == "decision"]
    except Exception:  # noqa: BLE001
        return {}
    if since:
        entries = [e for e in entries if (e.get("logged_at") or "") >= since]
    entries.sort(key=lambda e: e.get("logged_at", ""))
    out = {}
    for e in entries:
        if e.get("ticker") in tickers:
            out[e["ticker"]] = {"decision": e.get("decision"), "conviction": e.get("conviction")}
    return out


# ---- transcript (§4.2) ----
def write_event(fh, ev: dict) -> None:
    """One transcript line: ``{"ts": <wall clock>, "ev": <raw event verbatim>}``.

    The wrapper only adds a timestamp — the events themselves carry none — so the
    file stays a faithful record. Never raises: a logging hiccup must not take
    down the analysis it is logging.
    """
    try:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "ev": ev},
                            ensure_ascii=False) + "\n")
    except (OSError, TypeError, ValueError):
        pass


def read_transcript(session_id: str) -> list[dict] | None:
    """Distil a saved transcript into a timeline the log tab can render directly.

    Raw stream-json is verbose (full tool inputs/outputs, several KB per event),
    so the parsing happens here and the browser only ever sees the summary.
    """
    path = transcript_path(session_id)
    if not os.path.exists(path):
        return None
    pending: dict[str, dict] = {}   # tool_use_id -> timeline item awaiting its result
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts, ev = rec.get("ts"), rec.get("ev") or {}
            etype = ev.get("type")

            if etype == "dashboard_meta":
                out.append({"ts": ts, "kind": "prompt", "title": "요청 프롬프트",
                            "body": ev.get("prompt", "")})
                continue
            if etype == "assistant":
                for block in (ev.get("message") or {}).get("content", []):
                    btype = block.get("type")
                    if btype == "text":
                        text = (block.get("text") or "").strip()
                        if text:
                            out.append({"ts": ts, "kind": "text", "title": "에이전트 메시지",
                                        "body": clip(text, 1200)})
                    elif btype == "tool_use":
                        item = {"ts": ts, "kind": "tool", "title": block.get("name", "?"),
                                "body": tool_summary(block.get("name", ""), block.get("input") or {}),
                                "status": "running", "result": ""}
                        tid = block.get("id")
                        if tid:
                            pending[tid] = item
                        out.append(item)
            elif etype == "user":
                for block in (ev.get("message") or {}).get("content", []):
                    if block.get("type") != "tool_result":
                        continue
                    item = pending.pop(block.get("tool_use_id"), None)
                    if item is None:
                        continue
                    item["status"] = "error" if block.get("is_error") else "ok"
                    item["result"] = clip(_flatten_content(block.get("content")), 600)
            elif etype == "result":
                out.append({"ts": ts, "kind": "result", "title": "최종 응답",
                            "body": clip(ev.get("result") or "", 2000)})
    for item in pending.values():   # never got a result (killed / timed out)
        item["status"] = "unknown"
    return out


def clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + f"\n… (+{len(text) - limit}자 생략)"


def _flatten_content(content) -> str:
    """tool_result content is either a string or a list of content blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


def tool_summary(name: str, tool_input: dict) -> str:
    """One-line 'what was actually called', per tool shape."""
    if name == "Bash":
        return tool_input.get("command", "")
    if name == "Agent":
        return f"{tool_input.get('subagent_type', '?')} — {tool_input.get('description', '')}"
    if name == "Skill":
        return f"/{tool_input.get('skill', '?')} {tool_input.get('args', '')}".strip()
    for key in ("file_path", "pattern", "path", "url", "query"):
        if tool_input.get(key):
            return f"{key}={tool_input[key]}"
    return clip(json.dumps(tool_input, ensure_ascii=False), 200)
