"""Hand-off orders wait for the forming candle (handoff_candle.py): WAIT (its way, else skip) and FOLLOW (its colour)."""
from __future__ import annotations

import datetime as dt

import pytest

import handoff_candle as hc


def test_decide():
    assert hc.decide("WAIT", "LONG", 100.2, 100.0, 0.05) == "LONG"
    assert hc.decide("WAIT", "LONG", 99.8, 100.0, 0.05) is None
    assert hc.decide("WAIT", "LONG", 100.02, 100.0, 0.05) is None  # not 0.05 % past the open yet
    assert hc.decide("WAIT", "SHORT", 99.8, 100.0, 0.05) == "SHORT"
    assert hc.decide("FOLLOW", "LONG", 99.8, 100.0, 0.05) == "SHORT"
    assert hc.decide("FOLLOW", "SHORT", 100.2, 100.0, 0.05) == "LONG"
    assert hc.decide("OFF", "LONG", 100.2, 100.0, 0.05) is None
    assert hc.decide("WAIT", "LONG", None, 100.0, 0.05) is None
    assert hc.mode(type("C", (), {"handoff_candle": "follow"})()) == "FOLLOW"
    assert hc.mode(type("C", (), {})()) == "OFF"


async def _engine(monkeypatch, tmp_path, mode):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/h.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from models import BotConfig
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
        row.handoff_candle = mode
        db.commit()
    frame = _tape([100.0 - i * 0.2 for i in range(40)])
    frame.loc[frame.index[-1], "open"] = 50.0  # the forming candle opened at 50
    eng._frames["ACME"] = frame
    eng._ltps["ACME"] = 49.0
    eng.status = "RUNNING"
    return eng, now, database


@pytest.mark.asyncio
async def test_wait_orders_only_once_the_candle_goes_its_way(monkeypatch, tmp_path):
    from models import TradeLog

    eng, now, database = await _engine(monkeypatch, tmp_path, "WAIT")
    text = await eng.direct_order("ACME", "BULLISH", why="HANDOFF")
    assert "waits" in text and "ACME" not in eng.positions
    await eng._watch_handoff_waits(eng.load_config(), now)  # red so far: still waiting
    assert "ACME" not in eng.positions and "ACME" in eng._handoff_wait
    eng._ltps["ACME"] = 51.0  # green
    await eng._watch_handoff_waits(eng.load_config(), now)
    assert eng.positions["ACME"].direction == "LONG" and "ACME" not in eng._handoff_wait
    with database.session_factory()() as db:
        assert db.query(TradeLog).order_by(TradeLog.id.desc()).first().entry_reason == "HANDOFF:CANDLE_CONFIRM"
    database.reset_engine()


@pytest.mark.asyncio
async def test_follow_takes_the_forming_candles_side(monkeypatch, tmp_path):
    eng, now, database = await _engine(monkeypatch, tmp_path, "FOLLOW")
    await eng.direct_order("ACME", "BULLISH", why="HANDOFF")
    await eng._watch_handoff_waits(eng.load_config(), now)  # the forming candle is red
    assert eng.positions["ACME"].direction == "SHORT"
    database.reset_engine()


@pytest.mark.asyncio
async def test_off_orders_at_once_and_news_is_untouched(monkeypatch, tmp_path):
    eng, now, database = await _engine(monkeypatch, tmp_path, "WAIT")
    assert await eng.direct_order("ACME", "BULLISH", why="NEWS") == "opened LONG"  # only hand-offs wait
    database.reset_engine()
