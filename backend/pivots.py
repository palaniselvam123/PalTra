"""Classic daily pivot points (`pivot_target`, off by default; per bot and per stock, PAPER and LIVE).

From the previous session's high, low and close (the last trading day before the day being traded, read from
the stock's 1-minute tape, which already carries the earlier days the SMAs warm up on):

    P  = (high + low + close) / 3
    R1 = 2P - low        S1 = 2P - high
    R2 = P + (high - low)   S2 = P - (high - low)

The chart draws them (`chart_payload` -> `pivots`, display only). With `pivot_target` the trade books its profit
at a level: `R1` takes a long out at R1 and a short at S1, or at R2 / S2 when the entry is already beyond the first
level; `R2` uses R2 / S2 only. No level beyond the entry = no pivot target for that trade. Checked on every price
by `StrategyEngine._watch_pivot_target` and closed through `_exit_now` (`PIVOT_TARGET`; a LIVE stop is cancelled
first, a LIVE position restored without its stop id is left to that stop). The levels come from the candles, so a
restart finds the same ones. Other targets and stops are unchanged: whichever is reached first closes the trade.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
TARGETS = ("OFF", "R1", "R2")
PIVOT_EXIT = "PIVOT_TARGET"


def target_mode(cfg) -> str:
    mode = (getattr(cfg, "pivot_target", None) or "OFF").upper()
    return mode if mode in TARGETS else "OFF"


def _days(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date


def levels(frame: pd.DataFrame | None, day: dt.date) -> dict | None:
    """P, R1, R2, S1, S2 for trading `day`, from the last session before it in `frame` (needs ts/high/low/close)."""
    if frame is None or getattr(frame, "empty", True) or not {"ts", "high", "low", "close"} <= set(frame.columns):
        return None
    days = _days(frame)
    earlier = sorted({d for d in days if d < day})
    if not earlier:
        return None
    prev = earlier[-1]
    rows = frame[days == prev]
    high, low, close = float(rows["high"].max()), float(rows["low"].min()), float(rows["close"].iloc[-1])
    if not (high > 0 and low > 0 and close > 0) or high < low:
        return None
    p = (high + low + close) / 3
    span = high - low
    return {
        "day": prev.isoformat(),
        "p": round(p, 2),
        "r1": round(2 * p - low, 2),
        "s1": round(2 * p - high, 2),
        "r2": round(p + span, 2),
        "s2": round(p - span, 2),
    }


def target_for(direction: str, entry: float, lv: dict | None, mode: str) -> tuple[str, float] | None:
    """(level name, price) the trade books its profit at, or None (off, no levels, or none beyond the entry)."""
    if lv is None or mode == "OFF":
        return None
    names = (["r1", "r2"] if mode == "R1" else ["r2"]) if direction == "LONG" else (["s1", "s2"] if mode == "R1" else ["s2"])
    for name in names:
        price = lv[name]
        if (direction == "LONG" and price > entry) or (direction == "SHORT" and price < entry):
            return name.upper(), price
    return None


def reached(direction: str, price: float, level: float) -> bool:
    return price >= level if direction == "LONG" else price <= level
