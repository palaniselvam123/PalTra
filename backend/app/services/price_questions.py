"""Recognise "what was X at 11:00" and answer it from the recorded prices.

**Why parse the question at all.** The chat advisor is deliberately grounded: it
answers only from a FACTS snapshot and is forbidden from inventing a number. A
price at a past minute is not in that snapshot, so without this the honest
answer would always be "I do not have that recorded" — even though the recorder
has it on disk.

This closes that gap by looking the price up *before* the model sees the
question and putting the result into the facts. The model still never invents a
price; it just now has the one that was asked for.

**Ambiguity is reported, not resolved by guessing.** If the symbol is not in the
tracked universe, or nothing was recorded near that minute, the fact says so
plainly and the model relays that. A confident wrong price is worse than "not
recorded" — the user has no way to tell the two apart.
"""
from __future__ import annotations

import datetime as dt
import re

from app.core.market_clock import IST, ist_now
from app.research.snapshots import ist_date, snapshot_store

# "at 11", "at 11:00", "at 11am", "at 11.30", "11:00 am"
_TIME_RE = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?\b", re.IGNORECASE
)
_DAY_WORDS = {
    "today": 0,
    "yesterday": -1,
}


def _known_symbols() -> set[str]:
    """Every symbol the app might have a price for."""
    from app.services.market_data import market_data

    symbols = {s.upper() for s in market_data.symbols}
    try:
        from app.research.cross_sectional import SECTOR_OF

        symbols |= set(SECTOR_OF)
    except Exception:  # noqa: BLE001 — the research map is optional context
        pass
    return symbols


def find_symbol(text: str) -> str | None:
    """The longest tracked symbol mentioned, matched on word boundaries.

    Longest-first so that a name which contains another (BAJAJ-AUTO versus a
    hypothetical BAJAJ) resolves to the one actually written.
    """
    upper = text.upper()
    for symbol in sorted(_known_symbols(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(symbol)}\b", upper):
            return symbol
    return None


def find_time(text: str) -> dt.time | None:
    """A wall-clock time mentioned in the question, IST.

    Bare hours are read as market hours: "at 11" during a trading discussion
    means 11:00, and 24-hour input is accepted as written.
    """
    for match in _TIME_RE.finditer(text):
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        meridiem = (match.group(3) or "").lower().replace(".", "")

        if meridiem.startswith("p") and hour < 12:
            hour += 12
        elif meridiem.startswith("a") and hour == 12:
            hour = 0
        elif not meridiem and hour <= 6:
            # 1-6 with no meridiem in an Indian market context is the afternoon;
            # the session only runs 09:15-15:30, so 3 means 15:00.
            hour += 12

        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return dt.time(hour, minute)
    return None


def find_day(text: str, today: dt.date) -> dt.date:
    lowered = text.lower()
    for word, offset in _DAY_WORDS.items():
        if word in lowered:
            return today + dt.timedelta(days=offset)
    iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", text)
    if iso:
        try:
            return dt.date.fromisoformat(iso.group(1))
        except ValueError:
            pass
    return today


def looks_like_price_question(text: str) -> bool:
    lowered = text.lower()
    asks_price = any(w in lowered for w in ("price", "trading at", "quote", "was it at", "cost"))
    has_time = _TIME_RE.search(text) is not None
    return asks_price and has_time


def lookup(text: str, source: str | None = None) -> dict | None:
    """Answer a point-in-time price question, or explain why it cannot be.

    Returns None when the question is not one of these, so the caller can leave
    the facts untouched.
    """
    if not looks_like_price_question(text):
        return None

    from app.services.market_data import market_data

    src = source or market_data.source.value
    now = ist_now()
    today = ist_date(int(now.timestamp()))

    symbol = find_symbol(text)
    when_time = find_time(text)
    day = find_day(text, today)

    if symbol is None:
        return {
            "asked": text,
            "answered": False,
            "reason": "No tracked symbol was named in the question.",
        }
    if when_time is None:
        return {
            "asked": text,
            "answered": False,
            "symbol": symbol,
            "reason": "No time of day was recognised in the question.",
        }

    when = dt.datetime.combine(day, when_time, tzinfo=IST)
    point = snapshot_store.price_at(symbol, when, src)

    if point is None:
        return {
            "asked": text,
            "answered": False,
            "symbol": symbol,
            "day": day.isoformat(),
            "requested_time_ist": when_time.strftime("%H:%M"),
            "source": src,
            "reason": (
                f"No price for {symbol} was recorded within 15 minutes before "
                f"{when_time.strftime('%H:%M')} on {day.isoformat()} from the {src} feed. "
                "Either the recorder was not running then, or the symbol was not being tracked."
            ),
        }

    return {
        "asked": text,
        "answered": True,
        "symbol": point.symbol,
        "day": day.isoformat(),
        "requested_time_ist": when_time.strftime("%H:%M"),
        "recorded_time_ist": point.time_ist,
        "price": round(point.price, 2),
        "session_open": round(point.open_price, 2) if point.open_price else None,
        "pct_from_open": round(point.pct_from_open, 3) if point.pct_from_open is not None else None,
        "source": src,
        "note": (
            "Prices are recorded once a minute. `recorded_time_ist` is the minute actually "
            "found; if it differs from the requested time, report the difference rather than "
            "presenting it as exact."
        ),
    }
