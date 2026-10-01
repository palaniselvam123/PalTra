"""The kill switch closes each stock at that stock's own price (PAPER only).

29 Sept: every open stock was closed at 875.25. Every practice tape started
at 875, so after a restart with no Groww quote all stocks were priced there.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
EVENING = dt.datetime(2026, 9, 29, 19, 0, tzinfo=IST)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    # No Groww session at all: every quote comes from the practice tape.
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: False)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: False)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: EVENING)
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    engine = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    yield engine
    database.reset_engine()


def _seed(symbol: str, direction: str, entry: float) -> None:
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        db.add(
            TradeLog(
                date="2026-09-29",
                symbol=symbol,
                direction=direction,
                qty=10,
                entry_time=dt.datetime(2026, 9, 29, 14, 0),
                entry_price=entry,
                ma_cross_price=entry,
                atr_at_entry=1.0,
                sl_trigger_price=entry * 0.99 if direction == "LONG" else entry * 1.01,
                mode="PAPER",
            )
        )
        db.commit()


@pytest.mark.asyncio
async def test_kill_after_a_restart_closes_each_stock_near_its_own_price(eng):
    _seed("RELIANCE", "LONG", 2400.0)
    _seed("IRFC", "SHORT", 120.0)
    _seed("TCS", "LONG", 3100.0)
    eng.restore_open_books()
    await eng.tick(EVENING)  # loads a quote for every held stock

    await eng.kill("Manual PANIC SQUARE-OFF")

    exits = {row["symbol"]: row["exit_price"] for row in eng.trades()}
    assert exits["RELIANCE"] == pytest.approx(2400.0, rel=0.01)
    assert exits["IRFC"] == pytest.approx(120.0, rel=0.01)
    assert exits["TCS"] == pytest.approx(3100.0, rel=0.01)
    assert len(set(exits.values())) == 3
    assert not any(abs(px - 875.0) < 5 for px in exits.values())
    assert eng.positions == {}


@pytest.mark.asyncio
async def test_kill_uses_each_stock_quote_not_the_chart_stock(eng):
    from strategy_engine import OpenPosition

    for symbol, entry in (("RELIANCE", 2400.0), ("IRFC", 120.0)):
        eng.positions[symbol] = OpenPosition(
            direction="LONG",
            qty=10,
            entry_price=entry,
            ma_cross_price=entry,
            atr_at_entry=1.0,
            sl_trigger=entry - 5,
            sl_order_id="",
            entry_order_id="P",
            entry_time=EVENING,
            trade_id=0,
            mode="PAPER",
        )
    # The chart is on a third stock priced at 875.25.
    eng._focus = "KIRLOSFER"
    eng.ltp = 875.25
    eng._ltps.update({"KIRLOSFER": 875.25, "RELIANCE": 2410.0, "IRFC": 121.5})

    closed: dict[str, float] = {}

    async def record(pos, exit_price, reason, now, cfg):
        closed[cfg.symbol] = exit_price

    eng._close_position = record  # type: ignore[method-assign]
    await eng.kill("Manual PANIC SQUARE-OFF")
    assert closed == {"RELIANCE": pytest.approx(2410.0), "IRFC": pytest.approx(121.5)}
    # The screen is left on the chart stock.
    assert eng._focus == "KIRLOSFER" and eng.ltp == pytest.approx(875.25)


def test_a_demo_tape_is_replaced_once_the_stock_price_is_known():
    import asyncio

    from groww_client import GrowwClient

    client = GrowwClient(mode="PAPER")
    client.token = ""
    ltp, _frame, source = asyncio.run(client.refresh("ZZNEW"))
    assert source == "SIMULATOR" and abs(ltp - 875.0) < 5
    client.anchor_price("ZZNEW", 42.0)
    ltp, _frame, _source = asyncio.run(client.refresh("ZZNEW"))
    assert ltp == pytest.approx(42.0, rel=0.01)
