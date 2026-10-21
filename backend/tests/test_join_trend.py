"""Join the morning trend (join_trend.py): a trend whose cross printed yesterday is traded today.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import join_trend
from groww_client import IST
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)

DAY = dt.date(2026, 9, 22)


def _day(day: dt.date, path, price: float) -> list[dict]:
    rows, cum = [], 0
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for i in range(375):
        o = price
        price = path(i, price)
        cum += 1000
        rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, price) + 0.05,
                     "low": min(o, price) - 0.05, "close": price, "volume": cum})
        t += dt.timedelta(minutes=1)
    return rows


def _frame() -> pd.DataFrame:
    def flat_then_fall(i, p):  # the day before: flat, then the bearish cross in the afternoon
        return 1000.0 + (0.02 if i % 2 else -0.02) if i < 250 else p - 0.15

    def keep_falling(i, p):  # today: no cross at all, just the fall going on
        return p - 0.12 + (0.3 if i % 10 == 0 else 0.0)

    yesterday = _day(dt.date(2026, 9, 21), flat_then_fall, 1000.0)
    # Today opens where yesterday closed: no gap, no new cross, just the fall going on.
    return pd.DataFrame(yesterday + _day(DAY, keep_falling, yesterday[-1]["close"]))


async def _shorts(database, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(_frame(), "SIMJ", [DAY], _settings(database, **over))
    return [t for t in trades if t["direction"] == "SHORT"]


@pytest.mark.asyncio
async def test_without_join_a_trend_carried_from_yesterday_is_never_traded(db):  # noqa: F811
    assert await _shorts(db) == []


@pytest.mark.asyncio
async def test_join_sells_the_morning_downtrend_n_minutes_after_the_open(db):  # noqa: F811
    shorts = await _shorts(db, join_trend=True)  # default: 15 minutes after 09:15
    assert len(shorts) == 1
    assert dt.datetime(2026, 9, 22, 9, 30) <= shorts[0]["entry_time"] <= dt.datetime(2026, 9, 22, 9, 32)
    assert shorts[0]["entry_reason"] == "JOIN_TREND"
    later = await _shorts(db, join_trend=True, join_after_min=60)
    assert len(later) == 1
    assert dt.datetime(2026, 9, 22, 10, 15) <= later[0]["entry_time"] <= dt.datetime(2026, 9, 22, 10, 17)


@pytest.mark.asyncio
async def test_join_waits_for_the_ticked_filters(db):  # noqa: F811
    # RSI is deep below 30 on a steady fall: the sell range 30-60 refuses it all day.
    assert await _shorts(db, join_trend=True, use_rsi=True, rsi_short_min=30, rsi_short_max=60) == []


@pytest.mark.asyncio
async def test_a_stopped_out_join_is_not_joined_again_that_day(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    # A tight ATR stop is hit by the first bounce; the trend goes on but is joined once a day.
    settings = _settings(db, join_trend=True, use_stop=True, stop_type="ATR", atr_multiplier=0.2)
    trades = await replay_symbol(_frame(), "SIMJ", [DAY], settings)
    shorts = [t for t in trades if t["direction"] == "SHORT"]
    assert len(shorts) == 1 and shorts[0]["exit_reason"] not in ("EOD_SQUARE_OFF", None)


@pytest.mark.asyncio
async def test_join_arms_gap_mode_which_still_needs_its_gap(db):  # noqa: F811
    wide = await _shorts(db, join_trend=True, use_gap_mode=True, gap_entry_short=-50.0, gap_exit_short=-0.02)
    assert wide == []  # a -50% gap is never reached
    ok = await _shorts(db, join_trend=True, use_gap_mode=True, gap_entry_short=-0.05, gap_exit_short=-0.02)
    assert ok and ok[0]["entry_time"] <= dt.datetime(2026, 9, 22, 9, 35)


def test_side_start_is_the_last_cross_clipped_to_today():
    t0 = int(dt.datetime(2026, 9, 21, 14, 0, tzinfo=IST).timestamp())
    today = int(dt.datetime(2026, 9, 22, 9, 15, tzinfo=IST).timestamp())
    rows = [
        {"ts": t0, "sma_9": 101.0, "sma_21": 100.0},
        {"ts": t0 + 60, "sma_9": 99.0, "sma_21": 100.0},  # bearish cross yesterday
        {"ts": today, "sma_9": 98.0, "sma_21": 100.0},
        {"ts": today + 60, "sma_9": 97.0, "sma_21": 100.0},
        {"ts": today + 120, "sma_9": 96.0, "sma_21": 100.0},  # forming candle: not read
    ]
    assert join_trend.side_start(pd.DataFrame(rows)) == ("SHORT", today)
    rows.insert(3, {"ts": today + 30, "sma_9": 100.5, "sma_21": 100.0})  # a cross back up today ...
    rows[4]["sma_9"] = 99.5  # ... and down again on the next candle
    assert join_trend.side_start(pd.DataFrame(rows)) == ("SHORT", today + 60)
    assert join_trend.side_start(pd.DataFrame(rows[:2])) is None


def test_api_saves_join_trend(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"join_trend", "join_after_min"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["join_trend"] is False and first["join_after_min"] == 15
        ok = client.put("/api/config", json={"join_trend": True, "join_after_min": 30})
        assert ok.status_code == 200 and ok.json()["join_trend"] is True and ok.json()["join_after_min"] == 30
        assert client.put("/api/config", json={"join_after_min": 999}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"join_trend": False})
        assert own.status_code == 200 and own.json()["join_trend"] is False
    database.reset_engine()
