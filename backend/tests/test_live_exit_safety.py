"""LIVE exit safety: what happens to the exchange stop around the bot's own exits, failed exits, restarts,
paused / halted bots, square-offs, and orders running next to the bot's pass. No network: stub brokers only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from groww_client import OrderAck
from tests.test_sma_atr_terminal import IST, _seed_open_trade, _tape, engine  # noqa: F401
from tests.test_stop_health import T0, _live


def _row(trade_id):
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        row = db.get(TradeLog, trade_id)
        return row.exit_reason, row.exit_price, row.gross_pnl, row.sl_order_id


@pytest.mark.asyncio
async def test_a_filled_vwap_stop_is_booked_at_grows_price_and_sends_nothing(engine):  # noqa: F811
    cfg = _live(engine, net=0, sl_status="EXECUTED", sl_fill=1619.4)
    engine.position.tsl_step = None
    engine.position.vwap_stop = True
    trade_id = engine.position.trade_id
    await engine._watch_stop(cfg)
    reason, price, gross, _ = _row(trade_id)
    assert (reason, price) == ("VWAP_SL_HIT", 1619.4)
    assert gross < 0  # the real loss, not a ₹0 "not on Groww"
    assert not any(e.startswith("EXIT") for e in engine.broker.events)


@pytest.mark.asyncio
async def test_every_bot_exit_cancels_the_exchange_stop_first(engine):  # noqa: F811
    for reason in ("EXIT_CANDLE_CROSS", "TREND_HANDOFF"):
        engine.broker.events.clear()
        cfg = _live(engine, net=1, sl_status="TRIGGER_PENDING")
        await engine._exit_now(cfg, engine.ltp, reason, gate=False)
        assert engine.broker.events[:2] == ["CANCEL SL-LIVE", "EXIT SELL"], reason
        assert engine.position is None


@pytest.mark.asyncio
async def test_the_15_second_sweep_leaves_a_position_with_its_stop_to_the_stop_watch(engine):  # noqa: F811
    cfg = _live(engine, net=0, sl_status="EXECUTED", sl_fill=1619.4)
    trade_id = engine.position.trade_id
    held = dict(engine.positions)
    await engine._drop_positions_groww_does_not_hold(cfg)
    assert engine.positions == held  # not booked "NOT_ON_GROWW" at ₹0
    await engine._watch_stop(cfg)
    assert _row(trade_id)[:2] == ("TSL_HIT", 1619.4)


@pytest.mark.asyncio
async def test_a_failed_exit_after_the_stop_was_cancelled_places_the_stop_again(engine):  # noqa: F811
    cfg = _live(engine, net=1, sl_status="TRIGGER_PENDING")
    b = engine.broker
    engine.position.target = 1660.0

    async def place_exit(symbol, side, qty, ltp):
        b.events.append(f"EXIT {side}")
        return OrderAck("X9", "OPEN", None)

    async def read_order(order_id):
        return ("CANCELLED", None) if order_id == "X9" else (b.sl_status, None)

    b.place_exit, b.read_order = place_exit, read_order
    with pytest.raises(Exception):
        await engine._watch_stop(cfg)  # target hit, the exit never fills and is cancelled
    pos = engine.position
    assert pos is not None
    assert "CANCEL SL-LIVE" in b.events
    assert b.events[-1] == "SL SELL 1621.0"  # protected again at its own level
    assert pos.sl_order_id == "SL1" and pos.naked_since is None
    assert _row(pos.trade_id)[3] == "SL1"  # kept for a restart


@pytest.mark.asyncio
async def test_a_stop_that_cannot_be_placed_again_alerts_and_is_retried(engine):  # noqa: F811
    cfg = _live(engine, net=1, sl_status="TRIGGER_PENDING")
    b = engine.broker
    engine.position.target = 1660.0

    async def place_exit(symbol, side, qty, ltp):
        raise RuntimeError("connection reset")

    async def place_sl(symbol, side, qty, trigger):
        raise RuntimeError("Groww refused the stop")

    b.place_exit, b.place_sl = place_exit, place_sl
    with pytest.raises(Exception):
        await engine._watch_stop(cfg)
    pos = engine.position
    assert pos is not None and pos.naked_since is not None and not pos.sl_order_id
    assert any(critical and call for _, critical, call in b.alerts)
    # The exit went through after all: Groww shows flat. The no-stop watch books it at Groww's price.
    b.net, b.closed = 0, 1661.0
    pos.protect_at = 0.0
    await engine._watch_stop(cfg)
    assert engine.position is None
    assert _row(pos.trade_id)[:2] == ("SOLD_ON_GROWW", 1661.0)


@pytest.mark.asyncio
async def test_an_unconfirmed_stop_cancel_sends_no_exit_and_retries_once_cancelled(engine):  # noqa: F811
    cfg = _live(engine, net=1, sl_status="TRIGGER_PENDING")
    b = engine.broker

    async def cancel_order(order_id):
        b.events.append(f"CANCEL {order_id}")  # Groww has not answered yet: status stays TRIGGER_PENDING

    b.cancel_order = cancel_order
    await engine._exit_now(cfg, engine.ltp, "COUNTER_CANDLES", gate=False)
    pos = engine.position
    assert pos is not None and pos.sl_order_id == "SL-LIVE" and pos.pending_exit == "COUNTER_CANDLES"
    assert not any(e.startswith("EXIT") for e in b.events)  # not booked as a stop fill, no second sell
    b.sl_status = "CANCELLED"  # the cancel lands
    await engine._watch_stop(cfg)
    assert engine.position is None
    assert b.events[-1] == "EXIT SELL"
    assert not any(critical for _, critical, _ in b.alerts)  # the bot's own cancel is not a "NO STOP" alarm


@pytest.mark.asyncio
async def test_a_halted_or_paused_bot_still_works_its_stops(engine):  # noqa: F811
    from strategy_engine import OpenPosition

    cfg = engine.load_config()
    trade_id = _seed_open_trade()
    engine._focus = "SBIN"
    engine.position = OpenPosition(
        direction="LONG", qty=1, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=98.0,
        sl_order_id="SL1", entry_order_id="E1", entry_time=T0, trade_id=trade_id, mode="PAPER",
    )
    engine._ltps["SBIN"] = 90.0
    engine.status = "HALTED"
    engine.halt_reason = "max_trades_per_day (1) reached"
    await engine._guard_held(cfg, T0)
    assert engine.positions == {}
    assert _row(trade_id)[0] == "ATR_SL_HIT"
    assert engine.status == "HALTED"


@pytest.mark.asyncio
async def test_one_stocks_error_does_not_stop_the_others_square_off(engine):  # noqa: F811
    from strategy_engine import OpenPosition

    b = engine.broker
    for name in ("AAA", "BBB"):
        engine._focus = name
        engine._ltps[name] = 100.0
        engine.position = OpenPosition(
            direction="LONG", qty=1, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=98.0,
            sl_order_id=f"SL-{name}", entry_order_id="E1", entry_time=T0, trade_id=_seed_open_trade(), mode="PAPER",
        )
    real_cancel = b.cancel_order

    async def cancel_order(order_id):
        if order_id == "SL-AAA":
            raise RuntimeError("Groww timed out")
        await real_cancel(order_id)

    b.cancel_order = cancel_order
    await engine.kill("Manual PANIC SQUARE-OFF")
    assert "AAA" in engine.positions and "BBB" not in engine.positions
    assert engine._squareoff_due == "KILL_SWITCH"  # AAA is tried again by the next pass
    b.cancel_order = real_cancel
    engine._squareoff_try_at = 0.0
    await engine._guard_held(engine.load_config(), T0)
    assert engine.positions == {}
    assert engine._squareoff_due == ""


@pytest.mark.asyncio
async def test_a_restart_brings_back_the_live_stop_id(engine):  # noqa: F811
    import database
    from models import TradeLog
    from strategy_engine import StrategyEngine

    trade_id = _seed_open_trade()
    with database.session_factory()() as db:
        row = db.get(TradeLog, trade_id)
        row.mode, row.sl_order_id = "LIVE", "SL-KEPT"
        db.commit()
    fresh = StrategyEngine(broker=engine.broker)
    fresh.restore_open_books()
    (pos,) = fresh.positions.values()
    assert pos.sl_order_id == "SL-KEPT"
    assert pos.plan_stage == 0


@pytest.mark.asyncio
async def test_the_vwap_trail_never_puts_a_stop_above_a_long(engine):  # noqa: F811
    import pandas as pd
    from strategy_engine import OpenPosition

    cfg = engine.load_config()
    cfg.stop_type, cfg.vwap_stop_atr = "VWAP", 0.5
    frame = _tape([100.0] * 30 + [99.0, 98.0, 97.0, 96.0, 96.0])
    frame["volume"] = range(1000, 1000 + 1000 * len(frame), 1000)
    frame["atr_14"] = 0.5
    engine._frames["SBIN"] = frame
    engine._focus = "SBIN"
    engine.ltp = 96.0
    engine.position = OpenPosition(
        direction="LONG", qty=1, entry_price=96.0, ma_cross_price=96.0, atr_at_entry=0.3, sl_trigger=95.55,
        sl_order_id="SL1", entry_order_id="E1", entry_time=T0, trade_id=_seed_open_trade(), mode="PAPER",
        vwap_stop=True,
    )
    cfg.symbol = "SBIN"
    await engine._trail_vwap_stop(cfg)
    assert engine.position.sl_trigger <= engine.ltp
    assert isinstance(frame, pd.DataFrame)


@pytest.mark.asyncio
async def test_a_mode_switch_during_the_pass_sends_no_order(engine):  # noqa: F811
    from strategy_engine import SlCancelFailed

    cfg = engine.load_config()
    cfg.symbol = "SBIN"
    engine._focus = "SBIN"
    engine._frames["SBIN"] = _tape([100.0] * 40)
    engine._ltps["SBIN"] = engine.ltp = 100.0
    engine.broker.mode = "LIVE"  # the switch landed while this pass still reads PAPER settings
    with pytest.raises(SlCancelFailed):
        await engine._open("LONG", 100.0, 1.0, cfg, T0)
    assert engine.broker.events == []


class _YieldingLiveBroker:
    """A LIVE broker stub whose every call yields, like a real HTTP call to Groww."""

    mode = "LIVE"

    def __init__(self, started: asyncio.Event):
        self.events: list[str] = []
        self.status = {"SLC": "TRIGGER_PENDING"}
        self.frames: dict = {}
        self.px: dict = {}
        self.started = started

    def set_mode(self, mode, token=None):
        pass

    async def refresh(self, symbol):
        await asyncio.sleep(0.002)
        return self.px[symbol], self.frames[symbol], "GROWW"

    async def get_order_status(self, order_id):
        if order_id == "SLC":
            self.started.set()  # the bot's pass is now waiting on Groww for CCC's stop
        await asyncio.sleep(0.03)
        return self.status.get(order_id, "OPEN")

    async def read_order(self, order_id):
        await asyncio.sleep(0.001)
        return self.status.get(order_id, "OPEN"), None

    async def cancel_order(self, order_id):
        self.events.append(f"CANCEL {order_id}")
        self.status[order_id] = "CANCELLED"

    async def net_quantity(self, symbol):
        return {"CCC": 10, "BBB": 1000 if any(e.startswith("ENTRY BBB") for e in self.events) else 0}.get(symbol, 0)

    async def place_entry(self, symbol, side, qty, price):
        self.events.append(f"ENTRY {symbol} {side}")
        self.status["E1"] = "EXECUTED"
        return OrderAck("E1", "EXECUTED", price)

    async def place_sl(self, symbol, side, qty, trigger):
        self.events.append(f"SL {symbol} {side}")
        return OrderAck("SLB", "TRIGGER_PENDING")

    async def place_exit(self, symbol, side, qty, price):
        self.events.append(f"EXIT {symbol} {side} {price}")
        return OrderAck("X1", "OPEN")


@pytest.mark.asyncio
async def test_an_order_next_to_the_pass_does_not_move_the_pass_onto_its_stock(engine, monkeypatch):  # noqa: F811
    """A news order on BBB (₹2,000) while the pass waits on Groww for CCC's stop (₹100, target ₹105) used to make
    the pass read 2,000 as CCC's price, call it a target hit and exit BBB's position with CCC's order."""
    import database
    import strategy_engine as se
    from models import BotConfig
    from strategy_engine import OpenPosition

    now = dt.datetime(2026, 9, 29, 11, 0, 30, tzinfo=IST)
    monkeypatch.setattr(se, "market_is_open", lambda now=None: True)
    started = asyncio.Event()
    b = _YieldingLiveBroker(started)
    engine.broker = b
    engine._now = lambda: now
    engine._alert = lambda *a, **k: None
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols, row.symbol, row.trading_mode, row.max_daily_loss = "CCC,BBB", "CCC", "LIVE", 1e9
        db.commit()
    b.frames = {"CCC": _tape([100.0] * 40), "BBB": _tape([2000.0] * 40)}
    b.px = {"CCC": 100.0, "BBB": 2000.0}
    engine._frames.update(b.frames)
    engine._ltps.update(b.px)
    engine._focus = "CCC"
    cfg = engine.load_config()
    trade_id = engine._insert_open_trade(
        cfg=se._cfg_for(cfg, "CCC"), direction="LONG", fill=100.0, cross_price=100.0, atr=1.0, sl=98.0, now=now,
    )
    engine.position = OpenPosition(
        direction="LONG", qty=10, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=98.0,
        sl_order_id="SLC", entry_order_id="E0", entry_time=now - dt.timedelta(minutes=5), trade_id=trade_id,
        mode="LIVE", target=105.0,
    )
    engine.status = "RUNNING"

    async def passes():
        for _ in range(4):
            await engine.tick(now)
            await asyncio.sleep(0.002)

    task = asyncio.create_task(passes())
    await started.wait()
    assert await engine.direct_order("BBB", "BULLISH", why="NEWS") == "opened LONG"
    await task
    assert not any(e.startswith("EXIT") or e.startswith("CANCEL") for e in b.events), b.events
    assert engine.positions["CCC"].sl_order_id == "SLC"
    assert engine.positions["BBB"].sl_order_id == "SLB"


@pytest.mark.asyncio
async def test_a_stop_that_triggered_but_never_filled_is_closed_at_the_market(engine):  # noqa: F811
    """The LIVE stop is a stop-limit 0.2 % through its trigger: a gap through that limit leaves it working, unfilled."""
    cfg = _live(engine, net=1, sl_status="TRIGGERED")
    engine.ltp = 1600.0  # stop 1621, its limit about 1617.8: the price is past it
    await engine._watch_stop(cfg)
    assert engine.position is not None  # one look is not enough: a print can bounce back
    engine.clock = T0 + dt.timedelta(seconds=6)
    await engine._watch_stop(cfg)
    assert engine.position is None
    assert engine.broker.events[:2] == ["CANCEL SL-LIVE", "EXIT SELL"]


@pytest.mark.asyncio
async def test_news_and_force_orders_wait_for_the_0920_buffer(engine, monkeypatch):  # noqa: F811
    import strategy_engine as se
    from strategy_engine import ForceRefused

    monkeypatch.setattr(se, "market_is_open", lambda now=None: True)
    engine.status = "RUNNING"
    engine._now = lambda: dt.datetime(2026, 10, 29, 9, 16, tzinfo=IST)
    with pytest.raises(ForceRefused, match="09:20"):
        await engine.direct_order("SBIN", "BULLISH", why="NEWS")
    with pytest.raises(ForceRefused, match="09:20"):
        await engine.force_order("SBIN")
