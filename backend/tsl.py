"""Trailing stop-loss in rupee steps, like Groww's "Stoploss (TSL)".

    LONG   stop   = entry − sl_points + step × floor((best − entry) / step)
           target = entry + target_points            (0 = no target)
    SHORT  mirrored: best is the lowest price since entry.

`best` is the most favourable LTP since entry. Each full ₹`step` it gains
moves the stop ₹`step` toward profit. The stop never moves back.

The settings can be set in either **₹ (points)** or **% of entry price**.
The % mode is for ranges where a fixed ₹ stop does not scale: ₹10 is a tight
stop on a ₹1,000 stock but a 10% stop on a ₹100 one; 1% is 1% on both. The
chosen mode is stored in `tsl_mode` (default `POINTS`), and the % values live
in `tsl_sl_pct` / `tsl_trail_pct` / `tsl_target_pct`. The order path reads
`tsl_settings(cfg, entry_price=…)` which returns rupee points either way, so
all downstream logic (`tsl_entry_levels`, `tsl_stop`, chart drawing, Telegram)
is unchanged.
"""
from __future__ import annotations

import math

POINTS = "POINTS"
PERCENT = "PERCENT"
#: Sized from the stock's own ATR at entry: stop / trail / target = multiples of the ATR on the bot's candle
#: (tsl_sl_atr / tsl_trail_atr / tsl_target_atr). Meant for a bot that trades whatever stock is handed to it
#: (Bot 5's fast movers): a ₹ or % number cannot fit a ₹100 stock and a ₹3,000 one, an ATR multiple does.
ATR = "ATR"
DEFAULT_SL_ATR = 1.5
DEFAULT_TRAIL_ATR = 0.5
DEFAULT_TARGET_ATR = 3.0


def uses_tsl(cfg) -> bool:
    """Trailing stop only on entries that have a stop at all."""
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    return str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() == "TSL"


def tsl_mode(cfg) -> str:
    raw = str(getattr(cfg, "tsl_mode", POINTS) or POINTS).upper()
    return raw if raw in (POINTS, PERCENT, ATR) else POINTS


def tsl_atrs(cfg) -> tuple[float, float, float]:
    """(stop × ATR, trail step × ATR, target × ATR or 0) as the owner set them."""
    sl = float(getattr(cfg, "tsl_sl_atr", DEFAULT_SL_ATR) or DEFAULT_SL_ATR)
    step = float(getattr(cfg, "tsl_trail_atr", DEFAULT_TRAIL_ATR) or DEFAULT_TRAIL_ATR)
    target = getattr(cfg, "tsl_target_atr", DEFAULT_TARGET_ATR)
    target = DEFAULT_TARGET_ATR if target is None else float(target)
    return abs(sl), abs(step), max(0.0, target)


def tsl_pcts(cfg) -> tuple[float, float, float]:
    """(stop %, trail step %, target %) as the owner typed them, before conversion."""
    sl_pct = float(getattr(cfg, "tsl_sl_pct", 1.0) or 1.0)
    step_pct = float(getattr(cfg, "tsl_trail_pct", 0.5) or 0.5)
    target_pct = float(getattr(cfg, "tsl_target_pct", 0.0) or 0.0)
    return abs(sl_pct), abs(step_pct), max(0.0, target_pct)


def tsl_settings(cfg, entry_price: float | None = None, atr: float | None = None) -> tuple[float, float, float]:
    """(stop ₹ from entry, trail step ₹, target ₹ or 0).

    When `tsl_mode` is `PERCENT`, the % settings are turned into points using
    `entry_price`. When it is `ATR`, the multiples are turned into points using `atr` (the stock's ATR on
    the bot's candle at entry, `OpenPosition.atr_at_entry`); without a usable ATR the trade falls back to
    the ₹ points, so a stop is always placed. Callers inside the engine always have the fill price (so the
    conversion is exact per trade). Without `entry_price`, the raw point values
    are returned so the frontend has something to show on the config screens;
    this is only safe for display and never for an order.
    """
    if tsl_mode(cfg) == ATR and atr is not None and atr > 0:
        sl_atr, step_atr, target_atr = tsl_atrs(cfg)
        return (atr * sl_atr, atr * step_atr, atr * target_atr if target_atr > 0 else 0.0)
    if tsl_mode(cfg) == PERCENT and entry_price is not None and entry_price > 0:
        sl_pct, step_pct, target_pct = tsl_pcts(cfg)
        return (
            entry_price * sl_pct / 100.0,
            entry_price * step_pct / 100.0,
            entry_price * target_pct / 100.0 if target_pct > 0 else 0.0,
        )
    sl_points = float(getattr(cfg, "tsl_sl_points", 20.0) or 20.0)
    step = float(getattr(cfg, "tsl_trail_points", 10.0) or 10.0)
    target = float(getattr(cfg, "tsl_target_points", 0.0) or 0.0)
    return abs(sl_points), abs(step), max(0.0, target)


def tsl_entry_levels(direction: str, entry: float, sl_points: float, target_points: float) -> tuple[float, float | None]:
    """(stop, target) at entry. No target when target_points is 0."""
    if direction == "LONG":
        return entry - sl_points, (entry + target_points if target_points > 0 else None)
    return entry + sl_points, (entry - target_points if target_points > 0 else None)


def tsl_stop(direction: str, entry: float, sl_points: float, step: float, best: float) -> float:
    """The trailed stop for the best price seen since entry."""
    gain = (best - entry) if direction == "LONG" else (entry - best)
    steps = math.floor(gain / step + 1e-9) if step > 0 and gain > 0 else 0
    if direction == "LONG":
        return entry - sl_points + steps * step
    return entry + sl_points - steps * step
