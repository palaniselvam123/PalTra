"""Loss limit per trade in ₹ (trade_loss.py). PAPER only: exits are recorded, nothing is ordered."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import trade_loss
from tests.test_day_target import _engine

CFG = SimpleNamespace(trade_loss_rs=20_000, symbol="SHREECEM")


def test_the_same_money_for_a_cheap_and_a_costly_stock():
    # 1,000 shares: a ₹1,000 stock may fall 20 points, a ₹50,000 stock 20 points too, the same ₹20,000.
    assert trade_loss.level("LONG", 1000.0, 1000, CFG) == pytest.approx(980.0)
    assert trade_loss.level("SHORT", 50_000.0, 1000, CFG) == pytest.approx(50_020.0)
    assert trade_loss.open_loss("LONG", 1000.0, 990.0, 1000) == pytest.approx(10_000)
    assert trade_loss.open_loss("LONG", 1000.0, 1100.0, 1000) == 0  # in profit: nothing to limit
    assert not trade_loss.hit("LONG", 1000.0, 980.5, 1000, CFG)
    assert trade_loss.hit("LONG", 1000.0, 980.0, 1000, CFG)
    assert trade_loss.hit("SHORT", 50_000.0, 50_025.0, 1000, CFG)
    assert not trade_loss.on(SimpleNamespace()) and trade_loss.level("LONG", 100.0, 10, SimpleNamespace()) is None


@pytest.mark.asyncio
async def test_the_engine_closes_only_a_losing_trade_at_the_limit():
    eng, calls = _engine("LONG", 1000.0)
    eng.positions["SHREECEM"].qty = 1000
    eng.ltp = 1500.0  # far in profit: left to run
    await eng._watch_trade_loss(CFG)
    eng.ltp = 985.0  # down ₹15,000: under the limit
    await eng._watch_trade_loss(CFG)
    assert calls == []
    eng.ltp = 979.0
    await eng._watch_trade_loss(CFG)
    assert calls == [(979.0, trade_loss.EXIT_REASON)]
    assert "loss limit per trade" in eng._signals["SHREECEM"]


@pytest.mark.asyncio
async def test_off_by_default_and_a_short():
    eng, calls = _engine("SHORT", 1000.0)
    eng.positions["SHREECEM"].qty = 1000
    eng.ltp = 1100.0
    await eng._watch_trade_loss(SimpleNamespace(symbol="SHREECEM"))
    assert calls == []
    await eng._watch_trade_loss(CFG)
    assert calls == [(1100.0, trade_loss.EXIT_REASON)]


def test_api_saves_the_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        assert client.get("/api/bots/2/config").json()["trade_loss_rs"] == 0
        assert client.put("/api/bots/2/config", json={"trade_loss_rs": 25000}).status_code == 200
        assert client.get("/api/bots/2/config").json()["trade_loss_rs"] == 25000
        assert client.put("/api/bots/2/config", json={"trade_loss_rs": -1}).status_code == 422
    database.reset_engine()
