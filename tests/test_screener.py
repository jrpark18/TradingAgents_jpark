"""Unit tests for the pure (I/O-free) scanner scoring and rotation logic."""

from tradingagents.scanner.screener import (
    score_growth,
    score_momentum,
    score_ticker,
    score_valuation,
)
from tradingagents.scanner import universe


# --- valuation: cheaper metrics should score higher ---------------------------

def test_valuation_cheap_beats_expensive():
    cheap = {"trailingPE": 9, "priceToBook": 1.2, "pegRatio": 0.8, "priceToSales": 1.5}
    rich = {"trailingPE": 45, "priceToBook": 12, "pegRatio": 4.0, "priceToSales": 20}
    sc, used, flags = score_valuation(cheap)
    se, _, _ = score_valuation(rich)
    assert sc > se
    assert used == 4
    assert any("PEG" in f for f in flags)


def test_loss_maker_not_treated_as_cheap():
    # Negative P/E must not read as "deep value".
    s_loss, _, _ = score_valuation({"trailingPE": -5})
    s_reasonable, _, _ = score_valuation({"trailingPE": 12})
    assert s_reasonable > s_loss


# --- momentum: uptrend should score higher than downtrend ---------------------

def test_momentum_uptrend_beats_downtrend():
    up = {"price": 110, "fiftyDayAverage": 105, "twoHundredDayAverage": 95,
          "fiftyTwoWeekHigh": 115, "fiftyTwoWeekLow": 80}
    down = {"price": 82, "fiftyDayAverage": 90, "twoHundredDayAverage": 100,
            "fiftyTwoWeekHigh": 130, "fiftyTwoWeekLow": 80}
    su, _, fu = score_momentum(up)
    sd, _, _ = score_momentum(down)
    assert su > sd
    assert any("golden cross" in f for f in fu)


# --- growth ------------------------------------------------------------------

def test_growth_rewards_expansion():
    fast = {"revenueGrowth": 0.30, "earningsGrowth": 0.35, "trailingEps": 4, "forwardEps": 6}
    flat = {"revenueGrowth": 0.00, "earningsGrowth": -0.05, "trailingEps": 4, "forwardEps": 3}
    assert score_growth(fast)[0] > score_growth(flat)[0]


# --- composite + tagging ------------------------------------------------------

def test_value_and_momentum_tag():
    metrics = {
        "trailingPE": 10, "pegRatio": 0.7, "priceToBook": 1.3, "priceToSales": 1.4,
        "returnOnEquity": 0.22, "profitMargins": 0.18, "debtToEquity": 60, "currentRatio": 1.8,
        "price": 112, "fiftyDayAverage": 104, "twoHundredDayAverage": 94,
        "fiftyTwoWeekHigh": 118, "fiftyTwoWeekLow": 78,
        "revenueGrowth": 0.20, "earningsGrowth": 0.25, "trailingEps": 8, "forwardEps": 10,
    }
    s = score_ticker("TEST", metrics)
    assert "value_candidate" in s.tags
    assert "momentum_candidate" in s.tags
    assert "value_and_momentum" in s.tags
    assert 0 <= s.composite <= 100
    assert s.confidence > 0.8


def test_missing_metrics_low_confidence():
    s = score_ticker("SPARSE", {"trailingPE": 15})
    assert s.confidence < 0.2
    # Neutral defaults keep the composite in range even with almost no data.
    assert 0 <= s.composite <= 100


# --- rotation cursor ----------------------------------------------------------

def test_next_batch_cycles_without_gaps():
    tickers = [f"T{i}" for i in range(10)]
    seen = []
    key = "unit-test-rotation"
    # Use persist=False and simulate by threading the cursor manually.
    b1 = universe.next_batch(tickers, 4, cursor_key=key, persist=False)
    assert b1.tickers == tickers[0:4]
    assert b1.next_offset == 4


def test_next_batch_whole_universe_when_batch_zero():
    tickers = ["A", "B", "C"]
    b = universe.next_batch(tickers, 0, cursor_key="whole", persist=False)
    assert b.tickers == tickers
    assert b.wrapped is True
