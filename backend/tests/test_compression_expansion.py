"""H005 v1 generator and pre-registration tests.

Nothing here runs the forward experiment. These pin the causal definitions so
that when the test is eventually run, it runs the registered rule — and so a
later edit cannot drift the window, the ATR index, or the controls without a
test failing.
"""
from __future__ import annotations

import ast
import datetime as dt
import pathlib
import random
import statistics

import pytest

from app.research.compression_expansion import (
    ATR_PERIOD, C2_MAX, C3_MIN, HORIZONS, LOOKBACK, PARAMS,
    aligned_true_ranges, body_direction, compression_stage,
    compression_window_start, compression_without_expansion, control_a_index,
    control_c_sampler, generate, generate_detailed, measure,
)
from app.services.indicators import OHLCV, atr
from app.services.observation_window import SessionIndex, forward_return_pct

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BASE = int(dt.datetime(2026, 6, 1, 9, 15, tzinfo=IST).timestamp())
BARS_PER_SESSION = 75


def _bars(spec: list[tuple[float, float, float, float]], day="2026-06-01") -> list[OHLCV]:
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, o, h, l, c, 1000) for i, (o, h, l, c) in enumerate(spec)]


def _session(day: str, bars: int = BARS_PER_SESSION) -> list[OHLCV]:
    return _bars([(100.0, 101.0, 99.0, 100.0 + i * 0.01) for i in range(bars)], day)


def large() -> tuple[float, float, float, float]:
    """Wide bar, modest body up — inflates ATR."""
    return (100.0, 104.0, 96.0, 101.0)


def tiny() -> tuple[float, float, float, float]:
    """Tight bar, tiny body up — the compression state."""
    return (100.0, 100.04, 99.96, 100.02)


def expand_up() -> tuple[float, float, float, float]:
    return (100.0, 110.0, 99.5, 109.0)


def expand_down() -> tuple[float, float, float, float]:
    return (100.0, 100.5, 90.0, 91.0)


def expand_flat() -> tuple[float, float, float, float]:
    """Large range, close == open — excluded from directional events."""
    return (100.0, 110.0, 90.0, 100.0)


def compressed_then(transition: tuple[float, float, float, float],
                    extra: int = 8) -> list[OHLCV]:
    """ATR warmup on wide bars, then 12 tiny bars, then the transition.

    Layout: 0-19 large (ATR memory), 20-31 tiny (compression window for t=32),
    32 = transition. Tail continues from the transition close so a gap does
    not fabricate a second expansion bar.
    """
    spec = [large()] * 20 + [tiny()] * 12 + [transition]
    last = transition[3]
    spec += [(last, last + 0.04, last - 0.04, last + 0.02)] * extra
    return _bars(spec)


TRANSITION = 32    # first bar after the 12-bar tiny window


class TestWindowMembership:
    def test_c2_uses_only_bars_t_minus_12_through_t_minus_1(self):
        c = compressed_then(expand_up())
        t = TRANSITION
        m = measure(c, t)
        assert m is not None
        trs = aligned_true_ranges(c)
        expected_mean = sum(trs[i] for i in range(t - LOOKBACK, t)) / LOOKBACK
        assert m.mean_tr == pytest.approx(expected_mean)

        # A bar strictly before the window does not enter the mean-TR numerator.
        # (ATR is recursive, so C2 itself can still move; the window membership
        # claim is about which TRs are averaged.)
        outside = list(c)
        prev = c[t - LOOKBACK - 1]
        outside[t - LOOKBACK - 1] = OHLCV(prev.ts, prev.open, 200.0, 1.0, prev.close, 1000)
        assert measure(outside, t).mean_tr == pytest.approx(m.mean_tr)

    def test_c2_does_not_include_bar_t(self):
        c = compressed_then(expand_up())
        t = TRANSITION
        base = measure(c, t)
        mutated = list(c)
        mutated[t] = OHLCV(c[t].ts, 100.0, 500.0, 1.0, 400.0, 1000)
        after = measure(mutated, t)
        assert after is not None
        assert after.c2 == pytest.approx(base.c2)
        assert after.tr_t != base.tr_t          # C3's numerator DID change

    def test_c2_uses_atr_at_t_minus_1_not_t(self):
        c = compressed_then(expand_up())
        t = TRANSITION
        m = measure(c, t)
        atrs = atr(c, ATR_PERIOD)
        assert atrs[t] is not None and atrs[t - 1] is not None
        assert atrs[t] != atrs[t - 1], "expansion bar must move ATR, otherwise the test is vacuous"
        assert m.atr_prev == atrs[t - 1]
        assert m.c2 == pytest.approx(m.mean_tr / atrs[t - 1])
        assert m.c2 != pytest.approx(m.mean_tr / atrs[t])

    def test_c3_is_tr_t_over_preceding_window_mean(self):
        c = compressed_then(expand_up())
        t = TRANSITION
        m = measure(c, t)
        trs = aligned_true_ranges(c)
        mean_tr = sum(trs[i] for i in range(t - LOOKBACK, t)) / LOOKBACK
        assert m.c3 == pytest.approx(trs[t] / mean_tr)
        # Not TR/ATR — that would be a different hypothesis.
        atrs = atr(c, ATR_PERIOD)
        assert m.c3 != pytest.approx(trs[t] / atrs[t - 1])

    def test_compression_window_start_is_t_minus_lookback(self):
        c = compressed_then(expand_up())
        assert compression_window_start(c, TRANSITION) == TRANSITION - LOOKBACK


class TestSessionCrossing:
    def test_session_crossing_window_is_rejected_not_truncated(self):
        day1 = _bars([large()] * 20, "2026-06-01")
        day2 = _bars([tiny()] * 20, "2026-06-02")
        c = day1 + day2
        # t = 25 is bar 5 of day 2; t-12 = 13, which is still day 1.
        t = 25
        assert compression_window_start(c, t) is None
        assert measure(c, t) is None

    def test_first_lookback_bars_of_a_session_are_undefined(self):
        c = compressed_then(expand_up())
        for t in range(LOOKBACK):
            assert compression_window_start(c, t) is None
            assert measure(c, t) is None

    def test_a_same_session_window_is_accepted(self):
        c = compressed_then(expand_up())
        assert compression_window_start(c, TRANSITION) is not None
        assert measure(c, TRANSITION) is not None


class TestDirection:
    def test_flat_transition_bars_are_excluded(self):
        c = compressed_then(expand_flat())
        m = measure(c, TRANSITION)
        assert m is not None and m.direction is None
        assert generate("X", c) == []
        assert TRANSITION not in {e.index for e in generate_detailed("X", c)}

    def test_long_body_direction_is_causal(self):
        c = compressed_then(expand_up())
        m = measure(c, TRANSITION)
        assert m is not None and m.direction == 1
        assert body_direction(c[TRANSITION]) == 1
        sigs = generate("X", c)
        assert (TRANSITION, "BUY") in sigs

    def test_short_body_direction_is_causal(self):
        c = compressed_then(expand_down())
        m = measure(c, TRANSITION)
        assert m is not None and m.direction == -1
        sigs = generate("X", c)
        assert (TRANSITION, "SELL") in sigs

    def test_direction_uses_only_open_and_close_of_bar_t(self):
        c = compressed_then(expand_up())
        mutated = list(c)
        mutated[TRANSITION] = OHLCV(
            c[TRANSITION].ts,
            c[TRANSITION].open,
            999.0,              # wick does not flip a long body
            1.0,
            c[TRANSITION].close,
            1000,
        )
        assert body_direction(mutated[TRANSITION]) == body_direction(c[TRANSITION]) == 1

    def test_range_break_of_the_prior_window_is_not_the_trigger(self):
        """A close inside the prior 12-bar high/low can still be an event if
        the body expands. That is the point of not recreating H002."""
        c = compressed_then(expand_up())
        # Prior tiny bars live around 100; expand_up closes at 109, which does
        # break the box. Build a large-body bar that stays inside 99.96-100.04
        # — impossible with C3>=2. So instead: confirm generate never consults
        # the window high/low by mutating those extremes after the fact.
        t = TRANSITION
        before = generate("X", c)
        mutated = list(c)
        for i in range(t - LOOKBACK, t):
            mutated[i] = OHLCV(c[i].ts, 100.0, 10_000.0, 0.01, 100.02, 1000)
        # Window extremes exploded; if the rule were "close beyond the box"
        # the event at t would vanish. C2 will also change because TR grew —
        # so this only documents that generate() has no high/low-break predicate.
        src = pathlib.Path(__file__).resolve().parents[1] / "app" / "research" / "compression_expansion.py"
        text = src.read_text(encoding="utf-8")
        assert "range_high" not in text
        assert "window high" not in text.lower() or "not a range breakout" in text.lower()
        assert before  # the canonical up-expansion still fires


class TestNoLookAhead:
    def test_mutating_bars_after_t_does_not_change_the_event_at_t(self):
        c = compressed_then(expand_up(), extra=8)
        t = TRANSITION
        before = [e for e in generate_detailed("X", c) if e.index == t]
        mutated = list(c)
        for i in range(t + 1, len(c)):
            mutated[i] = OHLCV(c[i].ts, 999, 1000, 998, 999, 1)
        after = [e for e in generate_detailed("X", mutated) if e.index == t]
        assert before == after

    def test_truncating_the_future_never_changes_a_past_event(self):
        c = compressed_then(expand_up(), extra=8)
        full = generate("X", c)
        for t in range(TRANSITION, len(c)):
            truncated = generate("X", c[: t + 1])
            assert {s for s in truncated if s[0] <= t} == {s for s in full if s[0] <= t}

    def test_measure_does_not_read_atr_at_t_for_c2(self):
        """Pin the contamination path: ATR[t] includes TR[t]."""
        c = compressed_then(expand_up())
        t = TRANSITION
        m = measure(c, t)
        atrs = atr(c, ATR_PERIOD)
        assert m.atr_prev == atrs[t - 1] != atrs[t]


class TestForwardWindowEligibility:
    def test_eligibility_uses_the_shared_session_index(self):
        c = compressed_then(expand_up(), extra=40)
        # Pad to a full session so horizon 24 is meaningful.
        while len(c) < BARS_PER_SESSION:
            last = c[-1]
            c.append(OHLCV(last.ts + 300, 100, 100.04, 99.96, 100.02, 1000))
        index = SessionIndex(c)
        for h in HORIZONS:
            assert index.is_forward_window_valid(TRANSITION, h)
            assert not index.is_forward_window_valid(len(c) - 1, h)

    def test_a_window_may_not_cross_the_session(self):
        c = _session("2026-06-01") + _session("2026-06-02")
        index = SessionIndex(c)
        assert not index.is_forward_window_valid(BARS_PER_SESSION - 2, 6)


class TestControlA:
    def test_control_a_is_the_same_bar_as_the_signal(self):
        assert control_a_index(TRANSITION) == TRANSITION
        for i in (0, 17, 40, 74):
            assert control_a_index(i) == i

    def test_control_a_inherits_the_signals_eligibility_exactly(self):
        candles = _session("2026-06-01")
        index = SessionIndex(candles)
        for i in (10, 40, BARS_PER_SESSION - 13, BARS_PER_SESSION - 1):
            a_idx = control_a_index(i)
            assert a_idx == i
            for h in HORIZONS:
                signal_ok = index.is_forward_window_valid(i, h)
                control_ok = index.is_forward_window_valid(a_idx, h)
                assert signal_ok is control_ok
                # Same-bar random-side: both directions share eligibility.
                buy = forward_return_pct(candles, index, a_idx, "BUY", h)
                sell = forward_return_pct(candles, index, a_idx, "SELL", h)
                assert (buy is None) == (sell is None) == (not signal_ok)


class TestControlC:
    def test_population_is_constructed_before_sampling(self):
        c = compressed_then(expand_up(), extra=20)
        pool = compression_without_expansion(c)
        # The expansion bar itself is a signal, so it must NOT be in Control C.
        assert TRANSITION not in pool["BUY"] + pool["SELL"]
        sampler = control_c_sampler(c, horizon=6)
        # Sampler cells are a subset of the pre-built pool (plus horizon filter).
        pooled = pool["BUY"] + pool["SELL"]
        for idxs in sampler._cells.values():
            for i in idxs:
                assert i in pooled

    def test_control_c_excludes_the_expansion_trigger(self):
        c = compressed_then(expand_up())
        assert (TRANSITION, "BUY") in generate("X", c)
        pool = compression_without_expansion(c)
        assert TRANSITION not in pool["BUY"]
        # Compression-stage (no C3 filter) DOES contain the signal bar.
        stage = compression_stage(c)
        assert TRANSITION in stage["BUY"]

    def test_control_c_does_not_exhibit_post_selection_horizon_drift(self):
        """Sampling first and dropping afterwards would pull controls earlier.
        Building the population through TimeMatchedSampler forbids that.

        Series is wide bars then a long run of tiny bars and no expansion, so
        the Control C pool is large enough for the drift check to be meaningful.
        """
        spec = [large()] * 20 + [tiny()] * (BARS_PER_SESSION - 20)
        c = _bars(spec)
        horizon = 12
        sampler = control_c_sampler(c, horizon=horizon)
        index = SessionIndex(c)
        rng = random.Random(11)

        signals, controls = [], []
        for side, idxs in compression_without_expansion(c).items():
            for s in idxs:
                if not index.is_forward_window_valid(s, horizon):
                    continue
                pick = sampler.sample(s, side, rng)
                if pick is not None:
                    signals.append(s)
                    controls.append(pick)
                    assert index.is_forward_window_valid(pick, horizon)

        assert len(signals) >= 8
        drift = statistics.fmean(signals) - statistics.fmean(controls)
        assert abs(drift) < 2.0, f"controls drift {drift:+.2f} bars from their signals"

    def test_horizon_is_required_when_building_the_sampler(self):
        c = compressed_then(expand_up(), extra=20)
        sampler = control_c_sampler(c, horizon=24)
        assert sampler.horizon == 24
        f = sampler.feasibility()
        assert f["population"] >= f["horizon_eligible"]


class TestCanonicalEvent:
    def test_compressed_then_up_expansion_fires(self):
        c = compressed_then(expand_up())
        m = measure(c, TRANSITION)
        assert m is not None
        assert m.c2 <= C2_MAX
        assert m.c3 >= C3_MIN
        assert generate("X", c) == [(TRANSITION, "BUY")]

    def test_no_expansion_means_no_event(self):
        c = compressed_then(tiny())          # another tiny bar, not an expansion
        m = measure(c, TRANSITION)
        assert m is not None
        assert m.c3 < C3_MIN
        assert generate("X", c) == []


class TestRegisteredParametersMatchImplementation:
    def test_module_constants_are_the_frozen_values(self):
        assert LOOKBACK == 12
        assert ATR_PERIOD == 14
        assert C2_MAX == 0.794
        assert C3_MIN == 2.0
        assert HORIZONS == (6, 12, 24)
        assert PARAMS.lookback == LOOKBACK
        assert PARAMS.atr_period == ATR_PERIOD
        assert PARAMS.c2_max == C2_MAX
        assert PARAMS.c3_min == C3_MIN

    def test_seeded_rule_matches_the_module(self, tmp_path, monkeypatch):
        from app.research.hypothesis_registry import HypothesisRegistry
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        h = r.get("H005", "v1")
        assert h is not None
        assert h.status == "DEFINED"
        rule = h.rule_definition
        assert rule["lookback_bars"] == LOOKBACK
        assert rule["atr_period"] == ATR_PERIOD
        assert rule["c2_max"] == C2_MAX
        assert rule["c3_min"] == C3_MIN
        assert rule["horizons_bars"] == list(HORIZONS)
        assert rule["implementation"] == "app/research/compression_expansion.py"

    def test_h005_cannot_be_overwritten_after_results_exist(self, tmp_path, monkeypatch):
        from app.research.hypothesis_registry import HypothesisRegistry
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        r.record_result("H005", "v1", "research_5m_v1", {"verdict": "placeholder"}, "REJECTED")
        seed_mod.seed()
        got = r.get("H005", "v1")
        assert got.status == "REJECTED"
        assert got.result_summary == {"verdict": "placeholder"}

    def test_seeding_h005_does_not_change_h001_through_h004(self, tmp_path, monkeypatch):
        from app.research.hypothesis_registry import Hypothesis, HypothesisRegistry
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        for hid, status, summary in (
            ("H001", "REJECTED", {"locked": True}),
            ("H002", "REJECTED", {"locked": True}),
            ("H003", "REJECTED", {"locked": True}),
            ("H004", "REJECTED", {"locked": True}),
        ):
            existing = r.get(hid, "v1")
            existing.status = status
            existing.result_summary = summary
            r.register(existing)

        seed_mod.seed()
        for hid in ("H001", "H002", "H003", "H004"):
            got = r.get(hid, "v1")
            assert got.status == "REJECTED"
            assert got.result_summary == {"locked": True}
        assert r.get("H005", "v1").status == "DEFINED"


class TestIsolation:
    def test_generator_touches_no_live_module(self):
        src = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app" / "research" / "compression_expansion.py"
        ).read_text(encoding="utf-8")
        for banned in (
            "candle_store", "strategy_runner", "scanner_worker", "paper_engine",
            "market_data", "execution",
        ):
            assert f"import {banned}" not in src and f"services.{banned}" not in src

    def test_no_indicator_filters_were_added(self):
        src = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app" / "research" / "compression_expansion.py"
        ).read_text(encoding="utf-8")
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
        assert "atr" in imported

    def test_module_computes_no_forward_outcome(self):
        src = (
            pathlib.Path(__file__).resolve().parents[1]
            / "app" / "research" / "compression_expansion.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(src)
        called = {
            node.func.id if isinstance(node.func, ast.Name) else node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
        }
        assert "forward_return_pct" not in called
        assert "observe" not in called
        assert "permutation_p" not in called
        # The word appears in comments/doc; the functions must not be called.
        assert "mfe" not in called and "mae" not in called
