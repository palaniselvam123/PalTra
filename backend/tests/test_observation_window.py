"""Observation eligibility, and proof that control sampling does not skew.

The last test class is the one that matters. Eligibility rules are easy to state
and easy to apply in the wrong order, and applying them after sampling rather
than before is invisible in every per-bar assertion — it only shows up as a
population that has quietly drifted earlier in the session.
"""
from __future__ import annotations

import datetime as dt
import random
import statistics

from app.research.matched_controls import MatchStats, TimeMatchedSampler, session_bucket
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex, forward_return_pct

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BARS_PER_SESSION = 75      # 09:15 -> 15:30 at 5 minutes


def session(day: str, bars: int = BARS_PER_SESSION, start=(9, 15)) -> list[OHLCV]:
    base = int(dt.datetime.strptime(day, "%Y-%m-%d")
               .replace(hour=start[0], minute=start[1], tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, 100.0, 101.0, 99.0, 100.0 + i * 0.01, 1000) for i in range(bars)]


class TestSessionBoundaries:
    def setup_method(self):
        self.candles = session("2026-06-01")
        self.index = SessionIndex(self.candles)

    def test_interior_bar_is_eligible(self):
        assert self.index.is_forward_window_valid(30, 6)
        assert self.index.is_forward_window_valid(30, 24)

    def test_last_bar_of_session_is_never_eligible(self):
        last = BARS_PER_SESSION - 1
        for h in (6, 12, 24):
            assert not self.index.is_forward_window_valid(last, h)

    def test_second_last_bar_is_eligible_only_at_horizon_one(self):
        second_last = BARS_PER_SESSION - 2
        assert self.index.is_forward_window_valid(second_last, 1)
        assert not self.index.is_forward_window_valid(second_last, 2)

    def test_exact_boundary_is_inclusive(self):
        """A window ending exactly on the session's last bar is valid."""
        last = BARS_PER_SESSION - 1
        assert self.index.is_forward_window_valid(last - 6, 6)
        assert not self.index.is_forward_window_valid(last - 5, 6)

    def test_horizon_must_be_positive(self):
        assert not self.index.is_forward_window_valid(30, 0)
        assert not self.index.is_forward_window_valid(30, -1)

    def test_out_of_range_index_is_not_eligible(self):
        assert not self.index.is_forward_window_valid(-1, 6)
        assert not self.index.is_forward_window_valid(999, 6)


class TestNoOvernightLeakage:
    def test_window_may_not_cross_into_the_next_session(self):
        candles = session("2026-06-01") + session("2026-06-02")
        index = SessionIndex(candles)
        last_of_day1 = BARS_PER_SESSION - 1
        assert not index.is_forward_window_valid(last_of_day1 - 2, 6)
        assert index.session_end(last_of_day1) == last_of_day1

    def test_second_session_is_independently_eligible(self):
        candles = session("2026-06-01") + session("2026-06-02")
        index = SessionIndex(candles)
        assert index.is_forward_window_valid(BARS_PER_SESSION + 30, 24)

    def test_a_holiday_gap_needs_no_calendar(self):
        """2026-06-02 absent entirely: nothing can span it, because eligibility
        requires the window to stay inside one session."""
        candles = session("2026-06-01") + session("2026-06-03")
        index = SessionIndex(candles)
        assert not index.is_forward_window_valid(BARS_PER_SESSION - 3, 6)
        assert index.session_of(BARS_PER_SESSION) == dt.date(2026, 6, 3)

    def test_forward_return_is_none_when_ineligible(self):
        candles = session("2026-06-01") + session("2026-06-02")
        index = SessionIndex(candles)
        assert forward_return_pct(candles, index, BARS_PER_SESSION - 2, "BUY", 6) is None
        assert forward_return_pct(candles, index, 30, "BUY", 6) is not None


class TestMissingBars:
    def test_eligibility_counts_bars_not_clock_time(self):
        """A session with a gap still yields a valid window; the rule is
        bar-based, matching every existing hypothesis."""
        full = session("2026-06-01")
        gapped = full[:20] + full[26:]           # six bars missing
        index = SessionIndex(gapped)
        assert index.is_forward_window_valid(18, 6)

    def test_contiguity_is_reported_separately(self):
        full = session("2026-06-01")
        gapped = full[:20] + full[26:]
        index = SessionIndex(gapped)
        assert index.is_forward_window_valid(18, 6)
        assert not index.window_is_contiguous(18, 6, bar_seconds=300)
        assert index.window_is_contiguous(5, 6, bar_seconds=300)


class TestFeasibilityReporting:
    def test_feasibility_matches_the_horizon(self):
        index = SessionIndex(session("2026-06-01"))
        assert index.feasibility(6)["eligible"] == BARS_PER_SESSION - 6
        assert index.feasibility(24)["eligible"] == BARS_PER_SESSION - 24

    def test_longer_horizons_are_strictly_less_feasible(self):
        index = SessionIndex(session("2026-06-01"))
        f6 = index.feasibility(6)["feasibility_pct"]
        f24 = index.feasibility(24)["feasibility_pct"]
        assert f6 > f24


class TestControlSamplingHasNoForwardSelection:
    """The defect this whole layer exists to prevent.

    Sampling first and discarding afterwards biases controls earlier in the
    session, because an earlier bar is likelier to have room for its window.
    Filtering the population first removes that by construction.
    """

    @staticmethod
    def _population() -> tuple[list[OHLCV], dict]:
        candles = session("2026-06-01")
        return candles, {"UP": list(range(0, BARS_PER_SESSION))}

    def test_every_sampled_control_has_a_valid_forward_window(self):
        candles, pool = self._population()
        sampler = TimeMatchedSampler(candles, pool, horizon=24)
        index = SessionIndex(candles)
        rng = random.Random(1)
        for signal in range(0, 50, 3):
            pick = sampler.sample(signal, "UP", rng)
            if pick is not None:
                assert index.is_forward_window_valid(pick, 24), (
                    f"control {pick} would have been discarded after sampling"
                )

    def test_late_session_candidates_are_absent_from_the_population(self):
        candles, pool = self._population()
        sampler = TimeMatchedSampler(candles, pool, horizon=24)
        # Bars 51..74 cannot support a 24-bar window.
        for signal in (40, 45, 48):
            for cand in sampler.candidates(signal, "UP"):
                assert cand <= BARS_PER_SESSION - 24 - 1 + 1

    def test_sampling_does_not_shift_controls_earlier_than_signals(self):
        """The measurable symptom of the old defect: the mean bar position of
        controls sitting earlier than that of the signals they matched."""
        candles, pool = self._population()
        index = SessionIndex(candles)
        horizon = 12
        sampler = TimeMatchedSampler(candles, pool, horizon=horizon)
        rng = random.Random(7)

        signals, controls = [], []
        for s in index.eligible(horizon):
            pick = sampler.sample(s, "UP", rng)
            if pick is not None:
                signals.append(s)
                controls.append(pick)

        assert len(signals) > 40
        drift = statistics.fmean(signals) - statistics.fmean(controls)
        # Both populations are drawn under the same eligibility, so any residual
        # is the bucket-matching granularity, not a systematic pull.
        assert abs(drift) < 1.0, f"controls drift {drift:+.2f} bars from their signals"

    def test_feasibility_is_reported(self):
        candles, pool = self._population()
        sampler = TimeMatchedSampler(candles, pool, horizon=24)
        f = sampler.feasibility()
        assert f["population"] == BARS_PER_SESSION
        assert f["horizon_eligible"] == BARS_PER_SESSION - 24
        assert 60 < f["horizon_feasibility_pct"] < 75

    def test_stats_carry_population_figures(self):
        candles, pool = self._population()
        sampler = TimeMatchedSampler(candles, pool, horizon=6)
        stats = MatchStats()
        sampler.record_feasibility(stats)
        rng = random.Random(3)
        for s in range(0, 30, 5):
            sampler.sample(s, "UP", rng, stats)
        d = stats.as_dict()
        assert d["population"] == BARS_PER_SESSION
        assert d["horizon_feasibility_pct"] > 0

    def test_a_horizon_that_excludes_everything_yields_no_controls(self):
        candles, pool = self._population()
        sampler = TimeMatchedSampler(candles, pool, horizon=BARS_PER_SESSION + 5)
        assert sampler.feasibility()["horizon_eligible"] == 0
        assert sampler.sample(10, "UP", random.Random(1)) is None


class TestControlAEligibility:
    """Control A shares the signal's own bar, so it inherits eligibility exactly.

    Worth pinning rather than assuming: if Control A were ever built from a
    neighbouring bar it would acquire the same asymmetry Control C had.
    """

    def test_control_a_is_defined_exactly_when_the_signal_is(self):
        candles = session("2026-06-01")
        index = SessionIndex(candles)
        for i in (10, 40, BARS_PER_SESSION - 13, BARS_PER_SESSION - 12, BARS_PER_SESSION - 1):
            signal = forward_return_pct(candles, index, i, "BUY", 12)
            control = forward_return_pct(candles, index, i, "SELL", 12)
            assert (signal is None) == (control is None)

    def test_control_a_is_the_exact_negation(self):
        candles = session("2026-06-01")
        index = SessionIndex(candles)
        assert forward_return_pct(candles, index, 20, "BUY", 12) == -forward_return_pct(
            candles, index, 20, "SELL", 12
        )
