"""stock_regime: the Stocks bar's choppy / trending read of an armed stock (display only)."""
import datetime as dt
import math

import pandas as pd

import stock_regime
from groww_client import IST


def _tape(day: dt.date, closes: list[float], start=(9, 15)) -> pd.DataFrame:
    t0 = dt.datetime(day.year, day.month, day.day, *start, tzinfo=IST)
    rows = []
    for i, c in enumerate(closes):
        prev = closes[i - 1] if i else c
        rows.append({"ts": int((t0 + dt.timedelta(minutes=i)).timestamp()), "open": prev, "high": max(prev, c) + 0.05,
                     "low": min(prev, c) - 0.05, "close": c, "volume": 1000 * (i + 1)})
    return pd.DataFrame(rows)


DAY = dt.date(2026, 11, 4)
PREV = dt.date(2026, 11, 3)


def _frame(today: list[float]) -> pd.DataFrame:
    warm = _tape(PREV, [100 + 0.3 * math.sin(i / 3) for i in range(120)])
    return pd.concat([warm, _tape(DAY, today)], ignore_index=True)


def test_a_steady_climb_reads_as_an_up_phase():
    closes = [100 + 0.4 * math.sin(i / 2) for i in range(60)] + [100 + 0.15 * i for i in range(1, 91)]
    frame = _frame(closes)
    now = dt.datetime(2026, 11, 4, 11, 46, tzinfo=IST)
    r = stock_regime.read(frame, 9, 21, 1, now)
    assert r is not None and r["phase"] == "BULL"
    assert r["phase_since"] is not None and r["phase_move_pct"] > 0
    assert r["minutes"] == 1


def test_back_and_forth_reads_as_choppy_with_crosses():
    closes = [100 + 1.2 * math.sin(i / 4) for i in range(150)]
    now = dt.datetime(2026, 11, 4, 11, 46, tzinfo=IST)
    r = stock_regime.read(_frame(closes), 9, 21, 1, now)
    assert r is not None and r["crosses"] >= 4
    assert r["score"] == "CHOPPY"


def test_short_tape_gives_nothing_and_cache_reuses_within_the_candle():
    now = dt.datetime(2026, 11, 4, 9, 20, tzinfo=IST)
    assert stock_regime.read(_tape(DAY, [100, 101, 102]), 9, 21, 1, now) is None
    frame = _frame([100 + 0.1 * i for i in range(90)])
    later = dt.datetime(2026, 11, 4, 10, 46, 10, tzinfo=IST)
    a = stock_regime.cached(1, "X", frame, 9, 21, 5, later)
    b = stock_regime.cached(1, "X", frame, 9, 21, 5, later + dt.timedelta(seconds=20))
    assert a is b
