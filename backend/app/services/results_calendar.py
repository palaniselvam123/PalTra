"""Results calendar from NSE's own data (read-only; the News page's "Results calendar" tab).

Two public NSE answers, read with the same cookie dance as `stock_news` (home page first):

* **When results are due**: NSE's event calendar (`api/event-calendar`, falling back to `api/corporate-board-meetings`):
  every board meeting called to consider financial results, with its date and purpose (`parse_calendar`,
  `is_results_meeting`).
* **When they were really filed**: NSE's market-wide corporate announcements for the dates that have passed
  (`api/corporate-announcements` with `from_date` / `to_date`): each company's "Financial Result" / "Outcome of
  Board Meeting" filing and its exact time (`results_filings`).

`build` joins them per stock and meeting: `status` UPCOMING (date ahead), TODAY (due today, not filed yet), FILED
(with `filed_at` and `session`: BEFORE_OPEN / IN_MARKET / AFTER_CLOSE, i.e. whether the market could trade it the same
day) or NOT_SEEN (date passed, no filing found in NSE's answer); a results filing with no calendar entry in the range is
added as FILED too. Cached `CACHE_SEC`. No key, no order path; nothing in the strategy reads it.
"""
from __future__ import annotations

import datetime as dt
import re
import time

import httpx

from app.services import stock_news

IST = stock_news.IST
OPEN = dt.time(9, 15)
CLOSE = dt.time(15, 30)
CACHE_SEC = 900
MAX_RANGE_DAYS = 62
#: A results filing is matched to the meeting it follows: on that day or up to this many days after (late-night filings).
FILED_WITHIN_DAYS = 2
NSE_EVENTS = "https://www.nseindia.com/api/event-calendar?index=equities&from_date={a}&to_date={b}"
NSE_MEETINGS = "https://www.nseindia.com/api/corporate-board-meetings?index=equities&from_date={a}&to_date={b}"
NSE_FILINGS = "https://www.nseindia.com/api/corporate-announcements?index=equities&from_date={a}&to_date={b}"
NSE_EVENTS_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-event-calendar"

_RESULTS_MEETING = re.compile(r"financial results?|quarterly results?|audited results?|unaudited|results? for the", re.I)
_RESULTS_FILED = re.compile(r"financial results?|results? for the (quarter|period|half|year)", re.I)
_INTEGRATED = re.compile(r"integrated filing.{0,4}financ", re.I)
# Notices about results that are not the results themselves.
_NOT_RESULTS = re.compile(r"delay|non[- ]?submission|intimation|prior intimation|newspaper|trading window", re.I)
_OUTCOME = re.compile(r"outcome of (the )?board meeting", re.I)
_cache: dict[tuple, tuple[float, dict]] = {}


def _date(raw) -> dt.date | None:
    text = str(raw or "").strip()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d", "%d-%b-%Y %H:%M:%S", "%d %b %Y"):
        try:
            return dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def is_results_meeting(purpose: str, desc: str) -> bool:
    return bool(_RESULTS_MEETING.search(f"{purpose} {desc}"))


def parse_calendar(rows: object) -> list[dict]:
    """Board meetings to consider results, from NSE's event calendar or board-meetings answer (either key set)."""
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    out: list[dict] = []
    seen: set[tuple[str, dt.date]] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or row.get("bm_symbol") or "").strip().upper()
        day = _date(row.get("date") or row.get("bm_date"))
        purpose = str(row.get("purpose") or row.get("bm_purpose") or "").strip()
        desc = str(row.get("bm_desc") or row.get("desc") or "").strip()
        if not symbol or day is None or not is_results_meeting(purpose, desc) or (symbol, day) in seen:
            continue
        seen.add((symbol, day))
        out.append({
            "symbol": symbol,
            "company": str(row.get("company") or row.get("sm_name") or "").strip() or None,
            "meeting_date": day.isoformat(),
            "purpose": purpose or "Financial Results",
            "desc": desc or None,
        })
    return out


def results_filings(items: list[dict]) -> dict[str, list[dict]]:
    """symbol -> its results filings (time, title, link), oldest first, from parsed market-wide announcements
    (`stock_news.parse_nse_market`). `strict` marks a filing that names the results (not only a board-meeting outcome)."""
    out: dict[str, list[dict]] = {}
    for it in items:
        # The filing's subject decides (NSE's "Financial Result Updates", "Integrated Filing- Financial", "Outcome of
        # Board Meeting"); a "General Updates" that only mentions results in its text is not a results filing.
        title = it.get("title") or ""
        if _NOT_RESULTS.search(title):
            continue
        strict = bool(_RESULTS_FILED.search(title) or _INTEGRATED.search(title))
        if not (strict or _OUTCOME.search(title)):
            continue
        when = stock_news._when(it.get("at"))
        if when is None or not it.get("symbol"):
            continue
        out.setdefault(it["symbol"], []).append(
            {"at": when, "title": it.get("title"), "url": it.get("url"), "strict": strict}
        )
    for rows in out.values():
        rows.sort(key=lambda r: r["at"])
    return out


def session_of(when: dt.datetime) -> str:
    """Whether the market could trade it the same day: before 09:15, in market hours, or after 15:30 / a weekend."""
    if when.weekday() >= 5:
        return "AFTER_CLOSE"
    t = when.timetz().replace(tzinfo=None)
    if t < OPEN:
        return "BEFORE_OPEN"
    return "IN_MARKET" if t < CLOSE else "AFTER_CLOSE"


def first_trading_day(when: dt.datetime) -> dt.date:
    """The first session whose prices can carry the news: the same day before the close, else the next weekday."""
    day = when.date()
    if session_of(when) == "AFTER_CLOSE" or day.weekday() >= 5:
        day += dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return day


def build(meetings: list[dict], filings: dict[str, list[dict]], today: dt.date, *, end: dt.date) -> list[dict]:
    """One row per results meeting (plus results filings with no meeting in range), with its status."""
    rows: list[dict] = []
    used: set[tuple[str, dt.datetime]] = set()
    for m in meetings:
        day = dt.date.fromisoformat(m["meeting_date"])
        lo = dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST)
        hi = lo + dt.timedelta(days=FILED_WITHIN_DAYS + 1)
        near = [f for f in filings.get(m["symbol"], []) if lo <= f["at"] < hi]
        row = {**m, "status": "UPCOMING" if day > today else "TODAY" if day == today else "NOT_SEEN"}
        if near:
            # The first filing after the meeting is when the news came out; the rest (integrated filing, press
            # release, …) belong to the same meeting.
            used.update((m["symbol"], f["at"]) for f in near)
            row.update(_filed(near[0]))
            row["filings"] = len(near)
        rows.append(row)
    for symbol, fs in filings.items():
        extra = [f for f in fs if f["strict"] and (symbol, f["at"]) not in used and f["at"].date() <= end]
        days: set[dt.date] = set()
        for f in extra:  # one row per stock and day: its first results filing
            if f["at"].date() in days:
                continue
            days.add(f["at"].date())
            rows.append({
                "symbol": symbol, "company": None, "meeting_date": f["at"].date().isoformat(),
                "purpose": "Financial Results", "desc": None, "status": "FILED", "from_filing": True, **_filed(f),
            })
    rows.sort(key=lambda r: (r["meeting_date"], r.get("filed_at") or "99", r["symbol"]))
    return rows


def _filed(f: dict) -> dict:
    return {
        "status": "FILED",
        "filed_at": f["at"].isoformat(timespec="minutes"),
        "session": session_of(f["at"]),
        "trades_on": first_trading_day(f["at"]).isoformat(),
        "filing_title": f.get("title"),
        "filing_url": f.get("url"),
    }


async def _nse_json(client: httpx.AsyncClient, url: str, referer: str):
    res = await client.get(url, headers={**_headers(), "Referer": referer})
    res.raise_for_status()
    return res.json()


def _headers() -> dict:
    return {"User-Agent": stock_news.UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}


async def fetch(start: dt.date, end: dt.date, today: dt.date) -> tuple[list[dict], dict[str, list[dict]], dict[str, str]]:
    """NSE's results meetings for [start, end] and the results filings for the days that have passed."""
    errors: dict[str, str] = {}
    meetings: list[dict] = []
    filings: dict[str, list[dict]] = {}
    a, b = f"{start:%d-%m-%Y}", f"{end:%d-%m-%Y}"
    async with httpx.AsyncClient(timeout=stock_news.TIMEOUT * 2, follow_redirects=True) as client:
        await client.get(stock_news.NSE_HOME, headers={**_headers(), "Accept": "text/html"})
        try:
            meetings = parse_calendar(await _nse_json(client, NSE_EVENTS.format(a=a, b=b), NSE_EVENTS_PAGE))
        except Exception as exc:  # noqa: BLE001 - try the board-meetings list instead
            errors["calendar"] = stock_news._why(exc)
        if not meetings:
            try:
                meetings = parse_calendar(await _nse_json(client, NSE_MEETINGS.format(a=a, b=b), NSE_EVENTS_PAGE))
                errors.pop("calendar", None)
            except Exception as exc:  # noqa: BLE001
                errors.setdefault("calendar", stock_news._why(exc))
        past_end = min(end + dt.timedelta(days=FILED_WITHIN_DAYS), today)
        if start <= past_end:
            url = NSE_FILINGS.format(a=a, b=f"{past_end:%d-%m-%Y}")
            try:
                filings = results_filings(stock_news.parse_nse_market(await _nse_json(client, url, stock_news.NSE_MARKET_PAGE)))
            except Exception as exc:  # noqa: BLE001 - the calendar still shows the dates
                errors["filings"] = stock_news._why(exc)
    return meetings, filings, errors


async def calendar(start: dt.date, end: dt.date, *, force: bool = False, now: dt.datetime | None = None) -> dict:
    """The results calendar for [start, end] (every listed stock; the route narrows it). Cached CACHE_SEC."""
    now = now or dt.datetime.now(IST)
    key = (start, end)
    hit = _cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < CACHE_SEC:
        return hit[1]
    meetings, filings, errors = await fetch(start, end, now.date())
    rows = build(meetings, filings, now.date(), end=end)
    out = {
        "from": start.isoformat(), "to": end.isoformat(), "rows": rows, "errors": errors,
        "fetched_at": now.isoformat(timespec="seconds"),
        "disclaimer": "From NSE's event calendar and filings. Information only: nothing is traded from this.",
    }
    if rows or not errors:
        _cache[key] = (time.monotonic(), out)
    return out

