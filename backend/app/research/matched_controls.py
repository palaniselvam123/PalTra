"""Time-matched control sampling.

A control that holds symbol, day, direction and a prerequisite stage constant
can still differ from its signals in *when during the session* it sits. That
matters because intraday drift accumulates: a bar sampled at 10:00 has most of
the session ahead of it, while one at 15:00 has almost none, and a
forward-return measurement that stops at the close therefore treats the two
very differently. If signals cluster at a different time of day than their
controls, the comparison measures position-in-session as much as it measures
the hypothesis.

This module adds time-of-day to the matched set. It is deliberately general —
nothing here refers to any particular hypothesis — and deterministic, so the
bucket a bar belongs to can be stated before any result is seen.

**Bucket width is 30 minutes, argued a priori.** Two constraints pull in
opposite directions: buckets must be wide enough that a (symbol, day, bucket)
cell usually contains several eligible control candidates, and narrow enough
that drift within a bucket is small next to drift between buckets. At a
5-minute interval, 30 minutes is 6 bars — the smallest round subdivision of the
session that reliably leaves more than a handful of candidates per cell. The
NSE session (09:15-15:30, 375 minutes) divides into 13 such buckets, the last
being a half-width tail.

**Widening, then skipping.** If a signal's own bucket has no eligible control,
the search widens to the adjacent buckets, and if that also fails the signal is
skipped. It is never filled from another day or another symbol: those are the
variables the matching exists to hold constant, and substituting across days
would break the day-level pairing the block permutation depends on. Skips are
counted and reported rather than silently absorbed.
"""
from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict
from dataclasses import dataclass, field

from app.core.market_clock import IST, MARKET_OPEN
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex

BUCKET_MINUTES = 30
MAX_WIDEN_STEPS = 1     # search this many buckets either side before skipping


def ist_time(ts: int) -> dt.time:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).time()


def ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def session_bucket(ts: int) -> int:
    """Index of the 30-minute session bucket containing `ts`.

    Bucket 0 is 09:15-09:45. Bars before the open (there should be none in a
    clean dataset) clamp to 0 rather than going negative.
    """
    t = ist_time(ts)
    minutes = (t.hour * 60 + t.minute) - (MARKET_OPEN.hour * 60 + MARKET_OPEN.minute)
    return max(minutes, 0) // BUCKET_MINUTES


def bucket_label(b: int) -> str:
    start = dt.datetime.combine(dt.date(2026, 1, 1), MARKET_OPEN) + dt.timedelta(
        minutes=b * BUCKET_MINUTES
    )
    end = start + dt.timedelta(minutes=BUCKET_MINUTES)
    return f"{start:%H:%M}-{end:%H:%M}"


@dataclass
class MatchStats:
    """How well the matching succeeded. Reported, never hidden."""

    exact: int = 0          # control found in the signal's own bucket
    widened: int = 0        # found in an adjacent bucket
    skipped: int = 0        # no eligible control; signal contributes none
    offsets: list[int] = field(default_factory=list)   # control bucket - signal bucket
    population: int = 0     # candidate bars before horizon eligibility
    horizon_eligible: int = 0   # candidate bars after it

    @property
    def attempted(self) -> int:
        return self.exact + self.widened + self.skipped

    def as_dict(self) -> dict:
        n = self.attempted or 1
        mean_offset = sum(self.offsets) / len(self.offsets) if self.offsets else 0.0
        return {
            "attempted": self.attempted,
            "exact_pct": round(self.exact / n * 100, 1),
            "widened_pct": round(self.widened / n * 100, 1),
            "skipped_pct": round(self.skipped / n * 100, 1),
            "mean_bucket_offset": round(mean_offset, 3),
            "population": self.population,
            "horizon_eligible": self.horizon_eligible,
            "horizon_feasibility_pct": (
                round(self.horizon_eligible / self.population * 100, 1) if self.population else 0.0
            ),
        }


class TimeMatchedSampler:
    """Samples control bars matched on symbol, day, direction and session bucket.

    The eligible population is supplied by the caller — for a staged hypothesis
    that is the prerequisite-stage bar list, so the stage condition stays held
    constant and only the later stages vary.
    """

    def __init__(
        self,
        candles: list[OHLCV],
        eligible_by_direction: dict[str, list[int]],
        horizon: int,
        session_index: SessionIndex | None = None,
    ):
        """Build the matched-control population for ONE horizon.

        `horizon` is required rather than optional: a control population is only
        well defined against the window it will be measured over. Candidates
        whose forward window does not fit inside their session are removed HERE,
        before any sampling, so the surviving population is the one the signal
        itself would have been drawn from.

        The alternative — sample first, drop afterwards when the window turns
        out not to fit — quietly biases the control earlier in the session,
        because an earlier bar is likelier to have room. That is the defect this
        parameter exists to prevent, so it cannot be omitted.
        """
        self.candles = candles
        self.horizon = horizon
        self.index = session_index or SessionIndex(candles)
        self.stats_population = 0
        self.stats_eligible = 0

        # (day, direction, bucket) -> [bar indices], horizon-eligible only
        self._cells: dict[tuple[dt.date, str, int], list[int]] = defaultdict(list)
        for direction, indices in eligible_by_direction.items():
            for i in indices:
                self.stats_population += 1
                if not self.index.is_forward_window_valid(i, horizon):
                    continue
                self.stats_eligible += 1
                ts = candles[i].ts
                self._cells[(ist_date(ts), direction, session_bucket(ts))].append(i)

    def feasibility(self) -> dict:
        """Share of the candidate population that survived the horizon rule."""
        return {
            "population": self.stats_population,
            "horizon_eligible": self.stats_eligible,
            "horizon_feasibility_pct": (
                round(self.stats_eligible / self.stats_population * 100, 1)
                if self.stats_population else 0.0
            ),
        }

    def candidates(self, signal_idx: int, direction: str, widen: int = MAX_WIDEN_STEPS) -> list[int]:
        """Eligible bars for this signal, nearest bucket first.

        Returns the signal's own bucket if it has any candidates; otherwise the
        adjacent buckets, pooled. Never mixes the two: an exact match is
        strictly better than a widened one, so a bucket with candidates is used
        alone rather than diluted.
        """
        ts = self.candles[signal_idx].ts
        day, b = ist_date(ts), session_bucket(ts)

        exact = [i for i in self._cells.get((day, direction, b), []) if i != signal_idx]
        if exact:
            return exact

        pooled: list[int] = []
        for step in range(1, widen + 1):
            for nb in (b - step, b + step):
                pooled += [i for i in self._cells.get((day, direction, nb), []) if i != signal_idx]
            if pooled:
                return pooled
        return []

    def sample(
        self, signal_idx: int, direction: str, rng: random.Random, stats: MatchStats | None = None
    ) -> int | None:
        """One matched control bar, or None when the signal must be skipped."""
        ts = self.candles[signal_idx].ts
        own = session_bucket(ts)

        exact = self.candidates(signal_idx, direction, widen=0)
        if exact:
            pick = rng.choice(exact)
            if stats is not None:
                stats.exact += 1
                stats.offsets.append(session_bucket(self.candles[pick].ts) - own)
            return pick

        widened = self.candidates(signal_idx, direction, widen=MAX_WIDEN_STEPS)
        if widened:
            pick = rng.choice(widened)
            if stats is not None:
                stats.widened += 1
                stats.offsets.append(session_bucket(self.candles[pick].ts) - own)
            return pick

        if stats is not None:
            stats.skipped += 1
        return None

    def record_feasibility(self, stats: MatchStats) -> None:
        """Copy this sampler's population figures onto a MatchStats."""
        stats.population = self.stats_population
        stats.horizon_eligible = self.stats_eligible
