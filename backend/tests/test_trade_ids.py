"""Trade ids per book (N- NSE live, P- simulation, R<run>- replay) and exit markers on the chart.

PAPER only: LIVE rows are written straight to the DB, no order is ever sent.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from zoneinfo import ZoneInfo

import pytest

from models import trade_ref

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)


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
    yield engine
    database.reset_engine()


def _cfg(eng):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "RELIANCE"
        row.trade_symbols = "RELIANCE"
        row.qty = 1
        db.commit()
    return eng.load_config()


def _insert(eng, cfg, mode="PAPER", run_id=None, minute=0):
    eng.run_id = run_id
    cfg.trading_mode = mode
    return eng._insert_open_trade(
        cfg=cfg, direction="LONG", fill=1000.0, cross_price=1000.0, atr=2.0, sl=996.0,
        now=NOW + dt.timedelta(minutes=minute),
    )


def test_trade_ref_formats():
    assert trade_ref("LIVE", None, 12) == "N-12"
    assert trade_ref("PAPER", None, 3) == "P-3"
    assert trade_ref(None, None, 3) == "P-3"
    assert trade_ref("REPLAY", 7, 1) == "R7-1"
    assert trade_ref("REPLAY", None, 4) == "R-4"
    assert trade_ref("PAPER", None, None, 55) == "#55"


def test_each_book_counts_from_one(eng):
    import database
    from models import TradeLog

    cfg = _cfg(eng)
    _insert(eng, cfg, "PAPER")
    _insert(eng, cfg, "REPLAY", run_id=7)
    _insert(eng, cfg, "PAPER")
    _insert(eng, cfg, "LIVE")
    _insert(eng, cfg, "REPLAY", run_id=7)
    _insert(eng, cfg, "REPLAY", run_id=8)
    with database.session_factory()() as db:
        refs = [trade_ref(r.mode, r.run_id, r.book_seq, r.id) for r in db.query(TradeLog).order_by(TradeLog.id)]
    assert refs == ["P-1", "R7-1", "P-2", "N-1", "R7-2", "R8-1"]
    rows = eng.trades()
    assert {r["trade_ref"] for r in rows} >= {"P-1", "P-2"}


def test_older_trades_are_numbered_on_startup(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{path}")
    import database

    database.reset_engine()
    database.init_db()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE trade_log DROP COLUMN book_seq")
    for mode, run in [("PAPER", None), ("REPLAY", 3), ("PAPER", None), ("LIVE", None), ("REPLAY", 3)]:
        con.execute(
            "INSERT INTO trade_log (date, symbol, direction, qty, entry_time, entry_price, ma_cross_price,"
            " atr_at_entry, sl_trigger_price, mode, stop_active, run_id) VALUES ('2026-09-29', 'RELIANCE',"
            " 'LONG', 1, '2026-09-29 10:30:00', 1000, 1000, 2, 996, ?, 1, ?)",
            (mode, run),
        )
    con.commit()
    con.close()
    database.reset_engine()
    database.init_db()
    con = sqlite3.connect(path)
    got = [trade_ref(m, r, s) for m, r, s in con.execute("SELECT mode, run_id, book_seq FROM trade_log ORDER BY id")]
    con.close()
    database.reset_engine()
    assert got == ["P-1", "R3-1", "P-2", "N-1", "R3-2"]


def test_chart_shows_entry_and_exit_with_the_trade_id(eng):
    cfg = _cfg(eng)
    first = _insert(eng, cfg, "PAPER")
    eng._finalize_trade(first, 1004.0, "MA_CROSS", NOW + dt.timedelta(minutes=5),
                       {"gross_pnl": 4.0, "total_charges": 1.0, "net_pnl": 3.0})
    _insert(eng, cfg, "PAPER", minute=10)  # still open
    marks = eng.chart_payload()["markers"]
    entries = [m for m in marks if m["kind"] == "ENTRY"]
    exits = [m for m in marks if m["kind"] == "EXIT"]
    assert [m["trade_ref"] for m in entries] == ["P-1", "P-2"]
    assert [m["open"] for m in entries] == [False, True]
    assert len(exits) == 1
    assert exits[0]["trade_ref"] == "P-1" and exits[0]["price"] == 1004.0 and exits[0]["reason"] == "MA_CROSS"
    assert exits[0]["time"] >= entries[0]["time"]
