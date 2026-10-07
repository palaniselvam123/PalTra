"""Reset today's trade count: the cap counts again from 0; P&L and loss limit stay. PAPER only."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    engine = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    yield engine
    database.reset_engine()


def _add_trades(n: int, day: str) -> None:
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        for i in range(n):
            db.add(TradeLog(
                date=day, symbol="TCS", direction="LONG", qty=1, entry_time=dt.datetime(2026, 10, 7, 10, i),
                entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=99.0,
                exit_time=dt.datetime(2026, 10, 7, 10, i, 30), exit_price=100.5, exit_reason="MA_CROSS",
                gross_pnl=0.5, brokerage_and_taxes=0.1, net_pnl=0.4, mode="PAPER",
            ))
        db.commit()


def test_reset_survives_a_restart_and_lifts_only_a_cap_halt(eng):
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    day = eng._session_date
    _add_trades(5, day)
    assert eng.restore_trades_today() == 5
    eng.status, eng.halt_reason = "HALTED", "max_trades_per_day (5) reached"
    assert eng.reset_trade_count() == 5
    assert eng.trades_today == 0 and eng.status == "STOPPED" and eng.halt_reason == ""
    # Today's P&L still counts the earlier trades.
    assert eng._kpis("PAPER")["trades"] == 5
    _add_trades(2, day)
    fresh = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    assert fresh.restore_trades_today() == 2  # only trades after the reset

    eng.status, eng.halt_reason = "HALTED", "Daily loss limit hit"
    eng.reset_trade_count()
    assert eng.status == "HALTED"  # a loss-limit halt stays locked


def test_a_reset_from_another_day_is_ignored(eng):
    import database
    from models import BotConfig

    _add_trades(3, eng._session_date)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_count_reset_id, row.trade_count_reset_date = 999, "2020-01-01"
        db.commit()
    assert eng.restore_trades_today() == 3


def test_api_resets_each_desk_on_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app, engine, research_engine

    with TestClient(app) as client:
        engine.trades_today, research_engine.trades_today = 7, 4
        body = client.post("/api/bot/reset-trades").json()
        assert body["was"] == 7 and body["trades_today"] == 0
        assert research_engine.trades_today == 4
        assert client.post("/api/research/bot/reset-trades").json()["was"] == 4
    database.reset_engine()
