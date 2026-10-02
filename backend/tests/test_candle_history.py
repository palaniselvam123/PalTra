"""From/To chart history: read-only Groww candle download for the terminal."""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest

import candle_history
from candle_history import HistoryError, check_range, load_history, parse_ist
from groww_client import IST


class _CandleOnlySdk:
    """Answers candle requests only. Any other SDK call fails the test."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def get_historical_candle_data(self, *, trading_symbol, exchange, segment, start_time, end_time, interval_in_minutes, timeout):
        assert exchange == "NSE" and segment == "CASH" and interval_in_minutes == 1
        self.calls.append((start_time, end_time))
        start = dt.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        end = dt.datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        rows = []
        day = start.date()
        while day <= end.date():
            if day.weekday() < 5:
                t = dt.datetime.combine(day, dt.time(9, 15))
                for i in range(375):
                    bar = t + dt.timedelta(minutes=i)
                    if start <= bar <= end:
                        price = 100 + (i % 30)
                        rows.append([bar.strftime("%Y-%m-%dT%H:%M:%S"), price, price + 1, price - 1, price + 0.5, 1000])
            day += dt.timedelta(days=1)
        return {"candles": rows}

    def __getattr__(self, name):
        raise AssertionError(f"history must not call SDK.{name}")


class _Broker:
    def __init__(self, token="tok", sdk=None):
        self.token = token
        self.sdk = sdk or _CandleOnlySdk()

    def adopt_saved_session(self, *, force=False):
        return None

    def _require_sdk(self):
        return self.sdk

    def _recover_from_auth_failure(self):
        return False


_CFG = SimpleNamespace(sma_fast=9, sma_slow=21, atr_period=14, atr_multiplier=1.5)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/hist.db")
    import database

    database.reset_engine()
    database.init_db()
    yield database
    database.reset_engine()


def test_parse_ist_accepts_picker_and_plain_formats():
    assert parse_ist("2026-09-29T09:15") == dt.datetime(2026, 9, 29, 9, 15)
    assert parse_ist("2026-09-29 15:30") == dt.datetime(2026, 9, 29, 15, 30)
    assert parse_ist("2026-09-29", end_of_day=True) == dt.datetime(2026, 9, 29, 23, 59)
    with pytest.raises(HistoryError):
        parse_ist("yesterday")


def test_range_checks():
    now = dt.datetime(2026, 10, 1, 12, 0)
    with pytest.raises(HistoryError, match="before"):
        check_range("TCS", dt.datetime(2026, 9, 30, 10), dt.datetime(2026, 9, 30, 9), now)
    with pytest.raises(HistoryError, match="30 days"):
        check_range("TCS", dt.datetime(2026, 8, 1), dt.datetime(2026, 9, 30), now)
    with pytest.raises(HistoryError, match="stock"):
        check_range("bad symbol!", dt.datetime(2026, 9, 29), dt.datetime(2026, 9, 30), now)
    # A To in the future is clamped to now.
    _, _, end = check_range("tcs", dt.datetime(2026, 10, 1, 9), dt.datetime(2026, 10, 2), now)
    assert end == now


def test_long_range_is_split_into_seven_day_requests(db):
    broker = _Broker()
    out = asyncio.run(load_history(broker, "tcs", "2026-09-01T09:15", "2026-09-29T15:30", _CFG))
    assert out["symbol"] == "TCS"
    assert len(broker.sdk.calls) >= 5
    for start, end in broker.sdk.calls:
        a = dt.datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
        b = dt.datetime.strptime(end, "%Y-%m-%d %H:%M:%S")
        assert b - a <= dt.timedelta(days=7)
    first = dt.datetime.fromtimestamp(out["candles"][0]["time"], IST).replace(tzinfo=None)
    last = dt.datetime.fromtimestamp(out["candles"][-1]["time"], IST).replace(tzinfo=None)
    assert first == dt.datetime(2026, 9, 1, 9, 15)
    assert last == dt.datetime(2026, 9, 29, 15, 29)
    # The warm-up bars before From mean the first shown bar already has SMAs and ATR.
    assert out["candles"][0]["sma21"] is not None
    assert out["candles"][0]["atr14"] is not None
    times = [c["time"] for c in out["candles"]]
    assert times == sorted(set(times))


def test_no_groww_login_is_a_clear_message(db):
    with pytest.raises(HistoryError, match="Log in to Groww"):
        asyncio.run(load_history(_Broker(token=""), "TCS", "2026-09-29T09:15", "2026-09-29T15:30", _CFG))


def test_groww_failure_is_reported_not_raised_raw(db):
    class _Down(_CandleOnlySdk):
        def get_historical_candle_data(self, **kwargs):
            raise RuntimeError("rate limited")

    with pytest.raises(HistoryError, match="rate limited"):
        asyncio.run(load_history(_Broker(sdk=_Down()), "TCS", "2026-09-29T09:15", "2026-09-29T15:30", _CFG))


def test_markers_show_entries_and_exits_inside_the_range(db):
    from models import TradeLog

    with db.session_factory()() as s:
        s.add(
            TradeLog(
                date="2026-09-29", symbol="TCS", direction="LONG", qty=10,
                entry_time=dt.datetime(2026, 9, 29, 10, 5), entry_price=101.0, ma_cross_price=101.0,
                atr_at_entry=1.0, sl_trigger_price=99.5,
                exit_time=dt.datetime(2026, 9, 29, 11, 0), exit_price=104.0, exit_reason="MA_CROSS",
                gross_pnl=30.0, net_pnl=12.0, mode="PAPER",
            )
        )
        s.add(
            TradeLog(
                date="2026-09-20", symbol="TCS", direction="SHORT", qty=10,
                entry_time=dt.datetime(2026, 9, 20, 10, 5), entry_price=101.0, ma_cross_price=101.0,
                atr_at_entry=1.0, sl_trigger_price=102.0, mode="PAPER",
            )
        )
        s.add(
            TradeLog(
                date="2026-09-29", symbol="INFY", direction="LONG", qty=10,
                entry_time=dt.datetime(2026, 9, 29, 10, 5), entry_price=1.0, ma_cross_price=1.0,
                atr_at_entry=1.0, sl_trigger_price=1.0, mode="PAPER",
            )
        )
        s.commit()
    out = asyncio.run(load_history(_Broker(), "TCS", "2026-09-29T09:15", "2026-09-29T15:30", _CFG))
    kinds = [(m["kind"], m["direction"], m["price"]) for m in out["markers"]]
    assert kinds == [("ENTRY", "LONG", 101.0), ("EXIT", "LONG", 104.0)]
    assert out["markers"][1]["net_pnl"] == 12.0
    assert out["markers"][0]["net_pnl"] == 12.0  # the entry is coloured by the trade's result too
    assert out["markers"][1]["reason"] == "MA_CROSS"  # the chart colours stop exits from this
    assert out["entry_price"] is None and out["sl_trigger"] is None


def test_history_endpoint_needs_groww_login(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app, engine

    monkeypatch.setattr(engine.broker, "token", "")
    monkeypatch.setattr(engine.broker, "adopt_saved_session", lambda **_: None)
    with TestClient(app) as client:
        res = client.get("/api/history", params={"symbol": "TCS", "start": "2026-09-29T09:15", "end": "2026-09-29T15:30"})
        assert res.status_code == 400
        assert "Groww" in res.json()["detail"]
        bad = client.get("/api/history", params={"symbol": "TCS", "start": "2026-09-29T10:00", "end": "2026-09-29T09:00"})
        assert bad.status_code == 400
    assert engine.broker.mode == "PAPER"
    database.reset_engine()
    get_settings.cache_clear()


def test_resample_builds_bars_from_the_0915_open():
    from candle_history import resample
    import pandas as pd

    base = int(dt.datetime(2026, 9, 29, 9, 15, tzinfo=IST).timestamp())
    rows = [
        {"ts": base + 60 * i, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "volume": 10}
        for i in range(31)
    ]
    out = resample(pd.DataFrame(rows), 15)
    assert list(out["ts"]) == [base, base + 900, base + 1800]
    first = out.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (100, 115, 99, 114.5, 150)
    # 60-minute bars also start at 09:15, then 10:15.
    hourly = resample(pd.DataFrame(rows), 60)
    assert list(hourly["ts"]) == [base]


@pytest.mark.parametrize("interval", [5, 15, 30, 60])
def test_history_at_bigger_intervals_has_formed_indicators(db, interval):
    broker = _Broker()
    out = asyncio.run(
        load_history(broker, "TCS", "2026-09-28T09:15", "2026-09-29T15:30", _CFG, interval)
    )
    assert out["interval"] == interval
    times = [c["time"] for c in out["candles"]]
    assert all(b - a >= interval * 60 for a, b in zip(times, times[1:]))
    first = dt.datetime.fromtimestamp(times[0], IST).replace(tzinfo=None)
    assert first == dt.datetime(2026, 9, 28, 9, 15)
    assert out["candles"][0]["sma21"] is not None


def test_unknown_interval_is_refused(db):
    with pytest.raises(HistoryError, match="Candle size"):
        asyncio.run(load_history(_Broker(), "TCS", "2026-09-29T09:15", "2026-09-29T15:30", _CFG, 7))
