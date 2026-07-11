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
import re
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

UNIVERSE_DIR = os.path.join(_REPO_ROOT, "data", "universe")

WIKI = {
    "sp500": ("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ("Symbol", "Ticker")),
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


# A cell that looks like a US ticker after stripping wiki footnote markers.
_FOOTNOTE = re.compile(r"\[.*?\]")
_TICKER_RE = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")


def _clean_cell(v: str) -> str:
    return _FOOTNOTE.sub("", str(v)).strip().upper()


def _fetch_wiki(url: str, candidate_cols=None) -> list[str]:
    """Find the constituents column by content, not header name.

    Wikipedia periodically renames/reorders columns and wraps headers in
    MultiIndexes, so instead of matching 'Ticker'/'Symbol' we scan every column
    of every table and pick the one whose cells are overwhelmingly ticker-like.
    """
    import pandas as pd
    import requests

    html = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
    tables = pd.read_html(io.StringIO(html))

    def ticker_cells(tbl, col):
        cells = [c for c in (_clean_cell(v) for v in tbl[col].dropna().tolist()) if c]
        if len(cells) < 25:
            return None
        hits = [c for c in cells if _TICKER_RE.match(c)]
        return hits if len(hits) / len(cells) >= 0.8 else None

    def header_text(col) -> str:
        return " ".join(str(p) for p in (col if isinstance(col, tuple) else (col,))).lower()

    # Pass 1: a column explicitly headed 'symbol'/'ticker' (the normal case,
    # robust even if other short-text columns look ticker-like).
    for tbl in tables:
        for col in tbl.columns:
            if ("symbol" in header_text(col) or "ticker" in header_text(col)):
                hits = ticker_cells(tbl, col)
                if hits:
                    return hits
    # Pass 2: fall back to the column with the most ticker-like cells.
    best: list[str] = []
    for tbl in tables:
        for col in tbl.columns:
            hits = ticker_cells(tbl, col)
            if hits and len(hits) > len(best):
                best = hits
    if not best:
        raise ValueError("no ticker-like column found among tables")
    return best


def _fetch_iwb() -> list[str]:
    """Parse the iShares IWB (Russell 1000) holdings CSV.

    The file has a metadata preamble, then a header row whose first field is
    'Ticker'. Handle a BOM and quoted fields; on failure include a snippet of
    what came back so the format can be diagnosed (iShares occasionally serves
    an HTML block page instead of the CSV).
    """
    import pandas as pd
    import requests

    # iShares bot-blocks bare requests (returns an HTML page). Present fuller
    # browser-like headers; if it still serves HTML we raise with a snippet.
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
        "Accept": "text/csv,application/csv,*/*",
        "Referer": "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/",
    }
    raw = requests.get(IWB_CSV, headers=headers, timeout=60).content
    text = raw.decode("utf-8-sig", errors="ignore")
    lines = text.splitlines()
    start = next(
        (i for i, ln in enumerate(lines)
         if ln.split(",")[0].strip().strip('"').strip().lower() == "ticker"),
        None,
    )
    if start is None:
        looks_html = text.lstrip().lower().startswith(("<!doctype", "<html"))
        hint = (" iShares served an HTML block page, not the CSV. Russell 1000 is "
                "optional — sp500 covers most of it. To use Russell 1000 anyway, "
                "download the IWB holdings CSV in a browser and save its tickers "
                "to data/universe/russell1000.txt (one per line)."
                if looks_html else "")
        snippet = text[:200].replace("\n", " ⏎ ")
        raise ValueError(f"could not locate header row in IWB CSV.{hint} got: {snippet!r}")
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])))
    col = next((c for c in df.columns if str(c).strip().lower() == "ticker"), df.columns[0])
    cells = [_clean_cell(v) for v in df[col].dropna().tolist()]
    # Keep equity tickers; drop cash/derivative rows ('-', blanks, long names).
    return [c for c in cells if _TICKER_RE.match(c)]


def run(args) -> int:
    only = set(s.strip() for s in args.only.split(",")) if args.only else None
    targets = {
        "sp500": lambda: _fetch_wiki(*WIKI["sp500"]),
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
    p.add_argument("--only", default="", help="Comma-separated subset: sp500,nasdaq100,sp100,dow30,russell1000")
    return p


if __name__ == "__main__":
    raise SystemExit(run(build_parser().parse_args()))
