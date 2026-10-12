"""Join the morning trend (join_trend.py): enter a trend whose SMA cross the bot missed.

`join_trend` (off by default) and `join_after_min` (default 15; per bot and per
stock, PAPER and LIVE). Normally a trade opens only on a fresh SMA cross on
today's closed candles, so a stock that opens already trending (its cross printed
yesterday or before 09:20) is never traded. With `join_trend` on, once per day per
stock: from `join_after_min` minutes after 09:15, while the bot is flat, the side the
SMAs are on is treated like a cross, as long as that run started before that time
(the morning trend). The usual entry path (`apply_signal`) is asked on each closed
candle, and the first candle on which every ticked filter, ADX and the cut-off /
cap / stop / one-LIVE-bot-per-stock checks agree opens the trade. In gap mode the
side arms the gap entry instead, so the gap level must be met too.

It stops trying once it has joined (or armed gap mode) that day, when a new cross
prints (the normal cross rules take over), or when the bot already traded that run.
The memory is per session and in-process (`StrategyEngine._joined_today`,
`_side_entered`).

Pure helpers only; the engine keeps the state.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

DEFAULT_AFTER_MIN = 15
MAX_AFTER_MIN = 360
SESSION_OPEN = (9, 15)


def enabled(cfg) -> bool:
    return bool(getattr(cfg, "join_trend", False))


def after_min(cfg) -> int:
    value = getattr(cfg, "join_after_min", None)
    try:
        value = DEFAULT_AFTER_MIN if value is None else int(value)
    except (TypeError, ValueError):
        return DEFAULT_AFTER_MIN
    return max(0, min(MAX_AFTER_MIN, value))


def join_from(cfg, now: dt.datetime) -> dt.datetime:
    """The IST time from which today's morning trend may be joined."""
    open_ = now.replace(hour=SESSION_OPEN[0], minute=SESSION_OPEN[1], second=0, microsecond=0)
    return open_ + dt.timedelta(minutes=after_min(cfg))


def side_start(frame: pd.DataFrame | None) -> tuple[str, int] | None:
    """(LONG | SHORT, ts of the first closed candle of the current SMA side), or None."""
    if frame is None or len(frame) < 3 or not {"sma_9", "sma_21", "ts"}.issubset(frame.columns):
        return None
    closed = frame.iloc[:-1]  # the forming candle is never read
    fast = pd.to_numeric(closed["sma_9"], errors="coerce")
    slow = pd.to_numeric(closed["sma_21"], errors="coerce")
    diff = (fast - slow).where(slow != 0)
    signs = diff[diff.notna() & (diff != 0)] > 0
    if signs.empty:
        return None
    up = bool(signs.iloc[-1])
    flips = signs.ne(signs.shift())
    first = flips[flips].index[-1]  # first candle of the current run
    start = int(closed.loc[first, "ts"])
    # A run carried over from an earlier day starts at today's first candle, so the
    # start does not move as old candles leave the frame.
    ts = pd.to_numeric(closed["ts"], errors="coerce").astype("int64")
    today = ts[(ts + 19_800) // 86_400 == (int(ts.iloc[-1]) + 19_800) // 86_400]
    return ("LONG" if up else "SHORT"), max(start, int(today.iloc[0]))
