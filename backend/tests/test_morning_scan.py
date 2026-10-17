"""09:30 morning checklist (morning_scan.py): five checks on the three opening 5-minute candles.

Read-only: nothing here places an order.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import morning_scan
from groww_client import IST

TODAY = dt.date(2026, 9, 22)  # a Tuesday


def _earlier_days(n: int) -> list[dt.date]:
    days: list[dt.date] = []
    d = TODAY - dt.timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= dt.timedelta(days=1)
    return sorted(days)


def _session(day: dt.date, price, per_minute: float, minutes: int = 375) -> list[dict]:
    """1-minute candles from 09:15 with Groww's running session volume; `price(i)` is minute i's close."""
    rows, cum = [], 0.0
    t = dt.datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    prev = price(0)
    for i in range(minutes):
        close = price(i)
        cum += per_minute
        rows.append(
            {"ts": int(t.timestamp()), "open": prev, "high": max(prev, close) + 0.02, "low": min(prev, close) - 0.02, "close": close, "volume": cum}
        )
        prev = close
        t += dt.timedelta(minutes=1)
    return rows


def _tape(today_price, today_volume: float = 2500.0, sessions: int = 10, today_minutes: int = 25) -> pd.DataFrame:
    rows: list[dict] = []
    for day in _earlier_days(sessions):
        rows += _session(day, lambda i: 100.0 + (0.01 if i % 2 else 0.0), 1000.0)
    rows += _session(TODAY, today_price, today_volume, minutes=today_minutes)
    return pd.DataFrame(rows)


def _now(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm, tzinfo=IST)


@pytest.fixture(autouse=True)
def _fresh_cache():
    morning_scan._BASELINE.clear()
    yield
    morning_scan._BASELINE.clear()


def test_clean_bullish_morning_passes_all_five():
    tape = _tape(lambda i: 100.0 + 0.1 * i)
    row = morning_scan.read(tape, 9, 21, _now(9, 40), symbol="RUNUP")
    assert row.ready and row.side == morning_scan.LONG
    assert row.checks == {"orb": True, "rvol": True, "vwap": True, "gap": True, "candles": True}, row.notes
    assert row.score == 5 and row.runner
    assert row.rvol == pytest.approx(2.5) and row.rvol_sessions == 10
    assert row.or_high == pytest.approx(101.42) and row.price == pytest.approx(102.4)
    assert row.greens == 3
    assert "above the 09:15-09:30 high" in row.notes["orb"]


def test_clean_bearish_morning_is_a_short_runner():
    tape = _tape(lambda i: 100.0 - 0.1 * i)
    row = morning_scan.read(tape, 9, 21, _now(9, 40), symbol="RUNDOWN")
    assert row.side == morning_scan.SHORT
    assert all(row.checks.values()), row.notes
    assert row.runner and row.short_score == 5 and row.long_score < 4
    assert row.reds == 3


def test_sideways_chop_stays_in_the_chop_zone():
    # Zig-zag around 100 on ordinary volume: inside the range, across VWAP, mixed colours, flat SMAs.
    tape = _tape(lambda i: 100.0 + (0.15 if i % 2 else -0.15), today_volume=1000.0)
    row = morning_scan.read(tape, 9, 21, _now(9, 40), symbol="CHOP")
    assert row.ready
    assert not row.checks["orb"] and not row.checks["rvol"] and not row.checks["gap"]
    assert row.score < morning_scan.RUNNER_MIN and not row.runner
    assert "inside the opening range" in row.notes["orb"]
    assert row.rvol == pytest.approx(1.0)


def test_before_0930_the_window_is_not_ready():
    tape = _tape(lambda i: 100.0 + 0.1 * i, today_minutes=12)
    row = morning_scan.read(tape, 9, 21, _now(9, 27), symbol="EARLY")
    assert row is not None and not row.ready and row.score == 0 and not row.runner
    assert "Waiting for 09:30" in row.notes["orb"]


def test_break_waits_for_a_close_after_0930():
    # At 09:30:30 the window is closed but no 1-minute candle after 09:30 has closed yet.
    tape = _tape(lambda i: 100.0 + 0.1 * i, today_minutes=16)
    now = _now(9, 30) + dt.timedelta(seconds=30)
    row = morning_scan.read(tape, 9, 21, now, symbol="EDGE")
    assert row.ready and row.checks["orb"] is False
    assert "Waiting for a 1-minute candle" in row.notes["orb"]


def test_volume_needs_two_earlier_sessions_and_one_point_eight_times():
    one = morning_scan.read(_tape(lambda i: 100.0 + 0.1 * i, sessions=1), 9, 21, _now(9, 40), symbol="ONE")
    assert one.checks["rvol"] is False and one.rvol_sessions == 1
    assert "only 1 earlier session" in one.notes["rvol"]
    weak = morning_scan.read(_tape(lambda i: 100.0 + 0.1 * i, today_volume=1700.0), 9, 21, _now(9, 40), symbol="WEAK")
    assert weak.rvol == pytest.approx(1.7) and weak.checks["rvol"] is False
    # The other four still pass: 4 of 5 is a runner.
    assert weak.score == 4 and weak.runner


def test_a_wick_through_vwap_fails_the_vwap_check():
    def price(i: int) -> float:
        return 100.0 + 0.1 * i

    tape = _tape(price)
    today = tape[pd.to_datetime(tape["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date == TODAY]
    # The 09:22 minute dips to 99.0, through the VWAP, and closes back up.
    hit = today.index[7]
    tape.loc[hit, "low"] = 99.0
    row = morning_scan.read(tape, 9, 21, _now(9, 40), symbol="WICK")
    assert row.checks["vwap"] is False
    assert "09:22 touched VWAP" in row.notes["vwap"]


def test_baseline_is_kept_for_the_day_so_later_scans_download_less():
    now = _now(9, 40)
    assert morning_scan.download_start("RUNUP", now).date() == (now - dt.timedelta(days=morning_scan.LONG_DAYS)).date()
    morning_scan.analyze(_tape(lambda i: 100.0 + 0.1 * i), 9, 21, now, symbol="RUNUP")
    assert len(morning_scan._BASELINE[("RUNUP", TODAY)]) == 10
    assert morning_scan.download_start("RUNUP", now).date() == (now - dt.timedelta(days=morning_scan.SHORT_DAYS)).date()
    # A later scan with only the short download still compares with all ten sessions.
    short = _tape(lambda i: 100.0 + 0.1 * i, sessions=2)
    row = morning_scan.analyze(short, 9, 21, now, symbol="RUNUP")
    assert row.rvol_sessions == 10


@pytest.mark.asyncio
async def test_scanner_lists_runners_first():
    tapes = {
        "CHOP": _tape(lambda i: 100.0 + (0.15 if i % 2 else -0.15), today_volume=1000.0),
        "RUNUP": _tape(lambda i: 100.0 + 0.1 * i),
    }

    async def fetch(symbol, start, end):
        return tapes[symbol]

    scanner = morning_scan.MorningScanner()
    out = scanner.start(["CHOP", "RUNUP"], fetch, now=_now(9, 40))
    assert out["running"] is True
    await scanner._task
    snap = scanner.snapshot()
    assert snap["running"] is False and snap["error"] is None
    assert [r["symbol"] for r in snap["rows"]] == ["RUNUP", "CHOP"]
    assert snap["rows"][0]["runner"] is True and snap["rules"]["runner_min"] == 4
