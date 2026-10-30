"""LIVE stop health: a stop Groww cancels or rejects, and exits booked at Groww's real price. Fake broker only."""
from __future__ import annotations

import datetime as dt

import pytest

from groww_client import OrderAck
from tests.test_sma_atr_terminal import IST, _seed_open_trade, engine  # noqa: F401  (engine is a fixture)

T0 = dt.datetime(2026, 10, 29, 12, 30, tzinfo=IST)


def _live(engine, *, net, sl_status, closed=None, sl_fill=None):  # noqa: F811
    from strategy_engine import OpenPosition

    b = engine.broker
    b.sl_status, b.net, b.closed, b.alerts = sl_status, net, closed, []

    async def net_quantity(symbol):
        return b.net

    async def closed_price(symbol, direction):
        return b.closed

    async def read_order(order_id):
        return b.sl_status, sl_fill

    async def order_remark(order_id):
        return "price outside the allowed band"

    async def place_exit(symbol, side, qty, ltp):
        b.events.append(f"EXIT {side}")
        b.net = 0
        return OrderAck("X1", "EXECUTED", 1664.6)

    b.net_quantity, b.closed_price, b.read_order, b.order_remark, b.place_exit = (
        net_quantity, closed_price, read_order, order_remark, place_exit)
    engine.status = "RUNNING"
    engine.ltp = 1664.6
    engine.clock = T0
    engine._now = lambda: engine.clock
    engine._alert = lambda text, critical=False, call=False: b.alerts.append((text, critical, call))
    engine.position = OpenPosition(
        direction="LONG", qty=1, entry_price=1670.7, ma_cross_price=1670, atr_at_entry=4, sl_trigger=1621.0,
        sl_order_id="SL-LIVE", entry_order_id="E-LIVE", entry_time=dt.datetime(2026, 10, 29, 12, 15, tzinfo=IST),
        trade_id=_seed_open_trade(), mode="LIVE", tsl_step=5.0,
    )
    cfg = engine.load_config()
    cfg.trading_mode = "LIVE"
    return cfg


def _row(trade_id):
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        row = db.get(TradeLog, trade_id)
        return row.exit_reason, row.exit_price


@pytest.mark.asyncio
async def test_sold_on_groww_is_booked_at_grows_price_not_the_trigger(engine):  # noqa: F811
    # 29 Oct ATHERENERG: the stop ended cancelled and Groww sold the share at 1664.60.
    cfg = _live(engine, net=0, sl_status="CANCELLED", closed=1664.6)
    trade_id = engine.position.trade_id
    await engine._watch_stop(cfg)
    assert engine.position is None
    assert _row(trade_id) == ("SOLD_ON_GROWW", 1664.6)
    assert not any(e.startswith("EXIT") for e in engine.broker.events)  # nothing sent: it would open a short


@pytest.mark.asyncio
async def test_a_dropped_stop_alerts_then_closes_at_the_market(engine):  # noqa: F811
    cfg = _live(engine, net=1, sl_status="REJECTED")
    trade_id = engine.position.trade_id
    await engine._watch_stop(cfg)
    assert engine.position is not None  # first read: alert and call, no order yet
    (text, critical, call), = engine.broker.alerts
    assert "NO STOP" in text and "outside the allowed band" in text and critical and call
    engine.clock = T0 + dt.timedelta(seconds=2)
    await engine._watch_stop(cfg)
    assert engine.position is not None and engine.broker.events == []  # still inside the grace
    engine.clock = T0 + dt.timedelta(seconds=6)
    await engine._watch_stop(cfg)
    assert engine.position is None and "EXIT SELL" in engine.broker.events
    assert _row(trade_id) == ("STOP_LOST", 1664.6)


@pytest.mark.asyncio
async def test_groww_square_off_inside_the_grace_sends_no_order(engine):  # noqa: F811
    cfg = _live(engine, net=1, sl_status="CANCELLED", closed=1664.6)
    trade_id = engine.position.trade_id
    await engine._watch_stop(cfg)
    engine.broker.net = 0  # Groww's own square-off sold it a moment later
    engine.clock = T0 + dt.timedelta(seconds=6)
    await engine._watch_stop(cfg)
    assert engine.position is None and not any(e.startswith("EXIT") for e in engine.broker.events)
    assert _row(trade_id) == ("SOLD_ON_GROWW", 1664.6)


@pytest.mark.asyncio
async def test_a_filled_stop_books_its_own_fill_price(engine):  # noqa: F811
    cfg = _live(engine, net=0, sl_status="EXECUTED", sl_fill=1619.4)
    trade_id = engine.position.trade_id
    await engine._watch_stop(cfg)
    assert _row(trade_id) == ("TSL_HIT", 1619.4)


@pytest.mark.asyncio
async def test_unreadable_book_does_nothing(engine):  # noqa: F811
    cfg = _live(engine, net=None, sl_status="CANCELLED")
    await engine._watch_stop(cfg)
    assert engine.position is not None and engine.broker.alerts == [] and engine.broker.events == []
