"""SMA 9 slope exit (slope_exit.py): close a trade when the fast SMA bends or turns.

Off by default (`slope_exit`), per bot and per stock, PAPER and LIVE. Read once per
closed candle after the entry, on the bot's own candles and the trade's signal side
(a flipped trade is judged like its unflipped twin, as the other exits are).

The angle on a chart depends on its zoom, so the bot reads the fast SMA's slope in
% per candle instead:

    slope = (SMA fast now - SMA fast `slope_lookback` candles ago)
            / SMA fast `slope_lookback` candles ago x 100 / `slope_lookback`

signed so that positive is with the trade (rising for a buy, falling for a sell).

  * Bend (`slope_bend_pct`, default 60, 0 = off): the trade closes as `SMA9_BEND` once
    the slope has dropped by that share from its steepest since the entry, e.g. from
    0.20 %/candle to 0.08 % or less with 60. Only after the slope has been at least
    `slope_turn_pct` with the trade, so a flat line never counts as a bend.
  * Turn (`slope_turn`, on): the trade closes as `SMA9_TURN` once the slope is at
    least `slope_turn_pct` (default 0.02 %/candle) against the trade.

  * V-turn (`vturn_exit`, off, on its own tick box): the trade closes as `SMA9_VTURN`
    once the slope is at least `vturn_turn_pct` (0.015 %/candle) against the trade
    within `vturn_within` (6) candles of having been at least `vturn_move_pct`
    (0.02 %/candle) with it: a steep run and a steep turn back, the point of a V. A slow
    U-shaped rounding or a sideways drift never qualifies. Uses the same `slope_lookback`.
    With `vturn_handoff` (off), a V-turn exit at a loss moves the stock to Bot 2 like a
    recovery (`recovery.handoff`: flipped, gap mode; in LIVE only once Groww shows no shares).

  * Slope candle (`slope_minutes`, 0 = off: the bot's own candle): read the slope, bend, turn and
    V-turn on candles of another size built from the stock's 1-minute tape from 09:15, e.g. a 1- or
    5-minute slope under a 15-minute bot, so a V is seen sooner. Entries stay on the bot's own
    candle; `slope_lookback` and `vturn_within` then count these candles, and the candle-colour hold
    (`turn_exit_candle`) reads the colour of the last one.

The steepest slope is kept in memory, so after a restart it is measured again from
the candles closed after that point. Exits go through `_exit_now`, which cancels a
LIVE stop first; a LIVE position restored without its stop id is left to that stop.
"""
from __future__ import annotations

import math

import pandas as pd

BEND_EXIT = "SMA9_BEND"
TURN_EXIT = "SMA9_TURN"
VTURN_EXIT = "SMA9_VTURN"

DEFAULT_LOOKBACK = 2
MAX_LOOKBACK = 10
DEFAULT_BEND_PCT = 60.0
DEFAULT_TURN_PCT = 0.02
MAX_TURN_PCT = 5.0
DEFAULT_VTURN_MOVE = 0.02
DEFAULT_VTURN_TURN = 0.015
DEFAULT_VTURN_WITHIN = 6
MAX_VTURN_WITHIN = 10
SLOPE_MINUTES = (0, 1, 2, 3, 5, 10, 15)


def on(cfg) -> bool:
    return bool(getattr(cfg, "slope_exit", False))


def _float(cfg, name: str, default: float, low: float, high: float) -> float:
    raw = getattr(cfg, name, None)
    try:
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def lookback(cfg) -> int:
    try:
        value = int(getattr(cfg, "slope_lookback", None) or DEFAULT_LOOKBACK)
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK
    return max(1, min(MAX_LOOKBACK, value))


def bend_pct(cfg) -> float:
    return _float(cfg, "slope_bend_pct", DEFAULT_BEND_PCT, 0.0, 100.0)


def turn_on(cfg) -> bool:
    return getattr(cfg, "slope_turn", None) is not False


def turn_pct(cfg) -> float:
    return _float(cfg, "slope_turn_pct", DEFAULT_TURN_PCT, 0.0, MAX_TURN_PCT)


def vturn_on(cfg) -> bool:
    return bool(getattr(cfg, "vturn_exit", False))


def vturn_handoff_on(cfg) -> bool:
    """After a V-turn exit at a loss, move the stock to Bot 2 (recovery.handoff), off by default."""
    return vturn_on(cfg) and bool(getattr(cfg, "vturn_handoff", False))


def lost(direction: str, entry: float, exit_price: float) -> bool:
    """The trade closed at a loss (before charges): a long below its entry, a short above it."""
    return exit_price < entry if direction == "LONG" else exit_price > entry


def vturn_move_pct(cfg) -> float:
    return _float(cfg, "vturn_move_pct", DEFAULT_VTURN_MOVE, 0.0, MAX_TURN_PCT)


def vturn_turn_pct(cfg) -> float:
    return _float(cfg, "vturn_turn_pct", DEFAULT_VTURN_TURN, 0.0, MAX_TURN_PCT)


def vturn_within(cfg) -> int:
    try:
        value = int(getattr(cfg, "vturn_within", None) or DEFAULT_VTURN_WITHIN)
    except (TypeError, ValueError):
        return DEFAULT_VTURN_WITHIN
    return max(1, min(MAX_VTURN_WITHIN, value))


def minutes(cfg) -> int:
    """The slope candle size in minutes; 0 = the bot's own candle."""
    try:
        value = int(getattr(cfg, "slope_minutes", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value in SLOPE_MINUTES else 0


def own_size(cfg, entry_minutes: int) -> int:
    """The slope candle size when it differs from the bot's own candle, else 0."""
    m = minutes(cfg)
    return m if m > 0 and m != int(entry_minutes or 1) else 0


def watched(cfg) -> bool:
    """Is any SMA 9 slope exit on (bend / turn, or the V-turn)?"""
    return on(cfg) or vturn_on(cfg)


def slope(closed: pd.DataFrame, fast: int, back: int) -> float | None:
    """The fast SMA's slope in % per candle on the last closed candle (closed candles only)."""
    if closed is None or getattr(closed, "empty", True) or "close" not in closed.columns:
        return None
    fast, back = max(1, int(fast)), max(1, int(back))
    closes = pd.to_numeric(closed["close"], errors="coerce")
    if closes.notna().sum() < fast + back:
        return None
    sma = closes.rolling(fast).mean()
    now, then = sma.iloc[-1], sma.iloc[-1 - back]
    if pd.isna(now) or pd.isna(then) or then == 0:
        return None
    return float((now - then) / then * 100.0 / back)


def judge(direction: str, value: float, state: dict, cfg) -> tuple[str, str] | None:
    """(exit reason, note) once the slope bends or turns, else None. `state` keeps `peak`."""
    signed = value if direction == "LONG" else -value
    floor = turn_pct(cfg)
    peak = max(state.get("peak", signed), signed)
    state["peak"] = peak
    if turn_on(cfg) and signed < 0 and signed <= -floor:
        return TURN_EXIT, f"SMA 9 slope {value:+.3f}%/candle turned against the trade"
    bend = bend_pct(cfg)
    if bend > 0 and peak >= max(floor, 1e-9) and signed <= peak * (1 - bend / 100):
        return BEND_EXIT, (
            f"SMA 9 slope {value:+.3f}%/candle, down {bend:g}% or more from its steepest "
            f"{(peak if direction == 'LONG' else -peak):+.3f}%/candle"
        )
    return None


def judge_vturn(direction: str, value: float, state: dict, cfg) -> tuple[str, str] | None:
    """(exit reason, note) on a V: a steep slope with the trade, then a steep one against it.

    `state["hist"]` keeps the signed slopes of the candles read before this one (newest last).
    """
    signed = value if direction == "LONG" else -value
    hist = state.setdefault("hist", [])
    within = vturn_within(cfg)
    move, turn = vturn_move_pct(cfg), vturn_turn_pct(cfg)
    recent = hist[-within:]
    hist.append(signed)
    del hist[:-MAX_VTURN_WITHIN]
    if signed >= 0 or -signed < max(turn, 1e-9) or not recent:
        return None
    steepest = max(recent)
    if steepest < max(move, 1e-9):
        return None
    with_trade = steepest if direction == "LONG" else -steepest
    return VTURN_EXIT, (
        f"SMA 9 V-turn: slope {with_trade:+.3f}%/candle, then {value:+.3f}%/candle "
        f"within {within} candle(s)"
    )
