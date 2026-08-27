"""H003 v1 generator tests.

Built around one canonical setup whose every quantity is known by construction,
so a failure names the stage that broke rather than just "no signal".

Canonical LONG (bar indices):
    0-8    flat at 100, warming up ATR
    9      dips to low 90            -> impulse low
    10-14  rises to high 110         -> impulse high (impulse_bars = 5, R = 20)
    15-16  pullback                  -> completed pullback bars
    17     trigger                   -> pullback_bars = 2
"""
from __future__ import annotations

import datetime as dt

import pytest

from app.research.pullback_hypothesis import (
    VARIANT_A, VARIANT_B, PullbackParams, generate, generate_detailed, stage_a_bars,
)
from app.services.indicators import OHLCV

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = int(dt.datetime(2026, 6, 1, 9, 15, tzinfo=IST).timestamp())

IMPULSE_LOW, IMPULSE_HIGH = 90.0, 110.0
R = IMPULSE_HIGH - IMPULSE_LOW          # 20.0
HIGH_IDX, TRIGGER = 14, 17


def _bars(spec: list[tuple[float, float, float, float]]) -> list[OHLCV]:
    return [OHLCV(BASE + i * 300, o, h, l, c, 1000) for i, (o, h, l, c) in enumerate(spec)]


def long_setup(pullback_low: float, pb_bars: int = 2, trigger: bool = True) -> list[OHLCV]:
    """Canonical long structure with a caller-chosen retracement depth."""
    spec: list[tuple[float, float, float, float]] = [(100, 100.5, 99.5, 100)] * 9
    spec.append((100, 100.2, IMPULSE_LOW, 91))                      # 9: impulse low
    for i in range(4):                                              # 10-13: advance
        p = 91 + (i + 1) * 4
        spec.append((p - 3, p + 0.5, p - 3.5, p))
    spec.append((107, IMPULSE_HIGH, 106.5, 109))                    # 14: impulse high

    # Pullback bars, descending to `pullback_low` on the last one.
    for i in range(pb_bars):
        last = i == pb_bars - 1
        lo = pullback_low if last else pullback_low + 1.5
        spec.append((lo + 1.5, lo + 2.0, lo, lo + 0.5))

    prev_high = spec[-1][1]
    close = prev_high + 1.0 if trigger else prev_high - 0.5
    spec.append((prev_high, close + 0.3, prev_high - 0.5, close))    # trigger bar
    return _bars(spec)


def short_setup(pullback_high: float, pb_bars: int = 2, trigger: bool = True) -> list[OHLCV]:
    """Exact mirror: impulse high first, then the low, then a pullback upward."""
    spec: list[tuple[float, float, float, float]] = [(100, 100.5, 99.5, 100)] * 9
    spec.append((100, IMPULSE_HIGH, 99.8, 109))                     # 9: impulse high
    for i in range(4):                                              # 10-13: decline
        p = 109 - (i + 1) * 4
        spec.append((p + 3, p + 3.5, p - 0.5, p))
    spec.append((93, 93.5, IMPULSE_LOW, 91))                        # 14: impulse low

    for i in range(pb_bars):
        last = i == pb_bars - 1
        hi = pullback_high if last else pullback_high - 1.5
        spec.append((hi - 1.5, hi, hi - 2.0, hi - 0.5))

    prev_low = spec[-1][2]
    close = prev_low - 1.0 if trigger else prev_low + 0.5
    spec.append((prev_low, prev_low + 0.5, close - 0.3, close))
    return _bars(spec)


def depth_to_pullback_low(retrace: float) -> float:
    return IMPULSE_HIGH - retrace * R


def depth_to_pullback_high(retrace: float) -> float:
    return IMPULSE_LOW + retrace * R


class TestLongStructure:
    def test_canonical_shallow_setup_fires_variant_a(self):
        c = long_setup(depth_to_pullback_low(0.30))
        sigs = generate("X", c, VARIANT_A)
        assert sigs == [(TRIGGER, "BUY")]

    def test_anatomy_matches_the_construction(self):
        rec = generate_detailed("X", long_setup(depth_to_pullback_low(0.30)), VARIANT_A)[0]
        assert rec.impulse_bars == HIGH_IDX - 9      # 5
        assert rec.pullback_bars == 2
        assert rec.retrace == pytest.approx(0.30, abs=0.01)
        assert rec.impulse_score >= VARIANT_A.k

    def test_no_trigger_means_no_signal(self):
        c = long_setup(depth_to_pullback_low(0.30), trigger=False)
        assert generate("X", c, VARIANT_A) == []

    def test_impulse_below_k_is_rejected(self):
        """Stage A must actually bind."""
        strict = PullbackParams("A", 0.20, 0.40, k=99.0)
        c = long_setup(depth_to_pullback_low(0.30))
        assert generate("X", c, strict) == []


class TestShortStructure:
    def test_canonical_shallow_setup_fires_variant_a(self):
        c = short_setup(depth_to_pullback_high(0.30))
        assert generate("X", c, VARIANT_A) == [(TRIGGER, "SELL")]

    def test_short_anatomy_mirrors_long(self):
        rec = generate_detailed("X", short_setup(depth_to_pullback_high(0.30)), VARIANT_A)[0]
        assert rec.side == "SELL"
        assert rec.impulse_bars == 5 and rec.pullback_bars == 2
        assert rec.retrace == pytest.approx(0.30, abs=0.01)

    def test_short_needs_a_close_below_the_previous_low(self):
        c = short_setup(depth_to_pullback_high(0.30), trigger=False)
        assert generate("X", c, VARIANT_A) == []


class TestVariantSeparation:
    def test_shallow_depth_fires_a_and_not_b(self):
        c = long_setup(depth_to_pullback_low(0.30))
        assert generate("X", c, VARIANT_A) and not generate("X", c, VARIANT_B)

    def test_medium_depth_fires_b_and_not_a(self):
        c = long_setup(depth_to_pullback_low(0.50))
        assert generate("X", c, VARIANT_B) and not generate("X", c, VARIANT_A)

    def test_no_depth_is_classified_into_both_variants(self):
        """The bands share the value 0.40; exactly one variant may claim it."""
        for depth in (0.20, 0.25, 0.30, 0.35, 0.3999, 0.40, 0.45, 0.55, 0.65):
            c = long_setup(depth_to_pullback_low(depth))
            in_a = bool(generate("X", c, VARIANT_A))
            in_b = bool(generate("X", c, VARIANT_B))
            assert not (in_a and in_b), f"depth {depth} claimed by both variants"

    def test_variants_are_labelled_on_the_record(self):
        a = generate_detailed("X", long_setup(depth_to_pullback_low(0.30)), VARIANT_A)[0]
        b = generate_detailed("X", long_setup(depth_to_pullback_low(0.50)), VARIANT_B)[0]
        assert a.variant == "A" and b.variant == "B"


class TestDepthBoundaries:
    @pytest.mark.parametrize(
        "depth,in_a,in_b",
        [
            (0.18, False, False),   # below both bands
            (0.20, True, False),    # A lower edge, inclusive
            (0.39, True, False),
            (0.40, False, True),    # shared edge resolves to B
            (0.64, False, True),
            (0.65, False, True),    # B upper edge, inclusive
            (0.70, False, False),   # above both bands
        ],
    )
    def test_boundary_classification(self, depth, in_a, in_b):
        c = long_setup(depth_to_pullback_low(depth))
        assert bool(generate("X", c, VARIANT_A)) is in_a
        assert bool(generate("X", c, VARIANT_B)) is in_b


class TestPullbackDuration:
    def test_single_pullback_bar_is_allowed(self):
        c = long_setup(depth_to_pullback_low(0.30), pb_bars=1)
        rec = generate_detailed("X", c, VARIANT_A)
        assert rec and rec[0].pullback_bars == 1

    def test_pullback_longer_than_the_impulse_is_rejected(self):
        """impulse_bars is 5, so a 6-bar pullback must not qualify: a pause that
        outlasts the move is no longer a pullback."""
        c = long_setup(depth_to_pullback_low(0.30), pb_bars=6)
        recs = generate_detailed("X", c, VARIANT_A)
        assert all(r.pullback_bars <= r.impulse_bars for r in recs)
        assert not any(r.index == len(c) - 1 for r in recs)

    def test_zero_pullback_bars_cannot_produce_a_signal(self):
        """A trigger on the bar straight after the impulse high has no
        completed pullback to measure."""
        c = long_setup(depth_to_pullback_low(0.30), pb_bars=0)
        assert generate("X", c, VARIANT_A) == []


class TestRetraceUsesCompletedBarsOnly:
    def test_trigger_bar_low_does_not_affect_depth(self):
        """Bar t contributes the trigger and nothing else. Driving its low far
        below the pullback must not change the measured retracement."""
        c = long_setup(depth_to_pullback_low(0.30))
        base = generate_detailed("X", c, VARIANT_A)[0]

        t = TRIGGER
        deep = list(c)
        deep[t] = OHLCV(c[t].ts, c[t].open, c[t].high, IMPULSE_LOW + 0.5, c[t].close, c[t].volume)
        after = generate_detailed("X", deep, VARIANT_A)[0]

        assert after.retrace == base.retrace

    def test_structural_check_uses_the_pullback_not_the_trigger(self):
        """A pullback breaking below the impulse low is rejected even though the
        depth band alone would not catch it."""
        c = long_setup(IMPULSE_LOW - 1.0)
        assert generate("X", c, VARIANT_A) == []
        assert generate("X", c, VARIANT_B) == []


class TestNoLookAheadAndNonRepainting:
    @staticmethod
    def _series() -> list[OHLCV]:
        c = long_setup(depth_to_pullback_low(0.30))
        tail = [(112, 113.0, 111.0, 112.5), (112, 118.0, 111.5, 117.0),
                (117, 117.5, 108.0, 109.0), (109, 115.0, 108.5, 114.0)]
        return c + _bars(tail)[: len(tail)]

    def test_truncating_the_future_never_changes_a_signal(self):
        c = self._series()
        for variant in (VARIANT_A, VARIANT_B):
            full = set(generate("X", c, variant))
            for t in range(15, len(c)):
                truncated = set(generate("X", c[: t + 1], variant))
                # every signal at or before t must agree with the full-series view
                assert {s for s in truncated if s[0] <= t} == {s for s in full if s[0] <= t}, (
                    f"repaint at bar {t}, variant {variant.variant}"
                )

    def test_appending_bars_does_not_retract_an_earlier_signal(self):
        c = long_setup(depth_to_pullback_low(0.30))
        before = generate("X", c, VARIANT_A)
        after = generate("X", c + _bars([(112, 130.0, 111.0, 129.0)] * 3), VARIANT_A)
        assert before == [s for s in after if s[0] <= TRIGGER]

    def test_signal_bar_close_is_used_but_no_later_bar_is(self):
        """Changing bars strictly after t must leave t's signal untouched."""
        c = long_setup(depth_to_pullback_low(0.30))
        extended = c + _bars([(112, 113.0, 111.0, 112.5)])
        mutated = list(extended)
        mutated[-1] = OHLCV(extended[-1].ts, 999, 1000, 998, 999, 1)
        assert (
            [s for s in generate("X", extended, VARIANT_A) if s[0] == TRIGGER]
            == [s for s in generate("X", mutated, VARIANT_A) if s[0] == TRIGGER]
        )


class TestStageAPopulation:
    def test_stage_a_is_a_superset_of_signals(self):
        """Control C samples from stage-A bars, so every signal must be in it —
        otherwise the control is not holding the impulse condition constant."""
        c = long_setup(depth_to_pullback_low(0.30))
        pool = set(stage_a_bars(c, "UP", VARIANT_A))
        assert {i for i, _ in generate("X", c, VARIANT_A)} <= pool

    def test_stage_a_ignores_pullback_and_trigger(self):
        """Removing the trigger kills the signal but not stage-A membership."""
        no_trigger = long_setup(depth_to_pullback_low(0.30), trigger=False)
        assert generate("X", no_trigger, VARIANT_A) == []
        assert TRIGGER in set(stage_a_bars(no_trigger, "UP", VARIANT_A))

    def test_stage_a_respects_k(self):
        c = long_setup(depth_to_pullback_low(0.30))
        strict = PullbackParams("A", 0.20, 0.40, k=99.0)
        assert stage_a_bars(c, "UP", strict) == []


class TestIsolation:
    def test_generator_touches_no_live_module(self):
        import pathlib

        src = (pathlib.Path(__file__).resolve().parents[1]
               / "app" / "research" / "pullback_hypothesis.py").read_text(encoding="utf-8")
        for banned in ("candle_store", "strategy_runner", "scanner_worker", "paper_engine",
                       "market_data", "execution"):
            assert f"import {banned}" not in src and f"services.{banned}" not in src

    def test_no_indicator_filters_were_added(self):
        """The rule is structural. ATR normalises the impulse; no other
        indicator may be called.

        Checked against the parsed syntax tree rather than the file text, so
        the module's own prose about which indicators it avoids cannot trip it.
        """
        import ast
        import pathlib

        src = (pathlib.Path(__file__).resolve().parents[1]
               / "app" / "research" / "pullback_hypothesis.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        imported = {
            alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) for alias in node.names
        }

        banned = {"rsi", "macd", "adx", "supertrend", "vwap", "bollinger", "ema", "sma"}
        assert not (called & banned), f"indicator called: {called & banned}"
        assert not (imported & banned), f"indicator imported: {imported & banned}"
        assert "atr" in imported, "ATR is the one permitted indicator and should be present"
