"""SMA 9 turn alert and entry (turn_entry.py): act on the fast SMA's V before the 9/21 cross.

Both off by default, per bot and per stock, PAPER and LIVE. Read once per closed candle of
each armed stock on the bot's own candles, with the same numbers as the V-turn exit
(slope_exit.py): the fast SMA's slope in % per candle over `slope_lookback` candles.

A **turn** is printed on the candle where the slope first reaches `vturn_turn_pct` one way
after it was at least `vturn_move_pct` the other way within the `vturn_within` candles
before: rising then falling = a turn down (sell side), falling then rising = a turn up (buy
side). A slow rounding or a sideways drift never qualifies, and a turn fires once (the
candle after it is no longer "first").

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


def alert_on(cfg) -> bool:
    return bool(getattr(cfg, "turn_alert", False))


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "turn_entry", False))


def watched(cfg) -> bool:
    return alert_on(cfg) or entry_on(cfg)


def slopes(closed: pd.DataFrame, fast: int, back: int) -> pd.Series:
    """The fast SMA's slope in % per candle on every closed candle (NaN until formed)."""
    closes = pd.to_numeric(closed["close"], errors="coerce")
    sma = closes.rolling(max(1, int(fast))).mean()
    then = sma.shift(max(1, int(back)))
    return (sma - then) / then * 100.0 / max(1, int(back))


def detect(closed: pd.DataFrame, fast: int, cfg) -> tuple[str, str] | None:
    """("LONG" | "SHORT", note) when the last closed candle prints a fresh SMA 9 turn, else None."""
    if closed is None or getattr(closed, "empty", True) or "close" not in closed.columns:
        return None
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
        return "SHORT", f"SMA {fast} turned down: slope {prior.max():+.3f}%/candle, now {now:+.3f}%/candle"
    if now >= turn and (pd.isna(prev) or prev < turn) and prior.min() <= -move:
        return "LONG", f"SMA {fast} turned up: slope {prior.min():+.3f}%/candle, now {now:+.3f}%/candle"
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
