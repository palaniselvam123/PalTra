"""With use_stop off, nothing reports a stop that does not exist (PAPER only)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)


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


def _cfg(eng, use_stop: bool):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "RELIANCE"
        row.trade_symbols = "RELIANCE"
        row.qty = 1
        row.use_stop = use_stop
        db.commit()
    return eng.load_config()


@pytest.mark.asyncio
async def test_stop_off_sends_no_stop_and_shows_none(eng):
    cfg = _cfg(eng, use_stop=False)
    eng._focus = "RELIANCE"
    eng._ltps["RELIANCE"] = 1000.0
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    assert not any(order.kind == "SL" for order in eng.broker._orders.values())

    snap = eng.snapshot()
    assert snap["stop_enabled"] is False
    assert snap["position"]["stop_active"] is False
    assert snap["position"]["sl_trigger"] is None
    assert snap["active_sl_trigger"] is None
    assert snap["sl_room"] is None
    book = next(b for b in snap["books"] if b["symbol"] == "RELIANCE")
    assert book["stop_active"] is False and book["sl_trigger"] is None
    assert eng.chart_payload()["sl_trigger"] is None
    row = eng.trades()[0]
    assert row["stop_active"] is False and row["sl_trigger_price"] is None


@pytest.mark.asyncio
async def test_a_price_through_the_would_be_stop_does_not_close_it(eng):
    cfg = _cfg(eng, use_stop=False)
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    eng.ltp = 900.0
    await eng._watch_stop(cfg)
    assert "RELIANCE" in eng.positions


@pytest.mark.asyncio
async def test_stop_on_still_shows_and_triggers(eng):
    cfg = _cfg(eng, use_stop=True)
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    eng._ltps["RELIANCE"] = 1000.0
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    snap = eng.snapshot()
    assert snap["position"]["stop_active"] is True
    assert snap["active_sl_trigger"] == pytest.approx(997.0)
    assert snap["atr_multiplier"] == pytest.approx(1.5)
    eng.ltp = 996.0
    await eng._watch_stop(cfg)
    assert "RELIANCE" not in eng.positions
    assert eng.trades()[0]["exit_reason"] == "ATR_SL_HIT"


@pytest.mark.asyncio
async def test_turning_the_box_off_later_does_not_drop_a_placed_stop(eng):
    cfg = _cfg(eng, use_stop=True)
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    cfg = _cfg(eng, use_stop=False)
    eng._focus = "RELIANCE"
    eng.ltp = 996.0
    await eng._watch_stop(cfg)
    assert "RELIANCE" not in eng.positions


def test_fill_alert_says_stop_off():
    from strategy_engine import fill_alert

    text = fill_alert(mode="PAPER", direction="LONG", symbol="X", qty=1, fill=10.0, stop=None, when=NOW)
    assert "Stop OFF" in text
