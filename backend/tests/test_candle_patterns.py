"""Candle-pattern entries: buy a bullish / short a bearish pattern at the start of the
next candle, close at its end. Replays on local fills only (no Groww order path).
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from candle_patterns import candle_pattern, closes_bucket, pattern_call, tf_candles
from tests.test_gap_mode import _settings, db  # noqa: F401

IST = ZoneInfo("Asia/Kolkata")
DAY0, DAY = dt.date(2026, 9, 21), dt.date(2026, 9, 22)


def _c(o, c, wick=0.05):
    return {"open": o, "high": max(o, c) + wick, "low": min(o, c) - wick, "close": c}


def test_pattern_names_match_the_chart_table():
    rows = [_c(100, 100.1), _c(100.1, 100.2), _c(100.6, 100.0), _c(99.9, 100.8)]
    assert candle_pattern(rows, 3) == ("Bullish engulfing", "bullish")
    rows = [_c(100, 99.9), _c(99.9, 99.8), _c(99.4, 100.0), _c(100.1, 99.2)]
    assert candle_pattern(rows, 3) == ("Bearish engulfing", "bearish")
    soldiers = [_c(100, 100), _c(100, 101, 0.1), _c(100.5, 101.5, 0.1), _c(101, 102, 0.1)]
    assert candle_pattern(soldiers, 3)[0] == "Three white soldiers"


def _rows(day: dt.date, candles: list[tuple[float, float]]) -> list[dict]:
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    out, vol = [], 0
    for o, c in candles:
        vol += 1000
        out.append({"ts": int(t.timestamp()), **_c(o, c), "volume": vol})
        t += dt.timedelta(minutes=1)
    return out


def _quiet(n: int, price: float = 1000.0) -> list[tuple[float, float]]:
    # Doji-like candles: no trading pattern.
    return [(price, price)] * n


def test_five_minute_candles_close_on_the_session_grid():
    def ts(h, m):
        return int(dt.datetime.combine(DAY, dt.time(h, m), tzinfo=IST).timestamp())

    assert closes_bucket(ts(9, 19), 5) and not closes_bucket(ts(9, 18), 5)
    assert closes_bucket(ts(9, 17), 3) and closes_bucket(ts(10, 0), 1)
    frame = pd.DataFrame(_rows(DAY, [(1000 + i, 1001 + i) for i in range(12)]))
    merged = tf_candles(frame, 5)
    # 12 minutes = two complete 5-minute candles; the third is still forming.
    assert len(merged) == 2 and merged[0]["open"] == 1000 and merged[0]["close"] == 1005


def _cfg(**over):
    base = dict(entry_mode="PATTERN", pattern_tf=1, pattern_trend=False, pattern_set="STRONG", pattern_min_edge=0.0,
                qty=1, sma_fast=9, sma_slow=21)
    return SimpleNamespace(**{**base, **over})


def test_trend_and_charge_checks():
    candles = _quiet(40) + [(1000.6, 1000.0), (999.9, 1000.8)] + _quiet(3, 1000.8)
    frame = pd.DataFrame(_rows(DAY, candles)).iloc[:42 + 1]  # the engulfing candle closed, the next one forming
    frame["sma_9"], frame["sma_21"] = 1000.0, 1001.0  # SMA 9 below SMA 21: a down trend
    assert pattern_call(frame, _cfg()).side == "LONG"
    against = pattern_call(frame, _cfg(pattern_trend=True))
    assert against.side is None and "against the SMA trend" in against.note
    # 1000 shares: the quiet candles' 0.1 range is far below 1.5× the round-trip charges.
    small = pattern_call(frame, _cfg(pattern_min_edge=1.5, qty=1000))
    assert small.side is None and "too small for the charges" in small.note


@pytest.mark.asyncio
async def test_buys_at_the_next_candle_start_and_sells_at_its_end(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    day2 = _quiet(30) + [(1000.6, 1000.0), (999.9, 1000.8)] + _quiet(343, 1000.8)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    trades = await replay_symbol(frame, "PAT", [DAY], _settings(db, entry_mode="PATTERN", pattern_tf=1,
                                                                pattern_min_edge=0, qty=1))
    assert len(trades) == 1
    t = trades[0]
    # Engulfing candle 09:46 closes at 09:47: buy then, sell when the 09:47 candle ends.
    assert t["direction"] == "LONG" and t["exit_reason"] == "CANDLE_END"
    assert t["entry_time"].strftime("%H:%M") == "09:47" and t["exit_time"].strftime("%H:%M") == "09:48"


@pytest.mark.asyncio
async def test_bearish_pattern_shorts_and_a_five_minute_candle_waits_for_its_close(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    # 09:40-09:44 one rising 5-minute candle, 09:45-09:49 one falling candle that engulfs it.
    up = [(1000.0 + 0.1 * k, 1000.1 + 0.1 * k) for k in range(5)]
    down = [(1000.6 - 0.25 * k, 1000.35 - 0.25 * k) for k in range(5)]
    day2 = _quiet(25) + up + down + _quiet(340, 999.35)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    trades = await replay_symbol(frame, "PAT", [DAY], _settings(db, entry_mode="PATTERN", pattern_tf=5,
                                                                pattern_min_edge=0, qty=1))
    assert len(trades) == 1
    t = trades[0]
    assert t["direction"] == "SHORT" and t["exit_reason"] == "CANDLE_END"
    assert t["entry_time"].strftime("%H:%M") == "09:50" and t["exit_time"].strftime("%H:%M") == "09:55"


def test_api_saves_the_pattern_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["entry_mode"] == "SMA" and first["pattern_tf"] == 1 and first["pattern_min_edge"] == 1.5
        ok = client.put("/api/config", json={"entry_mode": "PATTERN", "pattern_tf": 5, "pattern_trend": True})
        assert ok.status_code == 200 and ok.json()["pattern_tf"] == 5
        assert client.put("/api/config", json={"pattern_tf": 2}).status_code == 422
        assert client.put("/api/bots/2/config", json={"entry_mode": "PATTERN"}).json()["entry_mode"] == "PATTERN"
        own = client.put("/api/config/stock/TCS", json={"pattern_tf": 3})
        assert own.status_code == 200 and own.json()["own"] == {"pattern_tf": 3}
    database.reset_engine()
