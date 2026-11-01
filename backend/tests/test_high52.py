"""52-week high (high52.py): the entry check and the Scalp page's scan. Read-only; no order path."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import high52
from groww_client import IST
from strategy_engine import _entry_block

TODAY = dt.date(2026, 9, 22)  # a Tuesday


def _daily(closes: list[float], last_day: dt.date = TODAY, high_bump: float = 1.0) -> pd.DataFrame:
    """One daily candle per weekday ending on `last_day`, oldest first; the high sits `high_bump` above the close."""
    rows = []
    day = last_day
    for close in reversed(closes):
        while day.weekday() >= 5:
            day -= dt.timedelta(days=1)
        t = dt.datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
        rows.append({"ts": int(t.timestamp()), "open": close, "high": close + high_bump, "low": close - 1.0, "close": close, "volume": 1000.0})
        day -= dt.timedelta(days=1)
    return pd.DataFrame(list(reversed(rows)))


@pytest.fixture(autouse=True)
def _fresh():
    high52.clear()
    yield
    high52.clear()


def test_levels_read_the_52_weeks_before_the_day():
    # 281 sessions ≈ 56 weeks; the peak 500 sits 30 sessions back, the older 600 falls outside the 52-week window.
    closes = [600.0] + [100.0] * 249 + [500.0] + [100.0] * 29 + [120.0]
    frame = _daily(closes)
    lv = high52.levels_from(frame, TODAY, "X")
    assert lv is not None and lv.high == 501.0 and lv.low == 99.0 and lv.sessions >= 250
    assert lv.prev_close == 100.0 and lv.first_close == 100.0 and lv.day == TODAY.isoformat()
    # The window moves with the day: as of the peak's own day the older 600 is still inside the 52 weeks,
    # and the peak itself (that day's candle) is not counted.
    peak_day = dt.date.fromisoformat(lv.high_date)
    assert high52.levels_from(frame, peak_day, "X").high == 601.0
    assert high52.levels_from(pd.DataFrame(), TODAY) is None


def _minute_frame(day: dt.date, closes: list[float]) -> pd.DataFrame:
    rows, t = [], dt.datetime(day.year, day.month, day.day, 10, 0, tzinfo=IST)
    for c in closes + [closes[-1]]:  # plus the forming candle
        rows.append({"ts": int(t.timestamp()), "open": c, "high": c + 0.2, "low": c - 0.2, "close": c})
        t += dt.timedelta(minutes=1)
    return pd.DataFrame(rows)


def test_entry_check_needs_a_close_at_the_level_and_reads_the_side():
    high52.remember(high52.Levels("SBIN", TODAY.isoformat(), 500.0, "2026-08-10", 300.0, "2026-01-05", 250), TODAY)
    cfg = SimpleNamespace(symbol="SBIN", use_high52=True, high52_side="LONG", high52_pct=0.0)
    assert high52.entry_reason(_minute_frame(TODAY, [498.0, 500.0]), "LONG", cfg) is None
    why = high52.entry_reason(_minute_frame(TODAY, [498.0, 495.0]), "LONG", cfg)
    assert "1.00% under the 52-week high 500.00 (2026-08-10)" in why and "at or above" in why
    assert "sells are off" in high52.entry_reason(_minute_frame(TODAY, [299.0]), "SHORT", cfg)
    # Within 2 % counts with the band set.
    near = SimpleNamespace(symbol="SBIN", use_high52=True, high52_side="LONG", high52_pct=2.0)
    assert high52.entry_reason(_minute_frame(TODAY, [491.0]), "LONG", near) is None
    assert "within 2% of it" in high52.entry_reason(_minute_frame(TODAY, [489.0]), "LONG", near)
    # BOTH: a sell at the 52-week low.
    both = SimpleNamespace(symbol="SBIN", use_high52=True, high52_side="BOTH", high52_pct=0.0)
    assert high52.entry_reason(_minute_frame(TODAY, [299.5]), "SHORT", both) is None
    assert "above the 52-week low 300.00" in high52.entry_reason(_minute_frame(TODAY, [310.0]), "SHORT", both)
    assert "SHORT" in high52.side_of(SimpleNamespace(high52_side="short")) and high52.side_of(SimpleNamespace()) == "LONG"
    assert high52.pct_of(SimpleNamespace(high52_pct=99)) == high52.MAX_PCT
    assert "at the 52-week high 500.00" in high52.passed_text(_minute_frame(TODAY, [500.0]), "LONG", cfg)


def test_not_loaded_refuses_and_the_entry_block_reads_it_only_when_ticked():
    cfg = SimpleNamespace(symbol="TCS", use_high52=True, high52_side="LONG", high52_pct=0.0)
    frame = _minute_frame(TODAY, [100.0, 101.0])
    assert "not loaded yet" in high52.entry_reason(frame, "LONG", cfg)
    assert "not loaded yet" in _entry_block(frame, "LONG", cfg)
    assert _entry_block(frame, "LONG", SimpleNamespace(symbol="TCS", use_high52=False)) is None
    high52.remember(high52.Levels("TCS", TODAY.isoformat(), 101.0, "2026-05-01", 50.0, "2026-02-02", 200), TODAY)
    assert _entry_block(frame, "LONG", cfg) is None


@pytest.mark.asyncio
async def test_preload_fills_every_replayed_day_from_one_download(monkeypatch):
    closes = [100.0] * 240 + [200.0] + [100.0] * 10
    frame = _daily(closes)
    calls: list = []

    async def fake_fetch(broker, symbol, start, end):
        calls.append((symbol, start, end))
        return frame

    monkeypatch.setattr(high52, "fetch_daily", fake_fetch)
    days = [TODAY - dt.timedelta(days=7), TODAY]
    assert await high52.preload(object(), "ABC", days) is True
    assert len(calls) == 1 and all(high52.cached("ABC", d) is not None for d in days)
    assert high52.cached("ABC", TODAY).high == 201.0
    # Already cached: no second download.
    assert await high52.preload(object(), "ABC", days) is True and len(calls) == 1


def test_ensure_skips_a_broker_without_a_groww_session():
    # A replay or research broker has no SDK: nothing is scheduled and nothing breaks.
    high52.ensure(SimpleNamespace(), ["SBIN"], TODAY)
    assert high52.cached("SBIN", TODAY) is None


def test_scan_row_measures_the_distance_and_the_breakout():
    now = dt.datetime(TODAY.year, TODAY.month, TODAY.day, 11, 0, tzinfo=IST)
    near = high52.analyze(_daily([100.0] * 250 + [150.0] + [140.0] * 5 + [148.0]), now, symbol="NEAR")
    assert near is not None and near.high52 == 151.0 and near.dist_high_pct == pytest.approx((151 - 148) / 151 * 100)
    assert near.broke_high is False and near.days_since_high == 6 and near.day_move_pct == pytest.approx(148 / 140 * 100 - 100)
    broke = high52.analyze(_daily([100.0] * 250 + [150.0] + [140.0] * 5 + [152.0]), now, symbol="BROKE")
    assert broke.broke_high is True and broke.dist_high_pct < 0
    far = high52.analyze(_daily([100.0] * 250 + [150.0] + [100.0] * 6), now, symbol="FAR")
    assert far.dist_high_pct == pytest.approx((151 - 100) / 151 * 100)
    rows = sorted([near, far, broke], key=high52.sort_key)
    assert [r.symbol for r in rows] == ["BROKE", "NEAR", "FAR"]
    assert high52.analyze(pd.DataFrame(), now) is None


@pytest.mark.asyncio
async def test_scanner_lists_breakouts_first():
    tapes = {
        "FAR": _daily([100.0] * 250 + [150.0] + [100.0] * 6),
        "BROKE": _daily([100.0] * 250 + [150.0] + [140.0] * 5 + [152.0]),
    }

    async def fetch(symbol, start, end):
        return tapes[symbol]

    scanner = high52.High52Scanner()
    out = scanner.start(["FAR", "BROKE"], fetch, now=dt.datetime(TODAY.year, TODAY.month, TODAY.day, 11, 0, tzinfo=IST))
    assert out["running"] is True
    await scanner._task
    snap = scanner.snapshot()
    assert snap["running"] is False and snap["error"] is None and snap["weeks"] == 52
    assert [r["symbol"] for r in snap["rows"]] == ["BROKE", "FAR"] and snap["rows"][0]["broke_high"] is True


def test_api_saves_the_52_week_check_and_serves_the_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["use_high52"] is False and got["high52_side"] == "LONG" and got["high52_pct"] == 0.0
        ok = client.put("/api/bots/2/config", json={"use_high52": True, "high52_side": "BOTH", "high52_pct": 1.5}).json()
        assert ok["use_high52"] is True and ok["high52_side"] == "BOTH" and ok["high52_pct"] == 1.5
        assert client.put("/api/bots/2/config", json={"high52_side": "UP"}).status_code == 422
        assert client.put("/api/bots/2/config", json={"high52_pct": 50}).status_code == 422
        assert client.get("/api/high52-scan").json()["rows"] == []
    database.reset_engine()
