"""Fresh trend entry check (fresh_entry.py): a new cross + VWAP + widening gap + volume. Entries only."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd

import fresh_entry
from strategy_engine import _entry_block

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _frame(today_fast: list[float], today_close: list[float], vol_per_candle: int = 2000) -> pd.DataFrame:
    """5-minute candles: two flat earlier sessions (1,000 shares a candle), then today, plus one forming candle.
    The slow SMA is 100 throughout; `volume` is Groww's running session total."""
    rows = []
    for day in (dt.date(2026, 10, 26), dt.date(2026, 10, 27)):
        start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        for i in range(12):
            rows.append({"ts": int((start + dt.timedelta(minutes=5 * i)).timestamp()), "open": 100.0, "high": 100.2, "low": 99.8,
                         "close": 100.0, "sma_9": 99.9, "sma_21": 100.0, "volume": 1000 * (i + 1)})
    start = dt.datetime.combine(dt.date(2026, 10, 28), dt.time(9, 15), tzinfo=IST)
    for i, (fast, close) in enumerate(zip(today_fast + [today_fast[-1]], today_close + [today_close[-1]])):
        rows.append({"ts": int((start + dt.timedelta(minutes=5 * i)).timestamp()), "open": close - 0.1, "high": close + 0.3, "low": close - 0.3,
                     "close": close, "sma_9": fast, "sma_21": 100.0, "volume": vol_per_candle * (i + 1)})
    return pd.DataFrame(rows)


CFG = SimpleNamespace(use_fresh=True, fresh_rvol=1.5)
UP_FAST = [99.9, 99.95, 100.4, 100.6]   # crossed above 100 two candles ago, gap widening
UP_CLOSE = [99.9, 100.0, 100.5, 100.7]


def test_a_new_cross_above_vwap_with_volume_passes_for_a_buy_only():
    frame = _frame(UP_FAST, UP_CLOSE)
    assert fresh_entry.entry_reason(frame, "LONG", CFG) is None
    assert "new cross" in fresh_entry.passed_text(frame, "LONG", CFG)
    short = fresh_entry.entry_reason(frame, "SHORT", CFG)
    assert short.startswith("Fresh trend:") and "no SMA cross to this side" in short


def test_each_part_refuses_with_its_reason():
    old = _frame([100.3, 100.4, 100.5, 100.6, 100.7], [100.8, 100.9, 101.0, 101.1, 101.2])  # above all day: no cross
    assert "no SMA cross to this side in the last 3 candles" in fresh_entry.entry_reason(old, "LONG", CFG)
    narrowing = _frame([99.9, 99.95, 100.6, 100.4], UP_CLOSE)
    assert "is not wider than" in fresh_entry.entry_reason(narrowing, "LONG", CFG)
    under = _frame(UP_FAST, [99.9, 100.0, 100.5, 100.1])  # the close fell back under today's average price
    assert "not both above VWAP" in fresh_entry.entry_reason(under, "LONG", CFG)
    quiet = _frame(UP_FAST, UP_CLOSE, vol_per_candle=1000)  # the usual volume, not 1.5x
    assert "volume 1.0× usual, needs 1.5×" in fresh_entry.entry_reason(quiet, "LONG", CFG)
    assert fresh_entry.entry_reason(quiet, "LONG", SimpleNamespace(use_fresh=True, fresh_rvol=0)) is None  # 0 = not checked


def test_unknown_volume_and_short_frames_refuse():
    frame = _frame(UP_FAST, UP_CLOSE)
    today_only = frame[frame["ts"] >= int(dt.datetime(2026, 10, 28, tzinfo=IST).timestamp())]
    assert "volume vs usual not known yet" in fresh_entry.entry_reason(today_only, "LONG", CFG)
    assert "not ready" in fresh_entry.entry_reason(frame.iloc[:3], "LONG", CFG)
    assert "not ready" in fresh_entry.entry_reason(None, "LONG", CFG)


def test_entry_block_reads_it_only_when_ticked():
    quiet = _frame(UP_FAST, UP_CLOSE, vol_per_candle=1000)
    assert "Fresh trend" in _entry_block(quiet, "LONG", CFG, only="fresh")
    assert _entry_block(quiet, "LONG", SimpleNamespace(use_fresh=False), only="fresh") is None
    assert fresh_entry.min_rvol(SimpleNamespace()) == 1.5 and fresh_entry.min_rvol(SimpleNamespace(fresh_rvol=99)) == 20.0


def test_entry_block_joins_it_with_the_other_checks_and_names_it_on_the_chart():
    from strategy_engine import _CHECK_NAMES, STOCK_FIELDS

    quiet = _frame(UP_FAST, UP_CLOSE, vol_per_candle=1000)
    assert "Fresh trend: volume 1.0× usual" in _entry_block(quiet, "LONG", CFG)  # read with every other ticked check
    assert _entry_block(_frame(UP_FAST, UP_CLOSE), "LONG", CFG) is None
    assert ("fresh", "Fresh trend") in _CHECK_NAMES
    assert "use_fresh" in STOCK_FIELDS and "fresh_rvol" in STOCK_FIELDS  # a stock can have its own


def test_api_saves_the_fresh_trend_check(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    from main import app

    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["use_fresh"] is False and got["fresh_rvol"] == 1.5  # off by default
        ok = client.put("/api/config", json={"use_fresh": True, "fresh_rvol": 2})
        assert ok.status_code == 200 and ok.json()["use_fresh"] is True and ok.json()["fresh_rvol"] == 2
        assert client.put("/api/config", json={"fresh_rvol": 50}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"use_fresh": True, "fresh_rvol": 0})
        assert own.status_code == 200 and own.json()["use_fresh"] is True and own.json()["fresh_rvol"] == 0
        assert client.get("/api/config/defaults").json()["use_fresh"] is False
    database.reset_engine()
