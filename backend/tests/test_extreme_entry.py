"""Candle-extreme limit entry (extreme_entry.py): wait at the signal candle's low (buy) / high (sell).

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import extreme_entry
from tests.test_gap_mode import _day, _settings, db  # noqa: F401  (db is a fixture)

DAY = dt.date(2026, 9, 22)
JUMP = dt.datetime(2026, 9, 22, 10, 15)
CROSS_ROW = 375 + 60  # the 10:15 tall green candle that crosses the SMAs


def _frame(after) -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        if i < 40:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        if i < 60:  # a gentle slide, so the fast SMA is under the slow one before the cross
            return p - 0.03
        if i == 60:
            return p + 5.0
        return after(i, p)

    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(DAY, path))


def _back_to_the_low(i, p):
    return p - 5.1 if i == 61 else p + 0.05  # one minute back to the cross candle's low, then up


def _runs_away(i, p):
    return p + 0.05


def _gaps_down(frame: pd.DataFrame, to: float) -> pd.DataFrame:
    """10:16 opens far under the cross candle's low (a gap), then climbs."""
    out = frame.copy()
    row = CROSS_ROW + 1
    price = to
    for k in range(row, CROSS_ROW + 315):
        o = price if k > row else to
        price = to + 0.05 * (k - row + 1)
        out.loc[k, ["open", "high", "low", "close"]] = [o, max(o, price) + 0.05, min(o, price) - 0.05, price]
    return out


async def _longs(database, frame, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(frame, "SIMX", [DAY], _settings(database, **over))
    return [t for t in trades if t["direction"] == "LONG" and t["entry_time"] >= JUMP]


def test_levels_invalidation_and_settings():
    cfg = type("C", (), {"extreme_entry": True})()
    bar = {"high": 110.0, "low": 100.0, "sma_9": 103.0, "sma_21": 101.0}
    assert extreme_entry.levels(bar, "LONG", cfg) == (100.0, pytest.approx(99.95))
    assert extreme_entry.levels(bar, "SHORT", cfg) == (110.0, pytest.approx(110.055))
    sma = type("C", (), {"extreme_anchor": "SMA_9", "extreme_invalid_pct": 1.0})()
    assert extreme_entry.levels(bar, "LONG", sma) == (103.0, pytest.approx(99.0))  # max(low, SMA 9)
    assert extreme_entry.levels(bar, "SHORT", sma)[0] == 103.0  # min(high, SMA 9)
    assert extreme_entry.broken("LONG", 99.9, 99.95) and not extreme_entry.broken("LONG", 99.95, 99.95)
    assert extreme_entry.broken("SHORT", 110.1, 110.055) and not extreme_entry.broken("SHORT", None, 110.055)
    assert extreme_entry.sma_side(bar) == 1
    assert extreme_entry.crossed_against("LONG", 1, -1) and not extreme_entry.crossed_against("LONG", 1, 1)
    # An early-reversal buy starts with the SMAs already against it: that is not a flip.
    assert not extreme_entry.crossed_against("LONG", -1, -1)
    assert not extreme_entry.on(type("C", (), {})())  # off by default
    assert not extreme_entry.applies(type("C", (), {"extreme_entry": True, "entry_mode": "PATTERN"})())
    assert extreme_entry.anchor(type("C", (), {"extreme_anchor": "nonsense"})()) == "CANDLE_EXTREME"
    assert extreme_entry.invalid_pct(type("C", (), {"extreme_invalid_pct": 99})()) == extreme_entry.MAX_INVALID_PCT
    assert extreme_entry.expiry(type("C", (), {})()) == 2
    assert extreme_entry.cancel_on_cross(type("C", (), {})())


@pytest.mark.asyncio
async def test_the_buy_waits_at_the_cross_candle_low_not_its_top(db):  # noqa: F811
    frame = _frame(_back_to_the_low)
    low = float(frame.loc[CROSS_ROW, "low"])
    plain = await _longs(db, frame)
    waited = await _longs(db, frame, extreme_entry=True)
    assert plain and waited
    assert plain[0]["entry_price"] > low + 4  # plain mode buys right after the tall candle, near its top
    assert waited[0]["entry_price"] <= low + 0.01  # the limit at the candle's low
    assert waited[0]["entry_time"] > plain[0]["entry_time"]


@pytest.mark.asyncio
async def test_no_fill_within_the_expiry_means_no_order(db):  # noqa: F811
    assert await _longs(db, _frame(_runs_away))  # plain mode buys
    assert await _longs(db, _frame(_runs_away), extreme_entry=True) == []


@pytest.mark.asyncio
async def test_a_gap_through_the_low_cancels_the_buy(db):  # noqa: F811
    frame = _frame(_runs_away)
    low = float(frame.loc[CROSS_ROW, "low"])
    gapped = _gaps_down(frame, low - 4.0)
    # A wide buffer lets the gap fill the limit; the 0.05 % default calls the setup broken.
    wide = await _longs(db, gapped, extreme_entry=True, extreme_invalid_pct=5.0, extreme_cancel_on_cross=False)
    assert wide and wide[0]["entry_price"] < low
    assert await _longs(db, gapped, extreme_entry=True, extreme_cancel_on_cross=False) == []


def test_api_saves_the_extreme_entry_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    keys = {"extreme_entry", "extreme_anchor", "extreme_invalid_pct", "extreme_expiry_candles", "extreme_cancel_on_cross"}
    assert keys <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/bots/3/config").json()
        assert got["extreme_entry"] is False and got["extreme_anchor"] == "CANDLE_EXTREME"
        assert got["extreme_invalid_pct"] == 0.05 and got["extreme_expiry_candles"] == 2
        assert got["extreme_cancel_on_cross"] is True
        ok = client.put("/api/bots/3/config", json={"extreme_entry": True, "extreme_anchor": "SMA_9"})
        assert ok.status_code == 200 and ok.json()["extreme_entry"] is True and ok.json()["extreme_anchor"] == "SMA_9"
        assert client.put("/api/bots/3/config", json={"extreme_anchor": "TOP"}).status_code == 422
        assert client.put("/api/bots/3/config", json={"extreme_expiry_candles": 0}).status_code == 422
        own = client.put("/api/bots/3/config/stock/BEL", json={"extreme_entry": False})
        assert own.status_code == 200 and own.json()["extreme_entry"] is False
    database.reset_engine()
