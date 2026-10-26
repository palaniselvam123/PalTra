"""News page: validity till the next trading day's close, watch lists, and prices in the alerts."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.core.market_clock import IST
from app.services import news_impact as ni
from tests.test_news_impact import db  # noqa: F401

FRI = dt.datetime(2026, 10, 23, 11, 0, tzinfo=IST)  # a Friday


def test_valid_until_the_next_trading_days_close():
    assert ni.valid_until(FRI.isoformat()) == dt.datetime(2026, 10, 26, 15, 30, tzinfo=IST)  # Fri -> Mon
    wed = dt.datetime(2026, 10, 21, 20, 0, tzinfo=IST)
    assert ni.valid_until(wed.isoformat()) == dt.datetime(2026, 10, 22, 15, 30, tzinfo=IST)  # today -> tomorrow
    sat = dt.datetime(2026, 10, 24, 9, 0, tzinfo=IST)
    assert ni.valid_until(sat.isoformat()) == dt.datetime(2026, 10, 26, 15, 30, tzinfo=IST)
    assert ni.valid_until(None) is None and ni.is_valid(None, FRI)
    mon_close = dt.datetime(2026, 10, 26, 15, 30, tzinfo=IST)
    assert ni.is_valid(FRI.isoformat(), mon_close)
    assert not ni.is_valid(FRI.isoformat(), mon_close + dt.timedelta(minutes=1))
    item = ni.with_validity({"at": FRI.isoformat()}, mon_close + dt.timedelta(hours=1))
    assert item["valid"] is False and item["valid_until"].startswith("2026-10-26T15:30")


def test_expired_news_no_longer_moves_the_view():
    good = {**ni.score_text("ACME bags order worth Rs 900 crore"), "at": FRI.isoformat()}
    assert ni.overall([good], FRI + dt.timedelta(days=3))["sentiment"] == "POSITIVE"  # Monday: still valid
    tue = dt.datetime(2026, 10, 27, 10, 0, tzinfo=IST)
    assert ni.overall([good], tue)["counted"] == 0


def test_watch_list_adds_every_lists_stocks_after_the_bots():
    lists = [{"name": "Banks", "symbols": ["HDFCBANK", "SBIN"]}, {"name": "Mine", "symbols": ["SBIN", "TCS"]}]
    out = ni.watch_list(["INFY"], lists)
    assert list(out) == ["INFY", "HDFCBANK", "SBIN", "TCS"]
    assert out["SBIN"]["lists"] == ["Banks", "Mine"]


def test_alert_text_carries_price_lists_and_validity():
    view = ni.overall([], FRI)
    result = {"symbol": "SBIN", "overall": view, "action": ni.action(view, None), "method": "rules", "plan": ni.plan(view)}
    item = {"title": "SBIN bags order", "source": "NSE", "sentiment": "POSITIVE", "impact": "HIGH", "at": FRI.isoformat()}
    text = ni.alert_text(result, [item], {"armed": [], "held": [], "lists": ["Banks"]}, False, 812.4)
    assert "price ₹812.40" in text and "Watch lists: Banks" in text and "valid till Mon 26 Oct 15:30" in text


@pytest.mark.asyncio
async def test_lists_crud_and_a_list_stock_is_alerted_with_its_price(db, monkeypatch):  # noqa: F811
    from app.services import news_lists, stock_news
    from app.services.alert_notifier import alert_notifier

    banks = await news_lists.create("Banks", ["hdfcbank", "SBIN", "SBIN"])
    assert banks["symbols"] == ["HDFCBANK", "SBIN"]
    other = await news_lists.create("Other", [])
    row = await news_lists.update(banks["id"], add=["ICICIBANK"], remove=["HDFCBANK"])
    assert row["symbols"] == ["SBIN", "ICICIBANK"]
    await news_lists.delete(other["id"])
    assert [x["name"] for x in await news_lists.all_lists()] == ["Banks"]
    with pytest.raises(news_lists.ListError):
        await news_lists.create("Bad", ["NOT A SYMBOL"])
    with pytest.raises(LookupError):
        await news_lists.update(999, add=["TCS"])

    async def fake_news(symbol, force=False, day=None):
        items = [{"title": "SBIN bags order worth Rs 900 crore", "at": (FRI - dt.timedelta(hours=1)).isoformat(),
                  "source": "NSE", "url": "s1"}] if symbol == "SBIN" else []
        return {"symbol": symbol, "name": symbol, "errors": {}, "items": items}

    async def no_key():
        return None

    sent = []

    async def send(msg):
        sent.append(msg)

    async def prices(symbols, groww=True):
        return {"SBIN": 812.4}

    monkeypatch.setattr(stock_news, "stock_news", fake_news)
    monkeypatch.setattr(ni, "_openai_key", no_key)
    monkeypatch.setattr(news_lists, "prices", prices)
    monkeypatch.setattr(alert_notifier, "send", send)
    from app.models.database import NewsWatchSettings

    cfg = NewsWatchSettings(id=1, enabled=True, every_min=15, min_impact="MEDIUM", use_ai=False, popup=True,
                            telegram=True, call_critical=True, extra_symbols="", all_fno=False, fno_telegram=False)
    made = await ni.NewsWatcher().run_once(cfg, FRI)
    assert [r.symbol for r in made] == ["SBIN"]
    out = ni.alert_out(made[0])
    assert out["price"] == 812.4 and out["lists"] == ["Banks"] and out["valid_until"].startswith("2026-10-26")
    assert len(sent) == 1 and "price ₹812.40" in sent[0]
    assert json.loads(made[0].body)["price"] == 812.4


def test_link_routes_need_the_desk_and_its_token(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import server_role
    from app.api import routes_link

    app = FastAPI()
    app.include_router(routes_link.router)
    client = TestClient(app)
    monkeypatch.setattr(server_role, "link_token", lambda: "secret-token")
    monkeypatch.setattr(server_role, "is_desk", lambda: True)
    monkeypatch.setattr(server_role, "is_scanner", lambda: False)
    assert client.get(routes_link.NEWS_LISTS).status_code == 401
    assert routes_link.token_path(routes_link.NEWS_LISTS_ADD)
    assert client.get("/api/link/desk-news-lists").status_code == 404  # scanner side only
