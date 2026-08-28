"""Causal observation eligibility — one definition, shared by signals and controls.

An observation at bar `i` with horizon `h` is only meaningful if the whole
forward window `[i+1, i+h]` lies inside the same trading session. Otherwise the
measurement either runs past the close or leaks across an overnight gap.

**Why this needs to be a shared layer rather than a check inside each caller.**
It was previously enforced by each consumer computing its own forward return and
returning None when the window did not fit. For a signal that is correct — the
observation simply does not exist. For a *control* it is not, because the
control was sampled first and discarded afterwards:

    sample a control bar  ->  compute its forward return  ->  drop if it ran out

That silently changes the control population. Within a 30-minute bucket an
earlier bar is likelier to have room for its window, so surviving controls skew
earlier in the session and capture more of the day's remaining drift. Measured
on the H004 screening, the entire stage-A population scored −0.0214 against a
control drawn from *itself*, where the true edge is zero by construction.

The rule here is therefore applied **before sampling**, to build the eligible
population, rather than after it as a filter.

**Sessions come from the data.** Bars are grouped by IST calendar date, so a
weekend or an NSE holiday is simply a date with no bars and needs no calendar:
nothing can span it, because eligibility requires the window to stay inside one
session. This keeps the rule correct without a holiday list that would go stale.

**Missing bars.** Eligibility counts *bars*, not clock time, matching the
convention every existing hypothesis already used. A session with a gap in it
therefore yields a window spanning more wall-clock time than nominal;
`window_is_contiguous` reports that rather than silently redefining the rule.
"""
from __future__ import annotations

import datetime as dt
from bisect import bisect_right

from app.core.market_clock import IST
from app.services.indicators import OHLCV


def ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


class SessionIndex:
    """Session boundaries for one symbol's bar series.

    Built once per series; every eligibility question is then O(log n) or O(1).
    """

    def __init__(self, candles: list[OHLCV]):
        self.candles = candles
        self._day: list[dt.date] = [ist_date(c.ts) for c in candles]
        # Index of the LAST bar of the session each bar belongs to.
        self._session_end: list[int] = [0] * len(candles)
        self._session_start: list[int] = [0] * len(candles)
        start = 0
        for i in range(len(candles)):
            if i > 0 and self._day[i] != self._day[i - 1]:
                for j in range(start, i):
                    self._session_end[j] = i - 1
                start = i
            self._session_start[i] = start
        for j in range(start, len(candles)):
            self._session_end[j] = len(candles) - 1

    def __len__(self) -> int:
        return len(self.candles)

    def session_of(self, i: int) -> dt.date:
        return self._day[i]

    def session_start(self, i: int) -> int:
        return self._session_start[i]

    def session_end(self, i: int) -> int:
        return self._session_end[i]

    def is_forward_window_valid(self, i: int, horizon: int) -> bool:
        """Does `[i+1, i+horizon]` lie entirely inside bar `i`'s own session?

        This is the single definition of observation eligibility. A signal that
        fails it does not exist; a control candidate that fails it must never
        enter the sampling population.
        """
        if i < 0 or i >= len(self.candles) or horizon < 1:
            return False
        return i + horizon <= self._session_end[i]

    def window_is_contiguous(self, i: int, horizon: int, bar_seconds: int) -> bool:
        """Diagnostic: does the window contain a gap in the bar series?

        Not part of eligibility — reported so a dataset with missing bars can be
        recognised rather than quietly producing wider windows than nominal.
        """
        if not self.is_forward_window_valid(i, horizon):
            return False
        expected = self.candles[i].ts + horizon * bar_seconds
        return self.candles[i + horizon].ts == expected

    def eligible(self, horizon: int, among: list[int] | None = None) -> list[int]:
        """Bars whose forward window fits, optionally restricted to `among`.

        This is what a control population must be built from.
        """
        source = among if among is not None else range(len(self.candles))
        return [i for i in source if self.is_forward_window_valid(i, horizon)]

    def feasibility(self, horizon: int, among: list[int] | None = None) -> dict:
        """How much of a population survives the eligibility rule."""
        source = list(among) if among is not None else list(range(len(self.candles)))
        kept = self.eligible(horizon, source)
        return {
            "population": len(source),
            "eligible": len(kept),
            "feasibility_pct": round(len(kept) / len(source) * 100, 2) if source else 0.0,
        }


def forward_return_pct(
    candles: list[OHLCV], index: SessionIndex, i: int, side: str, horizon: int
) -> float | None:
    """Signed forward move in the stated direction, or None if ineligible.

    Eligibility is delegated to `SessionIndex` so signals and controls cannot
    diverge on what counts as measurable.
    """
    if not index.is_forward_window_valid(i, horizon):
        return None
    entry = candles[i].close
    if entry <= 0:
        return None
    exit_ = candles[i + horizon].close
    return (exit_ - entry) * (1.0 if side == "BUY" else -1.0) / entry * 100
