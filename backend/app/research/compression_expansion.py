"""H005 v1 — Compression → Expansion.

Pre-registered rule, frozen before any forward outcome is read. Tests whether
a transition from unusually small realized true range into a bar that expands
materially beyond that window's own scale carries directional information.

Structure:

    compression window   bars [t-K, t-1], K = 12, same session
    compression          C2(t) = mean(TR[t-K, t-1]) / ATR(14)[t-1]
    transition bar       bar t  (not part of the compression window)
    expansion            C3(t) = TR[t] / mean(TR[t-K, t-1])
    direction            sign(close[t] - open[t]); flat bodies excluded
    observation          close of bar t

**ATR is taken at t-1.** The project's Wilder ATR at t includes TR[t], which
would let the expansion bar contaminate the compression scale.

**Not a range breakout.** Direction is the transition bar's body, not a close
beyond the window high/low. That path recreates H002 as a rolling opening range
and is not this hypothesis.

**No indicator filters.** ATR normalises recent bar size so instruments are
comparable. There is no EMA, RSI, MACD, ADX, VWAP, Supertrend, Bollinger or
volume condition.

**No forward outcome is computed here.** This module identifies the event.
Eligibility of a forward window is the shared SessionIndex layer; Control C
populations are built through TimeMatchedSampler, which applies that layer
before any sampling.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.core.market_clock import IST
from app.research.matched_controls import TimeMatchedSampler
from app.services.indicators import OHLCV, atr
from app.services.observation_window import SessionIndex

LOOKBACK = 12
ATR_PERIOD = 14
C2_MAX = 0.794
C3_MIN = 2.0
HORIZONS = (6, 12, 24)

C2_MAX_PROVENANCE = (
    "development-period 10th percentile of C2, measured on 298,865 eligible bars, "
    "38 sessions (2026-06-01..2026-07-23), 125 symbols (raw p10 = 0.7938, registered "
    "as 0.794). Predictor distribution only. 'Unusually compressed' is the left tail "
    "of that distribution — p10, not p25 (which is merely below typical). Must not "
    "be re-calibrated against performance."
)
C3_MIN_PROVENANCE = (
    "a-priori round multiple: twice the compression window's own mean true range. "
    "C3 = 1 is the natural neutral (transition bar equal to the window it follows). "
    "2.0 is 'materially expanded' relative to that window, not a percentile of C3 "
    "and not compared against forward returns. Must not be re-calibrated against "
    "performance."
)


@dataclass(frozen=True)
class H005Params:
    lookback: int = LOOKBACK
    atr_period: int = ATR_PERIOD
    c2_max: float = C2_MAX
    c3_min: float = C3_MIN


PARAMS = H005Params()


@dataclass(frozen=True)
class Measures:
    """Causal C2/C3 at bar t, with no threshold applied."""

    index: int
    c2: float
    c3: float
    atr_prev: float
    mean_tr: float
    tr_t: float
    direction: int | None   # +1 close>open, -1 close<open, None if flat


@dataclass(frozen=True)
class Event:
    index: int
    side: str               # "BUY" or "SELL"
    symbol: str
    ts: int
    c2: float
    c3: float
    direction: int          # +1 or -1


def _ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def compression_window_start(candles: list[OHLCV], t: int, lookback: int = LOOKBACK) -> int | None:
    """First index of [t-lookback, t-1], or None if the window would leave the session.

    Dropped, never truncated: a 4-bar stub at the open is not the same statistic
    as a 12-bar window, and overnight data is not an intraday compression.
    """
    start = t - lookback
    if start < 0:
        return None
    if _ist_date(candles[start].ts) != _ist_date(candles[t].ts):
        return None
    return start


def aligned_true_ranges(candles: list[OHLCV]) -> list[float | None]:
    """TR[i] for i >= 1. TR[0] is undefined (no previous close).

    TR[i] = max(high-low, |high-prev_close|, |low-prev_close|). Matches the
    project's ATR input. Bar t's TR is the expansion numerator and is never
    folded into C2.
    """
    out: list[float | None] = [None] * len(candles)
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        out[i] = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
    return out


def body_direction(bar: OHLCV) -> int | None:
    """Causal body direction of one completed bar. None when close == open."""
    if bar.close > bar.open:
        return 1
    if bar.close < bar.open:
        return -1
    return None


def measure(candles: list[OHLCV], t: int, params: H005Params = PARAMS,
            atr_series: list | None = None, trs: list | None = None) -> Measures | None:
    """C2 and C3 at bar t, or None when the observation is not causally defined.

    Reads bars <= t only. Compression uses [t-K, t-1] and ATR[t-1]. Expansion
    uses TR[t] against that same window mean. Flat bodies still produce a
    Measures with direction=None; the generator excludes them from events.
    """
    start = compression_window_start(candles, t, params.lookback)
    if start is None:
        return None
    if t < 1:
        return None

    atrs = atr_series if atr_series is not None else atr(candles, params.atr_period)
    a_prev = atrs[t - 1]
    if not a_prev or a_prev <= 0:
        return None

    tr_list = trs if trs is not None else aligned_true_ranges(candles)
    window = range(t - params.lookback, t)          # [t-K, t-1] — bar t excluded
    window_trs = [tr_list[i] for i in window]
    if any(v is None for v in window_trs):
        return None
    mean_tr = sum(window_trs) / params.lookback     # type: ignore[arg-type]
    if mean_tr <= 0:
        return None
    tr_t = tr_list[t]
    if tr_t is None or tr_t < 0:
        return None

    return Measures(
        index=t,
        c2=mean_tr / a_prev,
        c3=tr_t / mean_tr,
        atr_prev=a_prev,
        mean_tr=mean_tr,
        tr_t=tr_t,
        direction=body_direction(candles[t]),
    )


def _side(direction: int) -> str:
    return "BUY" if direction > 0 else "SELL"


def generate_detailed(
    symbol: str, candles: list[OHLCV], params: H005Params = PARAMS
) -> list[Event]:
    """Every H005 event in this series. No forward return is computed."""
    atr_series = atr(candles, params.atr_period)
    trs = aligned_true_ranges(candles)
    out: list[Event] = []
    for t in range(len(candles)):
        m = measure(candles, t, params, atr_series, trs)
        if m is None or m.direction is None:
            continue
        if m.c2 > params.c2_max:
            continue
        if m.c3 < params.c3_min:
            continue
        out.append(
            Event(
                index=t,
                side=_side(m.direction),
                symbol=symbol,
                ts=candles[t].ts,
                c2=round(m.c2, 6),
                c3=round(m.c3, 6),
                direction=m.direction,
            )
        )
    return out


def generate(
    symbol: str, candles: list[OHLCV], params: H005Params = PARAMS
) -> list[tuple[int, str]]:
    """Harness interface: (bar_index, side), matching H001-H003."""
    return [(e.index, e.side) for e in generate_detailed(symbol, candles, params)]


def compression_stage(
    candles: list[OHLCV], params: H005Params = PARAMS
) -> dict[str, list[int]]:
    """Bars with C2 compressed and a non-flat body, regardless of C3.

    The compression-stage population. Signals are the subset that also expand.
    """
    atr_series = atr(candles, params.atr_period)
    trs = aligned_true_ranges(candles)
    by: dict[str, list[int]] = {"BUY": [], "SELL": []}
    for t in range(len(candles)):
        m = measure(candles, t, params, atr_series, trs)
        if m is None or m.direction is None or m.c2 > params.c2_max:
            continue
        by[_side(m.direction)].append(t)
    return by


def compression_without_expansion(
    candles: list[OHLCV], params: H005Params = PARAMS
) -> dict[str, list[int]]:
    """Control C population: compressed, directional, and NOT expanded.

    Constructed in full before any sampling. Horizon eligibility is applied by
    TimeMatchedSampler at construction, never after a draw.
    """
    atr_series = atr(candles, params.atr_period)
    trs = aligned_true_ranges(candles)
    by: dict[str, list[int]] = {"BUY": [], "SELL": []}
    for t in range(len(candles)):
        m = measure(candles, t, params, atr_series, trs)
        if m is None or m.direction is None:
            continue
        if m.c2 > params.c2_max:
            continue
        if m.c3 >= params.c3_min:
            continue
        by[_side(m.direction)].append(t)
    return by


def control_a_index(signal_index: int) -> int:
    """Control A is the signal's own bar. Direction is randomised at evaluation.

    Eligibility is therefore identical to the signal's: both are observations
    at the same index, judged by SessionIndex.is_forward_window_valid.
    """
    return signal_index


def control_c_sampler(
    candles: list[OHLCV],
    horizon: int,
    params: H005Params = PARAMS,
    session_index: SessionIndex | None = None,
) -> TimeMatchedSampler:
    """Control C sampler for one horizon.

    Population = compression without expansion, built first. The sampler then
    drops candidates whose forward window does not fit, before any sample is
    drawn. That order is the whole point of TimeMatchedSampler.
    """
    return TimeMatchedSampler(
        candles,
        compression_without_expansion(candles, params),
        horizon,
        session_index=session_index,
    )
