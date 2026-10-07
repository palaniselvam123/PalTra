"""Flip strategy: a buy signal places a sell, a sell signal a buy; every condition stays the same.

Replays on local fills (no Groww order path). PAPER only.
"""
from __future__ import annotations

import pytest

from tests.test_gap_mode import _trades, db  # noqa: F401


def _opposite(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


@pytest.mark.asyncio
async def test_same_entries_and_exits_on_the_other_side(db):  # noqa: F811
    plain = await _trades(db)
    flipped = await _trades(db, flip_orders=True)
    assert plain and len(flipped) == len(plain)
    for a, b in zip(plain, flipped):
        assert b["entry_time"] == a["entry_time"] and b["exit_time"] == a["exit_time"]
        assert b["direction"] == _opposite(a["direction"])
        assert b["exit_reason"] == a["exit_reason"]
        assert b["gross"] == pytest.approx(-a["gross"], abs=0.01)


@pytest.mark.asyncio
async def test_gap_mode_flips_too_and_exits_when_the_signal_fades(db):  # noqa: F811
    gap = dict(use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.02, gap_entry_short=-0.05, gap_exit_short=-0.02)
    plain = await _trades(db, **gap)
    flipped = await _trades(db, flip_orders=True, **gap)
    assert plain and len(flipped) == len(plain)
    for a, b in zip(plain, flipped):
        assert (b["entry_time"], b["exit_time"], b["exit_reason"]) == (a["entry_time"], a["exit_time"], a["exit_reason"])
        assert b["direction"] == _opposite(a["direction"])
    assert any(t["exit_reason"] == "GAP_FADE" for t in flipped)


@pytest.mark.asyncio
async def test_the_stop_guards_the_real_flipped_position(db):  # noqa: F811
    trades = await _trades(db, flip_orders=True, use_stop=True)
    from models import TradeLog

    with db.session_factory()() as s:
        rows = s.query(TradeLog).filter(TradeLog.mode == "REPLAY").all()
    assert rows and all(r.flipped for r in rows)
    for r in rows:
        if r.direction == "SHORT":
            assert r.sl_trigger_price > r.entry_price
        else:
            assert r.sl_trigger_price < r.entry_price
    assert trades


def test_restart_keeps_a_flipped_position_flipped(db):  # noqa: F811
    import datetime as dt

    from models import TradeLog
    from strategy_engine import StrategyEngine

    with db.session_factory()() as s:
        s.add(TradeLog(date=dt.date.today().isoformat(), symbol="TCS", direction="SHORT", qty=1,
                       entry_time=dt.datetime.now(), entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0,
                       sl_trigger_price=101.5, mode="PAPER", flipped=True))
        s.commit()
    eng = StrategyEngine()
    eng.restore_open_books()
    pos = eng.positions["TCS"]
    assert pos.direction == "SHORT" and pos.flipped and pos.signal_direction == "LONG"


def test_api_saves_the_switch_and_a_stock_can_flip_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/config").json()["flip_orders"] is False  # off by default
        assert client.get("/api/state").json()["flip_orders"] is False
        ok = client.put("/api/config", json={"flip_orders": True})
        assert ok.status_code == 200 and ok.json()["flip_orders"] is True
        own = client.put("/api/config/stock/TCS", json={"flip_orders": False})
        assert own.status_code == 200 and own.json()["own"] == {"flip_orders": False}
        client.put("/api/config", json={"flip_orders": False})
    database.reset_engine()


@pytest.mark.asyncio
async def test_one_stock_can_flip_on_its_own(db):  # noqa: F811
    import json

    plain = await _trades(db)
    own = await _trades(db, stock_settings=json.dumps({"SIMG": {"flip_orders": True}}))
    assert [t["direction"] for t in own] == [_opposite(t["direction"]) for t in plain]
