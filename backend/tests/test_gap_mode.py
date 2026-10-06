"""SMA gap mode: a cross arms the trade, the widening gap fires it, a fading gap closes it."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from gap_mode import Pending, check, judge_exit, judge_pending
from groww_client import IST


def _cfg(**over):
    base = dict(use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.02, gap_entry_short=-0.05,
                gap_exit_short=-0.02, gap_giveback_pct=0.0, gap_entry_delay_min=0, gap_entry_window_min=0)
    base.update(over)
    return SimpleNamespace(**base)


T0 = 1_790_000_000


def test_entry_waits_for_the_gap_then_fires():
    p = Pending("LONG", T0)
    assert judge_pending(_cfg(), p, 0.01, T0)[0] == "wait"
    action, note = judge_pending(_cfg(), p, 0.06, T0 + 120)
    assert action == "enter" and "+0.060%" in note


def test_short_uses_negative_levels():
    p = Pending("SHORT", T0)
    assert judge_pending(_cfg(), p, -0.03, T0)[0] == "wait"
    assert judge_pending(_cfg(), p, -0.07, T0 + 60)[0] == "enter"


def test_delay_counts_from_when_the_level_is_met_and_restarts_if_it_falls_back():
    cfg, p = _cfg(gap_entry_delay_min=2), Pending("LONG", T0)
    assert judge_pending(cfg, p, 0.06, T0 + 60)[0] == "wait"   # met at +1 min
    assert judge_pending(cfg, p, 0.03, T0 + 120)[0] == "wait"  # fell back: wait restarts
    assert p.met_ts is None
    assert judge_pending(cfg, p, 0.07, T0 + 180)[0] == "wait"  # met again at +3
    assert judge_pending(cfg, p, 0.08, T0 + 240)[0] == "wait"
    assert judge_pending(cfg, p, 0.08, T0 + 300)[0] == "enter"  # 2 min after +3


def test_window_gives_up():
    cfg, p = _cfg(gap_entry_window_min=5), Pending("LONG", T0)
    assert judge_pending(cfg, p, 0.01, T0 + 240)[0] == "wait"
    action, note = judge_pending(cfg, p, 0.01, T0 + 300)
    assert action == "drop" and "within 5 min" in note


def test_exit_arms_above_the_level_then_closes_when_the_gap_fades_back():
    cfg, state = _cfg(), {}
    assert judge_exit(cfg, "LONG", 0.06, None, state) is None and state["armed"]
    assert judge_exit(cfg, "LONG", 0.12, 0.06, state) is None   # widening: hold
    assert judge_exit(cfg, "LONG", 0.05, 0.12, state) is None   # narrowing, still above 0.02
    assert "exit level" in judge_exit(cfg, "LONG", 0.02, 0.05, state)


def test_exit_is_not_armed_by_a_trade_that_never_cleared_the_level():
    state = {}
    assert judge_exit(_cfg(), "LONG", 0.01, None, state) is None
    assert judge_exit(_cfg(), "LONG", 0.0, 0.01, state) is None and not state.get("armed")


def test_giveback_exits_on_a_narrowing_gap():
    cfg, state = _cfg(gap_giveback_pct=50), {}
    judge_exit(cfg, "SHORT", -0.06, None, state)
    judge_exit(cfg, "SHORT", -0.20, -0.06, state)  # widest -0.20
    assert judge_exit(cfg, "SHORT", -0.12, -0.20, state) is None  # gave back 40%
    assert "gave back 50%" in judge_exit(cfg, "SHORT", -0.09, -0.12, state)


def test_settings_that_cannot_work_are_refused():
    assert check(_cfg()) is None
    assert "Buy exit" in check(_cfg(gap_exit_long=0.06))
    assert "Sell exit" in check(_cfg(gap_exit_short=-0.06))


# ---- through the real engine (replay engine, local fills) -------------------------------------

def _day(day: dt.date, path) -> list[dict]:
    rows, cum = [], 0
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    price = 1000.0
    for i in range(375):
        o = price
        price = path(i, price)
        cum += 1000
        rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, price) + 0.05,
                     "low": min(o, price) - 0.05, "close": price, "volume": cum})
        t += dt.timedelta(minutes=1)
    return rows


def _frame() -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def trend(i, p):
        if i < 60:  # 09:15-10:14 flat
            return 1000.0 + (0.02 if i % 2 else -0.02)
        if i < 90:  # 10:15-10:44 steady rise
            return p + 0.12
        if i < 150:  # flat top: the gap fades
            return p + (0.02 if i % 2 else -0.02)
        return p - 0.12  # then a fall
    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(dt.date(2026, 9, 22), trend))


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/gap.db")
    import database

    database.reset_engine()
    database.init_db()
    yield database
    database.reset_engine()


def _settings(database, **over) -> dict:
    from models import BotConfig
    from sma_research.replayer import baseline_settings

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        data = baseline_settings({c.name: getattr(row, c.name) for c in BotConfig.__table__.columns})
    data.update({"use_stop": False, **over})
    return data


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_frame(), "SIMG", [dt.date(2026, 9, 22)], _settings(database, **over))


@pytest.mark.asyncio
async def test_gap_mode_enters_later_than_the_cross_and_exits_on_the_fade(db):
    plain = await _trades(db)
    gap = await _trades(db, use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.02,
                        gap_entry_short=-0.05, gap_exit_short=-0.02)
    trend = dt.datetime(2026, 9, 22, 10, 10)
    # The flat 09:15-10:14 stretch makes tiny crosses: plain mode trades them, gap mode never fires.
    assert any(t["entry_time"] < trend for t in plain)
    assert not any(t["entry_time"] < trend for t in gap)
    mid = dt.datetime(2026, 9, 22, 10, 30)
    first_plain = next(t for t in plain if t["direction"] == "LONG" and t["entry_time"] <= mid < t["exit_time"])
    first_gap = next(t for t in gap if t["direction"] == "LONG")
    assert first_gap["entry_time"] > first_plain["entry_time"]  # waited for the gap to widen
    assert first_gap["exit_reason"] == "GAP_FADE"
    assert first_plain["exit_reason"] != "GAP_FADE"
    assert first_gap["exit_time"] < first_plain["exit_time"]  # left on the fade, before the cross


@pytest.mark.asyncio
async def test_delay_moves_the_entry_back_by_at_least_that_many_minutes(db):
    base = dict(use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.02, gap_entry_short=-0.05, gap_exit_short=-0.02)
    now = next(t for t in await _trades(db, **base) if t["direction"] == "LONG")
    later = next(t for t in await _trades(db, gap_entry_delay_min=3, **base) if t["direction"] == "LONG")
    assert (later["entry_time"] - now["entry_time"]).total_seconds() >= 180


def test_api_saves_gap_mode_and_refuses_an_exit_past_the_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_gap_mode", "gap_entry_long", "gap_exit_short", "gap_entry_delay_min"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_gap_mode"] is False and first["gap_entry_short"] == -0.05 and first["gap_entry_delay_min"] == 0
        ok = client.put("/api/config", json={"use_gap_mode": True, "gap_entry_long": 0.08, "gap_exit_long": 0.03,
                                             "gap_giveback_pct": 40, "gap_entry_delay_min": 2})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["use_gap_mode"] is True and body["gap_entry_long"] == 0.08 and body["gap_entry_delay_min"] == 2
        bad = client.put("/api/config", json={"gap_exit_long": 0.1})
        assert bad.status_code == 400 and "Buy exit" in bad.json()["detail"]
    database.reset_engine()
