#!/usr/bin/env python3
"""Refresh index constituents into data/universe/*.txt.

Fetches current members of the target indices from public sources and writes one
ticker per line (normalised to yfinance's convention, e.g. BRK.B -> BRK-B).
Requires network access; each source is isolated so one failure doesn't abort
the rest. Constituents drift, so re-run periodically (e.g. monthly).

    python scripts/refresh_universe.py                 # all indices
    python scripts/refresh_universe.py --only dow30,sp100

Sources: Wikipedia (Nasdaq-100, S&P 100, Dow 30) and the iShares IWB holdings
CSV (Russell 1000). If a source's layout changes, adjust the extractor below.
"""

from __future__ import annotations

import argparse
import io
import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

UNIVERSE_DIR = os.path.join(_REPO_ROOT, "data", "universe")

WIKI = {
    "nasdaq100": ("https://en.wikipedia.org/wiki/Nasdaq-100", ("Ticker", "Symbol")),
    "sp100": ("https://en.wikipedia.org/wiki/S%26P_100", ("Symbol", "Ticker")),
    "dow30": ("https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average", ("Symbol", "Ticker")),
}
# iShares Russell 1000 ETF (IWB) holdings CSV.
IWB_CSV = ("https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/"
           "1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund")


def _norm(sym: str) -> str:
    return sym.strip().upper().replace(".", "-").replace(" ", "")


def _write(name: str, tickers: list[str]) -> None:
    tickers = sorted({t for t in (_norm(s) for s in tickers) if t and t.isascii()})
    path = os.path.join(UNIVERSE_DIR, f"{name}.txt")
    os.makedirs(UNIVERSE_DIR, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f"# {name} constituents — refreshed by scripts/refresh_universe.py\n")
        fh.write("\n".join(tickers) + "\n")
    print(f"  wrote {len(tickers)} tickers -> {path}")


def _fetch_wiki(url: str, candidate_cols) -> list[str]:
    import pandas as pd
    import requests

    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    tables = pd.read_html(io.StringIO(html))
    for tbl in tables:
        cols = {str(c).strip(): c for c in tbl.columns}
        for want in candidate_cols:
            if want in cols:
                vals = tbl[cols[want]].dropna().astype(str).tolist()
                # Heuristic: a constituents table has many short ticker-like cells.
                if len(vals) >= 25 and all(len(v) <= 6 for v in vals[:5]):
                    return vals
    raise ValueError(f"no ticker column {candidate_cols} found among tables")


def _fetch_iwb() -> list[str]:
    import pandas as pd
    import requests

    raw = requests.get(IWB_CSV, headers={"User-Agent": "Mozilla/5.0"}, timeout=60).content
    text = raw.decode("utf-8", errors="ignore")
    # The CSV has preamble lines before the header row containing "Ticker".
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.lower().startswith("ticker,")
                  or ",ticker," in ln.lower() or ln.split(",")[0].strip('"').lower() == "ticker"), None)
    if start is None:
        raise ValueError("could not locate header row in IWB CSV")
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    col = next((c for c in df.columns if str(c).strip().lower() == "ticker"), df.columns[0])
    tickers = df[col].dropna().astype(str).tolist()
    # Drop cash/other non-equity rows (blank, '-', long names).
    return [t for t in tickers if t and t not in {"-", "--"} and len(t) <= 6]


def run(args) -> int:
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    targets = {
        "nasdaq100": lambda: _fetch_wiki(*WIKI["nasdaq100"]),
        "sp100": lambda: _fetch_wiki(*WIKI["sp100"]),
        "dow30": lambda: _fetch_wiki(*WIKI["dow30"]),
        "russell1000": _fetch_iwb,
    }
    rc = 0
    for name, fetch in targets.items():
        if only and name not in only:
            continue
        print(f"Refreshing {name} ...")
        try:
            _write(name, fetch())
        except Exception as exc:  # noqa: BLE001
            print(f"  FAILED {name}: {type(exc).__name__}: {exc}", file=sys.stderr)
            rc = 1
    return rc


def build_parser():
    p = argparse.ArgumentParser(description="Refresh index constituent lists")
    p.add_argument("--only", default="", help="Comma-separated subset: nasdaq100,sp100,dow30,russell1000")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
