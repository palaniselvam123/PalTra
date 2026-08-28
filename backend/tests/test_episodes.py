"""Episode grouping tests.

The grouping decides how much independent evidence a hypothesis actually has,
so the tests that matter are the ones proving it never merges events that are
genuinely distinct, and never splits one event into several.
"""
from __future__ import annotations

import datetime as dt

from app.research.episodes import (
    SignalPoint, episode_report, format_report, group_episodes,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def ts_at(day: str, bar: int) -> int:
    base = dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST)
    return int((base + dt.timedelta(minutes=5 * bar)).timestamp())


def pt(index: int, day: str = "2026-06-01", symbol: str = "AAA", side: str = "BUY") -> SignalPoint:
    return SignalPoint(symbol, index, ts_at(day, index), side)


class TestGrouping:
    def test_adjacent_signals_form_one_episode(self):
        eps = group_episodes([pt(10), pt(11), pt(12)], min_separation_bars=12)
        assert len(eps) == 1 and eps[0].size == 3

    def test_widely_separated_signals_are_separate_episodes(self):
        eps = group_episodes([pt(10), pt(40)], min_separation_bars=12)
        assert len(eps) == 2

    def test_separation_boundary_is_exclusive(self):
        """Exactly `min_separation_bars` apart means the windows no longer
        overlap, so it is a new episode."""
        assert len(group_episodes([pt(10), pt(22)], min_separation_bars=12)) == 2
        assert len(group_episodes([pt(10), pt(21)], min_separation_bars=12)) == 1

    def test_chaining_extends_an_episode(self):
        """Each gap is under the threshold, so all four belong together even
        though the first and last are 21 bars apart."""
        eps = group_episodes([pt(10), pt(17), pt(24), pt(31)], min_separation_bars=12)
        assert len(eps) == 1 and eps[0].size == 4 and eps[0].duration_bars == 21

    def test_unsorted_input_is_handled(self):
        eps = group_episodes([pt(31), pt(10), pt(24), pt(17)], min_separation_bars=12)
        assert len(eps) == 1 and eps[0].indices == [10, 17, 24, 31]


class TestKeySeparation:
    def test_different_symbols_never_merge(self):
        eps = group_episodes([pt(10, symbol="AAA"), pt(11, symbol="BBB")], min_separation_bars=12)
        assert len(eps) == 2

    def test_different_days_never_merge(self):
        eps = group_episodes([pt(10, day="2026-06-01"), pt(11, day="2026-06-02")], 12)
        assert len(eps) == 2

    def test_opposite_directions_never_merge(self):
        """A long and a short minutes apart are different events, not one
        continuing event."""
        eps = group_episodes([pt(10, side="BUY"), pt(11, side="SELL")], min_separation_bars=12)
        assert len(eps) == 2

    def test_same_key_across_two_days_gives_two_episodes(self):
        points = [pt(10, day="2026-06-01"), pt(12, day="2026-06-01"),
                  pt(10, day="2026-06-02"), pt(12, day="2026-06-02")]
        assert len(group_episodes(points, 12)) == 2


class TestDuration:
    def test_lone_signal_spans_zero_bars(self):
        assert group_episodes([pt(10)], 12)[0].duration_bars == 0

    def test_duration_is_first_to_last(self):
        assert group_episodes([pt(10), pt(15), pt(20)], 12)[0].duration_bars == 10


class TestReport:
    def test_reports_the_seven_required_measures(self):
        points = [pt(10), pt(11), pt(40), pt(10, day="2026-06-02")]
        r = episode_report(points, min_separation_bars=12)
        for key in ("signals", "signal_days", "signals_per_signal_day", "episodes",
                    "signals_per_episode", "median_episode_duration_bars",
                    "max_episode_duration_bars"):
            assert key in r

    def test_counts_are_correct(self):
        points = [pt(10), pt(11), pt(40), pt(10, day="2026-06-02")]
        r = episode_report(points, 12)
        assert r["signals"] == 4
        assert r["signal_days"] == 2
        assert r["episodes"] == 3                 # (10,11), (40), (day2 10)
        assert r["signals_per_episode"] == round(4 / 3, 2)
        assert r["max_episode_duration_bars"] == 1

    def test_singleton_share_is_reported(self):
        """Distinguishes 'a few big clusters' from 'uniformly small ones' — the
        mean alone cannot."""
        r = episode_report([pt(10), pt(11), pt(40), pt(70)], 12)
        assert r["episodes"] == 3
        assert r["singleton_episodes_pct"] == round(2 / 3 * 100, 1)

    def test_no_clustering_gives_one_signal_per_episode(self):
        r = episode_report([pt(0), pt(20), pt(40), pt(60)], 12)
        assert r["signals_per_episode"] == 1.0
        assert r["singleton_episodes_pct"] == 100.0

    def test_separation_is_recorded_with_the_result(self):
        """The number is meaningless without the rule that produced it."""
        assert episode_report([pt(10)], min_separation_bars=7)["min_separation_bars"] == 7

    def test_empty_input_does_not_crash(self):
        assert episode_report([])["signals"] == 0
        assert "no signals" in format_report("X", episode_report([]))

    def test_format_renders_all_seven(self):
        text = format_report("H999", episode_report([pt(10), pt(11), pt(40)], 12))
        for label in ("total signals", "signal-days", "signals per signal-day",
                      "distinct episodes", "signals per episode",
                      "median episode duration", "max episode duration"):
            assert label in text


class TestSeparationSensitivity:
    def test_a_larger_separation_merges_more(self):
        points = [pt(10), pt(20), pt(30)]
        assert episode_report(points, 5)["episodes"] == 3
        assert episode_report(points, 15)["episodes"] == 1
