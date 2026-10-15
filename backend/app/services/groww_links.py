"""Where a stock lives on groww.in, for the Scalp page's "open on Groww" links.

Groww's stock pages are keyed by a name slug (``/stocks/au-small-finance-bank-ltd``),
not the NSE symbol, so the slug is looked up once per symbol on Groww's public
website search and kept in memory. Read-only: one GET to the website search,
nothing to the trading API, no session, no order. When the search fails or finds
no exact NSE match, the link falls back to Groww's search page.
"""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

SEARCH_URL = "https://groww.in/v1/api/search/v3/query/global/st_query"
STOCK_PAGE = "https://groww.in/stocks/{slug}"
SEARCH_PAGE = "https://groww.in/search?q={symbol}"
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,120}")
_cache: dict[str, str] = {}


def pick_slug(payload: dict, symbol: str) -> str | None:
    """The stock's slug from a search answer: the Stocks entry whose NSE code is the symbol."""
    content = ((payload or {}).get("data") or {}).get("content") or []
    for item in content:
        if not isinstance(item, dict) or item.get("entity_type") != "Stocks":
            continue
        if str(item.get("nse_scrip_code") or "").upper() != symbol:
            continue
        slug = str(item.get("search_id") or item.get("id") or "")
        if _SLUG.fullmatch(slug):
            return slug
    return None


async def groww_page(symbol: str, client: httpx.AsyncClient | None = None) -> str:
    """URL of the stock's page on groww.in (its search page when it cannot be found)."""
    symbol = symbol.upper()
    if symbol in _cache:
        return _cache[symbol]
    fallback = SEARCH_PAGE.format(symbol=quote(symbol, safe=""))
    try:
        own = client is None
        client = client or httpx.AsyncClient(timeout=5.0, headers={"User-Agent": "Mozilla/5.0 PalTra"})
        try:
            res = await client.get(SEARCH_URL, params={"query": symbol, "page": 0, "size": 10})
            res.raise_for_status()
            slug = pick_slug(res.json(), symbol)
        finally:
            if own:
                await client.aclose()
    except Exception:  # noqa: BLE001 - a link that opens the search is still useful
        return fallback
    if not slug:
        return fallback
    url = STOCK_PAGE.format(slug=slug)
    _cache[symbol] = url
    return url
