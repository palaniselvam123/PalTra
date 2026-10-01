"""A position exists only after the broker confirms the fill.

Every broker here is a fake. No request reaches Groww.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from groww_client import OrderAck, TERMINAL_FILLED

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)


class _Broker:
    """Fake LIVE broker. Each knob decides one Groww answer."""

    def __init__(self, *, entry_status="EXECUTED", read_status="EXECUTED", net=0, sl_raises=False):
        self.mode = "LIVE"
        self.entry_status = entry_status
        self.read_status = read_status
        self.net = net
        self.sl_raises = sl_raises
        self.events: list[str] = []

    def set_mode(self, mode, token=None):
        self.mode = mode

    async def place_entry(self, symbol, side, qty, ltp):
        self.events.append(f"ENTRY {side} {qty}")
        return OrderAck("G1", self.entry_status, ltp if self.entry_status == "EXECUTED" else None)

    async def place_exit(self, symbol, side, qty, ltp):
        self.events.append(f"EXIT {side} {qty}")
        self.net = 0
        return OrderAck("G2", "EXECUTED", ltp)

    async def place_sl(self, symbol, side, qty, trigger):
        self.events.append(f"SL {side} {qty}")
        if self.sl_raises:
            raise RuntimeError("Groww refused the SL")
        return OrderAck("G3", "TRIGGER_PENDING")

    async def cancel_order(self, order_id):
        self.events.append(f"CANCEL {order_id}")
        if self.read_status not in TERMINAL_FILLED:
            self.read_status = "CANCELLED"

    async def read_order(self, order_id):
        return self.read_status, None

    async def get_order_status(self, order_id):
        return self.read_status

    async def net_quantity(self, symbol):
        return self.net


async def _instant(_seconds=0):
    return None


@pytest.fixture
def make_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    import database

    database.reset_engine()
    database.init_db()
    from strategy_engine import StrategyEngine

    def build(broker):
        eng = StrategyEngine(broker=broker)
        eng._sleep = _instant
        cfg = eng.load_config()
        cfg.symbol = "RELIANCE"
        cfg.qty = 10
        cfg.trading_mode = "LIVE"
        cfg.use_stop = True
        eng._focus = "RELIANCE"
        eng._ltps["RELIANCE"] = 1000.0
        eng._session_date = NOW.date().isoformat()
        return eng, cfg

    yield build
    database.reset_engine()


async def _try_open(eng, cfg):
    from strategy_engine import SlCancelFailed

    try:
        await eng._apply_locked(
            signal="BULLISH", cross_price=1000.0, atr=2.0, adx_blocks_entry=False, cfg=cfg, now=NOW
        )
    except SlCancelFailed as exc:
        return str(exc)
    return ""


@pytest.mark.asyncio
async def test_a_rejected_entry_stays_flat_and_is_not_counted(make_engine):
    eng, cfg = make_engine(_Broker(entry_status="REJECTED", read_status="REJECTED"))
    error = await _try_open(eng, cfg)
    assert "REJECTED" in error
    assert "RELIANCE" not in eng.positions
    assert eng.trades_today == 0
    assert eng.trades() == []


@pytest.mark.asyncio
async def test_a_pending_entry_is_cancelled_and_stays_flat(make_engine):
    broker = _Broker(entry_status="OPEN", read_status="OPEN")
    eng, cfg = make_engine(broker)
    error = await _try_open(eng, cfg)
    assert "cancelled" in error
    assert "CANCEL G1" in broker.events
    assert "RELIANCE" not in eng.positions
    assert eng.trades_today == 0


@pytest.mark.asyncio
async def test_triggered_is_not_read_as_a_fill(make_engine):
    broker = _Broker(entry_status="TRIGGERED", read_status="TRIGGERED")
    eng, cfg = make_engine(broker)
    await _try_open(eng, cfg)
    assert "RELIANCE" not in eng.positions
    assert eng.trades_today == 0


@pytest.mark.asyncio
async def test_a_part_fill_on_a_cancelled_order_books_what_groww_holds(make_engine):
    broker = _Broker(entry_status="OPEN", read_status="OPEN", net=4)
    eng, cfg = make_engine(broker)
    await _try_open(eng, cfg)
    pos = eng.positions["RELIANCE"]
    assert pos.qty == 4
    assert "SL SELL 4" in broker.events
    assert eng.trades_today == 1


@pytest.mark.asyncio
async def test_a_confirmed_fill_opens_and_counts(make_engine):
    broker = _Broker()
    eng, cfg = make_engine(broker)
    await _try_open(eng, cfg)
    assert eng.positions["RELIANCE"].direction == "LONG"
    assert eng.trades_today == 1
    assert broker.events == ["ENTRY BUY 10", "SL SELL 10"]


@pytest.mark.asyncio
async def test_a_refused_stop_does_not_leave_a_naked_position(make_engine):
    broker = _Broker(sl_raises=True, net=10)
    eng, cfg = make_engine(broker)
    await _try_open(eng, cfg)
    assert "RELIANCE" not in eng.positions
    assert broker.events[-1] == "EXIT SELL 10"
    row = eng.trades()[0]
    assert row["exit_reason"] == "SL_REJECTED"
    assert "Stop refused" in eng.last_error


@pytest.mark.asyncio
async def test_a_row_groww_never_held_gives_back_its_cap_slot(make_engine):
    broker = _Broker()
    eng, cfg = make_engine(broker)
    await _try_open(eng, cfg)
    assert eng.trades_today == 1
    pos = eng.positions["RELIANCE"]
    eng._book_not_on_groww(pos, NOW)
    eng.position = None
    assert eng.trades_today == 0
    # A restart must agree with the live count.
    assert eng.restore_trades_today() == 0
