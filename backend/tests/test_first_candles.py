"""First candles (first_candles.py): the day's first N candles and the Second candle call, read-only."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import first_candles as fc
from groww_client import IST

DAY = dt.date(2026, 11, 5)


def _tape(moves: list[float], minutes: int = 5) -> pd.DataFrame:
    """1-minute candles from 09:15; each 5-minute block opens at 100 and closes at 100 + its move."""
    rows = []
    t0 = dt.datetime.combine(DAY, dt.time(9, 15), IST)
    for i, mv in enumerate(moves):
        for m in range(minutes):
            ts = int((t0 + dt.timedelta(minutes=i * minutes + m)).timestamp())
            o = 100 + mv * m / minutes
            c = 100 + mv * (m + 1) / minutes
            rows.append({"ts": ts, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000 * (i + 1)})
    return pd.DataFrame(rows)


NOW = dt.datetime.combine(DAY, dt.time(10, 30), IST)


def test_colours_and_the_three_side_rules():
    tape = _tape([2.0, 1.0, -0.5])  # green, green, small red
    nth = fc.read("X", tape, NOW, fc.Rule(candle_no=3).clean())
    assert nth["colours"] == ["G", "G", "R"] and nth["call"] == "SHORT" and nth["matched"] == 1
    maj = fc.read("X", tape, NOW, fc.Rule(candle_no=3, side_rule="MAJORITY").clean())
    assert maj["call"] == "LONG" and maj["matched"] == 2 and maj["greens"] == 2 and maj["reds"] == 1
    agree = fc.read("X", tape, NOW, fc.Rule(candle_no=3, side_rule="AGREE").clean())
    assert agree["call"] is None and "against the majority" in agree["why"]
    assert fc.read("X", _tape([2.0, -1.0, 1.0]), NOW, fc.Rule(candle_no=3, side_rule="AGREE").clean())["call"] == "LONG"


def test_size_majority_body_floor_and_waiting():
    tape = _tape([3.0, -1.0, -1.0])
    assert fc.read("X", tape, NOW, fc.Rule(candle_no=3, side_rule="MAJORITY").clean())["call"] == "SHORT"
    assert fc.read("X", tape, NOW, fc.Rule(candle_no=3, side_rule="MAJORITY", majority_by="SIZE").clean())["call"] == "LONG"
    small = fc.read("X", tape, NOW, fc.Rule(candle_no=3, min_body_pct=2).clean())
    assert small["call"] is None and "under 2%" in small["why"]
    early = dt.datetime.combine(DAY, dt.time(9, 27), IST)  # only candles 1 and 2 have closed
    wait = fc.read("X", _tape([1.0, 1.0, 1.0]), early, fc.Rule(candle_no=3).clean())
    assert wait["call"] is None and wait["closed"] == 2 and "09:30" in wait["why"]


@pytest.mark.asyncio
async def test_scanner_reads_every_stock():
    tapes = {"A": _tape([1.0, 1.0]), "B": _tape([-1.0, -1.0])}

    async def fetch(symbol, start, end):
        return tapes[symbol]

    sc = fc.FirstCandleScanner()
    sc.start(["A", "B"], fetch, fc.Rule(candle_no=2, side_rule="MAJORITY"), now=NOW)
    await sc._task
    calls = {r["symbol"]: r["call"] for r in sc.snapshot()["rows"]}
    assert calls == {"A": "LONG", "B": "SHORT"}


def test_up_down_by_closes_or_both():
    # Blocks open at 100 each: green, green, red by colour; closes 102, 101, 99.5 (candle 1 has no earlier close).
    tape = _tape([2.0, 1.0, -0.5])
    closes = fc.read("X", tape, NOW, fc.Rule(candle_no=3, side_rule="MAJORITY", dir_rule="CLOSES").clean())
    assert closes["dirs"] == ["0", "D", "D"] and closes["call"] == "SHORT" and closes["matched"] == 2
    both = fc.read("X", tape, NOW, fc.Rule(candle_no=2, dir_rule="BOTH").clean())
    assert both["dirs"] == ["0", "0"] and both["call"] is None and "neither" in both["why"]
    assert fc.read("X", tape, NOW, fc.Rule(candle_no=3, dir_rule="BOTH").clean())["call"] == "SHORT"
    assert fc.Rule(dir_rule="X").clean().dir_rule == "COLOUR"


def test_heikin_ashi_and_candle_shapes():
    # Every 5-minute block opens back at 100 (real: green, green, small red). On Heikin Ashi candle 1 is a doji
    # (HA open (100+102)/2 = HA close (100+102+100+102)/4 = 101), and the next two open at 101 / 100.75 and close lower.
    tape = _tape([2.0, 1.0, -0.5])
    real = fc.read("X", tape, NOW, fc.Rule(candle_no=3).clean())
    assert real["colours"] == ["G", "G", "R"] and len(real["ohlc"]) == 3 and real["ha"] is False
    o, h, l, c = real["ohlc"][0]
    assert (o, c) == (100.0, 102.0) and h >= c and l <= o
    ha = fc.read("X", tape, NOW, fc.Rule(candle_no=3, ha=True).clean())
    assert ha["ha"] is True and ha["colours"] == ["D", "R", "R"] and ha["call"] == "SHORT"
    for o, h, l, c in ha["ohlc"]:
        assert h >= max(o, c) and l <= min(o, c)
    assert ha["ohlc"] != real["ohlc"]
