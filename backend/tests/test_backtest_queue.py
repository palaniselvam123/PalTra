"""Backtest queue and schedule (backtest_queue.py): one backtest at a time, refusals move on, the daily schedule."""

import asyncio
import datetime as dt

import pytest

import backtest_queue as bq

IST = bq.IST


class FakeSession:
    def __init__(self):
        self.status = "IDLE"
        self.run_id = None
        self.error = ""


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_QUEUE_FILE", str(tmp_path / "q.json"))
    sessions = {b: FakeSession() for b in (1, 2, 3, 4)}
    started: list[dict] = []
    runs: dict[int, str] = {}
    next_id = [100]

    async def start(body, kind="AUTOARM"):
        body = {**body, "_kind": kind}
        if body.get("refuse"):
            raise RuntimeError("Switch Bot 2 to PAPER before replaying its settings.")
        started.append(body)
        s = sessions[int(body["bot"])]
        s.status, s.run_id = "LOADING", None

    def run_status(run_id):
        return runs.get(run_id)

    def assign(bot):
        next_id[0] += 1
        sessions[bot].status, sessions[bot].run_id = "PLAYING", next_id[0]
        runs[next_id[0]] = "RUNNING"
        return next_id[0]

    return {"sessions": sessions, "started": started, "start": start, "run_status": run_status, "runs": runs,
            "assign": assign}


def _step(env, runner, now=None):
    asyncio.run(runner.step(env["start"], lambda b: env["sessions"][b], env["run_status"], now=now))


def _body(bot, day="2026-11-18", **extra):
    return {"date": day, "end_date": day, "bot": bot, "source": "ACTIVE", **extra}


def test_one_backtest_at_a_time_in_order(env):
    st = bq.load()
    bq.add(st, _body(1))
    bq.add(st, _body(2))
    bq.save(st)
    r = bq.Runner()
    _step(env, r)
    assert [b["bot"] for b in env["started"]] == [1]
    rid = env["assign"](1)
    _step(env, r)  # bot 1 still playing: bot 2 waits, the run id is noted
    assert len(env["started"]) == 1
    assert bq.load().items[0].run_id == rid
    env["sessions"][1].status = "FINISHED"
    env["runs"][rid] = "FINISHED"
    _step(env, r)  # bot 1 done, bot 2 starts
    items = bq.load().items
    assert items[0].status == "DONE"
    assert [b["bot"] for b in env["started"]] == [1, 2]
    assert items[1].status == "PLAYING"


def test_waits_while_a_manual_replay_plays(env):
    st = bq.load()
    bq.add(st, _body(1))
    bq.save(st)
    env["sessions"][3].status = "PLAYING"  # someone's own replay on bot 3
    r = bq.Runner()
    _step(env, r)
    assert env["started"] == []
    env["sessions"][3].status = "FINISHED"
    _step(env, r)
    assert len(env["started"]) == 1


def test_a_refusal_fails_the_item_and_the_queue_moves_on(env):
    st = bq.load()
    bq.add(st, _body(2, refuse=True))
    bq.add(st, _body(1))
    bq.save(st)
    r = bq.Runner()
    _step(env, r)
    items = bq.load().items
    assert items[0].status == "FAILED" and "PAPER" in items[0].note
    _step(env, r)
    assert [b["bot"] for b in env["started"]] == [1]


def test_stopped_run_and_paused_queue(env):
    st = bq.load()
    bq.add(st, _body(1))
    bq.add(st, _body(1, day="2026-11-17"))
    st.paused = True
    bq.save(st)
    r = bq.Runner()
    _step(env, r)
    assert env["started"] == []  # paused: nothing starts
    st = bq.load()
    st.paused = False
    bq.save(st)
    _step(env, r)
    rid = env["assign"](1)
    _step(env, r)
    env["sessions"][1].status, env["sessions"][1].run_id = "IDLE", None  # Stop pressed on the replay bar
    env["runs"][rid] = "STOPPED"
    _step(env, r)
    items = bq.load().items
    assert items[0].status == "STOPPED"
    assert items[1].status == "PLAYING"


def test_schedule_queues_today_once_per_ticked_bot(env):
    st = bq.load()
    st.schedule = bq.Schedule(on=True, at="15:45", bots=[1, 3], template={"source": "BOTH", "universe": ["SBIN"]})
    bq.save(st)
    r = bq.Runner()
    before = dt.datetime(2026, 11, 19, 15, 40, tzinfo=IST)  # Thursday, before the time
    _step(env, r, now=before)
    assert bq.load().items == []
    assert bq.due_at(before, bq.load().schedule) == "15:45"
    after = dt.datetime(2026, 11, 19, 15, 50, tzinfo=IST)
    _step(env, r, now=after)
    items = bq.load().items
    assert [(i.body["bot"], i.body["date"], i.origin) for i in items] == [(1, "2026-11-19", "schedule"), (3, "2026-11-19", "schedule")]
    assert items[0].status == "PLAYING" and items[1].status == "QUEUED"
    assert bq.due_at(after, bq.load().schedule) is None
    _step(env, r, now=after + dt.timedelta(minutes=5))
    assert len(bq.load().items) == 2  # not queued twice the same day


def test_schedule_skips_weekends_and_needs_a_template():
    s = bq.Schedule(on=True, at="15:45", bots=[1], template={})
    assert not bq.due(dt.datetime(2026, 11, 19, 16, 0, tzinfo=IST), s)
    s.template = {"source": "ACTIVE"}
    assert bq.due(dt.datetime(2026, 11, 19, 16, 0, tzinfo=IST), s)
    assert not bq.due(dt.datetime(2026, 11, 21, 16, 0, tzinfo=IST), s)  # Saturday


def test_queue_limit(env):
    st = bq.load()
    for _ in range(bq.MAX_QUEUED):
        bq.add(st, _body(1))
    with pytest.raises(ValueError):
        bq.add(st, _body(1))


def test_wake_loop_asks_then_comes_back_at_the_due_time():
    calls: list[dt.datetime] = []
    clock = [dt.datetime(2026, 11, 19, 15, 0, tzinfo=IST)]
    answers = iter([{"due_at": "16:30"}, {"due_at": None}])

    def fetch(path):
        assert path == bq.WAKE_PATH
        calls.append(clock[0])
        return next(answers)

    async def drive():
        task = asyncio.create_task(bq.wake_loop(fetch, now_fn=lambda: clock[0], poll=0))
        for minute in range(0, 120):
            clock[0] = dt.datetime(2026, 11, 19, 15, 0, tzinfo=IST) + dt.timedelta(minutes=minute)
            await asyncio.sleep(0.03)  # the fetch runs in a thread; let each pass finish on this minute
        task.cancel()

    asyncio.run(drive())
    assert [c.strftime("%H:%M") for c in calls] == ["15:31", "16:30"]


def test_a_replay_run_queues_and_starts_as_a_replay(env):
    st = bq.load()
    item = bq.add(st, {"date": "2026-10-14", "end_date": "2026-10-16", "bot": 2, "symbols": ["SBIN", "INFY"],
                       "start": "09:15", "note": "again from run #7"}, kind="REPLAY")
    bq.add(st, _body(1))
    bq.save(st)
    assert item.label == "Bot 2 · replay · 2026-10-14 → 2026-10-16 · SBIN, INFY · again from run #7"
    assert bq.load().items[0].kind == "REPLAY" and bq.load().items[1].kind == "AUTOARM"
    r = bq.Runner()
    _step(env, r)
    assert env["started"][0]["_kind"] == "REPLAY" and env["started"][0]["bot"] == 2
    many = bq.label_for({"date": "2026-10-14", "bot": 1, "symbols": list("ABCDEF")}, "REPLAY")
    assert many.endswith("A, B, C, D +2")


def test_old_queue_file_items_read_as_auto_arm(env, tmp_path):
    import json

    (tmp_path / "q.json").write_text(json.dumps({"items": [{"id": "x1", "body": _body(1), "label": "old"}]}))
    assert bq.load().items[0].kind == "AUTOARM"
