"""SMA, Wilder ATR, and Wilder ADX on 1-minute OHLCV frames.

Crossover decisions must use the last two *closed* candles only:

    prev = df.iloc[-3]   # raw series still includes the forming bar at [-1]
    curr = df.iloc[-2]

`iloc[-1]` is the bar that is still printing. Reading it repaints.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

logger = logging.getLogger("sma.indicators")

NSE_TICK = 0.05
# One warning per bad cumulative print. Enrich runs on every quote.
_VOLUME_WARNED: set[tuple] = set()


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


def _warn_volume_once(kind: str, ts: int, previous: float, current: float) -> None:
    key = (kind, int(ts), round(float(previous), 4), round(float(current), 4))
    if key in _VOLUME_WARNED:
        return
    if len(_VOLUME_WARNED) > 400:
        _VOLUME_WARNED.clear()
    _VOLUME_WARNED.add(key)
    logger.warning(
        "minute volume unavailable (%s) at ts=%s previous_cumulative=%s current_cumulative=%s",
        kind,
        int(ts),
        previous,
        current,
    )


def sma_gap_pct(fast: float, slow: float) -> float | None:
    """SMA 9 minus SMA 21, as a percent of SMA 21."""
    if pd.isna(fast) or pd.isna(slow):
        return None
    slow_f = float(slow)
    if slow_f == 0:
        return None
    return (float(fast) - slow_f) / slow_f * 100.0


def derive_minute_volume(df: pd.DataFrame) -> pd.Series:
    """Shares traded in each candle, from Groww's cumulative session volume.

    `volume` on a Groww 1-minute candle is the running total since the IST
    session open, not the shares printed in that minute. The first candle of
    a session has no predecessor, so its minute volume is unavailable. A
    falling counter is rejected, and so is the following candle: differencing
    against a reset baseline would invent one huge bar. An unchanged counter
    is a real zero. Missing values stay missing. They are never stored as 0.
    """
    if df is None or len(df) == 0:
        return pd.Series(dtype="float64")
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    if "volume" not in df.columns or "ts" not in df.columns:
        return out

    work = pd.DataFrame(
        {
            "_idx": df.index,
            "_ts": pd.to_numeric(df["ts"], errors="coerce"),
            "_vol": pd.to_numeric(df["volume"], errors="coerce"),
        }
    )
    work = work.sort_values(["_ts", "_idx"], kind="mergesort")
    dates = pd.to_datetime(work["_ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date

    prev_cum: float | None = None
    prev_ts: float | None = None
    prev_day = None
    reject_next = False
    # Collected, then written once: a per-row .at write was most of enrich()'s
    # time on every quote.
    found: dict = {}
    for idx, ts, vol, day in zip(work["_idx"], work["_ts"], work["_vol"], dates):
        if pd.isna(ts) or pd.isna(day):
            continue
        if day != prev_day:
            prev_day = day
            prev_cum = None
            prev_ts = None
            reject_next = False
        if pd.isna(vol):
            prev_cum = None
            prev_ts = None
            reject_next = False
            continue
        vol_f = float(vol)
        ts_f = float(ts)
        if prev_ts is not None and ts_f == prev_ts:
            _warn_volume_once("duplicate timestamp", int(ts_f), prev_cum or 0.0, vol_f)
            continue
        if prev_cum is None:
            prev_cum = vol_f
            prev_ts = ts_f
            continue
        if reject_next:
            _warn_volume_once("bar after a cumulative reset", int(ts_f), prev_cum, vol_f)
            prev_cum = vol_f
            prev_ts = ts_f
            reject_next = False
            continue
        if vol_f < prev_cum:
            _warn_volume_once("cumulative volume fell", int(ts_f), prev_cum, vol_f)
            prev_cum = vol_f
            prev_ts = ts_f
            reject_next = True
            continue
        found[idx] = vol_f - prev_cum
        prev_cum = vol_f
        prev_ts = ts_f
    if found:
        out.loc[list(found.keys())] = list(found.values())
    return out


def minute_volume_stats(
    closed: pd.DataFrame, lookback: int = 20
) -> tuple[float | None, float | None, float | None]:
    """Current minute volume, the mean of the previous valid prints, and the ratio.

    The average uses the last `lookback` available minute volumes before the
    decision candle. Missing prints are skipped. They are not treated as zero.
    """
    if closed is None or len(closed) == 0:
        return None, None, None
    if "minute_volume" in closed.columns:
        vol = pd.to_numeric(closed["minute_volume"], errors="coerce")
    else:
        vol = derive_minute_volume(closed)
    lookback = max(1, int(lookback))
    current = vol.iloc[-1]
    current_f = None if pd.isna(current) else float(current)
    prior = vol.iloc[:-1].dropna()
    if len(prior) < lookback:
        return current_f, None, None
    average = float(prior.iloc[-lookback:].mean())
    if current_f is None or average <= 0:
        return current_f, average, None
    return current_f, average, current_f / average


def enrich(df: pd.DataFrame, sma_fast: int = 9, sma_slow: int = 21, atr_period: int = 14) -> pd.DataFrame:
    """Return a copy with SMA, ATR, ADX, and minute volume attached.

    `sma_9` / `sma_21` are the configured fast/slow series (defaults 9 and 21).
    `atr_14` / `adx_14` follow `atr_period` (default 14).
    `volume` stays the raw cumulative counter. `minute_volume` is the shares
    traded in that candle.
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
    if "volume" in out.columns:
        out["cumulative_volume"] = pd.to_numeric(out["volume"], errors="coerce")
    else:
        out["cumulative_volume"] = np.nan
    out["minute_volume"] = derive_minute_volume(out)
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


BB_PERIOD = 20
BB_STD = 2.0


def bollinger(closes: pd.Series, period: int = BB_PERIOD, k: float = BB_STD) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger Bands on closes: the period SMA and k population standard
    deviations either side (the usual charting definition, ddof=0)."""
    closes = pd.to_numeric(closes, errors="coerce")
    mid = closes.rolling(int(period), min_periods=int(period)).mean()
    sd = closes.rolling(int(period), min_periods=int(period)).std(ddof=0)
    return mid, mid + float(k) * sd, mid - float(k) * sd


def _bollinger_reason(
    closed: pd.DataFrame, side: str, period: int, k: float, min_width_pct: float
) -> list[str]:
    """Why Bollinger refuses an entry on the last closed candle.

    Two checks, both about the cross arriving at a bad moment:
    * stretched: a buy that already closed above the upper band (or a sell
      below the lower) has run past ~2 sigma, so it is chasing a spike that
      usually snaps back toward the middle band;
    * squeeze: bands narrower than `min_width_pct` of price mean the stock is
      going sideways, where SMA crosses whipsaw. 0 turns this check off.
    """
    if closed is None or len(closed) < int(period):
        return [f"Bollinger needs {int(period)} candles"]
    mid, upper, lower = bollinger(closed["close"], period, k)
    m, u, lo = mid.iloc[-1], upper.iloc[-1], lower.iloc[-1]
    if pd.isna(m) or pd.isna(u) or pd.isna(lo) or m <= 0:
        return ["Bollinger is not ready"]
    close = float(closed["close"].iloc[-1])
    width = (float(u) - float(lo)) / float(m) * 100
    out: list[str] = []
    if min_width_pct > 0 and width < min_width_pct:
        out.append(f"Bollinger squeeze: bands {width:.2f}% wide < {min_width_pct:g}%")
    if side == "LONG" and close > float(u):
        out.append(f"Bollinger: {close:.2f} is above the upper band {float(u):.2f}")
    elif side == "SHORT" and close < float(lo):
        out.append(f"Bollinger: {close:.2f} is below the lower band {float(lo):.2f}")
    return out


BB_EXIT_MODES = ("OFF", "BAND", "MIDDLE", "BOTH")


def bollinger_exit(
    closes: pd.Series, direction: str, mode: str, period: int, k: float, armed: bool
) -> tuple[str | None, str, bool]:
    """Bollinger exit read on the last close of `closes` (closed candles only).

    Returns (reason, note, armed):
    * BAND (or BOTH): a buy that closes at or above the upper band (a sell at
      or below the lower) is stretched ~2 sigma in its favour. Take the profit
      before it snaps back: reason "BB_TARGET".
    * MIDDLE (or BOTH): once a close has been on the trade's side of the middle
      band (`armed`), a close back across it means the move has faded:
      reason "BB_MIDDLE". Not armed yet means a trade that entered on the
      wrong side of the middle is not cut on its first candle.
    """
    mode = (mode or "OFF").upper()
    if mode not in ("BAND", "MIDDLE", "BOTH") or closes is None or len(closes) < int(period):
        return None, "", armed
    mid, upper, lower = bollinger(closes, period, k)
    m, u, lo = mid.iloc[-1], upper.iloc[-1], lower.iloc[-1]
    if pd.isna(m) or pd.isna(u) or pd.isna(lo):
        return None, "", armed
    close = float(closes.iloc[-1])
    m, u, lo = float(m), float(u), float(lo)
    long = direction == "LONG"
    if mode in ("BAND", "BOTH"):
        if long and close >= u:
            return "BB_TARGET", f"closed {close:.2f} at the upper band {u:.2f}", armed
        if not long and close <= lo:
            return "BB_TARGET", f"closed {close:.2f} at the lower band {lo:.2f}", armed
    if mode in ("MIDDLE", "BOTH"):
        if armed and ((long and close < m) or (not long and close > m)):
            return "BB_MIDDLE", f"closed {close:.2f} back across the middle band {m:.2f}", armed
        armed = armed or (close > m if long else close < m)
    return None, "", armed


def _gap_reason(closed: pd.DataFrame, side: str, lo: float, hi: float) -> list[str]:
    """SMA fast vs slow gap % on the closed candle, signed: + when SMA 9 is above SMA 21.

    A buy needs it inside [lo, hi] of the buy range, a sell inside the sell
    range. The sign is kept, so a sell range is usually negative numbers.
    """
    bar = closed.iloc[-1]
    gap = sma_gap_signed(bar.get("sma_9"), bar.get("sma_21"))
    word = "buy" if side == "LONG" else "sell"
    if gap is None:
        return ["SMA gap is not ready"]
    if lo > hi:
        return [f"SMA gap {word} range is empty ({lo:g}% > {hi:g}%)"]
    if gap < lo or gap > hi:
        return [f"SMA gap {gap:+.3f}% is outside the {word} range {lo:g}% to {hi:g}%"]
    return []


def sma_gap_signed(fast, slow) -> float | None:
    """(SMA fast - SMA slow) / SMA slow x 100, keeping the sign."""
    try:
        f, s = float(fast), float(slow)
    except (TypeError, ValueError):
        return None
    if np.isnan(f) or np.isnan(s) or s == 0:
        return None
    return (f - s) / s * 100


def entry_filter_reason(
    df: pd.DataFrame,
    direction: str,
    *,
    use_vwap: bool = False,
    use_volume: bool = False,
    use_density: bool = False,
    use_rsi: bool = False,
    use_bollinger: bool = False,
    bb_period: int = BB_PERIOD,
    bb_std: float = BB_STD,
    bb_min_width_pct: float = 0.15,
    use_gap_long: bool = False,
    gap_long_min: float = 0.02,
    gap_long_max: float = 0.5,
    use_gap_short: bool = False,
    gap_short_min: float = -0.5,
    gap_short_max: float = -0.02,
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
    side = (direction or "").upper()
    use_gap = use_gap_long if side == "LONG" else use_gap_short if side == "SHORT" else False
    if not any((use_vwap, use_volume, use_density, use_rsi, use_bollinger, use_gap)):
        return None
    if df is None or len(df) < 3:
        return "filters need more candles"
    closed = df.iloc[:-1]
    bar = closed.iloc[-1]
    reasons: list[str] = []

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

    if use_bollinger:
        reasons.extend(_bollinger_reason(closed, side, int(bb_period), float(bb_std), float(bb_min_width_pct)))

    if use_gap:
        if side == "LONG":
            reasons.extend(_gap_reason(closed, side, float(gap_long_min), float(gap_long_max)))
        else:
            reasons.extend(_gap_reason(closed, side, float(gap_short_min), float(gap_short_max)))

    if not reasons:
        return None
    return "; ".join(reasons)


def _session_vwap(closed: pd.DataFrame) -> float | None:
    """IST-session VWAP from completed candles, weighted by minute volume.

    Typical price is (high + low + close) / 3. The weight is the shares traded
    in that candle, not Groww's running total. The session is the IST date of
    the last closed candle. A missing minute volume is left out. It is not
    treated as zero.
    """
    if closed is None or len(closed) == 0:
        return None
    if not {"high", "low", "close"}.issubset(closed.columns):
        return None
    typical = (closed["high"] + closed["low"] + closed["close"]) / 3
    if "minute_volume" in closed.columns:
        vol = pd.to_numeric(closed["minute_volume"], errors="coerce")
    else:
        vol = derive_minute_volume(closed)
    mask = vol.notna()
    if "ts" in closed.columns:
        dates = pd.to_datetime(closed["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
        mask = mask & (dates == dates.iloc[-1])
    typical = typical[mask]
    vol = vol[mask]
    total = float(vol.sum()) if len(vol) else 0.0
    if total <= 0:
        return None
    return float((typical * vol).sum() / total)


def session_vwap_series(df: pd.DataFrame) -> pd.Series:
    """VWAP after each candle, the same value `_session_vwap` gives when that
    candle is the last closed one: typical price weighted by minute volume,
    restarting each IST session. NaN until the session has volume.
    """
    if df is None or len(df) == 0 or not {"high", "low", "close"}.issubset(df.columns):
        return pd.Series(np.nan, index=getattr(df, "index", None), dtype=float)
    typical = (df["high"] + df["low"] + df["close"]) / 3
    if "minute_volume" in df.columns:
        vol = pd.to_numeric(df["minute_volume"], errors="coerce")
    else:
        vol = derive_minute_volume(df)
    weight = vol.where(vol.notna(), 0.0)
    if "ts" in df.columns:
        session = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    else:
        session = pd.Series(0, index=df.index)
    num = (typical.where(vol.notna(), 0.0) * weight).groupby(session).cumsum()
    den = weight.groupby(session).cumsum()
    return (num / den.where(den > 0)).astype(float)


def _volume_reason(closed: pd.DataFrame, lookback: int, ratio: float) -> list[str]:
    if "volume" not in closed.columns and "minute_volume" not in closed.columns:
        return ["volume is not on these candles"]
    current, average, _got = minute_volume_stats(closed, lookback)
    if current is None:
        return ["volume is not ready on the closed candle"]
    if average is None:
        return [f"volume needs {lookback} earlier candles"]
    need = ratio * average
    if current < need:
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


def _num(value) -> float | None:
    if value is None or pd.isna(value):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass
class TechnicalSnapshot:
    """One closed candle, with the indicators the order path actually uses."""

    timestamp: int | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    sma9: float | None
    sma21: float | None
    sma_gap: float | None
    sma_gap_pct: float | None
    vwap: float | None
    cumulative_volume: float | None
    minute_volume: float | None
    average_previous_20_volume: float | None
    volume_ratio: float | None
    rsi14: float | None
    atr14: float | None
    adx14: float | None
    candle_direction: str | None
    candle_body: float | None
    candle_range: float | None
    density_pct: float | None


def closed_technical_snapshot(df: pd.DataFrame, volume_lookback: int = 20) -> TechnicalSnapshot | None:
    """Readings for the last completed candle. The forming bar at iloc[-1] is dropped."""
    if df is None or len(df) < 2:
        return None
    closed = df.iloc[:-1]
    bar = closed.iloc[-1]
    sma9 = _num(bar.get("sma_9"))
    sma21 = _num(bar.get("sma_21"))
    gap = None if sma9 is None or sma21 is None else sma9 - sma21
    o = _num(bar.get("open"))
    h = _num(bar.get("high"))
    low = _num(bar.get("low"))
    c = _num(bar.get("close"))
    body = None if o is None or c is None else c - o
    span = None if h is None or low is None else h - low
    if body is None or span is None:
        direction = None
        density = None
    elif c > o:
        direction = "BULLISH"
    elif c < o:
        direction = "BEARISH"
    else:
        direction = "DOJI"
    density = 0.0 if span is not None and span <= 0 else (None if body is None or span is None else abs(body) / span * 100)
    current, average, ratio = minute_volume_stats(closed, volume_lookback)
    rsi = rsi_wilder(closed["close"], 14)
    rsi_now = _num(rsi.iloc[-1]) if len(rsi) else None
    ts = _num(bar.get("ts"))
    return TechnicalSnapshot(
        timestamp=None if ts is None else int(ts),
        open=o,
        high=h,
        low=low,
        close=c,
        sma9=sma9,
        sma21=sma21,
        sma_gap=gap,
        sma_gap_pct=sma_gap_pct(sma9, sma21) if sma9 is not None and sma21 is not None else None,
        vwap=_session_vwap(closed),
        cumulative_volume=_num(bar.get("cumulative_volume", bar.get("volume"))),
        minute_volume=current,
        average_previous_20_volume=average,
        volume_ratio=ratio,
        rsi14=rsi_now,
        atr14=_num(bar.get("atr_14")),
        adx14=_num(bar.get("adx_14")),
        candle_direction=direction,
        candle_body=body,
        candle_range=span,
        density_pct=density,
    )


def _fmt(value: float | None, digits: int = 2, signed: bool = False) -> str:
    if value is None:
        return "unavailable"
    text = f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"
    return text


def format_signal_report(
    *,
    symbol: str,
    evaluated_at: str,
    action: str,
    snap: TechnicalSnapshot | None,
    use_vwap: bool,
    use_volume: bool,
    volume_multiple: float,
    use_rsi: bool,
    rsi_min: float,
    rsi_max: float,
    decision: str,
    note: str = "",
) -> str:
    """One block for a crossover or a force evaluation. Not for every tick."""
    if snap is None:
        return f"AUTOMATIC SIGNAL {symbol} {evaluated_at} {action}\nindicators unavailable\n{decision}"
    if not use_vwap:
        vwap_state = "DISABLED"
    elif snap.vwap is None or snap.close is None:
        vwap_state = "FAIL"
    elif action == "SELL":
        vwap_state = "PASS" if snap.close <= snap.vwap else "FAIL"
    else:
        vwap_state = "PASS" if snap.close >= snap.vwap else "FAIL"
    if not use_volume:
        volume_state = "DISABLED"
    elif snap.volume_ratio is None:
        volume_state = "FAIL"
    elif snap.volume_ratio + 1e-12 >= volume_multiple:
        volume_state = "PASS"
    else:
        volume_state = "FAIL"
    if not use_rsi:
        rsi_state = "DISABLED"
    elif snap.rsi14 is None:
        rsi_state = "FAIL"
    elif rsi_min <= snap.rsi14 <= rsi_max:
        rsi_state = "PASS"
    else:
        rsi_state = "FAIL"
    distance = None if snap.close is None or snap.vwap is None else snap.close - snap.vwap
    distance_pct = None if distance is None or not snap.vwap else distance / snap.vwap * 100
    lines = [
        "AUTOMATIC SIGNAL",
        f"Symbol: {symbol}",
        f"Time: {evaluated_at}",
        f"Signal: {action}",
        f"Signal candle ts: {snap.timestamp if snap.timestamp is not None else 'unavailable'}",
        f"SMA9: {_fmt(snap.sma9)}",
        f"SMA21: {_fmt(snap.sma21)}",
        f"SMA Gap: {_fmt(snap.sma_gap, signed=True)}",
        f"SMA Gap %: {_fmt(snap.sma_gap_pct, 3, signed=True)}%",
        f"Close: {_fmt(snap.close)}",
        f"VWAP: {_fmt(snap.vwap)}",
        f"VWAP distance: {_fmt(distance, signed=True)} ({_fmt(distance_pct, 2, signed=True)}%)",
        f"VWAP filter: {vwap_state}",
        f"Cumulative volume: {_fmt(snap.cumulative_volume, 0)}",
        f"Minute volume: {_fmt(snap.minute_volume, 0)}",
        f"Previous 20-minute average: {_fmt(snap.average_previous_20_volume, 0)}",
        f"Volume ratio: {_fmt(snap.volume_ratio, 2)}x",
        f"Configured multiple: {volume_multiple:.2f}x",
        f"Volume filter: {volume_state}",
        f"RSI(14): {_fmt(snap.rsi14, 1)}",
        f"RSI filter: {rsi_state}",
        f"ATR(14): {_fmt(snap.atr14)}",
        f"ADX(14): {_fmt(snap.adx14, 1)}",
        f"Open: {_fmt(snap.open)} High: {_fmt(snap.high)} Low: {_fmt(snap.low)} Close: {_fmt(snap.close)}",
        f"Direction: {snap.candle_direction or 'unavailable'}",
        f"Body: {_fmt(snap.candle_body, signed=True)} Range: {_fmt(snap.candle_range)} Density: {_fmt(snap.density_pct, 0)}%",
        f"FINAL DECISION: {decision}",
    ]
    if note:
        lines.append(note)
    return "\n".join(lines)


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
