"""Time-matched control sampling tests.

The matching exists to hold time-of-day constant, so the tests that matter are
the ones proving it never quietly relaxes a constraint it claims to hold —
especially by borrowing a control from another day when the right cell is empty.
"""
from __future__ import annotations

import datetime as dt
import random

from app.research.matched_controls import (
    BUCKET_MINUTES, MatchStats, TimeMatchedSampler, bucket_label, ist_date, session_bucket,
)
from app.services.indicators import OHLCV

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def at(day: str, hh: int, mm: int) -> int:
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=IST).timestamp())


def session(day: str, bars: int = 75) -> list[OHLCV]:
    """A full 5-minute NSE session starting 09:15."""
    base = at(day, 9, 15)
    return [OHLCV(base + i * 300, 100, 101, 99, 100, 1000) for i in range(bars)]


class TestBucketing:
    def test_open_is_bucket_zero(self):
        assert session_bucket(at("2026-06-01", 9, 15)) == 0
        assert session_bucket(at("2026-06-01", 9, 44)) == 0

    def test_bucket_boundary_is_exclusive_at_the_top(self):
        assert session_bucket(at("2026-06-01", 9, 45)) == 1

    def test_close_falls_in_the_last_bucket(self):
        assert session_bucket(at("2026-06-01", 15, 25)) == 12

    def test_buckets_are_thirty_minutes(self):
        a = session_bucket(at("2026-06-01", 11, 0))
        b = session_bucket(at("2026-06-01", 11, 0 + BUCKET_MINUTES))
        assert b == a + 1

    def test_pre_open_clamps_to_zero_rather_than_going_negative(self):
        assert session_bucket(at("2026-06-01", 9, 0)) == 0

    def test_label_is_readable(self):
        assert bucket_label(0) == "09:15-09:45"
        assert bucket_label(4) == "11:15-11:45"

    def test_bucketing_is_deterministic(self):
        ts = at("2026-06-01", 12, 7)
        assert session_bucket(ts) == session_bucket(ts)


class TestMatching:
    """Bucket matching itself. A horizon of 1 keeps the whole population
    eligible, so these isolate the matching from the horizon rule (which
    `test_observation_window.py` covers)."""

    def setup_method(self):
        self.candles = session("2026-06-01")
        # eligible = every 3rd bar, both directions
        self.eligible = {"UP": list(range(0, 75, 3)), "DOWN": list(range(1, 75, 3))}
        self.sampler = TimeMatchedSampler(self.candles, self.eligible, horizon=1)
        self.rng = random.Random(1)

    def test_control_lands_in_the_signals_own_bucket(self):
        for signal_idx in (10, 25, 40, 60):
            pick = self.sampler.sample(signal_idx, "UP", self.rng)
            assert pick is not None
            assert session_bucket(self.candles[pick].ts) == session_bucket(self.candles[signal_idx].ts)

    def test_control_is_never_the_signal_bar_itself(self):
        for _ in range(50):
            pick = self.sampler.sample(12, "UP", self.rng)
            assert pick != 12

    def test_control_respects_direction(self):
        pick = self.sampler.sample(10, "DOWN", self.rng)
        assert pick in self.eligible["DOWN"]

    def test_control_stays_on_the_same_day(self):
        two_days = session("2026-06-01") + session("2026-06-02")
        sampler = TimeMatchedSampler(two_days, {"UP": list(range(0, 150, 3))}, horizon=1)
        for signal_idx in (10, 80, 120):
            pick = sampler.sample(signal_idx, "UP", random.Random(2))
            assert ist_date(two_days[pick].ts) == ist_date(two_days[signal_idx].ts)

    def test_empty_bucket_widens_to_a_neighbour(self):
        """Bucket 2 (10:15-10:45) is bars 12-17; leave it empty."""
        eligible = {"UP": [i for i in range(0, 75, 2) if not 12 <= i <= 17]}
        sampler = TimeMatchedSampler(self.candles, eligible, horizon=1)
        stats = MatchStats()
        pick = sampler.sample(14, "UP", random.Random(3), stats)
        assert pick is not None
        assert abs(session_bucket(self.candles[pick].ts) - session_bucket(self.candles[14].ts)) == 1
        assert stats.widened == 1 and stats.exact == 0

    def test_no_eligible_control_anywhere_near_is_skipped_not_substituted(self):
        """The failure mode this guards: filling from a distant bucket, or from
        another day, would silently undo the matching."""
        eligible = {"UP": [0, 1, 2]}          # only bucket 0
        sampler = TimeMatchedSampler(self.candles, eligible, horizon=1)
        stats = MatchStats()
        pick = sampler.sample(60, "UP", random.Random(4), stats)   # bucket 9
        assert pick is None
        assert stats.skipped == 1

    def test_exact_match_is_preferred_over_widening(self):
        """A populated own-bucket must be used alone, not pooled with
        neighbours — pooling would dilute the match it just achieved."""
        eligible = {"UP": [6, 7, 8, 13]}      # bucket 1 has three, bucket 2 has one
        sampler = TimeMatchedSampler(self.candles, eligible, horizon=1)
        picks = {sampler.sample(9, "UP", random.Random(s)) for s in range(30)}
        assert picks <= {6, 7, 8}

    def test_signal_with_no_peers_in_its_own_bucket_but_itself(self):
        eligible = {"UP": [30]}               # only the signal bar
        sampler = TimeMatchedSampler(self.candles, eligible, horizon=1)
        assert sampler.sample(30, "UP", random.Random(5)) is None


class TestStats:
    def test_offsets_are_zero_when_matching_is_exact(self):
        candles = session("2026-06-01")
        sampler = TimeMatchedSampler(candles, {"UP": list(range(0, 75, 2))}, horizon=1)
        stats = MatchStats()
        rng = random.Random(6)
        for i in range(4, 70, 7):
            sampler.sample(i, "UP", rng, stats)
        assert stats.offsets and all(o == 0 for o in stats.offsets)
        assert stats.as_dict()["mean_bucket_offset"] == 0.0

    def test_percentages_sum_to_a_hundred(self):
        s = MatchStats(exact=7, widened=2, skipped=1)
        d = s.as_dict()
        assert d["attempted"] == 10
        assert d["exact_pct"] + d["widened_pct"] + d["skipped_pct"] == 100.0

    def test_empty_stats_do_not_divide_by_zero(self):
        assert MatchStats().as_dict()["attempted"] == 0
