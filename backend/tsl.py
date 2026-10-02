"""Trailing stop-loss in rupee steps, like Groww's "Stoploss (TSL)".

    LONG   stop   = entry − sl_points + step × floor((best − entry) / step)
           target = entry + target_points            (0 = no target)
    SHORT  mirrored: best is the lowest price since entry.

`best` is the most favourable LTP since entry. Each full ₹`step` it gains
moves the stop ₹`step` toward profit. The stop never moves back.
"""
from __future__ import annotations

import math


def uses_tsl(cfg) -> bool:
    """Trailing stop only on entries that have a stop at all."""
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    return str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() == "TSL"


def tsl_settings(cfg) -> tuple[float, float, float]:
    """(stop ₹ from entry, trail step ₹, target ₹ or 0)."""
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
