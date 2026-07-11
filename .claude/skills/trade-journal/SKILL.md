---
name: trade-journal
description: >-
  Read and update the trading journal — the system's memory (매매 일지 · 학습).
  Log scan picks and BUY/HOLD/SELL decisions, then review past picks to measure
  realised return vs. the benchmark and learn which calls worked. Use when the
  user asks about past picks/decisions, "지난 추천 어땠어", "일지/기록 보여줘",
  "성과 리뷰", or wants the scanner/decisions to accumulate learning over time.
---

# Trade Journal (매매 일지 — 메모리/학습)

The journal is an append-only record (`data/journal/journal.jsonl`, mirrored to
`journal.md`) of every scan pick and trade decision, plus periodic reviews that
re-price past picks. It is how the system remembers and improves.

## Commands

```bash
# See history
python scripts/journal.py list --type pick --limit 30
python scripts/journal.py list --type decision

# Log a decision verdict (usually done after trade-decision)
python scripts/journal.py decision NVDA BUY --conviction High --note "AI capex cycle"

# Learning loop: re-price open picks as of a date, record return vs SPY
python scripts/journal.py review --asof 2026-07-11 --benchmark SPY

# Scorecard across all reviewed picks
python scripts/journal.py summary
```

`review` computes, for each past pick, `return_pct`, the benchmark return over
the same window, and `alpha_pct`, then appends a `review` entry. `summary`
aggregates hit-rate, average return, and average alpha — the screen's track
record.

## How to use it in analysis

- **Before** a fresh `trade-decision`, check the journal for prior picks/decisions
  on that ticker (`journal.py list`) so you build on past reasoning, not from
  scratch.
- **After** a `trade-decision`, log the verdict with `journal.py decision`.
- When the user asks "how are the picks doing", run `review` then `summary` and
  present the scorecard; call out consistent winners/losers as a learning signal
  (e.g. "momentum picks outperformed; value picks lagged this window").

Report only what the journal and re-pricing show — never fabricate a past return.
Requires network for `review` (it re-prices via the data engine).
