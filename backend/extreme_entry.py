"""Candle-extreme limit entry (extreme_entry.py): never buy the top or short the bottom of a signal candle.

Off by default (`extreme_entry`), per bot and per stock, PAPER and LIVE. When a signal
(an SMA cross, a gap-mode entry, a join, a late or early-reversal entry: anything that
orders through `apply_signal`, except candle patterns) prints on a closed candle N, the
order does not go at once. It waits at the candle's favourable extreme instead, judged on
the side the real order goes (a flipped stock's buy signal sells, so it waits at the high):

    BUY   limit = low of N            (`extreme_anchor` CANDLE_EXTREME)
          limit = max(low of N, SMA fast)   (SMA_9)
    SELL  limit = high of N
          limit = min(high of N, SMA fast)

The level is watched on every price the bot gets, like the tall-candle pullback
(tall_pullback.py). Before the fill is judged, the wait is dropped with
`SETUP_INVALIDATED_OPPOSITE_BREAK` when the price breaks the far side of candle N by more
than `extreme_invalid_pct` % (a buy: under low x (1 - pct/100); a sell: over high x (1 +
pct/100)), with `SETUP_INVALIDATED_SMA_CROSS` when `extreme_cancel_on_cross` is on and a
closed candle after N has the SMA fast / slow crossed against the signal, and when
`extreme_expiry_candles` (2) more candles close without a fill. The first price at or
beyond the limit sends the bot's normal entry, so every check, the stop, the trade cap, the
cut-off and one LIVE bot per stock still apply. Nothing rests on Groww's book while it
waits, so there is nothing to cancel there.

It takes over from the tall-candle pullback when both are on (it covers every signal candle,
tall or not). A breakout arrival's own pullback level (breakout.py) still wins.
"""
from __future__ import annotations

import math

import pandas as pd

ANCHORS = ("CANDLE_EXTREME", "SMA_9")
DEFAULT_ANCHOR = "CANDLE_EXTREME"
DEFAULT_INVALID_PCT = 0.05
MAX_INVALID_PCT = 5.0
DEFAULT_EXPIRY = 2
MAX_EXPIRY = 10

OPPOSITE_BREAK = "SETUP_INVALIDATED_OPPOSITE_BREAK"
SMA_CROSS = "SETUP_INVALIDATED_SMA_CROSS"


def on(cfg) -> bool:
    return bool(getattr(cfg, "extreme_entry", False))


def applies(cfg) -> bool:
    """On, and not for candle-pattern or candle-colour trades (they order at the next candle's start)."""
    return on(cfg) and (getattr(cfg, "entry_mode", None) or "SMA").upper() not in ("PATTERN", "COLOUR")


def anchor(cfg) -> str:
    value = str(getattr(cfg, "extreme_anchor", None) or DEFAULT_ANCHOR).upper()
    return value if value in ANCHORS else DEFAULT_ANCHOR


def invalid_pct(cfg) -> float:
    raw = getattr(cfg, "extreme_invalid_pct", None)
    try:
        value = float(DEFAULT_INVALID_PCT if raw is None else raw)
    except (TypeError, ValueError):
        return DEFAULT_INVALID_PCT
    if not math.isfinite(value):
        return DEFAULT_INVALID_PCT
    return max(0.0, min(MAX_INVALID_PCT, value))


def expiry(cfg) -> int:
    try:
        value = int(getattr(cfg, "extreme_expiry_candles", None) or DEFAULT_EXPIRY)
    except (TypeError, ValueError):
        return DEFAULT_EXPIRY
    return max(1, min(MAX_EXPIRY, value))


def cancel_on_cross(cfg) -> bool:
    return getattr(cfg, "extreme_cancel_on_cross", None) is not False


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) or not math.isfinite(out) else out


def levels(bar, side: str, cfg) -> tuple[float, float] | None:
    """(limit, invalidation price) for the real order `side` (LONG buys, SHORT sells), or None."""
    high, low = _f(bar.get("high")), _f(bar.get("low"))
    if high is None or low is None or high < low or low <= 0:
        return None
    fast = _f(bar.get("sma_9")) if anchor(cfg) == "SMA_9" else None
    pct = invalid_pct(cfg) / 100.0
    if side == "LONG":
        limit = low if fast is None else max(low, fast)
        return limit, low * (1 - pct)
    limit = high if fast is None else min(high, fast)
    return limit, high * (1 + pct)


def broken(side: str, price: float | None, invalid_at: float) -> bool:
    """Has the price broken the far side of the signal candle (strictly beyond the buffer)?"""
    if price is None or price <= 0:
        return False
    return price < invalid_at if side == "LONG" else price > invalid_at


def sma_side(bar) -> int:
    """+1 when SMA fast is above slow, -1 below, 0 when equal or not ready."""
    fast, slow = _f(bar.get("sma_9")), _f(bar.get("sma_21"))
    if fast is None or slow is None or fast == slow:
        return 0
    return 1 if fast > slow else -1


def crossed_against(direction: str, at_signal: int, now: int) -> bool:
    """The SMAs turned against the signal's side since the signal candle."""
    against = -1 if direction == "LONG" else 1
    return now == against and at_signal != against


def side_on_closed(frame: pd.DataFrame | None, fast: int, slow: int) -> int:
    """SMA fast vs slow on the last closed candle of a plain candle frame (forming bar last)."""
    if frame is None or getattr(frame, "empty", True) or "close" not in frame.columns:
        return 0
    fast, slow = max(1, int(fast)), max(1, int(slow))
    closes = pd.to_numeric(frame["close"].iloc[:-1], errors="coerce").dropna()
    if len(closes) < max(fast, slow):
        return 0
    return sma_side({"sma_9": closes.iloc[-fast:].mean(), "sma_21": closes.iloc[-slow:].mean()})
