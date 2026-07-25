#!/usr/bin/env python3
"""Build (or live-serve) the market-scanner monitoring dashboard.

Reads the latest scan JSON from data/scans/ plus the journal scorecard and
renders a self-contained HTML dashboard (scripts/dashboard_template.html with the
data inlined — no external assets, opens by double-click).

    # one-shot: write data/dashboard/dashboard.html
    python scripts/build_dashboard.py

    # live monitor: serve on http://localhost:8787, regenerate each request,
    # page auto-refreshes so a scanner cron keeps it current
    python scripts/build_dashboard.py --serve --port 8787 --refresh 60

Pair with the scanner on a schedule (see README → Continuous scanning); each new
scan_*.json is picked up automatically.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

SCANS_DIR = os.path.join(_REPO_ROOT, "data", "scans")
DASH_DIR = os.path.join(_REPO_ROOT, "data", "dashboard")
ANALYSES_DIR = os.path.join(_REPO_ROOT, "data", "dashboard", "analyses")
TEMPLATE = os.path.join(_REPO_ROOT, "scripts", "dashboard_template.html")
_MARKER = "/*__DATA__*/ null"

# ---- background analysis job runner (dashboard_plan_v1.md Phase 1-2) ----
_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")
_MAX_COMPARE_TICKERS = 6
_JOB_CONCURRENCY = 2          # §7.2 default
_DAILY_TRIGGER_CAP = 20       # §7.2 default (weighted by ticker count for compare jobs)
_JOB_TIMEOUT_SEC = 1800
_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict] = {}   # job_key (ticker or session_id) -> {status, stage_*, agents_*, session_id}
_JOB_SEMAPHORE = threading.Semaphore(_JOB_CONCURRENCY)
_DAILY_LOCK = threading.Lock()
_DAILY_COUNT = {"date": None, "count": 0}

# Real, observable stages of the trade-decision pipeline (verified against actual
# stream-json event shapes, not guessed): analyst Agent dispatch -> all analysts
# returned -> journal.py decision Bash call(s) seen. Each transition is driven by
# a concrete event, not a time estimate. Same 4 stages for single or compare
# (compare just has agents_total/expected journal calls scaled by ticker count).
_ANALYST_NAMES = ("fundamental-analyst", "technical-analyst", "news-analyst")
_STAGE_LABELS = [
    "요청 접수",
    "펀더멘털·기술·뉴스 분석 중",
    "분석 결과 종합 및 결정 중",
    "저널 기록 중",
]


def _daily_cap_ok(weight: int = 1) -> bool:
    today = datetime.now().strftime("%Y-%m-%d")
    with _DAILY_LOCK:
        if _DAILY_COUNT["date"] != today:
            _DAILY_COUNT["date"] = today
            _DAILY_COUNT["count"] = 0
        if _DAILY_COUNT["count"] + weight > _DAILY_TRIGGER_CAP:
            return False
        _DAILY_COUNT["count"] += weight
        return True


# ---- session persistence (dashboard_plan_v1.md §4.1) ----
def _new_session_id(tickers: list[str]) -> str:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "-".join(t.lower() for t in tickers[:3])
    return f"{ts}-{slug}"


def _session_path(session_id: str) -> str:
    return os.path.join(ANALYSES_DIR, f"{session_id}.json")


def save_session(session: dict) -> None:
    os.makedirs(ANALYSES_DIR, exist_ok=True)
    with open(_session_path(session["id"]), "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False, indent=2)


def get_session(session_id: str) -> dict | None:
    path = _session_path(session_id)
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


def _session_decisions(tickers: list[str], since: str | None = None) -> dict:
    """Journal decisions (decision+conviction) per ticker logged by *this* session.

    ``since`` is the job's start timestamp: only entries logged at/after it count.
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


def _job_env() -> dict:
    """Environment for the headless `claude` child: repo venv first on PATH.

    The analysts are pre-approved for `python scripts/market_data.py *` (§7.1), but
    a plain login shell here has no `python` at all and the system `python3` lacks
    the scanner deps. Prepending `.venv/bin` makes the *already allow-listed*
    command both resolvable and dependency-complete — no widening of the allowlist.
    """
    env = dict(os.environ)
    venv_bin = os.path.join(_REPO_ROOT, ".venv", "bin")
    if os.path.isdir(venv_bin):
        env["PATH"] = venv_bin + os.pathsep + env.get("PATH", "")
        env["VIRTUAL_ENV"] = os.path.dirname(venv_bin)
    return env


def _set_stage(job_key: str, stage_index: int, agents_total: int, agents_done: int) -> None:
    with _JOBS_LOCK:
        if job_key in _JOBS and _JOBS[job_key].get("status") == "running":
            _JOBS[job_key].update({
                "stage_index": stage_index,
                "stage_total": len(_STAGE_LABELS),
                "stage_label": _STAGE_LABELS[stage_index - 1],
                "agents_done": agents_done,
                "agents_total": agents_total,
            })


def _run_job(job_key: str, session_id: str, tickers: list[str], mode: str) -> None:
    with _JOB_SEMAPHORE:
        agents_total = len(tickers) * len(_ANALYST_NAMES)
        started_at = datetime.now().isoformat(timespec="seconds")
        with _JOBS_LOCK:
            _JOBS[job_key].update({
                "status": "running", "session_id": session_id,
                "stage_index": 1, "stage_total": len(_STAGE_LABELS), "stage_label": _STAGE_LABELS[0],
                "agents_done": 0, "agents_total": agents_total,
            })
        save_session({
            "id": session_id, "type": mode, "tickers": tickers,
            "source": "dashboard", "requested_by": "web",
            "requested_at": started_at,
            "completed_at": None, "status": "running",
            "decisions": {}, "report_markdown": "", "transcript_path": None, "error": None,
        })

        if mode == "single":
            prompt = (
                f"{tickers[0]} 종목을 오늘 날짜 기준으로 trade-decision 스킬을 사용해 전체 분석하고, "
                f"최종 BUY/HOLD/SELL 결정을 trade-journal 스킬로 저널에 기록해줘. "
                f"저널 note 필드는 반드시 한국어로 작성해줘(영문 약어나 티커/숫자는 그대로 둬도 됨)."
            )
        else:
            prompt = (
                f"{', '.join(tickers)} 종목을 trade-decision으로 각각 오늘 날짜 기준 전체 분석하고, "
                f"종목 간 비교 분석(상대 매력도, 확신도 순위, 핵심 차별점)을 마크다운으로 정리해줘. "
                f"각 종목의 최종 BUY/HOLD/SELL 결정을 trade-journal 스킬로 저널에 기록해줘. "
                f"모든 응답과 저널 note 필드는 반드시 한국어로 작성해줘."
            )

        # Stream stdout+stderr merged (stream-json, one JSON object per line) so
        # /api/status can report real progress. Verified event shapes: {"type":
        # "assistant","message":{"content":[{"type":"tool_use","id":...,"name":
        # "Agent"/"Bash",...}]}} and {"type":"user","message":{"content":[{"type":
        # "tool_result","tool_use_id":...}]}}, and {"type":"result","result":"<full
        # final text>"} as the last line.
        agent_ids_dispatched: set[str] = set()
        agent_ids_done: set[str] = set()
        journal_calls = 0
        stage_index = 1
        raw_tail: list[str] = []
        report_text = ""
        proc = None
        try:
            proc = subprocess.Popen(
                ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"],
                cwd=_REPO_ROOT,
                env=_job_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            killer = threading.Thread(
                target=lambda: (time.sleep(_JOB_TIMEOUT_SEC), proc.poll() is None and proc.kill()),
                daemon=True,
            )
            killer.start()

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                raw_tail.append(line)
                if len(raw_tail) > 40:
                    raw_tail.pop(0)
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue

                etype = ev.get("type")
                if etype == "assistant":
                    for block in (ev.get("message") or {}).get("content", []):
                        if block.get("type") != "tool_use":
                            continue
                        name = block.get("name", "")
                        blob = json.dumps(block.get("input", {}), ensure_ascii=False)
                        if name == "Agent" and any(a in blob for a in _ANALYST_NAMES):
                            agent_ids_dispatched.add(block.get("id", ""))
                            if stage_index < 2:
                                stage_index = 2
                        elif name == "Bash" and "journal.py decision" in blob:
                            journal_calls += 1
                elif etype == "user":
                    for block in (ev.get("message") or {}).get("content", []):
                        if block.get("type") != "tool_result":
                            continue
                        tid = block.get("tool_use_id")
                        if tid in agent_ids_dispatched:
                            agent_ids_done.add(tid)
                elif etype == "result":
                    report_text = ev.get("result") or report_text

                if stage_index == 2 and len(agent_ids_done) >= agents_total:
                    stage_index = 3
                if journal_calls >= len(tickers):
                    stage_index = 4

                _set_stage(job_key, stage_index, agents_total, len(agent_ids_done))

            returncode = proc.wait()
            finished_at = datetime.now().isoformat(timespec="seconds")
            if returncode != 0:
                detail = "\n".join(raw_tail).strip() or "(출력 없음)"
                error_msg = f"exit {returncode}: {detail}"[-800:]
                with _JOBS_LOCK:
                    _JOBS[job_key] = {"status": "error", "error": error_msg, "finished_at": finished_at, "session_id": session_id}
                save_session({
                    "id": session_id, "type": mode, "tickers": tickers,
                    "source": "dashboard", "requested_by": "web",
                    "requested_at": started_at, "completed_at": finished_at, "status": "error",
                    "decisions": {}, "report_markdown": report_text, "transcript_path": None,
                    "error": error_msg,
                })
            else:
                with _JOBS_LOCK:
                    _JOBS[job_key] = {"status": "done", "finished_at": finished_at, "session_id": session_id}
                save_session({
                    "id": session_id, "type": mode, "tickers": tickers,
                    "source": "dashboard", "requested_by": "web",
                    "requested_at": started_at, "completed_at": finished_at, "status": "done",
                    "decisions": _session_decisions(tickers, since=started_at),
                    "report_markdown": report_text, "transcript_path": None, "error": None,
                })
        except Exception as exc:  # noqa: BLE001
            if proc is not None and proc.poll() is None:
                proc.kill()
            finished_at = datetime.now().isoformat(timespec="seconds")
            with _JOBS_LOCK:
                _JOBS[job_key] = {"status": "error", "error": str(exc), "finished_at": finished_at, "session_id": session_id}
            save_session({
                "id": session_id, "type": mode, "tickers": tickers,
                "source": "dashboard", "requested_by": "web",
                "requested_at": started_at, "completed_at": finished_at, "status": "error",
                "decisions": {}, "report_markdown": report_text, "transcript_path": None,
                "error": str(exc),
            })


def start_analysis(ticker: str) -> dict:
    ticker = (ticker or "").strip().upper()
    if not _TICKER_RE.match(ticker):
        return {"status": "error", "error": "invalid ticker"}
    with _JOBS_LOCK:
        existing = _JOBS.get(ticker)
        if existing and existing.get("status") in ("queued", "running"):
            return dict(existing)
        if not _daily_cap_ok(weight=1):
            return {"status": "error", "error": "일일 트리거 한도(20건)를 초과했습니다"}
        started_at = datetime.now().isoformat(timespec="seconds")
        session_id = _new_session_id([ticker])
        _JOBS[ticker] = {"status": "queued", "started_at": started_at, "session_id": session_id}
    threading.Thread(target=_run_job, args=(ticker, session_id, [ticker], "single"), daemon=True).start()
    with _JOBS_LOCK:
        return dict(_JOBS[ticker])


def start_compare(tickers_raw: str) -> dict:
    tickers = []
    for t in (tickers_raw or "").split(","):
        t = t.strip().upper()
        if t and _TICKER_RE.match(t) and t not in tickers:
            tickers.append(t)
    if len(tickers) < 2:
        return {"status": "error", "error": "비교하려면 종목을 2개 이상 선택해줘"}
    if len(tickers) > _MAX_COMPARE_TICKERS:
        return {"status": "error", "error": f"한 번에 최대 {_MAX_COMPARE_TICKERS}개까지 비교할 수 있어"}
    session_id = _new_session_id(tickers)
    with _JOBS_LOCK:
        if not _daily_cap_ok(weight=len(tickers)):
            return {"status": "error", "error": "일일 트리거 한도(20건)를 초과했습니다"}
        started_at = datetime.now().isoformat(timespec="seconds")
        _JOBS[session_id] = {"status": "queued", "started_at": started_at, "session_id": session_id, "tickers": tickers}
    threading.Thread(target=_run_job, args=(session_id, session_id, tickers, "compare"), daemon=True).start()
    with _JOBS_LOCK:
        return dict(_JOBS[session_id])


def job_status(job_key: str) -> dict:
    with _JOBS_LOCK:
        return dict(_JOBS.get(job_key, {"status": "idle"}))

_SKELETON = (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Market Scanner Dashboard</title></head><body>\n{inner}\n</body></html>"
)


def _latest_scan() -> dict | None:
    files = glob.glob(os.path.join(SCANS_DIR, "scan_*.json"))
    if not files:
        return None
    with open(max(files, key=os.path.getmtime), encoding="utf-8") as fh:
        return json.load(fh)


def _journal_summary() -> dict:
    try:
        from tradingagents.scanner import journal
        return journal.summary()
    except Exception:  # noqa: BLE001 - journal is optional context
        return {}


def _decision_entries() -> list[dict]:
    """Most-recent-first list of journal 'decision' entries, for badges/History."""
    try:
        from tradingagents.scanner import journal
        entries = [e for e in journal.load_entries() if e.get("type") == "decision"]
    except Exception:  # noqa: BLE001 - journal is optional context
        return []
    entries.sort(key=lambda e: e.get("logged_at", ""), reverse=True)
    return entries


def build_payload(live: bool, refresh: int) -> dict:
    scan = _latest_scan() or {}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "live": live,
        "refresh_seconds": refresh,
        "subtitle": "S&P 500 · 저평가 & 상승 모니터",
        "scan": scan,
        "journal": _journal_summary(),
        "decisions": _decision_entries(),
        "sessions": list_sessions(limit=50),
    }


def render(payload: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as fh:
        inner = fh.read()
    data = json.dumps(payload, ensure_ascii=False)
    # Guard against a stray marker in the data breaking the splice.
    if _MARKER not in inner:
        raise RuntimeError(f"template missing data marker {_MARKER!r}")
    inner = inner.replace(_MARKER, data)
    return _SKELETON.format(inner=inner)


def write_file(out: str, live: bool, refresh: int) -> str:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    html = render(build_payload(live, refresh))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out


def serve(port: int, refresh: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import urlparse, parse_qs

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console quiet
            pass

        def _json(self, obj: dict, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                try:
                    body = render(build_payload(live=True, refresh=refresh)).encode("utf-8")
                except Exception as exc:  # noqa: BLE001
                    self.send_error(500, str(exc))
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            if parsed.path == "/api/status":
                ticker = (parse_qs(parsed.query).get("ticker") or [""])[0].strip().upper()
                self._json(job_status(ticker))
                return
            if parsed.path.startswith("/api/session/"):
                rest = parsed.path[len("/api/session/"):]
                if rest.endswith("/status"):
                    self._json(job_status(rest[: -len("/status")]))
                    return
                session = get_session(rest)
                if session is None:
                    self.send_error(404)
                    return
                self._json(session)
                return
            self.send_error(404)

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/analyze":
                ticker = (parse_qs(parsed.query).get("ticker") or [""])[0]
                self._json(start_analysis(ticker))
                return
            if parsed.path == "/api/compare":
                tickers = (parse_qs(parsed.query).get("tickers") or [""])[0]
                self._json(start_compare(tickers))
                return
            self.send_error(404)

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Dashboard live at http://localhost:{port}  (Ctrl-C to stop)", file=sys.stderr)
    if not glob.glob(os.path.join(SCANS_DIR, "scan_*.json")):
        print("  note: no scans yet — run scripts/screen_universe.py first.", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Build/serve the scanner dashboard")
    p.add_argument("--serve", action="store_true", help="Live HTTP server with auto-refresh")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--refresh", type=int, default=60, help="Auto-refresh seconds (serve mode)")
    p.add_argument("--out", default=os.path.join(DASH_DIR, "dashboard.html"))
    args = p.parse_args()

    if args.serve:
        return serve(args.port, args.refresh)
    out = write_file(args.out, live=False, refresh=args.refresh)
    print(f"Wrote {out}\nOpen it in a browser (double-click), or use --serve for live monitoring.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
