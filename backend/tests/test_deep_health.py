"""Deep health: the rules a watcher relies on, the engine stamp, and that the route is public."""
from __future__ import annotations

import asyncio

import deep_health as dh


def _tracer(**kw):
    base = {"on": True, "stalled_now": False, "last_beat_age_s": 0.4, "stalls_since_start": 0, "worst_lag_s": 0.1}
    return {**base, **kw}


def _bot(n=1, status="RUNNING", age=1.0, alive=True):
    return {"bot": n, "status": status, "last_tick_age_s": age, "loop_alive": alive}


def test_healthy_in_market_hours():
    out = dh.evaluate(market_open=True, tracer=_tracer(), bots=[_bot(1), _bot(2, "STOPPED", 0.6)])
    assert out["ok"] and out["reasons"] == []


def test_blocked_loop_fails():
    out = dh.evaluate(market_open=True, tracer=_tracer(stalled_now=True), bots=[_bot()])
    assert not out["ok"] and "blocked" in out["reasons"][0]
    out = dh.evaluate(market_open=True, tracer=_tracer(last_beat_age_s=11), bots=[_bot()])
    assert not out["ok"]


def test_a_recovered_stall_is_not_a_failure():
    out = dh.evaluate(market_open=True, tracer=_tracer(stalls_since_start=3, worst_lag_s=9), bots=[_bot()])
    assert out["ok"]


def test_bot_not_ticking_in_market_hours_fails_even_when_stopped():
    out = dh.evaluate(market_open=True, tracer=_tracer(), bots=[_bot(3, "STOPPED", age=45)])
    assert not out["ok"] and "Bot 3" in out["reasons"][0]


def test_after_the_close_a_stopped_bot_gets_a_long_leash_but_a_running_one_does_not():
    ok = dh.evaluate(market_open=False, tracer=_tracer(), bots=[_bot(1, "STOPPED", age=6)])
    assert ok["ok"]
    gone = dh.evaluate(market_open=False, tracer=_tracer(), bots=[_bot(1, "STOPPED", age=400)])
    assert not gone["ok"]
    stuck = dh.evaluate(market_open=False, tracer=_tracer(), bots=[_bot(1, "RUNNING", age=60)])
    assert not stuck["ok"]


def test_dead_loop_task_and_never_ticked():
    assert not dh.evaluate(market_open=False, tracer=_tracer(), bots=[_bot(2, alive=False)])["ok"]
    assert not dh.evaluate(market_open=True, tracer=_tracer(), bots=[_bot(1, age=None)])["ok"]
    assert dh.evaluate(market_open=False, tracer=_tracer(), bots=[_bot(1, "STOPPED", age=None)])["ok"]


def test_tracer_off_does_not_fail_by_itself():
    assert dh.evaluate(market_open=False, tracer={"on": False}, bots=[_bot(1, "STOPPED", 2)])["ok"]


def test_engine_stamps_each_pass():
    import main as sma

    eng = sma.StrategyEngine() if hasattr(sma, "StrategyEngine") else sma.engine
    assert eng.last_tick_at is None or isinstance(eng.last_tick_at, float)

    async def go():
        calls = {"n": 0}

        async def fake_tick(now):
            calls["n"] += 1
            eng._stop = True

        async def no_sleep(_s):
            return None

        eng.tick, eng._sleep = fake_tick, no_sleep
        eng._stop = False
        eng.last_tick_at = None
        await eng.run()
        return calls["n"]

    try:
        assert asyncio.run(go()) == 1
        assert eng.last_tick_at is not None
    finally:
        del eng.tick
        eng._sleep = asyncio.sleep
        eng._stop = False


def test_terminal_endpoint_answers_and_says_what_it_sees():
    from fastapi.testclient import TestClient

    import main as sma

    with TestClient(sma.app) as http:
        r = http.get("/api/health/deep")
    assert r.status_code in (200, 503)
    body = r.json()
    assert {"ok", "reasons", "bots", "loop", "market_open"} <= set(body)
    assert [b["bot"] for b in body["bots"]] == [1, 2, 3, 4]
    assert "mode" not in body


def test_desk_lock_lets_the_deep_check_through_without_sign_in():
    from app.core.desk_lock import _api_public

    assert _api_public("/api/health/deep") and not _api_public("/api/state")


def test_desk_route_is_public_and_follows_the_terminal(monkeypatch):
    import sys
    import types

    from fastapi.testclient import TestClient

    from app.main import app as desk

    # No terminal copied in: a watcher must hear that, not a false "ok".
    monkeypatch.delitem(sys.modules, "sma_terminal_main", raising=False)
    with TestClient(desk) as http:
        down = http.get("/api/health/deep")
    assert down.status_code == 503 and down.json()["ok"] is False

    stub = types.ModuleType("sma_terminal_main")
    stub.deep_health = lambda: ({"ok": True, "reasons": [], "bots": []}, True)
    monkeypatch.setitem(sys.modules, "sma_terminal_main", stub)
    with TestClient(desk) as http:
        up = http.get("/api/health/deep")
    assert up.status_code == 200 and up.json()["ok"] is True
