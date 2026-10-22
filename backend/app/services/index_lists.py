"""Which stocks are in an NSE index (Nifty 50, Nifty 100, ...), for the Scalp page's scan lists.

NSE publishes each index's constituents as a CSV (Company Name, Industry, Symbol,
Series, ISIN Code) on its archives site. A list is downloaded on first use,
kept in memory for `CACHE_TTL_SEC` and in a file next to the desk's database,
so a failed download (NSE blocking a cloud machine, a network blip) serves the
last good copy instead of nothing. Read-only; nothing in the strategy reads it.
"""
from __future__ import annotations

import csv
import io
import os
import threading
import time
import urllib.request
from pathlib import Path

CACHE_TTL_SEC = 12 * 3600
FETCH_TIMEOUT_SEC = 15
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
ARCHIVE = "https://archives.nseindia.com/content/indices/{file}"

# key -> (label, CSV file on NSE's archives)
INDEXES: dict[str, tuple[str, str]] = {
    "nifty50": ("Nifty 50", "ind_nifty50list.csv"),
    "nifty100": ("Nifty 100", "ind_nifty100list.csv"),
    "niftybank": ("Nifty Bank", "ind_niftybanklist.csv"),
}

_lock = threading.Lock()
_memory: dict[str, tuple[float, list[str]]] = {}


def parse(text: str) -> list[str]:
    """The Symbol column of NSE's index CSV, upper-case, in file order, no blanks or repeats."""
    out: list[str] = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        symbol = str({k.strip().lower(): v for k, v in row.items() if k}.get("symbol") or "").strip().upper()
        if symbol and symbol not in out:
            out.append(symbol)
    return out


def _cache_file(key: str) -> Path:
    explicit = os.environ.get("INDEX_CACHE_DIR", "").strip()
    if explicit:
        return Path(explicit) / f"index_{key}.csv"
    url = os.environ.get("DATABASE_URL", "")
    if "sqlite" in url and ":///" in url:
        return Path(url.split(":///", 1)[1]).with_name(f"index_{key}.csv")
    return Path(f"index_{key}.csv")


def _download(key: str) -> str:
    request = urllib.request.Request(ARCHIVE.format(file=INDEXES[key][1]), headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SEC) as res:  # noqa: S310 - fixed NSE URL
        return res.read().decode("utf-8", errors="replace")


def members(key: str) -> list[str]:
    """The index's stocks; raises with the reason when there is no fresh or saved copy."""
    if key not in INDEXES:
        raise ValueError(f"unknown index list {key!r}")
    with _lock:
        hit = _memory.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_TTL_SEC:
            return list(hit[1])
    path = _cache_file(key)
    try:
        text = _download(key)
        names = parse(text)
        if not names:
            raise RuntimeError("the list came back empty")
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass  # a read-only disk: memory only
    except Exception as exc:  # noqa: BLE001 - fall back to the last saved copy
        try:
            names = parse(path.read_text(encoding="utf-8"))
        except OSError:
            names = []
        if not names:
            raise RuntimeError(f"Could not load the {INDEXES[key][0]} list from NSE: {exc}") from exc
    with _lock:
        _memory[key] = (time.monotonic(), names)
    return list(names)
