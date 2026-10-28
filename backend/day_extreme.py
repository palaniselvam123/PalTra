"""Day-extreme guard (day_extreme.py): don't buy at the day's high or short at the day's low.

Off by default (`day_extreme_guard`), per bot and per stock, PAPER and LIVE. In a strong trend
the signal candle often closes right at the day's high (a buy) or low (a short), so the order
fills at the most stretched price of the day and the first pullback turns it into a loss.

With it on, an entry `apply_signal` would order at once (cross, gap-mode entry, join, late /
early-reversal entry, re-entry, SMA 9 turn; not candle patterns or candle colour) whose real
order side is a buy within `day_extreme_pct` (0.15) % of today's high, or a sell within that of
today's low, waits as a `PullbackOrder` instead: at the nearer of the fast SMA and
`day_extreme_pull_pct` (0.15) % back from the signal candle's close (a buy below it, a sell
above it). `StrategyEngine._watch_pullbacks` sends the normal entry through `_open` on the first
price at or beyond that level (every check, stop, cap, cut-off and one LIVE bot per stock apply),
and drops it after `day_extreme_candles` (3) closed candles, when the SMAs cross against the
signal, at the cut-off or trade cap. If the price never comes back, there is no trade.

Today's high / low are read on the bot's closed candles of the signal candle's IST session, the
signal candle included. The tall-candle pullback and the candle-extreme limit take over from it
(they already wait for a better price); the entry-candle check is replaced by it on such a signal.
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))

DEFAULT_NEAR_PCT = 0.15
DEFAULT_PULL_PCT = 0.15
DEFAULT_CANDLES = 3
MAX_PCT = 5.0
MAX_CANDLES = 10
NOTE = "day high/low"


def on(cfg) -> bool:
    return bool(getattr(cfg, "day_extreme_guard", False))


def _pct(cfg, name: str, default: float) -> float:
    raw = getattr(cfg, name, None)
    try:
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(0.0, min(MAX_PCT, value))


def near_pct(cfg) -> float:
    return _pct(cfg, "day_extreme_pct", DEFAULT_NEAR_PCT)


def pull_pct(cfg) -> float:
    return _pct(cfg, "day_extreme_pull_pct", DEFAULT_PULL_PCT)


def candles(cfg) -> int:
    try:
        value = int(getattr(cfg, "day_extreme_candles", None) or DEFAULT_CANDLES)
    except (TypeError, ValueError):
        return DEFAULT_CANDLES
    return max(1, min(MAX_CANDLES, value))


def day_range(closed: pd.DataFrame) -> tuple[float, float] | None:
    """(high, low) of the last closed candle's IST session so far, that candle included."""
    if closed is None or getattr(closed, "empty", True) or not {"ts", "high", "low"} <= set(closed.columns):
        return None
    day = dt.datetime.fromtimestamp(int(closed["ts"].iloc[-1]), IST).date()
    start = int(dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST).timestamp())
    today = closed[pd.to_numeric(closed["ts"], errors="coerce") >= start]
    if today.empty:
        return None
    high = pd.to_numeric(today["high"], errors="coerce").max()
    low = pd.to_numeric(today["low"], errors="coerce").min()
    if pd.isna(high) or pd.isna(low):
        return None
    return float(high), float(low)


def plan(closed: pd.DataFrame, real: str, cfg) -> tuple[float, str] | None:
    """(limit, note) when the real order would go too near today's extreme, else None.

    `real` is the side the order actually goes ("LONG" buys, "SHORT" sells; a flipped stock's)."""
    rng = day_range(closed)
    if rng is None:
        return None
    curr = closed.iloc[-1]
    close = pd.to_numeric(pd.Series([curr.get("close")]), errors="coerce").iloc[0]
    if pd.isna(close) or close <= 0:
        return None
    close = float(close)
    fast = pd.to_numeric(pd.Series([curr.get("sma_9")]), errors="coerce").iloc[0]
    high, low = rng
    near, pull = near_pct(cfg) / 100.0, pull_pct(cfg) / 100.0
    if real == "LONG":
        if close < high * (1 - near):
            return None
        level = close * (1 - pull)
        if not pd.isna(fast) and float(fast) < close:
            level = max(level, float(fast))
        return min(level, close), f"buy at {close:.2f} is within {near * 100:g}% of today's high {high:.2f}"
    if close > low * (1 + near):
        return None
    level = close * (1 + pull)
    if not pd.isna(fast) and float(fast) > close:
        level = min(level, float(fast))
    return max(level, close), f"sell at {close:.2f} is within {near * 100:g}% of today's low {low:.2f}"
