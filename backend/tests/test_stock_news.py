"""Stock news (app/services/stock_news.py): parsing, merging and the route. No network: fakes only."""
from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import stock_news as sn

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Reliance shares rise after results - Economic Times</title>
<link>https://news.example/a</link><pubDate>Thu, 22 Oct 2026 09:30:00 GMT</pubDate>
<source url="https://economictimes.indiatimes.com">Economic Times</source></item>
<item><title></title><link>https://news.example/empty</link></item>
<item><title>No date story</title><link>https://news.example/b</link></item>
</channel></rss>"""

NSE = [
    {"symbol": "RELIANCE", "desc": "Outcome of Board Meeting", "attchmntText": "Board approved results",
     "attchmntFile": "https://nsearchives.example/x.pdf", "sort_date": "2026-10-22 16:05:10"},
    {"symbol": "RELIANCE", "desc": "", "attchmntText": "", "sort_date": "2026-10-21 10:00:00"},
    {"symbol": "RELIANCE", "desc": "Press Release", "attchmntText": "Press Release", "an_dt": "20-Oct-2026 11:15:00"},
]


def test_query_uses_the_company_name_without_limited():
    assert sn.company_query("RELIANCE", "RELIANCE INDUSTRIES LIMITED") == '"RELIANCE INDUSTRIES" when:7d'
    assert sn.company_query("SBIN", None) == '"SBIN" share when:7d'
    assert sn.company_query("TCS", "TCS") == '"TCS" share when:7d'


def test_google_rss_items_lose_the_publisher_suffix_and_get_ist_time():
    items = sn.parse_google_rss(RSS)
    assert [i["title"] for i in items] == ["Reliance shares rise after results", "No date story"]
    first = items[0]
    assert first["source"] == "Economic Times" and first["at"] == "2026-10-22T15:00:00+05:30"
    assert items[1]["at"] is None and items[1]["source"] == "Google News"


def test_nse_rows_become_announcements_with_links():
    items = sn.parse_nse(NSE, "RELIANCE")
    assert len(items) == 2
    assert items[0] == {
        "kind": "announcement", "title": "Outcome of Board Meeting", "detail": "Board approved results",
        "url": "https://nsearchives.example/x.pdf", "source": "NSE", "at": "2026-10-22T16:05:10+05:30",
    }
    assert items[1]["detail"] is None and items[1]["at"] == "2026-10-20T11:15:00+05:30"
    assert "get-quotes" in items[1]["url"]
    assert sn.parse_nse({"data": NSE[:1]}, "RELIANCE")[0]["title"] == "Outcome of Board Meeting"
    assert sn.parse_nse("oops", "X") == []


@pytest.mark.asyncio
async def test_both_sources_merge_newest_first_and_one_failure_stays_its_own(monkeypatch):
    sn._cache.clear()
    calls = []

    async def news(symbol, name, client, day=None):
        calls.append("news")
        return sn.parse_google_rss(RSS)

    async def nse(symbol, client, day=None):
        raise httpx.HTTPStatusError("blocked", request=httpx.Request("GET", "https://x"), response=httpx.Response(403))

    monkeypatch.setattr(sn, "google_news", news)
    monkeypatch.setattr(sn, "nse_announcements", nse)
    monkeypatch.setattr(sn, "_name_of", lambda s: "RELIANCE INDUSTRIES LIMITED")
    out = await sn.stock_news("reliance")
    assert out["symbol"] == "RELIANCE" and out["errors"] == {"nse": "answered 403"}
    assert [i["title"] for i in out["items"]][0] == "Reliance shares rise after results"
    await sn.stock_news("RELIANCE")  # cached
    assert calls == ["news"]

    async def nse_ok(symbol, client, day=None):
        return sn.parse_nse(NSE, symbol)

    monkeypatch.setattr(sn, "nse_announcements", nse_ok)
    out = await sn.stock_news("RELIANCE", force=True)
    assert out["errors"] == {}
    times = [i["at"] for i in out["items"] if i["at"]]
    assert times == sorted(times, reverse=True) and out["items"][0]["source"] == "NSE"


def test_route_checks_the_symbol(monkeypatch):
    from app.api import routes_instruments

    async def fake(symbol, force=False):
        return {"symbol": symbol, "items": [], "errors": {}}

    monkeypatch.setattr(routes_instruments, "stock_news", fake)
    app = FastAPI()
    app.include_router(routes_instruments.router)
    client = TestClient(app)
    assert client.get("/api/instruments/news/sbin").json()["symbol"] == "SBIN"
    assert client.get("/api/instruments/news/bad%20name").status_code == 400
