"""Descriptive tradeability measurements — not a strategy.

These pin the cost identity, causal availability of regime inputs, session
eligibility, and the coverage-ratio definition so a later edit cannot quietly
turn this layer into a second cost model or a BUY/SELL rule.
"""
from __future__ import annotations

import ast
import datetime as dt
import pathlib

import pytest

from app.research.tradeability import (
    DEV_END, DEV_START, GATE_HIGH_CUT, GATE_PRIMARY_HORIZON, HORIZONS,
    LARGE_MOVE_ATR, LOOKBACK_BARS, MKT_REL_ATR_P25, MKT_REL_ATR_P75, VAL_END,
    assert_development_window, atr_series_for, bar_move_in_atr,
    classify_vol_regime, comparable_day_effects, contemporaneous_return_pct,
    cost_constants, cost_coverage_ratio, cost_floor_pct, coverage_hits,
    forward_window_ok, gap_over_atr, gate_label, is_large_move, rel_atr_at,
    round_trip_cost_audit, session_range_over_atr, shuffle_labels_within_buckets,
    unsigned_window_metrics,
)
from app.services.hypothesis_lab import SLIPPAGE_ROUND_TRIP_PCT
from app.services.hypothesis_lab import cost_floor_pct as lab_cost_floor
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex
from app.services.paper_engine import estimate_charges
from app.services.volume_contract import OK, UNKNOWN

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BARS_PER_SESSION = 75


def _bars(spec: list[tuple[float, float, float, float]], day="2026-06-01", volume=1000) -> list[OHLCV]:
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, o, h, l, c, volume) for i, (o, h, l, c) in enumerate(spec)]


def _session(day: str, bars: int = BARS_PER_SESSION, start=100.0) -> list[OHLCV]:
    spec = []
    px = start
    for i in range(bars):
        spec.append((px, px + 1.0, px - 1.0, px + 0.01))
        px += 0.01
    return _bars(spec, day)


class TestCostIdentity:
    def test_floor_is_the_lab_function_not_a_second_model(self):
        for p in (50.0, 200.0, 1136.0, 5000.0, 8000.0):
            assert cost_floor_pct(p) == lab_cost_floor(p)

    def test_floor_equals_estimate_charges_plus_round_trip_slippage(self):
        price, value = 1136.0, 100_000.0
        qty = max(int(value / price), 1)
        charges = estimate_charges("BUY", price, qty) + estimate_charges("SELL", price, qty)
        expected = charges / (price * qty) * 100 + SLIPPAGE_ROUND_TRIP_PCT
        assert abs(cost_floor_pct(price) - expected) < 1e-12

    def test_canonical_floor_near_0_183_at_typical_price(self):
        audit = round_trip_cost_audit(1136.0)
        assert abs(audit["total_floor_pct"] - 0.1831) < 0.001
        assert audit["slippage_round_trip_pct"] == 0.10
        assert audit["charges_pct"] == pytest.approx(0.0831, abs=0.001)

    def test_constants_match_the_lab_floor(self):
        c = cost_constants()
        assert c["slippage_round_trip_pct"] == SLIPPAGE_ROUND_TRIP_PCT
        assert c["slippage_pct_per_leg"] == pytest.approx(0.05)
        assert c["floor_function"].endswith("cost_floor_pct")

    def test_published_charge_schedule_is_paper_engine_not_a_copy(self):
        """The report quotes these figures; they must stay the live schedule."""
        from app.core.config import Settings
        from app.services.paper_engine import (
            BROKERAGE_CAP, BROKERAGE_PCT, EXCHANGE_TXN_PCT, GST_PCT,
            SEBI_PCT, STAMP_DUTY_PCT, STT_PCT,
        )
        assert BROKERAGE_PCT == 0.0003
        assert BROKERAGE_CAP == 20.0
        assert STT_PCT == 0.00025
        assert EXCHANGE_TXN_PCT == 0.0000325
        assert GST_PCT == 0.18
        assert SEBI_PCT == 0.000001
        assert STAMP_DUTY_PCT == 0.00003
        assert Settings().paper_slippage_pct == 0.05

    def test_research_module_does_not_import_paper_engine(self):
        for path in (
            pathlib.Path("app/research/tradeability.py"),
            pathlib.Path("app/research/tradeability_evaluate.py"),
        ):
            text = path.read_text(encoding="utf-8")
            assert "import paper_engine" not in text
            assert "from app.services.paper_engine" not in text


class TestCoverageRatio:
    def test_two_times_cost_is_ratio_two(self):
        assert cost_coverage_ratio(0.366, 0.183) == pytest.approx(2.0)

    def test_not_defined_for_zero_cost(self):
        assert cost_coverage_ratio(0.2, 0.0) is None

    def test_hits_are_inclusive_thresholds(self):
        hits = coverage_hits(0.366, 0.183)
        assert hits[1] and hits[2]
        assert not hits[3] and not hits[5]

    def test_coverage_bins_are_left_closed_except_the_first(self):
        from app.research.tradeability import coverage_bin
        assert coverage_bin(0.99) == "<1x"
        assert coverage_bin(1.0) == "1-2x"
        assert coverage_bin(1.99) == "1-2x"
        assert coverage_bin(2.0) == "2-3x"
        assert coverage_bin(3.0) == "3-5x"
        assert coverage_bin(5.0) == ">5x"
        assert coverage_bin(None) is None

    def test_module_doc_distinguishes_available_from_pnl(self):
        text = pathlib.Path("app/research/tradeability.py").read_text(encoding="utf-8")
        assert "available movement" in text
        assert "capturable movement" in text
        assert "realized trading P&L" in text
        assert "not a probability" in text.lower() or "Not a probability" in text


class TestRegimeClassification:
    def test_quartile_cuts_are_closed_at_the_tails(self):
        assert classify_vol_regime(1.0, 2.0, 4.0) == "LOW"
        assert classify_vol_regime(2.0, 2.0, 4.0) == "LOW"
        assert classify_vol_regime(3.0, 2.0, 4.0) == "NORMAL"
        assert classify_vol_regime(4.0, 2.0, 4.0) == "HIGH"
        assert classify_vol_regime(5.0, 2.0, 4.0) == "HIGH"

    def test_large_move_threshold_is_a_priori_one_atr(self):
        assert LARGE_MOVE_ATR == 1.0


class TestCausalInputs:
    def test_rel_atr_does_not_change_when_a_later_bar_is_mutated(self):
        c = _session("2026-06-01") + _session("2026-06-02")
        series = atr_series_for(c)
        i = 20
        before = rel_atr_at(c, series, i)
        c[i + 1] = OHLCV(c[i + 1].ts, 999, 1000, 1, 500, 1000)
        after = rel_atr_at(c, atr_series_for(c), i)
        assert before is not None and before == after

    def test_twelve_bar_return_is_none_until_the_session_has_enough_history(self):
        c = _session("2026-06-01")
        index = SessionIndex(c)
        assert contemporaneous_return_pct(c, index, LOOKBACK_BARS - 1) is None
        got = contemporaneous_return_pct(c, index, LOOKBACK_BARS)
        assert got is not None
        expected = (c[LOOKBACK_BARS].close / c[0].close - 1) * 100
        assert got == pytest.approx(expected)

    def test_twelve_bar_return_does_not_cross_sessions(self):
        c = _session("2026-06-01") + _session("2026-06-02")
        index = SessionIndex(c)
        first_of_day2 = BARS_PER_SESSION
        assert contemporaneous_return_pct(c, index, first_of_day2 + LOOKBACK_BARS - 1) is None
        assert contemporaneous_return_pct(c, index, first_of_day2 + LOOKBACK_BARS) is not None

    def test_session_range_ignores_the_next_bar(self):
        c = _session("2026-06-01")
        index = SessionIndex(c)
        series = atr_series_for(c)
        i = 20
        before = session_range_over_atr(c, index, series, i)
        c[i + 1] = OHLCV(c[i + 1].ts, 1, 10_000, 1, 1, 1000)
        after = session_range_over_atr(c, index, atr_series_for(c), i)
        assert before is not None and before == after

    def test_gap_is_only_defined_on_the_session_open(self):
        c = _session("2026-06-01") + _session("2026-06-02", start=110.0)
        index = SessionIndex(c)
        series = atr_series_for(c)
        assert gap_over_atr(c, index, series, 0) is None
        g = gap_over_atr(c, index, series, BARS_PER_SESSION)
        assert g is not None and g > 0
        assert gap_over_atr(c, index, series, BARS_PER_SESSION + 1) is None

    def test_large_move_uses_prior_atr_not_the_bar_being_measured(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        # inflate bar i's range without changing close-to-close; ATR[t] would
        # move, ATR[t-1] would not.
        close_prev, close = c[i - 1].close, c[i].close
        c[i] = OHLCV(c[i].ts, close, close + 50, close - 50, close, 1000)
        moved = bar_move_in_atr(c, atr_series_for(c), i)
        expected = (close - close_prev) / series[i - 1]
        assert moved == pytest.approx(expected)


class TestForwardEligibility:
    def test_horizons_include_the_registered_set(self):
        assert HORIZONS == (1, 3, 6, 12, 24, 48)

    def test_forty_eight_bar_window_is_invalid_late_in_the_session(self):
        c = _session("2026-06-01")
        index = SessionIndex(c)
        assert forward_window_ok(index, 0, 48)
        assert forward_window_ok(index, 26, 48)  # 26+48 = 74, last bar
        assert not forward_window_ok(index, 27, 48)

    def test_unsigned_metrics_clip_unfavourable_side_at_zero(self):
        m = unsigned_window_metrics(100.0, 101.0, 102.0, 99.0)
        assert m["signed_pct"] == pytest.approx(1.0)
        assert m["abs_pct"] == pytest.approx(1.0)
        assert m["upside_exc_pct"] == pytest.approx(2.0)
        assert m["downside_exc_pct"] == pytest.approx(1.0)
        assert m["range_pct"] == pytest.approx(3.0)
        down = unsigned_window_metrics(100.0, 98.0, 99.5, 97.0)
        assert down["upside_exc_pct"] == 0.0
        assert down["downside_exc_pct"] == pytest.approx(3.0)


class TestHoldoutGuard:
    def test_development_window_is_accepted(self):
        assert_development_window(DEV_START, DEV_END)

    def test_validation_and_holdout_dates_are_rejected(self):
        with pytest.raises(ValueError, match="hold-out"):
            assert_development_window(DEV_START, VAL_END)
        with pytest.raises(ValueError, match="hold-out"):
            assert_development_window(DEV_START, dt.date(2026, 8, 11))


class TestNoStrategyCreep:
    def test_module_does_not_emit_buy_or_sell(self):
        src = pathlib.Path("app/research/tradeability.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        text = src.lower()
        assert "generate(" not in src
        assert "entry_long" not in src
        assert "H006" not in src
        assert "buy" not in text or "does not generate buy/sell" in text

    def test_seed_still_has_exactly_five_hypotheses(self):
        import app.research.seed_hypotheses as seed

        # The live seed module must not have grown a sixth hypothesis.
        ids = [name for name in dir(seed) if name.startswith("H") and name[1:4].isdigit()]
        assert ids == ["H001", "H002", "H003", "H004", "H005"]

    def test_frozen_mkt_rel_atr_cuts_are_predictor_side_quartiles(self):
        assert MKT_REL_ATR_P25 == 0.001802
        assert MKT_REL_ATR_P75 == 0.002430
        assert classify_vol_regime(MKT_REL_ATR_P25, MKT_REL_ATR_P25, MKT_REL_ATR_P75) == "LOW"
        assert classify_vol_regime(MKT_REL_ATR_P75, MKT_REL_ATR_P25, MKT_REL_ATR_P75) == "HIGH"

    def test_gate_audit_does_not_compute_forward_returns(self):
        src = pathlib.Path("app/research/tradeability_gate_audit.py").read_text(encoding="utf-8")
        assert "forward_return" not in src
        assert "unsigned_window_metrics" not in src
        assert "H006" not in src or "NOT CREATED" in src
        assert "from app.services.paper_engine" not in src

    def test_gate_label_uses_the_frozen_p75_and_is_not_a_side(self):
        assert GATE_HIGH_CUT == 0.002430
        assert GATE_PRIMARY_HORIZON == 6
        assert gate_label(0.002430) == "HIGH"
        assert gate_label(0.002429) == "STANDBY"
        assert gate_label(0.002430) not in {"BUY", "SELL"}

    def test_label_shuffle_preserves_counts_inside_each_bucket(self):
        labels = {1: "HIGH", 2: "STANDBY", 3: "HIGH", 10: "STANDBY", 11: "STANDBY"}
        buckets = {1: 0, 2: 0, 3: 0, 10: 1, 11: 1}
        rng = __import__("random").Random(0)
        out = shuffle_labels_within_buckets(labels, buckets, rng)
        assert sum(out[t] == "HIGH" for t in (1, 2, 3)) == 2
        assert sum(out[t] == "STANDBY" for t in (10, 11)) == 2
        # A HIGH from bucket 0 cannot land in bucket 1.
        assert out[10] != "HIGH" and out[11] != "HIGH"

    def test_day_effects_ignore_buckets_that_lack_one_side(self):
        cells = {
            ("d1", 0): ([2.0, 2.0], [1.0, 1.0]),
            ("d1", 1): ([3.0], []),          # not comparable
            ("d2", 0): ([], [1.0]),          # not comparable
        }
        fx = comparable_day_effects(cells)
        assert set(fx) == {"d1"}
        assert fx["d1"] == pytest.approx(1.0)

    def test_gate_evaluate_is_not_a_registered_strategy(self):
        src = pathlib.Path("app/research/tradeability_gate_evaluate.py").read_text(encoding="utf-8")
        assert "NOT CREATED" in src
        assert "from app.services.paper_engine" not in src
        assert "entry_long" not in src
        assert "registry.register" not in src
        assert "GATE_PRIMARY_HORIZON" in src


class TestVolumeQualityContract:
    def test_unknown_volume_is_a_distinct_flag_not_a_quiet_bar(self):
        assert OK != UNKNOWN
        assert UNKNOWN == "UNKNOWN"
