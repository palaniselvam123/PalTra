"""The pick backtests' options: Heikin Ashi candles and the time window (trend_picks.py, auto_arm.py).

Read-only: nothing here places an order.
"""
from __future__ import annotations

import datetime as dt
import math
import random

import pandas as pd

from auto_arm import IST, strong_read

DAYS = [dt.date(2026, 11, 9), dt.date(2026, 11, 10), dt.date(2026, 11, 11), dt.date(2026, 11, 12)]
TODAY = DAYS[-1]


def _day(day: dt.date, path, per_minute: int = 1000, seed: int = 1) -> list[dict]:
    rnd = random.Random(seed)
    rows, cum, prev = [], 0, path(0)
    for i in range(375):
        t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=i)
        close = path(i + 1) + rnd.uniform(-0.3, 0.3)
        cum += per_minute
        rows.append({"ts": int(t.timestamp()), "open": prev, "high": max(prev, close) + 0.2,
                     "low": min(prev, close) - 0.2, "close": close, "volume": cum})
        prev = close
    return rows


def _flat(price: float):
    return lambda i: price + 2 * math.sin(i / 7)


def _tape(today, yesterday=None, per_minute: int = 1000) -> pd.DataFrame:
    rows = []
    for d in DAYS[:2]:
        rows += _day(d, _flat(1000), seed=d.day)
    rows += _day(DAYS[2], yesterday or _flat(1000), seed=3)
    rows += _day(TODAY, today, per_minute=per_minute, seed=4)
    return pd.DataFrame(rows)


def _read(tape: pd.DataFrame, hhmm: str, minutes: int = 5) -> dict:
    h, m = map(int, hhmm.split(":"))
    return strong_read(tape, TODAY, dt.time(h, m), minutes, 9, 21)


def _trend(sign: int):
    """Flat to 10:30, a steady 1.5 % move to 12:00, flat after."""
    def path(i: int) -> float:
        if i < 75:
            return 1000 + 1.5 * math.sin(i / 7)
        if i < 165:
            return 1000 + sign * (i - 75) * 15 / 90
        return 1000 + sign * 15 + 1.5 * math.sin(i / 7)
    return path


def test_heikin_ashi_candles_can_be_read_by_the_backtests():
    """The pick backtest and the auto-arm replay read the trend on Heikin Ashi candles when asked (prices stay real)."""
    import trend_picks
    from auto_arm import AutoArmRule

    tape = _tape(_trend(1))
    normal = strong_read(tape, TODAY, dt.time(11, 16), 5, 9, 21)
    ha = strong_read(tape, TODAY, dt.time(11, 16), 5, 9, 21, ha=True)
    assert normal["trend"] == ha["trend"] == "STRONG_BULLISH"
    assert ha["sma_gap_pct"] != normal["sma_gap_pct"]  # the averages are worked out on other candles
    assert ha["vwap"] == normal["vwap"]  # the day's average price is the real one

    rule = trend_picks.TrendPickRule(pick_time=dt.time(11, 16), candles="HA")
    assert rule.as_dict()["candles"] == "HA" and AutoArmRule(candles="HA").as_dict()["candles"] == "HA"
    picks = trend_picks.picks_for_day({"UP": tape}, TODAY, rule)
    assert [p["symbol"] for p in picks] == ["UP"] and picks[0]["side"] == "LONG"
    assert trend_picks.TrendPickRule().as_dict()["candles"] == "NORMAL"


def test_pick_backtests_run_inside_a_time_window():
    """Pick from any minute 09:15-15:25 and stop trading at 'Trade until' (09:20-15:30); the replay day ends there."""
    import pytest
    import trend_picks
    from active_picks import ActivePickRule
    from auto_arm import AutoArmRule
    from replay import ReplaySession

    assert trend_picks.parse_window("09:15", "11:00") == (dt.time(9, 15), dt.time(11, 0))
    assert trend_picks.parse_window("14:45", "15:30") == (dt.time(14, 45), None)  # 15:30 = the usual close
    assert trend_picks.parse_window("10:00", None) == (dt.time(10, 0), None)
    for bad in (("09:10", None), ("15:28", None), ("11:00", "10:30"), ("10:00", "15:40"), ("x", None)):
        with pytest.raises(ValueError):
            trend_picks.parse_window(*bad)

    rule = trend_picks.TrendPickRule(pick_time=dt.time(9, 30), until=dt.time(11, 15))
    assert rule.as_dict()["until"] == "11:15"
    assert ActivePickRule(until=dt.time(12, 0)).as_dict()["until"] == "12:00"
    assert AutoArmRule().as_dict()["until"] is None

    session = ReplaySession()
    session.day = TODAY
    session.rule = rule
    assert session._day_end() == dt.datetime.combine(TODAY, dt.time(11, 15), tzinfo=IST)
    session.rule = trend_picks.TrendPickRule()
    assert session._day_end().time() == dt.time(15, 30)
