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


def _picks(monkeypatch, calls: list):
    """Fixed picks per day (the pick arithmetic has its own tests): Monday TCS, Tuesday INFY, Wednesday TCS."""
    import replay as replay_mod

    plan = {MON: "TCS", TUE: "INFY", WED: "TCS"}

    def fake_picks(frames, day, rule):  # noqa: ARG001
        calls.append((day, sorted(frames)))
        return [{"symbol": plan[day], "score": 1}]

    monkeypatch.setattr(replay_mod, "picks_for_day", fake_picks)


@pytest.mark.asyncio
async def test_a_scalp_page_backtest_resumes_after_a_deploy(db, monkeypatch):  # noqa: F811
    """A strong-trend pick run cut by a deploy resumes: the whole stock list it picks from is saved on the run, the
    finished day keeps its picks and trades, and the days left pick again and play as an unbroken run would."""
    import trend_picks
    from database import session_factory
    from models import ReplayRun
    from replay import ReplaySession, close_orphan_replay_rows, get_run, resumable_runs

    _fast(monkeypatch)
    calls: list = []
    _picks(monkeypatch, calls)
    rule = lambda: trend_picks.TrendPickRule(pick_time=dt.time(9, 30), top_n=1, minutes=5)  # noqa: E731

    clean = ReplaySession()
    await clean.begin(object(), ["TCS", "INFY"], MON, dt.time(9, 30), 1_000_000, end_day=WED, settings=SETTINGS, rule=rule())
    await _until(clean, lambda: clean.status == "FINISHED")
    expected = _by_day(clean.info()["run_id"])
    await clean.stop()

    cut = ReplaySession()
    await cut.begin(object(), ["TCS", "INFY"], MON, dt.time(9, 30), 1_000_000, end_day=WED, settings=SETTINGS, rule=rule())
    await _until(cut, lambda: cut.day_index == 1 and cut.engine is not None and cut.engine.feed.clock.time() > dt.time(10, 0))
    run_id = cut.info()["run_id"]
    cut._task.cancel()
    await asyncio.sleep(0.05)
    close_orphan_replay_rows(interrupted=True)

    offer = resumable_runs(1)
    assert offer and offer[0]["id"] == run_id and offer[0]["kind"] == "TREND"
    # The page gets a flag, not the saved stock list.
    page = get_run(run_id)["settings"]["scalp_pick"]
    assert page["resumable"] is True and "universe_symbols" not in page

    calls.clear()
    again = ReplaySession()
    await again.resume(object(), run_id, 1_000_000)
    assert again.rule is not None and again.rule.kind == "TREND" and again.rule.pick_time == dt.time(9, 30)
    assert list(again.picks) == [MON.isoformat()]  # Monday's picks kept, Tuesday's dropped with its half day
    await _until(again, lambda: again.status == "FINISHED")
    # Only the days left were picked again, from the whole saved list.
    assert calls == [(TUE, ["INFY", "TCS"]), (WED, ["INFY", "TCS"])]
    with session_factory()() as s:
        run = s.get(ReplayRun, run_id)
        assert (run.status, run.days_done) == ("FINISHED", 3)
    assert sorted(get_run(run_id)["settings"]["scalp_pick"]["picks"]) == [MON.isoformat(), TUE.isoformat(), WED.isoformat()]
    got = _by_day(run_id)
    assert [(d, [t[0] for t in v], [t[3] for t in v]) for d, v in sorted(got.items())] == [
        (d, [t[0] for t in v], [t[3] for t in v]) for d, v in sorted(expected.items())
    ]
    await again.stop()


@pytest.mark.asyncio
async def test_an_old_scalp_page_run_without_its_stock_list_is_not_offered(db, monkeypatch):  # noqa: F811
    import json

    from database import session_factory
    from models import ReplayRun
    from replay import ReplaySession, resumable_runs

    _fast(monkeypatch)
    with session_factory()() as s:
        run = ReplayRun(
            created_at=dt.datetime(2026, 10, 1, 16, 0), start_date=MON.isoformat(), end_date=WED.isoformat(),
            start_time="09:30", symbols="TCS",
            settings=json.dumps({**SETTINGS, "scalp_pick": {"kind": "TREND", "pick_time": "09:30", "picks": {}}}),
            status="INTERRUPTED", days_total=3, days_done=1, days=",".join(d.isoformat() for d in (MON, TUE, WED)),
        )
        s.add(run)
        s.commit()
        run_id = run.id
    assert resumable_runs(1) == []
    with pytest.raises(ValueError, match="started before"):
        await ReplaySession().resume(object(), run_id, 60)


def test_each_pick_rule_comes_back_from_its_saved_form():
    import active_picks
    import auto_arm
    import trend_picks
    from scalp_picks import PickRule, rule_from_dict

    trend = trend_picks.TrendPickRule(pick_time=dt.time(10, 15), top_n=4, direction="BEARISH", minutes=15, sma_fast=5, sma_slow=13)
    assert rule_from_dict(trend.as_dict()).as_dict() == trend.as_dict()
    active = active_picks.ActivePickRule(pick_time=dt.time(11, 0), top_n=7, rank="speed", bias="LONG", min_value_cr=12.5)
    assert rule_from_dict(active.as_dict()).as_dict() == active.as_dict()
    arm = auto_arm.AutoArmRule(source="ACTIVE", first=dt.time(9, 45), last=dt.time(13, 0), every_min=10, pct=80.0,
                               max_day=4, price_min=100.0, active_hits={"2026-09-29": [("10:05", "TCS", "BUY", 81.0)]})
    saved = {**arm.as_dict(), "active_hits": {"2026-09-29": [["10:05", "TCS", "BUY", 81.0]]}}
    back = rule_from_dict(saved, 9, 21)
    assert back.as_dict() == arm.as_dict() and back.active_hits == arm.active_hits
    scalp = PickRule(pick_time=dt.time(9, 50), top_n=2, require_bias=False)
    assert rule_from_dict(scalp.as_dict()).as_dict() == scalp.as_dict()
