"""Causal ordered impulse: ordering, session boundaries, and non-repainting.

The non-repainting tests are the important ones. A structure that looks
different in hindsight than it did at the time produces research results that
cannot be reproduced live, and the failure is silent — the numbers still come
out, they are just describing a rule nobody could have traded.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.research.impulse_structure import (
    Impulse, find_impulse, pullback_bar_count, window_start,
)
from app.services.indicators import OHLCV

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def bars(day: str, spec: list[tuple[float, float, float, float]], start="09:15") -> list[OHLCV]:
    h, m = (int(x) for x in start.split(":"))
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, o, hi, lo, c, 1000) for i, (o, hi, lo, c) in enumerate(spec)]


def flat(n: int, price: float = 100.0):
    return [(price, price + 0.2, price - 0.2, price)] * n


def rising(n: int, start: float = 100.0, step: float = 1.0):
    return [(start + i * step, start + i * step + 0.5, start + i * step - 0.5, start + i * step + step)
            for i in range(n)]


def falling(n: int, start: float = 120.0, step: float = 1.0):
    return [(start - i * step, start - i * step + 0.5, start - i * step - 0.5, start - i * step - step)
            for i in range(n)]


class TestOrdering:
    def test_low_is_always_before_high_for_an_up_impulse(self):
        """The bug this guards: an independently-found low can fall AFTER the
        high, making the impulse range measure the pullback instead."""
        # rise, then a deep fall whose low is the window minimum and is LATE
        spec = rising(8, 100.0) + [(108, 108.2, 90.0, 91.0)] + flat(4, 91.0)
        c = bars("2026-06-01", spec)
        imp = find_impulse(c, len(c) - 1, lookback=12, direction="UP")
        if imp is not None:
            assert imp.low_idx < imp.high_idx
            # the late crash low must not have been used as the impulse low
            assert imp.impulse_low > 90.0

    def test_high_is_always_before_low_for_a_down_impulse(self):
        spec = falling(8, 120.0) + [(112, 130.0, 111.8, 129.0)] + flat(4, 129.0)
        c = bars("2026-06-01", spec)
        imp = find_impulse(c, len(c) - 1, lookback=12, direction="DOWN")
        if imp is not None:
            assert imp.high_idx < imp.low_idx
            assert imp.impulse_high < 130.0

    def test_clean_rise_is_detected_with_correct_endpoints(self):
        c = bars("2026-06-01", rising(14, 100.0))
        imp = find_impulse(c, 13, lookback=12, direction="UP")
        assert imp is not None
        assert imp.low_idx == 1 and imp.high_idx == 12   # window is [1, 12]
        assert imp.bars == 11
        assert imp.range > 0

    def test_signal_bar_is_excluded_from_the_impulse(self):
        """A completed impulse must already be behind us; if bar t itself were
        eligible, the 'impulse high' could be the trigger bar."""
        c = bars("2026-06-01", rising(14, 100.0))
        imp = find_impulse(c, 13, lookback=12, direction="UP")
        assert imp is not None and imp.high_idx <= 12

    def test_flat_market_yields_no_ordered_impulse(self):
        c = bars("2026-06-01", flat(14))
        # every bar identical: earliest-tie rules put both extremes at the start
        assert find_impulse(c, 13, lookback=12, direction="UP") is None

    def test_range_and_bars_are_consistent(self):
        c = bars("2026-06-01", rising(14, 100.0))
        imp = find_impulse(c, 13, lookback=12, direction="UP")
        assert imp.range == pytest.approx(imp.impulse_high - imp.impulse_low)
        assert imp.bars == imp.end_idx - imp.start_idx


class TestSessionBoundary:
    def test_window_may_not_cross_into_the_previous_session(self):
        """An overnight gap is not an intraday impulse."""
        day1 = bars("2026-06-01", rising(10, 100.0))
        day2 = bars("2026-06-02", rising(6, 200.0))
        c = day1 + day2
        # bar 12 is the 3rd bar of day 2; a 12-bar lookback would reach day 1
        assert window_start(c, 12, 12) is None
        assert find_impulse(c, 12, lookback=12, direction="UP") is None

    def test_full_lookback_inside_one_session_is_evaluated(self):
        c = bars("2026-06-01", rising(20, 100.0))
        assert window_start(c, 15, 12) == 3
        assert find_impulse(c, 15, lookback=12, direction="UP") is not None

    def test_insufficient_history_returns_none(self):
        c = bars("2026-06-01", rising(5, 100.0))
        assert find_impulse(c, 3, lookback=12, direction="UP") is None


class TestNonRepainting:
    """A structure decided at bar t must never change when later bars arrive."""

    @staticmethod
    def path() -> list[OHLCV]:
        spec = (rising(6, 100.0) + falling(4, 106.0, 0.7) + rising(5, 103.0)
                + falling(3, 108.0, 1.2) + rising(6, 104.0))
        return bars("2026-06-01", spec)

    def test_truncating_the_future_does_not_change_the_structure(self):
        """The decisive test: evaluating bar t with every later bar deleted must
        give an identical answer."""
        c = self.path()
        for direction in ("UP", "DOWN"):
            for t in range(13, len(c)):
                full = find_impulse(c, t, 12, direction)
                truncated = find_impulse(c[: t + 1], t, 12, direction)
                assert full == truncated, f"repaint at bar {t} ({direction})"

    def test_appending_future_bars_does_not_change_an_earlier_structure(self):
        """Same guarantee from the other side: extend the series and re-ask."""
        c = self.path()
        t = 15
        before = find_impulse(c[: t + 1], t, 12, "UP")
        after = find_impulse(c + bars("2026-06-01", rising(10, 200.0)), t, 12, "UP")
        assert before == after

    def test_structure_is_allowed_to_differ_between_different_bars(self):
        """Non-repainting means bar t's answer is stable, NOT that every bar
        gives the same answer. Without this the test above could pass on a
        function that always returned None."""
        c = self.path()
        seen = {find_impulse(c, t, 12, "UP") for t in range(13, len(c))}
        assert len(seen) > 1

    def test_deterministic_across_repeated_calls(self):
        c = self.path()
        assert find_impulse(c, 20, 12, "UP") == find_impulse(c, 20, 12, "UP")


class TestPullbackTiming:
    def test_trigger_bar_is_not_counted_as_a_pullback_bar(self):
        """Pullback occupies [end_idx+1, t-1]; bar t is the trigger."""
        imp = Impulse("UP", low_idx=2, high_idx=8, impulse_low=100.0, impulse_high=110.0)
        assert pullback_bar_count(imp, 10) == 1   # only bar 9
        assert pullback_bar_count(imp, 11) == 2   # bars 9, 10

    def test_a_trigger_immediately_after_the_high_has_no_pullback(self):
        imp = Impulse("UP", low_idx=2, high_idx=8, impulse_low=100.0, impulse_high=110.0)
        assert pullback_bar_count(imp, 9) == 0    # invalid: needs >= 1

    def test_mirrors_for_a_short(self):
        imp = Impulse("DOWN", low_idx=8, high_idx=2, impulse_low=100.0, impulse_high=110.0)
        assert imp.end_idx == 8
        assert pullback_bar_count(imp, 10) == 1
