"""H003 v1 — Pullback Continuation signal generator.

Pre-registered rule, frozen before implementation. Tests whether a continuation
trigger after a defined pullback carries directional information beyond an
arbitrary entry following the same directional impulse.

Structure, in four stages:

    A. ordered directional impulse     (impulse_structure.find_impulse, K = 3.5)
    B. defined pullback                (duration and depth)
    C. pullback structurally valid     (impulse not destroyed)
    D. continuation trigger            (close beyond the previous bar's extreme)

**No indicator filters.** ATR appears only to normalise the impulse size so it
is comparable across instruments. There is no moving average, RSI, MACD, ADX,
VWAP, volume or candle-count condition anywhere in this file, deliberately: the
hypothesis under test is structural, and a filter that improved results would
make the outcome unattributable.

**Frozen parameters** (see PLAN.md Step 5c). K was calibrated from the
development-period predictor distribution alone — the rounded 75th percentile of
causal impulse_score — with no reference to forward outcomes. It must not be
re-calibrated against performance.

**Timing.** The trigger bar is not a pullback bar. The pullback occupies
[end_idx + 1, t - 1] and its depth is measured from those completed bars only;
bar t contributes the trigger and nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.research.impulse_structure import Impulse, find_impulse, pullback_bar_count
from app.services.indicators import OHLCV, atr

LOOKBACK = 12
ATR_PERIOD = 14
K = 3.5


@dataclass(frozen=True)
class PullbackParams:
    variant: str
    depth_min: float
    depth_max: float
    lookback: int = LOOKBACK
    atr_period: int = ATR_PERIOD
    k: float = K

    def in_band(self, retrace: float) -> bool:
        """Depth band membership.

        Variant A is half-open at the top and B closed, so a retracement of
        exactly 0.40 — the value the two pre-registered bands share — belongs to
        exactly one variant. The registered bands are otherwise unchanged; this
        only removes an ambiguity at a single point, which floating-point depths
        essentially never hit but which must still be deterministic.
        """
        if self.variant == "A":
            return self.depth_min <= retrace < self.depth_max
        return self.depth_min <= retrace <= self.depth_max


VARIANT_A = PullbackParams(variant="A", depth_min=0.20, depth_max=0.40)   # shallow
VARIANT_B = PullbackParams(variant="B", depth_min=0.40, depth_max=0.65)   # medium

VARIANTS = {"A": VARIANT_A, "B": VARIANT_B}


@dataclass(frozen=True)
class SignalRecord:
    """A signal plus its anatomy. The anatomy is reported for diagnostics and
    is never used as a filter."""

    index: int
    side: str
    symbol: str
    ts: int
    variant: str
    impulse_bars: int
    pullback_bars: int
    retrace: float
    impulse_score: float


def _retrace(candles: list[OHLCV], imp: Impulse, t: int) -> tuple[float, bool] | None:
    """Retracement depth from the COMPLETED pullback bars, and stage-C validity.

    Returns None when there are no completed pullback bars to measure.
    """
    first, last = imp.end_idx + 1, t - 1
    if last < first:
        return None
    window = candles[first : last + 1]
    span = imp.range
    if span <= 0:
        return None

    if imp.direction == "UP":
        pullback_low = min(c.low for c in window)
        return (imp.impulse_high - pullback_low) / span, pullback_low > imp.impulse_low

    pullback_high = max(c.high for c in window)
    return (pullback_high - imp.impulse_low) / span, pullback_high < imp.impulse_high


def stage_a_bars(candles: list[OHLCV], direction: str, params: PullbackParams) -> list[int]:
    """Indices where the ordered impulse exists and clears K, ignoring stages
    B-D.

    This is the population Control C samples from: it holds the impulse
    selection constant so that only the pullback and trigger vary.
    """
    atr_series = atr(candles, params.atr_period)
    out: list[int] = []
    for t in range(len(candles)):
        a = atr_series[t]
        if not a or a <= 0:
            continue
        imp = find_impulse(candles, t, params.lookback, direction)
        if imp is None:
            continue
        score = imp.score(a)
        if score is not None and score >= params.k:
            out.append(t)
    return out


def generate_detailed(
    symbol: str, candles: list[OHLCV], params: PullbackParams
) -> list[SignalRecord]:
    """Every H003 signal in this series, with its anatomy attached."""
    atr_series = atr(candles, params.atr_period)
    out: list[SignalRecord] = []

    for t in range(len(candles)):
        a = atr_series[t]
        if not a or a <= 0:
            continue

        for direction, side in (("UP", "BUY"), ("DOWN", "SELL")):
            # --- Stage A: ordered directional impulse ---
            imp = find_impulse(candles, t, params.lookback, direction)
            if imp is None:
                continue
            score = imp.score(a)
            if score is None or score < params.k:
                continue

            # --- Stage B: defined pullback ---
            pb = pullback_bar_count(imp, t)
            if pb < 1 or pb > imp.bars:
                continue
            measured = _retrace(candles, imp, t)
            if measured is None:
                continue
            retrace, structurally_valid = measured
            if not params.in_band(retrace):
                continue

            # --- Stage C: pullback did not destroy the impulse ---
            if not structurally_valid:
                continue

            # --- Stage D: continuation trigger ---
            prev = candles[t - 1]
            if direction == "UP":
                if not candles[t].close > prev.high:
                    continue
            elif not candles[t].close < prev.low:
                continue

            out.append(
                SignalRecord(
                    index=t, side=side, symbol=symbol, ts=candles[t].ts,
                    variant=params.variant, impulse_bars=imp.bars, pullback_bars=pb,
                    retrace=round(retrace, 4), impulse_score=round(score, 3),
                )
            )
    return out


def generate(symbol: str, candles: list[OHLCV], params: PullbackParams) -> list[tuple[int, str]]:
    """Harness interface: (bar_index, side) pairs, matching H001 and H002."""
    return [(r.index, r.side) for r in generate_detailed(symbol, candles, params)]
