---
name: market-scanner
description: >-
  Continuously scan the big US index universe (Nasdaq-100, S&P 100, Dow 30,
  Russell 1000) to surface undervalued and uptrend/상승예측 candidates, then hand
  the shortlist to deep multi-agent analysis (시장 순환 스캔 · 저평가/상승 종목 발굴).
  Use when the user asks to scan the market, find candidates/저평가 종목/상승 종목,
  run the daily screen, "오늘 뭐 살만한 종목", or set up continuous/unattended scanning.
---

# Market Scanner (시장 순환 스캔 → 종목 발굴)

A two-stage funnel that lets you watch hundreds–thousands of tickers cheaply and
spend expensive multi-agent analysis only where it matters.

## Stage 1 — headless quantitative screen (LLM-free, cheap)

Runs `scripts/screen_universe.py`, which pulls one metrics snapshot per ticker
and scores **valuation / quality / momentum / growth**, then ranks:
- **undervalued** (cheap + not a value trap),
- **uptrend** (momentum + growth),
- **value_and_momentum** (the sweet spot: 저평가 & 상승).

```bash
# One cron-sized batch (rotates a cursor so successive runs cover the universe)
python scripts/screen_universe.py --batch-size 150 --top 25

# Specific indices, whole set at once, parallel fetch
python scripts/screen_universe.py --indices sp100,dow30 --batch-size 0 --workers 8
```

Outputs `data/scans/scan_<date>_<offset>.json` + `data/scans/latest_scan.md`, and
logs top picks to the journal (see `trade-journal`). Read `latest_scan.md` (or the
console table) to get the shortlist.

**Universe:** out of the box only Dow 30 ships populated. Run
`python scripts/refresh_universe.py` (needs network) to populate **S&P 500 /
Nasdaq-100 / S&P 100 / Dow 30** (~500+ names) in `data/universe/`. S&P 500 is the
reliable broad universe; **Russell 1000 is best-effort** (its iShares source is
bot-blocked — on failure, save IWB holdings tickers to
`data/universe/russell1000.txt` manually). Russell 1000 ⊃ S&P 500, so coverage is
largely preserved either way.

## Stage 2 — deep dive (multi-agent)

Take the top N names from Stage 1 and run the **`trade-decision`** skill on each
(fundamental + technical + news agents → bull/bear → BUY/HOLD/SELL). Rank the
results by conviction and present the ranked shortlist.

## Continuous / unattended operation

The rotation cursor means each run advances through the universe, so a scheduled
job "continuously cycles" the market. To automate (manual market, so the user
opts in), see the cron / Routine setup in `README.md` (Continuous scanning). This
skill does not create schedules itself.

## Workflow when invoked

1. Confirm indices (default: all four) and batch size (default 150).
2. Run Stage 1; read `latest_scan.md`.
3. Present the undervalued / uptrend / sweet-spot tables.
4. Offer to run Stage 2 `trade-decision` on the top few, or the specific tickers
   the user cares about.
5. Ground everything in the scan output; never invent tickers or scores. Note
   any coverage gaps (e.g. "run refresh_universe.py — only Dow 30 is populated").

This is decision-support, not financial advice.
