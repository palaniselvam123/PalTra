"""Indicator library — one implementation, used by both the chart and the bot.

Deliberately server-side rather than computed in the browser. A chart that
draws its own JavaScript ADX would eventually disagree with the Python ADX the
entry gate actually rejected on, and then the chart is lying about why a trade
did or didn't happen. Everything here returns series aligned index-for-index
with the input candles, with `None` during the warm-up period, which is what a
charting library needs and what makes "no value yet" visually honest instead of
a fabricated zero.

Formulas follow Wilder's originals where they differ from the common
simplified versions (RSI, ADX, ATR all use Wilder smoothing, not a plain SMA).
"""
from __future__ import annotations

from dataclasses import dataclass

Series = list[float | None]


@dataclass
class OHLCV:
    ts: int          # epoch seconds, candle open time
    open: float
    high: float
    low: float
    close: float
    volume: int


# ---------- primitives ------------------------------------------------------


def _sma_list(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    running = sum(values[:period])
    out[period - 1] = running / period
    for i in range(period, len(values)):
        running += values[i] - values[i - period]
        out[i] = running / period
    return out


def _ema_list(values: list[float], period: int) -> Series:
    """Seeded with an SMA, which is the conventional way to start an EMA —
    seeding with the first value alone makes early output depend heavily on
    one arbitrary candle.
    """
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    k = 2 / (period + 1)
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def _wilder_list(values: list[float], period: int) -> Series:
    """Wilder's smoothing: like an EMA with k = 1/period. Used by RSI, ATR and
    ADX — substituting a standard EMA here produces visibly different numbers.
    """
    out: Series = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def _true_ranges(candles: list[OHLCV]) -> list[float]:
    """One TR per candle from index 1 onward; length is len(candles) - 1."""
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    return trs


def _shift(values: Series, by: int, length: int) -> Series:
    """Right-align a series computed over a shorter derived array (e.g. one
    that starts at candle 1) back onto the full candle index.
    """
    out: Series = [None] * length
    for i, v in enumerate(values):
        idx = i + by
        if 0 <= idx < length:
            out[idx] = v
    return out


# ---------- overlays --------------------------------------------------------


def sma(candles: list[OHLCV], period: int = 20) -> Series:
    return _sma_list([c.close for c in candles], period)


def ema(candles: list[OHLCV], period: int = 20) -> Series:
    return _ema_list([c.close for c in candles], period)


def vwap(candles: list[OHLCV], session_seconds: int = 86400) -> Series:
    """Session-anchored VWAP: cumulative typical-price x volume over volume,
    reset at each session boundary.

    Anchoring matters — a VWAP that never resets drifts uselessly far from
    price after a few days, and intraday traders read VWAP specifically as
    "the average price paid so far TODAY".
    """
    out: Series = [None] * len(candles)
    cum_pv = 0.0
    cum_vol = 0.0
    current_session: int | None = None
    for i, c in enumerate(candles):
        session = c.ts // session_seconds
        if session != current_session:
            current_session = session
            cum_pv = 0.0
            cum_vol = 0.0
        typical = (c.high + c.low + c.close) / 3
        cum_pv += typical * c.volume
        cum_vol += c.volume
        out[i] = (cum_pv / cum_vol) if cum_vol > 0 else typical
    return out


def bollinger(candles: list[OHLCV], period: int = 20, k: float = 2.0) -> dict[str, Series]:
    closes = [c.close for c in candles]
    middle = _sma_list(closes, period)
    upper: Series = [None] * len(candles)
    lower: Series = [None] * len(candles)
    width: Series = [None] * len(candles)
    for i in range(period - 1, len(candles)):
        mid = middle[i]
        if mid is None:
            continue
        window = closes[i - period + 1 : i + 1]
        variance = sum((v - mid) ** 2 for v in window) / period
        std = variance**0.5
        upper[i] = mid + k * std
        lower[i] = mid - k * std
        width[i] = ((upper[i] - lower[i]) / mid * 100) if mid else None
    return {"upper": upper, "middle": middle, "lower": lower, "bandwidth_pct": width}


def atr(candles: list[OHLCV], period: int = 14) -> Series:
    if len(candles) < period + 1:
        return [None] * len(candles)
    return _shift(_wilder_list(_true_ranges(candles), period), 1, len(candles))


def supertrend(candles: list[OHLCV], period: int = 10, multiplier: float = 3.0) -> dict[str, Series]:
    """Supertrend with the trend-flip and band-locking logic.

    The band only ever tightens while the trend holds and resets on a flip —
    without that locking step the "indicator" is just an ATR envelope that
    loosens whenever volatility rises, which is precisely the behaviour a
    trailing stop must not have.
    """
    n = len(candles)
    value: Series = [None] * n
    direction: Series = [None] * n
    atr_series = atr(candles, period)

    final_upper = final_lower = None
    trend_up = True

    for i in range(n):
        a = atr_series[i]
        if a is None:
            continue
        hl2 = (candles[i].high + candles[i].low) / 2
        basic_upper = hl2 + multiplier * a
        basic_lower = hl2 - multiplier * a
        prev_close = candles[i - 1].close if i > 0 else candles[i].close

        if final_upper is None or final_lower is None:
            final_upper, final_lower = basic_upper, basic_lower
            trend_up = candles[i].close >= hl2
        else:
            final_upper = basic_upper if (basic_upper < final_upper or prev_close > final_upper) else final_upper
            final_lower = basic_lower if (basic_lower > final_lower or prev_close < final_lower) else final_lower
            if trend_up and candles[i].close < final_lower:
                trend_up = False
                final_upper = basic_upper
            elif not trend_up and candles[i].close > final_upper:
                trend_up = True
                final_lower = basic_lower

        value[i] = final_lower if trend_up else final_upper
        direction[i] = 1.0 if trend_up else -1.0

    return {"value": value, "direction": direction}


# ---------- oscillators -----------------------------------------------------


def rsi(candles: list[OHLCV], period: int = 14) -> Series:
    closes = [c.close for c in candles]
    n = len(closes)
    out: Series = [None] * n
    if n <= period:
        return out

    gains, losses = [], []
    for i in range(1, n):
        change = closes[i] - closes[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    def value(g: float, l: float) -> float:
        if l == 0:
            return 100.0
        return 100 - 100 / (1 + g / l)

    out[period] = value(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        out[i + 1] = value(avg_gain, avg_loss)
    return out


def macd(
    candles: list[OHLCV], fast: int = 12, slow: int = 26, signal: int = 9
) -> dict[str, Series]:
    closes = [c.close for c in candles]
    fast_ema = _ema_list(closes, fast)
    slow_ema = _ema_list(closes, slow)

    macd_line: Series = [None] * len(closes)
    for i in range(len(closes)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]

    # The signal line is an EMA of the MACD line, which itself only exists
    # after `slow` candles — so it is computed over the defined tail and
    # shifted back, not over a zero-padded array (padding would drag the
    # early signal line toward zero and invent crossovers).
    defined = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: Series = [None] * len(closes)
    histogram: Series = [None] * len(closes)
    if defined:
        offset = defined[0][0]
        sig = _ema_list([v for _, v in defined], signal)
        for j, v in enumerate(sig):
            if v is not None:
                signal_line[offset + j] = v
        for i in range(len(closes)):
            if macd_line[i] is not None and signal_line[i] is not None:
                histogram[i] = macd_line[i] - signal_line[i]

    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def adx(candles: list[OHLCV], period: int = 14) -> dict[str, Series]:
    """Wilder's ADX with +DI / -DI.

    The mutual-exclusivity rule below (only the larger of up/down move counts,
    and only if positive) is what makes this DIRECTIONAL movement rather than
    plain volatility; simplified versions that drop it produce a materially
    different indicator.
    """
    n = len(candles)
    empty: Series = [None] * n
    if n < period * 2 + 1:
        return {"adx": empty, "plus_di": list(empty), "minus_di": list(empty)}

    plus_dm, minus_dm = [], []
    for i in range(1, n):
        up_move = candles[i].high - candles[i - 1].high
        down_move = candles[i - 1].low - candles[i].low
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)

    tr_s = _wilder_list(_true_ranges(candles), period)
    plus_s = _wilder_list(plus_dm, period)
    minus_s = _wilder_list(minus_dm, period)

    plus_di_raw: Series = [None] * len(tr_s)
    minus_di_raw: Series = [None] * len(tr_s)
    dx_raw: list[tuple[int, float]] = []
    for i in range(len(tr_s)):
        tr_v, p_v, m_v = tr_s[i], plus_s[i], minus_s[i]
        if tr_v is None or p_v is None or m_v is None or tr_v <= 0:
            continue
        p_di = 100 * p_v / tr_v
        m_di = 100 * m_v / tr_v
        plus_di_raw[i] = p_di
        minus_di_raw[i] = m_di
        total = p_di + m_di
        dx_raw.append((i, 100 * abs(p_di - m_di) / total if total > 0 else 0.0))

    adx_raw: Series = [None] * len(tr_s)
    if len(dx_raw) >= period:
        running = sum(v for _, v in dx_raw[:period]) / period
        adx_raw[dx_raw[period - 1][0]] = running
        for idx, dx_val in dx_raw[period:]:
            running = (running * (period - 1) + dx_val) / period
            adx_raw[idx] = running

    return {
        "adx": _shift(adx_raw, 1, n),
        "plus_di": _shift(plus_di_raw, 1, n),
        "minus_di": _shift(minus_di_raw, 1, n),
    }


# ---------- dispatch --------------------------------------------------------

OVERLAYS = {"sma", "ema", "ema_fast", "ema_slow", "vwap", "bollinger", "supertrend"}
OSCILLATORS = {"rsi", "macd", "adx", "atr"}
AVAILABLE = OVERLAYS | OSCILLATORS


def compute(candles: list[OHLCV], names: list[str]) -> dict:
    """Computes the requested indicators. Unknown names are ignored rather
    than raising — a chart asking for something this build doesn't have should
    lose that one line, not fail to render.
    """
    result: dict = {}
    for name in names:
        key = name.strip().lower()
        if key == "sma":
            result["sma"] = sma(candles, 20)
        elif key in ("ema", "ema_fast"):
            result["ema_fast"] = ema(candles, 9)
        elif key == "ema_slow":
            result["ema_slow"] = ema(candles, 21)
        elif key == "vwap":
            result["vwap"] = vwap(candles)
        elif key == "bollinger":
            result["bollinger"] = bollinger(candles, 20, 2.0)
        elif key == "supertrend":
            result["supertrend"] = supertrend(candles, 10, 3.0)
        elif key == "rsi":
            result["rsi"] = rsi(candles, 14)
        elif key == "macd":
            result["macd"] = macd(candles)
        elif key == "adx":
            result["adx"] = adx(candles, 14)
        elif key == "atr":
            result["atr"] = atr(candles, 14)
    return result
