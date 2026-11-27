"""Candle patterns as an extra entry check and an exit (owner's request: the patterns of the "Candle pattern" entry
mode, but as support for whatever opens the trade, not as the entry itself).

* **Entry check** (``pc_entry``, off by default): an entry the bot would make (an SMA cross, a candle colour, a turn, a
  join, a late entry …) goes only when one of the last ``pc_within`` (1-5) closed pattern candles shows a pattern of
  the trade's side: a bullish one to buy, a bearish one to sell. Otherwise it is refused like the other entry checks,
  so late entry can wait for a pattern on a later candle. Read on the signal's side (a flipped stock's buy signal needs a
  bullish pattern, like the other checks).
* **Exit** (``pc_exit``, off by default): once per closed pattern candle that closed after the entry, a pattern against
  the trade (a bearish one under a buy, a bullish one under a sell) closes it (``PATTERN_EXIT``).

The candles are ``pc_minutes`` long (0 = the candle the bot's exits read: its own, or the colour / pattern candle in
those modes), grouped from 09:15 from the 1-minute tape, closed ones only. ``pc_set``: ``STRONG`` (engulfing, piercing
line / dark cloud, morning / evening star, three soldiers / crows, marubozu) or ``ALL`` (adds hammer, shooting star,
harami, tweezers, dragonfly / gravestone doji). The names are the chart's (``candle_patterns.candle_pattern``).

Per bot and per stock, PAPER and LIVE, replays too. No order path of its own: the exit goes through the bot's
``_exit_now`` like every other exit.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from candle_patterns import IST, SOFT_BEAR, SOFT_BULL, STRONG_BEAR, STRONG_BULL, candle_pattern, tf_candles

REASON = "PATTERN_EXIT"
SETS = ("STRONG", "ALL")
SIZES = (0, 1, 3, 5, 10, 15, 30, 60)
WITHIN_MAX = 5
# Candles before the one read, so the pattern reader sees the trend into it (it looks back a few candles).
CONTEXT = 12


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "pc_entry", False))


def exit_on(cfg) -> bool:
    return bool(getattr(cfg, "pc_exit", False))


def minutes(cfg, own: int) -> int:
    """The pattern candle's length: `pc_minutes`, or 0 = `own` (the candle the bot's exits read)."""
    try:
        value = int(getattr(cfg, "pc_minutes", 0) or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value in SIZES and value > 0 else max(1, int(own or 1))


def pattern_set(cfg) -> str:
    value = str(getattr(cfg, "pc_set", None) or "STRONG").upper()
    return value if value in SETS else "STRONG"


def within(cfg) -> int:
    try:
        value = int(getattr(cfg, "pc_within", 1) or 1)
    except (TypeError, ValueError):
        value = 1
    return max(1, min(WITHIN_MAX, value))


def names(cfg, side: str) -> set[str]:
    """The patterns that count for a trade on `side` (LONG wants bullish ones, SHORT bearish)."""
    full = pattern_set(cfg) == "ALL"
    if side == "LONG":
        return STRONG_BULL | (SOFT_BULL if full else set())
    return STRONG_BEAR | (SOFT_BEAR if full else set())


def candles(tape: pd.DataFrame | None, size: int) -> list[dict]:
    """The closed `size`-minute candles of the tape (its last row is the forming minute), from 09:15."""
    if tape is None or getattr(tape, "empty", True) or len(tape) < 2:
        return []
    closed = tape.iloc[:-1]
    # Only the last couple of sessions matter for a pattern: keep the grouping cheap on a long tape.
    keep = (CONTEXT + WITHIN_MAX + 2) * max(1, int(size)) + 400
    if len(closed) > keep:
        closed = closed.iloc[-keep:]
    return tf_candles(closed, int(size))


def _clock(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def _word(side: str) -> str:
    return "bullish" if side == "LONG" else "bearish"


def found(rows: list[dict], side: str, cfg, last: int) -> tuple[str, int] | None:
    """(pattern, candle start) of the newest of the last `last` candles that shows a pattern for `side`, else None."""
    wanted = names(cfg, side)
    for i in range(len(rows) - 1, max(-1, len(rows) - 1 - last), -1):
        name, _ = candle_pattern(rows, i)
        if name in wanted:
            return name, int(rows[i]["ts"])
    return None


def entry_reason(tape: pd.DataFrame | None, side: str, cfg, own_minutes: int) -> str | None:
    """None when the entry may go; else why not, in plain words (joins the entry checks' refusal line)."""
    size = minutes(cfg, own_minutes)
    rows = candles(tape, size)
    n = within(cfg)
    if len(rows) < 3:
        return f"candle pattern: waiting for 3 closed {size}-min candles"
    hit = found(rows, side, cfg, n)
    if hit is not None:
        return None
    last_name, _ = candle_pattern(rows, len(rows) - 1)
    span = "the last closed" if n == 1 else f"the last {n}"
    return (
        f"candle pattern: no {_word(side)} pattern on {span} {size}-min candle{'s' if n > 1 else ''} "
        f"(last {_clock(rows[-1]['ts'])}: {last_name})"
    )


def entry_note(tape: pd.DataFrame | None, side: str, cfg, own_minutes: int) -> str | None:
    """The pattern an allowed entry rests on ("Bullish engulfing at 10:15 on 5-min candles"), for the signal line."""
    size = minutes(cfg, own_minutes)
    hit = found(candles(tape, size), side, cfg, within(cfg))
    return None if hit is None else f"{hit[0]} at {_clock(hit[1])} on {size}-min candles"


def exit_read(
    tape: pd.DataFrame | None, side: str, cfg, own_minutes: int, opened_ts: float
) -> tuple[int | None, str | None]:
    """(start of the newest closed pattern candle, why the trade closes or None).

    Only a candle that closed after the entry is read (a pattern from before the trade never closes it). The caller
    judges each candle once, by its start."""
    size = minutes(cfg, own_minutes)
    rows = candles(tape, size)
    if len(rows) < 3:
        return None, None
    start = int(rows[-1]["ts"])
    if start + size * 60 <= opened_ts:
        return start, None
    against = "SHORT" if side == "LONG" else "LONG"
    name, _ = candle_pattern(rows, len(rows) - 1)
    if name not in names(cfg, against):
        return start, None
    return start, f"{name} at {_clock(start)} on {size}-min candles, against the {'buy' if side == 'LONG' else 'sell'}"
