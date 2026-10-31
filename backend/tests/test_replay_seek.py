"""Replay rewind / fast-forward (ReplaySession.seek): the slider moves the replayed day's clock."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from groww_client import IST
from replay import ReplaySession
from tests.test_replay import DAY, _frames, db  # noqa: F401 - fixture


def _at(h, m):
    return dt.datetime.combine(DAY, dt.time(h, m), tzinfo=IST)


async def _until(session, pred, tries=400):
    for _ in range(tries):
        await asyncio.sleep(0.02)
        if pred():
            return True
    return False


@pytest.mark.asyncio
async def test_fast_forward_and_rewind_keep_the_same_trades(db, monkeypatch):
    import replay as replay_mod
    from database import session_factory
    from models import TradeLog

    frames = _frames()

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return frames[symbol].copy()

    monkeypatch.setattr("replay.fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)
    session = ReplaySession()
    await session.begin(object(), ["TCS"], DAY, dt.time(9, 15), 1)
    assert await _until(session, lambda: session.status == "PLAYING" and session.engine is not None)
    session.control("pause")
    with pytest.raises(ValueError):
        session.seek("nonsense")

    # Forward while paused: the clock jumps to the target on the grid and the replay stays paused.
    target = session.seek("11:00")
    assert target == _at(11, 0) and session.info()["seeking"] == target.isoformat()
    assert await _until(session, lambda: session.engine.feed.clock >= _at(11, 0) and session.info()["seeking"] is None)
    assert session.status == "PAUSED"
    clock_after = session.engine.feed.clock
    assert _at(11, 0) <= clock_after < _at(11, 1)
    with session_factory()() as s:
        forward_rows = [(r.symbol, r.direction, r.entry_time, r.exit_reason) for r in s.query(TradeLog).order_by(TradeLog.id).all()]
    await asyncio.sleep(0.1)
    assert session.engine.feed.clock == clock_after  # still paused

    # Back: the day restarts from 09:15 and catches up to 10:00; the same candles give the same trades.
    first_engine = session.engine
    target = session.seek("10:00")
    assert target == _at(10, 0)
    assert await _until(session, lambda: session.engine is not first_engine and session.info()["seeking"] is None and session.engine.feed.clock >= _at(10, 0))
    assert session.status == "PAUSED"
    assert _at(10, 0) <= session.engine.feed.clock < _at(10, 1)
    with session_factory()() as s:
        back_rows = [(r.symbol, r.direction, r.entry_time, r.exit_reason) for r in s.query(TradeLog).order_by(TradeLog.id).all()]
    # Nothing booked after 10:00 is left; what was booked before it is the same as the first pass had.
    assert all(e < dt.datetime(DAY.year, DAY.month, DAY.day, 10, 0) for _, _, e, _ in back_rows)
    same = lambda rows: [(sym, side, entry) for sym, side, entry, _ in rows]  # noqa: E731 - an exit after 10:00 is not replayed yet
    assert same(back_rows) == same([r for r in forward_rows if r[2] < dt.datetime(DAY.year, DAY.month, DAY.day, 10, 0)])

    # The target is clamped to the day: past the close it stops just before 15:30, and the day finishes on play.
    target = session.seek("23:00")
    assert target == _at(15, 30) - dt.timedelta(seconds=replay_mod.STEP_SECONDS)
    assert await _until(session, lambda: session.info()["seeking"] is None)
    assert session.engine.feed.clock >= target
    await session.stop()


@pytest.mark.asyncio
async def test_seek_needs_a_playing_replay(db):
    session = ReplaySession()
    with pytest.raises(ValueError):
        session.seek("10:00")
