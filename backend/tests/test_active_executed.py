"""Executed buyers / sellers on the Most active scan (active_stocks.flow_read) and the alert / auto-arm judging by
the waiting orders, the executed volume or both (scalp_alerts.control_side, auto_arm.active_hits)."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

import auto_arm
import scalp_alerts
import trade_flow
from app.core.market_clock import IST
from app.services import active_stocks
from app.services.active_stocks import ActiveScanner, clean_auto, flow_read

DAY = dt.date(2026, 11, 18)
OPEN = int(dt.datetime(2026, 11, 18, 9, 15, tzinfo=IST).timestamp())
NOW = dt.datetime(2026, 11, 18, 10, 0, 30, tzinfo=IST)


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("SCALP_ALERTS_FILE", str(tmp_path / "scalp_alerts.json"))
    monkeypatch.setenv("ACTIVE_AUTO_FILE", str(tmp_path / "active_auto.json"))
    monkeypatch.setattr(active_stocks, "CALL_GAP_SEC", 0)
    # No recorded seconds: every minute reads the candle's shape.
    monkeypatch.setattr(trade_flow.tick_store, "between", lambda *_a, **_k: [])
    trade_flow.reset()
    scalp_alerts._SENT["pressure"].clear()
    scalp_alerts.recent.clear()
    scalp_alerts.last.clear()
    monkeypatch.setattr(scalp_alerts, "_log_loaded", False)
    yield
    trade_flow.reset()


def _bars(minutes: int, up_from: int, vol: int = 1000) -> list[tuple]:
    """1-minute candles from 09:15 with Groww's running volume: red closes at the low before `up_from`, green
    closes at the high from it on."""
    out, total = [], 0
    for i in range(minutes):
        total += vol
        if i < up_from:
            out.append((OPEN + 60 * i, 100.0, 100.0, 99.0, 99.0, total))
        else:
            out.append((OPEN + 60 * i, 99.0, 100.0, 99.0, 100.0, total))
    return out


def test_flow_read_reads_the_window_and_the_day_and_never_the_forming_minute():
    # 09:15-10:00 closed is 45 minutes, plus the forming 10:00 one: 30 red then 16 green.
    frame = pd.DataFrame(_bars(46, 30), columns=["ts", "open", "high", "low", "close", "volume"])
    got = flow_read("X", frame, NOW.timestamp(), 15)
    # The last 15 closed minutes (09:45-10:00) are all green: bought at market.
    assert got["flow_buy"] == 100.0
    assert (got["flow_from"], got["flow_to"]) == ("09:45", "10:00")
    assert got["flow_src"] == "candles"
    # The day: the first minute has no volume of its own (Groww's running total), so 29 red and 15 green minutes.
    assert got["flow_day_buy"] == pytest.approx(round(100 * 15 / 44, 1))
    whole = flow_read("X", frame, NOW.timestamp(), 0)
    assert whole["flow_buy"] == whole["flow_day_buy"] and whole["flow_from"] == "09:15"
    assert flow_read("X", frame.iloc[:0], NOW.timestamp(), 15) == {}


def test_auto_settings_keep_the_executed_read():
    s = clean_auto({"flow_on": False, "flow_minutes": 30, "flow_top": 9999})
    assert (s.flow_on, s.flow_minutes, s.flow_top) == (False, 30, active_stocks.FLOW_TOP_MAX)
    s = clean_auto({"flow_minutes": 7, "flow_top": "x"})
    assert (s.flow_on, s.flow_minutes, s.flow_top) == (True, 15, 60)
    scanner = ActiveScanner()
    with pytest.raises(ValueError):
        scanner.set_auto(flow_minutes=7)
    assert scanner.set_auto(flow_minutes=5, flow_top=20).flow_minutes == 5
    view = scanner.auto_view()
    assert view["flow_minutes"] == 5 and view["flow_top"] == 20 and view["flow_on"] is True


class _Groww:
    def __init__(self):
        self.candle_calls: list[str] = []

    async def get_quote_payload(self, symbol):
        value = {"BIG": 3_000_000, "MID": 2_000_000, "SMALL": 100_000}[symbol]
        return {"last_price": 100.0, "volume": value, "average_price": 100.0,
                "ohlc": {"open": 100, "high": 101, "low": 99, "close": 99.5},
                "total_buy_quantity": 300, "total_sell_quantity": 100}

    async def get_daily_volumes(self, symbol, days=20, end=None):
        return [1_000_000] * days

    async def get_candles_window(self, symbol, interval, start, end):
        assert interval == 1 and start == dt.datetime(2026, 11, 18, 9, 15) and end.tzinfo is None
        self.candle_calls.append(symbol)
        return _bars(46, 30 if symbol == "BIG" else 46)

    async def place_order(self, *_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError("the scan must not place orders")


def test_the_scan_reads_executed_volume_for_the_busiest_stocks_only():
    scanner = ActiveScanner()
    scanner.set_auto(flow_top=10, flow_minutes=15)
    client = _Groww()
    asyncio.run(scanner.scan(client, symbols=["BIG", "MID", "SMALL"], now=NOW))
    rows = {r["symbol"]: r for r in scanner.snapshot()["rows"]}
    assert sorted(client.candle_calls) == ["BIG", "MID", "SMALL"]
    assert rows["BIG"]["flow_buy"] == 100.0       # green over the last 15 minutes
    assert rows["MID"]["flow_buy"] == 0.0         # red all day
    assert scanner.snapshot(sort="flow")["flow"] == {"on": True, "minutes": 15, "top": 10}
    # Busiest N only, and off = no candle download at all.
    scanner.auto.flow_top = 1
    client.candle_calls.clear()
    asyncio.run(scanner.scan(client, symbols=["BIG", "MID", "SMALL"], now=NOW))
    assert client.candle_calls == ["BIG"]
    assert {r["symbol"]: r["flow_buy"] for r in scanner.snapshot()["rows"]}["MID"] is None
    scanner.set_auto(flow_on=False)
    client.candle_calls.clear()
    asyncio.run(scanner.scan(client, symbols=["BIG", "MID", "SMALL"], now=NOW))
    assert client.candle_calls == []


def test_control_side_by_waiting_executed_or_both():
    row = {"buy_share": 0.75, "flow_buy": 30.0}          # buyers waiting, sellers doing the trading
    assert scalp_alerts.control_side(row, "WAITING", 70, 60) == ("BUY", 75.0, 30.0)
    assert scalp_alerts.control_side(row, "EXECUTED", 70, 60) == ("SELL", 25.0, 70.0)
    assert scalp_alerts.control_side(row, "BOTH", 70, 60) is None
    agree = {"buy_share": 0.8, "flow_buy": 65.0}
    assert scalp_alerts.control_side(agree, "BOTH", 70, 60) == ("BUY", 80.0, 65.0)
    assert scalp_alerts.control_side(agree, "BOTH", 70, 70) is None
    # No executed read (older scans, or a stock outside the busiest N) never passes EXECUTED / BOTH.
    assert scalp_alerts.control_side({"buy_share": 0.8}, "EXECUTED", 70, 60) is None
    assert scalp_alerts.control_side({"buy_share": 0.8}, "BOTH", 70, 60) is None
    assert scalp_alerts.control_side({"buy_share": 0.8}, "WAITING", 70, 60) == ("BUY", 80.0, None)


def test_the_alert_judges_by_executed_volume_when_asked():
    scalp_alerts.save({"pressure_on": True, "pressure_basis": "EXECUTED", "pressure_flow_pct": 65})
    sent: list[str] = []

    async def send(text):
        sent.append(text)

    rows = [
        {"symbol": "SBIN", "buy_share": 0.74, "flow_buy": 30.0, "ltp": 812.4, "value_cr": 300},
        {"symbol": "TCS", "buy_share": 0.5, "flow_buy": 70.0, "ltp": 4000.0, "value_cr": 200},
        {"symbol": "ITC", "buy_share": 0.9, "flow_buy": None, "ltp": 450.0, "value_cr": 100},
    ]
    now = dt.datetime(2026, 11, 18, 11, 0, tzinfo=IST)
    picks = asyncio.run(scalp_alerts.after_active_scan({"rows": rows}, send, now))
    assert [(p["symbol"], p["side"], p["pct"]) for p in picks] == [("SBIN", "SELL", 70.0), ("TCS", "BUY", 70.0)]
    assert "executed volume 65% or more" in sent[0]
    assert "SBIN · 🔴 Sellers 26% waiting / 70% executed" in sent[0]
    s = scalp_alerts.load()
    assert (s.pressure_basis, s.pressure_flow_pct) == ("EXECUTED", 65.0)
    assert scalp_alerts.clean({"pressure_basis": "NONSENSE", "pressure_flow_pct": 200}).pressure_basis == "WAITING"
    assert scalp_alerts.clean({"pressure_flow_pct": 200}).pressure_flow_pct == 95.0


def test_auto_arm_replay_reads_the_same_rule_from_saved_scans():
    scans = [{"as_of": "2026-11-18T09:40:00+05:30", "rows": [
        {"symbol": "A", "buy_share": 0.8, "flow_buy": 70.0, "value_cr": 50, "read_at": "2026-11-18T09:41:10+05:30"},
        {"symbol": "B", "buy_share": 0.8, "flow_buy": 20.0, "value_cr": 50, "read_at": "2026-11-18T09:41:20+05:30"},
        {"symbol": "C", "buy_share": 0.5, "value_cr": 50, "read_at": "2026-11-18T09:41:30+05:30"},
    ]}]
    assert auto_arm.active_hits(scans, 75, "BOTH", 0) == [("09:42", "A", "BUY", 80.0), ("09:42", "B", "BUY", 80.0)]
    assert auto_arm.active_hits(scans, 75, "BOTH", 0, basis="EXECUTED", flow_pct=65) == [
        ("09:42", "A", "BUY", 70.0), ("09:42", "B", "SELL", 80.0)]
    assert auto_arm.active_hits(scans, 75, "BOTH", 0, basis="BOTH", flow_pct=65) == [("09:42", "A", "BUY", 80.0)]
