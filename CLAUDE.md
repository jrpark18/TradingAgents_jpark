# TradingAgents — project notes

## Recording analysis sessions

The dashboard's Result / History / Log tabs read one directory:
`data/dashboard/analyses/`. The web trigger writes there itself. **Any other path
— Telegram, an interactive session, cron — must record the session explicitly**,
or the analysis is invisible in the dashboard.

After finishing a `trade-decision` run (single ticker or a comparison):

1. Log the verdict with `python scripts/journal.py decision …` as usual.
2. Then record the session:

```bash
python scripts/session_log.py record \
  --tickers AAPL \
  --source telegram --requested-by <telegram user id> \
  --started-at <ISO timestamp when the analysis began> \
  --report-file <markdown report>       # or --report "…", or - for stdin
```

Use `--source telegram` with the user's numeric id, `--source cli` (the default)
otherwise. `--started-at` matters: badges are read back from the journal counting
only entries logged at/after it, so passing the real start time is what keeps a
session from inheriting an older verdict. Journal first, then record.

Both writers go through `tradingagents/scanner/session_store.py` — put any change
to the record shape there, not in a caller.

## Language

Journal `note` fields and analysis reports are written in Korean (tickers,
figures, and English abbreviations stay as-is).
