"""Second-by-second prices of the watched stocks, recorded while the market is open.

Groww's history API only goes down to 1-minute candles, so the seconds inside
a minute exist only live. The live and research engines hand each fresh price
here (``add``); rows are buffered and written every few seconds off the event
loop (``flush``), one per stock per second. The data table expands a minute
into these seconds (``between``). Rows older than TICK_KEEP_DAYS are pruned.

Real Groww prices only, unless SMA_RECORD_TICKS=all (a local test on the
simulator). Recording never changes a trade.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import threading
import time

from database import session_factory
from models import PriceTick

logger = logging.getLogger(__name__)

TICK_KEEP_DAYS = 10
FLUSH_EVERY_SEC = 5.0
MAX_SPAN_SEC = 6 * 3600

_buffer: dict[tuple[str, int], float] = {}
_lock = threading.Lock()
_last_flush = 0.0
_last_prune_day = ""
_flushing: asyncio.Future | None = None


def records(source: str) -> bool:
    """Whether a price from this source is worth keeping."""
    if (source or "").upper() == "GROWW":
        return True
    return os.environ.get("SMA_RECORD_TICKS", "").lower() == "all" and (source or "").upper() == "SIMULATOR"


def add(symbol: str, when: dt.datetime | float, price: float) -> None:
    """Remember one price for this second. A later price in the same second wins."""
    try:
        value = float(price)
    except (TypeError, ValueError):
        return
    if not symbol or value <= 0:
        return
    sec = int(when.timestamp() if isinstance(when, dt.datetime) else when)
    with _lock:
        _buffer[((symbol or "").upper(), sec)] = value


def _write(rows: list[tuple[str, int, float]], prune_before: int | None) -> None:
    with session_factory()() as db:
        if rows:
            seen = {
                (s, t)
                for s, t in db.query(PriceTick.symbol, PriceTick.ts).filter(
                    PriceTick.ts >= min(r[1] for r in rows), PriceTick.ts <= max(r[1] for r in rows)
                )
            }
            db.add_all(PriceTick(symbol=s, ts=t, price=p) for s, t, p in rows if (s, t) not in seen)
        if prune_before is not None:
            db.query(PriceTick).filter(PriceTick.ts < prune_before).delete()
        db.commit()


def take() -> list[tuple[str, int, float]]:
    with _lock:
        rows = [(s, t, p) for (s, t), p in _buffer.items()]
        _buffer.clear()
    return rows


async def flush(force: bool = False) -> None:
    """Write the buffered seconds, at most every FLUSH_EVERY_SEC, off the event loop."""
    global _last_flush, _last_prune_day, _flushing
    now = time.monotonic()
    if not force and now - _last_flush < FLUSH_EVERY_SEC:
        return
    if _flushing is not None and not _flushing.done():
        return
    _last_flush = now
    rows = take()
    today = dt.date.today().isoformat()
    prune = None
    if today != _last_prune_day:
        _last_prune_day = today
        prune = int(time.time()) - TICK_KEEP_DAYS * 86_400
    if not rows and prune is None:
        return
    try:
        _flushing = asyncio.get_running_loop().run_in_executor(None, _write, rows, prune)
        await _flushing
    except Exception:  # noqa: BLE001
        logger.exception("recording second prices failed")


def between(symbol: str, start: int, end: int) -> list[list[float]]:
    """[[ts, price], ...] for one stock, start <= ts < end (at most MAX_SPAN_SEC)."""
    end = min(int(end), int(start) + MAX_SPAN_SEC)
    name = (symbol or "").upper()
    with _lock:
        pending = {t: p for (s, t), p in _buffer.items() if s == name and start <= t < end}
    with session_factory()() as db:
        rows = (
            db.query(PriceTick.ts, PriceTick.price)
            .filter(PriceTick.symbol == name, PriceTick.ts >= int(start), PriceTick.ts < end)
            .order_by(PriceTick.ts)
            .all()
        )
    out = {int(t): float(p) for t, p in rows}
    out.update(pending)
    return [[t, out[t]] for t in sorted(out)]
