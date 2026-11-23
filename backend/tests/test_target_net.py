"""Safety net after the pivot / day target (target_net.py). PAPER only: exits are recorded, nothing is ordered."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import target_net
from tests.test_day_target import _engine

ON = SimpleNamespace(target_net=True)


def test_floor_arms_only_after_the_target_and_steps_up():
    assert target_net.floor("LONG", 100.0, 103.0, 102.9, ON) is None  # target not reached yet
    lvl, share = target_net.floor("LONG", 100.0, 103.0, 103.0, ON)
    assert share == 70 and lvl == pytest.approx(102.1)
    lvl, share = target_net.floor("LONG", 100.0, 103.0, 106.0, ON)  # 6 >= 2 x 3: second step
    assert share == 75 and lvl == pytest.approx(104.5)
    lvl, share = target_net.floor("SHORT", 100.0, 97.0, 96.0, ON)
    assert share == 70 and lvl == pytest.approx(97.2)
    none2 = SimpleNamespace(target_net=True, target_net_at2=0)
    assert target_net.floor("LONG", 100.0, 103.0, 110.0, none2)[1] == 70
    assert target_net.hit("LONG", 102.0, 102.1) and not target_net.hit("LONG", 102.2, 102.1)
    assert not target_net.on(SimpleNamespace())


CFG = SimpleNamespace(day_target=True, target_net=True, symbol="SHREECEM")


@pytest.mark.asyncio
async def test_the_day_target_runs_on_with_a_net_instead_of_closing():
    eng, calls = _engine("LONG", 100.0)  # today's high 103 before the entry
    pos = eng.positions["SHREECEM"]
    pos.stop_active = False
    for price in (103.2, 104.0, 105.0):
        eng.ltp = price
        pos.high = max(pos.high or price, price)
        await eng._watch_day_target(CFG)
    assert calls == []  # touched and passed the target: still open
    assert "safety net at 103.50" in eng._signals["SHREECEM"]  # 100 + 70% of 5
    eng.ltp = 103.4
    await eng._watch_day_target(CFG)
    assert calls == [(103.4, target_net.EXIT_REASON)]


@pytest.mark.asyncio
async def test_the_stop_moves_up_to_the_net(monkeypatch):
    eng, calls = _engine("LONG", 100.0)
    pos = eng.positions["SHREECEM"]
    moved = []

    async def move(p, symbol, level, label):
        moved.append(level)
        p.sl_trigger = level
        return True

    eng._move_stop = move
    eng.ltp = 104.0
    pos.high = 104.0
    await eng._watch_day_target(CFG)
    assert calls == [] and moved == [pytest.approx(102.8)]


@pytest.mark.asyncio
async def test_without_the_net_the_target_still_closes():
    eng, calls = _engine("LONG", 100.0)
    eng.ltp = 103.1
    await eng._watch_day_target(SimpleNamespace(day_target=True, symbol="SHREECEM"))
    assert calls and calls[0][1] == "DAY_LEVEL_TARGET"


def test_api_saves_the_net(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["target_net"] is False and got["target_net_keep"] == 70 and got["target_net_at2"] == 2
        ok = client.put("/api/bots/2/config", json={"target_net": True, "target_net_keep": 60, "target_net_keep2": 80}).json()
        assert ok["target_net"] is True and ok["target_net_keep"] == 60 and ok["target_net_keep2"] == 80
        assert client.put("/api/bots/2/config", json={"target_net_keep": 100}).status_code == 422
    database.reset_engine()
