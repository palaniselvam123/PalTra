"""Profit safety net on every trade (target_net.py). PAPER only: exits are recorded, nothing is ordered."""
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
async def test_the_day_target_closes_even_with_the_net_on():
    """The old "hold past the target" mode is gone: the day target closes as usual."""
    eng, calls = _engine("LONG", 100.0)  # today's high 103 before the entry
    eng.positions["SHREECEM"].stop_active = False
    eng.ltp = 103.1
    await eng._watch_day_target(CFG)
    assert calls and calls[0][1] == "DAY_LEVEL_TARGET"


@pytest.mark.asyncio
async def test_the_stop_moves_up_to_the_net():
    eng, calls = _engine("LONG", 100.0)
    pos = eng.positions["SHREECEM"]
    pos.qty = 1
    moved = []

    async def move(p, symbol, level, label):
        moved.append(level)
        p.sl_trigger = level
        return True

    eng._move_stop = move
    eng.ltp = 101.0
    pos.high = 101.0
    cfg = SimpleNamespace(target_net=True, target_net_start="PCT", target_net_start_pct=0.5, symbol="SHREECEM")
    await eng._watch_profit_net(cfg)  # start 100.5; 1 >= 2 x 0.5: 75% of 1
    assert calls == [] and moved == [pytest.approx(100.75)]


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
        ok = client.put("/api/bots/2/config", json={"target_net_start": "RS", "target_net_start_rs": 500}).json()
        assert ok["target_net_start"] == "RS" and ok["target_net_start_rs"] == 500 and ok["target_net_start_pct"] == 0.25
        assert client.put("/api/bots/2/config", json={"target_net_start": "X"}).status_code == 422
    database.reset_engine()


def test_start_level_on_any_trade():
    rs = SimpleNamespace(target_net=True, target_net_start="RS", target_net_start_rs=500)
    assert target_net.start_level("SHORT", 2715.3, 100, None, rs) == ("₹500 profit", pytest.approx(2710.3))
    pct = SimpleNamespace(target_net=True, target_net_start="PCT", target_net_start_pct=0.25)
    assert target_net.start_level("LONG", 1000.0, 10, None, pct)[1] == pytest.approx(1002.5)
    atr = SimpleNamespace(target_net=True, target_net_start="ATR", target_net_start_atr=1.5)
    assert target_net.start_level("LONG", 100.0, 10, 2.0, atr)[1] == pytest.approx(103.0)
    assert target_net.start_level("LONG", 100.0, 10, None, atr) is None  # no ATR formed
    assert target_net.start_level("LONG", 100.0, 10, 2.0, ON)[1] == pytest.approx(100.25)  # default: 0.25% of the entry
    assert target_net.start(SimpleNamespace(target_net_start="TARGET")) == "PCT"  # the old mode reads as PCT
    assert target_net.start(SimpleNamespace(target_net_start="junk")) == "PCT"


@pytest.mark.asyncio
async def test_profit_start_runs_the_net_without_any_target():
    eng, calls = _engine("LONG", 100.0)
    pos = eng.positions["SHREECEM"]
    pos.stop_active = False
    pos.qty = 100
    cfg = SimpleNamespace(target_net=True, target_net_start="RS", target_net_start_rs=200, symbol="SHREECEM")
    eng.ltp = 101.9
    pos.high = 101.9
    await eng._watch_profit_net(cfg)
    assert calls == [] and "safety net" not in eng._signals.get("SHREECEM", "")
    for price in (102.0, 104.0):  # start 102 (₹200 / 100 shares)
        eng.ltp = price
        pos.high = max(pos.high, price)
        await eng._watch_profit_net(cfg)
    assert calls == [] and "safety net at 103.00" in eng._signals["SHREECEM"]  # 4 >= 2 x 2: 75% of 4
    eng.ltp = 102.9
    await eng._watch_profit_net(cfg)
    assert calls == [(102.9, target_net.EXIT_REASON)]
