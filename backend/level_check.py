"""Price level check (level_check.py): enter only once the price is beyond a Bollinger band or a pivot level.

Off by default (`use_level`), per bot and per stock, PAPER and LIVE, entries only. Wired through
`strategy_engine._entry_block` like the other checks (check name `level`, chart ✕ "Level"): the closed signal candle
must close beyond the chosen line on the trade's side (`level_line`):

* `BB_MIDDLE` – a buy above the Bollinger middle band (`bb_period` / `bb_std`), a sell below it.
* `BB_UPPER` – a buy above the upper band, a sell below the lower band (a breakout entry).
* `PIVOT_P` – a buy above the day's pivot P, a sell below it (pivots.py, from the previous session).
* `PIVOT_R1` – a buy above R1, a sell below S1.

A line that cannot be worked out yet (too few candles, no previous session) refuses the entry, like the other checks.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import pivots
from indicators import BB_PERIOD, BB_STD, bollinger

LINES = ("BB_MIDDLE", "BB_UPPER", "PIVOT_P", "PIVOT_R1")
DEFAULT_LINE = "BB_MIDDLE"
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def line_of(cfg) -> str:
    value = str(getattr(cfg, "level_line", None) or DEFAULT_LINE).upper()
    return value if value in LINES else DEFAULT_LINE


def level(closed: pd.DataFrame, direction: str, cfg) -> tuple[float, str] | None:
    """(level, name) the closed signal candle must close beyond for `direction`, or None when not ready."""
    if closed is None or getattr(closed, "empty", True):
        return None
    line = line_of(cfg)
    if line.startswith("BB_"):
        period = int(getattr(cfg, "bb_period", None) or BB_PERIOD)
        k = float(getattr(cfg, "bb_std", None) or BB_STD)
        mid, upper, lower = bollinger(closed["close"], period, k)
        if line == "BB_MIDDLE":
            value, name = mid.iloc[-1], "the Bollinger middle band"
        elif direction == "LONG":
            value, name = upper.iloc[-1], "the upper Bollinger band"
        else:
            value, name = lower.iloc[-1], "the lower Bollinger band"
        return None if pd.isna(value) else (float(value), name)
    day = dt.datetime.fromtimestamp(int(closed["ts"].iloc[-1]), IST).date()
    lv = pivots.levels(closed, day)
    if lv is None:
        return None
    if line == "PIVOT_P":
        return float(lv["p"]), "the pivot P"
    return (float(lv["r1"]), "R1") if direction == "LONG" else (float(lv["s1"]), "S1")


def entry_reason(frame: pd.DataFrame, direction: str, cfg) -> str | None:
    """Why the price level refuses this entry, or None when the candle closed beyond it."""
    closed = frame.iloc[:-1] if frame is not None else None
    found = level(closed, direction, cfg)
    if found is None:
        return "Price level is not ready (needs more candles or the previous session)"
    value, name = found
    close = float(closed["close"].iloc[-1])
    if direction == "LONG" and close <= value:
        return f"Price level: {close:.2f} is not above {name} {value:.2f}"
    if direction == "SHORT" and close >= value:
        return f"Price level: {close:.2f} is not below {name} {value:.2f}"
    return None


def passed_text(frame: pd.DataFrame, direction: str, cfg) -> str:
    closed = frame.iloc[:-1]
    found = level(closed, direction, cfg)
    if found is None:
        return "Price level"
    value, name = found
    word = "above" if direction == "LONG" else "below"
    return f"Price level: {float(closed['close'].iloc[-1]):.2f} {word} {name} {value:.2f}"
