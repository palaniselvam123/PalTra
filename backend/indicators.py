"""SMA, Wilder ATR, and Wilder ADX on 1-minute OHLCV frames.

Crossover decisions must use the last two *closed* candles only:

    prev = df.iloc[-3]   # raw series still includes the forming bar at [-1]
    curr = df.iloc[-2]

`iloc[-1]` is the bar that is still printing. Reading it repaints.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

NSE_TICK = 0.05


def round_to_nse_tick(price: float, tick: float = NSE_TICK) -> float:
    """Nearest NSE cash tick (default ₹0.05)."""
    if price is None or not math.isfinite(price):
        return float("nan")
    steps = round(float(price) / tick)
    # 2 decimal places covers 0.05; extra rounding kills binary dust.
    return round(steps * tick, 2)


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return ranges.max(axis=1)


def atr_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ATR via `ewm(alpha=1/period, adjust=False)` — TradingView's seed."""
    tr = true_range(df)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def adx_wilder(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder ADX(period). NaN until the smoother has `period` samples."""
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df)
    alpha = 1 / period
    atr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_s = pd.Series(plus_dm, index=df.index).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    minus_s = pd.Series(minus_dm, index=df.index).ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    plus_di = 100 * plus_s / atr.replace(0, np.nan)
    minus_di = 100 * minus_s / atr.replace(0, np.nan)
    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    return dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()


def enrich(df: pd.DataFrame, sma_fast: int = 9, sma_slow: int = 21, atr_period: int = 14) -> pd.DataFrame:
    """Return a copy with SMA, ATR, and ADX columns attached.

    `sma_9` / `sma_21` are the configured fast/slow series (defaults 9 and 21).
    `atr_14` / `adx_14` follow `atr_period` (default 14).
    """
    out = df.copy()
    out["sma_fast"] = out["close"].rolling(int(sma_fast)).mean()
    out["sma_slow"] = out["close"].rolling(int(sma_slow)).mean()
    # Aliases named in the strategy spec. They track the configured periods.
    out["sma_9"] = out["sma_fast"]
    out["sma_21"] = out["sma_slow"]
    out["tr"] = true_range(out)
    out["atr_14"] = atr_wilder(out, int(atr_period))
    out["adx_14"] = adx_wilder(out, int(atr_period))
    return out


def closed_candle_bias(df: pd.DataFrame) -> str | None:
    """Direction of the last closed bar: fast SMA above or below the slow SMA.

    A stock that is already trending does not need a brand-new cross to be
    eligible. The crossover helper still decides reversals.
    """
    if len(df) < 3:
        return None
    curr = df.iloc[-2]
    fast, slow = curr.get("sma_9"), curr.get("sma_21")
    if pd.isna(fast) or pd.isna(slow):
        return None
    if fast > slow:
        return "BULLISH"
    if fast < slow:
        return "BEARISH"
    return None


def closed_candle_cross(df: pd.DataFrame) -> str | None:
    """Bullish / bearish SMA cross on closed candles only.

    Returns "BULLISH", "BEARISH", or None. Never inspects the forming bar.
    """
    if len(df) < 3:
        return None
    prev = df.iloc[-3]
    curr = df.iloc[-2]
    needed = ("sma_9", "sma_21")
    if any(pd.isna(prev[c]) or pd.isna(curr[c]) for c in needed):
        return None
    if prev.sma_9 <= prev.sma_21 and curr.sma_9 > curr.sma_21:
        return "BULLISH"
    if prev.sma_9 >= prev.sma_21 and curr.sma_9 < curr.sma_21:
        return "BEARISH"
    return None
