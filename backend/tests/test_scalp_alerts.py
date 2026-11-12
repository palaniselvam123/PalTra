"""Scalp page alerts (scalp_alerts.py): strong trend with 6-7 checks, buyers / sellers at 70 % or more."""
import asyncio
import datetime as dt

import pytest

import scalp_alerts
from groww_client import IST

NOW = dt.datetime(2026, 11, 10, 11, 0, tzinfo=IST)  # a Tuesday, market open


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SCALP_ALERTS_FILE", str(tmp_path / "scalp_alerts.json"))
    scalp_alerts._SENT["strong"].clear()
    scalp_alerts._SENT["pressure"].clear()
    scalp_alerts.recent.clear()


def trend_row(symbol, trend, passed, ltp=100.0):
    names = ["stack", "vwap", "slope", "fan", "hold", "structure", "run"]
    return {"symbol": symbol, "trend": trend, "ltp": ltp, "minutes": 5, "day_move_pct": 1.2,
            "trend_checks": {n: i < passed for i, n in enumerate(names)}}


def test_off_by_default_sends_nothing():
    sent = []

    async def send(text):
        sent.append(text)

    rows = [trend_row("SBIN", "STRONG_BULLISH", 7)]
    assert asyncio.run(scalp_alerts.after_trend_scan({"rows": rows}, send, NOW)) == []
    assert asyncio.run(scalp_alerts.after_active_scan({"rows": [{"symbol": "SBIN", "buy_share": 0.9, "ltp": 1}]}, send, NOW)) == []
    assert sent == []


def test_strong_trend_needs_enough_checks_and_sends_once_a_day():
    scalp_alerts.save({"strong_on": True, "strong_min_checks": 7})
    sent = []

    async def send(text):
        sent.append(text)

    rows = [trend_row("SBIN", "STRONG_BULLISH", 7, 812.4), trend_row("TCS", "STRONG_BEARISH", 6), trend_row("ITC", None, 7)]
    picks = asyncio.run(scalp_alerts.after_trend_scan({"rows": rows}, send, NOW))
    assert [p["symbol"] for p in picks] == ["SBIN"]
    assert "SBIN ▲ UP · ₹812.40" in sent[0] and "7 of 7 checks" in sent[0] and "missed: none" in sent[0]
    # The same stock and side is not sent twice the same day.
    assert asyncio.run(scalp_alerts.after_trend_scan({"rows": rows}, send, NOW)) == []
    # Six checks are enough once the owner asks for six; the missed check is named.
    scalp_alerts.save({"strong_min_checks": 6})
    picks = asyncio.run(scalp_alerts.after_trend_scan({"rows": rows}, send, NOW))
    assert [(p["symbol"], p["side"], p["passed"]) for p in picks] == [("TCS", "DOWN", 6)]
    assert "TCS ▼ DOWN" in sent[1] and "missed: no recent cross" in sent[1]


def test_strong_trend_side_filter():
    scalp_alerts.save({"strong_on": True, "strong_side": "DOWN"})
    rows = [trend_row("SBIN", "STRONG_BULLISH", 7), trend_row("TCS", "STRONG_BEARISH", 7)]
    assert [p["symbol"] for p in scalp_alerts.strong_picks(rows, scalp_alerts.load(), NOW)] == ["TCS"]


def test_buyers_or_sellers_at_70_percent():
    scalp_alerts.save({"pressure_on": True})
    sent = []

    async def send(text):
        sent.append(text)

    rows = [
        {"symbol": "SBIN", "buy_share": 0.74, "ltp": 812.4, "change_pct": 1.1, "rvol": 2.3, "value_cr": 300},
        {"symbol": "TCS", "buy_share": 0.22, "ltp": 4000.0, "change_pct": -0.8, "rvol": None, "value_cr": 200},
        {"symbol": "INFY", "buy_share": 0.69, "ltp": 1500.0, "value_cr": 100},
        {"symbol": "ITC", "buy_share": None, "ltp": 450.0, "value_cr": 100},
    ]
    picks = asyncio.run(scalp_alerts.after_active_scan({"rows": rows}, send, NOW))
    assert [(p["symbol"], p["side"], p["pct"]) for p in picks] == [("TCS", "SELL", 78.0), ("SBIN", "BUY", 74.0)]
    assert "SBIN · 🟢 Buyers 74% · ₹812.40 · day +1.10% · volume 2.3× usual" in sent[0]
    assert "TCS · 🔴 Sellers 78% · ₹4000.00" in sent[0]
    assert asyncio.run(scalp_alerts.after_active_scan({"rows": rows}, send, NOW)) == []


def test_pressure_filters_and_market_hours():
    scalp_alerts.save({"pressure_on": True, "pressure_side": "BUY", "pressure_min_value_cr": 250, "pressure_pct": 80})
    rows = [
        {"symbol": "SBIN", "buy_share": 0.85, "ltp": 1.0, "value_cr": 300},
        {"symbol": "TCS", "buy_share": 0.10, "ltp": 1.0, "value_cr": 300},
        {"symbol": "INFY", "buy_share": 0.90, "ltp": 1.0, "value_cr": 100},
        {"symbol": "ITC", "buy_share": 0.75, "ltp": 1.0, "value_cr": 300},
    ]
    assert [p["symbol"] for p in scalp_alerts.pressure_picks(rows, scalp_alerts.load(), NOW)] == ["SBIN"]

    async def send(text):
        raise AssertionError("no alert outside market hours")

    night = NOW.replace(hour=20)
    assert asyncio.run(scalp_alerts.after_active_scan({"rows": rows}, send, night)) == []


def test_settings_are_clamped():
    s = scalp_alerts.save({"strong_min_checks": 9, "pressure_pct": 10, "strong_side": "SIDEWAYS"})
    assert (s.strong_min_checks, s.pressure_pct, s.strong_side) == (7, 50.0, "BOTH")


def test_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/scalp-alerts").json()
        assert got["strong_on"] is False and got["pressure_on"] is False and got["pressure_pct"] == 70
        got = client.put("/api/scalp-alerts", json={"strong_on": True, "strong_min_checks": 7, "pressure_pct": 80}).json()
        assert (got["strong_on"], got["strong_min_checks"], got["pressure_pct"], got["pressure_on"]) == (True, 7, 80, False)
        assert client.put("/api/scalp-alerts", json={"pressure_pct": 20}).status_code == 422
