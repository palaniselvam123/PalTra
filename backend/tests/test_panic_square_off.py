"""Panic / square-off: every held stock is closed at the same time, and a LIVE exit Groww did not fill at the usual
limit is sent once more, further through the touch, instead of leaving the position open. Fake brokers only: no
Groww session, no real order."""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 11, 6, 14, 0, tzinfo=IST)


async def _instant(_seconds=0):
    return None


class SlowPaperBroker:
    """A practice broker whose exit takes a moment, to show the closes overlap."""

    mode = "PAPER"

    def __init__(self, delay: float = 0.15):
        self.delay = delay
        self.events: list[tuple[str, float]] = []

    def set_mode(self, mode, token=None):
        self.mode = mode

    async def place_exit(self, symbol, side, qty, ltp):
        from groww_client import OrderAck

        self.events.append((f"start {symbol}", time.monotonic()))
        await asyncio.sleep(self.delay)
        self.events.append((f"done {symbol}", time.monotonic()))
        return OrderAck(f"X-{symbol}", "FILLED", ltp)

    async def cancel_order(self, order_id):
        return None

    async def get_order_status(self, order_id):
        return "CANCELLED"


class StickyLiveBroker:
    """A LIVE-shaped fake: the first exit never fills (Groww keeps it pending), the second one fills."""

    mode = "LIVE"

    def __init__(self):
        self.exits: list[tuple[str, str, int, float]] = []
        self.cancelled: list[str] = []
        self.held = 10

    def set_mode(self, mode, token=None):
        self.mode = mode

    async def net_quantity(self, symbol):
        return self.held

    async def place_exit(self, symbol, side, qty, ltp):
        from groww_client import OrderAck

        self.exits.append((symbol, side, qty, ltp))
        if len(self.exits) == 1:
            return OrderAck("X1", "OPEN", None)
        self.held = 0
        return OrderAck("X2", "FILLED", ltp)

    async def read_order(self, order_id):
        return ("FILLED", self.exits[-1][3]) if order_id == "X2" else ("OPEN", None)

    async def get_order_status(self, order_id):
        return (await self.read_order(order_id))[0]

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)


def _seed(symbol: str, direction: str, entry: float, mode: str = "PAPER") -> int:
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        row = TradeLog(
            date="2026-11-06",
            symbol=symbol,
            direction=direction,
            qty=10,
            entry_time=dt.datetime(2026, 11, 6, 10, 0),
            entry_price=entry,
            ma_cross_price=entry,
            atr_at_entry=1.0,
            sl_trigger_price=entry * 0.99 if direction == "LONG" else entry * 1.01,
            mode=mode,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return int(row.id)


def _position(symbol: str, direction: str, entry: float, trade_id: int, mode: str = "PAPER"):
    from strategy_engine import OpenPosition

    return OpenPosition(
        direction=direction,
        qty=10,
        entry_price=entry,
        ma_cross_price=entry,
        atr_at_entry=1.0,
        sl_trigger=entry * 0.99 if direction == "LONG" else entry * 1.01,
        sl_order_id="",
        entry_order_id="E1",
        entry_time=dt.datetime(2026, 11, 6, 10, 0, tzinfo=IST),
        trade_id=trade_id,
        mode=mode,
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: NOW)
    import database

    database.reset_engine()
    database.init_db()
    yield database
    database.reset_engine()


def _engine(broker):
    from strategy_engine import StrategyEngine

    eng = StrategyEngine(broker=broker)
    eng._sleep = _instant
    return eng


@pytest.mark.asyncio
async def test_panic_closes_every_stock_at_the_same_time(db):  # noqa: F811
    broker = SlowPaperBroker(0.15)
    eng = _engine(broker)
    for symbol, price in (("RELIANCE", 2400.0), ("TCS", 3100.0), ("SBIN", 800.0), ("INFY", 1500.0)):
        eng.positions[symbol] = _position(symbol, "LONG", price, _seed(symbol, "LONG", price))
        eng._ltps[symbol] = price
    started = time.monotonic()
    await eng.kill("Manual PANIC SQUARE-OFF")
    took = time.monotonic() - started
    assert eng.positions == {} and eng.status == "HALTED"
    # Four closes of 0.15 s each: one after another would take 0.6 s; together they take about one.
    assert took < 0.45, took
    starts = [t for name, t in broker.events if name.startswith("start")]
    dones = [t for name, t in broker.events if name.startswith("done")]
    assert len(starts) == 4 and max(starts) < min(dones)  # every exit was sent before the first one came back
    exits = {row["symbol"]: row["exit_reason"] for row in eng.trades()}
    assert exits == {"RELIANCE": "KILL_SWITCH", "TCS": "KILL_SWITCH", "SBIN": "KILL_SWITCH", "INFY": "KILL_SWITCH"}


@pytest.mark.asyncio
async def test_a_live_exit_groww_did_not_fill_is_sent_again_further_through_the_touch(db):  # noqa: F811
    from models import BotConfig

    with db.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.trading_mode = "LIVE"
        s.commit()
    broker = StickyLiveBroker()
    eng = _engine(broker)
    eng.positions["SBIN"] = _position("SBIN", "LONG", 800.0, _seed("SBIN", "LONG", 800.0, "LIVE"), "LIVE")
    eng._ltps["SBIN"] = 800.0
    await eng.kill("Manual PANIC SQUARE-OFF")
    assert eng.positions == {}
    # The first limit (at the usual 0.20 % through) sat pending and was cancelled; the second went 0.8 % further.
    assert [e[0] for e in broker.exits] == ["SBIN", "SBIN"] and broker.cancelled == ["X1"]
    assert broker.exits[0][3] == pytest.approx(800.0) and broker.exits[1][3] == pytest.approx(800.0 * (1 - 0.008), rel=1e-4)
    (trade,) = eng.trades()
    assert trade["exit_reason"] == "KILL_SWITCH" and trade["exit_price"] == pytest.approx(broker.exits[1][3], rel=1e-4)


@pytest.mark.asyncio
async def test_an_ordinary_exit_is_not_sent_twice(db):  # noqa: F811
    """Only a square-off retries wider: a normal close that Groww leaves unfilled is refused as before."""
    from models import BotConfig
    from strategy_engine import SlCancelFailed

    with db.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.trading_mode = "LIVE"
        s.commit()
    broker = StickyLiveBroker()
    eng = _engine(broker)
    pos = _position("SBIN", "LONG", 800.0, _seed("SBIN", "LONG", 800.0, "LIVE"), "LIVE")
    eng.positions["SBIN"] = pos
    eng._ltps["SBIN"] = 800.0
    cfg = eng.load_config()
    cfg.symbol = "SBIN"
    with pytest.raises(SlCancelFailed):
        await eng._close_position(pos, 800.0, "MA_CROSS", NOW, cfg)
    assert len(broker.exits) == 1 and broker.cancelled == ["X1"]
