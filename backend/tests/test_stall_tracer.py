"""The stall tracer names what blocked the event loop. Logging only: it never touches the bots."""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from stall_tracer import StallTracer


def _tracer(**kw) -> StallTracer:
    return StallTracer(stall_sec=0.4, beat_sec=0.05, watch_sec=0.05, use_faulthandler=False, **kw)


def _block_the_loop_for_the_test(seconds: float) -> None:
    time.sleep(seconds)  # what a stuck call looks like to the loop


def test_a_blocked_loop_is_logged_with_the_line_that_blocked_it(caplog):
    tracer = _tracer()

    async def scenario():
        assert tracer.start() is True
        await asyncio.sleep(0.3)  # beating normally
        _block_the_loop_for_the_test(1.2)  # nothing else on the loop can run now
        await asyncio.sleep(0.4)  # the loop answers again
        tracer.stop()

    with caplog.at_level(logging.WARNING, logger="sma.stall"):
        asyncio.run(scenario())

    messages = [r.getMessage() for r in caplog.records]
    stall = [m for m in messages if m.startswith("STALL:")]
    assert len(stall) == 1, messages
    assert "_block_the_loop_for_the_test" in stall[0]  # the stack names the blocking function
    assert "rss" in stall[0] and "threads" in stall[0]
    assert any(m.startswith("STALL over") for m in messages)
    snap = tracer.snapshot()
    assert snap["stalls_since_start"] == 1 and snap["longest_stall_s"] >= 0.9
    assert snap["recent"] and snap["stalled_now"] is False


def test_a_healthy_loop_logs_nothing(caplog):
    tracer = _tracer()

    async def scenario():
        tracer.start()
        for _ in range(12):
            await asyncio.sleep(0.05)
        tracer.stop()

    with caplog.at_level(logging.WARNING, logger="sma.stall"):
        asyncio.run(scenario())
    assert [r for r in caplog.records if "STALL" in r.getMessage()] == []
    assert tracer.snapshot()["stalls_since_start"] == 0


def test_it_starts_once_and_can_be_switched_off(monkeypatch):
    once = _tracer()

    async def twice():
        first, second = once.start(), once.start()
        once.stop()
        return first, second

    assert asyncio.run(twice()) == (True, False)

    monkeypatch.setenv("STALL_TRACE", "off")
    off = _tracer()

    async def disabled():
        return off.start()

    assert asyncio.run(disabled()) is False
    assert off.snapshot()["on"] is False


def test_a_bad_environment_value_falls_back_to_five_seconds(monkeypatch):
    monkeypatch.setenv("STALL_TRACE_SEC", "soon")
    assert StallTracer().stall_sec == 5.0
    monkeypatch.setenv("STALL_TRACE_SEC", "12")
    assert StallTracer().stall_sec == 12.0


def test_the_faulthandler_timer_is_armed_and_cancelled_cleanly():
    tracer = StallTracer(stall_sec=30.0, beat_sec=0.05, watch_sec=0.05, use_faulthandler=True)

    async def scenario():
        tracer.start()
        await asyncio.sleep(0.2)
        tracer.stop()

    asyncio.run(scenario())  # no stall, no output, no exception; the 30 s timer is cancelled by stop()
    assert tracer.snapshot()["stalls_since_start"] == 0


def test_the_stall_endpoint_reports_the_tracer(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/stall.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        body = client.get("/api/stall").json()
    assert {"on", "stalls_since_start", "longest_stall_s", "last_beat_age_s", "rss_mb", "recent"} <= set(body)
    database.reset_engine()
