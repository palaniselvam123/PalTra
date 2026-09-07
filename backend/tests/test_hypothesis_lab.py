"""Tests for the hypothesis harness.

The harness decides whether a strategy earns a place in the app, so a bug here
does not produce a wrong number on a screen — it produces a wrong verdict, and
the wrong strategy gets promoted. These pin the parts that decide that.
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.core.market_clock import IST
from app.services.entry_diagnostics import observe
from app.services.hypothesis_lab import Stats, cost_floor_pct, permutation_p
from app.services.indicators import OHLCV
from app.services.orb_hypothesis import ORBParams, generate


def session(day: str, prices: list[tuple[float, float, float, float]], start="09:15") -> list[OHLCV]:
    """Build 5-minute bars for one trading day from (o,h,l,c) tuples."""
    h, m = (int(x) for x in start.split(":"))
    base = IST.localize(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=h, minute=m)) \
        if hasattr(IST, "localize") else \
        dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=IST)
    return [
        OHLCV(int((base + dt.timedelta(minutes=5 * i)).timestamp()), o, hi, lo, c, 1000)
        for i, (o, hi, lo, c) in enumerate(prices)
    ]


FLAT = (100.0, 100.5, 99.5, 100.0)


class TestORBGeneration:
    def test_breakout_above_range_is_a_long(self):
        # 3 bars build the 09:15-09:30 range (high 100.5), then a close above it
        bars = session("2026-08-18", [FLAT, FLAT, FLAT, (100.0, 101.5, 100.0, 101.2)] + [FLAT] * 20)
        sig = generate("X", bars)
        assert sig and sig[0] == (3, "BUY")

    def test_breakdown_below_range_is_a_short(self):
        bars = session("2026-08-18", [FLAT, FLAT, FLAT, (100.0, 100.0, 98.5, 98.8)] + [FLAT] * 20)
        sig = generate("X", bars)
        assert sig and sig[0] == (3, "SELL")

    def test_price_inside_the_range_produces_nothing(self):
        bars = session("2026-08-18", [FLAT] * 24)
        assert generate("X", bars) == []

    def test_first_breakout_only_stops_after_one(self):
        breakout = (100.0, 101.5, 100.0, 101.2)
        bars = session("2026-08-18", [FLAT, FLAT, FLAT] + [breakout] * 10)
        assert len(generate("X", bars, ORBParams(first_breakout_only=True))) == 1
        assert len(generate("X", bars, ORBParams(first_breakout_only=False))) > 1

    def test_no_entries_after_the_cutoff(self):
        """A breakout at 15:05 has no session left to resolve in, so taking it
        would measure the close, not the strategy."""
        bars = session("2026-08-18", [FLAT] * 3 + [(100.0, 101.5, 100.0, 101.2)] * 70)
        for i, _ in generate("X", bars, ORBParams(first_breakout_only=False)):
            t = dt.datetime.fromtimestamp(bars[i].ts, tz=dt.timezone.utc).astimezone(IST).time()
            assert t <= dt.time(14, 45)

    def test_range_is_measured_per_day_not_across_days(self):
        """Yesterday's range must not gate today's breakout."""
        d1 = session("2026-08-18", [FLAT] * 24)
        d2 = session("2026-08-19", [(200.0, 200.5, 199.5, 200.0)] * 3 + [(200.0, 202.0, 200.0, 201.5)] + [FLAT] * 5)
        sig = generate("X", d1 + d2)
        assert sig == [(27, "BUY")], "day 2 breakout should be found against day 2's own range"

    def test_partial_session_is_skipped(self):
        """Fewer than two bars in the opening window means there is no range."""
        bars = session("2026-08-18", [FLAT] * 10, start="11:00")
        assert generate("X", bars) == []


class TestCostFloor:
    def test_is_a_positive_percentage(self):
        assert 0.1 < cost_floor_pct(1000.0) < 1.0

    def test_floor_is_near_price_independent_at_fixed_position_value(self):
        """Every charge is either turnover-based or capped, and the position
        value is held constant, so a Rs 50 stock and a Rs 3000 stock face
        essentially the same percentage floor. Worth pinning because it is the
        opposite of the intuition that cheap stocks are cheaper to trade."""
        cheap, dear = cost_floor_pct(50.0), cost_floor_pct(3000.0)
        assert abs(cheap - dear) < 0.005

    def test_floor_rises_as_position_value_falls(self):
        """The brokerage cap stops binding on small positions, so a Rs 10,000
        trade carries a heavier percentage cost than a Rs 1,00,000 one."""
        assert cost_floor_pct(1000.0, position_value=10_000) > cost_floor_pct(1000.0, position_value=100_000)

    def test_always_exceeds_slippage_alone(self):
        assert cost_floor_pct(1000.0) > 0.10


class TestPermutation:
    def _obs(self, mfe: float, mae: float, n: int) -> list:
        bars = session("2026-08-18", [FLAT] * 40)
        o = observe("X", bars, 10, "BUY", 6)
        assert o is not None
        out = []
        for _ in range(n):
            import copy

            c = copy.deepcopy(o)
            c.forward.mfe_pct, c.forward.mae_pct = mfe, mae
            out.append(c)
        return out

    def test_identical_groups_are_not_significant(self):
        a, b = self._obs(1.0, 1.0, 30), self._obs(1.0, 1.0, 30)
        assert permutation_p(a, b, iterations=500) > 0.5

    def test_clearly_different_groups_are_significant(self):
        a, b = self._obs(3.0, 1.0, 30), self._obs(1.0, 3.0, 30)
        assert permutation_p(a, b, iterations=500) < 0.05

    def test_empty_group_is_not_significant(self):
        assert permutation_p([], self._obs(1.0, 1.0, 5), iterations=100) == 1.0


class TestStats:
    def test_empty_returns_none_rather_than_dividing_by_zero(self):
        assert Stats.of([]) is None
