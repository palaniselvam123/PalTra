"""The bot only orders stocks on the Trade list (PAPER, fake quotes)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from groww_client import OrderAck
from indicators import closed_candle_cross

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, 1, tzinfo=IST)


def _bullish_now() -> pd.DataFrame:
    """A cross on the bar that closed at 10:29, with the 10:30 bar forming."""
    closes = [100.0] * 30 + [100, 100, 160, 100]
    forming = int(NOW.replace(second=0).timestamp())
    first = forming - (len(closes) - 1) * 60
    rows = [
        {"ts": first + i * 60, "open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": 1000 * (i + 1)}
        for i, c in enumerate(closes)
    ]
    frame = pd.DataFrame(rows)
    from indicators import enrich

    assert closed_candle_cross(enrich(frame)) == "BULLISH"
    return frame


def _set_armed(names: str) -> None:
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = names
        row.symbol = names.split(",")[0] if names else "VIEW"
        row.qty = 1
        row.use_adx_filter = False
        db.commit()


class _Broker:
    """Fake PAPER broker. on_refresh runs while a quote is being fetched."""

    def __init__(self):
        self.mode = "PAPER"
        self.entries: list[str] = []
        self.on_refresh = None

    def set_mode(self, mode, token=None):
        self.mode = mode

    async def refresh(self, symbol):
        if self.on_refresh is not None:
            self.on_refresh(symbol)
        return 100.0, _bullish_now(), "SIMULATOR"

    async def place_entry(self, symbol, side, qty, ltp):
        self.entries.append(symbol)
        return OrderAck(f"PAPER-{len(self.entries)}", "FILLED", ltp)

    async def place_exit(self, symbol, side, qty, ltp):
        return OrderAck("PAPER-X", "FILLED", ltp)

    async def place_sl(self, symbol, side, qty, trigger):
        return OrderAck("PAPER-SL", "TRIGGER_PENDING")

    async def cancel_order(self, order_id):
        return None

    async def get_order_status(self, order_id):
        return "CANCELLED"


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    import database

    database.reset_engine()
    database.init_db()
    from strategy_engine import StrategyEngine

    engine = StrategyEngine(broker=_Broker())
    engine._session_date = NOW.date().isoformat()
    yield engine
    database.reset_engine()


@pytest.mark.asyncio
async def test_both_armed_stocks_trade(eng):
    _set_armed("AAA,BBB")
    eng.status = "RUNNING"
    await eng.tick(NOW)
    assert sorted(eng.broker.entries) == ["AAA", "BBB"]


@pytest.mark.asyncio
async def test_a_stock_removed_during_the_pass_is_not_ordered(eng):
    _set_armed("AAA,BBB")
    eng.status = "RUNNING"

    def disarm_bbb(symbol):
        if symbol == "AAA":
            _set_armed("AAA")  # the user presses Trade on BBB again: off

    eng.broker.on_refresh = disarm_bbb
    await eng.tick(NOW)
    assert eng.broker.entries == ["AAA"]
    assert "BBB" not in eng.positions


@pytest.mark.asyncio
async def test_a_removed_stock_stops_being_quoted_and_its_state_is_dropped(eng):
    _set_armed("AAA,BBB")
    eng.status = "PAUSED"
    await eng.tick(NOW)
    assert "BBB" in eng._frames
    quoted: list[str] = []
    eng.broker.on_refresh = quoted.append
    _set_armed("AAA")
    await eng.tick(NOW)
    assert "BBB" not in quoted
    for table in (eng._frames, eng._ltps, eng._judged_bar, eng._signals, eng._skip_cross_until):
        assert "BBB" not in table


@pytest.mark.asyncio
async def test_a_held_stock_stays_watched_after_it_is_removed(eng):
    from strategy_engine import OpenPosition

    _set_armed("AAA")
    eng.positions["BBB"] = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=100.0,
        ma_cross_price=100.0,
        atr_at_entry=1.0,
        sl_trigger=98.0,
        sl_order_id="",
        entry_order_id="P",
        entry_time=NOW,
        trade_id=0,
        mode="PAPER",
    )
    quoted: list[str] = []
    eng.broker.on_refresh = quoted.append
    await eng.tick(NOW)
    assert "BBB" in quoted
    assert eng._ltps["BBB"] == pytest.approx(100.0)


@pytest.mark.asyncio
async def test_force_order_refuses_a_stock_not_on_the_trade_list(eng, monkeypatch):
    from strategy_engine import ForceRefused

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: NOW)
    _set_armed("AAA")
    with pytest.raises(ForceRefused, match="not on the Trade list"):
        await eng.force_order("ZZZ")
    assert eng.broker.entries == []
    assert eng.status != "RUNNING"
