#!/usr/bin/env python3
"""Trading-journal CLI (memory / learning loop).

    python scripts/journal.py list [--type pick|decision|review] [--limit N]
    python scripts/journal.py review [--asof YYYY-MM-DD] [--benchmark SPY]
    python scripts/journal.py summary
    python scripts/journal.py decision <TICKER> <BUY|HOLD|SELL> [--conviction High --note "..."]

`review` re-prices past picks and records realised return vs. the benchmark;
`summary` prints the scorecard (hit-rate, avg return, avg alpha).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tradingagents.scanner import journal  # noqa: E402


def cmd_list(a):
    entries = journal.load_entries()
    if a.type:
        entries = [e for e in entries if e.get("type") == a.type]
    entries = entries[-a.limit:] if a.limit else entries
    for e in entries:
        print(json.dumps(e, ensure_ascii=False))
    print(f"\n{len(entries)} entries.", file=sys.stderr)
    return 0


def cmd_review(a):
    try:
        recs = journal.review(asof=a.asof, benchmark=a.benchmark)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR during review: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    for r in recs:
        print(f"{r['ticker']:<7} {r['return_pct']:>7.2f}%  "
              f"vs {a.benchmark} {r.get('benchmark_return_pct')}%  "
              f"alpha {r.get('alpha_pct')}%")
    print(f"\nReviewed {len(recs)} picks.", file=sys.stderr)
    print(json.dumps(journal.summary(), ensure_ascii=False))
    return 0


def cmd_summary(a):
    print(json.dumps(journal.summary(), ensure_ascii=False, indent=2))
    return 0


def cmd_decision(a):
    e = journal.log_decision(a.ticker.upper(), journal._now_date(), a.decision.upper(),
                             conviction=a.conviction, note=a.note)
    print(json.dumps(e, ensure_ascii=False))
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Trading journal / memory CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="Print journal entries")
    s.add_argument("--type", choices=["pick", "decision", "review"])
    s.add_argument("--limit", type=int, default=50)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("review", help="Re-price past picks and record returns")
    s.add_argument("--asof", default=None, help="Valuation date YYYY-MM-DD (default today)")
    s.add_argument("--benchmark", default="SPY")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("summary", help="Scorecard over reviewed picks")
    s.set_defaults(func=cmd_summary)

    s = sub.add_parser("decision", help="Log a BUY/HOLD/SELL verdict")
    s.add_argument("ticker")
    s.add_argument("decision", choices=["BUY", "HOLD", "SELL", "buy", "hold", "sell"])
    s.add_argument("--conviction", default="")
    s.add_argument("--note", default="")
    s.set_defaults(func=cmd_decision)

    return p


if __name__ == "__main__":
    args = build_parser().parse_args()
    raise SystemExit(args.func(args))
