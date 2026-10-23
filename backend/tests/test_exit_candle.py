"""Mixed candles (exit_candle.py): enter on the bot's candle, exit on the SMA cross of another candle size.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import exit_candle
from strategy_engine import cross_exits
from tests.test_counter_candles import TURN, _trades
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _frame(fast_slow):
    rows = [{"ts": i * 60, "fast": f, "slow": s} for i, (f, s) in enumerate(fast_slow)]
    return pd.DataFrame(rows)


def test_minutes_and_active():
    assert exit_candle.minutes(Cfg(exit_candle_minutes=5)) == 5
    assert exit_candle.minutes(Cfg(exit_candle_minutes=7)) == 0  # not an allowed size
    assert exit_candle.minutes(Cfg()) == 0
    assert exit_candle.active(Cfg(exit_candle_minutes=5, cross_exit=True), 1)
    assert not exit_candle.active(Cfg(exit_candle_minutes=5, cross_exit=True), 5)  # same as the entry candle
    assert not exit_candle.active(Cfg(exit_candle_minutes=5, cross_exit=False), 1)  # needs the cross exit on
    assert not exit_candle.active(Cfg(exit_candle_minutes=0, cross_exit=True), 1)


def test_cross_exits_moves_off_the_bot_candle():
    assert cross_exits(Cfg(cross_exit=True, candle_minutes=1, exit_candle_minutes=0))
    assert not cross_exits(Cfg(cross_exit=True, candle_minutes=1, exit_candle_minutes=15))
    assert cross_exits(Cfg(cross_exit=True, candle_minutes=5, exit_candle_minutes=5))
    assert not cross_exits(Cfg(cross_exit=False, candle_minutes=1, exit_candle_minutes=0))


def test_crossed_against_reads_only_the_last_closed_candle():
    assert exit_candle.crossed_against(_frame([(10, 9), (8, 9)]), "LONG")[0]
    assert not exit_candle.crossed_against(_frame([(10, 9), (8, 9)]), "SHORT")[0]
    assert exit_candle.crossed_against(_frame([(8, 9), (10, 9)]), "SHORT")[0]
    assert not exit_candle.crossed_against(_frame([(8, 9), (7, 9)]), "LONG")[0]  # already below: no fresh cross
    assert not exit_candle.crossed_against(_frame([(float("nan"), 9), (8, 9)]), "LONG")[0]


def test_closed_frame_groups_the_tape_and_drops_the_forming_candle():
    base = 1_790_000_000 - 1_790_000_000 % 86400 + 3 * 3600 + 45 * 60  # 09:15 IST of some day
    tape = pd.DataFrame(
        [{"ts": base + i * 60, "open": 100 + i, "high": 100 + i, "low": 100 + i, "close": 100 + i} for i in range(31)]
    )
    frame = exit_candle.closed_frame(tape, 15, 2, 3)
    assert list(frame["ts"]) == [base, base + 900]  # the 09:45 candle (one minute so far) is forming
    assert list(frame["close"]) == [114, 129]


@pytest.mark.asyncio
async def test_short_held_through_1_minute_wiggles_exits_on_the_15_minute_cross(db):  # noqa: F811
    plain = await _trades(db)
    mixed = await _trades(db, exit_candle_minutes=15)
    mid = dt.datetime(2026, 9, 22, 11, 0)
    p = next(t for t in plain if t["entry_time"] <= mid < t["exit_time"])
    m = next(t for t in mixed if t["entry_time"] <= mid < t["exit_time"])
    assert p["direction"] == m["direction"] == "SHORT"
    assert p["exit_reason"] == "MA_CROSS" and m["exit_reason"] == exit_candle.REASON
    assert m["exit_time"] > p["exit_time"] > TURN  # the slower candle turns later
    # The flat opening chop: the 1-minute cross exit flips every minute, the 15-minute exit does not.
    assert len(mixed) < len(plain) / 5


@pytest.mark.asyncio
async def test_a_trade_against_the_exit_candle_closes_once_its_own_candle_turns(db):  # noqa: F811
    mixed = await _trades(db, exit_candle_minutes=15)
    first = mixed[0]
    # Bought at 09:25 while the 15-minute SMAs were bearish: out at the first 15-minute close (09:30), not held all day.
    assert first["direction"] == "LONG" and first["exit_reason"] == exit_candle.REASON
    assert first["exit_time"] <= dt.datetime(2026, 9, 22, 9, 31)


@pytest.mark.asyncio
async def test_same_size_or_cross_exit_off_changes_nothing(db):  # noqa: F811
    plain = await _trades(db)
    same = await _trades(db, exit_candle_minutes=1)
    assert [(t["entry_time"], t["exit_reason"]) for t in same] == [(t["entry_time"], t["exit_reason"]) for t in plain]
    off = await _trades(db, cross_exit=False, exit_candle_minutes=15)
    assert off and all(t["exit_reason"] != exit_candle.REASON for t in off)  # needs the SMA cross exit on


def test_api_saves_the_exit_candle(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "exit_candle_minutes" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["exit_candle_minutes"] == 0
        ok = client.put("/api/config", json={"exit_candle_minutes": 15})
        assert ok.status_code == 200 and ok.json()["exit_candle_minutes"] == 15
        assert client.put("/api/config", json={"exit_candle_minutes": 7}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"exit_candle_minutes": 5})
        assert own.status_code == 200 and own.json()["exit_candle_minutes"] == 5
    database.reset_engine()


def test_judge_needs_both_sizes_against_without_a_fresh_cross():
    exit_frame = _frame([(8, 9), (7, 9)])  # exit candle bearish, no fresh cross
    down = pd.DataFrame({"close": [110 - i for i in range(30)]})
    up = pd.DataFrame({"close": [80 + i for i in range(30)]})
    assert exit_candle.judge(exit_frame, down, "LONG", 9, 21)[0]
    assert not exit_candle.judge(exit_frame, up, "LONG", 9, 21)[0]
    assert not exit_candle.judge(_frame([(10, 9), (11, 9)]), down, "LONG", 9, 21)[0]  # exit candle still bullish
