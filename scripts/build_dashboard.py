#!/usr/bin/env python3
"""Build (or live-serve) the market-scanner monitoring dashboard.

Reads the latest scan JSON from data/scans/ plus the journal scorecard and
renders a self-contained HTML dashboard (scripts/dashboard_template.html with the
data inlined — no external assets, opens by double-click).

    # one-shot: write data/dashboard/dashboard.html
    python scripts/build_dashboard.py

    # live monitor: serve on http://localhost:8787, regenerate each request,
    # page auto-refreshes so a scanner cron keeps it current
    python scripts/build_dashboard.py --serve --port 8787 --refresh 60

Pair with the scanner on a schedule (see README → Continuous scanning); each new
scan_*.json is picked up automatically.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

SCANS_DIR = os.path.join(_REPO_ROOT, "data", "scans")
DASH_DIR = os.path.join(_REPO_ROOT, "data", "dashboard")
TEMPLATE = os.path.join(_REPO_ROOT, "scripts", "dashboard_template.html")
_MARKER = "/*__DATA__*/ null"

_SKELETON = (
    '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Market Scanner Dashboard</title></head><body>\n{inner}\n</body></html>"
)


def _latest_scan() -> dict | None:
    files = glob.glob(os.path.join(SCANS_DIR, "scan_*.json"))
    if not files:
        return None
    with open(max(files, key=os.path.getmtime), encoding="utf-8") as fh:
        return json.load(fh)


def _journal_summary() -> dict:
    try:
        from tradingagents.scanner import journal
        return journal.summary()
    except Exception:  # noqa: BLE001 - journal is optional context
        return {}


def build_payload(live: bool, refresh: int) -> dict:
    scan = _latest_scan() or {}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "live": live,
        "refresh_seconds": refresh,
        "subtitle": "S&P 500 · 저평가 & 상승 모니터",
        "scan": scan,
        "journal": _journal_summary(),
    }


def render(payload: dict) -> str:
    with open(TEMPLATE, encoding="utf-8") as fh:
        inner = fh.read()
    data = json.dumps(payload, ensure_ascii=False)
    # Guard against a stray marker in the data breaking the splice.
    if _MARKER not in inner:
        raise RuntimeError(f"template missing data marker {_MARKER!r}")
    inner = inner.replace(_MARKER, data)
    return _SKELETON.format(inner=inner)


def write_file(out: str, live: bool, refresh: int) -> str:
    os.makedirs(os.path.dirname(out), exist_ok=True)
    html = render(build_payload(live, refresh))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    return out


def serve(port: int, refresh: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # keep the console quiet
            pass

        def do_GET(self):
            if self.path not in ("/", "/index.html"):
                self.send_error(404)
                return
            try:
                body = render(build_payload(live=True, refresh=refresh)).encode("utf-8")
            except Exception as exc:  # noqa: BLE001
                self.send_error(500, str(exc))
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Dashboard live at http://localhost:{port}  (Ctrl-C to stop)", file=sys.stderr)
    if not glob.glob(os.path.join(SCANS_DIR, "scan_*.json")):
        print("  note: no scans yet — run scripts/screen_universe.py first.", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Build/serve the scanner dashboard")
    p.add_argument("--serve", action="store_true", help="Live HTTP server with auto-refresh")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--refresh", type=int, default=60, help="Auto-refresh seconds (serve mode)")
    p.add_argument("--out", default=os.path.join(DASH_DIR, "dashboard.html"))
    args = p.parse_args()

    if args.serve:
        return serve(args.port, args.refresh)
    out = write_file(args.out, live=False, refresh=args.refresh)
    print(f"Wrote {out}\nOpen it in a browser (double-click), or use --serve for live monitoring.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
