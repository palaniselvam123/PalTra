"""Scalp-pick backtest: past picks use only what was known at the pick time,
rank like the live page, and the bot trades only the picks. Practice only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

from groww_client import IST
from scalp_picks import PickRule, bars_known_at, picks_for_day
from tests.test_replay import _wave_day, db  # noqa: F401

DAY = dt.date(2026, 9, 29)
PREV = dt.date(2026, 9, 28)


def _trend_day(day: dt.date, base: float, step: float, span: float, per_min: int) -> pd.DataFrame:
    rows = []
    t0 = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for i in range(375):
        o = base + step * i
        c = o + step
        rows.append(
            {
                "ts": int((t0 + dt.timedelta(minutes=i)).timestamp()),
                "open": o,
                "high": max(o, c) + span / 2,
                "low": min(o, c) - span / 2,
                "close": c,
                "volume": per_min * (i + 1),  # Groww: running session total
            }
        )
    return pd.DataFrame(rows)


def test_the_pick_sees_only_what_was_known_at_the_pick_minute():
    frame = pd.concat([_wave_day(dt.date(2026, 9, 25)), _wave_day(PREV), _wave_day(DAY)]).reset_index(drop=True)
    bars = bars_known_at(frame, DAY, dt.time(9, 45))
    cut = int(dt.datetime.combine(DAY, dt.time(9, 45), tzinfo=IST).timestamp())
    last = bars[-1]
    assert last.ts == cut
    row = frame[frame["ts"] == cut].iloc[0]
    # The pick minute is known only by its open; how it ends is the future.
    assert last.open == last.high == last.low == last.close == pytest.approx(row["open"]) and last.volume == 0
    assert all(b.ts < cut for b in bars[:-1])
    # The previous trading day is included for warm-up; older days are not.
    first_day = dt.datetime.fromtimestamp(bars[0].ts, IST).date()
    assert first_day == PREV
    # Per-minute volume, not Groww's running total.
    assert {b.volume for b in bars[1:-1]} <= {0, 1000}


def test_picks_are_the_top_ready_stocks_with_a_bias():
    frames = {
        "MOVER": pd.concat([_trend_day(PREV, 980, 0.05, 1.5, 20_000), _trend_day(DAY, 1000, 0.5, 2.0, 20_000)]),
        "FLAT": pd.concat([_trend_day(PREV, 500, 0.0, 0.02, 20_000), _trend_day(DAY, 500, 0.0, 0.02, 20_000)]),
        "THIN": pd.concat([_trend_day(PREV, 980, 0.05, 1.5, 5), _trend_day(DAY, 1000, 0.5, 2.0, 5)]),
    }
    frames = {k: v.reset_index(drop=True) for k, v in frames.items()}
    picks = picks_for_day(frames, DAY, PickRule(dt.time(9, 45), top_n=2, min_atr_pct=0.05, min_value_cr=1.0))
    assert [p["symbol"] for p in picks] == ["MOVER"]
    assert picks[0]["bias"] == "LONG" and picks[0]["score"] > 0
    # No candle that day: nothing to pick.
    assert picks_for_day(frames, dt.date(2026, 9, 30), PickRule()) == []


@pytest.mark.asyncio
async def test_each_day_trades_only_its_picks_from_the_pick_time(db, monkeypatch):  # noqa: F811
    import json

    import replay as replay_mod
    from database import session_factory
    from models import ReplayRun, TradeLog
    from replay import ReplaySession

    days = (dt.date(2026, 9, 25), dt.date(2026, 9, 28), dt.date(2026, 9, 29))
    bases = {"TCS": 1000.0, "INFY": 1500.0}

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.concat([_wave_day(d, base=bases[symbol]) for d in days]).reset_index(drop=True)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(11, 30))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)

    rule = PickRule(dt.time(9, 45), top_n=1, min_atr_pct=0.0, min_value_cr=0.0, require_bias=False)
    session = ReplaySession()
    await session.begin(
        object(), ["TCS", "INFY"], days[1], dt.time(9, 45), 1_000_000, end_day=days[2], settings={"qty": 10}, rule=rule
    )
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    info = session.info()
    assert set(info["picks"]) == {days[1].isoformat(), days[2].isoformat()}
    assert all(len(p) == 1 for p in info["picks"].values())

    with session_factory()() as s:
        run = s.get(ReplayRun, info["run_id"])
        stored = json.loads(run.settings)["scalp_pick"]
        assert stored["pick_time"] == "09:45" and stored["top_n"] == 1 and stored["universe"] == 2
        assert stored["picks"] == info["picks"]
        picked = {p["symbol"] for ps in info["picks"].values() for p in ps}
        assert set(run.symbols.split(",")) == picked
        trades = s.query(TradeLog).filter(TradeLog.run_id == run.id).all()
    assert trades, "the wave should trade after 09:45"
    for t in trades:
        day_picks = {p["symbol"] for p in info["picks"][t.date]}
        assert t.symbol in day_picks
        assert t.entry_time.time() >= dt.time(9, 45)


def test_api_checks_the_pick_request(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main
    from models import BotConfig

    seen = {}

    async def fake_begin(broker, symbols, day, start, speed, end_day=None, settings=None, rule=None):
        seen.update(symbols=symbols, start=start, rule=rule, day=day, end_day=end_day)

    monkeypatch.setattr(main.engine.broker, "token", "test-token")
    monkeypatch.setattr(main.replay, "begin", fake_begin)
    body = {"date": "2026-09-28", "end_date": "2026-10-02", "universe": ["tcs", "INFY", "TCS"], "pick_time": "10:00", "top_n": 2}
    with TestClient(main.app) as client:
        assert client.post("/api/replay/scalp-picks", json=body).status_code == 200
        assert seen["symbols"] == ["TCS", "INFY"] and seen["start"] == dt.time(10, 0)
        assert seen["rule"].top_n == 2 and seen["rule"].pick_time == dt.time(10, 0)
        assert client.post("/api/replay/scalp-picks", json={**body, "pick_time": "15:10"}).status_code == 400
        assert client.post("/api/replay/scalp-picks", json={**body, "universe": ["A-B"]}).status_code == 400
        assert client.post("/api/replay/scalp-picks", json={**body, "top_n": 50}).status_code == 422
        with database.session_factory()() as s:
            s.get(BotConfig, 1).trading_mode = "LIVE"
            s.commit()
        res = client.post("/api/replay/scalp-picks", json=body)
        assert res.status_code == 409 and "PAPER" in res.json()["detail"]
    database.reset_engine()
    get_settings.cache_clear()
