"""Extreme-move event definition — input-only. Not H006.

These pin causality (ATR[t-1], no bar after t), the frozen 1.0 cut, the fade
sign convention, and the hold-out / registry fences. They do not evaluate
forward returns on research data.
"""
from __future__ import annotations

import ast
import datetime as dt
import pathlib
import random

import pytest

from app.research.extreme_move import (
    DEV_END, DEV_START, EXTREME_CUT, FADE_HORIZONS, FADE_PRIMARY_HORIZON,
    LARGE_MOVE_ATR, assert_development_window, atr_series_for,
    bar_move_in_atr, event_direction, extreme_score, is_extreme_event,
    is_reversion, opposite_session_room, same_session_predecessor,
    session_range_position, shuffle_directions, signed_future_move,
    trailing_ok_volume,
)
from app.research.tradeability import VAL_END
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
BARS_PER_SESSION = 75


def _bars(spec: list[tuple[float, float, float, float]], day="2026-06-01", volume=1000) -> list[OHLCV]:
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(hour=9, minute=15, tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, o, h, l, c, volume) for i, (o, h, l, c) in enumerate(spec)]


def _session(day: str, bars: int = BARS_PER_SESSION, start=100.0, drift=0.01) -> list[OHLCV]:
    spec = []
    px = start
    for _ in range(bars):
        spec.append((px, px + 1.0, px - 1.0, px + drift))
        px += drift
    return _bars(spec, day)


class TestFrozenCut:
    def test_cut_is_the_existing_one_atr_definition(self):
        assert EXTREME_CUT == 1.0
        assert EXTREME_CUT == LARGE_MOVE_ATR

    def test_horizons_are_the_precommitted_set(self):
        assert FADE_HORIZONS == (1, 3, 6, 12)
        assert FADE_PRIMARY_HORIZON == 6
        assert 24 not in FADE_HORIZONS and 48 not in FADE_HORIZONS


class TestCausalEvent:
    def test_score_uses_prior_atr_not_the_event_bar(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        # A huge range on bar i would move ATR[t] but not ATR[t-1].
        prev_close, close = c[i - 1].close, c[i].close
        c[i] = OHLCV(c[i].ts, close, close + 80, close - 80, close, 1000)
        got = extreme_score(c, atr_series_for(c), i)
        expected = abs(close - prev_close) / series[i - 1]
        assert got == pytest.approx(expected)

    def test_one_atr_close_to_close_is_an_event(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        atr_prev = series[i - 1]
        assert atr_prev is not None
        new_close = c[i - 1].close + 1.2 * atr_prev
        c[i] = OHLCV(c[i].ts, c[i].open, max(c[i].high, new_close), c[i].low, new_close, 1000)
        assert is_extreme_event(c, atr_series_for(c), i)
        assert event_direction(c, i) == 1
        assert extreme_score(c, atr_series_for(c), i) == pytest.approx(1.2)

    def test_sub_threshold_and_flat_are_not_events(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        atr_prev = series[i - 1]
        small = c[i - 1].close + 0.4 * atr_prev
        c[i] = OHLCV(c[i].ts, small, small, small, small, 1000)
        assert not is_extreme_event(c, atr_series_for(c), i)
        c[i] = OHLCV(c[i].ts, c[i - 1].close, c[i - 1].close, c[i - 1].close, c[i - 1].close, 1000)
        assert event_direction(c, i) is None
        assert not is_extreme_event(c, atr_series_for(c), i)

    def test_down_move_is_direction_minus_one(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        new_close = c[i - 1].close - 1.5 * series[i - 1]
        c[i] = OHLCV(c[i].ts, c[i].open, c[i].high, min(c[i].low, new_close), new_close, 1000)
        assert is_extreme_event(c, atr_series_for(c), i)
        assert event_direction(c, i) == -1

    def test_truncating_bars_after_t_does_not_change_the_event(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        new_close = c[i - 1].close + 1.1 * series[i - 1]
        c[i] = OHLCV(c[i].ts, c[i].open, max(c[i].high, new_close), c[i].low, new_close, 1000)
        full_series = atr_series_for(c)
        before = (
            is_extreme_event(c, full_series, i),
            event_direction(c, i),
            extreme_score(c, full_series, i),
            bar_move_in_atr(c, full_series, i),
        )
        truncated = c[: i + 1]
        trunc_series = atr_series_for(truncated)
        after = (
            is_extreme_event(truncated, trunc_series, i),
            event_direction(truncated, i),
            extreme_score(truncated, trunc_series, i),
            bar_move_in_atr(truncated, trunc_series, i),
        )
        assert before[0] is True and after[0] is True
        assert before[1] == after[1] == 1
        assert before[2] == pytest.approx(after[2])
        assert before[3] == pytest.approx(after[3])

    def test_mutating_the_next_bar_does_not_change_the_event(self):
        c = _session("2026-06-01")
        series = atr_series_for(c)
        i = 20
        new_close = c[i - 1].close + 1.1 * series[i - 1]
        c[i] = OHLCV(c[i].ts, c[i].open, max(c[i].high, new_close), c[i].low, new_close, 1000)
        before = extreme_score(c, atr_series_for(c), i)
        c[i + 1] = OHLCV(c[i + 1].ts, 1, 10_000, 1, 1, 9_999_999)
        after = extreme_score(c, atr_series_for(c), i)
        assert before == pytest.approx(after)
        assert event_direction(c, i) == 1


class TestSessionPredecessor:
    def test_first_bar_of_day_two_is_not_same_session(self):
        c = _session("2026-06-01") + _session("2026-06-02", start=110.0)
        index = SessionIndex(c)
        assert same_session_predecessor(index, BARS_PER_SESSION) is False
        assert same_session_predecessor(index, BARS_PER_SESSION + 1) is True
        assert same_session_predecessor(index, 20) is True


class TestMechanicalRoom:
    def test_a_spike_to_the_session_high_sits_near_one(self):
        c = _session("2026-06-01")
        i = 20
        c[i] = OHLCV(c[i].ts, 100.2, 130.0, 100.0, 129.0, 1000)
        index = SessionIndex(c)
        pos = session_range_position(c, index, i)
        assert pos is not None and pos > 0.9
        assert opposite_session_room(c, index, i, 1) == pytest.approx(pos)

    def test_room_ignores_the_next_bar(self):
        c = _session("2026-06-01")
        index = SessionIndex(c)
        i = 20
        before = session_range_position(c, index, i)
        c[i + 1] = OHLCV(c[i + 1].ts, 1, 9_000, 1, 1, 1000)
        after = session_range_position(c, index, i)
        assert before == after


class TestFadeDefinition:
    def test_negative_signed_move_is_reversion(self):
        assert signed_future_move(-2.0, 1) == -2.0
        assert signed_future_move(2.0, -1) == -2.0
        assert is_reversion(-1.0, 1) is True
        assert is_reversion(1.0, 1) is False
        assert is_reversion(1.0, -1) is True
        assert is_reversion(0.0, 1) is None

    def test_direction_shuffle_preserves_counts(self):
        dirs = [1, 1, 1, -1, -1]
        out = shuffle_directions(dirs, random.Random(0))
        assert sorted(out) == sorted(dirs)
        assert len(out) == 5


class TestCausalVolume:
    def test_unknown_event_bar_is_excluded(self):
        vols = [100] * 20
        ts = list(range(20))
        vol, rvol = trailing_ok_volume(vols, {15}, ts, 0, 15)
        assert vol is None and rvol is None

    def test_baseline_does_not_include_the_event_bar_or_the_future(self):
        vols = [10] * 12 + [100] + [999] * 5
        ts = list(range(18))
        vol, rvol = trailing_ok_volume(vols, set(), ts, 0, 12)
        assert vol == 100
        assert rvol == pytest.approx(10.0)


class TestHoldoutAndRegistryFences:
    def test_development_window_is_accepted(self):
        assert_development_window(DEV_START, DEV_END)

    def test_validation_and_holdout_are_rejected(self):
        with pytest.raises(ValueError, match="hold-out"):
            assert_development_window(DEV_START, VAL_END)
        with pytest.raises(ValueError, match="hold-out"):
            assert_development_window(DEV_START, dt.date(2026, 8, 11))

    def test_module_does_not_create_h006_or_a_side(self):
        src = pathlib.Path("app/research/extreme_move.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        assert "H006" not in src or "does not register H006" in src
        assert "entry_long" not in src
        assert "generate(" not in src
        names = [n.id for n in ast.walk(tree) if isinstance(n, ast.Name)]
        assert "BUY" not in names and "SELL" not in names

    def test_audit_does_not_compute_forward_returns_or_register(self):
        src = pathlib.Path("app/research/extreme_move_audit.py").read_text(encoding="utf-8")
        assert "forward_return" not in src
        assert "unsigned_window_metrics" not in src
        assert "close[i + h]" not in src
        assert "close[t+h]" not in src
        assert "registry.register" not in src
        assert "from app.services.paper_engine" not in src
        assert "NOT CREATED" in src
        assert "NOT RUN" in src

    def test_research_modules_do_not_import_paper_engine(self):
        for path in (
            pathlib.Path("app/research/extreme_move.py"),
            pathlib.Path("app/research/extreme_move_audit.py"),
        ):
            text = path.read_text(encoding="utf-8")
            assert "import paper_engine" not in text
            assert "from app.services.paper_engine" not in text

    def test_seed_still_has_exactly_five_hypotheses(self):
        import app.research.seed_hypotheses as seed

        ids = [name for name in dir(seed) if name.startswith("H") and name[1:4].isdigit()]
        assert ids == ["H001", "H002", "H003", "H004", "H005"]
        text = pathlib.Path("app/research/seed_hypotheses.py").read_text(encoding="utf-8")
        assert "H006" not in text
