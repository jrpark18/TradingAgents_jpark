#!/usr/bin/env python3
"""Headless Stage-1 screener over the target index universe.

Sweeps Nasdaq-100 / S&P 100 / Dow 30 / Russell 1000 (configurable) in rotating
batches, scores every ticker with the LLM-free quantitative screener, and writes
ranked shortlists of **undervalued** and **uptrend** candidates. Designed to run
unattended on a cron schedule; each run advances a rotation cursor so a large
universe is covered over successive runs ("continuously cycle the market").

Examples
--------
    # One cron tick: next 120 names, log top picks to the journal
    python scripts/screen_universe.py --batch-size 120 --top 25

    # Whole S&P 100 + Dow in one go
    python scripts/screen_universe.py --indices sp100,dow30 --batch-size 0

    # Faster with threads (mind vendor rate limits)
    python scripts/screen_universe.py --batch-size 200 --workers 8

Output: JSON + markdown under data/scans/. Stage-2 = run the `trade-decision`
skill on the top shortlist.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tradingagents.scanner import journal, universe  # noqa: E402
from tradingagents.scanner.screener import fetch_metrics, score_ticker  # noqa: E402

SCANS_DIR = os.path.join(_REPO_ROOT, "data", "scans")


def _score_one(ticker: str):
    try:
        metrics = fetch_metrics(ticker)
        return score_ticker(ticker, metrics), None
    except Exception as exc:  # noqa: BLE001 - per-ticker isolation: skip & record
        return None, f"{type(exc).__name__}: {exc}"


def run(args: argparse.Namespace) -> int:
    indices = tuple(s.strip() for s in args.indices.split(",") if s.strip())
    tickers = universe.load_universe(indices)
    if not tickers:
        print("ERROR: universe is empty. Run scripts/refresh_universe.py or check data/universe/.",
              file=sys.stderr)
        return 1

    batch = universe.next_batch(
        tickers, args.batch_size, cursor_key=",".join(indices), persist=not args.no_advance
    )
    cov = universe.coverage(indices)
    print(f"Universe {indices} = {batch.universe_size} tickers "
          f"(coverage: {cov}); scanning {len(batch.tickers)} "
          f"[{batch.offset}:{batch.offset + len(batch.tickers)}]"
          f"{' (wrapped — cycle complete)' if batch.wrapped else ''}", file=sys.stderr)

    scores, errors = [], {}
    if args.workers > 1:
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(_score_one, t): t for t in batch.tickers}
            for fut in concurrent.futures.as_completed(futs):
                s, err = fut.result()
                (scores.append(s) if s else errors.__setitem__(futs[fut], err))
    else:
        for t in batch.tickers:
            s, err = _score_one(t)
            if s:
                scores.append(s)
            else:
                errors[t] = err
            if args.sleep:
                time.sleep(args.sleep)

    scores = [s for s in scores if s.confidence >= args.min_confidence]
    scores.sort(key=lambda s: s.composite, reverse=True)

    def top(tag=None, key="composite", n=args.top):
        pool = [s for s in scores if (tag in s.tags)] if tag else scores
        pool = sorted(pool, key=lambda s: getattr(s, key), reverse=True)
        return pool[:n]

    undervalued = top("value_candidate", "valuation")
    uptrend = top("momentum_candidate", "momentum")
    sweet_spot = top("value_and_momentum", "composite")

    date = datetime.now().strftime("%Y-%m-%d")
    os.makedirs(SCANS_DIR, exist_ok=True)
    payload = {
        "date": date,
        "indices": list(indices),
        "universe_size": batch.universe_size,
        "scanned": len(batch.tickers),
        "scored": len(scores),
        "errors": len(errors),
        "batch_offset": batch.offset,
        "next_offset": batch.next_offset,
        "top_composite": [s.to_dict() for s in scores[: args.top]],
        "undervalued": [s.to_dict() for s in undervalued],
        "uptrend": [s.to_dict() for s in uptrend],
        "value_and_momentum": [s.to_dict() for s in sweet_spot],
    }
    stem = f"{date}_{batch.offset:04d}"
    json_path = os.path.join(SCANS_DIR, f"scan_{stem}.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    _write_markdown(payload, os.path.join(SCANS_DIR, "latest_scan.md"))

    if not args.no_journal:
        logged = {}
        for bucket in (undervalued, uptrend):
            for s in bucket:
                if s.ticker not in logged:
                    journal.log_pick(s.ticker, date, s.to_dict(), source="scan")
                    logged[s.ticker] = True
        print(f"Logged {len(logged)} picks to the journal.", file=sys.stderr)

    print(f"Wrote {json_path} and data/scans/latest_scan.md", file=sys.stderr)
    # Console summary
    print(f"\n== Scan {date} — scored {len(scores)}/{len(batch.tickers)} "
          f"({len(errors)} skipped) ==")
    _print_bucket("UNDERVALUED (cheap + quality)", undervalued, "valuation")
    _print_bucket("UPTREND (momentum + growth)", uptrend, "momentum")
    _print_bucket("SWEET SPOT (undervalued & rising)", sweet_spot, "composite")
    return 0


def _fmt(s, key):
    return (f"  {getattr(s, key):5.1f}  {s.ticker:<7} "
            f"[v{s.valuation:.0f} m{s.momentum:.0f} q{s.quality:.0f} g{s.growth:.0f}] "
            f"{', '.join(s.flags[:3])}")


def _print_bucket(title, bucket, key):
    print(f"\n{title}:")
    if not bucket:
        print("  (none)")
    for s in bucket:
        print(_fmt(s, key))


def _write_markdown(payload: dict, path: str) -> None:
    lines = [f"# Market scan — {payload['date']}", ""]
    lines.append(f"Indices: {', '.join(payload['indices'])} · "
                 f"universe {payload['universe_size']} · scanned {payload['scanned']} · "
                 f"scored {payload['scored']} · skipped {payload['errors']}")
    lines.append("")
    for title, key in (("Undervalued (cheap + quality)", "undervalued"),
                       ("Uptrend (momentum + growth)", "uptrend"),
                       ("Sweet spot — undervalued & rising", "value_and_momentum")):
        lines.append(f"## {title}\n")
        rows = payload[key]
        if not rows:
            lines.append("_(none this batch)_\n")
            continue
        lines.append("| Ticker | Composite | Valuation | Momentum | Quality | Growth | Flags |")
        lines.append("|---|--:|--:|--:|--:|--:|---|")
        for s in rows:
            lines.append(f"| {s['ticker']} | {s['composite']} | {s['valuation']} | "
                         f"{s['momentum']} | {s['quality']} | {s['growth']} | "
                         f"{', '.join(s['flags'][:4])} |")
        lines.append("")
    lines.append("> Stage-2: run the `trade-decision` skill on the top names for a full "
                 "multi-agent BUY/HOLD/SELL. Decision-support, not financial advice.")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Headless Stage-1 universe screener.")
    p.add_argument("--indices", default=",".join(universe.DEFAULT_INDICES),
                   help="Comma-separated: nasdaq100,sp100,dow30,russell1000,seed")
    p.add_argument("--batch-size", type=int, default=150,
                   help="Tickers per run (0 = whole universe). Rotates via a cursor.")
    p.add_argument("--top", type=int, default=25, help="Shortlist size per bucket")
    p.add_argument("--min-confidence", type=float, default=0.3,
                   help="Drop tickers with too few available metrics (0-1)")
    p.add_argument("--workers", type=int, default=1, help="Parallel fetch workers")
    p.add_argument("--sleep", type=float, default=0.0, help="Delay between calls (workers=1)")
    p.add_argument("--no-journal", action="store_true", help="Do not log picks")
    p.add_argument("--no-advance", action="store_true", help="Do not persist the rotation cursor")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
