"""Sideways test (regime_study.py): read-only research on past candles, no order path."""
from __future__ import annotations

import asyncio
import datetime as dt
import math

import numpy as np
import pandas as pd
import pytest

import regime_study
from regime_study import IST

DAYS = [dt.date(2026, 10, 26), dt.date(2026, 10, 27), dt.date(2026, 10, 28)]


def _tape(days=DAYS, kind="mixed") -> pd.DataFrame:
    """1-minute candles, 09:15-15:30, for each day: a straight climb (trend) or a sine wave (chop)."""
    rows = []
    price = 100.0
    for n, day in enumerate(days):
        start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        trend = kind == "trend" or (kind == "mixed" and n % 2 == 0)
        for i in range(375):
            if trend:
                price += 0.02
            else:
                price = 100.0 + 0.6 * math.sin(i / 6.0)
            o = price - 0.01
            rows.append(
                {"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": o, "high": price + 0.05, "low": o - 0.05, "close": price, "volume": 1000 * (i + 1)}
            )
    return pd.DataFrame(rows)


def test_a_steady_climb_trends_and_a_sine_wave_chops():
    trend = regime_study.analyze_stock("UP", _tape(kind="trend"), DAYS, minutes=5, window=60)
    chop = regime_study.analyze_stock("SIDE", _tape(kind="chop"), DAYS, minutes=5, window=60)
    assert trend and chop
    assert all(s["trended"] for s in trend[3:])  # past the warm-up, a straight line always trends
    assert sum(s["trended"] for s in chop) / len(chop) < 0.3
    assert np.mean([s["crosses"] for s in chop]) > np.mean([s["crosses"] for s in trend])
    assert np.mean([s["efficiency"] for s in trend[3:]]) > 0.9
    s = trend[-1]
    assert set(s) >= {"crosses", "turns", "adx", "efficiency", "overlap", "gap", "trended", "next_crosses", "move_atr", "cross_pnl_pct"}
    # The last check is 14:30: its 60-minute window ends by 15:30.
    assert {x["at"] for x in trend} <= {"10:00", "11:00", "12:00", "13:00", "14:00", "14:30"}


def test_no_candles_gives_no_samples():
    assert regime_study.analyze_stock("X", None, DAYS) == []
    assert regime_study.analyze_stock("X", pd.DataFrame(), DAYS) == []


def test_summarize_ranks_the_signals_against_the_base_rate():
    samples = regime_study.analyze_stock("UP", _tape(kind="trend"), DAYS) + regime_study.analyze_stock(
        "SIDE", _tape(kind="chop"), DAYS
    )
    rep = regime_study.summarize(samples, min_bucket=1)
    assert rep["samples"] == len(samples) and rep["stocks"] == 2
    keys = [s["key"] for s in rep["signals"]]
    assert set(keys) == set(regime_study.BUCKETS)
    spreads = [s["spread"] for s in rep["signals"]]
    assert spreads == sorted(spreads, reverse=True)
    assert rep["best"] is not None and rep["signals"][0]["spread"] >= 10
    eff = next(s for s in rep["signals"] if s["key"] == "efficiency")
    full = [b for b in eff["buckets"] if b["samples"]]
    assert all("trended_pct" in b and "vs_base" in b for b in full)
    assert regime_study.summarize([]) == {"samples": 0, "signals": [], "best": None}


def test_trading_days_skip_weekends():
    days = regime_study.trading_days(dt.date(2026, 11, 2), 5)  # a Monday
    assert len(days) == 5 and all(d.weekday() < 5 for d in days) and days[-1] == dt.date(2026, 10, 30)


@pytest.mark.asyncio
async def test_a_study_reads_each_stock_and_saves_its_report(tmp_path, monkeypatch):
    monkeypatch.setenv("REGIME_STUDY_FILE", str(tmp_path / "regime.json"))
    monkeypatch.setattr(regime_study, "trading_days", lambda end, count: DAYS)
    st = regime_study.RegimeStudy()

    async def fetch(name, start, end):
        if name == "BAD":
            raise RuntimeError("no candles")
        return _tape(kind="trend" if name == "UP" else "chop")

    st.start(["up", "side", "BAD", "UP"], fetch, days=3, minutes=5, window=60, sma_fast=9, sma_slow=21)
    assert st.running and st.total == 3
    with pytest.raises(RuntimeError):
        st.start(["X"], fetch, 3, 5, 60, 9, 21)
    for _ in range(200):
        if not st.running:
            break
        await asyncio.sleep(0.02)
    assert st.status == "DONE" and st.done == 3
    assert st.errors and st.errors[0]["symbol"] == "BAD"
    assert st.report["stocks"] == 2
    again = regime_study.RegimeStudy()
    assert again.report == st.report and again.status == "DONE"


def test_api_starts_and_reports(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("REGIME_STUDY_FILE", str(tmp_path / "regime.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(regime_study, "_STUDY", None)
    monkeypatch.setattr(regime_study, "trading_days", lambda end, count: DAYS)
    monkeypatch.setattr(main, "market_is_open", lambda *a, **k: False)

    async def fake_fetch(broker, name, start, end):
        return _tape(kind="mixed")

    monkeypatch.setattr(main, "fetch_frame", fake_fetch)
    with TestClient(main.app) as client:
        assert client.get("/api/regime-study").json()["status"] == "IDLE"
        assert client.post("/api/regime-study/start", json={"symbols": ["TCS"], "minutes": 7}).status_code == 400
        assert client.post("/api/regime-study/start", json={"symbols": ["TCS"], "window": 45}).status_code == 400
        assert client.post("/api/regime-study/start", json={"symbols": []}).status_code == 400
        ok = client.post("/api/regime-study/start", json={"symbols": ["TCS", "INFY"], "days": 3})
        assert ok.status_code == 200 and ok.json()["total"] == 2
        for _ in range(100):
            view = client.get("/api/regime-study").json()
            if view["status"] != "RUNNING":
                break
            import time

            time.sleep(0.05)
        assert view["status"] == "DONE" and view["report"]["samples"] > 0
    database.reset_engine()


def _sample(symbol, trended, crosses, pnl, at="10:00", day="2026-10-01"):
    return {
        "symbol": symbol, "day": day, "at": at, "crosses": crosses, "turns": 0, "adx": 22.0,
        "efficiency": 0.6 if trended else 0.1, "overlap": 0.4, "gap": 0.1, "trended": trended,
        "next_crosses": crosses, "move_atr": 1.5 if trended else 0.3, "cross_pnl_pct": pnl,
    }


def test_by_stock_suggests_a_bot_per_stock():
    samples = []
    for i in range(10):
        samples.append(_sample("TREND", True, 0, 0.4, day=f"2026-10-{i + 1:02d}"))
        samples.append(_sample("CHOP", False, 3, -0.2, day=f"2026-10-{i + 1:02d}"))
        samples.append(_sample("MIXED", i % 2 == 0, 1, 0.01, day=f"2026-10-{i + 1:02d}"))
    samples.append(_sample("THIN", True, 0, 0.5))
    rows = {r["symbol"]: r for r in regime_study.by_stock(samples)}
    assert rows["TREND"]["bot"] == 3 and rows["TREND"]["trend_score"] == 100
    assert rows["CHOP"]["bot"] == 2 and rows["CHOP"]["chop_score"] == 100
    assert rows["MIXED"]["bot"] == 1
    assert rows["THIN"]["bot"] is None and rows["THIN"]["verdict"] == "NOT_ENOUGH"
    assert any("Bot 3 because" in w for w in rows["TREND"]["why"])
    # Trending first, the stock with too few windows last.
    order = [r["symbol"] for r in regime_study.by_stock(samples)]
    assert order[0] == "TREND" and order[-1] == "THIN"
    assert "by_stock" in regime_study.summarize(samples)
