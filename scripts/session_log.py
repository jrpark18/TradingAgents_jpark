#!/usr/bin/env python3
"""Record an analysis session so it shows up in the dashboard (plan §8.3).

The web dashboard writes its own session records as it drives the headless run.
Every *other* trigger path — a Telegram message, a plain interactive session, a
cron job — has no such runner, so it calls this CLI once the analysis is done and
the result lands in the same store, appearing in the Result / History / Log tabs
next to web-triggered runs.

    # after finishing a trade-decision for one ticker
    python scripts/session_log.py record --tickers AAPL --source telegram \\
        --requested-by 12345678 --started-at 2026-07-25T10:32:45 --report-file memo.md

    # a comparison, report piped in
    trade_report | python scripts/session_log.py record --tickers C,KEY --report-file -

BUY/HOLD/SELL badges are not passed in: they are read back from the journal,
counting only entries logged at or after --started-at, so a session can never be
badged with a stale verdict from an earlier day. Log the decision with
scripts/journal.py first, then call this.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tradingagents.scanner import session_store  # noqa: E402


def _read_report(args: argparse.Namespace) -> str:
    if args.report is not None:
        return args.report
    if args.report_file == "-":
        return sys.stdin.read()
    if args.report_file:
        with open(args.report_file, encoding="utf-8") as fh:
            return fh.read()
    return ""


def cmd_record(args: argparse.Namespace) -> int:
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    if not tickers:
        print("error: --tickers must name at least one ticker", file=sys.stderr)
        return 2

    now = datetime.now().isoformat(timespec="seconds")
    started_at = args.started_at or now
    session = session_store.build_session(
        tickers,
        session_id=args.id,
        type_=args.type,
        source=args.source,
        requested_by=args.requested_by,
        requested_at=started_at,
        completed_at=args.completed_at or now,
        status=args.status,
        decisions=session_store.session_decisions(tickers, since=started_at),
        report_markdown=_read_report(args),
        transcript=os.path.exists(session_store.transcript_path(args.id)) if args.id else False,
        error=args.error,
    )
    session_store.save_session(session)
    print(session["id"])
    if not session["decisions"] and args.status == "done":
        print("note: no journal decision found at/after --started-at, so this session "
              "has no badge. Log it with scripts/journal.py, then re-run to attach it.",
              file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for s in session_store.list_sessions(limit=args.limit):
        decisions = " ".join(f"{t}={d.get('decision')}" for t, d in (s.get("decisions") or {}).items())
        print(f"{s['id']}\t{s.get('status'):7}\t{s.get('source'):9}\t"
              f"{','.join(s.get('tickers') or [])}\t{decisions}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    rec = sub.add_parser("record", help="Save an analysis session record")
    rec.add_argument("--tickers", required=True, help="Comma-separated, e.g. AAPL or C,KEY")
    rec.add_argument("--type", choices=session_store.VALID_TYPES,
                     help="Defaults to single/compare by ticker count")
    rec.add_argument("--source", default="cli", help="dashboard | telegram | cli (default: cli)")
    rec.add_argument("--requested-by", default="cli",
                     help="web | <telegram user id> | cli (default: cli)")
    rec.add_argument("--started-at", help="ISO timestamp the analysis began (default: now). "
                                          "Also the cutoff for reading journal decisions.")
    rec.add_argument("--completed-at", help="ISO timestamp (default: now)")
    rec.add_argument("--status", default="done", choices=session_store.VALID_STATUSES)
    rec.add_argument("--error", help="Error message when --status error")
    rec.add_argument("--id", help="Session id (default: generated from time + tickers)")
    g = rec.add_mutually_exclusive_group()
    g.add_argument("--report-file", help="Markdown report file, or - for stdin")
    g.add_argument("--report", help="Markdown report inline")
    rec.set_defaults(func=cmd_record)

    lst = sub.add_parser("list", help="List saved sessions, newest first")
    lst.add_argument("--limit", type=int, default=20)
    lst.set_defaults(func=cmd_list)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
