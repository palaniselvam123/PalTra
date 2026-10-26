"""Classic daily pivots (pivots.py): levels from the previous session, the pivot target exit, the chart levels."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import pivots
from strategy_engine import IST


def _day(day: dt.date, high: float, low: float, close: float) -> list[dict]:
    start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    rows = []
    for i, (h, lo, c) in enumerate([(high, low + 1, low + 2), (high - 1, low, close)]):
        ts = int((start + dt.timedelta(minutes=i)).timestamp())
        rows.append({"ts": ts, "open": c, "high": h, "low": lo, "close": c, "volume": 100})
    return rows


FRI, MON = dt.date(2026, 10, 23), dt.date(2026, 10, 26)
TAPE = pd.DataFrame(_day(dt.date(2026, 10, 22), 2400, 2350, 2360) + _day(FRI, 2300, 2250, 2280) + _day(MON, 2290, 2270, 2285))


def test_levels_from_the_previous_session():
    lv = pivots.levels(TAPE, MON)  # Friday: H 2300, L 2250, C 2280
    assert lv["day"] == "2026-10-23"
    assert lv["p"] == pytest.approx(2276.67) and lv["r1"] == pytest.approx(2303.33) and lv["s1"] == pytest.approx(2253.33)
    assert lv["r2"] == pytest.approx(2326.67) and lv["s2"] == pytest.approx(2226.67)
    assert pivots.levels(TAPE, dt.date(2026, 10, 22)) is None  # nothing earlier
    assert pivots.levels(pd.DataFrame(), MON) is None


def test_target_picks_the_first_level_beyond_the_entry():
    lv = pivots.levels(TAPE, MON)
    assert pivots.target_for("LONG", 2280, lv, "R1") == ("R1", lv["r1"])
    assert pivots.target_for("LONG", 2310, lv, "R1") == ("R2", lv["r2"])  # already past R1
    assert pivots.target_for("LONG", 2330, lv, "R1") is None
    assert pivots.target_for("SHORT", 2280, lv, "R1") == ("S1", lv["s1"])
    assert pivots.target_for("SHORT", 2280, lv, "R2") == ("S2", lv["s2"])
    assert pivots.target_for("LONG", 2280, lv, "OFF") is None
    assert pivots.target_mode(type("C", (), {"pivot_target": "x"})()) == "OFF"


@pytest.mark.asyncio
@pytest.mark.parametrize("ltp,mode,closes", [(2304.0, "R1", True), (2300.0, "R1", False), (2304.0, "OFF", False)])
async def test_watch_pivot_target_books_the_profit(monkeypatch, tmp_path, ltp, mode, closes):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    import database
    from strategy_engine import OpenPosition, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._tapes["ACME"] = TAPE
    eng._focus = "ACME"
    eng.position = OpenPosition(
        direction="LONG", qty=10, entry_price=2280.0, ma_cross_price=2280.0, atr_at_entry=5.0, sl_trigger=0.0,
        sl_order_id="", entry_order_id="E", entry_time=dt.datetime.combine(MON, dt.time(10, 0), tzinfo=IST),
        trade_id=0, mode="PAPER", stop_active=False,
    )
    eng.status, eng.ltp = "RUNNING", ltp
    exits = []

    async def fake_exit(cfg, price, reason):
        exits.append(reason)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    await eng._watch_pivot_target(type("C", (), {"symbol": "ACME", "pivot_target": mode})())
    assert exits == (["PIVOT_TARGET"] if closes else [])
    database.reset_engine()


def test_api_saves_pivot_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "pivot_target" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["pivot_target"] == "OFF"
        ok = client.put("/api/config", json={"pivot_target": "R1"})
        assert ok.status_code == 200 and ok.json()["pivot_target"] == "R1"
        assert client.put("/api/config", json={"pivot_target": "R3"}).status_code == 422
        chart = client.get("/api/chart").json()
        assert "pivots" in chart and chart["pivot_target"] == "R1"
    database.reset_engine()
