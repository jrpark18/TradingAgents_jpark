"""Universe management for the continuous market scanner.

Loads index constituents from ``data/universe/*.txt`` (one ticker per line,
``#`` comments allowed), unions the requested indices, de-duplicates, and
provides a persistent rotation cursor so a cron job can sweep a large universe
(e.g. Russell 1000) in fixed-size batches over successive runs.

Ships with ``seed_megacaps.txt`` and ``dow30.txt`` so the scanner works out of
the box. Run ``scripts/refresh_universe.py`` to populate the full, authoritative
``nasdaq100.txt`` / ``sp100.txt`` / ``dow30.txt`` / ``russell1000.txt`` lists.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
UNIVERSE_DIR = os.path.join(_REPO_ROOT, "data", "universe")
_CURSOR_PATH = os.path.join(_REPO_ROOT, "data", "scans", ".cursor.json")

# Logical index name -> constituent file. Missing files are skipped with a
# warning rather than crashing, so a fresh checkout still scans the seed set.
INDEX_FILES = {
    "seed": "seed_megacaps.txt",
    "nasdaq100": "nasdaq100.txt",
    "sp100": "sp100.txt",
    "dow30": "dow30.txt",
    "russell1000": "russell1000.txt",
}

# The four indices the scanner targets by default (per the project brief).
DEFAULT_INDICES = ("nasdaq100", "sp100", "dow30", "russell1000")


def _read_ticker_file(path: str) -> list[str]:
    tickers: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if line:
                tickers.append(line.upper())
    return tickers


def load_index(name: str) -> list[str]:
    """Return the constituents of one index, or [] if its file is absent."""
    fname = INDEX_FILES.get(name)
    if fname is None:
        raise ValueError(f"Unknown index {name!r}. Known: {sorted(INDEX_FILES)}")
    path = os.path.join(UNIVERSE_DIR, fname)
    if not os.path.exists(path):
        return []
    return _read_ticker_file(path)


def load_universe(indices=DEFAULT_INDICES, fallback_to_seed: bool = True) -> list[str]:
    """Union the requested indices into a sorted, de-duplicated ticker list.

    If none of the requested index files exist yet (fresh checkout), fall back
    to the bundled seed list so the scanner is never empty.
    """
    seen: dict[str, None] = {}
    populated = []
    for name in indices:
        members = load_index(name)
        if members:
            populated.append(name)
        for t in members:
            seen.setdefault(t, None)
    if not seen and fallback_to_seed:
        for t in load_index("seed"):
            seen.setdefault(t, None)
    return sorted(seen)


def coverage(indices=DEFAULT_INDICES) -> dict[str, int]:
    """Per-index constituent counts — for reporting which lists are populated."""
    return {name: len(load_index(name)) for name in indices}


@dataclass
class RotationBatch:
    tickers: list[str]
    offset: int          # start index of this batch in the universe
    next_offset: int     # cursor to persist for the following run
    universe_size: int
    wrapped: bool        # True if this batch wrapped past the end (cycle complete)


def _load_cursor(key: str) -> int:
    if not os.path.exists(_CURSOR_PATH):
        return 0
    try:
        with open(_CURSOR_PATH, encoding="utf-8") as fh:
            return int(json.load(fh).get(key, 0))
    except (ValueError, OSError, json.JSONDecodeError):
        return 0


def _save_cursor(key: str, value: int) -> None:
    os.makedirs(os.path.dirname(_CURSOR_PATH), exist_ok=True)
    data = {}
    if os.path.exists(_CURSOR_PATH):
        try:
            with open(_CURSOR_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            data = {}
    data[key] = value
    with open(_CURSOR_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


def next_batch(
    tickers: list[str],
    batch_size: int,
    cursor_key: str = "default",
    persist: bool = True,
) -> RotationBatch:
    """Slice the next ``batch_size`` tickers, advancing a persistent cursor.

    Successive calls sweep the whole universe and wrap around, so a cron job can
    "continuously cycle" a 1000-name universe in, say, 100-name chunks. Wrapping
    slices are contiguous (they include the head of the list) so no ticker is
    skipped at the boundary. ``batch_size <= 0`` means "the whole universe".
    """
    n = len(tickers)
    if n == 0:
        return RotationBatch([], 0, 0, 0, False)
    if batch_size <= 0 or batch_size >= n:
        if persist:
            _save_cursor(cursor_key, 0)
        return RotationBatch(list(tickers), 0, 0, n, True)

    start = _load_cursor(cursor_key) % n
    end = start + batch_size
    wrapped = end >= n
    batch = tickers[start:end] + (tickers[: end - n] if wrapped else [])
    nxt = (end) % n
    if persist:
        _save_cursor(cursor_key, nxt)
    return RotationBatch(batch, start, nxt, n, wrapped)
