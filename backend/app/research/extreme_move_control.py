"""Control C — the mechanical "room to revert" control. Input-only matching.

An extreme 1-ATR bar does not just happen at a moment; it *places* price near
the running session extreme. Measured on development data, the median event
already has ~74% of the printed session range sitting opposite it. So a fade
measured against nothing at all is partly the answer to a different question:

    "does price at 0.9 of the session range tend to come back?"

Control C answers that question directly. For each event it draws a NON-EVENT
bar from the same symbol, the same day, the same 30-minute bucket and
approximately the same place in the printed session range, then assigns it the
event's direction. If those bars fade too, the fade is location, not the event.

Nothing here reads a bar after t. Nothing here is a strategy, and no hypothesis
is registered.

**Matching mechanism.** Terciles of session position were considered first and
rejected on the development distribution of the *events*: their positions mass
against the top of the range (p25 0.44, p50 0.74, p75 0.94), so a top tercile
spanning 0.67-1.00 would happily pair an event at 0.99 with a control at 0.68 —
exactly the room difference the control exists to remove. Nearest-neighbour on
session position inside the cell needs no bin count, is deterministic, and
reports its own match error. A tercile match is kept as a coarse robustness
view, not as the primary.

**Caliper.** Fixed a priori at 0.10 of the printed session range: a tenth of
the day's range is the granularity at which "room" differs materially, and it
is the same order as the spread of positions inside one 30-minute bucket. It
was not tuned against any outcome. Pairs that cannot meet it are counted as
skipped, never silently widened past the existing framework's one-bucket step.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.research.matched_controls import MAX_WIDEN_STEPS
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex

POSITION_CALIPER = 0.10
POSITION_BINS = 3          # robustness view only; not the primary matcher
MATCH_HORIZON = 12         # feasibility is required at the LONGEST horizon


def session_positions(candles: list[OHLCV], index: SessionIndex) -> list[float | None]:
    """(close[t] − session_low) / (session_high − session_low), through t only.

    Running extremes are accumulated forward, so bar t's value can never see
    bar t+1. None when the session has printed no range yet.
    """
    out: list[float | None] = [None] * len(candles)
    hi = lo = 0.0
    current = -1
    for i, c in enumerate(candles):
        start = index.session_start(i)
        if start != current:
            current = start
            hi, lo = c.high, c.low
        else:
            hi = max(hi, c.high)
            lo = min(lo, c.low)
        out[i] = None if hi <= lo else (c.close - lo) / (hi - lo)
    return out


def tercile_cuts(values: list[float], bins: int = POSITION_BINS) -> tuple[float, ...]:
    """Equal-count cuts of a predictor-side distribution. No forward data."""
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return ()
    n = len(xs)
    return tuple(xs[min(int(n * k / bins), n - 1)] for k in range(1, bins))


def position_class(pos: float | None, cuts: tuple[float, ...]) -> int | None:
    """Index of the bin `pos` falls in. Lower edges are exclusive."""
    if pos is None:
        return None
    for k, cut in enumerate(cuts):
        if pos <= cut:
            return k
    return len(cuts)


@dataclass
class MatchStats:
    """How well Control C matched. Reported, never hidden."""

    exact: int = 0            # control found in the event's own bucket
    widened: int = 0          # found one bucket either side
    skipped_no_pool: int = 0  # no eligible non-event bar at all
    skipped_caliper: int = 0  # candidates existed but none within the caliper

    @property
    def matched(self) -> int:
        return self.exact + self.widened

    @property
    def skipped(self) -> int:
        return self.skipped_no_pool + self.skipped_caliper

    def as_dict(self) -> dict:
        total = self.matched + self.skipped
        return {
            "events_offered": total,
            "matched": self.matched,
            "exact_bucket": self.exact,
            "widened_one_bucket": self.widened,
            "skipped_no_pool": self.skipped_no_pool,
            "skipped_caliper": self.skipped_caliper,
            "match_rate_pct": (self.matched / total * 100) if total else None,
            "exact_share_pct": (self.exact / self.matched * 100) if self.matched else None,
        }


def match_nearest_position(
    events: list[tuple[int, int, float]],
    pool: dict[int, list[tuple[int, float]]],
    caliper: float = POSITION_CALIPER,
    max_widen: int = MAX_WIDEN_STEPS,
) -> tuple[dict[int, int], MatchStats]:
    """Pair each event with the closest-position non-event bar in its cell.

    `events` are (bar_index, bucket, position) for ONE (symbol, day).
    `pool` maps bucket -> list of eligible (bar_index, position) non-event bars
    for that same (symbol, day). Both must already satisfy forward-window
    feasibility: eligibility is decided before any outcome is looked at, so a
    control can never be dropped later for running out of session.

    Sampling is without replacement — one control bar serves one event, so a
    single quiet bar cannot stand in for a whole cluster. Deterministic: events
    are consumed in the given order and ties break to the lower bar index.
    """
    used: set[int] = set()
    matches: dict[int, int] = {}
    stats = MatchStats()

    for idx, bucket, pos in events:
        best: int | None = None
        best_gap = float("inf")
        best_step = 0
        for step in range(max_widen + 1):
            buckets = (bucket,) if step == 0 else (bucket - step, bucket + step)
            for b in buckets:
                for cand_idx, cand_pos in pool.get(b, ()):
                    if cand_idx in used:
                        continue
                    gap = abs(cand_pos - pos)
                    if gap < best_gap or (gap == best_gap and best is not None and cand_idx < best):
                        best, best_gap, best_step = cand_idx, gap, step
            if best is not None and best_gap <= caliper:
                break

        if best is None:
            stats.skipped_no_pool += 1
            continue
        if best_gap > caliper:
            stats.skipped_caliper += 1
            continue

        used.add(best)
        matches[idx] = best
        if best_step > 0:
            stats.widened += 1
        else:
            stats.exact += 1

    return matches, stats


def match_within_class(
    events: list[tuple[int, int, int]],
    pool: dict[tuple[int, int], list[int]],
    rng,
) -> tuple[dict[int, int], MatchStats]:
    """Coarse robustness matcher: same bucket AND same position tercile.

    `events` are (bar_index, bucket, position_class). `pool` maps
    (bucket, position_class) -> eligible non-event bar indices. A control is
    drawn at random from the cell, without replacement. No caliper: this is
    deliberately the coarse view, to show whether the conclusion depends on how
    tightly position is matched.
    """
    used: set[int] = set()
    matches: dict[int, int] = {}
    stats = MatchStats()
    for idx, bucket, cls in events:
        candidates = [c for c in pool.get((bucket, cls), ()) if c not in used]
        if not candidates:
            stats.skipped_no_pool += 1
            continue
        pick = candidates[rng.randrange(len(candidates))]
        used.add(pick)
        matches[idx] = pick
        stats.exact += 1
    return matches, stats


__all__ = [
    "MATCH_HORIZON",
    "MAX_WIDEN_STEPS",
    "MatchStats",
    "POSITION_BINS",
    "POSITION_CALIPER",
    "match_nearest_position",
    "match_within_class",
    "position_class",
    "session_positions",
    "tercile_cuts",
]
