"""SMA 9 turn alert and entry (turn_entry.py): act on the fast SMA's V before the 9/21 cross.

Both off by default, per bot and per stock, PAPER and LIVE. Read once per closed candle of
each armed stock on the bot's own candles, with the same numbers as the V-turn exit
(slope_exit.py): the fast SMA's slope in % per candle over `slope_lookback` candles.

A **turn** is printed on the candle where the slope first reaches `vturn_turn_pct` one way
after it was at least `vturn_move_pct` the other way within the `vturn_within` candles
before: rising then falling = a turn down (sell side), falling then rising = a turn up (buy
side). A slow rounding or a sideways drift never qualifies, and a turn fires once (the
candle after it is no longer "first").

**Rounded U** (`uturn_on`, off): also counts a gentle turn. It fires on the first candle whose
slope is at least `uturn_min_pct` (0.005 %/candle) the new way, when before it (skipping the
near-flat candles of the rounding, at most `U_MAX_FLAT` of them) the slope had been at least
`uturn_min_pct` the other way for `uturn_run` (5) candles in a row. A sharp V is still read
first; the alert and the order name which shape it was.

  * Alert (`turn_alert`): a Telegram for the stock on that candle.
  * Entry (`turn_entry`): the bot orders on that candle through `apply_signal` (entry reason
    `SMA9_TURN`): every entry check, the stop, flip, cut-off, caps and one LIVE bot per stock
    apply; an opposite position is closed and reversed like an opposite cross (held when the
    SMA cross exit is off). The SMA cross still trades as before. Gap mode and candle patterns
    / colour do not take turn entries.

The first candle the bot reads for a stock only sets the mark, so a turn printed before the
stock was armed (or before a restart) is neither alerted nor traded.
"""
from __future__ import annotations

import pandas as pd

import slope_exit

ENTRY_REASON = "SMA9_TURN"
DEFAULT_U_RUN = 5
MAX_U_RUN = 30
DEFAULT_U_MIN = 0.005
MAX_U_MIN = 1.0
U_MAX_FLAT = 10  # near-flat candles allowed between the old run and the new side
# A turn the same way within this many candles of the last one is the same turn (no second alert or order).
SAME_TURN_CANDLES = 6


def alert_on(cfg) -> bool:
    return bool(getattr(cfg, "turn_alert", False))


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "turn_entry", False))


def watched(cfg) -> bool:
    return alert_on(cfg) or entry_on(cfg)


def u_on(cfg) -> bool:
    return bool(getattr(cfg, "uturn_on", False))


def u_run(cfg) -> int:
    try:
        value = int(getattr(cfg, "uturn_run", None) or DEFAULT_U_RUN)
    except (TypeError, ValueError):
        return DEFAULT_U_RUN
    return max(2, min(MAX_U_RUN, value))


def u_min(cfg) -> float:
    try:
        raw = getattr(cfg, "uturn_min_pct", None)
        value = float(DEFAULT_U_MIN if raw is None else raw)
    except (TypeError, ValueError):
        return DEFAULT_U_MIN
    return max(0.0, min(MAX_U_MIN, value)) if value == value else DEFAULT_U_MIN


def slopes(closed: pd.DataFrame, fast: int, back: int) -> pd.Series:
    """The fast SMA's slope in % per candle on every closed candle (NaN until formed)."""
    closes = pd.to_numeric(closed["close"], errors="coerce")
    sma = closes.rolling(max(1, int(fast))).mean()
    then = sma.shift(max(1, int(back)))
    return (sma - then) / then * 100.0 / max(1, int(back))


def detect(closed: pd.DataFrame, fast: int, cfg) -> tuple[str, str] | None:
    """("LONG" | "SHORT", note) when the last closed candle prints a fresh SMA 9 turn (a V, or with
    `uturn_on` a rounded U), else None."""
    if closed is None or getattr(closed, "empty", True) or "close" not in closed.columns:
        return None
    found = _detect_v(closed, fast, cfg)
    if found is None and u_on(cfg):
        found = _detect_u(closed, fast, cfg)
    return found


def _detect_u(closed: pd.DataFrame, fast: int, cfg) -> tuple[str, str] | None:
    floor = max(u_min(cfg), 1e-9)
    run = u_run(cfg)
    s = slopes(closed, fast, slope_exit.lookback(cfg)).dropna()
    if len(s) < run + 2:
        return None
    now, prev = float(s.iloc[-1]), float(s.iloc[-2])
    if now <= -floor and prev > -floor:
        side, old = "SHORT", 1.0
    elif now >= floor and prev < floor:
        side, old = "LONG", -1.0
    else:
        return None
    values = s.iloc[:-1].tolist()[::-1]  # newest first, before this candle
    i, flat = 0, 0
    while i < len(values) and abs(values[i]) < floor and flat < U_MAX_FLAT:
        i, flat = i + 1, flat + 1
    count = 0
    while i < len(values) and values[i] * old >= floor:
        i, count = i + 1, count + 1
    if count < run:
        return None
    shape = "turned down (rounded top)" if side == "SHORT" else "turned up (rounded bottom)"
    return side, f"U: SMA {fast} {shape} after {count} candles the other way, now {now:+.3f}%/candle"


def _detect_v(closed: pd.DataFrame, fast: int, cfg) -> tuple[str, str] | None:
    within = slope_exit.vturn_within(cfg)
    move = max(slope_exit.vturn_move_pct(cfg), 1e-9)
    turn = max(slope_exit.vturn_turn_pct(cfg), 1e-9)
    s = slopes(closed, fast, slope_exit.lookback(cfg))
    if len(s) < within + 2:
        return None
    now, prev = s.iloc[-1], s.iloc[-2]
    prior = s.iloc[-1 - within : -1].dropna()
    if pd.isna(now) or prior.empty:
        return None
    if now <= -turn and (pd.isna(prev) or prev > -turn) and prior.max() >= move:
        return "SHORT", f"V: SMA {fast} turned down: slope {prior.max():+.3f}%/candle, now {now:+.3f}%/candle"
    if now >= turn and (pd.isna(prev) or prev < turn) and prior.min() <= -move:
        return "LONG", f"V: SMA {fast} turned up: slope {prior.min():+.3f}%/candle, now {now:+.3f}%/candle"
    return None


def alert_text(bot: str, symbol: str, direction: str, note: str, price: float | None, entry: bool) -> str:
    arrow = "▼" if direction == "SHORT" else "▲"
    at = f" at ₹{price:.2f}" if price else ""
    then = (
        f"{bot} orders {'a short' if direction == 'SHORT' else 'a buy'} now (every entry check applies)."
        if entry
        else "Alert only: no order."
    )
    return f"{arrow} {symbol} SMA turn{at} ({bot})\n{note}\n{then}"
