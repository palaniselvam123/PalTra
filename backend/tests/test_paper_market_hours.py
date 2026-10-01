"""PAPER follows market hours and the square-off, like LIVE (no real orders)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from indicators import closed_candle_cross, enrich

IST = ZoneInfo("Asia/Kolkata")
TUE = dt.date(2026, 9, 29)


def _at(hh: int, mm: int, day: dt.date = TUE) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


def _bullish() -> pd.DataFrame:
    closes = [100.0] * 30 + [100, 100, 160, 100]
    rows = [
        {"ts": 1_700_000_000 + i * 60, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1000}
        for i, c in enumerate(closes)
    ]
    frame = enrich(pd.DataFrame(rows))
    assert closed_candle_cross(frame) == "BULLISH"
    return frame


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
    engine.data_source = "SIMULATOR"
    yield engine
    database.reset_engine()


def _cfg(eng):
    cfg = eng.load_config()
    cfg.symbol = "RELIANCE"
    cfg.qty = 1
    cfg.use_adx_filter = False
    cfg.square_off_time = "15:15"
    cfg.trading_mode = "PAPER"
    return cfg


def _hold(eng, entry_time: dt.datetime, symbol: str = "RELIANCE") -> None:
    from strategy_engine import OpenPosition

    eng.positions[symbol] = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=100.0,
        ma_cross_price=100.0,
        atr_at_entry=1.0,
        sl_trigger=98.0,
        sl_order_id="",
        entry_order_id="P",
        entry_time=entry_time,
        trade_id=0,
        mode="PAPER",
    )
    eng._ltps[symbol] = 101.0


@pytest.mark.asyncio
async def test_paper_still_trades_inside_the_session(eng):
    eng.status = "RUNNING"
    await eng.on_minute(_at(10, 30), _cfg(eng), _bullish())
    assert eng.positions["RELIANCE"].direction == "LONG"


@pytest.mark.asyncio
@pytest.mark.parametrize("when", [_at(19, 0), _at(8, 30), _at(11, 0, dt.date(2026, 10, 3))])
async def test_paper_does_not_open_outside_the_session(eng, when):
    eng.status = "RUNNING"
    await eng.on_minute(when, _cfg(eng), _bullish())
    assert eng.positions == {}
    assert eng.broker._orders == {}


@pytest.mark.asyncio
async def test_before_the_open_the_day_is_not_marked_complete(eng):
    eng.status = "RUNNING"
    await eng.on_minute(_at(8, 30), _cfg(eng), _bullish())
    assert eng.status == "RUNNING"
    assert "market closed" in eng.last_signal


@pytest.mark.asyncio
async def test_paper_squares_off_at_1515(eng):
    eng.status = "RUNNING"
    _hold(eng, _at(11, 0))
    await eng.on_minute(_at(15, 15), _cfg(eng), _bullish())
    assert eng.positions == {}
    assert eng.status == "DAY_COMPLETED"


@pytest.mark.asyncio
async def test_a_paused_paper_bot_still_squares_off(eng):
    eng.status = "PAUSED"
    _hold(eng, _at(11, 0))
    await eng._close_finished_paper_books(_at(15, 20), _cfg(eng))
    assert eng.positions == {}


@pytest.mark.asyncio
async def test_a_paper_position_from_yesterday_is_closed_next_morning(eng):
    eng.status = "STOPPED"
    _hold(eng, _at(14, 0))
    _hold(eng, _at(10, 0, dt.date(2026, 9, 30)), symbol="TCS")
    await eng._close_finished_paper_books(_at(10, 5, dt.date(2026, 9, 30)), _cfg(eng))
    assert "RELIANCE" not in eng.positions
    # Today's position is left alone during the session.
    assert "TCS" in eng.positions


@pytest.mark.asyncio
async def test_force_order_is_refused_after_hours(eng, monkeypatch):
    from strategy_engine import ForceRefused

    monkeypatch.setattr("strategy_engine._ist_now", lambda: _at(19, 0))
    with pytest.raises(ForceRefused, match="Market is closed"):
        await eng.force_order("RELIANCE")
    monkeypatch.setattr("strategy_engine._ist_now", lambda: _at(15, 20))
    with pytest.raises(ForceRefused, match="square-off"):
        await eng.force_order("RELIANCE")
    assert eng.broker._orders == {}


def test_live_square_off_rule_is_unchanged(eng):
    cfg = _cfg(eng)
    cfg.trading_mode = "LIVE"
    eng.data_source = "LAST CLOSE"
    # After the close Groww has already flattened MIS; LIVE sends nothing.
    assert eng._past_square_off(_at(19, 0), cfg) is False
    assert eng._past_square_off(_at(15, 16), cfg) is True
