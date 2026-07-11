---
name: market-dashboard
description: >-
  Build or live-serve the HTML monitoring dashboard for scan results (대시보드 ·
  실시간 모니터링) — KPI tiles, sector/signal charts, score distribution, and ranked
  undervalued/uptrend/sweet-spot tables with value-trap warnings. Use when the
  user asks to see the dashboard, monitor scans in real time, "대시보드 보여줘",
  "모니터링 화면", or wants a visual view of the scanner output.
---

# Market Dashboard (실시간 모니터링 대시보드)

A self-contained HTML dashboard rendered from the latest scan (`data/scans/*.json`)
and the journal scorecard. No external assets — opens by double-click, or serve it
for live auto-refresh monitoring.

```bash
# One-shot: write data/dashboard/dashboard.html, then open it in a browser
python scripts/build_dashboard.py

# Live monitor: http://localhost:8787, regenerates each request, auto-refreshes
python scripts/build_dashboard.py --serve --port 8787 --refresh 60
```

It shows:
- **KPI tiles** — universe size, scored count, #undervalued / #uptrend / #sweet-spot,
  average composite, **value-trap count**, and (if reviews exist) journal hit-rate & alpha.
- **Sector distribution** and **signal-frequency** bar charts over the candidates.
- **Composite-score distribution** histogram.
- **Ranked tables** (Sweet Spot / Undervalued / Uptrend) with per-metric bars
  (V/M/Q/G), flag chips, and **negative-margin rows flagged as value traps** (red).

## Continuous / real-time monitoring

Run the scanner on a schedule and keep `--serve` running; each new `scan_*.json`
is picked up and the page auto-refreshes. Example (scan every 30 min, dashboard
live in the browser):

```bash
# terminal 1 — live dashboard
python scripts/build_dashboard.py --serve

# cron — rolling scan that advances through the universe
*/30 9-16 * * 1-5  cd /path/to/repo && python scripts/screen_universe.py --batch-size 150 --workers 8
```

## When invoked
1. If there's no scan yet, run `market-scanner` (Stage 1) first.
2. Build (or start serving) the dashboard and tell the user the file path / URL.
3. Point out what needs attention: the sweet-spot leaders and any value-trap flags.
Design/theme: uses the validated dataviz palette, works in light and dark.
