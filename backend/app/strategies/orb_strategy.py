"""15-minute Opening Range Breakout strategy.

Long:  5-min candle closes above the opening-range high, with RVOL confirmed.
Short: 5-min candle closes below the opening-range low, with RVOL confirmed.
Stop:  range midpoint (or the prior 5-min candle's low/high if tighter).
Target: fixed 1:2 R:R, or a Supertrend(10,3) trail — trailing is computed by
        the caller on each subsequent candle via `trail_stop`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.strategies.scanner import OpeningRange

Side = Literal["BUY", "SELL"]


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class Signal:
    symbol: str
    side: Side
    entry: float
    stop_loss: float
    target: float


class ORBStrategy:
    def __init__(self, risk_reward: float = 2.0):
        self.risk_reward = risk_reward

    def evaluate(self, opening_range: OpeningRange, candle: Candle, prior_candle: Candle | None = None) -> Signal | None:
        if not opening_range.qualifies:
            return None

        midpoint = (opening_range.high + opening_range.low) / 2

        if candle.close > opening_range.high:
            stop = midpoint
            if prior_candle is not None:
                stop = max(stop, prior_candle.low)
            risk = candle.close - stop
            if risk <= 0:
                return None
            return Signal(
                symbol=opening_range.symbol,
                side="BUY",
                entry=candle.close,
                stop_loss=stop,
                target=candle.close + risk * self.risk_reward,
            )

        if candle.close < opening_range.low:
            stop = midpoint
            if prior_candle is not None:
                stop = min(stop, prior_candle.high)
            risk = stop - candle.close
            if risk <= 0:
                return None
            return Signal(
                symbol=opening_range.symbol,
                side="SELL",
                entry=candle.close,
                stop_loss=stop,
                target=candle.close - risk * self.risk_reward,
            )

        return None

    @staticmethod
    def supertrend_bands(
        candles: list[Candle], period: int = 10, multiplier: float = 3.0
    ) -> tuple[float, float] | None:
        """Minimal Supertrend(10,3) basic bands as (lower, upper).

        `lower` is the trail level for longs, `upper` for shorts. Returns None
        until there is enough candle history to compute ATR.
        """
        if len(candles) < period + 1:
            return None

        trs = []
        for i in range(1, len(candles)):
            c, p = candles[i], candles[i - 1]
            tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
            trs.append(tr)
        atr = sum(trs[-period:]) / period

        last = candles[-1]
        hl2 = (last.high + last.low) / 2
        return hl2 - multiplier * atr, hl2 + multiplier * atr

    @staticmethod
    def supertrend_stop(candles: list[Candle], period: int = 10, multiplier: float = 3.0) -> float | None:
        """Long-side trail level. Kept for callers that only trade long."""
        bands = ORBStrategy.supertrend_bands(candles, period, multiplier)
        return bands[0] if bands else None

    @staticmethod
    def adx(candles: list[Candle], period: int = 14) -> float | None:
        """Wilder's Average Directional Index — a trend-strength filter.

        ORB is a breakout strategy, and a breakout means nothing in a
        directionless market: the same 5-min candle that "breaks" a range in
        chop reverses on the next one. ADX doesn't say which way price will
        go, only whether a trend exists to break INTO. Readings below 20
        mark a weak/ranging market; the ORB cheat-sheet's own guidance is
        that trend-following breakout systems need a real trend to follow.

        Requires 2*period+1 candles: one period to seed +DM/-DM/TR, a second
        for Wilder's smoothing to stabilise, so a fresh session has no ADX
        yet — callers should treat None as "not yet knowable", not "blocked".
        """
        if len(candles) < period * 2 + 1:
            return None

        plus_dm, minus_dm, tr = [], [], []
        for i in range(1, len(candles)):
            c, p = candles[i], candles[i - 1]
            up_move = c.high - p.high
            down_move = p.low - c.low
            # Only the LARGER of the two counts, and only if positive — this
            # mutual exclusivity is what makes it "directional" movement
            # rather than just volatility; a common simplification drops it
            # and produces a materially different (wrong) indicator.
            plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
            minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
            tr.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))

        def wilder_smooth(values: list[float]) -> list[float]:
            smoothed = [sum(values[:period])]
            for v in values[period:]:
                smoothed.append(smoothed[-1] - smoothed[-1] / period + v)
            return smoothed

        tr_s, plus_s, minus_s = wilder_smooth(tr), wilder_smooth(plus_dm), wilder_smooth(minus_dm)

        dx_values = []
        for tr_v, plus_v, minus_v in zip(tr_s, plus_s, minus_s):
            if tr_v <= 0:
                continue
            plus_di = 100 * plus_v / tr_v
            minus_di = 100 * minus_v / tr_v
            di_sum = plus_di + minus_di
            dx_values.append(100 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0)

        if len(dx_values) < period:
            return None

        adx = sum(dx_values[:period]) / period
        for dx in dx_values[period:]:
            adx = (adx * (period - 1) + dx) / period
        return round(adx, 2)

    @staticmethod
    def bollinger_bandwidth(candles: list[Candle], period: int = 20, k: float = 2.0) -> dict | None:
        """Band width as a percentage of the middle band — how tightly price
        is coiled right now.

        Not used as a hard entry gate: unlike ADX (a real trend-strength
        threshold with cheat-sheet-backed guidance), "how tight is too
        tight" is a judgment call, not a bright line. Surfaced as context
        instead — a squeeze ahead of a breakout is a stronger signal than
        the same breakout out of an already-wide range, and this is what
        lets a human (or the AI gate) see that distinction.
        """
        if len(candles) < period:
            return None
        closes = [c.close for c in candles[-period:]]
        mean = sum(closes) / period
        variance = sum((c - mean) ** 2 for c in closes) / period
        std = variance**0.5
        upper, lower = mean + k * std, mean - k * std
        bandwidth_pct = (upper - lower) / mean * 100 if mean else 0.0
        return {
            "upper": round(upper, 2),
            "middle": round(mean, 2),
            "lower": round(lower, 2),
            "bandwidth_pct": round(bandwidth_pct, 3),
            # Rule of thumb, not a statistically fitted percentile: under 4%
            # width on a liquid large-cap is a visibly tight coil worth
            # flagging, not a threshold to trade blindly on its own.
            "squeeze": bandwidth_pct < 4.0,
        }
