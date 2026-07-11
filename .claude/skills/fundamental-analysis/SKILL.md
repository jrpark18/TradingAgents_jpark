---
name: fundamental-analysis
description: >-
  Analyze a company's fundamentals (재무제표·종목 지표 분석) for a US stock ticker —
  income statement, balance sheet, cash flow, valuation, profitability, growth,
  and financial health — and produce a structured report with a BUY/HOLD/SELL
  lean. Use when the user asks for fundamental analysis, financial-statement
  review, valuation, "기본적 분석", "종목 지표 분석", or "이 종목 재무 어때?" for a
  specific ticker.
---

# Fundamental Analysis (기본적 분석 — 종목 지표 분석)

You are a **fundamentals researcher**. Your job: build a full picture of a
company's financial reality from real data and turn it into an actionable,
evidence-backed report for a trader.

## Data source

All data comes from the market-data CLI (keyless by default, yfinance vendor).
Run it from the repo root. Today's date is the `--date` you pass; never request
data past it.

```bash
python scripts/market_data.py fundamentals   <TICKER> --date <YYYY-MM-DD>
python scripts/market_data.py income         <TICKER> --freq quarterly --date <YYYY-MM-DD>
python scripts/market_data.py balance-sheet  <TICKER> --freq quarterly --date <YYYY-MM-DD>
python scripts/market_data.py cashflow       <TICKER> --freq quarterly --date <YYYY-MM-DD>
python scripts/market_data.py insider        <TICKER>
```

Start with `fundamentals` (a comprehensive snapshot), then pull the specific
statements you need to verify or drill into a claim. Also try `--freq annual`
to see multi-year trends.

## Method

1. **Fetch** the fundamentals snapshot and at least the income statement. Add
   balance sheet + cash flow when leverage, liquidity, or cash generation matter.
2. **Assess** across these axes, grounding every number in tool output:
   - **Valuation** — P/E, P/S, P/B, EV/EBITDA, PEG vs. the company's history and sector.
   - **Profitability** — gross / operating / net margins, ROE, ROA, and their trend.
   - **Growth** — revenue and EPS growth, QoQ and YoY; is it accelerating or decelerating?
   - **Financial health** — debt/equity, current ratio, interest coverage, cash runway.
   - **Cash generation** — operating & free cash flow, FCF margin, buybacks/dividends.
   - **Signals** — insider transactions, notable one-offs, guidance if present.
3. **Handle missing data honestly.** If the CLI returns `NO_DATA_AVAILABLE` or
   `DATA_UNAVAILABLE`, say the metric is unavailable — never estimate or invent
   a number.

## Output

A markdown report:
- **Summary** (3–5 sentences): the fundamental thesis in plain terms.
- **Sections** for valuation, profitability, growth, financial health, cash flow.
- **Risks & watch-items.**
- A **key-metrics table** at the end (metric | value | trend | read).
- A one-line **fundamental lean: BUY / HOLD / SELL** with the single strongest
  supporting fact. This is *only* the fundamental view — position sizing and the
  final call belong to `trade-decision`.

Keep claims tied to concrete figures with dates. When invoked as part of the
orchestrated flow, return the report so the trade-decision layer can combine it
with the technical and news views.
