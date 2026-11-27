"""Three SMAs stacked with widening gaps as an entry (stack_entry.py)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import stack_entry
from groww_client import IST

DAY = dt.date(2026, 9, 29)


def _frame(closes: list[float], start: dt.time = dt.time(9, 15)) -> pd.DataFrame:
    """1-minute candles with these closes, plus the forming candle last (never read)."""
    t0 = dt.datetime.combine(DAY, start, tzinfo=IST)
    rows = []
    for i, c in enumerate([*closes, closes[-1]]):
        o = closes[i - 1] if 0 < i <= len(closes) - 1 else c
        rows.append({"ts": int((t0 + dt.timedelta(minutes=i)).timestamp()), "open": o, "high": max(o, c) + 0.05,
                     "low": min(o, c) - 0.05, "close": c, "volume": 1000 * (i + 1)})
    return pd.DataFrame(rows)


def _cfg(**over):
    base = dict(stack_entry=True, stack_widen=2, stack_third=12, stack_min_gap=0.0, stack_only=False,
                sma_fast=3, sma_slow=6, sma_medium=None)
    return NS(**{**base, **over})


RISE = [100.0] * 12 + [100.0 + 0.02 * i * i for i in range(1, 20)]  # accelerating: both gaps keep growing
FALL = [200.0] * 12 + [200.0 - 0.02 * i * i for i in range(1, 20)]


def test_stacked_and_widening_calls_the_side():
    side, run, note = stack_entry.call(_frame(RISE), _cfg())
    assert side == "LONG" and run is not None and "stacked up" in note and "SMA 3 / 6 / 12" in note
    assert stack_entry.call(_frame(FALL), _cfg())[0] == "SHORT"


def test_no_call_when_not_widening_enough_or_gaps_too_small():
    flat = RISE + [RISE[-1]] * 4  # still stacked, the fast / slow gap closes
    side, _, note = stack_entry.call(_frame(flat), _cfg())
    assert side is None and "widening 0 of 2" in note
    side, _, note = stack_entry.call(_frame(RISE), _cfg(stack_min_gap=4.9))
    assert side is None and "under 4.9%" in note
    assert stack_entry.call(_frame(RISE), _cfg(stack_widen=10))[0] == "LONG"
    assert stack_entry.call(_frame(RISE[:13]), _cfg())[0] is None  # too early: not stacked / not widening yet


def test_periods_bounds_and_the_bots_own_three_lines():
    assert stack_entry.periods(_cfg()) == (3, 6, 12)
    assert stack_entry.periods(_cfg(stack_third=6)) is None  # not longer than the slow SMA
    assert "must be longer" in stack_entry.call(_frame(RISE), _cfg(stack_third=6))[2]
    # A bot already on three lines (fast / medium / slow) uses its own; stack_third is not read.
    assert stack_entry.periods(_cfg(sma_fast=3, sma_medium=6, sma_slow=12, stack_third=99)) == (3, 6, 12)
    assert stack_entry.longest(_cfg()) == 12 and stack_entry.longest(_cfg(stack_entry=False)) == 0
    assert stack_entry.widen_need(NS(stack_widen=99)) == stack_entry.WIDEN_MAX and not stack_entry.enabled(NS())
    assert "not formed" in stack_entry.call(_frame([100.0, 101.0, 102.0]), _cfg())[2]
    old = [100.0] * 12 + [100.0 + 0.02 * i * i for i in range(1, 45)]  # widening for 30+ candles: too late to join
    assert stack_entry.call(_frame(old), _cfg())[0] is None and "too late" in stack_entry.call(_frame(old), _cfg())[2]


def test_only_this_opens_trades():
    assert stack_entry.blocks(_cfg(), "SMA_CROSS") is None  # the switch is off
    only = _cfg(stack_only=True)
    assert stack_entry.blocks(only, "SMA_CROSS") and stack_entry.blocks(only, None) and stack_entry.blocks(only, "JOIN_TREND")
    for passes in ("STACK_WIDEN", "STACK_WIDEN:PULLBACK", "FORCE_ORDER", "NEWS", "HANDOFF"):
        assert stack_entry.blocks(only, passes) is None
    assert stack_entry.blocks(_cfg(stack_entry=False, stack_only=True), "SMA_CROSS") is None


@pytest.mark.asyncio
async def test_engine_enters_once_per_widening_run(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/s.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from models import BotConfig
    from strategy_engine import STOCK_FIELDS, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker, _instant

    assert {"stack_entry", "stack_widen", "stack_third", "stack_min_gap", "stack_only"} <= set(STOCK_FIELDS)
    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    now = dt.datetime.combine(DAY, dt.time(11, 0), tzinfo=IST)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: now)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "ACME"
        row.sma_fast, row.sma_slow = 3, 6
        row.stack_entry, row.stack_third, row.stack_min_gap, row.stack_only = True, 12, 0.0, True
        db.commit()
    cfg = eng.load_config()
    eng.status = "RUNNING"

    def put(closes):
        frame = _frame(closes)
        eng._frames["ACME"] = frame
        eng._tapes["ACME"] = frame
        eng._ltps["ACME"] = float(frame["close"].iloc[-1])
        return frame

    frame = put(RISE[:13])  # not stacked yet: nothing
    await eng._stack_entry_pass("ACME", frame, cfg, now)
    assert "ACME" not in eng.positions and "waits" in eng._signals["ACME"]
    frame = put(RISE)
    await eng._stack_entry_pass("ACME", frame, cfg, now)
    pos = eng.positions["ACME"]
    assert pos.direction == "LONG" and "stacked up" in eng._signals["ACME"]
    # The trade closes; the same widening run is still going on the next candle: no second entry.
    eng.positions.pop("ACME")
    frame = put(RISE + [RISE[-1] + 2.0])
    await eng._stack_entry_pass("ACME", frame, cfg, now)
    assert "ACME" not in eng.positions
    # The lines flatten (the run ends), then open up again: a new run, so the stock is entered again.
    again = RISE + [RISE[-1]] * 8 + [RISE[-1] + 0.3 * i * i for i in range(1, 5)]
    frame = put(again)
    await eng._stack_entry_pass("ACME", frame, cfg, now)
    assert eng.positions["ACME"].direction == "LONG", eng._signals["ACME"]
    with database.session_factory()() as db:
        from models import TradeLog

        assert db.query(TradeLog).order_by(TradeLog.id.desc()).first().entry_reason == stack_entry.ENTRY_REASON
    database.reset_engine()


def test_api_saves_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["stack_entry"] is False and first["stack_widen"] == 2 and first["stack_third"] == 50
        assert first["stack_min_gap"] == 0.05 and first["stack_only"] is False
        body = client.put("/api/config", json={"stack_entry": True, "stack_widen": 3, "stack_third": 100, "stack_min_gap": 0.1, "stack_only": True}).json()
        assert body["stack_entry"] and body["stack_widen"] == 3 and body["stack_third"] == 100 and body["stack_only"]
        assert client.put("/api/config", json={"stack_widen": 0}).status_code == 422
        assert client.put("/api/config", json={"stack_third": 500}).status_code == 422
    database.reset_engine()
