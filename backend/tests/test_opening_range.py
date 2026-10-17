"""Opening range (opening_range.py): four checks on 09:15–09:30, each its own tick box.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import opening_range
from groww_client import IST
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)

PREV = dt.date(2026, 9, 21)
DAY = dt.date(2026, 9, 22)


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _session(day, path, price, per_minute):
    """375 one-minute candles from 09:15 IST: `path(i, price)` gives each close, `per_minute(i)` the shares."""
    rows, cum = [], 0
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for i in range(375):
        o = price
        price = path(i, price)
        cum += per_minute(i)
        rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, price) + 0.05,
                     "low": min(o, price) - 0.05, "close": price, "volume": cum})
        t += dt.timedelta(minutes=1)
    return rows


def _flat(i, p):
    return 1000.0 + (0.02 if i % 2 else -0.02)


def _dip_then_rally(i, p):
    if i < 15:
        return p - 0.67  # the opening quarter-hour drifts down
    if i < 40:
        return p + 0.8  # then rallies through the opening high and crosses the SMAs up
    return p + 0.01


def _frame(opening_volume=1000):
    prev = _session(PREV, _flat, 1000.0, lambda i: 1000)
    today = _session(DAY, _dip_then_rally, prev[-1]["close"], lambda i: opening_volume if i < 15 else 1000)
    return pd.DataFrame(prev + today)


def test_volume_compares_the_opening_with_earlier_sessions():
    frame = _frame(opening_volume=3000).iloc[:-1]
    closed = frame.copy()
    assert opening_range._volume_refusal(closed, closed.tail(0), closed.tail(0), Cfg(or_volume_ratio=2)) == "opening range not complete"
    today = opening_range._today(closed)
    window = opening_range._window(today)
    assert opening_range._volume_refusal(closed, today, window, Cfg(or_volume_ratio=2)) is None  # 3× usual
    assert "needs 4" in opening_range._volume_refusal(closed, today, window, Cfg(or_volume_ratio=4))
    quiet = _frame(opening_volume=1000).iloc[:-1]
    qt = opening_range._today(quiet)
    assert opening_range._volume_refusal(quiet, qt, opening_range._window(qt), Cfg(or_volume_ratio=2)) is not None


def test_spread_needs_the_gap_to_be_wide_and_widening():
    cfg = Cfg(or_spread_pct=0.1)
    def bars(fast, slow):
        n = len(fast)
        ts = [int(dt.datetime(2026, 9, 22, 9, 15, tzinfo=IST).timestamp()) + 60 * i for i in range(n)]
        return pd.DataFrame({"ts": ts, "close": [100.0] * n, "sma_9": fast, "sma_21": slow})
    widening = bars([100.0, 100.2, 100.5], [100.0, 100.0, 100.0])
    assert opening_range._spread_refusal(widening, "LONG", cfg) is None
    assert opening_range._spread_refusal(widening, "SHORT", cfg) is not None
    narrowing = bars([100.0, 100.5, 100.3], [100.0, 100.0, 100.0])
    assert "not pulling apart" in opening_range._spread_refusal(narrowing, "LONG", cfg)
    tiny = bars([100.0, 100.01, 100.02], [100.0, 100.0, 100.0])
    assert "not pulling apart" in opening_range._spread_refusal(tiny, "LONG", cfg)


def test_break_needs_the_930_close_beyond_the_window():
    frame = _frame().iloc[:-1]
    today = opening_range._today(frame)
    window = opening_range._window(today)
    high = float(window["high"].max())
    before_930 = today.iloc[: len(window)]  # only the 09:15–09:29 candles so far
    assert opening_range._break_refusal(before_930, window, "LONG") is not None  # 09:30 not closed yet
    after = today[pd.to_datetime(today["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.time >= dt.time(9, 30)]
    first_break = after[after["close"] > high]
    assert not first_break.empty  # the rally does break the opening high later in the day
    cut = int(first_break.index[0])
    through = today.loc[: cut]
    assert opening_range._break_refusal(through, window, "LONG") is None


@pytest.mark.asyncio
async def test_break_keeps_buys_to_closes_beyond_the_opening_high(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    frame = _frame()
    plain = await replay_symbol(frame, "SIMO", [DAY], _settings(db))
    gated = await replay_symbol(frame, "SIMO", [DAY], _settings(db, use_or_break=True))
    today = frame[frame["ts"] >= int(dt.datetime(2026, 9, 22, 9, 15, tzinfo=IST).timestamp())]
    high = float(today[today["ts"] < int(dt.datetime(2026, 9, 22, 9, 30, tzinfo=IST).timestamp())]["high"].max())
    longs_plain = [t for t in plain if t["direction"] == "LONG"]
    longs_gated = [t for t in gated if t["direction"] == "LONG"]
    assert longs_plain
    assert len(longs_gated) <= len(longs_plain)
    assert all(t["entry_price"] > high for t in longs_gated)


@pytest.mark.asyncio
async def test_volume_check_blocks_all_trades_when_the_opening_is_too_quiet(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    frame = _frame(opening_volume=3000)
    loud = await replay_symbol(frame, "SIMO", [DAY], _settings(db, use_or_volume=True, or_volume_ratio=2))
    too_high = await replay_symbol(frame, "SIMO", [DAY], _settings(db, use_or_volume=True, or_volume_ratio=4))
    assert loud and not too_high
    assert all(t["entry_time"] >= dt.datetime(2026, 9, 22, 9, 30) for t in loud)


def test_settings_api_saves_the_opening_range_checks(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_or_volume", "use_or_vwap", "use_or_spread", "use_or_break", "or_volume_ratio", "or_spread_pct"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_or_volume"] is False and first["use_or_break"] is False
        assert (first["or_volume_ratio"], first["or_spread_pct"]) == (2.0, 0.1)
        ok = client.put("/api/config", json={"use_or_break": True, "or_volume_ratio": 3})
        assert ok.status_code == 200 and ok.json()["use_or_break"] is True and ok.json()["or_volume_ratio"] == 3
        assert client.put("/api/config", json={"or_spread_pct": -1}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"use_or_vwap": True})
        assert own.status_code == 200 and own.json()["use_or_vwap"] is True
    database.reset_engine()
