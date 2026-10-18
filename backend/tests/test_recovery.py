"""Slow SMA exit, recovery hold and the Bot 2 handoff (recovery.py).

PAPER only: the replay engine fills locally (no Groww order path) and never hands off.
"""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

import recovery
from tests.test_gap_mode import _day, _settings, db  # noqa: F401  (db is a fixture)

DAY = dt.date(2026, 9, 22)
START = dt.datetime(2026, 9, 22, 9, 55)


def _frame(tail) -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        if i < 40:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        return tail(i, p)

    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(DAY, path))


def _recovers(i, p):  # a short rise, a drop through the entry (bearish cross at a loss), then a rebound
    if i < 48:
        return p + 0.3
    if i < 58:
        return p - 0.4
    if i < 66:
        return p + 0.35
    return p + (0.02 if i % 2 else -0.02)


def _crashes(i, p):  # the same start, then the drop keeps going
    if i < 48:
        return p + 0.3
    if i < 100:
        return p - 0.4
    return p + (0.02 if i % 2 else -0.02)


def _profits(i, p):  # a long run up, then a fall: the bearish cross comes while the long is in profit
    if i < 70:
        return p + 0.3
    if i < 90:
        return p - 0.3
    return p + (0.02 if i % 2 else -0.02)


async def _trades(database, tail, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(_frame(tail), "SIMR", [DAY], _settings(database, **over))
    return [t for t in trades if t["entry_time"] >= START]


HOLD = {"recovery_hold": True, "recovery_atr_mult": 20, "recovery_tolerance_pct": 0.01}


def test_judge_rules():
    cfg = type("C", (), {"recovery_tolerance_pct": 0.15, "recovery_emergency_mult": 1.5,
                         "recovery_atr_mult": 2.5, "recovery_max_candles": 6})()
    bar = {"close": 99.0, "high": 99.2, "low": 98.8, "sma_21": 100.5, "atr_14": 2.0}
    # LONG from 100, loss 1.0 at the cross.
    assert recovery.judge("LONG", 100.0, bar, 1.0, 1, cfg)[0] == "hold"
    assert recovery.judge("LONG", 100.0, {**bar, "close": 99.9}, 1.0, 1, cfg)[0] == "target"  # within 0.15 %
    assert recovery.judge("LONG", 100.0, {**bar, "high": 100.6}, 1.0, 1, cfg)[0] == "target"  # touched the slow SMA
    assert recovery.judge("LONG", 100.0, {**bar, "close": 98.5, "low": 98.4}, 1.0, 1, cfg)[0] == "abort"  # 1.5x
    assert recovery.judge("LONG", 100.0, {**bar, "atr_14": 0.3}, 1.0, 1, cfg)[0] == "abort"  # 2.5x ATR
    assert recovery.judge("LONG", 100.0, bar, 1.0, 6, cfg)[0] == "abort"  # out of candles
    # SHORT mirrors it: a loss is the price above the entry.
    short_bar = {"close": 101.0, "high": 101.2, "low": 100.8, "sma_21": 99.5, "atr_14": 2.0}
    assert recovery.judge("SHORT", 100.0, short_bar, 1.0, 1, cfg)[0] == "hold"
    assert recovery.judge("SHORT", 100.0, {**short_bar, "low": 99.4}, 1.0, 1, cfg)[0] == "target"
    assert recovery.loss_per_share("LONG", 100.0, 99.0) == 1.0 and recovery.loss_per_share("SHORT", 100.0, 99.0) == -1.0
    assert recovery.slow_breach("LONG", 99.0, 100.0) and not recovery.slow_breach("LONG", 100.0, 100.0)
    assert recovery.slow_breach("SHORT", 101.0, 100.0) and not recovery.slow_breach("SHORT", 99.0, 100.0)


@pytest.mark.asyncio
async def test_a_losing_trade_is_held_through_the_cross_and_closes_on_its_recovery(db):  # noqa: F811
    plain = await _trades(db, _recovers)
    held = await _trades(db, _recovers, **HOLD, recovery_max_candles=12)
    assert plain[0]["exit_reason"] == "MA_CROSS" and plain[0]["exit_price"] < plain[0]["entry_price"]
    first = held[0]
    assert first["direction"] == "LONG" and first["exit_reason"] == recovery.TARGET_EXIT
    assert first["exit_time"] > plain[0]["exit_time"]  # held past the cross
    assert first["exit_price"] > plain[0]["exit_price"]  # and closed better than the cross did


@pytest.mark.asyncio
async def test_no_recovery_within_the_candles_is_an_emergency_exit_and_blocks_the_stock_today(db):  # noqa: F811
    plain = await _trades(db, _crashes)
    held = await _trades(db, _crashes, **HOLD, recovery_max_candles=2)
    assert len(plain) > 1  # plain mode reverses and keeps trading the stock
    assert len(held) == 1 and held[0]["exit_reason"] == recovery.ABORT_EXIT


@pytest.mark.asyncio
async def test_the_loss_floor_ends_the_hold(db):  # noqa: F811
    held = await _trades(db, _crashes, recovery_hold=True, recovery_tolerance_pct=0.01, recovery_max_candles=60)
    assert held[0]["exit_reason"] == recovery.ABORT_EXIT


@pytest.mark.asyncio
async def test_a_winning_trade_closes_on_the_cross_and_reverses(db):  # noqa: F811
    plain = await _trades(db, _profits)
    held = await _trades(db, _profits, **HOLD)
    assert held[0]["exit_reason"] == recovery.PROFIT_EXIT
    assert held[0]["exit_time"] == plain[0]["exit_time"] and held[0]["exit_price"] == plain[0]["exit_price"]
    assert held[1]["direction"] == "SHORT" and held[1]["entry_time"] == held[0]["exit_time"]


@pytest.mark.asyncio
async def test_slow_sma_exit_leaves_on_the_first_close_below_the_slow_sma(db):  # noqa: F811
    plain = await _trades(db, _profits)
    slow = await _trades(db, _profits, slow_sma_exit=True)
    assert slow[0]["exit_reason"] == recovery.SLOW_SMA_EXIT
    assert slow[0]["exit_time"] < plain[0]["exit_time"]  # before the SMAs crossed
    assert slow[0]["exit_price"] > plain[0]["exit_price"]


def _seed_bots(database, bot1_mode="PAPER", bot2_mode="PAPER"):
    import bots
    from models import BotConfig

    bots.ensure_bot_config(2)
    with database.session_factory()() as s:
        one = s.get(BotConfig, bots.config_id_for(1))
        one.trade_symbols = "SBIN,TCS"
        one.trading_mode = bot1_mode
        two = s.get(BotConfig, bots.config_id_for(2))
        two.trading_mode = bot2_mode
        two.trade_symbols = ""
        s.commit()


def test_handoff_moves_the_stock_to_bot_2_with_the_chop_settings(db):  # noqa: F811
    import bots
    from models import BotConfig
    from strategy_engine import stock_settings, trade_names

    _seed_bots(db)
    text = recovery.handoff(1, "SBIN", source_live=False)
    assert text.startswith("moved to Bot 2"), text
    with db.session_factory()() as s:
        one = s.get(BotConfig, bots.config_id_for(1))
        two = s.get(BotConfig, bots.config_id_for(2))
        assert trade_names(one) == ["TCS"]
        assert trade_names(two) == ["SBIN"]
        own = stock_settings(two)["SBIN"]
        assert own["flip_orders"] is True and own["use_gap_mode"] is True
        assert own["gap_entry_long"] == 0.119 and own["gap_exit_short"] == -0.003
        assert own["rsi_long_min"] == 40.0 and own["rsi_short_max"] == 60.0
        assert json.loads(two.stock_settings)["SBIN"]["use_vwap"] is True
    assert recovery.handoff(2, "SBIN", source_live=False).startswith("already on Bot 2")


def test_a_practice_bot_never_hands_a_stock_to_a_live_bot_2(db):  # noqa: F811
    import bots
    from models import BotConfig
    from strategy_engine import trade_names

    _seed_bots(db, bot2_mode="LIVE")
    text = recovery.handoff(1, "SBIN", source_live=False)
    assert text.startswith("no handoff"), text
    with db.session_factory()() as s:
        assert "SBIN" in trade_names(s.get(BotConfig, bots.config_id_for(1)))
        assert "SBIN" not in trade_names(s.get(BotConfig, bots.config_id_for(2)))


def test_a_recovery_hold_survives_a_restart(db):  # noqa: F811
    from models import TradeLog
    from strategy_engine import StrategyEngine

    with db.session_factory()() as s:
        s.add(TradeLog(date="2026-09-22", symbol="SBIN", direction="LONG", qty=1, entry_time=dt.datetime(2026, 9, 22, 10, 0),
                       entry_price=800.0, ma_cross_price=800.0, atr_at_entry=2.0, sl_trigger_price=795.0, mode="PAPER",
                       bot=1, recovery_loss=1.25, recovery_bar=1_790_000_000))
        s.commit()
    engine = StrategyEngine()
    engine.restore_open_books()
    pos = engine.positions["SBIN"]
    assert pos.recovery_loss == 1.25 and pos.recovery_bar == 1_790_000_000


def test_api_saves_the_recovery_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    keys = {"slow_sma_exit", "recovery_hold", "recovery_tolerance_pct", "recovery_emergency_mult",
            "recovery_atr_mult", "recovery_max_candles", "recovery_handoff"}
    assert keys <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["recovery_hold"] is False and got["slow_sma_exit"] is False and got["recovery_handoff"] is True
        assert got["recovery_tolerance_pct"] == 0.15 and got["recovery_max_candles"] == 6
        ok = client.put("/api/config", json={"recovery_hold": True, "recovery_max_candles": 8, "slow_sma_exit": True})
        assert ok.status_code == 200 and ok.json()["recovery_hold"] is True and ok.json()["recovery_max_candles"] == 8
        assert client.put("/api/config", json={"recovery_emergency_mult": 0.5}).status_code == 422
        own = client.put("/api/bots/3/config/stock/SBIN", json={"recovery_hold": True})
        assert own.status_code == 200 and own.json()["recovery_hold"] is True
    database.reset_engine()
