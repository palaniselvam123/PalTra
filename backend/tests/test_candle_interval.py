"""Candle interval (candle_minutes): the bot trades on 2-15 minute candles built from the 1-minute tape."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from candles import bucket_start, candle_minutes, resample
from groww_client import IST
from strategy_engine import _candle_is_behind
from test_replay import DAY, _frames, _run_day, db  # noqa: F401  (db is a fixture)


def _t(h: int, m: int, s: int = 0) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(h, m, s), tzinfo=IST)


def test_candles_are_counted_from_0915_with_the_forming_one_last():
    t0 = int(_t(9, 15).timestamp())
    rows = [
        {"ts": t0 + 60 * i, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "volume": 10 * (i + 1)}
        for i in range(12)  # 09:15 .. 09:26, 09:26 forming
    ]
    out = resample(pd.DataFrame(rows), 5)
    assert [dt.datetime.fromtimestamp(t, IST).strftime("%H:%M") for t in out["ts"]] == ["09:15", "09:20", "09:25"]
    first = out.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"]) == (100, 105, 99, 104.5)
    assert first["volume"] == 50  # the running session total at the candle's last minute
    assert out.iloc[-1]["close"] == 111.5  # the forming candle follows the newest minute
    assert resample(pd.DataFrame(rows), 1) is not None and len(resample(pd.DataFrame(rows), 1)) == 12


def test_10_and_15_minute_candles_and_the_bucket_start():
    assert bucket_start(int(_t(9, 29, 59).timestamp()), 15) == int(_t(9, 15).timestamp())
    assert bucket_start(int(_t(9, 30).timestamp()), 15) == int(_t(9, 30).timestamp())
    assert bucket_start(int(_t(15, 29).timestamp()), 10) == int(_t(15, 25).timestamp())
    assert bucket_start(int(_t(10, 0, 30).timestamp()), 1) == int(_t(10, 0).timestamp())


def test_the_interval_setting_and_its_limits():
    class Cfg:
        candle_minutes = 5
        entry_mode = "SMA"

    assert candle_minutes(Cfg()) == 5
    assert candle_minutes(Cfg(), {"candle_minutes": 15}) == 15  # a stock's own setting
    assert candle_minutes(Cfg(), {"entry_mode": "PATTERN"}) == 1  # patterns keep the 1-minute tape
    Cfg.candle_minutes = 7
    assert candle_minutes(Cfg()) == 1  # not an offered interval
    Cfg.candle_minutes = None
    assert candle_minutes(Cfg()) == 1


def test_a_closed_candle_is_behind_only_after_its_interval():
    closed = int(_t(9, 20).timestamp())  # the 09:20-09:24 five-minute candle
    assert not _candle_is_behind(closed, _t(9, 25, 30), 5)
    assert not _candle_is_behind(closed, _t(9, 29, 59), 5)
    assert _candle_is_behind(closed, _t(9, 30, 5), 5)
    one = int(_t(9, 24).timestamp())
    assert not _candle_is_behind(one, _t(9, 25, 30)) and _candle_is_behind(one, _t(9, 26, 1))


async def _replay(minutes: int):
    import database
    from models import BotConfig, TradeLog
    from replay import ReplayEngine, ReplayFeed

    with database.session_factory()() as s:
        s.get(BotConfig, 1).candle_minutes = minutes
        s.query(TradeLog).delete()
        s.commit()
    engine = ReplayEngine(ReplayFeed(_frames(), _t(9, 15)), ["TCS"])
    engine.load_config()
    await engine.tick(_t(9, 15))
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    await _run_day(engine, dt.time(15, 20))
    with database.session_factory()() as s:
        return engine, s.query(TradeLog).order_by(TradeLog.id).all()


@pytest.mark.asyncio
async def test_a_5_minute_bot_trades_only_when_a_5_minute_candle_closes(db):  # noqa: F811
    engine, rows = await _replay(5)
    assert rows, "the sine wave should cross on 5-minute candles too"
    # Entries and cross exits go out in the first minute of the next 5-minute candle.
    for r in rows:
        assert r.entry_time.minute % 5 == 0, r.entry_time
        if r.exit_reason == "MA_CROSS":
            assert r.exit_time.minute % 5 == 0, r.exit_time
    frame = engine._frames["TCS"]
    gaps = frame["ts"].diff().dropna()
    assert (gaps[gaps < 3600] == 300).all()  # the bot only ever saw 5-minute candles
    assert engine.snapshot()["candle_minutes"] == 5


@pytest.mark.asyncio
async def test_one_minute_is_unchanged(db):  # noqa: F811
    _, one = await _replay(1)
    _, again = await _replay(1)
    assert [(r.entry_time, r.direction) for r in one] == [(r.entry_time, r.direction) for r in again]
    assert any(r.entry_time.minute % 5 for r in one)  # 1-minute crosses land on any minute


def test_api_sets_the_interval_for_the_bot_and_one_stock(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/config").json()["candle_minutes"] == 1  # default
        assert client.put("/api/config", json={"candle_minutes": 7}).status_code == 422
        assert client.put("/api/config", json={"candle_minutes": 5}).json()["candle_minutes"] == 5
        own = client.put("/api/config/stock/TCS", json={"candle_minutes": 15}).json()
        assert own["candle_minutes"] == 15 and own["own"] == {"candle_minutes": 15}
        assert client.get("/api/config").json()["candle_minutes"] == 5
        assert client.get("/api/config").json()["trading_mode"] == "PAPER"
    database.reset_engine()
