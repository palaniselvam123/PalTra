"""Per-stock price step (tick size) for NSE cash orders.

Groww rejects a price that is not a multiple of that stock's tick. A fixed
₹0.05 is wrong for most names above ₹1,000, whose tick is ₹0.10 or more.

The tick comes from Groww's public instrument list (the `tick_size` column),
loaded once a day off the event loop. Until that list is loaded, or for a
name it does not carry, the fallback is the NSE price band, never finer than
₹0.05. A coarser step that is a multiple of the real tick is always accepted,
so the fallback can cost a few paise of price but never a rejection.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import threading
import time
import urllib.request

logger = logging.getLogger("sma.ticks")

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
_TTL_SEC = 24 * 3600
_FETCH_TIMEOUT_SEC = 30
_MIN_FALLBACK_TICK = 0.05

# NSE equity price bands: (price below, tick).
_BANDS = (
    (250.0, 0.01),
    (1000.0, 0.05),
    (5000.0, 0.10),
    (10000.0, 0.50),
    (20000.0, 1.00),
)
_TOP_BAND_TICK = 5.00

_ticks: dict[str, float] = {}
_loaded_at = 0.0
_lock = threading.Lock()


def band_tick(price: float | None) -> float:
    """Fallback step from the NSE price band, never finer than ₹0.05."""
    tick = _TOP_BAND_TICK
    try:
        value = float(price)
    except (TypeError, ValueError):
        value = 0.0
    for below, step in _BANDS:
        if value < below:
            tick = step
            break
    return max(tick, _MIN_FALLBACK_TICK)


def parse_instrument_csv(text: str) -> dict[str, float]:
    """NSE cash symbol -> tick size from Groww's instrument CSV."""
    out: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("exchange") or "").upper() != "NSE" or (row.get("segment") or "").upper() != "CASH":
            continue
        symbol = (row.get("trading_symbol") or "").strip().upper()
        tick = _valid_tick(row.get("tick_size"))
        if symbol and tick is not None:
            out[symbol] = tick
    return out


def _valid_tick(raw) -> float | None:
    try:
        tick = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if not math.isfinite(tick) or tick <= 0 or tick > 100:
        return None
    return tick


def set_ticks(table: dict[str, float]) -> None:
    """Replace the table (used by the loader and by tests)."""
    global _loaded_at
    with _lock:
        _ticks.clear()
        _ticks.update({k.upper(): v for k, v in table.items()})
        _loaded_at = time.monotonic() if table else 0.0


def load_from_groww() -> int:
    """Blocking download. Call from a thread. Keeps the old table on failure."""
    try:
        raw = urllib.request.urlopen(INSTRUMENT_CSV_URL, timeout=_FETCH_TIMEOUT_SEC).read().decode("utf-8")
        table = parse_instrument_csv(raw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("tick sizes not loaded, using NSE price bands: %s", exc)
        return 0
    if table:
        set_ticks(table)
    return len(table)


def is_stale() -> bool:
    return not _ticks or time.monotonic() - _loaded_at > _TTL_SEC


def tick_for(symbol: str | None, price: float | None = None) -> float:
    """Groww's tick for this stock, else the safe price-band fallback."""
    tick = _ticks.get((symbol or "").upper())
    if tick is not None:
        return tick
    return band_tick(price)


def round_to_tick(price: float, tick: float) -> float:
    if price is None or not math.isfinite(price):
        return float("nan")
    steps = round(float(price) / tick)
    # Two decimals covers a ₹0.01 tick and removes float dust.
    return round(steps * tick, 2)


def round_price(symbol: str | None, price: float) -> float:
    """Nearest valid order price for this stock."""
    if price is None or not math.isfinite(price):
        return float("nan")
    return round_to_tick(price, tick_for(symbol, price))
