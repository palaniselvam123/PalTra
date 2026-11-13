"""ATR target: a profit target a multiple of the ATR from the entry, next to the ATR stop.

    LONG   target = entry + atr_target_mult × ATR
    SHORT  target = entry − atr_target_mult × ATR

`atr_target_mult` (0 = off, the default; per bot and per stock, PAPER and LIVE, replays too) is read once at the
entry with the ATR the stop was sized from (`OpenPosition.atr_at_entry`), so stop and target are the same kind of
distance: a 1.5 × ATR stop with a 3 × ATR target is a 1 : 2 trade on every stock. It applies while the stop is on and
its type is ATR, SUPERTREND or VWAP. The trailing stop (TSL) and the SMA-gap stop keep their own targets.

Hit on the price like any target (`TARGET_HIT`, in `StrategyEngine._watch_stop`; in LIVE the exchange stop is
cancelled first by `_exit_now`). No order path of its own.
"""
from __future__ import annotations

MAX_MULT = 50.0
#: Stop types that set no target themselves.
STOP_TYPES = ("ATR", "SUPERTREND", "VWAP")


def mult(cfg) -> float:
    """The owner's multiple, 0 when off or unreadable."""
    try:
        k = float(getattr(cfg, "atr_target_mult", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if k != k or k <= 0:
        return 0.0
    return min(k, MAX_MULT)


def on(cfg) -> bool:
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    if str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() not in STOP_TYPES:
        return False
    return mult(cfg) > 0


def level(cfg, direction: str, entry: float, atr: float | None) -> float | None:
    """The target price (not yet rounded to the tick), or None: off, or no ATR formed."""
    if not on(cfg) or not atr or atr != atr or atr <= 0 or entry <= 0:
        return None
    distance = mult(cfg) * float(atr)
    return entry + distance if str(direction).upper() == "LONG" else entry - distance
