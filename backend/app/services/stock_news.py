"""News for one stock: NSE company announcements plus Google News headlines.

Groww's trading API has no news, and the news on groww.in comes from Groww's own
website (no documented API), so two public sources are used instead:

* **NSE announcements** (`nse_announcements`): the exchange's own corporate filings
  for the symbol (results, board meetings, orders, dividends), from
  nseindia.com's `api/corporate-announcements`. NSE wants its own cookies, so the
  home page is opened first in the same client.
* **Google News** (`google_news`): headlines from Google News' public RSS search
  for the company's name (from the instrument list) or the symbol, last 7 days.

A **past day** (`day=`) asks both sources for that day's window instead (`window`: the
`LOOKBACK_DAYS` days before it up to its 15:30 close; Google's `after:` / `before:`
operators, NSE's `from_date` / `to_date`), keeps only items inside it and caches it longer.

`stock_news` asks both at once, keeps each source's error to itself, merges the
items newest first and caches the answer per symbol for `CACHE_SEC`. Read-only:
no key, no login, no order path; nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import email.utils
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

import httpx

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
CACHE_SEC = 300
MAX_ITEMS = 40
TIMEOUT = 12.0
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
NSE_HOME = "https://www.nseindia.com/"
NSE_API = "https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}"
NSE_PAGE = "https://www.nseindia.com/get-quotes/equity?symbol={symbol}"
# Every listed company's latest announcements in one answer (the all-F&O news pass, news_impact.NewsWatcher).
NSE_MARKET_API = "https://www.nseindia.com/api/corporate-announcements?index=equities"
NSE_MARKET_PAGE = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
GOOGLE_RSS = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"

_cache: dict[str, tuple[float, dict]] = {}
_SUFFIX = re.compile(r"\b(LIMITED|LTD\.?|INDIA LTD|\(INDIA\))\s*$", re.I)


#: A past day's news (`day=`): that day up to the close, plus the LOOKBACK_DAYS days before it.
LOOKBACK_DAYS = 2
SESSION_CLOSE = dt.time(15, 30)
PAST_CACHE_SEC = 6 * 3600


def window(day: dt.date) -> tuple[dt.datetime, dt.datetime]:
    """The news a trader could have read by `day`'s close: LOOKBACK_DAYS before it, up to 15:30 IST."""
    start = dt.datetime.combine(day - dt.timedelta(days=LOOKBACK_DAYS), dt.time(0, 0), tzinfo=IST)
    return start, dt.datetime.combine(day, SESSION_CLOSE, tzinfo=IST)


def company_query(symbol: str, name: str | None, day: dt.date | None = None) -> str:
    """The Google News search: the company's name without "Limited", else the symbol. The last 7 days, or
    with `day` the dates of its window (Google's after: / before: operators; before is exclusive)."""
    clean = _SUFFIX.sub("", (name or "").strip()).strip(" .,")
    term = f'"{clean}"' if clean and clean.upper() != symbol.upper() else f'"{symbol}" share'
    if day is None:
        return f"{term} when:7d"
    start, _ = window(day)
    return f"{term} after:{start:%Y-%m-%d} before:{day + dt.timedelta(days=1):%Y-%m-%d}"


def _iso(when: dt.datetime | None) -> str | None:
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return when.astimezone(IST).isoformat(timespec="seconds")


def parse_google_rss(text: str) -> list[dict]:
    """Items of a Google News RSS answer: title, link, publisher, time (IST)."""
    out = []
    root = ET.fromstring(text)
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        if not title or not link:
            continue
        source_el = item.find("source")
        source = (source_el.text or "").strip() if source_el is not None else ""
        # Google adds " - Publisher" to every title; the publisher has its own field.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3].rstrip()
        try:
            when = email.utils.parsedate_to_datetime(item.findtext("pubDate") or "")
        except (TypeError, ValueError):
            when = None
        out.append({"kind": "news", "title": title, "url": link, "source": source or "Google News", "at": _iso(when)})
    return out


def _nse_time(row: dict) -> dt.datetime | None:
    for key, fmt in (("sort_date", "%Y-%m-%d %H:%M:%S"), ("an_dt", "%d-%b-%Y %H:%M:%S")):
        raw = row.get(key)
        if raw:
            try:
                return dt.datetime.strptime(str(raw).strip(), fmt).replace(tzinfo=IST)
            except ValueError:
                continue
    return None


def parse_nse(rows: object, symbol: str) -> list[dict]:
    """NSE's corporate announcements for the symbol: subject, details, attachment, time."""
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    out = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        subject = str(row.get("desc") or "").strip()
        detail = str(row.get("attchmntText") or "").strip()
        if not (subject or detail):
            continue
        out.append(
            {
                "kind": "announcement",
                "title": subject or detail[:120],
                "detail": detail if detail and detail != subject else None,
                "url": str(row.get("attchmntFile") or "").strip() or NSE_PAGE.format(symbol=symbol),
                "source": "NSE",
                "at": _iso(_nse_time(row)),
            }
        )
    return out


def parse_nse_market(rows: object) -> list[dict]:
    """NSE's market-wide announcements: each one as `parse_nse` reads it, plus its `symbol`."""
    if isinstance(rows, dict):
        rows = rows.get("data") or []
    out: list[dict] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        out.extend({**item, "symbol": symbol} for item in parse_nse([row], symbol))
    return out


async def nse_market_announcements(client: httpx.AsyncClient) -> list[dict]:
    """One request for every company's latest NSE announcements (not one per stock)."""
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
    await client.get(NSE_HOME, headers={**headers, "Accept": "text/html"})
    res = await client.get(NSE_MARKET_API, headers={**headers, "Referer": NSE_MARKET_PAGE})
    res.raise_for_status()
    return parse_nse_market(res.json())


async def google_news(symbol: str, name: str | None, client: httpx.AsyncClient, day: dt.date | None = None) -> list[dict]:
    url = GOOGLE_RSS.format(q=quote_plus(company_query(symbol, name, day)))
    res = await client.get(url, headers={"User-Agent": UA})
    res.raise_for_status()
    return parse_google_rss(res.text)


async def nse_announcements(symbol: str, client: httpx.AsyncClient, day: dt.date | None = None) -> list[dict]:
    headers = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "en-US,en;q=0.9"}
    # NSE answers its API only with the cookies its home page sets.
    await client.get(NSE_HOME, headers={**headers, "Accept": "text/html"})
    url = NSE_API.format(symbol=quote_plus(symbol))
    if day is not None:
        start, _ = window(day)
        url += f"&from_date={start:%d-%m-%Y}&to_date={day:%d-%m-%Y}"
    res = await client.get(
        url,
        headers={**headers, "Referer": NSE_PAGE.format(symbol=symbol)},
    )
    res.raise_for_status()
    return parse_nse(res.json(), symbol)


def _name_of(symbol: str) -> str | None:
    try:
        from app.services.instruments import instrument_master

        inst = instrument_master.get(symbol)
        return inst.name if inst else None
    except Exception:  # noqa: BLE001 - the symbol alone still searches
        return None


def _why(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        return f"answered {exc.response.status_code}"
    if isinstance(exc, httpx.TimeoutException):
        return "timed out"
    return str(exc)[:160] or type(exc).__name__


def in_window(item: dict, day: dt.date) -> bool:
    """A dated item inside `day`'s window (an undated one is kept: the search already asked for those dates)."""
    at = item.get("at")
    if not at:
        return True
    try:
        when = dt.datetime.fromisoformat(at)
    except ValueError:
        return True
    when = when if when.tzinfo else when.replace(tzinfo=IST)
    start, end = window(day)
    return start <= when <= end


async def stock_news(symbol: str, force: bool = False, day: dt.date | None = None) -> dict:
    """Both sources for one symbol, newest first, cached for CACHE_SEC. With `day`, the news of that past day's
    window only (`window`), cached longer: an earlier day's news does not change."""
    symbol = symbol.strip().upper()
    if day is not None and day >= dt.datetime.now(IST).date():
        day = None  # today (or later) is the live search
    key = symbol if day is None else f"{symbol}@{day.isoformat()}"
    ttl = CACHE_SEC if day is None else PAST_CACHE_SEC
    hit = _cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < ttl:
        return hit[1]
    name = await asyncio.to_thread(_name_of, symbol)
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        news, nse = await asyncio.gather(
            google_news(symbol, name, client, day), nse_announcements(symbol, client, day), return_exceptions=True
        )
    errors = {}
    items: list[dict] = []
    for label, got in (("google_news", news), ("nse", nse)):
        if isinstance(got, Exception):
            errors[label] = _why(got)
        else:
            items.extend(got)
    if day is not None:
        items = [i for i in items if in_window(i, day)]
    items.sort(key=lambda i: i.get("at") or "", reverse=True)
    out = {
        "symbol": symbol,
        "name": name,
        "day": day.isoformat() if day else None,
        "items": items[:MAX_ITEMS],
        "errors": errors,
        "fetched_at": dt.datetime.now(IST).isoformat(timespec="seconds"),
    }
    # A total failure is not cached, so the next look tries again at once.
    if items or not errors:
        _cache[key] = (time.monotonic(), out)
    return out
