"""The strong-trend read judges today's current run (trend_strength.py): not one candle, not yesterday, not late.

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


def test_one_big_opening_candle_is_never_a_strong_trend():
    spike = _tape(lambda i: 1000 + 12 * min(i, 5) / 5 + 1.5 * math.sin(i / 7), per_minute=2000)
    for at in ("09:21", "09:26", "09:31", "09:46", "10:01", "10:16", "10:31", "10:46"):
        read = _read(spike, at)
        assert read["trend"] is None, (at, read["trend_checks"])
    assert "Too early" in _read(spike, "09:21")["trend_notes"]["early"]
    assert "spike" in _read(spike, "09:46")["trend_notes"]["run"]


def test_yesterdays_trend_is_not_todays():
    rise = lambda i: 1000 + max(0, i - 150) * 0.12  # noqa: E731
    top = rise(375)
    tape = _tape(lambda i: top + 2.5 * math.sin(i / 9), yesterday=rise)
    for minutes in (5, 15):
        for at in ("10:16", "10:31", "11:01", "11:31"):
            read = _read(tape, at, minutes)
            assert read["trend"] is None and not read["trend_checks"]["run"], (minutes, at)


def test_a_trend_is_called_in_its_first_half_both_ways():
    for sign, label in ((1, "STRONG_BULLISH"), (-1, "STRONG_BEARISH")):
        tape = _tape(_trend(sign))
        assert _read(tape, "10:31")["trend"] is None  # nothing has moved yet
        assert _read(tape, "11:01")["trend"] == label  # 30 minutes into a 90-minute move
        full = _read(tape, "11:16")
        assert full["trend"] == label and all(full["trend_checks"].values())  # 7 of 7 at half-way
        assert _read(tape, "11:31", minutes=15)["trend"] == label  # the 15-minute candles see it too


def test_the_label_goes_once_the_move_stops():
    tape = _tape(_trend(1))
    read = _read(tape, "13:01")  # an hour after the move ended, the price only goes sideways
    assert read["trend"] is None and not read["trend_checks"]["run"]


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
