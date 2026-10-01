"""Reporting: fill lag is measured, one realized-net number, honest notes (PAPER)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)
DAY = NOW.date().isoformat()


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
    engine._session_date = DAY
    engine._focus = "RELIANCE"
    yield engine
    database.reset_engine()


def _cfg(eng):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "RELIANCE"
        row.trade_symbols = "RELIANCE"
        row.qty = 10
        db.commit()
    return eng.load_config()


@pytest.mark.asyncio
async def test_paper_fill_is_the_live_price_so_fill_lag_shows(eng):
    cfg = _cfg(eng)
    eng._ltps["RELIANCE"] = 1003.4
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    row = eng.trades()[0]
    assert row["ma_cross_price"] == pytest.approx(1000.0)
    assert row["entry_price"] == pytest.approx(1003.4)
    assert row["entry_price"] != row["ma_cross_price"]
    assert row["fill_lag_points"] == pytest.approx(3.4)


@pytest.mark.asyncio
async def test_a_reverse_exits_at_the_live_price_too(eng):
    cfg = _cfg(eng)
    eng._ltps["RELIANCE"] = 1000.0
    await eng._apply_locked(signal="BULLISH", cross_price=1000.0, atr=2.0, adx_blocks_entry=False, cfg=cfg, now=NOW)
    eng._ltps["RELIANCE"] = 1010.0
    await eng._apply_locked(signal="BEARISH", cross_price=1012.0, atr=2.0, adx_blocks_entry=False, cfg=cfg, now=NOW)
    closed = [r for r in eng.trades() if r["exit_price"] is not None][0]
    assert closed["exit_price"] == pytest.approx(1010.0)
    opened = [r for r in eng.trades() if r["exit_price"] is None][0]
    assert opened["ma_cross_price"] == pytest.approx(1012.0)
    assert opened["entry_price"] == pytest.approx(1010.0)


def _closed_row(net: float, mode: str = "PAPER", date: str = DAY) -> None:
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        db.add(
            TradeLog(
                date=date, symbol="X", direction="LONG", qty=1,
                entry_time=dt.datetime(2026, 9, 29, 10, 0), entry_price=100, ma_cross_price=100,
                atr_at_entry=1, sl_trigger_price=99, exit_time=dt.datetime(2026, 9, 29, 10, 5),
                exit_price=100 + net, exit_reason="MA_CROSS", gross_pnl=net, brokerage_and_taxes=0,
                net_pnl=net, mode=mode,
            )
        )
        db.commit()


@pytest.mark.asyncio
async def test_realized_net_matches_the_kpi_net_after_a_restart(eng):
    _cfg(eng)
    _closed_row(-120.0)
    _closed_row(45.5)
    _closed_row(999.0, mode="LIVE")  # other book
    _closed_row(777.0, date="2026-09-28")  # other day
    eng.restore_trades_today()
    snap = eng.snapshot()
    assert eng.realized_net == pytest.approx(-74.5)
    assert snap["realized_net_pnl"] == pytest.approx(snap["kpis"]["net"]) == pytest.approx(-74.5)


@pytest.mark.asyncio
async def test_realized_net_matches_the_kpi_after_each_close(eng):
    cfg = _cfg(eng)
    eng.status = "RUNNING"
    eng._ltps["RELIANCE"] = 1000.0
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    eng.ltp = 996.0
    eng._ltps["RELIANCE"] = 996.0
    await eng._watch_stop(cfg)
    snap = eng.snapshot()
    assert snap["kpis"]["trades"] == 1
    assert eng.realized_net == pytest.approx(snap["kpis"]["net"])
    assert snap["realized_net_pnl"] == pytest.approx(snap["kpis"]["net"])
    assert eng.realized_net < 0


@pytest.mark.asyncio
async def test_a_stopped_out_stock_does_not_keep_saying_holding(eng):
    cfg = _cfg(eng)
    eng.status = "RUNNING"
    eng._ltps["RELIANCE"] = 1000.0
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    eng._signals["RELIANCE"] = "RELIANCE holding"
    eng.ltp = 996.0
    eng._ltps["RELIANCE"] = 996.0
    await eng._watch_stop(cfg)
    book = next(b for b in eng.snapshot()["books"] if b["symbol"] == "RELIANCE")
    assert book["direction"] == "FLAT"
    assert "holding" not in book["note"]
    assert book["note"] == "RELIANCE flat — ATR stop"


@pytest.mark.asyncio
async def test_kill_and_close_also_clear_the_holding_note(eng):
    cfg = _cfg(eng)
    eng._ltps["RELIANCE"] = 1000.0
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    eng._signals["RELIANCE"] = "RELIANCE holding"
    await eng.kill("Manual PANIC SQUARE-OFF")
    book = next(b for b in eng.snapshot()["books"] if b["symbol"] == "RELIANCE")
    assert book["note"] == "RELIANCE flat — panic square-off"


def test_a_flat_book_never_shows_a_holding_note():
    from strategy_engine import _book_note

    assert _book_note("ABC", None, "ABC holding") == "ABC flat"
    assert _book_note("ABC", None, "ABC no order — waiting") == "ABC no order — waiting"
