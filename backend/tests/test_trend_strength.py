"""Strong trend read (trend_strength.py) through the Chop scan's `analyze`.

Read-only; synthetic 1-minute candles, no network, no Groww call, no order path.
"""
from __future__ import annotations

import math

import pandas as pd

import chop_scan
import trend_strength
from tests.test_chop_scan import _at, _minutes


def _row(tape: list[float]):
    return chop_scan.analyze(_minutes(tape), 9, 21, 5, _at(13, 0), symbol="X")


def _down_with_bounces() -> list[float]:
    # The owner's example: a steady fall with small bounces that fail under the fast SMA.
    return [500 - i * 0.12 + 0.4 * math.sin(i / 4) for i in range(225)]  # 09:15-13:00


def test_a_clean_downtrend_reads_strong_bearish():
    row = _row(_down_with_bounces())
    assert row is not None
    assert row.trend == trend_strength.STRONG_BEARISH
    assert row.trend_side == "BEARISH"
    assert row.trend_score >= 85
    assert row.vwap is not None and row.ltp < row.vwap
    assert row.day_move_pct is not None and row.day_move_pct < -4
    d = row.as_dict()
    assert d["trend"] == "STRONG_BEARISH" and set(d["trend_checks"]) == set(trend_strength.CHECKS)


def test_the_bullish_mirror_reads_strong_bullish():
    row = _row([1000 - p for p in _down_with_bounces()])
    assert row is not None and row.trend == trend_strength.STRONG_BULLISH and row.trend_side == "BULLISH"


def test_a_sideways_chop_is_not_strong():
    row = _row([500 + 1.5 * math.sin(i / 6) for i in range(225)])
    assert row is not None and row.trend is None
    assert not row.trend_checks.get("run")


def test_a_fresh_reversal_is_not_strong_yet():
    # Fell all morning, then turned up: the SMAs have just crossed, so no run yet.
    tape = [500 - i * 0.12 for i in range(180)] + [478.4 + i * 0.4 for i in range(45)]
    row = _row(tape)
    assert row is not None and row.trend is None


def test_short_data_gives_no_read():
    assert trend_strength.read(pd.DataFrame(), None)["trend"] is None
