"""A replay cut short by a restart (deploy) resumes from the first unfinished day."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

from test_replay import _wave_day, db  # noqa: F401  (db is a fixture)

MON, TUE, WED = dt.date(2026, 9, 28), dt.date(2026, 9, 29), dt.date(2026, 9, 30)
WARM = dt.date(2026, 9, 25)


def _fast(monkeypatch):
    import replay as replay_mod

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.concat([_wave_day(d) for d in (WARM, MON, TUE, WED)]).reset_index(drop=True)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(10, 30))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)


async def _until(session, done) -> None:
    for _ in range(1200):
        await asyncio.sleep(0.02)
        if done():
            return
    raise AssertionError(f"replay stuck at {session.status} {session.error}")


def _by_day(run_id: int) -> dict[str, list[tuple]]:
    from database import session_factory
    from models import TradeLog

    with session_factory()() as s:
        rows = s.query(TradeLog).filter(TradeLog.run_id == run_id).order_by(TradeLog.id).all()
        out: dict[str, list[tuple]] = {}
        for r in rows:
            out.setdefault(r.date, []).append((r.direction, r.entry_time, r.exit_time, round(r.gross_pnl or 0, 2)))
        return out


SETTINGS = {"qty": 10, "stop_type": "ATR", "bot": 1, "bot_name": "Bot 1"}


@pytest.mark.asyncio
async def test_an_interrupted_run_resumes_and_matches_an_unbroken_one(db, monkeypatch):  # noqa: F811
    from database import session_factory
    from models import ReplayRun
    from replay import ReplaySession, close_orphan_replay_rows, resumable_runs

    _fast(monkeypatch)
    clean = ReplaySession()
    await clean.begin(object(), ["TCS"], MON, dt.time(9, 15), 1_000_000, end_day=WED, settings=SETTINGS)
    await _until(clean, lambda: clean.status == "FINISHED")
    expected = _by_day(clean.info()["run_id"])
    await clean.stop()

    cut = ReplaySession()
    await cut.begin(object(), ["TCS"], MON, dt.time(9, 15), 1_000_000, end_day=WED, settings=SETTINGS)
    # Into the second day, then the "deploy": the task dies without a clean stop.
    await _until(cut, lambda: cut.day_index == 1 and cut.engine is not None and cut.engine.feed.clock.time() > dt.time(10, 0))
    run_id = cut.info()["run_id"]
    cut._task.cancel()
    await asyncio.sleep(0.05)
    close_orphan_replay_rows(interrupted=True)  # what boot does
    with session_factory()() as s:
        run = s.get(ReplayRun, run_id)
        assert (run.status, run.days_done, run.days_total) == ("INTERRUPTED", 1, 3)

    # Settings changed after the cut: the resume still plays the run's saved ones.
    from models import BotConfig, TradeLog

    with session_factory()() as s:
        s.get(BotConfig, 1).qty = 99
        s.commit()

    offer = resumable_runs(1)
    assert offer and offer[0]["id"] == run_id and offer[0]["next_day"] == TUE.isoformat()
    assert resumable_runs(2) == []  # another bot's desk does not offer it

    again = ReplaySession()
    await again.resume(object(), run_id, 1_000_000)
    assert again.info()["day_index"] == 1
    await _until(again, lambda: again.status == "FINISHED")
    with session_factory()() as s:
        run = s.get(ReplayRun, run_id)
        assert (run.status, run.days_done) == ("FINISHED", 3)
    got = _by_day(run_id)
    # Monday kept as it was played; Tuesday replayed afresh (no half-day leftovers); same trades as unbroken.
    assert [(d, [t[0] for t in v], [t[3] for t in v]) for d, v in sorted(got.items())] == [
        (d, [t[0] for t in v], [t[3] for t in v]) for d, v in sorted(expected.items())
    ]
    with session_factory()() as s:
        assert {r.qty for r in s.query(TradeLog).filter(TradeLog.run_id == run_id)} == {10}
    assert resumable_runs(1) == []
    await again.stop()


@pytest.mark.asyncio
async def test_resume_refuses_a_finished_run(db, monkeypatch):  # noqa: F811
    from replay import ReplaySession

    _fast(monkeypatch)
    s = ReplaySession()
    await s.begin(object(), ["TCS"], MON, dt.time(9, 15), 1_000_000, end_day=TUE, settings=SETTINGS)
    await _until(s, lambda: s.status == "FINISHED")
    run_id = s.info()["run_id"]
    await s.stop()
    with pytest.raises(ValueError, match="nothing left"):
        await ReplaySession().resume(object(), run_id, 60)
