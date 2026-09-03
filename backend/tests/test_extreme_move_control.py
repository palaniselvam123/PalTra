"""Control C matching — causality and match integrity. Not H006.

The point of Control C is to remove the "price already sits near the session
extreme" confound, so the tests that matter are the ones proving the match is
built from information available at t and is not quietly re-selected after an
outcome is known.
"""
from __future__ import annotations

import datetime as dt
import pathlib
import random

import pytest

from app.research.extreme_move import atr_series_for, session_range_position
from app.research.extreme_move_control import (
    MATCH_HORIZON, POSITION_CALIPER, MatchStats, match_nearest_position,
    match_within_class, position_class, session_positions, tercile_cuts,
)
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BARS_PER_SESSION = 75


def _bars(spec, day="2026-06-01", volume=1000) -> list[OHLCV]:
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, o, h, l, c, volume) for i, (o, h, l, c) in enumerate(spec)]


def _session(day: str, bars: int = BARS_PER_SESSION, start=100.0, drift=0.01) -> list[OHLCV]:
    spec = []
    px = start
    for _ in range(bars):
        spec.append((px, px + 1.0, px - 1.0, px + drift))
        px += drift
    return _bars(spec, day)


class TestSessionPosition:
    def test_matches_the_single_bar_helper(self):
        c = _session("2026-06-01")
        index = SessionIndex(c)
        series = session_positions(c, index)
        for i in (0, 5, 20, 74):
            assert series[i] == pytest.approx(session_range_position(c, index, i))

    def test_uses_only_bars_up_to_t(self):
        c = _session("2026-06-01")
        index = SessionIndex(c)
        before = session_positions(c, index)[20]
        c[21] = OHLCV(c[21].ts, 1, 9_000, 0.5, 1, 1000)
        after = session_positions(c, SessionIndex(c))[20]
        assert before == after

    def test_resets_at_the_session_boundary(self):
        c = _session("2026-06-01") + _session("2026-06-02", start=500.0)
        index = SessionIndex(c)
        pos = session_positions(c, index)
        first_of_day2 = BARS_PER_SESSION
        # Day 2 opens far above day 1 but its own range is tiny, so its
        # position is computed inside day 2 only.
        assert 0.0 <= pos[first_of_day2] <= 1.0
        assert pos[first_of_day2 + 1] is not None

    def test_a_close_at_the_running_high_is_one(self):
        c = _bars([(100, 101, 99, 100), (100, 102, 99, 102)])
        index = SessionIndex(c)
        assert session_positions(c, index)[1] == pytest.approx(1.0)


class TestBinning:
    def test_tercile_cuts_split_a_uniform_distribution(self):
        cuts = tercile_cuts([i / 100 for i in range(100)], 3)
        assert len(cuts) == 2
        assert cuts[0] == pytest.approx(0.33, abs=0.02)
        assert cuts[1] == pytest.approx(0.66, abs=0.02)

    def test_class_is_stable_at_the_edges(self):
        cuts = (0.33, 0.66)
        assert position_class(0.10, cuts) == 0
        assert position_class(0.33, cuts) == 0
        assert position_class(0.50, cuts) == 1
        assert position_class(0.66, cuts) == 1
        assert position_class(0.90, cuts) == 2
        assert position_class(None, cuts) is None


class TestNearestMatching:
    def test_picks_the_closest_position_in_the_same_bucket(self):
        events = [(10, 0, 0.90)]
        pool = {0: [(3, 0.20), (4, 0.88), (5, 0.55)]}
        matches, stats = match_nearest_position(events, pool)
        assert matches == {10: 4}
        assert stats.exact == 1 and stats.skipped == 0

    def test_refuses_a_match_outside_the_caliper(self):
        events = [(10, 0, 0.95)]
        pool = {0: [(3, 0.20)]}
        matches, stats = match_nearest_position(events, pool, caliper=POSITION_CALIPER)
        assert matches == {}
        assert stats.skipped_caliper == 1
        assert stats.matched == 0

    def test_counts_an_empty_pool_separately_from_a_caliper_failure(self):
        matches, stats = match_nearest_position([(10, 0, 0.5)], {})
        assert matches == {} and stats.skipped_no_pool == 1
        assert stats.skipped_caliper == 0

    def test_widens_one_bucket_only_when_needed(self):
        events = [(10, 5, 0.50)]
        pool = {4: [(2, 0.52)], 7: [(9, 0.50)]}
        matches, stats = match_nearest_position(events, pool)
        assert matches == {10: 2}
        assert stats.widened == 1 and stats.exact == 0

    def test_sampling_is_without_replacement(self):
        events = [(10, 0, 0.50), (11, 0, 0.50)]
        pool = {0: [(3, 0.50)]}
        matches, stats = match_nearest_position(events, pool)
        assert len(matches) == 1
        assert stats.matched == 1 and stats.skipped == 1

    def test_matching_is_deterministic(self):
        events = [(10, 0, 0.50), (11, 0, 0.70)]
        pool = {0: [(3, 0.48), (4, 0.72), (5, 0.90)]}
        first, _ = match_nearest_position(events, pool)
        second, _ = match_nearest_position(events, pool)
        assert first == second == {10: 3, 11: 4}


class TestClassMatching:
    def test_control_comes_from_the_same_bucket_and_class(self):
        events = [(10, 2, 1)]
        pool = {(2, 1): [7, 8], (2, 0): [1], (3, 1): [20]}
        matches, stats = match_within_class(events, pool, random.Random(0))
        assert matches[10] in (7, 8)
        assert stats.exact == 1

    def test_no_candidate_in_the_cell_is_a_skip_not_a_substitution(self):
        matches, stats = match_within_class([(10, 2, 1)], {(2, 0): [1]}, random.Random(0))
        assert matches == {} and stats.skipped_no_pool == 1


class TestMatchStats:
    def test_rates_are_reported_not_hidden(self):
        s = MatchStats(exact=8, widened=2, skipped_no_pool=5, skipped_caliper=5)
        d = s.as_dict()
        assert d["events_offered"] == 20
        assert d["matched"] == 10
        assert d["match_rate_pct"] == pytest.approx(50.0)
        assert d["exact_share_pct"] == pytest.approx(80.0)


class TestNoHorizonDrift:
    def test_controls_are_filtered_before_matching_not_after(self):
        """Eligibility is applied to the pool that is offered to the matcher.

        If a late bar were offered and dropped afterwards, controls would skew
        earlier in the session than their events. Here the pool is pre-filtered
        at the longest horizon, so a late candidate can never be offered.
        """
        c = _session("2026-06-01")
        index = SessionIndex(c)
        pos = session_positions(c, index)
        last = BARS_PER_SESSION - 1
        eligible = [
            (i, pos[i]) for i in range(BARS_PER_SESSION)
            if index.is_forward_window_valid(i, MATCH_HORIZON)
        ]
        assert all(i + MATCH_HORIZON <= last for i, _ in eligible)
        assert max(i for i, _ in eligible) == last - MATCH_HORIZON
        matches, _ = match_nearest_position(
            [(20, 0, pos[20])], {0: eligible}
        )
        chosen = matches[20]
        assert index.is_forward_window_valid(chosen, MATCH_HORIZON)


class TestNoStrategyCreep:
    def test_modules_do_not_register_or_emit_a_side(self):
        for name in ("extreme_move_control.py", "extreme_move_control_evaluate.py"):
            src = pathlib.Path(f"app/research/{name}").read_text(encoding="utf-8")
            assert "registry.register" not in src
            assert "entry_long" not in src
            assert "from app.services.paper_engine" not in src
            assert "H006" not in src or "NOT CREATED" in src

    def test_evaluate_declares_the_holdout_unread_and_no_h006(self):
        src = pathlib.Path("app/research/extreme_move_control_evaluate.py").read_text(encoding="utf-8")
        assert '"hold_out": "NOT READ"' in src
        assert '"h006": "NOT CREATED"' in src
        assert "0.1831" in src

    def test_the_cut_is_not_searched(self):
        src = pathlib.Path("app/research/extreme_move_control_evaluate.py").read_text(encoding="utf-8")
        for banned in ("0.5 * ATR", "1.25", "1.5,", "2.0,"):
            assert banned not in src
        assert "NOT PERFORMED" in src
