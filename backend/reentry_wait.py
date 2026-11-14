"""Wait N candles after an exit (`reentry_wait_candles`, 0 = off; per bot and per stock, PAPER and LIVE, replays too).

Owner's request (MANKIND, 15-minute candle colour): every exit was followed at once by the next entry, so a sideways
stock was bought, sold, bought again on every candle. With N set, after *any* exit on a stock (a stop, a target, a
colour turn, a cross, the square-off…) that stock takes no new entry until N candles have closed after the exit. The
exit itself is never held back; an opposite signal that closes a trade does not reverse it (the stock waits flat).
After the wait the stock enters on its next signal as usual.

"Candles" are the ones the trade's exits read (`StrategyEngine._exit_minutes`): the bot's own candle, or the colour /
pattern candles (`pattern_tf`) in those modes, grouped from 09:15 IST. The candle the exit happened in counts as the
first one only when the exit came in its first `grace` seconds (90, at most half a candle) (an exit on a candle's close is processed a little after
the next candle opened): so a colour turn at 11:00 on 15-minute candles with N = 2 waits out 11:00-11:15 and
11:15-11:30, and the next entry can go at 11:30; a stop hit at 11:07 waits out 11:15-11:30 and 11:30-11:45.
"""
from __future__ import annotations

import datetime as dt
import math

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SESSION_OPEN = dt.time(9, 15)
MAX_CANDLES = 10
GRACE_MAX_SEC = 90  # an exit this soon after a candle opened was its close, processed late (the 1-minute tape lags)


def candles(cfg) -> int:
    """The setting, 0-10 (0 = off)."""
    try:
        value = int(getattr(cfg, "reentry_wait_candles", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_CANDLES, value))


def _ist(when: dt.datetime) -> dt.datetime:
    return when.replace(tzinfo=IST) if when.tzinfo is None else when.astimezone(IST)


def ready_at(exit_at: dt.datetime, minutes: int, n: int) -> dt.datetime:
    """When the stock may enter again: N whole candles of `minutes` after the exit (see the module note)."""
    exit_at = _ist(exit_at)
    span = max(1, int(minutes)) * 60
    grace = min(GRACE_MAX_SEC, span // 2)
    open_ = dt.datetime.combine(exit_at.date(), SESSION_OPEN, tzinfo=IST)
    since = (exit_at - open_).total_seconds() - grace
    first = open_ + dt.timedelta(seconds=max(0, math.ceil(since / span)) * span)
    return first + dt.timedelta(seconds=n * span)


def refusal(symbol: str, exit_at: dt.datetime | None, now: dt.datetime, minutes: int, n: int) -> str | None:
    """Why this stock takes no entry now, or None."""
    if n <= 0 or exit_at is None:
        return None
    exit_at = _ist(exit_at)
    now = _ist(now)
    if exit_at.date() != now.date():
        return None  # a new day starts fresh
    ready = ready_at(exit_at, minutes, n)
    if now >= ready:
        return None
    return (
        f"{symbol} waits {n} candle(s) of {int(minutes)} min after its exit at {exit_at:%H:%M} "
        f"(next entry from {ready:%H:%M})"
    )
