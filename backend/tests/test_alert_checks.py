"""Telegram text: the bot's own entry checks, refused crosses, and day P&L.

Every engine here runs in PAPER on a fake broker; nothing reaches Groww.
"""
from __future__ import annotations

import datetime as dt

import pytest

from groww_client import IST
from indicators import enrich
from strategy_engine import (
    _entry_block,
    close_alert,
    entry_checks,
    fill_alert,
    refused_alert,
    upcoming_entry_alert,
)
from tests.test_chart_filters import _cfg, _frame
from tests.test_sma_atr_terminal import _FakeBroker, _instant, _seed_open_trade, _tape  # noqa: F401


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database

    database.reset_engine()
    database.init_db()
    from strategy_engine import StrategyEngine

    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    return eng


def test_checks_agree_with_the_bots_own_decision():
    frame = _frame()
    for cut in range(60, len(frame), 41):
        part = frame.iloc[:cut]
        for side in ("LONG", "SHORT"):
            cfg = _cfg(use_vwap=True, use_rsi=True, use_density=True, use_volume=True)
            lines = entry_checks(part, side, cfg)
            assert len(lines) == 4
            refused = any(line.startswith("❌") for line in lines)
            assert refused == (_entry_block(part, side, cfg) is not None)


def test_checks_list_only_checked_filters_and_adx():
    frame = _frame()
    assert entry_checks(frame, "LONG", _cfg()) == ["No filters on — the cross alone places the order"]
    lines = entry_checks(frame, "LONG", _cfg(use_rsi=True, use_adx_filter=True, adx_threshold=99))
    assert len(lines) == 2
    assert lines[0].split()[1] == "RSI"
    assert lines[1].startswith("❌ ADX") and "needs 99" in lines[1]


def test_heads_up_says_whether_the_cross_would_order():
    text = upcoming_entry_alert(
        mode="PAPER", symbol="TCS", side="BULLISH", minutes=2, gap_pct=-0.05,
        checks=["✅ VWAP: 101.00 above 100.00", "❌ RSI 72.0 is outside 40–70"], trades=(2, 40),
    )
    assert "❌ RSI 72.0" in text and "no BUY — a check refuses it" in text
    assert "Trades today 2/40" in text
    ok = upcoming_entry_alert(
        mode="PAPER", symbol="TCS", side="BULLISH", minutes=2, gap_pct=-0.05, checks=["✅ VWAP: 101.00 above 100.00"]
    )
    assert "BUY would be placed" in ok


def test_close_and_fill_text_carry_the_day_total():
    when = dt.datetime(2026, 10, 5, 11, 0)
    day = {"net": 250.5, "trades": 3, "open_net": -40.0, "held": 1}
    closed = close_alert(
        direction="LONG", symbol="TCS", exit_price=101, reason="MA_CROSS", gross=20, net=12.5, when=when, day=day
    )
    assert "This trade P&L +20.00  net +12.50" in closed
    assert "Today net +250.50 (3 closed trades)" in closed
    assert "Open 1 other stock -40.00 · total +210.50" in closed
    opened = fill_alert(mode="PAPER", direction="LONG", symbol="TCS", qty=1, fill=100, stop=None, when=when, day=day)
    assert "Today net +250.50" in opened
    # Without totals the text is as before.
    assert "Today" not in fill_alert(mode="PAPER", direction="LONG", symbol="TCS", qty=1, fill=100, stop=None, when=when)


def test_refused_text_names_the_reason_and_checks():
    text = refused_alert(
        mode="PAPER", symbol="TCS", signal="BULLISH", reason="VWAP: 99.00 is below 100.00", closed=False,
        checks=["❌ VWAP: 99.00 is below 100.00"], price=99.0, when=dt.datetime(2026, 10, 5, 10, 35),
    )
    assert "TCS BULLISH cross — no BUY placed at 99.00" in text
    assert "Why: VWAP" in text and "❌ VWAP" in text


@pytest.mark.asyncio
async def test_a_refused_cross_sends_one_telegram_and_no_order(engine, monkeypatch):
    from strategy_engine import OpenPosition

    notes: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", notes.append)
    cfg = engine.load_config()
    cfg.use_adx_filter = False
    cfg.use_vwap = True
    frame = enrich(_tape([200.0] * 24 + [100.0, 100.0]))
    now = dt.datetime(2026, 9, 30, 11, 0, tzinfo=IST)
    result = await engine.apply_signal("BULLISH", frame, cfg, now)
    assert "ignored" in result
    assert engine.broker.events == []
    assert len(notes) == 1 and "PalTra no order" in notes[0] and "❌ VWAP" in notes[0]

    # Closing on the cross while the reverse is refused: the close alert and the refusal.
    notes.clear()
    engine.position = OpenPosition(
        direction="SHORT", qty=1, entry_price=100, ma_cross_price=100, atr_at_entry=1, sl_trigger=102,
        sl_order_id="", entry_order_id="E", entry_time=now, trade_id=_seed_open_trade(), mode="PAPER",
    )
    result = await engine.apply_signal("BULLISH", frame, cfg, now)
    assert "closed on BULLISH" in result
    assert any("This trade P&L" in n and "Today net" in n for n in notes)
    assert any("no reverse BUY" in n for n in notes)

    # An order that goes through sends no refusal.
    notes.clear()
    cfg.use_vwap = False
    assert "opened LONG" in await engine.apply_signal("BULLISH", frame, cfg, now)
    assert not any("PalTra no order" in n for n in notes)


@pytest.mark.asyncio
async def test_state_has_pnl_for_every_held_stock(engine):
    from strategy_engine import OpenPosition

    cfg = engine.load_config()
    cfg.trade_symbols = "TCS,INFY"
    cfg.symbol = "TCS"
    engine._cfg_cache = cfg
    now = dt.datetime(2026, 9, 30, 11, 0, tzinfo=IST)
    engine.positions["INFY"] = OpenPosition(
        direction="LONG", qty=10, entry_price=100, ma_cross_price=100, atr_at_entry=1, sl_trigger=98,
        sl_order_id="", entry_order_id="E", entry_time=now, trade_id=_seed_open_trade(), mode="PAPER",
    )
    engine._ltps["INFY"] = 105.0
    engine._ltps["TCS"] = 50.0
    state = engine.snapshot()
    books = {b["symbol"]: b for b in state["books"]}
    assert books["TCS"]["open_net"] is None and books["TCS"]["day_net"] == 0
    # INFY is not on the chart, and its P&L is still there.
    assert books["INFY"]["open_net"] == pytest.approx(state["open_net_total"])
    assert 0 < books["INFY"]["open_net"] < 50  # +₹50 gross less charges
    assert state["unrealized_net_pnl"] == 0.0  # the chart's stock is flat
