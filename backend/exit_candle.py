"""Mixed candles: enter on one candle size, exit on another (`exit_candle_minutes`).

Off by default (0 = the exit uses the bot's own candle, as before); per bot and per stock,
PAPER and LIVE. With a size set that differs from the bot's own `candle_minutes`, e.g.
enter on 1-minute candles and exit on 5-, 10- or 15-minute ones:

* Entries are unchanged: the SMA cross (and gap mode, join, late / early entry …) on the
  bot's own candle opens the trade.
* The **SMA cross exit moves to the exit candle**. An opposite cross on the bot's own
  candle no longer closes or reverses the trade (`cross_exits` reads as off for it), so a
  short 1-minute wiggle against a 5-minute trend does not shake the trade out.
* `StrategyEngine._watch_exit_candle`, once per newly closed exit candle, groups the
  stock's 1-minute Groww tape (`_tapes`) into candles of that size from 09:15
  (`candles.resample`), takes SMA fast / slow on them, and closes the trade
  (`EXIT_CANDLE_CROSS`, via `_exit_now`, which cancels a LIVE stop first) on a candle closed
  after the entry when (`judge`) they **cross against** the trade's signal side, or when they
  are **already against** it and the bot's own candle's SMAs are against it too (a trade
  entered against the exit candle's trend is not held once its own cross turns back). No
  reverse: the next entry waits for its own signal on the bot's own candle.
* It only replaces the SMA cross exit, so it needs `cross_exit` on. The stop / target,
  gap fade, Bollinger, counter-candle, EMA, Supertrend, slope and slow-SMA exits, and the
  square-off, still read the bot's own candle and still close the trade.

A LIVE position restored after a restart without its stop id is left to that stop, as with
the other candle exits. The stock research baseline keeps it off.
"""
from __future__ import annotations

import pandas as pd

from candles import resample

EXIT_MINUTES = (0, 1, 2, 3, 5, 10, 15, 30, 60)
REASON = "EXIT_CANDLE_CROSS"


def minutes(cfg) -> int:
    """The exit candle size in minutes; 0 = off (the bot's own candle)."""
    try:
        value = int(getattr(cfg, "exit_candle_minutes", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return value if value in EXIT_MINUTES else 0


def active(cfg, entry_minutes: int) -> bool:
    """A different exit candle is set and the SMA cross exit is on."""
    m = minutes(cfg)
    return m > 0 and m != int(entry_minutes or 1) and getattr(cfg, "cross_exit", None) is not False


def closed_frame(tape: pd.DataFrame | None, size: int, fast: int, slow: int) -> pd.DataFrame | None:
    """Closed candles of `size` minutes from the 1-minute tape (forming one dropped), with `fast` / `slow` SMAs."""
    if tape is None or getattr(tape, "empty", True) or len(tape) < 2:
        return None
    frame = resample(tape, size) if size > 1 else tape
    if frame is None or len(frame) < 2:
        return None
    frame = frame.iloc[:-1].copy()  # the last row is still forming
    closes = frame["close"].astype(float)
    frame["fast"] = closes.rolling(int(fast)).mean()
    frame["slow"] = closes.rolling(int(slow)).mean()
    return frame


def crossed_against(frame: pd.DataFrame | None, signal_direction: str) -> tuple[bool, str]:
    """True when the last closed candle's SMAs crossed against the trade (fast vs slow changed side)."""
    if frame is None or len(frame) < 2:
        return False, "not enough exit candles"
    prev, last = frame.iloc[-2], frame.iloc[-1]
    if any(pd.isna(v) for v in (prev["fast"], prev["slow"], last["fast"], last["slow"])):
        return False, "SMAs not formed on the exit candle yet"
    was_up, now_up = prev["fast"] > prev["slow"], last["fast"] > last["slow"]
    if signal_direction == "LONG" and was_up and not now_up:
        return True, f"SMA fast {last['fast']:.2f} crossed below slow {last['slow']:.2f}"
    if signal_direction == "SHORT" and not was_up and now_up:
        return True, f"SMA fast {last['fast']:.2f} crossed above slow {last['slow']:.2f}"
    return False, ""


def side_against(fast: float, slow: float, signal_direction: str) -> bool:
    if pd.isna(fast) or pd.isna(slow) or fast == slow:
        return False
    return (fast < slow) if signal_direction == "LONG" else (fast > slow)


def judge(exit_frame: pd.DataFrame | None, entry_closed: pd.DataFrame | None, signal_direction: str, fast: int, slow: int) -> tuple[bool, str]:
    """Exit on the exit candle's cross against the trade, or when both candle sizes are against it."""
    hit, note = crossed_against(exit_frame, signal_direction)
    if hit:
        return True, note
    if exit_frame is None or exit_frame.empty or entry_closed is None or len(entry_closed) < int(slow):
        return False, ""
    last = exit_frame.iloc[-1]
    if not side_against(last["fast"], last["slow"], signal_direction):
        return False, ""
    closes = entry_closed["close"].astype(float)
    own_fast, own_slow = closes.tail(int(fast)).mean(), closes.tail(int(slow)).mean()
    if side_against(own_fast, own_slow, signal_direction):
        return True, "SMA fast is against the trade on both the exit candle and the bot's own candle"
    return False, ""
