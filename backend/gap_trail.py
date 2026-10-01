"""Moving stop and target from the SMA 9 / SMA 21 gap.

    gap %   = |SMA 9 − SMA 21| / SMA 21 × 100, on the last closed candle
    LONG    stop   = price × (1 − g × sl_mult / 100)
            target = price × (1 + g × tp_mult / 100)
    SHORT   mirrored

where g = max(gap %, min_pct). Right after a cross the two SMAs are almost
equal, so without the floor the first stop would sit on the entry price.

The stop only ever tightens (trail). The target follows the formula both
ways. PAPER only: LIVE keeps the fixed ATR exchange stop.
"""
from __future__ import annotations

STOP_TYPES = ("ATR", "SMA_GAP")


def gap_levels(
    direction: str,
    price: float,
    gap_pct: float | None,
    sl_mult: float,
    tp_mult: float,
    min_pct: float,
) -> tuple[float, float]:
    """(stop, target) for a position at `price`."""
    g = max(abs(float(gap_pct or 0.0)), float(min_pct or 0.0))
    sl_move = g * float(sl_mult) / 100.0
    tp_move = g * float(tp_mult) / 100.0
    if direction == "LONG":
        return price * (1 - sl_move), price * (1 + tp_move)
    return price * (1 + sl_move), price * (1 - tp_move)


def tighten(direction: str, current: float | None, proposed: float) -> float:
    """The stop moves toward profit only: up for a LONG, down for a SHORT."""
    if current is None:
        return proposed
    return max(current, proposed) if direction == "LONG" else min(current, proposed)


def uses_gap_stop(cfg, live: bool) -> bool:
    """SMA-gap stop only on PAPER entries that have a stop at all."""
    if live:
        return False
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    return str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() == "SMA_GAP"
