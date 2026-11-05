"""Delivery % per stock from NSE's daily "full bhavcopy" (read-only; the Scalp page's Deliv % columns).

NSE publishes, each trading evening (about 18:00 IST), `sec_bhavdata_full_DDMMYYYY.csv` on its archives site: per
symbol and series, the day's traded quantity (TTL_TRD_QNTY), the quantity marked for delivery (DELIV_QTY) and its
share (DELIV_PER). The rest of the traded quantity was squared off the same day (intraday). The file covers a
finished session only, so "today" means the latest published day: during market hours that is yesterday.

`report(symbols, days)` reads the latest `days` published sessions (walking back over weekends and holidays, at most
`MAX_LOOKBACK` calendar days) and gives, per stock (EQ series only): the latest day's delivery %, delivered and traded
shares, the average delivery % of the sessions before it and the change against that average. Each day's file is kept
in memory and next to the desk's database (`delivery_DDMMYYYY.csv`), so it is downloaded once; a day not published yet
is asked again after `MISS_RETRY_SEC`. Nothing in the strategy reads it; no order path.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ARCHIVE = "https://archives.nseindia.com/products/content/sec_bhavdata_full_{ddmmyyyy}.csv"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
FETCH_TIMEOUT_SEC = 20
MAX_LOOKBACK = 21  # calendar days walked back to find the sessions asked for
MAX_DAYS = 10
MISS_RETRY_SEC = 30 * 60  # a day not published yet (or a failed download) is asked again after this
KEEP_FILES = 15
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

_lock = threading.Lock()
_days: dict[dt.date, dict[str, dict]] = {}  # day -> symbol -> row
_missing: dict[dt.date, float] = {}  # day -> monotonic time it was found missing


def _num(value) -> float | None:
    s = str(value or "").strip().replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse(text: str) -> dict[str, dict]:
    """EQ rows of a full bhavcopy: symbol -> traded / delivered shares, delivery %, close."""
    out: dict[str, dict] = {}
    for raw in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        row = {str(k).strip().upper(): str(v or "").strip() for k, v in raw.items() if k}
        if row.get("SERIES", "").upper() != "EQ":
            continue
        symbol = row.get("SYMBOL", "").upper()
        traded = _num(row.get("TTL_TRD_QNTY"))
        if not symbol or not traded:
            continue
        deliv = _num(row.get("DELIV_QTY"))
        pct = _num(row.get("DELIV_PER"))
        if pct is None and deliv is not None:
            pct = 100.0 * deliv / traded
        out[symbol] = {
            "traded": int(traded),
            "delivered": int(deliv) if deliv is not None else None,
            "pct": round(pct, 2) if pct is not None else None,
            "close": _num(row.get("CLOSE_PRICE")),
        }
    return out


def _cache_dir() -> Path:
    explicit = os.environ.get("DELIVERY_CACHE_DIR", "").strip()
    if explicit:
        return Path(explicit)
    url = os.environ.get("DATABASE_URL", "")
    if "sqlite" in url and ":///" in url:
        return Path(url.split(":///", 1)[1]).parent
    return Path(".")


def _file(day: dt.date) -> Path:
    return _cache_dir() / f"delivery_{day:%d%m%Y}.csv"


def _download(day: dt.date) -> str | None:
    """The day's file, or None when NSE has not published it (404)."""
    request = urllib.request.Request(ARCHIVE.format(ddmmyyyy=f"{day:%d%m%Y}"), headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SEC) as res:  # noqa: S310 - fixed NSE URL
            return res.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def _prune() -> None:
    try:
        files = sorted(_cache_dir().glob("delivery_*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[KEEP_FILES:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def load_day(day: dt.date, fetch=_download) -> dict[str, dict] | None:
    """One session's rows (memory, then the saved file, then NSE); None when there is no file for that day."""
    with _lock:
        if day in _days:
            return _days[day]
        missed = _missing.get(day)
        if missed is not None and time.monotonic() - missed < MISS_RETRY_SEC:
            return None
    path = _file(day)
    rows: dict[str, dict] | None = None
    try:
        rows = parse(path.read_text(encoding="utf-8")) or None
    except OSError:
        rows = None
    if rows is None:
        text = fetch(day)
        rows = parse(text) if text else None
        if rows:
            try:
                path.write_text(text, encoding="utf-8")
                _prune()
            except OSError:
                pass
    with _lock:
        if rows:
            _days[day] = rows
            _missing.pop(day, None)
        else:
            _missing[day] = time.monotonic()
    return rows


def sessions(days: int, today: dt.date | None = None, fetch=_download) -> tuple[list[tuple[dt.date, dict]], list[str]]:
    """The latest `days` published sessions, newest first, and any download errors."""
    today = today or dt.datetime.now(IST).date()
    found: list[tuple[dt.date, dict]] = []
    errors: list[str] = []
    for back in range(MAX_LOOKBACK):
        if len(found) >= days:
            break
        day = today - dt.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        try:
            rows = load_day(day, fetch)
        except Exception as exc:  # noqa: BLE001 - one bad day must not hide the rest
            with _lock:
                _missing[day] = time.monotonic()
            errors.append(f"{day.isoformat()}: {exc}")
            continue
        if rows:
            found.append((day, rows))
    return found, errors


def report(symbols: list[str] | None, days: int = 5, today: dt.date | None = None, fetch=_download) -> dict:
    """Per stock: the latest session's delivery and its average over the sessions before it."""
    days = max(1, min(MAX_DAYS, int(days)))
    found, errors = sessions(days, today, fetch)
    if not found:
        return {"day": None, "days": [], "rows": {}, "errors": errors or ["NSE has no delivery file in the last weeks"]}
    latest_day, latest = found[0]
    names = [s.upper() for s in symbols] if symbols else sorted(latest)
    rows: dict[str, dict] = {}
    for name in names:
        now = latest.get(name)
        if not now:
            continue
        history = [{"day": d.isoformat(), "pct": r[name]["pct"]} for d, r in found if name in r]
        before = [h["pct"] for h in history[1:] if h["pct"] is not None]
        avg = round(sum(before) / len(before), 2) if before else None
        rows[name] = {
            "pct": now["pct"],
            "delivered": now["delivered"],
            "traded": now["traded"],
            "intraday": (now["traded"] - now["delivered"]) if now["delivered"] is not None else None,
            "avg_pct": avg,
            "vs_avg": round(now["pct"] - avg, 2) if (avg is not None and now["pct"] is not None) else None,
            "history": history,
        }
    return {"day": latest_day.isoformat(), "days": [d.isoformat() for d, _ in found], "rows": rows, "errors": errors}


def clear_memory() -> None:
    with _lock:
        _days.clear()
        _missing.clear()
