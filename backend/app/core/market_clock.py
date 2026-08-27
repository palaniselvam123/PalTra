"""NSE session clock, always evaluated in IST regardless of the host's
timezone — a server running in UTC must not square off at the wrong hour.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

MARKET_OPEN = dt.time(9, 15)
MARKET_CLOSE = dt.time(15, 30)
OPENING_RANGE_END = dt.time(9, 30)


def ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def is_trading_day(now: dt.datetime | None = None) -> bool:
    """Weekday check only. NSE trading holidays are not encoded here — a
    holiday will look like an open day with a frozen feed, which the staleness
    check in the live feed surfaces.
    """
    now = now or ist_now()
    return now.weekday() < 5


def is_market_open(now: dt.datetime | None = None) -> bool:
    now = now or ist_now()
    return is_trading_day(now) and MARKET_OPEN <= now.time() <= MARKET_CLOSE


def session_state(now: dt.datetime | None = None) -> str:
    now = now or ist_now()
    if not is_trading_day(now):
        return "WEEKEND"
    t = now.time()
    if t < MARKET_OPEN:
        return "PRE_OPEN"
    if t > MARKET_CLOSE:
        return "CLOSED"
    return "OPEN"


def seconds_until_open(now: dt.datetime | None = None) -> int | None:
    """Seconds to the next open, or None if the market is currently open."""
    now = now or ist_now()
    if is_market_open(now):
        return None
    candidate = dt.datetime.combine(now.date(), MARKET_OPEN, tzinfo=IST)
    while candidate <= now or candidate.weekday() >= 5:
        candidate += dt.timedelta(days=1)
        candidate = dt.datetime.combine(candidate.date(), MARKET_OPEN, tzinfo=IST)
    return int((candidate - now).total_seconds())
