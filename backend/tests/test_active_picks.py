"""Most active pick backtest (active_picks.py): the card's read at the pick time from 1-minute candles, rank, top N."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import active_picks as ap

IST = ap.IST
DAY = dt.date(2026, 11, 2)
PREV = dt.date(2026, 10, 30)


def _session(day, start_price, step, vol_per_min, minutes=180):
    rows, cum, p = [], 0, start_price
    t0 = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for i in range(minutes):
        o = p
        p = p + step
        cum += vol_per_min
        rows.append({"ts": int((t0 + dt.timedelta(minutes=i)).timestamp()), "open": o, "high": max(o, p) + 0.05,
                     "low": min(o, p) - 0.05, "close": p, "volume": cum})
    return rows


def _frame(step, vol_today, vol_before=1000, start=100.0):
    return pd.DataFrame(_session(PREV, start, 0.0, vol_before) + _session(DAY, start, step, vol_today))


def test_the_card_read_at_the_pick_time():
    row = ap.read_at(_frame(0.01, 3000), DAY, dt.time(10, 15), "UP")
    assert row["bias"] == "LONG" and row["change_pct"] > 0 and row["vwap_dist_pct"] > 0
    assert row["rvol"] == pytest.approx(3.0, rel=0.02)  # three times the earlier session's volume by 10:15
    assert row["speed_pct_min"] > 0 and row["value_cr"] > 0
    down = ap.read_at(_frame(-0.01, 1000), DAY, dt.time(10, 15), "DN")
    assert down["bias"] == "SHORT" and down["rvol"] == pytest.approx(1.0, rel=0.02)
    assert ap.read_at(_frame(0.01, 1000), dt.date(2026, 11, 3), dt.time(10, 0)) is None  # no candles that day


def test_nothing_after_the_pick_time_is_read():
    early = ap.read_at(_frame(0.01, 1000), DAY, dt.time(9, 30), "X")
    late = ap.read_at(_frame(0.01, 1000), DAY, dt.time(11, 0), "X")
    assert early["ltp"] < late["ltp"]


def test_picks_rank_filter_and_top_n():
    frames = {
        "HOTUP": _frame(0.01, 4000, start=500.0),
        "WARMUP": _frame(0.01, 2000, start=500.0),
        "DOWN": _frame(-0.01, 3000, start=500.0),
        "FLAT": _frame(0.0, 5000, start=500.0),
        "TINY": _frame(0.01, 9000, vol_before=9000, start=1.0),
    }
    rule = ap.ActivePickRule(pick_time=dt.time(10, 30), top_n=3, rank="rvol", bias="BOTH", min_value_cr=0.5)
    got = ap.picks_for_day(frames, DAY, rule)
    assert [p["symbol"] for p in got] == ["HOTUP", "DOWN", "WARMUP"]  # FLAT has no bias, TINY trades too little money
    assert got[0]["side"] == "LONG" and got[1]["side"] == "SHORT"
    longs = ap.picks_for_day(frames, DAY, ap.ActivePickRule(pick_time=dt.time(10, 30), bias="LONG", min_value_cr=0.5))
    assert {p["symbol"] for p in longs} == {"HOTUP", "WARMUP"}
    anyb = ap.picks_for_day(frames, DAY, ap.ActivePickRule(pick_time=dt.time(10, 30), bias="ANY", rank="rvol", top_n=1,
                                                           min_value_cr=0.5))
    assert anyb[0]["symbol"] == "FLAT"


def test_the_scalp_pick_dispatch_uses_the_active_rule():
    from scalp_picks import picks_for_day

    rule = ap.ActivePickRule(pick_time=dt.time(10, 30), top_n=1, min_value_cr=0.0)
    assert picks_for_day({"UP": _frame(0.01, 2000)}, DAY, rule)[0]["symbol"] == "UP"
    assert rule.as_dict()["kind"] == "ACTIVE"


def test_api_starts_with_the_rule_and_refuses_bad_values(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/a.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    seen = []

    async def fake_begin(broker, names, day, start, speed, end_day=None, settings=None, rule=None, bot=1, bot_name=None):
        seen.append((rule, settings))

    monkeypatch.setattr(main.engine.broker, "token", "test-token", raising=False)
    monkeypatch.setattr(main.replays[1], "begin", fake_begin)
    monkeypatch.setattr(main, "_refuse_desk_replay_in_market", lambda *a, **k: None)
    with TestClient(main.app) as client:
        body = {"date": "2026-10-01", "end_date": "2026-10-02", "universe": ["SBIN"], "pick_time": "11:00",
                "rank": "change", "bias": "LONG", "min_value_cr": 2}
        assert client.post("/api/replay/active-picks", json=body).status_code == 200
        assert client.post("/api/replay/active-picks", json={**body, "rank": "pressure"}).status_code == 422
        assert client.post("/api/replay/active-picks", json={**body, "pick_time": "15:00"}).status_code == 400
    rule, settings = seen[0]
    assert rule.kind == "ACTIVE" and rule.rank == "change" and rule.bias == "LONG" and rule.min_value_cr == 2
    assert rule.pick_time == dt.time(11, 0) and settings["play_handoffs"] is True
    database.reset_engine()
