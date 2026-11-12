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


# ---- Fresh trend: a trend that has only just started (trend_strength.fresh) ----

def _fresh_frame(gaps: list[float], close: float = 101.0, open_: float = 100.5) -> "pd.DataFrame":
    """Closed candles whose SMA gap (fast - slow, slow = 100) follows `gaps`; the last candle's open / close as given."""
    import pandas as pd

    n = len(gaps)
    return pd.DataFrame({
        "ts": [1_792_035_900 + 300 * i for i in range(n)],
        "open": [100.0] * (n - 1) + [open_], "close": [100.0] * (n - 1) + [close],
        "sma_9": [100.0 + g for g in gaps], "sma_21": [100.0] * n,
    })


def _fresh_tape(today_per_min: int, earlier_per_min: int = 100):
    """1-minute tape: two earlier sessions and today from 09:15, Groww's running session volume."""
    import datetime as dt
    import pandas as pd

    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    rows = []
    for day, per in ((dt.date(2026, 10, 13), earlier_per_min), (dt.date(2026, 10, 14), earlier_per_min), (dt.date(2026, 10, 15), today_per_min)):
        start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=ist)
        for i in range(60):
            rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "close": 100.0, "volume": per * (i + 1)})
    now = dt.datetime.combine(dt.date(2026, 10, 15), dt.time(10, 10), tzinfo=ist)
    return pd.DataFrame(rows), now


def test_fresh_trend_needs_a_new_cross_vwap_a_widening_gap_and_volume():
    import trend_strength as ts

    tape, now = _fresh_tape(today_per_min=200)  # twice the usual volume
    up = _fresh_frame([-0.2, -0.1, -0.05, 0.05, 0.12])  # crossed up two candles ago, gap widening
    got = ts.fresh(up, 100.05, now, tape, price=101.0)
    assert got["fresh"] == "BULLISH" and all(got["fresh_checks"].values()) and got["fresh_rvol"] == 2.0
    down = _fresh_frame([0.2, 0.1, 0.05, -0.05, -0.12], close=99.0, open_=99.5)
    assert ts.fresh(down, 99.95, now, tape, price=99.0)["fresh"] == "BEARISH"


def test_fresh_trend_says_which_check_failed():
    import trend_strength as ts

    tape, now = _fresh_tape(today_per_min=200)
    old = ts.fresh(_fresh_frame([0.1, 0.1, 0.1, 0.11, 0.12]), 100.05, now, tape, price=101.0)  # no cross in the window
    assert old["fresh"] is None and old["fresh_checks"]["cross"] is False
    below = ts.fresh(_fresh_frame([-0.2, -0.1, -0.05, 0.05, 0.12]), 100.5, now, tape, price=100.3)  # price under VWAP
    assert below["fresh"] is None and below["fresh_checks"]["vwap"] is False
    narrowing = ts.fresh(_fresh_frame([-0.2, -0.1, -0.05, 0.12, 0.05]), 100.01, now, tape, price=101.0)
    assert narrowing["fresh"] is None and narrowing["fresh_checks"]["gap"] is False
    quiet_tape, _ = _fresh_tape(today_per_min=100)  # the usual volume, not 1.5x
    quiet = ts.fresh(_fresh_frame([-0.2, -0.1, -0.05, 0.05, 0.12]), 100.04, now, quiet_tape, price=101.0)
    assert quiet["fresh"] is None and quiet["fresh_checks"]["volume"] is False and quiet["fresh_rvol"] == 1.0
    assert ts.fresh(_fresh_frame([0.05, 0.12]), 100.0, now, tape)["fresh"] is None  # too few candles: never raises


def test_fresh_trend_on_heikin_ashi_also_needs_the_candle_colour():
    import trend_strength as ts

    tape, now = _fresh_tape(today_per_min=200)
    gaps = [-0.2, -0.1, -0.05, 0.05, 0.12]
    red = ts.fresh(_fresh_frame(gaps, close=100.6, open_=100.9), 100.04, now, tape, price=101.0, colour=True)
    assert red["fresh"] is None and red["fresh_checks"]["colour"] is False
    assert ts.fresh(_fresh_frame(gaps, close=100.9, open_=100.6), 100.04, now, tape, price=101.0, colour=True)["fresh"] == "BULLISH"
    assert "colour" not in ts.fresh(_fresh_frame(gaps), 100.04, now, tape, price=101.0)["fresh_checks"]
