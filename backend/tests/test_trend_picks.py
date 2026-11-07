"""Strong-trend pick backtest (trend_picks.py): the Trend quality read at the pick time, top N, and the daily run."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

import trend_picks as tp
from tests.test_gap_mode import _day

IST = tp.IST
DAY = dt.date(2026, 11, 2)


def _frame(step: float, wobble: float = 0.0) -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        return p + step + (wobble if i % 2 else -wobble)

    return pd.DataFrame(_day(DAY - dt.timedelta(days=3), flat) + _day(DAY, path))


def _rule(**over) -> tp.TrendPickRule:
    return tp.TrendPickRule(**{"pick_time": dt.time(11, 0), "top_n": 2, "direction": "BOTH", "minutes": 5, **over})


def test_strong_trends_are_picked_at_the_pick_time_best_first():
    frames = {"UP": _frame(0.15), "DOWN": _frame(-0.15), "FLAT": _frame(0.0, 0.5)}
    got = tp.picks_for_day(frames, DAY, _rule())
    names = {p["symbol"]: p for p in got}
    assert set(names) == {"UP", "DOWN"}
    assert names["UP"]["side"] == "LONG" and names["DOWN"]["side"] == "SHORT"
    assert tp.picks_for_day(frames, DAY, _rule(direction="BULLISH"))[0]["symbol"] == "UP"
    assert [p["symbol"] for p in tp.picks_for_day(frames, DAY, _rule(direction="BEARISH"))] == ["DOWN"]
    assert len(tp.picks_for_day(frames, DAY, _rule(top_n=1))) == 1


def test_nothing_after_the_pick_time_is_read():
    # Flat until 11:00, then a strong rise: at 11:00 it is not a strong trend yet.
    def path(i, p):
        return 1000.0 + (0.02 if i % 2 else -0.02) if i < 105 else p + 0.3

    frame = pd.DataFrame(_day(DAY - dt.timedelta(days=3), lambda i, p: 1000.0) + _day(DAY, path))
    assert tp.picks_for_day({"LATE": frame}, DAY, _rule()) == []
    assert tp.picks_for_day({"LATE": frame}, DAY, _rule(pick_time=dt.time(14, 0)))  # by 14:00 it is


def test_the_scalp_pick_dispatch_uses_the_trend_rule():
    from scalp_picks import picks_for_day

    assert picks_for_day({"UP": _frame(0.15)}, DAY, _rule())[0]["symbol"] == "UP"


def test_auto_settings_and_when_the_daily_run_is_due(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_PICKS_AUTO_FILE", str(tmp_path / "auto.json"))
    s = tp.load()
    assert s.on is False and s.run_at == "15:45" and s.pick_time == "10:00"
    with pytest.raises(ValueError):
        tp.update(run_at="14:00")
    s = tp.update(on=True, top_n=15, direction="BULLISH", bot=3)
    assert s.top_n == 15 and s.bot == 3
    thu = lambda h, m: dt.datetime(2026, 11, 5, h, m, tzinfo=IST)  # noqa: E731
    assert not tp.due(thu(15, 0), s) and tp.due(thu(15, 50), s)
    assert not tp.due(dt.datetime(2026, 11, 7, 16, 0, tzinfo=IST), s)  # Saturday
    s.last_day = "2026-11-05"
    assert not tp.due(thu(16, 0), s)  # once a day


@pytest.mark.asyncio
async def test_the_daily_loop_starts_once_and_notes_it(tmp_path, monkeypatch):
    monkeypatch.setenv("TREND_PICKS_AUTO_FILE", str(tmp_path / "auto.json"))
    tp.update(on=True)
    calls = []

    async def start(s, day):
        calls.append(day)
        return 42, "started"

    now = lambda: dt.datetime(2026, 11, 5, 16, 0, tzinfo=IST)  # noqa: E731
    task = asyncio.create_task(tp.run(start, now_fn=now, poll=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    assert calls == [dt.date(2026, 11, 5)]
    s = tp.load()
    assert s.last_day == "2026-11-05" and s.last_run_id == 42


def test_api_reads_saves_and_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TREND_PICKS_AUTO_FILE", str(tmp_path / "auto.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        assert client.get("/api/trend-picks/auto").json()["on"] is False
        got = client.put("/api/trend-picks/auto", json={"on": True, "top_n": 20, "lists": ["nifty50"]}).json()
        assert got["on"] is True and got["top_n"] == 20 and got["lists"] == ["nifty50"]
        assert client.put("/api/trend-picks/auto", json={"run_at": "10:00"}).status_code == 422
        assert client.put("/api/trend-picks/auto", json={"top_n": 21}).status_code == 422
        bad = client.post("/api/replay/trend-picks", json={"date": "2026-11-02", "universe": ["SBIN"], "pick_time": "15:00"})
        assert bad.status_code == 400
    database.reset_engine()


@pytest.mark.asyncio
async def test_a_replay_trades_only_the_strong_trend_picks_from_the_pick_time(monkeypatch, tmp_path):
    import json

    import replay as replay_mod
    from database import session_factory
    from models import ReplayRun, TradeLog
    from replay import ReplaySession
    from tests.test_gap_mode import db as _db  # noqa: F401

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    import database

    database.reset_engine()
    database.init_db()
    days = (dt.date(2026, 9, 25), dt.date(2026, 9, 28))

    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        step = 0.15 if symbol == "UP" else 0.0
        wobble = 0.0 if symbol == "UP" else 0.5
        rows = _day(days[0], flat) + _day(days[1], lambda i, p: p + step + (wobble if i % 2 else -wobble))
        return pd.DataFrame(rows)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(11, 30))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)

    rule = tp.TrendPickRule(pick_time=dt.time(10, 30), top_n=1, direction="BULLISH", minutes=1)
    session = ReplaySession()
    await session.begin(object(), ["UP", "WAVE"], days[1], dt.time(10, 30), 1_000_000, end_day=days[1], settings={"qty": 10}, rule=rule)
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    picks = session.info()["picks"][days[1].isoformat()]
    assert [p["symbol"] for p in picks] == ["UP"]
    with session_factory()() as s:
        run = s.get(ReplayRun, session.run_id)
        assert json.loads(run.settings)["scalp_pick"]["kind"] == "TREND"
        trades = s.query(TradeLog).filter(TradeLog.run_id == run.id).all()
    assert all(t.symbol == "UP" and t.entry_time.time() >= dt.time(10, 30) for t in trades)
    database.reset_engine()
