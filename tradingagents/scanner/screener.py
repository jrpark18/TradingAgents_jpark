"""Stage-1 quantitative screener: cheap, LLM-free, headless.

Given a plain ``metrics`` dict (yfinance ``info`` fields), ``score_ticker``
produces 0-100 sub-scores for **valuation** (cheapness), **quality** (avoid
value traps), **momentum** (uptrend), and **growth**, plus a blended composite
and human-readable flags. Scoring is a pure function of the metrics dict so it
is fully unit-testable without network access.

``fetch_metrics`` is the only I/O: it pulls the ``info`` snapshot from yfinance
(reusing the engine's symbol normalisation). Keeping the two apart means the
whole ranking logic can be tested with synthetic inputs, and a live sweep of
hundreds of tickers costs exactly one ``info`` call each.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


# ---------------------------------------------------------------------------
# Pure scoring (no I/O)
# ---------------------------------------------------------------------------

def _num(v: Any) -> float | None:
    """Coerce to float, treating None/NaN/non-numeric as missing."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _band(x: float, lo: float, hi: float, lo_score: float, hi_score: float) -> float:
    """Linearly map x in [lo, hi] to [lo_score, hi_score], clamped outside."""
    if hi == lo:
        return (lo_score + hi_score) / 2
    t = (x - lo) / (hi - lo)
    t = max(0.0, min(1.0, t))
    return lo_score + t * (hi_score - lo_score)


@dataclass
class TickerScore:
    ticker: str
    valuation: float                 # 0-100, higher = cheaper (attractive)
    quality: float                   # 0-100, higher = healthier
    momentum: float                  # 0-100, higher = stronger uptrend
    growth: float                    # 0-100, higher = faster growth
    composite: float                 # blended 0-100
    confidence: float                # 0-1, share of inputs that were available
    flags: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)  # value_candidate / momentum_candidate
    metrics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def score_valuation(m: dict) -> tuple[float, int, list[str]]:
    """Cheapness. Returns (score 0-100, n_inputs_used, flags)."""
    parts, flags, used = [], [], 0
    pe = _num(m.get("trailingPE"))
    if pe is not None:
        used += 1
        if pe <= 0:
            parts.append(35.0)  # loss-making: not "cheap", mild penalty
        else:
            parts.append(_band(pe, 10, 40, 90, 20))
            if pe < 15:
                flags.append(f"low P/E {pe:.1f}")
    fpe = _num(m.get("forwardPE"))
    if fpe is not None and pe is not None and fpe > 0 and pe > 0:
        used += 1
        # forward < trailing => earnings expected to grow into the multiple
        parts.append(60.0 if fpe < pe else 40.0)
        if fpe < pe * 0.85:
            flags.append("forward P/E improving")
    peg = _num(m.get("pegRatio"))
    if peg is not None and peg > 0:
        used += 1
        parts.append(_band(peg, 0.5, 3.0, 90, 20))
        if peg < 1:
            flags.append(f"PEG {peg:.2f} < 1")
    pb = _num(m.get("priceToBook"))
    if pb is not None and pb > 0:
        used += 1
        parts.append(_band(pb, 1.0, 8.0, 85, 25))
    ps = _num(m.get("priceToSales"))
    if ps is not None and ps > 0:
        used += 1
        parts.append(_band(ps, 1.0, 15.0, 85, 25))
    score = sum(parts) / len(parts) if parts else 50.0
    return score, used, flags


def score_quality(m: dict) -> tuple[float, int, list[str]]:
    parts, flags, used = [], [], 0
    roe = _num(m.get("returnOnEquity"))
    if roe is not None:
        used += 1
        parts.append(_band(roe, 0.0, 0.30, 40, 95))
        if roe > 0.20:
            flags.append(f"ROE {roe*100:.0f}%")
    pm = _num(m.get("profitMargins"))
    if pm is not None:
        used += 1
        parts.append(_band(pm, -0.05, 0.25, 25, 95))
        if pm < 0:
            flags.append("negative margins")
    de = _num(m.get("debtToEquity"))
    if de is not None:
        used += 1
        # yfinance reports debtToEquity as a percentage (e.g. 150 = 1.5x)
        parts.append(_band(de, 30, 250, 90, 30))
        if de > 250:
            flags.append(f"high leverage D/E {de:.0f}")
    cr = _num(m.get("currentRatio"))
    if cr is not None:
        used += 1
        parts.append(_band(cr, 0.8, 2.5, 30, 90))
    score = sum(parts) / len(parts) if parts else 50.0
    return score, used, flags


def score_momentum(m: dict) -> tuple[float, int, list[str]]:
    parts, flags, used = [], [], 0
    price = _num(m.get("price"))
    sma50 = _num(m.get("fiftyDayAverage"))
    sma200 = _num(m.get("twoHundredDayAverage"))
    if price and sma200:
        used += 1
        above = price > sma200
        parts.append(75.0 if above else 30.0)
        flags.append("above 200-day" if above else "below 200-day")
    if price and sma50:
        used += 1
        parts.append(70.0 if price > sma50 else 35.0)
    if sma50 and sma200:
        used += 1
        golden = sma50 > sma200
        parts.append(75.0 if golden else 35.0)
        if golden:
            flags.append("golden cross (50>200)")
    hi = _num(m.get("fiftyTwoWeekHigh"))
    lo = _num(m.get("fiftyTwoWeekLow"))
    if price and hi and lo and hi > lo:
        used += 1
        pos = (price - lo) / (hi - lo)  # 0 at low, 1 at high
        # Reward upper half (strength) but not blow-off exactly at the high.
        parts.append(_band(pos, 0.2, 0.9, 35, 85))
        if pos >= 0.9:
            flags.append("near 52w high")
        elif pos <= 0.15:
            flags.append("near 52w low")
    score = sum(parts) / len(parts) if parts else 50.0
    return score, used, flags


def score_growth(m: dict) -> tuple[float, int, list[str]]:
    parts, flags, used = [], [], 0
    rg = _num(m.get("revenueGrowth"))
    if rg is not None:
        used += 1
        parts.append(_band(rg, -0.05, 0.30, 25, 95))
        if rg > 0.20:
            flags.append(f"revenue +{rg*100:.0f}%")
    eg = _num(m.get("earningsGrowth"))
    if eg is not None:
        used += 1
        parts.append(_band(eg, -0.05, 0.40, 25, 95))
        if eg > 0.25:
            flags.append(f"earnings +{eg*100:.0f}%")
    teps = _num(m.get("trailingEps"))
    feps = _num(m.get("forwardEps"))
    if teps is not None and feps is not None:
        used += 1
        parts.append(70.0 if feps > teps else 40.0)
    score = sum(parts) / len(parts) if parts else 50.0
    return score, used, flags


# Composite weights. Tuned so a cheap-but-quality name and a strong-uptrend name
# both surface; growth is a modifier.
_WEIGHTS = {"valuation": 0.35, "momentum": 0.30, "quality": 0.20, "growth": 0.15}
_MAX_INPUTS = 5 + 4 + 4 + 3  # valuation + quality + momentum + growth input slots


def score_ticker(ticker: str, metrics: dict) -> TickerScore:
    """Blend the four dimensions into a composite and tag candidates."""
    val, uv, fv = score_valuation(metrics)
    qual, uq, fq = score_quality(metrics)
    mom, um, fm = score_momentum(metrics)
    grow, ug, fg = score_growth(metrics)

    composite = (
        _WEIGHTS["valuation"] * val
        + _WEIGHTS["momentum"] * mom
        + _WEIGHTS["quality"] * qual
        + _WEIGHTS["growth"] * grow
    )
    confidence = round((uv + uq + um + ug) / _MAX_INPUTS, 2)

    tags = []
    # Undervalued: cheap AND not a value trap.
    if val >= 65 and qual >= 45:
        tags.append("value_candidate")
    # Uptrend / predicted-to-rise: strong momentum, ideally with growth.
    if mom >= 65 and grow >= 45:
        tags.append("momentum_candidate")
    # The sweet spot the brief asks for: undervalued AND rising.
    if "value_candidate" in tags and "momentum_candidate" in tags:
        tags.append("value_and_momentum")

    return TickerScore(
        ticker=ticker,
        valuation=round(val, 1),
        quality=round(qual, 1),
        momentum=round(mom, 1),
        growth=round(grow, 1),
        composite=round(composite, 1),
        confidence=confidence,
        flags=fv + fq + fm + fg,
        tags=tags,
        metrics=metrics,
    )


# ---------------------------------------------------------------------------
# I/O: fetch the metrics snapshot (network)
# ---------------------------------------------------------------------------

# yfinance ``info`` key -> our normalised metrics key.
_INFO_MAP = {
    "trailingPE": "trailingPE",
    "forwardPE": "forwardPE",
    "pegRatio": "pegRatio",
    "trailingPegRatio": "pegRatio",   # fallback key some responses use
    "priceToBook": "priceToBook",
    "priceToSalesTrailing12Months": "priceToSales",
    "returnOnEquity": "returnOnEquity",
    "profitMargins": "profitMargins",
    "operatingMargins": "operatingMargins",
    "debtToEquity": "debtToEquity",
    "currentRatio": "currentRatio",
    "revenueGrowth": "revenueGrowth",
    "earningsGrowth": "earningsGrowth",
    "trailingEps": "trailingEps",
    "forwardEps": "forwardEps",
    "fiftyDayAverage": "fiftyDayAverage",
    "twoHundredDayAverage": "twoHundredDayAverage",
    "fiftyTwoWeekHigh": "fiftyTwoWeekHigh",
    "fiftyTwoWeekLow": "fiftyTwoWeekLow",
    "marketCap": "marketCap",
    "sector": "sector",
    "industry": "industry",
    "shortName": "name",
    "longName": "name",
}


def fetch_metrics(ticker: str) -> dict:
    """Pull the current fundamentals/price snapshot for one ticker.

    Uses yfinance directly (the same library and symbol normalisation the engine
    uses) because Stage-1 needs *numbers*, not the LLM-formatted report strings
    that ``route_to_vendor`` returns. Raises on a genuinely unknown symbol so the
    caller can skip it.
    """
    import yfinance as yf  # local import: keep pure scoring importable without yfinance

    from tradingagents.dataflows.symbol_utils import normalize_symbol

    canonical = normalize_symbol(ticker)
    info = yf.Ticker(canonical).info or {}
    metrics: dict = {"ticker": ticker, "canonical": canonical}
    for src, dst in _INFO_MAP.items():
        if src in info and info[src] is not None and metrics.get(dst) is None:
            metrics[dst] = info[src]
    # Current price: prefer explicit fields, fall back to previous close.
    for k in ("currentPrice", "regularMarketPrice", "regularMarketPreviousClose", "previousClose"):
        if info.get(k) is not None:
            metrics["price"] = info[k]
            break
    return metrics
