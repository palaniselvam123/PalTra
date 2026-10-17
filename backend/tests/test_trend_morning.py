"""Trend quality, morning mode (trend_strength.py): today's candles only, before 10:00 IST.

Read-only: nothing here places an order.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import trend_strength
from groww_client import IST

TODAY = dt.date(2026, 9, 22)


def _candles(day: dt.date, rows: list[tuple[str, float, float, float, float, float]]) -> pd.DataFrame:
    """rows: (HH:MM, open, close, fast SMA, slow SMA, unused) on 5-minute candles."""
    out = []
    for hhmm, o, c, fast, slow, _ in rows:
        h, m = map(int, hhmm.split(":"))
        ts = int(dt.datetime(day.year, day.month, day.day, h, m, tzinfo=IST).timestamp())
        out.append({"ts": ts, "open": o, "high": max(o, c) + 0.1, "low": min(o, c) - 0.1, "close": c,
                    "sma_9": fast, "sma_21": slow})
    return pd.DataFrame(out)


def _tape(sessions: dict[dt.date, float], today_per_minute: float | None = None) -> pd.DataFrame:
    """1-minute candles 09:15–15:29 with cumulative volume; `sessions` maps a date to shares per minute."""
    rows = []
    cum = 0.0
    for day, per_minute in sessions.items():
        cum = 0.0
        t = dt.datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
        for _ in range(375):
            cum += per_minute
            rows.append({"ts": int(t.timestamp()), "open": 100.0, "high": 100.1, "low": 99.9, "close": 100.0, "volume": cum})
            t += dt.timedelta(minutes=1)
    return pd.DataFrame(rows)


def _now(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(TODAY.year, TODAY.month, TODAY.day, hh, mm, tzinfo=IST)


def _three_bullish_candles() -> pd.DataFrame:
    # Closed 5-minute candles at 09:15, 09:20, 09:25 (the 09:30 candle is the one that just closed)
    return _candles(TODAY, [
        ("09:15", 100.0, 100.8, 100.2, 100.0, 0),
        ("09:20", 100.8, 101.6, 100.8, 100.1, 0),
        ("09:25", 101.6, 102.4, 101.4, 100.3, 0),
    ])


def test_morning_strong_bullish_at_0930_with_volume_confirmation():
    closed = _three_bullish_candles()
    tape = _tape({dt.date(2026, 9, 17): 1000, dt.date(2026, 9, 18): 1000, dt.date(2026, 9, 21): 1000,
                  TODAY: 2000})
    read = trend_strength.read(closed, vwap=100.5, now=_now(9, 30), tape=tape)
    assert read["trend_mode"] == "MORNING"
    checks = read["trend_checks"]
    assert set(checks) == set(trend_strength.MORNING_CHECKS)
    assert checks["stack"] and checks["vwap"] and checks["slope"] and checks["fan"] and checks["hold"] and checks["run"]
    assert checks["volume"] and read["rvol"] == 2.0 and read["trend_days"] == 3
    assert read["trend"] == trend_strength.STRONG_BULLISH and read["trend_side"] == "BULLISH"
    assert read["trend_notes"]["hold"] == "3 of 3 closes on the trend side today (needs all of them until 09:30)"


def test_hold_needs_every_close_until_0930_and_yesterday_does_not_count():
    closed = _candles(TODAY, [
        ("09:15", 100.0, 100.8, 100.2, 100.0, 0),
        ("09:20", 100.8, 99.9, 100.8, 100.1, 0),  # one close back under the fast SMA
        ("09:25", 99.9, 102.4, 101.4, 100.3, 0),
    ])
    read = trend_strength.read(closed, vwap=100.5, now=_now(9, 30))
    assert read["trend_checks"]["hold"] is False
    assert read["trend_notes"]["hold"].startswith("2 of 3")
    # Yesterday's closes (all on the trend side) are not in the morning read at all.
    yesterday = _candles(dt.date(2026, 9, 21), [("15:0%d" % i, 90.0, 95.0, 90.0, 89.0, 0) for i in range(12)])
    both = pd.concat([yesterday, closed], ignore_index=True)
    assert trend_strength.read(both, vwap=100.5, now=_now(9, 30))["trend_checks"] == read["trend_checks"]


def test_between_0930_and_1000_seventy_five_percent_is_enough():
    closed = _candles(TODAY, [
        ("09:15", 100.0, 100.8, 100.2, 100.0, 0),
        ("09:20", 100.8, 101.6, 100.8, 100.1, 0),
        ("09:25", 101.6, 99.9, 101.4, 100.3, 0),  # one miss
        ("09:30", 99.9, 102.4, 101.5, 100.5, 0),
    ])
    read = trend_strength.read(closed, vwap=100.5, now=_now(9, 45))
    assert read["trend_checks"]["hold"] is True  # 3 of 4 = 75%
    assert "75%" in read["trend_notes"]["hold"]


def test_morning_threshold_is_five_of_seven():
    closed = _three_bullish_candles()
    # No volume history and VWAP above the price: 5 checks at most, so 'Strong' needs both to pass.
    read = trend_strength.read(closed, vwap=105.0, now=_now(9, 30))
    assert read["trend_checks"]["vwap"] is False
    passed = sum(read["trend_checks"].values())
    assert passed < 5 or read["trend"] is not None
    assert (read["trend"] is not None) == (passed >= trend_strength.STRONG_MORNING_MIN)


def test_volume_needs_two_earlier_sessions_and_one_and_a_half_times_usual():
    closed = _three_bullish_candles()
    one_day = _tape({dt.date(2026, 9, 21): 1000, TODAY: 5000})
    r = trend_strength.read(closed, vwap=100.5, now=_now(9, 30), tape=one_day)
    assert r["trend_checks"]["volume"] is False and r["trend_days"] == 1
    assert "not enough earlier sessions" in r["trend_notes"]["volume"]
    quiet = _tape({dt.date(2026, 9, 17): 1000, dt.date(2026, 9, 18): 1000, TODAY: 1000})
    r = trend_strength.read(closed, vwap=100.5, now=_now(9, 30), tape=quiet)
    assert r["trend_checks"]["volume"] is False and r["rvol"] == 1.0


def test_from_1000_the_mature_midday_rules_apply():
    closed = _three_bullish_candles()
    midday = trend_strength.read(closed, vwap=100.5, now=_now(10, 0))
    assert midday["trend_mode"] == "MIDDAY"
    assert midday["trend"] is None  # three candles: the 12-candle rules are not read yet
    assert midday["trend_checks"] == {}


def test_morning_read_without_now_keeps_midday_behaviour():
    closed = _three_bullish_candles()
    assert trend_strength.read(closed, vwap=100.5)["trend_mode"] == "MIDDAY"
