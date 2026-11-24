"""Day high / low target (day_target.py): close a profit at today's or yesterday's high / low, whichever comes first.
PAPER only: the engine's exit is recorded, nothing is ordered."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import day_target
from strategy_engine import IST

CFG = SimpleNamespace(day_target=True, symbol="SHREECEM")


def _ts(day: dt.date, hh: int, mm: int) -> int:
    return int(dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST).timestamp())


FRI, MON = dt.date(2026, 11, 20), dt.date(2026, 11, 23)


def _tape():
    rows = [
        {"ts": _ts(FRI, 10, 0), "high": 105.0, "low": 95.0},  # yesterday: high 105, low 95
        {"ts": _ts(FRI, 15, 29), "high": 101.0, "low": 99.0},
        {"ts": _ts(MON, 9, 15), "high": 103.0, "low": 99.5},  # today before the entry: high 103, low 99.5
        {"ts": _ts(MON, 9, 40), "high": 101.0, "low": 97.0},  # today's low 97
        {"ts": _ts(MON, 9, 45), "high": 110.0, "low": 90.0},  # the entry minute itself: not read
    ]
    return pd.DataFrame(rows)


def test_levels_read_only_candles_before_the_entry_minute():
    lv = day_target.levels(_tape(), _ts(MON, 9, 45) + 20)
    assert lv["today_high"] == 103.0 and lv["today_low"] == 97.0
    assert lv["prev_high"] == 105.0 and lv["prev_low"] == 95.0 and lv["prev_day"] == FRI.isoformat()
    assert day_target.levels(None, 0) is None


def test_the_nearest_level_beyond_the_entry_wins_either_way():
    lv = day_target.levels(_tape(), _ts(MON, 9, 45) + 20)
    assert day_target.target_for("LONG", 100.0, lv, CFG) == ("today's high", 103.0, 103.0)
    assert day_target.target_for("LONG", 104.0, lv, CFG)[:2] == ("yesterday's high", 105.0)  # today's is below the entry
    assert day_target.target_for("SHORT", 100.0, lv, CFG)[:2] == ("today's low", 97.0)
    assert day_target.target_for("SHORT", 96.0, lv, CFG)[:2] == ("yesterday's low", 95.0)
    assert day_target.target_for("LONG", 106.0, lv, CFG) is None  # nothing above
    far = SimpleNamespace(day_target=True, day_target_min_pct=4.0)
    assert day_target.target_for("LONG", 100.0, lv, far)[:2] == ("yesterday's high", 105.0)  # 103 is under 4% away
    early = SimpleNamespace(day_target=True, day_target_early_pct=0.5)
    assert day_target.target_for("LONG", 100.0, lv, early)[2] == pytest.approx(103.0 * 0.995)
    assert day_target.reached("LONG", 103.0, 103.0) and not day_target.reached("LONG", 102.9, 103.0)
    assert day_target.reached("SHORT", 97.0, 97.0) and not day_target.reached("SHORT", 97.1, 97.0)


def test_skip_ignores_todays_early_high_but_keeps_yesterdays():
    entry = _ts(MON, 9, 45) + 20
    lv = day_target.levels(_tape(), entry, 15)  # 09:15 candle (high 103) left out; 09:40 still counts
    assert lv["today_high"] == 101.0 and lv["today_low"] == 97.0 and lv["prev_high"] == 105.0
    assert day_target.target_for("LONG", 100.0, lv, CFG)[:2] == ("today's high", 101.0)
    lv = day_target.levels(_tape(), entry, 30)  # every candle of today is before 09:45
    assert lv["today_high"] is None and lv["today_low"] is None
    assert day_target.target_for("LONG", 100.0, lv, CFG)[:2] == ("yesterday's high", 105.0)
    assert day_target.skip_min(SimpleNamespace(day_target_skip_min=999)) == day_target.MAX_SKIP_MIN
    assert day_target.skip_min(SimpleNamespace()) == 0


def _engine(direction: str, entry: float):
    from strategy_engine import OpenPosition, StrategyEngine

    eng = StrategyEngine()
    eng._focus = "SHREECEM"
    eng.status = "RUNNING"
    eng._tapes["SHREECEM"] = _tape()
    eng.positions["SHREECEM"] = OpenPosition(
        direction=direction, qty=1, entry_price=entry, ma_cross_price=entry, atr_at_entry=1.0, sl_trigger=entry - 5,
        sl_order_id="PAPER1", entry_order_id="PAPER0",
        entry_time=dt.datetime(2026, 11, 23, 9, 45, 20, tzinfo=IST), trade_id=4, mode="PAPER",
    )
    calls = []

    async def exit_now(cfg, price, reason):
        calls.append((price, reason))
        eng.positions.pop("SHREECEM", None)

    eng._exit_now = exit_now
    return eng, calls


@pytest.mark.asyncio
async def test_the_engine_closes_a_long_at_the_day_high():
    eng, calls = _engine("LONG", 100.0)
    eng.ltp = 102.5
    await eng._watch_day_target(CFG)
    assert calls == []
    eng.ltp = 103.1
    await eng._watch_day_target(CFG)
    assert calls == [(103.1, day_target.EXIT_REASON)]
    assert "today's high 103.00" in eng._signals["SHREECEM"]


@pytest.mark.asyncio
async def test_off_by_default_never_closes():
    eng, calls = _engine("SHORT", 100.0)
    eng.ltp = 90.0
    await eng._watch_day_target(SimpleNamespace(symbol="SHREECEM"))
    assert calls == []


def test_api_saves_the_day_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["day_target"] is False and got["day_target_min_pct"] == 0.2 and got["day_target_early_pct"] == 0
        ok = client.put("/api/bots/2/config", json={"day_target": True, "day_target_min_pct": 0.5, "day_target_early_pct": 0.05}).json()
        assert ok["day_target"] is True and ok["day_target_min_pct"] == 0.5 and ok["day_target_early_pct"] == 0.05
        assert client.put("/api/bots/2/config", json={"day_target_early_pct": 3}).status_code == 422
        assert got["day_target_skip_min"] == 0
        assert client.put("/api/bots/2/config", json={"day_target_skip_min": 15}).json()["day_target_skip_min"] == 15
        assert client.put("/api/bots/2/config", json={"day_target_skip_min": 500}).status_code == 422
    database.reset_engine()
