#!/usr/bin/env python3
"""Standalone market-data CLI for the trading skills.

This is the single data seam every trading skill and sub-agent calls. It wraps
``tradingagents.dataflows.interface.route_to_vendor`` — the same vendor-routing
layer the original framework used — so skills get real OHLCV, indicators,
fundamentals, news, and macro data without pulling in the LLM/LangGraph stack.

Defaults are keyless (yfinance for prices/indicators/fundamentals/news,
Polymarket for prediction markets). ``macro`` needs ``FRED_API_KEY``. Override
any vendor with ``TRADINGAGENTS_*`` env vars or the flags below.

Usage examples
--------------
    python scripts/market_data.py prices AAPL --start 2026-06-01 --end 2026-07-01
    python scripts/market_data.py indicators AAPL --indicator rsi,macd,close_50_sma --date 2026-07-01
    python scripts/market_data.py fundamentals AAPL --date 2026-07-01
    python scripts/market_data.py income AAPL --freq quarterly --date 2026-07-01
    python scripts/market_data.py news AAPL --start 2026-06-24 --end 2026-07-01
    python scripts/market_data.py global-news --date 2026-07-01 --lookback 7 --limit 10
    python scripts/market_data.py macro cpi --date 2026-07-01
    python scripts/market_data.py prediction-markets "Fed rate cut" --limit 5

Every command prints a plain-text report to stdout and exits 0 on success, or
prints an error to stderr and exits 1 on failure. The reports are meant to be
read by an LLM agent, so keep them verbatim.
"""

from __future__ import annotations

import argparse
import os
import sys

# Allow running as `python scripts/market_data.py ...` from the repo root
# without installing the package: put the repo root on sys.path.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# route_to_vendor initialises config on import; no setup required.
from tradingagents.dataflows.interface import route_to_vendor  # noqa: E402


def _emit(text: str) -> int:
    """Print a vendor report and translate its content into an exit code.

    The vendor layer never raises for "no data" — it returns a sentinel string
    (NO_DATA_AVAILABLE / DATA_UNAVAILABLE). Surface those as a non-zero exit so
    a calling skill or shell pipeline can tell success from an empty result,
    while still printing the instructive message for the agent to read.
    """
    print(text)
    if isinstance(text, str) and text.startswith(("NO_DATA_AVAILABLE", "DATA_UNAVAILABLE")):
        return 2
    return 0


def _run(method: str, *args) -> int:
    try:
        result = route_to_vendor(method, *args)
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, don't traceback
        print(f"ERROR calling {method}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return _emit(result if isinstance(result, str) else str(result))


def cmd_prices(a: argparse.Namespace) -> int:
    return _run("get_stock_data", a.symbol, a.start, a.end)


def cmd_indicators(a: argparse.Namespace) -> int:
    # Fetch each indicator individually and concatenate, mirroring the agent tool.
    names = [i.strip().lower() for i in a.indicator.split(",") if i.strip()]
    if not names:
        print("ERROR: --indicator must name at least one indicator", file=sys.stderr)
        return 1
    blocks, worst = [], 0
    for name in names:
        try:
            blocks.append(route_to_vendor("get_indicators", a.symbol, name, a.date, a.lookback))
        except Exception as exc:  # noqa: BLE001
            blocks.append(f"[{name}] ERROR: {type(exc).__name__}: {exc}")
            worst = max(worst, 1)
    text = "\n\n".join(blocks)
    print(text)
    return worst or (2 if "NO_DATA_AVAILABLE" in text else 0)


def cmd_fundamentals(a: argparse.Namespace) -> int:
    return _run("get_fundamentals", a.symbol, a.date)


def cmd_statement(a: argparse.Namespace) -> int:
    return _run(a._method, a.symbol, a.freq, a.date)


def cmd_news(a: argparse.Namespace) -> int:
    return _run("get_news", a.symbol, a.start, a.end)


def cmd_global_news(a: argparse.Namespace) -> int:
    return _run("get_global_news", a.date, a.lookback, a.limit)


def cmd_insider(a: argparse.Namespace) -> int:
    return _run("get_insider_transactions", a.symbol)


def cmd_macro(a: argparse.Namespace) -> int:
    return _run("get_macro_indicators", a.indicator, a.date, a.lookback)


def cmd_prediction_markets(a: argparse.Namespace) -> int:
    return _run("get_prediction_markets", a.topic, a.limit)


def cmd_chart(a: argparse.Namespace) -> int:
    from tradingagents.dataflows.chart import render_candlestick_chart
    from tradingagents.dataflows.symbol_utils import NoMarketDataError

    try:
        ma_periods = [int(p.strip()) for p in a.ma.split(",") if p.strip()]
    except ValueError:
        print(f"ERROR: --ma must be a comma-separated list of integers, got {a.ma!r}", file=sys.stderr)
        return 1

    try:
        path = render_candlestick_chart(a.symbol, a.date, a.out, days=a.days, ma_periods=ma_periods)
    except NoMarketDataError as exc:
        print(f"NO_DATA_AVAILABLE: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI boundary: report, don't traceback
        print(f"ERROR generating chart for {a.symbol}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="market_data.py",
        description="Market-data seam for the trading skills (wraps route_to_vendor).",
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add_date(sp, name="--date", required=True, help="Current date YYYY-MM-DD"):
        sp.add_argument(name, required=required, help=help)

    sp = sub.add_parser("prices", help="OHLCV price history")
    sp.add_argument("symbol")
    sp.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    sp.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    sp.set_defaults(func=cmd_prices)

    sp = sub.add_parser("indicators", help="Technical indicators (comma-separated)")
    sp.add_argument("symbol")
    sp.add_argument(
        "--indicator",
        required=True,
        help="One or more of: close_10_ema, close_50_sma, close_200_sma, macd, "
        "macds, macdh, rsi, boll, boll_ub, boll_lb, atr, vwma (comma-separated)",
    )
    add_date(sp)
    sp.add_argument("--lookback", type=int, default=30, help="Days to look back (default 30)")
    sp.set_defaults(func=cmd_indicators)

    sp = sub.add_parser("fundamentals", help="Comprehensive fundamentals snapshot")
    sp.add_argument("symbol")
    add_date(sp)
    sp.set_defaults(func=cmd_fundamentals)

    for name, method in (
        ("balance-sheet", "get_balance_sheet"),
        ("cashflow", "get_cashflow"),
        ("income", "get_income_statement"),
    ):
        sp = sub.add_parser(name, help=f"{name.replace('-', ' ').title()} statement")
        sp.add_argument("symbol")
        sp.add_argument("--freq", default="quarterly", choices=["annual", "quarterly"])
        add_date(sp, required=False, help="Current date YYYY-MM-DD (optional)")
        sp.set_defaults(func=cmd_statement, _method=method)

    sp = sub.add_parser("news", help="Ticker-specific news")
    sp.add_argument("symbol")
    sp.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    sp.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    sp.set_defaults(func=cmd_news)

    sp = sub.add_parser("global-news", help="Macro / global headlines")
    add_date(sp)
    sp.add_argument("--lookback", type=int, default=None, help="Days to look back")
    sp.add_argument("--limit", type=int, default=None, help="Max articles")
    sp.set_defaults(func=cmd_global_news)

    sp = sub.add_parser("insider", help="Insider transactions")
    sp.add_argument("symbol")
    sp.set_defaults(func=cmd_insider)

    sp = sub.add_parser("macro", help="FRED macro indicator (needs FRED_API_KEY)")
    sp.add_argument("indicator", help="e.g. cpi, core_pce, unemployment, fed_funds_rate, 10y_treasury, yield_curve, vix")
    add_date(sp)
    sp.add_argument("--lookback", type=int, default=None, help="Trailing window in days")
    sp.set_defaults(func=cmd_macro)

    sp = sub.add_parser("prediction-markets", help="Market-implied event probabilities (Polymarket)")
    sp.add_argument("topic")
    sp.add_argument("--limit", type=int, default=5, help="Max markets (default 5)")
    sp.set_defaults(func=cmd_prediction_markets)

    sp = sub.add_parser("chart", help="Candlestick + volume PNG chart (needs the [chart] extra)")
    sp.add_argument("symbol")
    add_date(sp)
    sp.add_argument("--out", required=True, help="Output PNG file path")
    sp.add_argument("--days", type=int, default=365, help="Trailing calendar days to plot (default 365)")
    sp.add_argument(
        "--ma",
        default="20,40,60,120,240",
        help="Comma-separated MA windows to overlay (default 20,40,60,120,240)",
    )
    sp.set_defaults(func=cmd_chart)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
