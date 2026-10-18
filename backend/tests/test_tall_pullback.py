"""Tall-candle pullback entry (tall_pullback.py): wait for a pullback after a tall signal candle.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import tall_pullback
from tests.test_gap_mode import _day, _settings, db  # noqa: F401  (db is a fixture)

DAY = dt.date(2026, 9, 22)
JUMP = dt.datetime(2026, 9, 22, 10, 15)


def _frame(after) -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        if i < 40:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        if i < 60:  # a gentle slide, so the fast SMA is under the slow one before the jump
            return p - 0.03
        if i == 60:  # 10:15: one tall green candle crosses the SMAs
            return p + 5.0
        return after(i, p)

    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(DAY, path))


def _pulls_back(i, p):
    if i == 61:  # the next minute gives back more than half the jump
        return p - 3.2
    return p + 0.05  # then the trend resumes


def _runs_away(i, p):
    return p + 0.05  # never comes back


async def _longs(database, after, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(_frame(after), "SIMT", [DAY], _settings(database, **over))
    return [t for t in trades if t["direction"] == "LONG" and t["entry_time"] >= JUMP]


def test_plan_levels_and_expiry():
    cfg = type("C", (), {"tall_pullback": True, "tall_atr_mult": 1.5, "tall_expiry_candles": 2})()
    bar = {"high": 110.0, "low": 100.0, "sma_9": 103.0}
    assert tall_pullback.plan(bar, "LONG", 10.0, cfg) is None  # 10 < 1.5 x 10: order at once
    long = tall_pullback.plan(bar, "LONG", 5.0, cfg)
    assert long is not None and long.limit == 105.0  # max(SMA 9 103, half-way 105)
    assert tall_pullback.plan({**bar, "sma_9": 107.0}, "LONG", 5.0, cfg).limit == 107.0
    short = tall_pullback.plan(bar, "SHORT", 5.0, cfg)
    assert short.limit == 103.0  # min(SMA 9 103, half-way 105)
    assert tall_pullback.touched("LONG", 104.9, 105.0) and not tall_pullback.touched("LONG", 105.1, 105.0)
    assert tall_pullback.touched("SHORT", 103.0, 103.0) and not tall_pullback.touched("SHORT", 102.9, 103.0)
    t0 = 1_790_000_000
    assert not tall_pullback.expired(t0, t0 + 300, 300, 2)  # one candle closed after the signal
    assert tall_pullback.expired(t0, t0 + 600, 300, 2)  # the second one closed
    assert tall_pullback.mult(type("C", (), {"tall_atr_mult": 99})()) == tall_pullback.MAX_MULT
    assert tall_pullback.expiry(type("C", (), {})()) == 2


@pytest.mark.asyncio
async def test_a_tall_cross_candle_is_bought_on_the_pullback_not_at_its_top(db):  # noqa: F811
    plain = await _longs(db, _pulls_back)
    pulled = await _longs(db, _pulls_back, tall_pullback=True)
    assert len(plain) >= 1 and len(pulled) >= 1
    top, dip = plain[0]["entry_price"], pulled[0]["entry_price"]
    assert top > 1003.0  # plain mode buys straight after the tall candle, near its top
    assert dip <= top - 1.5  # the pullback entry is well under the tall candle's top
    assert pulled[0]["entry_time"] >= plain[0]["entry_time"]


@pytest.mark.asyncio
async def test_no_pullback_within_two_candles_means_no_order(db):  # noqa: F811
    assert len(await _longs(db, _runs_away)) >= 1  # plain mode chases the move
    assert await _longs(db, _runs_away, tall_pullback=True) == []
    # A longer wait cannot help either: the price never comes back.
    assert await _longs(db, _runs_away, tall_pullback=True, tall_expiry_candles=10) == []


def test_api_saves_tall_pullback(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"tall_pullback", "tall_atr_mult", "tall_expiry_candles"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["tall_pullback"] is False and got["tall_atr_mult"] == 1.5 and got["tall_expiry_candles"] == 2
        ok = client.put("/api/config", json={"tall_pullback": True, "tall_atr_mult": 2.0, "tall_expiry_candles": 3})
        assert ok.status_code == 200 and ok.json()["tall_pullback"] is True and ok.json()["tall_atr_mult"] == 2.0
        assert client.put("/api/config", json={"tall_expiry_candles": 99}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"tall_pullback": False})
        assert own.status_code == 200 and own.json()["tall_pullback"] is False
    database.reset_engine()
