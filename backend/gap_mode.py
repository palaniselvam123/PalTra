"""SMA gap mode: enter on a widening SMA 9/21 gap, leave when it fades.

The gap is (SMA fast - SMA slow) / SMA slow x 100 on a closed candle, signed:
positive when SMA 9 is above SMA 21. With ``use_gap_mode`` on:

Entry (a cross arms it, the gap fires it)
  * a bullish cross arms a BUY; it is placed on the first closed candle whose
    gap is >= ``gap_entry_long`` (a bearish cross arms a SELL, placed when the
    gap is <= ``gap_entry_short``, a negative number);
  * ``gap_entry_delay_min`` > 0 waits that many more closed minutes after the
    gap first reaches the level, and the gap must still be past it then; if it
    falls back in between, the wait starts again;
  * ``gap_entry_window_min`` > 0 gives up when the level has not been reached
    that many minutes after the cross (0 = keep waiting until the next cross);
  * the usual checks (market hours, entry cut-off, ticked filters, ADX, trade
    cap) are made when the order would go, not at the cross.

Exit (the gap fades)
  * the trade is armed for the gap exit once the gap, in the trade's
    direction, has been beyond ``gap_exit_long`` / ``gap_exit_short``;
  * it closes on the first closed candle where the gap is back at or inside
    that level, or (``gap_giveback_pct`` > 0) has given back that share of
    the widest gap since entry while still narrowing;
  * the stop, target, square-off and an opposite cross keep working as before.

Telling a pullback from a reversal (both optional, off by default)
  * ``gap_fade_confirm_sma``: a fade only closes the trade on a candle that
    also closes on the wrong side of the slow SMA (below it for a buy, above
    it for a sell). A pullback that narrows the gap but stays on the trade's
    side of SMA 21 is held; a reversal that breaks through it exits;
  * ``gap_fade_min_candles`` > 0: the gap must have narrowed on that many
    closed candles in a row first, so a one-candle dip is not a fade.

Pure functions only; the engine keeps the state and places the orders.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULTS = {
    "gap_entry_long": 0.05,
    "gap_exit_long": 0.02,
    "gap_entry_short": -0.05,
    "gap_exit_short": -0.02,
    "gap_giveback_pct": 0.0,
    "gap_entry_delay_min": 0,
    "gap_entry_window_min": 0,
    "gap_fade_min_candles": 0,
}


def setting(cfg, key: str) -> float:
    """A gap-mode setting; 0 and negative numbers are real values."""
    value = getattr(cfg, key, None)
    return float(DEFAULTS[key] if value is None else value)


def uses_gap_mode(cfg) -> bool:
    return bool(getattr(cfg, "use_gap_mode", False))


@dataclass
class Pending:
    """A cross waiting for its gap: direction, cross candle, when the level was first met."""

    direction: str  # LONG | SHORT
    cross_ts: int
    met_ts: int | None = None
    alerted: bool = False


def entry_level(cfg, direction: str) -> float:
    return setting(cfg, "gap_entry_long" if direction == "LONG" else "gap_entry_short")


def past_entry(cfg, direction: str, gap: float) -> bool:
    level = entry_level(cfg, direction)
    return gap >= level if direction == "LONG" else gap <= level


def judge_pending(cfg, pending: Pending, gap: float | None, bar_ts: int) -> tuple[str, str]:
    """What a waiting cross does on this closed candle: ("enter" | "wait" | "drop", note)."""
    level = entry_level(cfg, pending.direction)
    side = "≥" if pending.direction == "LONG" else "≤"
    window = int(setting(cfg, "gap_entry_window_min"))
    waited_cross = (bar_ts - pending.cross_ts) / 60
    if gap is None:
        return "wait", "SMA gap is not ready"
    if past_entry(cfg, pending.direction, gap):
        if pending.met_ts is None:
            pending.met_ts = bar_ts
        delay = int(setting(cfg, "gap_entry_delay_min"))
        waited = (bar_ts - pending.met_ts) / 60
        if waited >= delay:
            return "enter", f"SMA gap {gap:+.3f}% {side} {level:g}%"
        return "wait", f"SMA gap {gap:+.3f}% {side} {level:g}%, waiting {delay - waited:.0f} more min"
    pending.met_ts = None
    if window > 0 and waited_cross >= window:
        return "drop", f"SMA gap {gap:+.3f}% did not reach {level:g}% within {window} min of the cross"
    return "wait", f"waiting for SMA gap {side} {level:g}% (now {gap:+.3f}%)"


def exit_level(cfg, direction: str) -> float:
    return setting(cfg, "gap_exit_long" if direction == "LONG" else "gap_exit_short")


def judge_exit(cfg, direction: str, gap: float, prev_gap: float | None, state: dict) -> str | None:
    """Exit note when the gap has faded on this closed candle, else None.

    `state` keeps `armed` and `peak` (the widest gap in the trade's direction)
    between candles; the caller stores it on the position.
    """
    sign = 1.0 if direction == "LONG" else -1.0
    g = sign * gap
    if prev_gap is not None:
        # Closed candles in a row on which the gap narrowed (for gap_fade_min_candles).
        state["narrow_run"] = state.get("narrow_run", 0) + 1 if g < sign * prev_gap else 0
    level = sign * exit_level(cfg, direction)
    state["peak"] = max(state.get("peak", g), g)
    if not state.get("armed"):
        if g > level:
            state["armed"] = True
        return None
    if g <= level:
        return f"SMA gap {gap:+.3f}% back to the exit level {exit_level(cfg, direction):g}%"
    give = setting(cfg, "gap_giveback_pct")
    narrowing = prev_gap is not None and g < sign * prev_gap
    if give > 0 and narrowing and state["peak"] > 0 and g <= state["peak"] * (1 - give / 100):
        return f"SMA gap {gap:+.3f}% gave back {give:g}% of its widest {sign * state['peak']:+.3f}%"
    return None


def fade_confirmed(cfg, direction: str, close: float, slow: float | None, state: dict) -> tuple[bool, str]:
    """Whether a fade judged by judge_exit may close the trade on this candle.

    (True, "") with both confirmations off. Otherwise (False, why it holds).
    """
    need = int(setting(cfg, "gap_fade_min_candles"))
    run = int(state.get("narrow_run", 0))
    if need > 0 and run < need:
        return False, f"the gap has narrowed {run} of {need} candles in a row"
    if bool(getattr(cfg, "gap_fade_confirm_sma", False)):
        if slow is None or slow != slow:  # NaN
            return False, "the slow SMA is not ready"
        if direction == "LONG" and close >= slow:
            return False, f"close {close:.2f} is still above the slow SMA {slow:.2f}"
        if direction == "SHORT" and close <= slow:
            return False, f"close {close:.2f} is still below the slow SMA {slow:.2f}"
    return True, ""


def check(cfg) -> str | None:
    """Why these settings cannot work, or None."""
    if setting(cfg, "gap_exit_long") >= setting(cfg, "gap_entry_long"):
        return "Buy exit gap must be below the buy entry gap"
    if setting(cfg, "gap_exit_short") <= setting(cfg, "gap_entry_short"):
        return "Sell exit gap must be above the sell entry gap (closer to zero)"
    return None
