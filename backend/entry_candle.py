"""Entry-candle colour check (entry_candle.py): the order goes only while its own candle moves the trade's way.

The entry checks read closed candles, so a signal from a green candle can still fill on a
candle that then closes red: the order goes at the start of the next candle, whose colour
is not known yet. With `entry_candle_confirm` on (off by default, per bot and per stock,
PAPER and LIVE), every entry that would order at once through `apply_signal` (cross, gap-mode
entry, join, late / early-reversal entry, re-entry; not candle patterns) waits instead as a
`PullbackOrder` (`confirm=True`) until the price is `entry_candle_pct` (default 0.05) % past
the **forming** candle's open on the side the real order goes: above it for a buy (the candle
green so far), below it for a sell (red so far). A flipped stock is judged on its real order.

The level follows each new forming candle, so if one candle closes the wrong colour the
next one can still fill. Dropped after `entry_candle_max` (default 2) closed candles, when a
closed candle shows the SMAs crossed against the signal, at the cut-off or trade cap, or
when a new signal replaces it (`StrategyEngine._watch_pullbacks`). The tall-candle pullback,
the candle-extreme limit and a breakout arrival already wait for a price of their own and
take over from it. Entry reason suffix `:CANDLE_CONFIRM`. Nothing here places an order on
its own; the engine decides.
"""
from __future__ import annotations

import math

PCT_DEFAULT = 0.05
MAX_PCT = 1.0
CANDLES_DEFAULT = 2
MAX_CANDLES = 5


def on(cfg) -> bool:
    return bool(getattr(cfg, "entry_candle_confirm", False))


def margin_pct(cfg) -> float:
    try:
        value = float(getattr(cfg, "entry_candle_pct", PCT_DEFAULT))
    except (TypeError, ValueError):
        return PCT_DEFAULT
    if not math.isfinite(value):
        return PCT_DEFAULT
    return max(0.0, min(MAX_PCT, value))


def max_candles(cfg) -> int:
    try:
        n = int(getattr(cfg, "entry_candle_max", None) or CANDLES_DEFAULT)
    except (TypeError, ValueError):
        return CANDLES_DEFAULT
    return max(1, min(MAX_CANDLES, n))


def level(candle_open: float, real_side: str, pct: float) -> float:
    """The price the forming candle must reach: open + pct % for a buy, open − pct % for a sell."""
    step = candle_open * pct / 100.0
    return candle_open + step if real_side == "LONG" else candle_open - step


def confirmed(real_side: str, price, at: float) -> bool:
    """Is the live price past the level the trade's way (a buy at or above it, a sell at or below)?"""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(p) or p <= 0:
        return False
    return p >= at if real_side == "LONG" else p <= at
