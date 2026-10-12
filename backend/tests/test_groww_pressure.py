"""Bots first on Groww: the bots' own threads, and scans that wait after a bot is rate-limited. No order path."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

import groww_pressure


@pytest.fixture(autouse=True)
def _fresh():
    groww_pressure.reset_for_tests()
    yield
    groww_pressure.reset_for_tests()


def test_a_bot_call_runs_on_the_bots_own_threads():
    seen: list[str] = []

    async def go():
        return await groww_pressure.run_bot_call(lambda x: seen.append(threading.current_thread().name) or x * 2, 21)

    assert asyncio.run(go()) == 42
    assert seen and seen[0].startswith("bot-groww")


def test_busy_default_threads_do_not_hold_up_a_bot_call():
    """Scans fill asyncio's default pool with slow downloads; the bot's call still answers at once."""

    async def go():
        loop = asyncio.get_running_loop()
        slow = [loop.run_in_executor(None, time.sleep, 1.5) for _ in range(40)]
        started = time.monotonic()
        await groww_pressure.run_bot_call(lambda: None)
        took = time.monotonic() - started
        await asyncio.gather(*slow)
        return took

    assert asyncio.run(go()) < 0.5


def test_a_rate_limited_bot_call_is_raised_and_makes_scans_wait(monkeypatch):
    def refused():
        raise RuntimeError("Groww: rate limit exceeded")

    async def go():
        with pytest.raises(RuntimeError):
            await groww_pressure.run_bot_call(refused)

    asyncio.run(go())
    assert groww_pressure.bots_under_pressure()
    assert groww_pressure.snapshot()["bot_rate_limits"] == 1

    # Scans wait while the bots are under pressure, and carry on once it has passed.
    monkeypatch.setattr(groww_pressure, "HOLD_SEC", 1.2)
    waited = asyncio.run(groww_pressure.wait_for_bots(max_wait=5))
    assert 0.0 < waited < 3.0
    assert not groww_pressure.bots_under_pressure()
    assert groww_pressure.snapshot()["scan_waits"] == 1


def test_scans_do_not_wait_when_the_bots_are_fine():
    assert asyncio.run(groww_pressure.wait_for_bots()) == 0.0


def test_other_errors_do_not_make_scans_wait():
    def broken():
        raise ValueError("bad symbol")

    async def go():
        with pytest.raises(ValueError):
            await groww_pressure.run_bot_call(broken)

    asyncio.run(go())
    assert not groww_pressure.bots_under_pressure()


def test_a_wait_never_runs_past_its_cap(monkeypatch):
    groww_pressure.note_bot_rate_limit()
    monkeypatch.setattr(groww_pressure, "HOLD_SEC", 60)
    started = time.monotonic()
    asyncio.run(groww_pressure.wait_for_bots(max_wait=1.0))
    assert time.monotonic() - started < 2.5
