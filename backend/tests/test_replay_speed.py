"""Replay speed (replay.py): a replay's share of each real second counts only its own ticks, so the page's polls
between two ticks no longer slow it down; the backtest server gives replays a larger share while none of its
practice bots run. The 10-second grid is never touched: only how fast real time walks it."""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import replay as replay_mod
from groww_client import IST
from replay import ReplaySession

DAY = dt.date(2026, 9, 29)  # a Tuesday, in the past
START = dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST)
TICK = 1 / 16  # seconds of the fake clock one tick costs (binary fractions, so the sums are exact)


class _Clock:
    """`time` inside replay.py: moves only when a fake tick or a fake page request runs."""

    def __init__(self) -> None:
        self.now = 1024.0

    def monotonic(self) -> float:
        return self.now

    def perf_counter(self) -> float:
        return self.now


class _Asyncio:
    """`asyncio` inside replay.py: notes how many ticks were played at each pause between bursts."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.pauses: list[int] = []

    def __getattr__(self, name):
        return getattr(asyncio, name)

    async def sleep(self, delay, *args, **kwargs):  # noqa: ARG002
        if delay == replay_mod.LOOP_SECONDS:
            self.pauses.append(self.engine.ticks)
        await asyncio.sleep(0)


class _Engine:
    """A replay engine whose tick costs `cost` seconds of the fake clock."""

    def __init__(self, clock: _Clock, cost: float) -> None:
        self.feed = SimpleNamespace(clock=START)
        self.clock, self.cost = clock, cost
        self.ticks = 0
        self.positions: dict = {}
        self.status = "RUNNING"
        self.last_error = ""
        self.halt_reason = ""

    async def tick(self, now) -> None:
        assert now == self.feed.clock and now.second % replay_mod.STEP_SECONDS == 0  # always on the 10-second grid
        self.clock.now += self.cost
        self.ticks += 1


def _setup(monkeypatch, cost: float = TICK):
    clock = _Clock()
    eng = _Engine(clock, cost)
    fake = _Asyncio(eng)
    monkeypatch.setattr(replay_mod, "time", clock)
    monkeypatch.setattr(replay_mod, "asyncio", fake)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 0.4)  # the desk's share; the numbers below are worked out for it
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    session = ReplaySession()
    session.day, session.speed, session.status, session.engine = DAY, 300, "PLAYING", eng
    return clock, eng, fake, session


def _page(clock: _Clock, eng: _Engine, cost: float):
    """One expensive page request (say /api/replay/state) that runs on the event loop right after the first tick."""

    async def run() -> None:
        while eng.ticks < 1:
            await asyncio.sleep(0)
        clock.now += cost

    return asyncio.ensure_future(run())


async def _play(session: ReplaySession, eng: _Engine, clock: _Clock, ticks: int = 30) -> None:
    """300x for one real second earns 300 replay seconds = 30 ticks; the day ends after `ticks` ticks."""
    end = START + dt.timedelta(seconds=ticks * replay_mod.STEP_SECONDS)
    try:
        await session._play_loop(eng, end, clock.now - 1.0, 0.0)
    finally:
        replay_mod._PLAYING.discard(id(session))


@pytest.mark.asyncio
async def test_a_page_request_between_ticks_does_not_count_against_the_replay(monkeypatch):
    clock, eng, fake, session = _setup(monkeypatch, cost=1 / 128)  # 30 ticks = 0.23 s, inside 40 % of a second
    page = _page(clock, eng, cost=5.0)
    await _play(session, eng, clock)
    await page
    # All 30 owed ticks in the first burst: the 5 s request between ticks 1 and 2 is not the replay's own time
    # (it used to end the burst after one tick and drop the owed time, so the replay played slower).
    assert eng.ticks == 30 and fake.pauses == [0]
    assert eng.feed.clock == START + dt.timedelta(minutes=5) and eng.status == "DAY_COMPLETED"


@pytest.mark.asyncio
async def test_the_replays_own_ticks_still_use_up_its_share(monkeypatch):
    clock, eng, fake, session = _setup(monkeypatch)
    await _play(session, eng, clock)
    # 40 % of the one second that passed: the 7th tick takes the burst past 0.4 s, the rest of the owed time is
    # dropped (the replay plays slower rather than piling up), and the next burst carries on from the same grid.
    assert fake.pauses[:2] == [0, 7]
    assert eng.ticks == 30


@pytest.mark.asyncio
async def test_one_burst_never_earns_more_than_a_seconds_share(monkeypatch):
    clock, eng, fake, session = _setup(monkeypatch)
    page = _page(clock, eng, cost=8.0)
    await _play(session, eng, clock)
    await page
    # After the 8 s request the next burst may still tick for only 0.4 s of its own time (BURST_SECONDS), not for
    # 40 % of all 8.4 s, so a busy page cannot make each burst longer than the last.
    assert fake.pauses[:3] == [0, 7, 14]


@pytest.mark.asyncio
async def test_the_backtest_server_gives_replays_a_larger_share(monkeypatch):
    import bots

    clock, eng, fake, session = _setup(monkeypatch)
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    monkeypatch.setattr(bots, "engines", lambda: {1: SimpleNamespace(status="STOPPED", positions={})})
    await _play(session, eng, clock)
    assert fake.pauses[:2] == [0, 13]  # 80 % of the second: the 13th tick takes it past 0.8 s


@pytest.mark.asyncio
async def test_fast_forward_counts_only_its_own_ticks(monkeypatch):
    clock, eng, fake, session = _setup(monkeypatch)
    session._seek = START + dt.timedelta(minutes=5)
    page = _page(clock, eng, cost=5.0)
    try:
        finished = await session._fast_forward(eng, START + dt.timedelta(hours=1))
    finally:
        replay_mod._PLAYING.discard(id(session))
    await page
    # 30 ticks of 1/16 s = 1.875 s of its own time: a pause after every 0.4 s of it, none for the page's 5 s.
    assert not finished and eng.ticks == 30 and eng.feed.clock == START + dt.timedelta(minutes=5)
    assert fake.pauses == [7, 14, 21, 28]
    assert session._seek is None


def test_cpu_share_by_server(monkeypatch):
    import bots

    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    assert replay_mod.cpu_share() == replay_mod.CPU_SHARE == 0.4  # the desk: live bots share the event loop
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    idle = {1: SimpleNamespace(status="STOPPED", positions={}), 2: SimpleNamespace(status="DAY_COMPLETED", positions={})}
    monkeypatch.setattr(bots, "engines", lambda: idle)
    assert replay_mod.cpu_share() == replay_mod.BACKTEST_CPU_SHARE == 0.8
    # Its practice bots running, paused or holding a trade: the desk's share again.
    for busy in (SimpleNamespace(status="RUNNING", positions={}), SimpleNamespace(status="PAUSED", positions={}),
                 SimpleNamespace(status="STOPPED", positions={"TCS": object()})):
        monkeypatch.setattr(bots, "engines", lambda busy=busy: {**idle, 3: busy})
        assert replay_mod.cpu_share() == replay_mod.CPU_SHARE
    monkeypatch.setenv("PALTRA_ROLE", "scanner")
    assert replay_mod.cpu_share() == replay_mod.CPU_SHARE
