"""Staged stop plan (stop_plan.py): ATR stop → breakeven at +1.5R → Supertrend trail from +2R. PAPER, fake broker."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import stop_plan
from tests.test_sma_atr_terminal import IST, _seed_open_trade, engine  # noqa: F401  (engine is a fixture)
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)
from tests.test_slope_exit import DAY, _peak_frame


def _cfg(**over):
    return type("C", (), over)()


def test_stages_and_breakeven():
    cfg = _cfg(plan_be_r=1.5, plan_trail_r=2.0)
    assert stop_plan.risk(100, 95) == 5 and stop_plan.risk(100, 100) is None
    assert stop_plan.stage("LONG", 100, 107.4, 5, cfg) == 0
    assert stop_plan.stage("LONG", 100, 107.5, 5, cfg) == 1
    assert stop_plan.stage("LONG", 100, 110.0, 5, cfg) == 2
    assert stop_plan.stage("SHORT", 100, 92.5, 5, cfg) == 1 and stop_plan.stage("SHORT", 100, 107.5, 5, cfg) == 0
    from charges import calculate_charges

    be = stop_plan.breakeven("LONG", 1670.7, 1)
    charges = calculate_charges(1670.7, 1670.7, 1)["total_charges"]
    assert charges > 0 and be == pytest.approx(1670.7 + charges)  # entry plus the round-trip charges per share
    assert stop_plan.breakeven("SHORT", 1670.7, 1) < 1670.7
    assert stop_plan.trail_r(_cfg(plan_be_r=3, plan_trail_r=2)) == 3  # the trail never starts before breakeven
    assert stop_plan.be_r(_cfg()) == 1.5 and stop_plan.trail_r(_cfg()) == 2.0


def _plan(engine, monkeypatch, line=None):  # noqa: F811
    from strategy_engine import OpenPosition

    import strategy_engine as se

    engine.status = "RUNNING"
    engine._now = lambda: dt.datetime(2026, 10, 30, 11, 42, tzinfo=IST)
    cfg = engine.load_config()
    cfg.stop_plan, cfg.plan_be_r, cfg.plan_trail_r = True, 1.5, 2.0
    symbol = (cfg.symbol or "").upper()
    engine.position = OpenPosition(
        direction="LONG", qty=1, entry_price=100.0, ma_cross_price=100, atr_at_entry=2, sl_trigger=95.0,
        sl_order_id="PAPERSL1", entry_order_id="E", entry_time=dt.datetime(2026, 10, 30, 10, 0, tzinfo=IST),
        trade_id=_seed_open_trade(), mode="PAPER", plan_r=5.0, high=100.0, low=100.0,
    )
    engine._frames[symbol] = pd.DataFrame({"ts": [1, 2, 3], "close": [100.0, 101.0, 102.0]})
    lines = {"v": line}
    monkeypatch.setattr(se.supertrend, "stop_level", lambda closed, direction, c: lines["v"])
    return cfg, lines


def _log(engine):
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        return db.get(TradeLog, engine.position.trade_id).stop_log


@pytest.mark.asyncio
async def test_plan_moves_to_breakeven_then_trails_the_supertrend_line(engine, monkeypatch):  # noqa: F811
    cfg, lines = _plan(engine, monkeypatch, line=104.0)
    pos = engine.position
    pos.high, engine.ltp = 107.0, 106.5  # +1.4R: the ATR stop stays
    await engine._trail_stop_plan(cfg)
    assert pos.sl_trigger == 95.0 and pos.plan_stage == 0
    pos.high, engine.ltp = 107.5, 107.0  # +1.5R: breakeven plus charges
    await engine._trail_stop_plan(cfg)
    assert 100 < pos.sl_trigger < 101 and pos.plan_stage == 1
    assert "11:42 stop → breakeven (+1.5R)" in _log(engine)
    pos.high, engine.ltp = 110.0, 109.5  # +2R: the Supertrend line
    await engine._trail_stop_plan(cfg)
    assert pos.sl_trigger == 104.0 and pos.plan_stage == 2
    assert "stop → Supertrend trail (+2R) at 104.00" in _log(engine)
    # A lower line on the next candle never loosens the stop; a higher one tightens it.
    engine._frames[(cfg.symbol or "").upper()] = pd.DataFrame({"ts": [2, 3, 4], "close": [101.0, 102.0, 103.0]})
    lines["v"] = 102.0
    await engine._trail_stop_plan(cfg)
    assert pos.sl_trigger == 104.0
    engine._frames[(cfg.symbol or "").upper()] = pd.DataFrame({"ts": [3, 4, 5], "close": [102.0, 103.0, 106.0]})
    lines["v"] = 105.5
    await engine._trail_stop_plan(cfg)
    assert pos.sl_trigger == 105.5
    # The moved stop is hit: the plan's own exit reason.
    engine.ltp = 105.4
    await engine._watch_stop(cfg)
    assert engine.position is None and engine.last_signal == "stop plan stop hit — flat"


@pytest.mark.asyncio
async def test_price_already_back_through_breakeven_closes_at_once(engine, monkeypatch):  # noqa: F811
    cfg, _ = _plan(engine, monkeypatch)
    engine.position.high, engine.ltp = 108.0, 99.9  # reached +1.5R, already back under the entry
    await engine._trail_stop_plan(cfg)
    assert engine.position is None and engine.last_signal == "stop plan level reached — flat"


@pytest.mark.asyncio
async def test_plan_off_or_other_stop_types_do_nothing(engine, monkeypatch):  # noqa: F811
    cfg, _ = _plan(engine, monkeypatch)
    cfg.stop_plan = False
    engine.position.high, engine.ltp = 110.0, 109.5
    await engine._trail_stop_plan(cfg)
    assert engine.position.sl_trigger == 95.0
    cfg.stop_plan = True
    engine.position.plan_r = None  # opened with the TSL / Supertrend / gap stop: no plan for it
    await engine._trail_stop_plan(cfg)
    assert engine.position.sl_trigger == 95.0


@pytest.mark.asyncio
async def test_replay_locks_in_the_run_before_the_cross(db):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_gap_mode import _settings

    async def long_trade(**over):
        trades = await replay_symbol(_peak_frame(1), "SIMV", [DAY], _settings(db, use_stop=True, atr_multiplier=2.5, **over))
        return next(t for t in trades if t["direction"] == "LONG" and t["entry_time"] >= dt.datetime(2026, 9, 22, 10, 15))

    plain = await long_trade()
    plan = await long_trade(stop_plan=True)
    assert plan["entry_time"] == plain["entry_time"]
    assert plan["exit_reason"] == stop_plan.HIT_REASON and plain["exit_reason"] == "MA_CROSS"
    assert plan["exit_price"] > plain["exit_price"] > plan["entry_price"]


def test_api_saves_the_stop_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"stop_plan", "plan_be_r", "plan_trail_r"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["stop_plan"] is False and got["plan_be_r"] == 1.5 and got["plan_trail_r"] == 2.0
        ok = client.put("/api/config", json={"stop_plan": True, "plan_be_r": 1.5, "plan_trail_r": 2.0})
        assert ok.status_code == 200 and ok.json()["stop_plan"] is True
        assert client.put("/api/config", json={"plan_be_r": 0}).status_code == 422
    database.reset_engine()
