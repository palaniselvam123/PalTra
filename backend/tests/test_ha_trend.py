"""Heikin Ashi trend read (ha_trend.py): the trend from the candles' colours, wicks and bodies, never the SMAs.

Synthetic candles only; no Groww call, no order path.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import chop_scan
import ha_trend

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 11, 16)


def _bars(rows: list[tuple[float, float, float, float]], minutes: int = 15) -> pd.DataFrame:
    start = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    return pd.DataFrame([
        {"ts": int((start + dt.timedelta(minutes=minutes * i)).timestamp()), "open": o, "high": h, "low": lo, "close": c,
         "atr_14": 2.0}
        for i, (o, h, lo, c) in enumerate(rows)
    ])


def _today() -> int:
    return chop_scan._ist_day(int(dt.datetime.combine(DAY, dt.time(12, 0), tzinfo=IST).timestamp()))


def _strong_up(n: int = 6, start: float = 100.0):
    # Green candles with no lower wick, growing a little, higher lows.
    out, p = [], start
    for _ in range(n):
        out.append((p, p + 1.2, p, p + 1.0))
        p += 1.0
    return out


def test_colours_and_doji():
    bars = _bars([(100, 101, 99.9, 100.8), (100.8, 101, 100, 100.0), (100, 101, 99, 100.02)])
    assert list(ha_trend.colours(bars)) == [1, -1, 0]


def test_flips_skip_dojis_and_count_only_colour_changes():
    rows = [(100, 101, 99.9, 100.8), (100.8, 101.5, 100.7, 101.4), (101.4, 102, 101, 101.42),  # green, green, doji
            (101.4, 101.5, 100.5, 100.6), (100.6, 101.6, 100.5, 101.5)]                        # red, green
    hits = ha_trend.flips(_bars(rows), _today())
    assert [h["side"] for h in hits] == ["BEARISH", "BULLISH"]


def test_a_clean_green_run_is_a_strong_bullish_trend():
    bars = _bars(_strong_up(6))
    read = ha_trend.checks(bars, vwap=100.0, price=106.0)
    assert read["trend"] == "STRONG_BULLISH" and read["trend_side"] == "BULLISH"
    assert read["trend_mode"] == "HEIKIN_ASHI" and all(read["trend_checks"].values())
    # The real price under VWAP: VWAP is required, so not strong.
    assert ha_trend.checks(bars, vwap=110.0, price=106.0)["trend"] is None


def test_wicks_against_the_trend_and_a_fresh_red_candle_break_it():
    wicky = [(p, p + 1.2, p - 0.8, p + 1.0) for p in (100, 101, 102, 103, 104)]  # lower wicks
    assert ha_trend.checks(_bars(wicky), 99.0, 105.0)["trend_checks"]["nowick"] is False
    turned = _strong_up(6) + [(106, 106.2, 105, 105.1)]  # one red candle: the side flips at once
    read = ha_trend.checks(_bars(turned), 100.0, 105.0)
    assert read["trend_side"] == "BEARISH" and read["trend"] is None


def test_run_read_counts_the_colour_run_and_its_move():
    since, candles, move, peak = ha_trend.run_read(_bars(_strong_up(6)))
    assert candles == 6 and move > 0 and peak == move


def test_phases_need_five_candles_of_one_colour():
    today_start = int(dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST).timestamp())
    found = ha_trend.phases(_bars(_strong_up(6)), today_start)
    assert [p["side"] for p in found] == ["BULL"]
    short = ha_trend.phases(_bars(_strong_up(3)), today_start)
    assert [p["side"] for p in short] == ["CHOP"] and ha_trend.summary(short)["phase_early"] is True


def test_score_from_flips():
    flip = {"ts": 0, "close": 1.0, "side": "BULLISH"}
    assert ha_trend.score([], None, None, 15) == "TRENDING"
    assert ha_trend.score([flip], 90.0, None, 15) == "TRENDING"
    assert ha_trend.score([flip] * 5, 10.0, 60.0, 15) == "CHOPPY"
    assert ha_trend.score([flip] * 2, 20.0, 30.0, 15) == "CHOPPY"  # runs under 3 candles
    assert ha_trend.score([flip] * 2, 20.0, 90.0, 15) == "MIXED"
