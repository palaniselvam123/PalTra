"""Causal ordered impulse identification — a structural primitive, not a strategy.

This module answers one question about one bar: *at bar t, looking only
backwards, is there an ordered directional impulse, and how large was it?*

It deliberately does **not**:

* generate signals,
* apply any threshold (`K` is what this is used to calibrate),
* evaluate pullbacks or triggers,
* look at anything that happens after bar t.

Separated from the eventual hypothesis generator so that the impulse
measurement can be calibrated and its non-repainting behaviour tested before any
trading rule is written on top of it.

**Ordering is structural, not checked afterwards.** The high is located first,
then the low is searched only in the bars *preceding* that high. An earlier
draft took the window's max and min independently, which allowed the low to fall
after the high — in which case the "impulse range" was measuring the pullback,
and a directionless zig-zag satisfied a depth band by construction.

**The window never crosses a session boundary.** An overnight gap is not an
intraday impulse; including one would inflate the range with a move no intraday
trader could participate in. Bars whose full lookback is not available inside
the same session are therefore not evaluated.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.core.market_clock import IST
from app.services.indicators import OHLCV


@dataclass(frozen=True)
class Impulse:
    """An ordered directional move ending before bar `t`."""

    direction: str          # "UP" (long candidate) or "DOWN" (short candidate)
    low_idx: int
    high_idx: int
    impulse_low: float
    impulse_high: float

    @property
    def range(self) -> float:
        return self.impulse_high - self.impulse_low

    @property
    def start_idx(self) -> int:
        return self.low_idx if self.direction == "UP" else self.high_idx

    @property
    def end_idx(self) -> int:
        """Bar on which the impulse completed — the anchor a pullback runs from."""
        return self.high_idx if self.direction == "UP" else self.low_idx

    @property
    def bars(self) -> int:
        return self.end_idx - self.start_idx

    def score(self, atr: float) -> float | None:
        """Impulse size in ATR units. None when ATR is unavailable or zero."""
        if not atr or atr <= 0:
            return None
        return self.range / atr


def _ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def window_start(candles: list[OHLCV], t: int, lookback: int) -> int | None:
    """First index of the lookback window, or None if it would leave the session.

    Requiring the *full* lookback inside one session (rather than truncating at
    the open) keeps every impulse_score comparable — a score measured over 4
    bars is not the same statistic as one measured over 12.
    """
    start = t - lookback
    if start < 0:
        return None
    if _ist_date(candles[start].ts) != _ist_date(candles[t].ts):
        return None
    return start


def find_impulse(
    candles: list[OHLCV], t: int, lookback: int = 12, direction: str = "UP"
) -> Impulse | None:
    """The ordered impulse visible at bar `t`, using only bars <= t.

    For UP: locate the highest high in [t-lookback, t-1] — bar t is excluded
    because a completed impulse must already be behind us — then the lowest low
    in [t-lookback, high_idx]. Bounding the low search above by `high_idx` is
    what makes the ordering structural. Ties resolve to the earliest index so
    the result is deterministic.

    DOWN mirrors exactly. Returns None when no ordered pair exists.
    """
    start = window_start(candles, t, lookback)
    if start is None:
        return None
    end = t - 1
    if end <= start:
        return None

    if direction == "UP":
        high_idx = max(range(start, end + 1), key=lambda i: (candles[i].high, -i))
        low_idx = min(range(start, high_idx + 1), key=lambda i: (candles[i].low, i))
        if low_idx >= high_idx:
            return None  # no advance: the low is not before the high
    else:
        low_idx = min(range(start, end + 1), key=lambda i: (candles[i].low, i))
        high_idx = max(range(start, low_idx + 1), key=lambda i: (candles[i].high, -i))
        if high_idx >= low_idx:
            return None

    return Impulse(
        direction=direction,
        low_idx=low_idx,
        high_idx=high_idx,
        impulse_low=candles[low_idx].low,
        impulse_high=candles[high_idx].high,
    )


def pullback_bar_count(impulse: Impulse, t: int) -> int:
    """Bars of pullback strictly between the impulse end and the trigger bar.

    The trigger bar `t` is NOT a pullback bar. The pullback occupies
    [end_idx + 1, t - 1], so the count is `t - end_idx - 1` and a valid
    structure needs at least 1 — meaning `t >= end_idx + 2`.
    """
    return t - impulse.end_idx - 1
