"""Strong trend read (trend_strength.py): is this stock in a clean, one-way trend?

Read-only, no order path. Used by the Chop scan (`chop_scan.analyze`) to tag
each stock `STRONG_BULLISH` / `STRONG_BEARISH` when it looks like the owner's
example chart: price, the fast SMA, the slow SMA and VWAP stacked one way,
both SMAs sloping with the trend and fanned apart, nearly every recent close on
the trend side of the fast SMA, lower highs and lower lows (or higher for a
bullish trend), and no SMA cross for a while.

The side is taken from the fast SMA versus the slow SMA on the last closed
candle; each check below is then judged for that side.

    stack     close beyond the fast SMA, fast beyond the slow SMA
    vwap      close and the slow SMA beyond today's VWAP
    slope     fast and slow SMA both moved with the trend over SLOPE_BARS candles
    fan       |SMA gap| at least FAN_MIN_PCT % and not narrowed by more than a third
              over SLOPE_BARS candles
    hold      at least HOLD_MIN_SHARE of the last LOOKBACK closes on the trend side
              of the fast SMA (a bounce that fails at the fast SMA still passes)
    structure the last half of LOOKBACK made a lower high and a lower low than the
              half before it (higher / higher for bullish)
    run       no SMA cross in the last LOOKBACK candles

`stack`, `vwap` and `slope` are required; the label is given when those pass
and at least STRONG_MIN_PASSED of the seven pass. `score` is the share of the
seven checks that passed (0-100). Nothing in the strategy reads it.
"""
from __future__ import annotations

import pandas as pd

from indicators import sma_gap_pct

STRONG_BULLISH = "STRONG_BULLISH"
STRONG_BEARISH = "STRONG_BEARISH"

LOOKBACK = 12  # closed candles judged for hold / structure / run
SLOPE_BARS = 3
FAN_MIN_PCT = 0.10
HOLD_MIN_SHARE = 0.75
STRONG_MIN_PASSED = 6
REQUIRED = ("stack", "vwap", "slope")
CHECKS = ("stack", "vwap", "slope", "fan", "hold", "structure", "run")


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def read(closed: pd.DataFrame, vwap: float | None) -> dict:
    """Trend read of enriched closed candles (`sma_9`, `sma_21`, OHLC). Never raises on short data."""
    empty = {"trend": None, "trend_side": None, "trend_score": 0, "trend_checks": {}, "sma_gap_pct": None, "vwap": None}
    if closed is None or len(closed) < LOOKBACK + SLOPE_BARS:
        return empty
    last = closed.iloc[-1]
    close, fast, slow = _f(last.get("close")), _f(last.get("sma_9")), _f(last.get("sma_21"))
    gap = sma_gap_pct(fast, slow) if fast is not None and slow is not None else None
    if close is None or gap is None or gap == 0:
        return empty
    up = gap > 0
    sign = 1 if up else -1

    def beyond(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and (a - b) * sign > 0

    back = closed.iloc[-1 - SLOPE_BARS]
    fast_then, slow_then = _f(back.get("sma_9")), _f(back.get("sma_21"))
    gap_then = sma_gap_pct(fast_then, slow_then) if fast_then is not None and slow_then is not None else None
    recent = closed.iloc[-LOOKBACK:]
    side_closes = [
        beyond(_f(c), _f(f)) for c, f in zip(recent["close"], recent["sma_9"])
    ]
    gaps = [sma_gap_pct(f, s) for f, s in zip(closed["sma_9"].iloc[-LOOKBACK - 1 :], closed["sma_21"].iloc[-LOOKBACK - 1 :])]
    signs = [g > 0 for g in gaps if g is not None and g != 0]
    half = LOOKBACK // 2
    older, newer = recent.iloc[:half], recent.iloc[half:]
    if up:
        structure = newer["high"].max() > older["high"].max() and newer["low"].min() > older["low"].min()
    else:
        structure = newer["high"].max() < older["high"].max() and newer["low"].min() < older["low"].min()
    checks = {
        "stack": beyond(close, fast) and beyond(fast, slow),
        "vwap": vwap is not None and beyond(close, vwap) and beyond(slow, vwap),
        "slope": beyond(fast, fast_then) and beyond(slow, slow_then),
        "fan": abs(gap) >= FAN_MIN_PCT and gap_then is not None and abs(gap) >= abs(gap_then) * 2 / 3,
        "hold": sum(side_closes) >= HOLD_MIN_SHARE * len(side_closes),
        "structure": bool(structure),
        "run": len(signs) >= LOOKBACK and all(s == up for s in signs),
    }
    passed = sum(checks.values())
    strong = all(checks[k] for k in REQUIRED) and passed >= STRONG_MIN_PASSED
    side = "BULLISH" if up else "BEARISH"
    return {
        "trend": (STRONG_BULLISH if up else STRONG_BEARISH) if strong else None,
        "trend_side": side,
        "trend_score": round(passed / len(CHECKS) * 100),
        "trend_checks": checks,
        "sma_gap_pct": round(gap, 4),
        "vwap": round(vwap, 2) if vwap is not None else None,
    }
