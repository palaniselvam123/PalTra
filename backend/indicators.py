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


def rsi_wilder(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder RSI. NaN until `period` changes exist."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    alpha = 1 / int(period)
    avg_gain = gain.ewm(alpha=alpha, min_periods=int(period), adjust=False).mean()
    avg_loss = loss.ewm(alpha=alpha, min_periods=int(period), adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((avg_loss == 0) & (avg_gain > 0), 100.0)
    rsi = rsi.mask((avg_gain == 0) & (avg_loss == 0), 50.0)
    return rsi


def entry_filter_reason(
    df: pd.DataFrame,
    direction: str,
    *,
    use_vwap: bool = False,
    use_volume: bool = False,
    use_density: bool = False,
    use_rsi: bool = False,
    volume_lookback: int = 20,
    volume_min_ratio: float = 1.0,
    density_min_pct: float = 50.0,
    rsi_period: int = 14,
    rsi_long_min: float = 40.0,
    rsi_long_max: float = 70.0,
    rsi_short_min: float = 30.0,
    rsi_short_max: float = 60.0,
    price: float | None = None,
) -> str | None:
    """Why this entry must wait. None when every checked filter agrees.

    An unchecked filter is not read. The decision uses the last closed candle
    (`iloc[-2]`). `price` overrides that close for the VWAP comparison only,
    so a manual order can be judged at the price about to be sent.
    """
    if not any((use_vwap, use_volume, use_density, use_rsi)):
        return None
    if df is None or len(df) < 3:
        return "filters need more candles"
    closed = df.iloc[:-1]
    bar = closed.iloc[-1]
    reasons: list[str] = []
    side = (direction or "").upper()

    if use_vwap:
        vwap = _session_vwap(closed)
        px = float(bar["close"] if price is None else price)
        if vwap is None:
            reasons.append("VWAP is not ready")
        elif side == "LONG" and px < vwap:
            reasons.append(f"VWAP: {px:.2f} is below {vwap:.2f}")
        elif side == "SHORT" and px > vwap:
            reasons.append(f"VWAP: {px:.2f} is above {vwap:.2f}")

    if use_volume:
        reasons.extend(_volume_reason(closed, int(volume_lookback), float(volume_min_ratio)))

    if use_density:
        reasons.extend(_density_reason(bar, float(density_min_pct)))

    if use_rsi:
        reasons.extend(
            _rsi_reason(
                closed["close"],
                side,
                int(rsi_period),
                float(rsi_long_min),
                float(rsi_long_max),
                float(rsi_short_min),
                float(rsi_short_max),
            )
        )

    if not reasons:
        return None
    return "; ".join(reasons)


def _session_vwap(closed: pd.DataFrame) -> float | None:
    if "volume" not in closed.columns:
        return None
    typical = (closed["high"] + closed["low"] + closed["close"]) / 3
    vol = pd.to_numeric(closed["volume"], errors="coerce").fillna(0).clip(lower=0)
    if "ts" in closed.columns:
        dates = pd.to_datetime(closed["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        mask = dates == dates.iloc[-1]
        typical = typical[mask]
        vol = vol[mask]
    total = float(vol.sum())
    if total <= 0:
        return None
    return float((typical * vol).sum() / total)


def _volume_reason(closed: pd.DataFrame, lookback: int, ratio: float) -> list[str]:
    if "volume" not in closed.columns:
        return ["volume is not on these candles"]
    lookback = max(1, lookback)
    if len(closed) < lookback + 1:
        return [f"volume needs {lookback} earlier candles"]
    hist = pd.to_numeric(closed["volume"].iloc[-(lookback + 1) : -1], errors="coerce").fillna(0)
    current = float(pd.to_numeric(closed["volume"].iloc[-1], errors="coerce") or 0)
    average = float(hist.mean())
    need = ratio * average
    if average <= 0 or current < need:
        return [f"volume {current:.0f} is below {ratio:g}× the {lookback}-candle average {average:.0f}"]
    return []


def _density_reason(bar: pd.Series, min_pct: float) -> list[str]:
    span = float(bar["high"]) - float(bar["low"])
    body = abs(float(bar["close"]) - float(bar["open"]))
    density = 0.0 if span <= 0 else body / span * 100
    if density + 1e-9 < min_pct:
        return [f"density {density:.0f}% is below {min_pct:.0f}%"]
    return []


def _rsi_reason(
    close: pd.Series,
    side: str,
    period: int,
    long_min: float,
    long_max: float,
    short_min: float,
    short_max: float,
) -> list[str]:
    value = rsi_wilder(close, period).iloc[-1]
    if pd.isna(value):
        return ["RSI is not ready"]
    rsi = float(value)
    if side == "LONG":
        lo, hi = long_min, long_max
    else:
        lo, hi = short_min, short_max
    if rsi < lo or rsi > hi:
        return [f"RSI {rsi:.1f} is outside {lo:.0f}–{hi:.0f}"]
    return []


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
