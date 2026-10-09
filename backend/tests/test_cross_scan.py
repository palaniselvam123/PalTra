"""Cross scan (cross_scan.py): stocks whose SMA fast / slow are about to cross on the N-minute candle.

Read-only: candles in, a ranked list out. No order path is touched; the fetch
here is a stub returning synthetic 1-minute candles.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import numpy as np
import pandas as pd
import pytest

import cross_scan
from indicators import enrich
from strategy_engine import minutes_until_cross

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 10, 8)  # a Thursday


def _minutes(closes_by_minute: list[float], day: dt.date = DAY) -> pd.DataFrame:
    """1-minute candles from 09:15 IST, one close each (open = previous close)."""
    start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    rows, prev = [], closes_by_minute[0]
    for i, c in enumerate(closes_by_minute):
        rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": prev, "high": max(prev, c) + 0.05,
                     "low": min(prev, c) - 0.05, "close": c, "volume": 1000 * (i + 1)})
        prev = c
    return pd.DataFrame(rows)


def _at(hh: int, mm: int, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


def _path(per_candle: list[float]) -> list[float]:
    """Each 5-minute candle's five 1-minute closes ramp to its value."""
    out, prev = [], per_candle[0]
    for c in per_candle:
        out += list(np.linspace(prev, c, 6)[1:])
        prev = c
    return out


# A long fall, then a rise that is about to lift the 9 back over the 21.
FALL_THEN_RISE = [1000 - 2 * i for i in range(30)] + [942 + 3 * i for i in range(1, 9)]


def test_a_stock_rising_toward_a_cross_is_approaching_with_minutes_in_candles_times_length():
    frame = _minutes(_path(FALL_THEN_RISE))
    now = _at(12, 0)
    row = cross_scan.scan_one("TCS", frame, 9, 21, 5, now)
    assert row is not None and row.state == cross_scan.APPROACHING and row.side == cross_scan.BULLISH
    assert row.gap_pct < 0 and row.slope_pct > 0
    assert row.minutes_to_cross == pytest.approx(row.candles_to_cross * 5)


def test_the_estimate_equals_the_bots_own_heads_up_arithmetic():
    """closed candles + a dummy forming one must give minutes_until_cross the same side, candles and gap."""
    frame = _minutes(_path(FALL_THEN_RISE))
    now = _at(12, 0)
    bars = cross_scan.closed_bars(frame, 5, now)
    enriched = enrich(bars, 9, 21)
    row = cross_scan.outlook(enriched, 5, symbol="X")
    forming = enriched.iloc[[-1]].copy()
    with_forming = pd.concat([enriched, forming], ignore_index=True)
    side, candles, gap = minutes_until_cross(with_forming)
    assert row is not None and row.state == cross_scan.APPROACHING
    assert (side, ) == (("BULLISH" if row.side == cross_scan.BULLISH else "BEARISH"), )
    assert candles == pytest.approx(row.candles_to_cross) and gap == pytest.approx(row.gap_pct)


def test_the_forming_candle_is_never_read():
    frame = _minutes(_path(FALL_THEN_RISE))
    n = len(frame)
    # "Now" sits 2 minutes into the last 5-minute candle: that candle is still forming.
    last_start = dt.datetime.fromtimestamp(int(frame["ts"].iloc[-5]), IST)
    now = last_start + dt.timedelta(minutes=2)
    bars = cross_scan.closed_bars(frame, 5, now)
    assert int(bars["ts"].iloc[-1]) + 300 <= int(now.timestamp())
    assert len(bars) == (n - 5) // 5  # the forming candle is out


def test_a_cross_on_the_last_closed_candle_is_crossed_and_a_flat_stock_is_neither():
    # A rise strong enough that the 9 is already above the 21 on the last closed candle.
    up = _minutes(_path([1000 - 2 * i for i in range(30)] + [942 + 12 * i for i in range(1, 9)]))
    row = cross_scan.scan_one("A", up, 9, 21, 5, _at(12, 0))
    assert row is not None and row.side == cross_scan.BULLISH and row.state in (cross_scan.CROSSED, cross_scan.APPROACHING)
    flat = _minutes(_path([1000.0] * 40))
    assert cross_scan.scan_one("B", flat, 9, 21, 5, _at(12, 30)) is None
    # Steadily falling with the 9 far under the 21 and widening: not close to crossing.
    falling = _minutes(_path([1000 - 3 * i for i in range(40)]))
    assert cross_scan.scan_one("C", falling, 9, 21, 5, _at(12, 30)) is None


def test_crossed_state_is_reported_with_how_many_candles_ago():
    # Build gaps directly: -0.2, -0.1, +0.1 -> crossed between the last two closed candles.
    frame = pd.DataFrame({"ts": [1, 2, 3], "sma_9": [99.8, 99.9, 100.1], "sma_21": [100.0, 100.0, 100.0]})
    row = cross_scan.outlook(frame, 5, symbol="X")
    assert row.state == cross_scan.CROSSED and row.crossed_candles_ago == 0 and row.side == cross_scan.BULLISH
    older = pd.DataFrame({"ts": [1, 2, 3], "sma_9": [100.1, 99.9, 99.8], "sma_21": [100.0, 100.0, 100.0]})
    ago1 = cross_scan.outlook(older, 5, symbol="Y")
    # +0.1, -0.1, -0.2: crossed between candles 1 and 2, and stays under -> one candle ago.
    assert ago1.state == cross_scan.CROSSED and ago1.crossed_candles_ago == 1 and ago1.side == cross_scan.BEARISH


# --- the scan pass -----------------------------------------------------------------------------

def _fetch_for(frames: dict[str, pd.DataFrame], fail: set[str] = frozenset(), message: str = "boom"):
    calls: list[tuple] = []

    async def fetch(symbol, start, end):
        calls.append((symbol, start, end))
        if symbol in fail:
            raise RuntimeError(message)
        return frames[symbol]

    fetch.calls = calls
    return fetch


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(cross_scan, "CALL_GAP_SEC", 0.0)
    monkeypatch.setattr(cross_scan, "MIN_RESCAN_SEC", 0)


async def _run(scanner, names, fetch, now, **kw):
    scanner.start(names, fetch, now=now, **kw)
    await scanner._task
    return scanner.snapshot()


@pytest.mark.asyncio
async def test_a_pass_ranks_soonest_first_skips_quiet_stocks_and_reports_progress():
    soon = _minutes(_path(FALL_THEN_RISE))
    later = _minutes(_path([1000 - 2 * i for i in range(30)] + [942 + 1.2 * i for i in range(1, 9)]))
    flat = _minutes(_path([1000.0] * 40))
    fetch = _fetch_for({"SOON": soon, "LATER": later, "FLAT": flat})
    scanner = cross_scan.CrossScanner()
    snap = await _run(scanner, ["flat", "later", "soon", "soon"], fetch, _at(12, 0))
    assert snap["done"] == snap["total"] == 3 and not snap["running"] and snap["error"] is None
    names = [r["symbol"] for r in snap["rows"]]
    assert "FLAT" not in names and names[0] == "SOON"
    mins = [r["minutes_to_cross"] for r in snap["rows"] if r["state"] == cross_scan.APPROACHING]
    assert mins == sorted(mins)
    # It only asked Groww for candles: one download per stock, from days before to now.
    assert len(fetch.calls) == 3 and all(s <= e for _, s, e in fetch.calls)
    assert (fetch.calls[0][2] - fetch.calls[0][1]).days >= cross_scan.WARMUP_DAYS


@pytest.mark.asyncio
async def test_the_same_candle_is_not_scanned_twice_until_the_next_one_closes_or_forced():
    frame = _minutes(_path(FALL_THEN_RISE))
    fetch = _fetch_for({"A": frame})
    scanner = cross_scan.CrossScanner()
    await _run(scanner, ["A"], fetch, _at(12, 1))
    await _run(scanner, ["A"], fetch, _at(12, 3))  # same 5-minute candle: cached
    assert len(fetch.calls) == 1
    await _run(scanner, ["A"], fetch, _at(12, 6))  # a new candle has closed
    assert len(fetch.calls) == 2
    await _run(scanner, ["A"], fetch, _at(12, 7), force=True)
    assert len(fetch.calls) == 3


@pytest.mark.asyncio
async def test_after_the_close_the_result_stays_until_forced():
    frame = _minutes(_path(FALL_THEN_RISE))
    fetch = _fetch_for({"A": frame})
    scanner = cross_scan.CrossScanner()
    await _run(scanner, ["A"], fetch, _at(16, 0))
    await _run(scanner, ["A"], fetch, _at(18, 30))
    assert len(fetch.calls) == 1
    assert scanner.snapshot()["market_open"] is False


@pytest.mark.asyncio
async def test_when_groww_will_not_answer_it_stops_early_and_says_why():
    names = [f"S{i}" for i in range(30)]
    fetch = _fetch_for({}, fail=set(names), message="Log in to Groww on the desk Settings page first.")
    scanner = cross_scan.CrossScanner()
    snap = await _run(scanner, names, fetch, _at(12, 0))
    assert "Log in to Groww" in (snap["error"] or "") and snap["rows"] == []
    assert len(fetch.calls) < len(names)  # it gave up instead of hammering Groww
    # Nothing was cached: the next ask tries again.
    await _run(scanner, names[:2], _fetch_for({"S0": _minutes([1000.0] * 40), "S1": _minutes([1000.0] * 40)}), _at(12, 1))
    assert scanner.snapshot()["error"] is None


@pytest.mark.asyncio
async def test_a_few_failures_still_give_a_result_with_a_note():
    ok = _minutes(_path(FALL_THEN_RISE))
    fetch = _fetch_for({"A": ok, "B": ok}, fail={"C"})
    scanner = cross_scan.CrossScanner()
    snap = await _run(scanner, ["A", "B", "C"], fetch, _at(12, 0))
    assert snap["failed"] == 1 and len(snap["rows"]) == 2 and "could not be read" in snap["error"]


def test_bad_input_is_refused():
    scanner = cross_scan.CrossScanner()
    with pytest.raises(ValueError):
        scanner.start([], _fetch_for({}), now=_at(12, 0))
    with pytest.raises(ValueError):
        scanner.start(["A"], _fetch_for({}), minutes=7, now=_at(12, 0))
    assert cross_scan.clean_symbols(["tcs", "TCS", " m&m ", "bad symbol!", "", "BAJAJ-AUTO"]) == ["TCS", "M&M", "BAJAJ-AUTO"]


def test_the_endpoints_start_a_scan_with_the_desks_sma_periods_and_never_touch_orders(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/scan.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    seen: list[tuple] = []

    def fake_start(symbols, fetch, **kw):
        seen.append((symbols, kw))
        return {"running": True, "rows": []}

    monkeypatch.setattr(main.cross_scanner, "start", fake_start)
    with TestClient(main.app) as client:
        ok = client.post("/api/cross-scan/start", json={"symbols": ["TCS", "INFY"], "minutes": 5})
        assert ok.status_code == 200
        symbols, kw = seen[0]
        assert symbols == ["TCS", "INFY"] and (kw["minutes"], kw["sma_fast"], kw["sma_slow"]) == (5, 9, 21)
        assert client.post("/api/cross-scan/start", json={"symbols": ["TCS"], "minutes": 7}).status_code == 422
        assert client.get("/api/cross-scan").status_code == 200
        assert client.get("/api/state").json()["mode"] == "PAPER"
    database.reset_engine()


def test_a_scan_over_days_with_volume_resets_logs_nothing_and_reads_the_same(caplog):
    """enrich() warns at every volume reset; four days of 213 stocks flooded the log. The cross reads closes only."""
    import indicators

    days = [dt.date(2026, 10, 6), dt.date(2026, 10, 7), DAY]
    frame = pd.concat([_minutes(_path(FALL_THEN_RISE), d) for d in days], ignore_index=True)
    # Groww's running total falls back to 0 mid-session (a reset), and the next bar starts over.
    frame.loc[(frame.index % 60) < 10, "volume"] = 0  # whole 5-minute candles, so the reset survives the grouping
    now = _at(12, 0)

    indicators._VOLUME_WARNED.clear()
    with caplog.at_level("WARNING"):
        row = cross_scan.scan_one("TCS", frame, 9, 21, 5, now)
    assert [r for r in caplog.records if "minute volume unavailable" in r.getMessage()] == []

    # The same candles without any volume column give the same answer.
    plain = cross_scan.scan_one("TCS", frame.drop(columns=["volume"]), 9, 21, 5, now)
    assert row is not None and plain is not None
    cross_only = lambda r: {k: v for k, v in r.as_dict().items() if k not in ("volume", "volume_window")}  # noqa: E731
    assert cross_only(row) == cross_only(plain)
    assert row.volume is not None and plain.volume is None  # the volume itself is read separately, without enrich()


def test_activity_reads_price_speed_and_volume_over_the_last_ten_minutes():
    closes = [1000 + 0.5 * i for i in range(40)]  # +0.5 a minute
    frame = _minutes(closes)  # running volume 1000, 2000, ... per minute
    out = cross_scan.activity(frame)
    last, base = closes[-1], closes[-1 - 10]
    assert out["move_pct"] == pytest.approx((last / base - 1) * 100)
    assert out["speed_pct_per_min"] == pytest.approx(out["move_pct"] / 10)
    assert out["volume"] == 40_000  # the running total at the last minute
    assert out["volume_window"] == 10_000  # ten minutes of 1,000 shares
    assert out["window_min"] == 10


def test_a_falling_price_has_a_negative_speed_and_a_volume_reset_gives_no_window_volume():
    closes = [1000 - 0.5 * i for i in range(40)]
    frame = _minutes(closes)
    frame.loc[frame.index >= 35, "volume"] = 50  # the running total fell back: a reset
    out = cross_scan.activity(frame)
    assert out["speed_pct_per_min"] < 0 and out["move_pct"] < 0
    assert out["volume"] == 50 and out["volume_window"] is None


def test_activity_uses_only_the_last_session_and_copes_with_thin_data():
    two_days = pd.concat([_minutes([100.0] * 30, dt.date(2026, 10, 7)), _minutes([200 + i for i in range(30)], DAY)], ignore_index=True)
    out = cross_scan.activity(two_days)
    assert out["move_pct"] > 0 and out["move_pct"] < 10  # not the jump from 100 to 200 between days
    assert cross_scan.activity(two_days.iloc[:1])["speed_pct_per_min"] is None
    assert cross_scan.activity(pd.DataFrame())["volume"] is None
    assert cross_scan.activity(None)["volume"] is None


def test_scan_rows_carry_the_activity_figures(caplog):
    frame = _minutes(_path(FALL_THEN_RISE))
    with caplog.at_level("WARNING"):
        row = cross_scan.scan_one("TCS", frame, 9, 21, 5, _at(12, 0))
    assert row is not None
    d = row.as_dict()
    assert d["volume"] and d["volume_window"] and d["speed_pct_per_min"] is not None and d["window_min"] == 10
    assert [r for r in caplog.records if "minute volume unavailable" in r.getMessage()] == []


def test_download_concurrency_is_one_by_default_and_clamped(monkeypatch):
    monkeypatch.delenv("CROSS_SCAN_CONCURRENCY", raising=False)
    assert cross_scan._concurrency() == 1
    for raw, want in (("3", 3), ("0", 1), ("99", 4), ("junk", 1), ("", 1)):
        monkeypatch.setenv("CROSS_SCAN_CONCURRENCY", raw)
        assert cross_scan._concurrency() == want
