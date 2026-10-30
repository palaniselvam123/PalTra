"""Staged stop plan (stop_plan.py): the stop changes kind as the trade moves your way.

Off by default (`stop_plan`), per bot and per stock, PAPER and LIVE (and replays). It needs the plain
ATR stop at entry (stop type ATR, the stop on); with the trailing (TSL), Supertrend or SMA-gap stop
the plan does nothing. R is the entry's risk: |fill - initial ATR stop| (ATR(14) x `atr_multiplier`).

* Stage 0, entry to +`plan_be_r` R: the initial ATR stop stays.
* Stage 1, the best price since entry reached +`plan_be_r` R (default 1.5): the stop moves to
  breakeven, the fill plus the estimated round-trip charges per share (minus for a short), so the
  trade cannot lose after charges.
* Stage 2, the best price reached +`plan_trail_r` R (default 2.0): the stop follows the Supertrend
  line (`st_period` / `st_mult`, 10 / 3 by default) on the bot's own closed candles, never below
  breakeven.

The stop only tightens (`gap_trail.tighten`). Stages only go forward. If the price is already back
through the new stop when a stage starts, the trade closes at once (`PLAN_STOP`); a hit of a stop the
plan moved is `PLAN_SL_HIT` (in LIVE Groww's own stop fills it, so no second exit is sent). Each stage change is
written to the trade's `stop_log` (shown in the blotter). StrategyEngine._trail_stop_plan runs it;
LIVE moves the Groww stop in place through `_move_stop`, like the trailing stop.
"""
from __future__ import annotations

import math

from charges import calculate_charges

DEFAULT_BE_R = 1.5
DEFAULT_TRAIL_R = 2.0
MAX_R = 20.0
EXIT_REASON = "PLAN_STOP"  # the bot closed it: the price was already through a new plan stop
HIT_REASON = "PLAN_SL_HIT"  # the stop the plan moved was hit (in LIVE Groww's stop filled it)


def on(cfg) -> bool:
    return bool(getattr(cfg, "stop_plan", False))


def _r(cfg, name: str, default: float) -> float:
    raw = getattr(cfg, name, None)
    try:
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return min(MAX_R, value)


def be_r(cfg) -> float:
    return _r(cfg, "plan_be_r", DEFAULT_BE_R)


def trail_r(cfg) -> float:
    """The trail starts at or after breakeven."""
    return max(be_r(cfg), _r(cfg, "plan_trail_r", DEFAULT_TRAIL_R))


def risk(entry: float, initial_stop: float) -> float | None:
    """R per share; None when there is no distance to measure."""
    value = abs(float(entry) - float(initial_stop))
    return value if value > 0 else None


def gain_r(direction: str, entry: float, best: float | None, r: float) -> float:
    """How far the best price since entry has gone the trade's way, in R."""
    if best is None or not r:
        return 0.0
    move = (best - entry) if direction == "LONG" else (entry - best)
    return move / r


def stage(direction: str, entry: float, best: float | None, r: float, cfg) -> int:
    g = gain_r(direction, entry, best, r)
    if g >= trail_r(cfg):
        return 2
    if g >= be_r(cfg):
        return 1
    return 0


def breakeven(direction: str, entry: float, qty: int) -> float:
    """The fill plus the round-trip charges per share for a long (minus for a short)."""
    qty = max(1, int(qty or 1))
    per_share = float(calculate_charges(entry, entry, qty).get("total_charges", 0.0)) / qty
    return entry + per_share if direction == "LONG" else entry - per_share


def guards(direction: str, stop: float, price: float) -> bool:
    """The stop is still on the safe side of the price (under a long, above a short)."""
    return stop < price if direction == "LONG" else stop > price


def note(stage_no: int, cfg, stop: float) -> str:
    if stage_no == 1:
        return f"stop → breakeven (+{be_r(cfg):g}R) at {stop:.2f}"
    return f"stop → Supertrend trail (+{trail_r(cfg):g}R) at {stop:.2f}"
