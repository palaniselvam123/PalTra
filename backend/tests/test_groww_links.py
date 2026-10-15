"""Scalp page "open on Groww" links: symbol -> groww.in stock page. No network, no order path."""
from __future__ import annotations

import httpx
import pytest

from app.services import groww_links

ANSWER = {
    "data": {
        "content": [
            {"entity_type": "Stocks", "nse_scrip_code": "AUBANKX", "search_id": "other-ltd"},
            {"entity_type": "Stocks", "nse_scrip_code": "AUBANK", "search_id": "au-small-finance-bank-ltd"},
        ]
    }
}


def _client(answer, status=200):
    return httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(status, json=answer)))


@pytest.fixture(autouse=True)
def _clear():
    groww_links._cache.clear()
    yield
    groww_links._cache.clear()


def test_the_exact_nse_match_is_picked():
    assert groww_links.pick_slug(ANSWER, "AUBANK") == "au-small-finance-bank-ltd"
    assert groww_links.pick_slug(ANSWER, "TCS") is None
    assert groww_links.pick_slug({"data": {"content": [{"entity_type": "Stocks", "nse_scrip_code": "X", "search_id": "../evil"}]}}, "X") is None


@pytest.mark.asyncio
async def test_found_stock_opens_its_page_and_is_cached():
    async with _client(ANSWER) as c:
        url = await groww_links.groww_page("aubank", client=c)
    assert url == "https://groww.in/stocks/au-small-finance-bank-ltd"
    assert groww_links._cache["AUBANK"] == url


@pytest.mark.asyncio
async def test_a_failed_search_falls_back_to_the_search_page():
    async with _client({}, status=500) as c:
        url = await groww_links.groww_page("GVT&D", client=c)
    assert url == "https://groww.in/search?q=GVT%26D"
    assert "GVT&D" not in groww_links._cache


def test_the_route_redirects_and_refuses_odd_input(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_instruments
    from app.main import app

    async def fake_page(name):
        return f"https://groww.in/stocks/{name.lower()}-ltd"

    monkeypatch.setattr(routes_instruments, "groww_page", fake_page)
    with TestClient(app) as client:
        res = client.get("/api/instruments/groww/infy", follow_redirects=False)
        assert res.status_code == 302 and res.headers["location"] == "https://groww.in/stocks/infy-ltd"
        assert client.get("/api/instruments/groww/A%20B", follow_redirects=False).status_code == 400
