"""Candlestick + volume chart rendering for report/Telegram attachments."""

from __future__ import annotations

import os
from typing import Annotated, Sequence

import matplotlib

matplotlib.use("Agg")  # headless: no display server available in the CLI/agent runtime

import mplfinance as mpf
import pandas as pd

from .stockstats_utils import load_ohlcv
from .symbol_utils import NoMarketDataError, normalize_symbol

DEFAULT_MA_PERIODS: tuple[int, ...] = (20, 40, 60, 120, 240)


def render_candlestick_chart(
    symbol: Annotated[str, "ticker symbol"],
    curr_date: Annotated[str, "as-of date, YYYY-MM-DD"],
    out_path: Annotated[str, "PNG file path to write"],
    days: Annotated[int, "trailing calendar days of history to plot"] = 730,
    ma_periods: Annotated[Sequence[int], "moving-average windows to overlay"] = DEFAULT_MA_PERIODS,
) -> str:
    """Render a candlestick chart with MA overlays and a volume panel (4:1
    price:volume height ratio), saved as a PNG.

    Returns out_path on success. Raises NoMarketDataError if there's no
    price history to plot.
    """
    canonical = normalize_symbol(symbol)
    data = load_ohlcv(symbol, curr_date)
    if data.empty:
        raise NoMarketDataError(symbol, canonical, "no price history to chart")

    data = data.copy()
    data["Date"] = pd.to_datetime(data["Date"])
    data = data.set_index("Date").sort_index()

    # Compute MAs over the full cached history (up to 5y) before slicing down
    # to the display window, so long windows (e.g. MA240) are already
    # populated at the left edge of the chart instead of only appearing near
    # the end of it.
    ma_cols = []
    for n in ma_periods:
        if len(data) >= n:
            col = f"MA{n}"
            data[col] = data["Close"].rolling(n).mean()
            ma_cols.append(col)

    cutoff = pd.to_datetime(curr_date) - pd.Timedelta(days=days)
    plot_data = data[data.index >= cutoff]
    if plot_data.empty:
        plot_data = data.tail(1)

    addplots = [mpf.make_addplot(plot_data[col], width=1.0) for col in ma_cols]

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    style = mpf.make_mpf_style(base_mpf_style="yahoo", rc={"font.size": 9})

    fig, axes = mpf.plot(
        plot_data[["Open", "High", "Low", "Close", "Volume"]],
        type="candle",
        addplot=addplots or None,
        volume=True,
        panel_ratios=(4, 1),
        style=style,
        title=f"\n{canonical} — {days}D",
        ylabel="Price",
        ylabel_lower="Volume",
        figsize=(11, 7),
        returnfig=True,
    )
    if ma_cols:
        # addplot lines land on ax.lines in the order they were passed.
        ma_lines = axes[0].lines[: len(ma_cols)]
        axes[0].legend(ma_lines, ma_cols, loc="upper left", fontsize=8)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    return out_path
