"""Counter candles (counter_candles.py): a run of candles against the trade closes it, or enters early.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import counter_candles
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)
from tests.test_join_trend import _day

DAY = dt.date(2026, 9, 22)


def _frame() -> pd.DataFrame:
    def path(i, p):
        if i < 60:  # flat
            return 1000.0 + (0.02 if i % 2 else -0.02)
        if i < 180:  # the fall: a bearish cross, one green candle every 10 minutes
            return p - 0.15 + (0.3 if i % 10 == 0 else 0.0)
        return p + 0.2  # the turn: green candle after green candle

    flat = _day(dt.date(2026, 9, 21), lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02), 1000.0)
    return pd.DataFrame(flat + _day(DAY, path, 1000.0))


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_frame(), "SIMC", [DAY], _settings(database, **over))


TURN = dt.datetime(2026, 9, 22, 12, 15)  # minute 180: the first green candle of the turn


def _first(trades, direction, since=dt.datetime(2026, 9, 22, 10, 15)):
    return next(t for t in trades if t["direction"] == direction and t["entry_time"] >= since)


@pytest.mark.asyncio
async def test_counter_candles_close_the_short_before_the_cross(db):  # noqa: F811
    plain = _first(await _trades(db), "SHORT")
    assert plain["exit_reason"] == "MA_CROSS"
    early = _first(await _trades(db, counter_exit_count=3), "SHORT")
    assert early["exit_reason"] == "COUNTER_CANDLES"
    assert TURN < early["exit_time"] < plain["exit_time"]
    assert early["entry_time"] == plain["entry_time"]  # the entry is untouched


@pytest.mark.asyncio
async def test_the_fast_sma_check_and_count_matter(db):  # noqa: F811
    loose = _first(await _trades(db, counter_exit_count=3, counter_need_fast=False), "SHORT")
    strict = _first(await _trades(db, counter_exit_count=3), "SHORT")
    assert loose["exit_reason"] == "COUNTER_CANDLES" and loose["exit_time"] <= strict["exit_time"]
    # One green candle every 10 minutes in the fall never makes a run of 3.
    assert loose["exit_time"] > TURN
    longer = _first(await _trades(db, counter_exit_count=6), "SHORT")
    assert longer["exit_time"] > strict["exit_time"]


@pytest.mark.asyncio
async def test_min_body_ignores_small_counter_candles(db):  # noqa: F811
    strict = _first(await _trades(db, counter_exit_count=3), "SHORT")
    tiny = _first(await _trades(db, counter_exit_count=3, counter_min_body=0.01), "SHORT")
    assert (tiny["exit_time"], tiny["exit_reason"]) == (strict["exit_time"], "COUNTER_CANDLES")
    # The turn's candles are far smaller than 5 ATR: they never count, so the cross closes it.
    big = _first(await _trades(db, counter_exit_count=3, counter_min_body=5), "SHORT")
    assert big["exit_reason"] != "COUNTER_CANDLES" and big["exit_time"] > strict["exit_time"]


@pytest.mark.asyncio
async def test_early_reversal_entry_buys_before_the_cross_once(db):  # noqa: F811
    plain = await _trades(db)
    plain_long = _first(plain, "LONG", since=TURN)
    assert plain_long["entry_time"] > TURN + dt.timedelta(minutes=5)
    trades = await _trades(db, counter_exit_count=3, counter_entry=True)
    longs = [t for t in trades if t["direction"] == "LONG" and TURN <= t["entry_time"] < plain_long["entry_time"]]
    assert len(longs) == 1  # once per SMA run
    assert longs[0]["entry_time"] - TURN <= dt.timedelta(minutes=5)


@pytest.mark.asyncio
async def test_off_by_default_changes_nothing(db):  # noqa: F811
    plain = await _trades(db)
    same = await _trades(db, counter_exit_count=0, counter_entry=False)
    key = lambda t: (t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"])  # noqa: E731
    assert [key(t) for t in plain] == [key(t) for t in same]


def _closed(closes, opens=None, fast=None):
    opens = opens or [c - 0.5 for c in closes]
    rows = [{"open": o, "close": c, "high": max(o, c) + 0.1, "low": min(o, c) - 0.1,
             "sma_9": fast if fast is not None else c, "sma_21": 100.0} for o, c in zip(opens, closes)]
    return pd.DataFrame(rows)


def test_run_towards_needs_every_candle_and_the_fast_sma():
    up = _closed([100.0, 101.0, 102.0, 103.0], fast=101.5)
    assert counter_candles.run_towards(up, "LONG", 3, "COLOUR", True)[0]
    assert not counter_candles.run_towards(up, "SHORT", 3, "COLOUR", True)[0]
    below_fast = _closed([100.0, 101.0, 102.0, 103.0], fast=104.0)
    ok, note = counter_candles.run_towards(below_fast, "LONG", 3, "COLOUR", True)
    assert not ok and "fast SMA" in note
    assert counter_candles.run_towards(below_fast, "LONG", 3, "COLOUR", False)[0]
    broken = _closed([100.0, 101.0, 100.5, 103.0], opens=[99.5, 100.5, 101.0, 102.5], fast=101.0)
    assert not counter_candles.run_towards(broken, "LONG", 3, "COLOUR", True)[0]


def test_run_towards_body_check():
    up = _closed([100.0, 101.0, 102.0, 103.0], fast=101.5)  # bodies of 0.5
    up["atr_14"] = 1.0
    assert counter_candles.run_towards(up, "LONG", 3, "COLOUR", True, 0.5)[0]
    ok, note = counter_candles.run_towards(up, "LONG", 3, "COLOUR", True, 0.6)
    assert not ok and "ATR" in note
    no_atr = _closed([100.0, 101.0, 102.0, 103.0], fast=101.5)
    assert not counter_candles.run_towards(no_atr, "LONG", 3, "COLOUR", True, 0.5)[0]
    assert counter_candles.run_towards(no_atr, "LONG", 3, "COLOUR", True, 0.0)[0]  # 0 = any size


def test_settings_helpers():
    class C:
        counter_exit_count = 99
        counter_rule = "nonsense"
        counter_need_fast = None
        counter_entry = True

    assert counter_candles.exit_count(C) == counter_candles.MAX_COUNT
    assert counter_candles.rule(C) == "COLOUR" and counter_candles.need_fast(C) is True
    assert counter_candles.entry_on(C) and counter_candles.entry_count(C) == counter_candles.MAX_COUNT
    C.counter_exit_count = 0
    assert counter_candles.entry_count(C) == 3
    assert counter_candles.min_body(C) == 0.0
    C.counter_min_body = 99
    assert counter_candles.min_body(C) == counter_candles.MAX_MIN_BODY
    assert counter_candles.sma_side(pd.DataFrame([{"sma_9": 99.0, "sma_21": 100.0}])) == "SHORT"
    assert counter_candles.sma_side(pd.DataFrame([{"sma_9": 101.0, "sma_21": 100.0}])) == "LONG"


def test_api_saves_counter_candles(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"counter_exit_count", "counter_entry", "counter_rule", "counter_need_fast", "counter_min_body"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["counter_exit_count"] == 0 and first["counter_entry"] is False
        assert first["counter_rule"] == "COLOUR" and first["counter_need_fast"] is True
        ok = client.put("/api/config", json={"counter_exit_count": 3, "counter_entry": True, "counter_rule": "BOTH"})
        assert ok.status_code == 200 and ok.json()["counter_exit_count"] == 3 and ok.json()["counter_rule"] == "BOTH"
        assert client.put("/api/config", json={"counter_exit_count": 11}).status_code == 422
        body = client.put("/api/config", json={"counter_min_body": 0.5})
        assert body.status_code == 200 and body.json()["counter_min_body"] == 0.5
        assert client.put("/api/config", json={"counter_min_body": 6}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"counter_entry": False})
        assert own.status_code == 200 and own.json()["counter_entry"] is False
    database.reset_engine()
