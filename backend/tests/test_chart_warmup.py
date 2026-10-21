"""The chart and the replay feed around the slow SMA's warm-up. PAPER only."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd

from replay import FRAME_BARS, IST, ReplayFeed, frame_bars
from strategy_engine import _from_formed_sma


def test_longer_candles_get_more_one_minute_bars():
    assert frame_bars(SimpleNamespace(candle_minutes=1, sma_slow=21)) == FRAME_BARS
    assert frame_bars(SimpleNamespace(candle_minutes=15, sma_slow=21)) == 15 * (21 + 40)
    assert frame_bars(SimpleNamespace(candle_minutes=5, sma_slow=100)) == 5 * (100 + 40)
    assert frame_bars(SimpleNamespace(candle_minutes=5, sma_slow=21)) == FRAME_BARS  # 305 < 500
    # Candle patterns always read 1-minute candles.
    assert frame_bars(SimpleNamespace(candle_minutes=15, sma_slow=21, entry_mode="PATTERN")) == FRAME_BARS


def test_the_feed_hands_each_stock_its_own_number_of_bars():
    start = dt.datetime(2026, 10, 6, 9, 15, tzinfo=IST)
    rows = [
        {"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": 100.0, "high": 100.5,
         "low": 99.5, "close": 100.0, "volume": 1000 * (i + 1)}
        for i in range(1200)
    ]
    frame = pd.DataFrame(rows)
    clock = start + dt.timedelta(minutes=1100)
    feed = ReplayFeed({"AAA": frame, "BBB": frame}, clock, {"BBB": 900})
    assert len(feed.quote("AAA")[1]) == FRAME_BARS + 1  # the forming bar on top
    assert len(feed.quote("BBB")[1]) == 900 + 1


def test_the_chart_starts_where_the_slow_sma_is_formed():
    rows = [{"time": i, "sma21": None} for i in range(3)] + [{"time": i, "sma21": 1.0} for i in range(3, 6)]
    assert [r["time"] for r in _from_formed_sma(rows)] == [3, 4, 5]
    blank = [{"time": i, "sma21": None} for i in range(4)]
    assert _from_formed_sma(blank) == blank  # too little history: keep what there is
