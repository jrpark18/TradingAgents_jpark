---
name: backtest
description: >-
  Backtest the momentum screen against history (백테스트) — reconstruct the screen
  as of past dates (look-ahead safe) and measure forward returns of the top picks
  vs. SPY, reporting hit-rate and alpha. Use when the user asks to backtest, test
  the strategy, "이 전략 검증", "과거 수익률", or validate the scanner before trusting it.
---

# Backtest (모멘텀 스크린 백테스트)

Validates the scanner's **momentum** screen by replaying it on historical dates
and measuring what the picks actually returned.

```bash
python scripts/backtest_screen.py \
    --asof 2026-01-15,2026-03-16,2026-05-15 \
    --indices sp100,dow30 --top 10 --horizon 21
```

For each `--asof` date the script reconstructs each ticker's momentum metrics
from price history **up to that date only** (no look-ahead), ranks, takes the top
`--top`, then measures the `--horizon`-day forward return vs. `--benchmark`
(default SPY). It prints per-date results and an aggregate hit-rate / average
return / average alpha.

## Method notes (be honest about scope)

- **Momentum is backtestable** here because it reconstructs cleanly from prices.
- **Valuation is NOT backtested**: yfinance's fundamentals snapshot is live-only
  (no point-in-time P/E history). State this when reporting — the backtest covers
  the momentum leg, not the value leg. A full valuation backtest needs a
  point-in-time fundamentals source (extension noted in `trading-agent-dev`).
- Results depend on the current universe files; refresh them first for a
  representative test, and prefer multiple `--asof` dates across different regimes
  to avoid a single lucky window.

## When invoked

1. Pick several `--asof` dates spanning different market conditions.
2. Run the backtest; read the aggregate.
3. Report hit-rate, avg return, and alpha plainly, with the valuation caveat.
4. Optionally log the run's takeaways for the user. Do not overclaim — a
   backtest is evidence, not a guarantee. Decision-support, not financial advice.
