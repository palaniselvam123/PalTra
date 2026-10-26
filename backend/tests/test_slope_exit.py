"""SMA 9 slope exit (slope_exit.py): close when the fast SMA bends or turns. PAPER only."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import slope_exit
from tests.test_gap_mode import _day, _settings, db  # noqa: F401  (db is a fixture)

DAY = dt.date(2026, 9, 22)


def _cfg(**over):
    return type("C", (), over)()


def test_slope_and_judge():
    closes = pd.DataFrame({"close": [100.0] * 9 + [101.0, 102.0]})
    # SMA 3 now 101, two candles back 100 -> 1 % over 2 candles = 0.5 %/candle.
    assert slope_exit.slope(closes, 3, 2) == pytest.approx(0.5)
    assert slope_exit.slope(closes.iloc[:3], 3, 2) is None  # not enough candles
    cfg = _cfg()
    state: dict = {}
    assert slope_exit.judge("LONG", 0.20, state, cfg) is None  # steepest so far
    assert slope_exit.judge("LONG", 0.10, state, cfg) is None  # 50 % off: not yet 60 %
    reason, note = slope_exit.judge("LONG", 0.08, state, cfg)
    assert reason == slope_exit.BEND_EXIT and "steepest +0.200" in note
    # A sell mirrors it: a falling line is "with" the trade.
    short: dict = {}
    assert slope_exit.judge("SHORT", -0.30, short, cfg) is None
    assert slope_exit.judge("SHORT", -0.05, short, cfg)[0] == slope_exit.BEND_EXIT
    # Turn: the line points against the trade, even with the bend off.
    no_bend = _cfg(slope_bend_pct=0)
    assert slope_exit.judge("LONG", -0.01, {}, no_bend) is None  # within 0.02
    assert slope_exit.judge("LONG", -0.03, {}, no_bend)[0] == slope_exit.TURN_EXIT
    assert slope_exit.judge("LONG", -0.03, {}, _cfg(slope_bend_pct=0, slope_turn=False)) is None
    # A line that was never steep enough never counts as bent.
    flat: dict = {}
    assert slope_exit.judge("LONG", 0.01, flat, cfg) is None and slope_exit.judge("LONG", 0.0, flat, cfg) is None
    assert not slope_exit.on(_cfg()) and slope_exit.lookback(_cfg(slope_lookback=99)) == slope_exit.MAX_LOOKBACK


def _frame() -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        if i < 40:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        if i < 60:  # a slide, so the rise below makes a fresh cross
            return p - 0.05
        if i < 100:  # 40 minutes of steady rise
            return p + 0.25
        if i < 105:  # the rise slows down: the SMA 9 line bends
            return p + 0.06
        if i < 110:
            return p
        return p - 0.25  # then the fall

    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(DAY, path))


async def _first_long(database, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(_frame(), "SIMS", [DAY], _settings(database, **over))
    longs = [t for t in trades if t["direction"] == "LONG" and t["entry_time"] >= dt.datetime(2026, 9, 22, 10, 15)]
    return longs[0]


@pytest.mark.asyncio
async def test_the_bend_exits_before_the_cross(db):  # noqa: F811
    plain = await _first_long(db)
    bent = await _first_long(db, slope_exit=True)
    assert plain["exit_reason"] == "MA_CROSS"
    assert bent["exit_reason"] == slope_exit.BEND_EXIT
    assert bent["exit_time"] < plain["exit_time"]
    assert bent["exit_price"] > plain["exit_price"]  # out near the top, not after the fall
    assert bent["entry_time"] == plain["entry_time"]


def test_api_saves_the_slope_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    keys = {"slope_exit", "slope_lookback", "slope_bend_pct", "slope_turn", "slope_turn_pct"}
    assert keys <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/bots/3/config").json()
        assert got["slope_exit"] is False and got["slope_lookback"] == 2 and got["slope_bend_pct"] == 60
        assert got["slope_turn"] is True and got["slope_turn_pct"] == 0.02
        ok = client.put("/api/bots/3/config", json={"slope_exit": True, "slope_bend_pct": 50})
        assert ok.status_code == 200 and ok.json()["slope_exit"] is True and ok.json()["slope_bend_pct"] == 50
        assert client.put("/api/bots/3/config", json={"slope_lookback": 0}).status_code == 422
        assert client.put("/api/bots/3/config", json={"slope_bend_pct": 101}).status_code == 422
        own = client.put("/api/bots/3/config/stock/BEL", json={"slope_exit": False})
        assert own.status_code == 200 and own.json()["slope_exit"] is False
    database.reset_engine()


def test_the_chart_is_told_the_slope_settings():
    from strategy_engine import chart_filters

    got = chart_filters(_cfg(slope_exit=True, slope_lookback=3, slope_turn_pct=0.05))
    assert got["slope_exit"] is True and got["slope_lookback"] == 3 and got["slope_turn_pct"] == 0.05
    assert chart_filters(_cfg())["slope_exit"] is False


def test_vturn_judge():
    cfg = _cfg()  # move 0.02, turn 0.015, within 6
    state: dict = {}
    assert slope_exit.judge_vturn("LONG", 0.05, state, cfg) is None  # the steep run
    assert slope_exit.judge_vturn("LONG", 0.0, state, cfg) is None
    reason, note = slope_exit.judge_vturn("LONG", -0.02, state, cfg)
    assert reason == slope_exit.VTURN_EXIT and "+0.050" in note
    # A sell mirrors it: a steep fall, then a steep rise.
    short: dict = {}
    assert slope_exit.judge_vturn("SHORT", -0.04, short, cfg) is None
    assert slope_exit.judge_vturn("SHORT", 0.03, short, cfg)[0] == slope_exit.VTURN_EXIT
    # A gentle run (never 0.02 with the trade) is a U, not a V.
    gentle: dict = {}
    for value in (0.012, 0.008, 0.0):
        assert slope_exit.judge_vturn("LONG", value, gentle, cfg) is None
    assert slope_exit.judge_vturn("LONG", -0.03, gentle, cfg) is None
    # The steep run must be within the window: 7 flat candles later it no longer counts.
    late: dict = {}
    slope_exit.judge_vturn("LONG", 0.05, late, cfg)
    for _ in range(6):
        slope_exit.judge_vturn("LONG", 0.0, late, cfg)
    assert slope_exit.judge_vturn("LONG", -0.03, late, cfg) is None
    assert not slope_exit.vturn_on(_cfg()) and slope_exit.vturn_within(_cfg(vturn_within=99)) == slope_exit.MAX_VTURN_WITHIN


def _peak_frame(rounding: int) -> pd.DataFrame:
    """A steep rise to a top, then a steep fall. `rounding` minutes to swing from +0.5 to -0.5 a minute:
    1 makes a V, 40 a slow U."""
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def path(i, p):
        if i < 40:
            return flat(i, p)
        if i < 60:
            return p - 0.05
        if i < 100:
            return p + 0.5
        k = i - 100
        if k < rounding:
            return p + 0.5 - k / rounding
        return p - 0.5

    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(DAY, path))


async def _long_on(database, frame, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(frame, "SIMV", [DAY], _settings(database, **over))
    return next(t for t in trades if t["direction"] == "LONG" and t["entry_time"] >= dt.datetime(2026, 9, 22, 10, 15))


@pytest.mark.asyncio
async def test_vturn_exits_a_v_top_but_not_a_slow_u(db):  # noqa: F811
    v_plain = await _long_on(db, _peak_frame(1))
    v_turn = await _long_on(db, _peak_frame(1), vturn_exit=True)
    assert v_turn["exit_reason"] == slope_exit.VTURN_EXIT
    assert v_turn["exit_time"] < v_plain["exit_time"]  # before the SMA cross
    assert v_turn["entry_time"] == v_plain["entry_time"]
    u_turn = await _long_on(db, _peak_frame(40), vturn_exit=True)
    assert u_turn["exit_reason"] != slope_exit.VTURN_EXIT  # a slow rounding is left to the other exits


def test_api_saves_the_vturn_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"vturn_exit", "vturn_move_pct", "vturn_turn_pct", "vturn_within"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["vturn_exit"] is False and got["vturn_within"] == 6 and got["vturn_turn_pct"] == 0.015
        ok = client.put("/api/config", json={"vturn_exit": True, "vturn_move_pct": 0.03, "vturn_within": 4})
        assert ok.status_code == 200 and ok.json()["vturn_exit"] is True and ok.json()["vturn_within"] == 4
        assert client.put("/api/config", json={"vturn_within": 11}).status_code == 422
    database.reset_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exit_price,handoff_on,moved",
    [(105.0, True, True), (95.0, True, False), (105.0, False, False)],
)
async def test_a_losing_vturn_moves_the_stock_to_bot_2(monkeypatch, tmp_path, exit_price, handoff_on, moved):
    """vturn_handoff: a V-turn exit at a loss hands the stock to Bot 2; a winner or the box off keeps it."""
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/v.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from tests.test_sma_atr_terminal import _FakeBroker, _instant
    from strategy_engine import OpenPosition, StrategyEngine

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    entry = dt.datetime(2026, 9, 22, 10, 0, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    eng._focus = "SIMV"
    eng.position = OpenPosition(
        direction="SHORT", qty=10, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=0.0,
        sl_order_id="", entry_order_id="E1", entry_time=entry, trade_id=0, mode="PAPER", stop_active=False,
    )
    eng.status = "RUNNING"
    eng.ltp = exit_price
    base = int(entry.timestamp())
    eng._frames["SIMV"] = pd.DataFrame({"ts": [base + 60 * i for i in range(30)], "close": [100.0] * 30})
    calls = []
    eng.handoff = lambda bot, symbol, live, to_bot=2: calls.append((bot, symbol, live)) or "moved to Bot 2"

    async def fake_exit(cfg, price, reason):
        assert reason == slope_exit.VTURN_EXIT
        eng.position = None

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    monkeypatch.setattr(slope_exit, "slope", lambda *a, **k: 0.05)
    monkeypatch.setattr(slope_exit, "judge_vturn", lambda *a, **k: (slope_exit.VTURN_EXIT, "V"))
    cfg = _cfg(symbol="SIMV", sma_fast=9, vturn_exit=True, vturn_handoff=handoff_on, candle_minutes=1, stock_settings="")
    await eng._watch_slope_exit(cfg)
    assert eng.position is None
    assert (calls == [(eng.bot_id, "SIMV", False)]) is moved
    database.reset_engine()


def test_api_saves_the_vturn_handoff(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "vturn_handoff" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["vturn_handoff"] is False
        ok = client.put("/api/config", json={"vturn_exit": True, "vturn_handoff": True})
        assert ok.status_code == 200 and ok.json()["vturn_handoff"] is True
    assert not slope_exit.vturn_handoff_on(_cfg(vturn_exit=False, vturn_handoff=True))
    assert slope_exit.lost("SHORT", 100, 101) and not slope_exit.lost("LONG", 100, 101)
    database.reset_engine()
