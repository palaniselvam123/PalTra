"""SMA 9 turn alert and entry (turn_entry.py): act on the fast SMA's V before the 9/21 cross.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import turn_entry
from tests.test_join_trend import _day

DAY = dt.date(2026, 9, 22)
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)


def _closed(closes) -> pd.DataFrame:
    return pd.DataFrame({"close": [float(c) for c in closes]})


CFG = SimpleNamespace()  # every V-turn number at its default


def test_detect_a_turn_down_once_on_the_first_candle():
    up = [100 + i * 0.1 for i in range(30)]  # rising: SMA 9 slope about +0.1 %/candle
    down = [up[-1] - (i + 1) * 0.4 for i in range(6)]
    found = [turn_entry.detect(_closed(up + down[: n + 1]), 9, CFG) for n in range(len(down))]
    hits = [(n, f[0]) for n, f in enumerate(found) if f]
    assert len(hits) == 1 and hits[0][1] == "SHORT"
    assert "turned down" in next(f for f in found if f)[1]


def test_detect_a_turn_up_after_a_fall():
    fall = [100 - i * 0.1 for i in range(30)]
    rise = [fall[-1] + (i + 1) * 0.4 for i in range(6)]
    hits = [turn_entry.detect(_closed(fall + rise[: n + 1]), 9, CFG) for n in range(len(rise))]
    assert [h[0] for h in hits if h] == ["LONG"]


def test_no_turn_from_flat_or_a_slow_drift():
    flat = [100 + (0.01 if i % 2 else -0.01) for i in range(30)]
    drop = [100 - (i + 1) * 0.4 for i in range(6)]
    # Flat then falling: no rise came before it, so it is not a V.
    assert all(turn_entry.detect(_closed(flat + drop[: n + 1]), 9, CFG) is None for n in range(len(drop)))
    drift = [100 + i * 0.002 for i in range(40)]
    assert turn_entry.detect(_closed(drift), 9, CFG) is None
    assert turn_entry.detect(_closed([100] * 5), 9, CFG) is None


def test_alert_text_says_whether_it_orders():
    msg = turn_entry.alert_text("Bot 3", "ASHOKLEY", "SHORT", "SMA 9 turned down", 142.97, entry=False)
    assert "▼ ASHOKLEY" in msg and "₹142.97" in msg and "Alert only" in msg
    assert "orders a buy" in turn_entry.alert_text("Bot 3", "X", "LONG", "n", None, entry=True)


def _v_day() -> pd.DataFrame:
    """Flat, a steady rise from 10:15 (SMA 9 about +0.025 %/candle), then a sharp fall from 12:15."""
    def path(i, p):
        if i < 60:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        return p + 0.25 if i < 180 else p - 0.5

    flat = _day(dt.date(2026, 9, 21), lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02), 1000.0)
    return pd.DataFrame(flat + _day(DAY, path, 1000.0))


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_v_day(), "SIMT", [DAY], _settings(database, **over))


PEAK = dt.datetime(2026, 9, 22, 12, 15)


@pytest.mark.asyncio
async def test_turn_entry_shorts_at_the_v_before_the_cross(db):  # noqa: F811
    plain = await _trades(db)
    cross_short = next(t for t in plain if t["direction"] == "SHORT" and t["entry_time"] >= PEAK)
    trades = await _trades(db, turn_entry=True)
    turned = [t for t in trades if t["entry_reason"] == turn_entry.ENTRY_REASON]
    assert len(turned) == 1 and turned[0]["direction"] == "SHORT"
    assert PEAK < turned[0]["entry_time"] < cross_short["entry_time"]
    # The rise out of the flat morning is not a V (nothing fell first), so no long is taken on a turn.
    held = next(t for t in trades if t["exit_time"] == turned[0]["entry_time"] and t["direction"] == "LONG")
    assert held["entry_reason"] == "SMA_CROSS"  # the open long was closed and reversed, like an opposite cross


@pytest.mark.asyncio
async def test_off_by_default_and_the_alert_never_trades(db):  # noqa: F811
    key = lambda t: (t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"])  # noqa: E731
    plain = [key(t) for t in await _trades(db)]
    assert [key(t) for t in await _trades(db, turn_entry=False, turn_alert=False)] == plain
    assert [key(t) for t in await _trades(db, turn_alert=True)] == plain


def test_engine_alerts_once_per_turn_and_not_an_old_one(db):  # noqa: F811
    from strategy_engine import StrategyEngine

    eng = StrategyEngine()
    sent: list[str] = []
    eng._alert = lambda message, critical=False, call=False: sent.append(message)
    cfg = SimpleNamespace(turn_alert=True, turn_entry=False, sma_fast=9, bot_name="Bot 3")
    up = [100 + i * 0.1 for i in range(30)]
    down = [up[-1] - (i + 1) * 0.4 for i in range(6)]
    closes = up + down
    frame = lambda n: pd.DataFrame({"ts": [60 * i for i in range(n + 1)], "close": closes[:n] + [closes[n - 1]]})  # noqa: E731
    seen = []
    for n in range(25, len(closes) + 1):
        hit = eng._turn_now("ASHOKLEY", frame(n), cfg)
        eng._turn_now("ASHOKLEY", frame(n), cfg)  # the same candle again: no second alert
        seen.append(hit)
    assert len(sent) == 1 and "ASHOKLEY" in sent[0] and "Alert only" in sent[0]
    assert sum(1 for h in seen if h) == 1
    # A stock first read on the turn candle itself is not alerted (it may be an old turn).
    fresh = StrategyEngine()
    fresh._alert = lambda message, critical=False, call=False: sent.append(message)
    turn_at = next(n for n in range(25, len(closes) + 1) if turn_entry.detect(pd.DataFrame({"close": closes[:n]}), 9, cfg))
    assert fresh._turn_now("ASHOKLEY", frame(turn_at), cfg) is None and len(sent) == 1


def test_api_saves_the_turn_switches_per_bot_and_per_stock(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/3/config").json()
        assert got["turn_alert"] is False and got["turn_entry"] is False
        ok = client.put("/api/bots/3/config", json={"turn_alert": True, "turn_entry": True})
        assert ok.status_code == 200 and ok.json()["turn_alert"] is True and ok.json()["turn_entry"] is True
        one = client.put("/api/bots/3/config/stock/ASHOKLEY", json={"turn_entry": False})
        assert one.status_code == 200
        assert client.get("/api/bots/3/config/stock/ASHOKLEY").json()["turn_entry"] is False
    database.reset_engine()


# ---- rounded U (uturn_on) ------------------------------------------------------------------------

import math  # noqa: E402

WAVE = [100 + 2 * math.sin(i / 12) for i in range(80)]  # a slow, rounded top then bottom


def _hits(closes, cfg, start=25):
    return [(n, h) for n in range(start, len(closes) + 1) if (h := turn_entry.detect(_closed(closes[:n]), 9, cfg))]


def test_rounded_u_is_caught_only_with_its_switch_and_before_the_v():
    plain = _hits(WAVE, SimpleNamespace())
    with_u = _hits(WAVE, SimpleNamespace(uturn_on=True))
    assert all(h[1].startswith("V:") for _, h in plain)
    u = [(n, h) for n, h in with_u if h[1].startswith("U:")]
    assert u and u[0][1][0] == "LONG" and "rounded bottom" in u[0][1][1]
    v_bottom = next(n for n, h in plain if h[0] == "LONG")
    assert u[0][0] < v_bottom  # the U shows a candle or more before the V would


def test_u_needs_a_real_run_first():
    drift = [100 + (0.001 if i % 2 else -0.001) for i in range(60)]  # wiggles under the 0.005 %/candle floor
    assert _hits(drift, SimpleNamespace(uturn_on=True)) == []
    short_run = [100] * 30 + [100 + i * 0.01 for i in range(1, 4)] + [100.03 - i * 0.01 for i in range(1, 10)]
    assert _hits(short_run, SimpleNamespace(uturn_on=True, uturn_run=8)) == []


def test_a_u_then_its_v_alerts_once(db):  # noqa: F811
    from strategy_engine import StrategyEngine

    eng = StrategyEngine()
    sent: list[str] = []
    eng._alert = lambda message, critical=False, call=False: sent.append(message)
    cfg = SimpleNamespace(turn_alert=True, turn_entry=False, sma_fast=9, bot_name="Bot 3", uturn_on=True)
    frame = lambda n: pd.DataFrame({"ts": [60 * i for i in range(n + 1)], "close": WAVE[:n] + [WAVE[n - 1]]})  # noqa: E731
    for n in range(40, len(WAVE) + 1):
        eng._turn_now("ASHOKLEY", frame(n), cfg)
    ups = [m for m in sent if m.startswith("▲")]
    assert len(ups) == 1 and "U: " in ups[0]


def test_api_saves_the_u_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["uturn_on"] is False and got["uturn_run"] == 5 and got["uturn_min_pct"] == 0.005
        ok = client.put("/api/bots/2/config", json={"uturn_on": True, "uturn_run": 8, "uturn_min_pct": 0.01})
        assert ok.status_code == 200 and ok.json()["uturn_run"] == 8
        assert client.put("/api/bots/2/config", json={"uturn_run": 1}).status_code == 422
    database.reset_engine()
