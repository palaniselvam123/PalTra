"""Day-extreme guard (day_extreme.py): a buy at today's high / a sell at today's low waits for a pullback.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import day_extreme
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)
from tests.test_join_trend import _day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 9, 22)


def _closed(rows):
    start = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    out = []
    for i, (high, low, close, fast) in enumerate(rows):
        out.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "high": high, "low": low,
                    "close": close, "sma_9": fast})
    # A row from the day before never counts towards today's range.
    out.insert(0, {"ts": int(start.timestamp()) - 86_400, "high": 2000.0, "low": 1.0, "close": 1000.0, "sma_9": 1000.0})
    return pd.DataFrame(out)


def test_plan_waits_only_near_the_days_extreme():
    cfg = SimpleNamespace(day_extreme_guard=True)
    at_high = _closed([(101, 99, 100, 99.5), (102.0, 100, 101.95, 100.5)])
    limit, note = day_extreme.plan(at_high, "LONG", cfg)
    assert limit == pytest.approx(max(101.95 * (1 - 0.0015), 100.5)) and "today's high 102.00" in note
    assert day_extreme.plan(at_high, "SHORT", cfg) is None  # a short at the high is not at the low
    mid = _closed([(105, 99, 100, 99.5), (102.0, 100, 101.0, 100.5)])
    assert day_extreme.plan(mid, "LONG", cfg) is None  # 101 is ~3.8% under the 105 high
    at_low = _closed([(101, 99, 100, 100.5), (100, 97.0, 97.05, 98.0)])
    limit, note = day_extreme.plan(at_low, "SHORT", cfg)
    assert limit == pytest.approx(min(97.05 * 1.0015, 98.0)) and "today's low 97.00" in note
    # The SMA nearer than the % pull wins (a buy waits at the higher of the two).
    near_sma = _closed([(101, 99, 100, 99.5), (102.0, 100, 101.95, 101.9)])
    assert day_extreme.plan(near_sma, "LONG", cfg)[0] == pytest.approx(101.9)


def test_settings_bounds():
    assert day_extreme.on(SimpleNamespace()) is False
    cfg = SimpleNamespace(day_extreme_pct=99, day_extreme_pull_pct=-1, day_extreme_candles=50)
    assert day_extreme.near_pct(cfg) == day_extreme.MAX_PCT and day_extreme.pull_pct(cfg) == 0
    assert day_extreme.candles(cfg) == day_extreme.MAX_CANDLES
    assert day_extreme.candles(SimpleNamespace()) == 3


def _rise_dip_rise() -> pd.DataFrame:
    """Flat, then a rise from 10:15 with a sharp dip every 10 minutes."""
    def path(i, p):
        if i < 60:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        return p - 3.0 if i % 10 == 0 else p + 0.7

    flat = _day(dt.date(2026, 9, 21), lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02), 1000.0)
    return pd.DataFrame(flat + _day(DAY, path, 1000.0))


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_rise_dip_rise(), "SIMX", [DAY], _settings(database, **over))


@pytest.mark.asyncio
async def test_guard_buys_on_the_pullback_not_at_the_high(db):  # noqa: F811
    trend = dt.datetime(2026, 9, 22, 10, 15)
    plain = [t for t in await _trades(db) if t["direction"] == "LONG" and t["entry_time"] >= trend]
    guarded = await _trades(db, day_extreme_guard=True, day_extreme_candles=10)
    longs = [t for t in guarded if t["direction"] == "LONG" and t["entry_time"] >= trend]
    assert plain and longs
    assert longs[0]["entry_reason"] == "SMA_CROSS:PULLBACK"
    assert longs[0]["entry_time"] > plain[0]["entry_time"]
    frame = _rise_dip_rise()
    entry_ts = longs[0]["entry_time"].replace(tzinfo=IST).timestamp()
    start = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST).timestamp()
    day_high = frame[(frame["ts"] >= start) & (frame["ts"] < entry_ts)]["high"].max()
    assert longs[0]["entry_price"] < day_high * (1 - 0.001)  # bought on a dip, below the day's high so far
    # The flat morning's buys at its high and shorts at its low wait too, so far fewer trades are taken.
    assert len(guarded) < len(await _trades(db))


@pytest.mark.asyncio
async def test_off_by_default_changes_nothing(db):  # noqa: F811
    key = lambda t: (t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"])  # noqa: E731
    plain = [key(t) for t in await _trades(db)]
    assert [key(t) for t in await _trades(db, day_extreme_guard=False)] == plain


def test_api_saves_the_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/3/config").json()
        assert got["day_extreme_guard"] is False and got["day_extreme_pct"] == 0.15 and got["day_extreme_candles"] == 3
        ok = client.put("/api/bots/3/config", json={"day_extreme_guard": True, "day_extreme_pct": 0.3, "day_extreme_candles": 5})
        assert ok.status_code == 200 and ok.json()["day_extreme_pct"] == 0.3
        assert client.put("/api/bots/3/config", json={"day_extreme_candles": 0}).status_code == 422
        stock = client.put("/api/bots/3/config/stock/SBIN", json={"day_extreme_guard": False})
        assert stock.status_code == 200
    database.reset_engine()
