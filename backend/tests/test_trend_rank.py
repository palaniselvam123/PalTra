"""Trend age, SMA gap and rank on the Trend quality scan (chop_scan._run_read, trend_rank.py). Read-only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

import chop_scan
import trend_rank

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 10, 8)


def _at(hh: int, mm: int) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(hh, mm), tzinfo=IST)


def _minutes(closes: list[float]) -> pd.DataFrame:
    start = _at(9, 15)
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": prev,
                     "high": max(prev, c) + 0.05, "low": min(prev, c) - 0.05, "close": c, "volume": 1000 * (i + 1)})
        prev = c
    return pd.DataFrame(rows)


def test_run_read_finds_where_the_current_side_began():
    ts = list(range(0, 600, 60))
    fast = [99, 99, 99, 101, 102, 103, 104, 104.5, 104.2, 104.1]
    slow = [100] * 10
    frame = pd.DataFrame({"ts": ts, "sma_9": fast, "sma_21": slow})
    since, candles, gap, peak = chop_scan._run_read(frame)
    assert since == 180 and candles == 7
    assert gap == pytest.approx(4.1) and peak == pytest.approx(4.5)
    assert chop_scan._run_read(pd.DataFrame({"ts": [0], "sma_9": [None], "sma_21": [100]})) == (None, None, None, None)


def test_analyze_reports_run_and_gap_on_every_size():
    # Down all morning, then up from 11:15: the 5-minute uptrend is a couple of hours old, not since the open.
    tape = [100 - i * 0.05 for i in range(120)] + [94 + i * 0.08 for i in range(180)]
    row = chop_scan.analyze(_minutes(tape), 9, 21, 5, _at(14, 20), symbol="TURN")
    assert row is not None
    assert row.side_by_minutes[5] == "BULLISH"
    candles = row.run_candles_by_minutes[5]
    assert candles is not None and 10 <= candles <= 36
    assert row.run_since_ts_by_minutes[5] > int(_at(11, 0).timestamp())
    assert row.gap_pct_by_minutes[5] > 0 and row.gap_peak_pct_by_minutes[5] >= row.gap_pct_by_minutes[5]
    assert set(row.as_dict()["gap_pct_by_minutes"]) == set(row.side_by_minutes)


def _row(symbol, side, candles, gap, peak=None, sizes=(3, 5)):
    return {
        "symbol": symbol,
        "ltp": 100.0,
        "side_by_minutes": {str(m): side for m in sizes},
        "trend_by_minutes": {str(m): None for m in sizes},
        "run_candles_by_minutes": {str(m): candles for m in sizes},
        "run_since_ts_by_minutes": {str(m): 1000 for m in sizes},
        "gap_pct_by_minutes": {str(m): gap if side == "BULLISH" else -gap for m in sizes},
        "gap_peak_pct_by_minutes": {str(m): peak if peak is not None else gap for m in sizes},
    }


def test_read_needs_every_size_on_the_same_side_and_takes_the_youngest_age():
    row = _row("A", "BULLISH", 4, 0.3)
    row["run_candles_by_minutes"]["5"] = 10
    got = trend_rank.read(row, [3, 5])
    assert got["side"] == "BULLISH" and got["age_min"] == 12 and got["gap_pct"] == pytest.approx(0.3)
    assert trend_rank.read(row, [3, 5], direction="DOWN") is None
    mixed = _row("B", "BULLISH", 4, 0.3)
    mixed["side_by_minutes"]["5"] = "BEARISH"
    assert trend_rank.read(mixed, [3, 5]) is None
    assert trend_rank.read(_row("C", "BULLISH", 4, 0.3), [3, 5], kind="STRONG") is None
    assert trend_rank.read(_row("D", "BEARISH", 4, 0.1, peak=0.4), [3, 5])["fading"] is True


def test_rank_puts_fresh_and_wide_first():
    rows = [
        _row("OLD_WIDE", "BULLISH", 60, 0.9),
        _row("FRESH_WIDE", "BULLISH", 3, 0.6),
        _row("FRESH_THIN", "BEARISH", 2, 0.05),
        _row("OLD_THIN", "BEARISH", 50, 0.04),
    ]
    order = [r["symbol"] for r in trend_rank.ranked(rows, [3, 5])]
    assert order[0] == "FRESH_WIDE" and order[-1] == "OLD_THIN"
    assert [r["rank"] for r in trend_rank.ranked(rows, [3, 5])] == [1, 2, 3, 4]


def test_alert_sends_top_ranks_once_a_day(monkeypatch, tmp_path):
    monkeypatch.setenv("TREND_ALERT_FILE", str(tmp_path / "ta.json"))
    trend_rank._SENT.clear()
    trend_rank.recent.clear()
    trend_rank.save({"on": True, "sizes": [3, 5], "top_n": 2, "max_age_min": 60, "min_gap_pct": 0.1, "call": True})
    rows = [
        _row("FRESH", "BULLISH", 3, 0.6),
        _row("THIN", "BULLISH", 2, 0.05),
        _row("OLD", "BULLISH", 40, 0.5),
        _row("FADED", "BEARISH", 2, 0.2, peak=0.8),
    ]
    sent, calls = [], []

    async def tg(text):
        sent.append(text)

    async def call(text):
        calls.append(text)

    now = _at(11, 0)
    picks = asyncio.run(trend_rank.after_scan({"rows": rows}, tg, call, now=now))
    assert [p["symbol"] for p in picks] == ["FRESH"]  # THIN under the gap, OLD over the age, FADED fading
    assert len(sent) == 1 and "FRESH" in sent[0] and "nothing is traded" in sent[0]
    assert len(calls) == 1 and "FRESH" in calls[0]
    assert asyncio.run(trend_rank.after_scan({"rows": rows}, tg, call, now=now)) == []  # once a day
    assert asyncio.run(trend_rank.after_scan({"rows": rows}, tg, call, now=_at(16, 0))) == []  # market closed
    trend_rank.save({"on": False})
    assert trend_rank.due(rows, trend_rank.load(), _at(11, 5)) == []


def test_trend_alert_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TREND_ALERT_FILE", str(tmp_path / "ta.json"))
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/chop-scan/trend-alerts").json()
        assert got["on"] is False and got["top_n"] == 5
        assert client.put("/api/chop-scan/trend-alerts", json={"on": True}).status_code == 400
        ok = client.put("/api/chop-scan/trend-alerts", json={"on": True, "sizes": [5, 15], "top_n": 3, "call": True})
        assert ok.status_code == 200 and ok.json()["sizes"] == [5, 15] and ok.json()["call"] is True
        assert client.put("/api/chop-scan/trend-alerts", json={"top_n": 99}).status_code == 422
    database.reset_engine()
