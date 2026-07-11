"""Memory / journal for the scanner and decisions (item 1: learning).

Every scan pick and every trade decision is appended to ``data/journal/
journal.jsonl`` (append-only, one JSON object per line) with the entry date and
a metrics snapshot. ``review`` re-prices past entries against a later date to
compute the realised forward return, turning the log into a feedback signal:
which screens actually worked, and by how much vs. the benchmark.

Kept deliberately simple and file-based so it survives across sessions when
committed, and so a cron job can append to it without a database.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JOURNAL_DIR = os.path.join(_REPO_ROOT, "data", "journal")
JOURNAL_PATH = os.path.join(JOURNAL_DIR, "journal.jsonl")
MARKDOWN_PATH = os.path.join(JOURNAL_DIR, "journal.md")


def _now_date() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def append(entry: dict) -> dict:
    """Append one entry (adds id/logged_at if missing) and mirror to markdown."""
    os.makedirs(JOURNAL_DIR, exist_ok=True)
    entry = dict(entry)
    entry.setdefault("logged_at", datetime.now().isoformat(timespec="seconds"))
    entry.setdefault("date", _now_date())
    entry.setdefault("status", "open")
    with open(JOURNAL_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _append_markdown(entry)
    return entry


def log_pick(ticker: str, date: str, score: dict, source: str = "scan") -> dict:
    """Record a screener pick with its scores and entry price."""
    return append({
        "type": "pick",
        "source": source,
        "ticker": ticker,
        "date": date,
        "entry_price": (score.get("metrics") or {}).get("price"),
        "composite": score.get("composite"),
        "valuation": score.get("valuation"),
        "momentum": score.get("momentum"),
        "quality": score.get("quality"),
        "growth": score.get("growth"),
        "tags": score.get("tags", []),
        "flags": score.get("flags", []),
    })


def log_decision(ticker: str, date: str, decision: str, conviction: str = "", note: str = "") -> dict:
    """Record a trade-decision verdict (BUY/HOLD/SELL)."""
    return append({
        "type": "decision",
        "ticker": ticker,
        "date": date,
        "decision": decision,
        "conviction": conviction,
        "note": note,
    })


def load_entries() -> list[dict]:
    if not os.path.exists(JOURNAL_PATH):
        return []
    out = []
    with open(JOURNAL_PATH, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def _append_markdown(entry: dict) -> None:
    """Human-readable mirror so the log is skimmable in a PR/diff."""
    header_needed = not os.path.exists(MARKDOWN_PATH)
    with open(MARKDOWN_PATH, "a", encoding="utf-8") as fh:
        if header_needed:
            fh.write("# Trading journal\n\n"
                     "Append-only log of scanner picks and trade decisions. "
                     "See `journal.jsonl` for the machine-readable source.\n\n")
        if entry.get("type") == "pick":
            fh.write(
                f"- `{entry['date']}` **PICK** {entry['ticker']} "
                f"(composite {entry.get('composite')}, "
                f"val {entry.get('valuation')} / mom {entry.get('momentum')}) "
                f"entry ${entry.get('entry_price')} "
                f"{', '.join(entry.get('tags', []))}\n"
            )
        elif entry.get("type") == "decision":
            fh.write(
                f"- `{entry['date']}` **{entry.get('decision','?')}** {entry['ticker']} "
                f"({entry.get('conviction','')}) {entry.get('note','')}\n"
            )
        elif entry.get("type") == "review":
            fh.write(
                f"  - review `{entry['date']}`: {entry['ticker']} "
                f"{entry.get('return_pct')}% vs SPY {entry.get('benchmark_return_pct')}% "
                f"→ alpha {entry.get('alpha_pct')}%\n"
            )


def _price_on_or_before(ticker: str, date: str, window_days: int = 10) -> float | None:
    """Closing price at/just before ``date`` (uses the engine's yfinance path)."""
    from datetime import timedelta

    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    d = datetime.strptime(date, "%Y-%m-%d")
    start = (d - timedelta(days=window_days)).strftime("%Y-%m-%d")
    end = (d + timedelta(days=1)).strftime("%Y-%m-%d")
    hist = yf.Ticker(normalize_symbol(ticker)).history(start=start, end=end)
    if hist.empty:
        return None
    return float(hist["Close"].iloc[-1])


def review(asof: str | None = None, benchmark: str = "SPY") -> list[dict]:
    """Re-price open 'pick' entries as of ``asof`` and record realised returns.

    Returns the list of review records appended. Skips entries whose forward
    price or entry price is unavailable. This is the learning loop: it tells you
    whether the screen's picks actually outperformed.
    """
    asof = asof or _now_date()
    picks = [e for e in load_entries() if e.get("type") == "pick" and e.get("status") != "reviewed"]
    reviews = []
    bench_entry_cache: dict[str, float | None] = {}
    for e in picks:
        entry_price = e.get("entry_price")
        if entry_price in (None, 0):
            continue
        fwd = _price_on_or_before(e["ticker"], asof)
        if fwd is None:
            continue
        ret = (fwd / entry_price - 1.0) * 100.0
        # Benchmark return over the same holding window.
        b0 = bench_entry_cache.get(e["date"])
        if e["date"] not in bench_entry_cache:
            b0 = _price_on_or_before(benchmark, e["date"])
            bench_entry_cache[e["date"]] = b0
        b1 = _price_on_or_before(benchmark, asof)
        bret = (b1 / b0 - 1.0) * 100.0 if (b0 and b1) else None
        rec = append({
            "type": "review",
            "ticker": e["ticker"],
            "date": asof,
            "pick_date": e["date"],
            "entry_price": entry_price,
            "exit_price": round(fwd, 2),
            "return_pct": round(ret, 2),
            "benchmark_return_pct": round(bret, 2) if bret is not None else None,
            "alpha_pct": round(ret - bret, 2) if bret is not None else None,
        })
        reviews.append(rec)
    return reviews


def summary() -> dict:
    """Aggregate stats over reviewed picks — the scorecard for the screen."""
    reviews = [e for e in load_entries() if e.get("type") == "review"]
    if not reviews:
        return {"reviewed": 0}
    rets = [r["return_pct"] for r in reviews if r.get("return_pct") is not None]
    alphas = [r["alpha_pct"] for r in reviews if r.get("alpha_pct") is not None]
    wins = [r for r in rets if r > 0]
    return {
        "reviewed": len(reviews),
        "avg_return_pct": round(sum(rets) / len(rets), 2) if rets else None,
        "hit_rate_pct": round(100 * len(wins) / len(rets), 1) if rets else None,
        "avg_alpha_pct": round(sum(alphas) / len(alphas), 2) if alphas else None,
    }
