"""US market clock for the US practice desk (NYSE / Nasdaq regular session, New York time).

The regular session is 09:30-16:00 America/New_York (19:00-01:30 IST while the US is on
daylight time, 20:00-02:30 IST after the clocks go back in November). Pre- and
after-market are not traded. Holidays and 13:00 early closes are listed per year below;
add the next year's NYSE calendar before it starts (an unlisted holiday only means the
desk waits on a market that never opens, since no prices arrive).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
IST = ZoneInfo("Asia/Kolkata")

OPEN = dt.time(9, 30)
CLOSE = dt.time(16, 0)
EARLY_CLOSE = dt.time(13, 0)

# NYSE full-day holidays (observed dates).
HOLIDAYS = {
    dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16), dt.date(2026, 4, 3),
    dt.date(2026, 5, 25), dt.date(2026, 6, 19), dt.date(2026, 7, 3), dt.date(2026, 9, 7),
    dt.date(2026, 11, 26), dt.date(2026, 12, 25),
    dt.date(2027, 1, 1), dt.date(2027, 1, 18), dt.date(2027, 2, 15), dt.date(2027, 3, 26),
    dt.date(2027, 5, 31), dt.date(2027, 6, 18), dt.date(2027, 7, 5), dt.date(2027, 9, 6),
    dt.date(2027, 11, 25), dt.date(2027, 12, 24),
}
# Days the market shuts at 13:00 New York time.
EARLY_CLOSES = {dt.date(2026, 11, 27), dt.date(2026, 12, 24), dt.date(2027, 11, 26)}


def et_now() -> dt.datetime:
    return dt.datetime.now(ET)


def to_et(when: dt.datetime) -> dt.datetime:
    return when.astimezone(ET) if when.tzinfo else when.replace(tzinfo=dt.timezone.utc).astimezone(ET)


def is_trading_day(day: dt.date) -> bool:
    return day.weekday() < 5 and day not in HOLIDAYS


def close_time(day: dt.date) -> dt.time:
    return EARLY_CLOSE if day in EARLY_CLOSES else CLOSE


def is_open(now: dt.datetime | None = None) -> bool:
    now = to_et(now or et_now())
    return is_trading_day(now.date()) and OPEN <= now.time() < close_time(now.date())


def next_open(now: dt.datetime | None = None) -> dt.datetime:
    """The next regular-session open (New York time); today's if it is still ahead."""
    now = to_et(now or et_now())
    day = now.date()
    for _ in range(15):
        start = dt.datetime.combine(day, OPEN, tzinfo=ET)
        if is_trading_day(day) and start > now:
            return start
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, OPEN, tzinfo=ET)


def parse_hhmm(text: str | None, default: dt.time) -> dt.time:
    try:
        hh, mm = str(text).split(":")
        return dt.time(int(hh), int(mm))
    except (ValueError, AttributeError):
        return default


def status(now: dt.datetime | None = None) -> dict:
    """What the page shows: New York and India time, open or closed, the next open in both."""
    now = to_et(now or et_now())
    nxt = next_open(now)
    day = now.date()
    return {
        "open": is_open(now),
        "et": now.strftime("%Y-%m-%d %H:%M"),
        "ist": now.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
        "trading_day": is_trading_day(day),
        "close_et": close_time(day).strftime("%H:%M"),
        "close_ist": dt.datetime.combine(day, close_time(day), tzinfo=ET).astimezone(IST).strftime("%H:%M"),
        "next_open_et": nxt.strftime("%Y-%m-%d %H:%M"),
        "next_open_ist": nxt.astimezone(IST).strftime("%Y-%m-%d %H:%M"),
        "dst": bool(now.dst()),
    }
