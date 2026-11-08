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
