"""Tall-candle pullback entry (tall_pullback.py): do not buy the top of a big green candle.

Off by default (`tall_pullback`), per bot and per stock, PAPER and LIVE. When a signal
candle (an SMA cross, or any setup that orders through `apply_signal`) is tall, its
height (high - low) at least `tall_atr_mult` (default 1.5) times its ATR, the order does
not go at once. The bot waits for a pullback to a limit level instead:

    LONG   limit = max(SMA fast, low + 0.5 x (high - low))   (half-way down, or the fast SMA)
    SHORT  limit = min(SMA fast, high - 0.5 x (high - low))  (half-way up, or the fast SMA)

The level is watched on every price the bot gets (about once a second live, every
replay step in a backtest). The first price at or beyond it (at or under it for a buy,
at or over it for a sell) sends the bot's normal entry, so every check, the stop, the
trade cap, the cut-off and one LIVE bot per stock still apply. Nothing rests on
Groww's book while it waits, so there is nothing to cancel: if `tall_expiry_candles`
(default 2) more candles close without the price reaching the level, the wait ends and
no order is sent. A smaller signal candle orders at once, as before.

A flipped stock (flip_orders) orders at once: its real order goes the other way, so a
pullback level for the signal's side would wait for the wrong move.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

DEFAULT_MULT = 1.5
MIN_MULT, MAX_MULT = 0.5, 10.0
DEFAULT_EXPIRY = 2
MAX_EXPIRY = 10


def on(cfg) -> bool:
    return bool(getattr(cfg, "tall_pullback", False))


def mult(cfg) -> float:
    try:
        value = float(getattr(cfg, "tall_atr_mult", None) or DEFAULT_MULT)
    except (TypeError, ValueError):
        return DEFAULT_MULT
    if not math.isfinite(value):
        return DEFAULT_MULT
    return max(MIN_MULT, min(MAX_MULT, value))


def expiry(cfg) -> int:
    try:
        value = int(getattr(cfg, "tall_expiry_candles", None) or DEFAULT_EXPIRY)
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY
    return max(1, min(MAX_EXPIRY, value))


@dataclass
class Plan:
    """A tall signal candle's pullback level."""

    limit: float
    height: float
    atr: float


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) or not math.isfinite(out) else out


def plan(bar, direction: str, atr: float | None, cfg) -> Plan | None:
    """The pullback level when `bar` (the closed signal candle) is tall, else None (order at once)."""
    high, low = _f(bar.get("high")), _f(bar.get("low"))
    atr = _f(atr)
    if high is None or low is None or atr is None or atr <= 0 or high <= low:
        return None
    height = high - low
    if height < mult(cfg) * atr:
        return None
    half = low + 0.5 * height if direction == "LONG" else high - 0.5 * height
    fast = _f(bar.get("sma_9"))
    if fast is None:
        limit = half
    elif direction == "LONG":
        limit = max(fast, half)
    else:
        limit = min(fast, half)
    return Plan(limit=limit, height=height, atr=atr)


def touched(direction: str, price: float | None, limit: float) -> bool:
    """Has the price come back to the level (at or under it for a buy, at or over it for a sell)?"""
    if price is None or price <= 0:
        return False
    return price <= limit if direction == "LONG" else price >= limit


def expired(signal_ts: int, closed_ts: int | None, span_sec: int, candles: int) -> bool:
    """True once `candles` more candles have closed after the signal candle (start `signal_ts`)."""
    if closed_ts is None:
        return False
    return int(closed_ts) >= int(signal_ts) + int(candles) * int(span_sec)
