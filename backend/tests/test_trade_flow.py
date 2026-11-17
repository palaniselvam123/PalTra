"""Executed buy / sell volume, estimated (trade_flow.py), and its check on the colour overrule (pressure_exit.py)."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

import pressure_exit
import tick_store
import trade_flow
from strategy_engine import IST
from tests.test_gap_mode import db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean():
    trade_flow.reset()
    pressure_exit.reset()
    tick_store.take()
    yield
    trade_flow.reset()
    pressure_exit.reset()
    tick_store.take()


T0 = int(dt.datetime(2026, 11, 16, 12, 30, tzinfo=IST).timestamp())


def _tape(rows: list[tuple[float, float, float, float, float]]) -> pd.DataFrame:
    """(open, high, low, close, minute volume) per minute from T0."""
    return pd.DataFrame(
        [{"ts": T0 + 60 * i, "open": o, "high": h, "low": low, "close": c, "minute_volume": v}
         for i, (o, h, low, c, v) in enumerate(rows)]
    )


def test_tick_rule_and_candle_shape():
    # Up, down, down, unchanged (keeps the down), up: 2 buys of 5 moves.
    assert trade_flow._tick_share([[0, 101], [1, 100], [2, 99], [3, 99], [4, 100]], 100.0) == pytest.approx(0.4)
    assert trade_flow._tick_share([[0, 100], [1, 100]], 100.0) is None   # the price never moved
    assert trade_flow.candle_share(99, 101, 99, 101) == 1.0               # low to high
    assert trade_flow.candle_share(100, 101, 99, 101) == 0.75             # mid-range to the high
    assert trade_flow.candle_share(100, 101, 99, 100) == 0.5              # doji
    assert trade_flow.candle_share(100, 100, 100, 100) == 0.5             # no range


def test_minutes_use_the_recorded_seconds_else_the_candle(db):  # noqa: F811
    tape = _tape([(100, 100.5, 99.5, 100.0, 0), (100, 101, 99, 100.5, 1000), (100.5, 101, 100, 101, 2000)])
    # A green minute in which the price mostly ticked down: sellers did most of it.
    for i, price in enumerate([100.4, 100.2, 100.0, 99.8, 100.5]):
        tick_store.add("ACME", T0 + 60 + i * 5, price)
    points = trade_flow.minutes("ACME", tape, T0, T0 + 180)
    assert [p["time"] for p in points] == [T0 + 60, T0 + 120]           # no volume in the first minute
    assert points[0] == {"time": T0 + 60, "buy": 400, "sell": 600, "src": "T"}
    assert points[1]["src"] == "C" and points[1]["buy"] == 1500          # no seconds: the candle's shape (0.75)
    assert trade_flow.share(points, "LONG") == pytest.approx(63.3)       # 1900 of 3000
    assert trade_flow.share(points, "SHORT") == pytest.approx(36.7)
    assert trade_flow.source_word(points) == "ticks + candles"
    assert trade_flow.share([], "LONG") is None


def test_flip_needs_the_executed_volume_when_set():
    now = dt.datetime(2026, 11, 16, 10, 0, tzinfo=IST)
    pressure_exit.note_scan({"as_of": now.isoformat(), "rows": [
        {"symbol": "ACME", "buy_share": 0.29, "read_at": (now - dt.timedelta(minutes=3)).isoformat(timespec="seconds")}]})
    ts, under = now.timestamp(), (429.0, 432.0)
    assert pressure_exit.flip_flow_pct(type("C", (), {})()) == 0.0
    assert pressure_exit.flip_flow_pct(type("C", (), {"pressure_flip_flow": 10})()) == 30.0
    # Off: the waiting orders and the VWAP are enough.
    assert pressure_exit.flip_side("ACME", "LONG", ts, 65, *under)[0] == "SHORT"
    # On: sellers did only 45 % of the traded shares (buyers 55 %): no flip.
    assert pressure_exit.flip_side("ACME", "LONG", ts, 65, *under, (55.0, "ticks"), 55) is None
    side, note = pressure_exit.flip_side("ACME", "LONG", ts, 65, *under, (38.0, "ticks"), 55)
    assert side == "SHORT" and "62% of the executed volume (ticks)" in note
    assert pressure_exit.flip_side("ACME", "LONG", ts, 65, *under, None, 55) is None       # unknown never flips
    assert pressure_exit.flip_side("ACME", "LONG", ts, 65, *under, (None, "candles"), 55) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ticks,want", [
    (False, ("LONG", "CANDLE_COLOUR")),   # the green candle's own shape says buyers did the trading: no flip
    (True, ("SHORT", "PRESSURE_FLIP")),   # its seconds ticked down mostly: sellers did, so the flip goes
])
async def test_colour_flip_reads_the_executed_volume(db, ticks, want):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    pressure_exit.note_scan({"as_of": "", "rows": [{"symbol": "COL", "buy_share": 0.29, "read_at": dt.datetime.combine(
        DAY, dt.time(9, 40), tzinfo=IST).isoformat(timespec="seconds")}]})
    if ticks:
        start = int(dt.datetime.combine(DAY, dt.time(9, 45), tzinfo=IST).timestamp())
        for i, price in enumerate([995.2, 995.1, 995.0, 994.9, 995.3]):
            tick_store.add("COL", start + i * 10, price)
    greens = [(995.0, 995.3), (995.3, 995.6), (995.6, 995.9)]
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + greens + _quiet(342, 995.9)))
    trades = await replay_symbol(frame, "COL", [DAY], _settings(
        db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, pressure_flip=True, pressure_flip_pct=65,
        pressure_flip_flow=55, trade_sources=json.dumps({"COL": "Auto-arm: Most active"})))
    assert (trades[0]["direction"], trades[0]["entry_reason"]) == want
