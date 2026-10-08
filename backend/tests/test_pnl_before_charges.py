"""The screens show P&L before charges; the server sends those figures next to the net ones.

Display only: the net figures (and the daily loss limit, which reads them) are unchanged.
"""
from __future__ import annotations

import datetime as dt

from tests.test_research_desk import db  # noqa: F401


def _row(database, cid: int, **values):
    from models import BotConfig

    with database.session_factory()() as s:
        row = s.get(BotConfig, cid)
        for key, value in values.items():
            setattr(row, key, value)
        s.commit()


def test_books_markers_and_kpis_carry_pnl_before_charges(db):  # noqa: F811
    from groww_client import GrowwClient
    from models import TradeLog
    from strategy_engine import StrategyEngine

    eng = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    eng.load_config()
    _row(db, 1, symbol="TCS", trade_symbols="TCS")
    eng.load_config()
    day = eng._session_date
    now = dt.datetime.now()
    with db.session_factory()() as s:
        # A winner that loses to its charges, and a plain loser.
        s.add(TradeLog(date=day, symbol="TCS", direction="LONG", qty=10, entry_time=now, entry_price=100.0,
                       ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=98.0, exit_time=now, exit_price=101.0,
                       exit_reason="MA_CROSS", gross_pnl=10.0, brokerage_and_taxes=25.0, net_pnl=-15.0, mode="PAPER", bot=1))
        s.add(TradeLog(date=day, symbol="TCS", direction="SHORT", qty=10, entry_time=now, entry_price=100.0,
                       ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=102.0, exit_time=now, exit_price=102.0,
                       exit_reason="ATR_SL_HIT", gross_pnl=-20.0, brokerage_and_taxes=25.0, net_pnl=-45.0, mode="PAPER", bot=1))
        s.commit()

    snap = eng.snapshot()
    book = next(b for b in snap["books"] if b["symbol"] == "TCS")
    assert book["closed_net"] == -60.0 and book["closed_gross"] == -10.0
    assert book["day_net"] == -60.0 and book["day_gross"] == -10.0
    assert book["open_gross"] is None and snap["open_gross_total"] == 0.0
    # One win before charges, none after them; the net figures are untouched.
    assert snap["kpis"]["gross_wins"] == 1 and snap["kpis"]["wins"] == 0
    assert snap["kpis"]["actual_gross"] == -10.0 and snap["kpis"]["net"] == -60.0 and snap["realized_net_pnl"] == -60.0

    markers = eng.chart_payload()["markers"]
    exits = sorted((m["gross_pnl"], m["net_pnl"]) for m in markers if m["kind"] == "EXIT")
    assert exits == [(-20.0, -45.0), (10.0, -15.0)]


def test_bots_list_has_todays_pnl_before_charges(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        bots = client.get("/api/bots").json()
        assert all(b["gross_today"] == 0.0 and b["net_today"] == 0.0 for b in bots)
    database.reset_engine()
