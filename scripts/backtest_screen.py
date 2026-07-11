#!/usr/bin/env python3
"""Backtest the momentum screen (item 3).

Reconstructs the *price/momentum* portion of the screen as of one or more
historical dates — using only price history up to that date, so it is
look-ahead safe — then measures the forward return of the top picks over a
holding horizon versus a benchmark (SPY). Reports hit-rate, average return, and
alpha per date and in aggregate.

    python scripts/backtest_screen.py --asof 2026-01-15,2026-03-16,2026-05-15 \
        --indices sp100,dow30 --top 10 --horizon 21

Note: valuation (P/E, PEG, …) is NOT backtested here — yfinance's ``info`` is a
live snapshot with no point-in-time history. Momentum reconstructs cleanly from
prices; a valuation backtest needs a point-in-time fundamentals source.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from tradingagents.scanner import universe  # noqa: E402
from tradingagents.scanner.screener import score_momentum  # noqa: E402


def _history(ticker: str, start: str, end: str):
    import yfinance as yf

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    return yf.Ticker(normalize_symbol(ticker)).history(start=start, end=end)


def _metrics_asof(hist, asof_dt) -> dict | None:
    """Build a momentum metrics dict from prices up to (and including) asof."""
    past = hist[hist.index <= asof_dt]
    if len(past) < 200:  # need enough history for a 200-day average
        return None
    close = past["Close"]
    price = float(close.iloc[-1])
    window = close.tail(252)
    return {
        "price": price,
        "fiftyDayAverage": float(close.tail(50).mean()),
        "twoHundredDayAverage": float(close.tail(200).mean()),
        "fiftyTwoWeekHigh": float(window.max()),
        "fiftyTwoWeekLow": float(window.min()),
    }


def _forward_return(hist, asof_dt, horizon_days: int) -> float | None:
    entry = hist[hist.index <= asof_dt]
    target = asof_dt + timedelta(days=horizon_days)
    exit_ = hist[hist.index <= target]
    if entry.empty or exit_.empty:
        return None
    e, x = float(entry["Close"].iloc[-1]), float(exit_["Close"].iloc[-1])
    if e == 0:
        return None
    return (x / e - 1.0) * 100.0


def run(args) -> int:
    indices = tuple(s.strip() for s in args.indices.split(",") if s.strip())
    tickers = universe.load_universe(indices)
    if not tickers:
        print("ERROR: empty universe.", file=sys.stderr)
        return 1
    dates = [d.strip() for d in args.asof.split(",") if d.strip()]

    all_rets, all_alphas, wins, n = [], [], 0, 0
    for date in dates:
        asof_dt = datetime.strptime(date, "%Y-%m-%d")
        # Fetch a padded window once per ticker.
        start = (asof_dt - timedelta(days=420)).strftime("%Y-%m-%d")
        end = (asof_dt + timedelta(days=args.horizon + 7)).strftime("%Y-%m-%d")

        bench_hist = _history(args.benchmark, start, end)
        bench_fwd = _forward_return(bench_hist, asof_dt, args.horizon) if not bench_hist.empty else None

        scored = []
        for t in tickers:
            try:
                h = _history(t, start, end)
            except Exception:  # noqa: BLE001
                continue
            if h.empty:
                continue
            m = _metrics_asof(h, asof_dt)
            if not m:
                continue
            mscore, used, _ = score_momentum(m)
            if used < 3:
                continue
            scored.append((t, mscore, h))
        scored.sort(key=lambda r: r[1], reverse=True)
        picks = scored[: args.top]

        rets = []
        for t, mscore, h in picks:
            fr = _forward_return(h, asof_dt, args.horizon)
            if fr is None:
                continue
            rets.append(fr)
            all_rets.append(fr)
            n += 1
            if fr > 0:
                wins += 1
            if bench_fwd is not None:
                all_alphas.append(fr - bench_fwd)
        avg = sum(rets) / len(rets) if rets else float("nan")
        print(f"{date}: picks={len(picks)} avg_fwd_return={avg:6.2f}%  "
              f"SPY={bench_fwd if bench_fwd is None else round(bench_fwd,2)}%  "
              f"(scored {len(scored)})")

    print("\n== Aggregate ==")
    if n:
        print(f"picks={n}  avg_return={sum(all_rets)/n:.2f}%  "
              f"hit_rate={100*wins/n:.1f}%  "
              f"avg_alpha={sum(all_alphas)/len(all_alphas):.2f}%" if all_alphas
              else f"picks={n}  avg_return={sum(all_rets)/n:.2f}%  hit_rate={100*wins/n:.1f}%")
    else:
        print("No picks produced (empty history? check network/dates).")
    return 0


def build_parser():
    p = argparse.ArgumentParser(description="Momentum-screen backtest")
    p.add_argument("--asof", required=True, help="Comma-separated historical dates YYYY-MM-DD")
    p.add_argument("--indices", default="sp100,dow30")
    p.add_argument("--top", type=int, default=10)
    p.add_argument("--horizon", type=int, default=21, help="Holding period in days")
    p.add_argument("--benchmark", default="SPY")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
