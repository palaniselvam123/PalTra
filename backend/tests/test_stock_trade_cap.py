"""Per-stock daily trade cap (max_trades_per_stock, 0 = off): counted per bot and per stock, restored after a restart."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest


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


def _add(symbol: str, day: str, n: int) -> None:
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        for i in range(n):
            db.add(TradeLog(
                date=day, symbol=symbol, direction="LONG", qty=1, entry_time=dt.datetime(2026, 10, 7, 10, i),
                entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=99.0,
                exit_time=dt.datetime(2026, 10, 7, 10, i, 30), exit_price=100.5, exit_reason="MA_CROSS",
                gross_pnl=0.5, brokerage_and_taxes=0.1, net_pnl=0.4, mode="PAPER", bot=1,
            ))
        db.commit()


def test_the_cap_is_off_at_zero_and_counts_each_stock_on_its_own(eng):
    eng._stock_trades = {"TCS": 2}
    assert eng._stock_cap_hit(SimpleNamespace(symbol="TCS", max_trades_per_stock=0)) is None
    assert eng._stock_cap_hit(SimpleNamespace(symbol="TCS", max_trades_per_stock=3)) is None
    assert eng._stock_cap_hit(SimpleNamespace(symbol="TCS", max_trades_per_stock=2)) == "TCS has had its 2 trade(s) today (per-stock cap)"
    assert eng._stock_cap_hit(SimpleNamespace(symbol="INFY", max_trades_per_stock=2)) is None


def test_a_restart_counts_todays_trades_per_stock(eng):
    day = eng._session_date
    _add("TCS", day, 2)
    _add("INFY", day, 1)
    _add("TCS", "2020-01-01", 5)
    assert eng.restore_trades_today() == 3
    assert eng._stock_trades == {"TCS": 2, "INFY": 1}
    assert eng._stock_cap_hit(SimpleNamespace(symbol="TCS", max_trades_per_stock=2)) is not None
    assert eng._stock_cap_hit(SimpleNamespace(symbol="INFY", max_trades_per_stock=2)) is None
    eng.reset_trade_count()
    assert eng._stock_trades == {}


def test_the_setting_is_per_stock_too_and_saved_through_the_config():
    from strategy_engine import STOCK_FIELDS, _cfg_for
    from models import BotConfig

    assert "max_trades_per_stock" in STOCK_FIELDS
    row = BotConfig(symbol="TCS", max_trades_per_stock=1, stock_settings='{"INFY": {"max_trades_per_stock": 4}}')
    assert _cfg_for(row, "INFY").max_trades_per_stock == 4
    assert _cfg_for(row, "TCS").max_trades_per_stock == 1
