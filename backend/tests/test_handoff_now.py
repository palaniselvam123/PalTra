"""handoff_now: after a hand-off to Bot 2, Bot 2 orders at once the opposite way of the closed trade."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

import recovery


def _cfg(**over):
    return type("C", (), over)()


@pytest.mark.asyncio
@pytest.mark.parametrize("closed,want", [("SHORT", "LONG"), ("LONG", "SHORT")])
async def test_hand_to_chop_bot_asks_bot_2_for_the_opposite_side(monkeypatch, tmp_path, closed, want):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    import database
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng.handoff = lambda bot, symbol, live, to_bot=2: "moved to Bot 2 (flipped, gap mode) — waits for its next cross"
    asked = []
    eng.handoff_order = lambda to_bot, symbol, direction: asked.append((to_bot, symbol, direction)) or "Bot 2 buys at once"
    text = await eng._hand_to_chop_bot("ACME", False, _cfg(handoff_now=True), closed)
    assert asked == [(2, "ACME", want)] and "at once" in text
    # Off, or a refused hand-off: no order.
    asked.clear()
    await eng._hand_to_chop_bot("ACME", False, _cfg(handoff_now=False), closed)
    eng.handoff = lambda *a, **k: "no handoff: Bot 2 is LIVE and this trade was practice money"
    await eng._hand_to_chop_bot("ACME", False, _cfg(handoff_now=True), closed)
    assert asked == []
    database.reset_engine()


@pytest.mark.asyncio
async def test_order_now_runs_bot_2s_direct_order_and_alerts(monkeypatch):
    import bots
    from strategy_engine import ForceRefused

    calls, alerts = [], []

    class Bot2:
        _signals: dict = {}

        async def direct_order(self, symbol, signal, why, note=""):
            calls.append((symbol, signal, why))
            if symbol == "NOPE":
                raise ForceRefused("market is closed")
            return "opened LONG"

        def _alert(self, text):
            alerts.append(text)

    monkeypatch.setattr(bots, "engines", lambda: {2: Bot2()})
    assert recovery.order_now(2, "acme", "LONG") == "Bot 2 buys at once"
    recovery.order_now(2, "NOPE", "SHORT")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert calls == [("ACME", "BULLISH", "HANDOFF"), ("NOPE", "BEARISH", "HANDOFF")]
    assert "opened LONG" in alerts[0] and "not ordered: market is closed" in alerts[1]
    monkeypatch.setattr(bots, "engines", lambda: {})
    assert "not running" in recovery.order_now(2, "ACME", "LONG")


@pytest.mark.asyncio
async def test_direct_order_tags_the_hand_off_and_ignores_flip(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/d.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from models import BotConfig, TradeLog
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import IST, _FakeBroker, _instant, _tape

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    now = dt.datetime(2026, 9, 29, 11, 0, tzinfo=IST)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: now)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "ACME"
        row.flip_orders = True
        row.use_vwap = True
        db.commit()
    eng._frames["ACME"] = _tape([100.0 - i * 0.2 for i in range(40)])
    eng._ltps["ACME"] = 50.0
    eng.status = "RUNNING"
    assert await eng.direct_order("ACME", "BULLISH", why="HANDOFF") == "opened LONG"
    assert eng.positions["ACME"].direction == "LONG"
    with database.session_factory()() as db:
        assert db.query(TradeLog).order_by(TradeLog.id.desc()).first().entry_reason == "HANDOFF"
    database.reset_engine()


def test_api_saves_handoff_now(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "handoff_now" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["handoff_now"] is False
        ok = client.put("/api/config", json={"handoff_now": True})
        assert ok.status_code == 200 and ok.json()["handoff_now"] is True
    database.reset_engine()
