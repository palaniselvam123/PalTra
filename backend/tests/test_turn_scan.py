"""Turning scan (turn_scan.py): candles against the SMA trend before the SMAs cross. Read-only."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import turn_scan
from tests.test_gap_mode import _day

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 11, 2)


def _frame(up_from: int = 300) -> pd.DataFrame:
    """A falling day (SMA 9 under SMA 21) that turns up at minute `up_from` (14:15 at 300)."""
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        if i < up_from:
            return p - 0.10
        return p + 0.15

    return pd.DataFrame(_day(DAY - dt.timedelta(days=3), flat) + _day(DAY, path))


def _at(h: int, m: int) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(h, m), tzinfo=IST)


def test_a_falling_stock_turning_up_before_the_cross_is_listed():
    row = turn_scan.read(_frame(), 9, 21, 5, _at(14, 35), symbol="ICICIBANK", count=3)
    assert row is not None
    assert row.trend == "BEARISH" and row.turn == "LONG"
    assert row.counter_candles >= 3 and row.counter_since == "14:15"
    assert row.gap_pct < 0 and row.gap_closed_pct is not None and row.gap_closed_pct > 0
    assert row.candles_to_cross is not None and row.candles_to_cross > 0
    assert row.move_since_turn_pct > 0 and row.day_move_pct > 0  # down since the open, signed the trend's way
    assert row.close > row.sma_fast  # back above SMA 9


def test_not_listed_before_enough_counter_candles_or_while_the_trend_runs():
    assert turn_scan.read(_frame(), 9, 21, 5, _at(14, 25), count=3) is None  # only 2 green candles closed
    assert turn_scan.read(_frame(), 9, 21, 5, _at(13, 0), count=3) is None  # still falling
    assert turn_scan.read(_frame(), 9, 21, 5, _at(14, 25), count=2) is not None


def test_the_rule_and_fast_sma_choices_apply():
    # A 1-candle bounce that has not reached SMA 9 yet: listed only without the fast-SMA check.
    early = _at(14, 20)
    assert turn_scan.read(_frame(), 9, 21, 5, early, count=1, need_fast=True) is None
    assert turn_scan.read(_frame(), 9, 21, 5, early, count=1, need_fast=False) is not None
    assert turn_scan.read(_frame(), 9, 21, 5, _at(14, 35), count=3, rule="BOTH") is not None


def test_alert_memory_sends_each_stock_once_a_day():
    mem = turn_scan.AlertMemory()
    rows = [{"symbol": "A", "turn": "LONG"}, {"symbol": "B", "turn": "SHORT"}]
    now = _at(14, 40)
    assert len(mem.fresh(rows, now)) == 2 and mem.fresh(rows, now) == []
    assert len(mem.fresh(rows, now + dt.timedelta(days=1))) == 2
    assert "A up against a bearish trend" in turn_scan.alert_text(
        [{"symbol": "A", "turn": "LONG", "counter_candles": 3, "counter_since": "14:15", "minutes_to_cross": 10.0}], 5
    )


@pytest.mark.asyncio
async def test_scanner_runs_one_pass_and_sorts_soonest_first():
    frames = {"FAST": _frame(up_from=290), "SLOW": _frame(up_from=300), "NONE": _frame(up_from=400)}

    async def fetch(symbol, start, end):
        return frames[symbol]

    scanner = turn_scan.TurnScanner()
    got = []
    scanner.on_done = lambda rows, minutes: got.append([r["symbol"] for r in rows])
    scanner.start(list(frames), fetch, minutes=5, count=3, now=_at(14, 40))
    await scanner._task
    snap = scanner.snapshot()
    assert not snap["running"] and snap["error"] is None
    assert [r["symbol"] for r in snap["rows"]] == got[0]
    assert set(got[0]) == {"FAST", "SLOW"}
    assert snap["rows"][0]["candles_to_cross"] <= snap["rows"][-1]["candles_to_cross"]
    with pytest.raises(ValueError):
        scanner.start(["X"], fetch, minutes=2)


def test_api_starts_and_reads_the_turning_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/turn-scan").json()
        assert got["running"] is False and got["count"] == 3 and got["alert"] is False
        assert client.post("/api/turn-scan/start", json={"symbols": ["SBIN"], "count": 11}).status_code == 422
        assert client.post("/api/turn-scan/start", json={"symbols": ["SBIN"], "rule": "X"}).status_code == 422
    database.reset_engine()
