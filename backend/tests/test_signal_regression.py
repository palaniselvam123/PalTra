"""Phase 16 regression: the optimised path must equal the original.

`evaluate()` used to recompute every indicator on a growing slice of history,
which made a backtest O(n^2) and never finished. `precompute()` +
`evaluate_at()` replaced it. These tests pin the equivalence so a future change
cannot silently alter live signal behaviour while "optimising".

The comparison is meaningful rather than tautological: the per-bar path
computes indicators over a TRUNCATED history `candles[:end]`, while the fast
path computes them ONCE over the full array. Any indicator that peeks forward,
or whose value depends on how much future data is present, would diverge here.
"""
from __future__ import annotations

import random

import pytest

from app.services.indicators import OHLCV
from app.services.scanner_engine import StrategyEngine, StrategyParams


def synthetic(n: int = 400, seed: int = 11) -> list[OHLCV]:
    random.seed(seed)
    bars, price = [], 100.0
    for i in range(n):
        o = price
        price = max(price * (1 + random.gauss(0, 0.004)), 1.0)
        bars.append(
            OHLCV(
                ts=i * 300,
                open=o,
                high=max(o, price) * 1.002,
                low=min(o, price) * 0.998,
                close=price,
                volume=random.randint(500, 5000),
            )
        )
    return bars


def signals_slow(engine: StrategyEngine, bars: list[OHLCV]):
    """Original behaviour: recompute over a growing slice, read the last bar."""
    out = []
    for end in range(engine.params.warmup_bars(), len(bars) + 1):
        sig = engine.evaluate("X", "5m", bars[:end])
        if sig is not None:
            out.append((end - 1, sig.side, sig.price, sig.fast_value, sig.slow_value))
    return out


def signals_fast(engine: StrategyEngine, bars: list[OHLCV]):
    """Optimised: compute once, evaluate by index."""
    ctx = engine.precompute(bars)
    out = []
    for i in range(len(bars)):
        sig = engine.evaluate_at("X", "5m", bars, i, ctx)
        if sig is not None:
            out.append((i, sig.side, sig.price, sig.fast_value, sig.slow_value))
    return out


FILTER_SETS = [
    pytest.param({}, id="bare"),
    pytest.param({"adx_filter": True, "adx_threshold": 15}, id="adx"),
    pytest.param({"volume_filter": True, "volume_multiplier": 1.2}, id="volume"),
    pytest.param({"pattern_filter": True, "pattern_lookback": 3}, id="pattern"),
    pytest.param({"trend_filter": True, "trend_period": 50}, id="trend"),
    pytest.param({"rsi_filter": True, "rsi_overbought": 70}, id="rsi"),
    pytest.param(
        {
            "adx_filter": True,
            "adx_threshold": 15,
            "volume_filter": True,
            "volume_multiplier": 1.2,
            "pattern_filter": True,
            "pattern_lookback": 3,
        },
        id="all-filters",
    ),
]


@pytest.mark.parametrize("filters", FILTER_SETS)
def test_optimised_path_matches_original(filters):
    bars = synthetic()
    engine = StrategyEngine(StrategyParams(fast_period=9, slow_period=21, **filters))
    assert signals_fast(engine, bars) == signals_slow(engine, bars)


@pytest.mark.parametrize("seed", [1, 42, 2024])
def test_equivalence_holds_across_price_paths(seed):
    bars = synthetic(seed=seed)
    engine = StrategyEngine(StrategyParams(fast_period=9, slow_period=21, adx_filter=True, adx_threshold=15))
    assert signals_fast(engine, bars) == signals_slow(engine, bars)


def test_produces_some_signals():
    """Guards against the equivalence passing trivially because both paths
    return nothing."""
    bars = synthetic()
    engine = StrategyEngine(StrategyParams(fast_period=9, slow_period=21))
    assert len(signals_fast(engine, bars)) > 5


def test_no_lookahead_truncating_future_bars_changes_nothing():
    """A signal at bar i must not depend on bars after i. Evaluating with the
    future removed has to give the identical answer."""
    bars = synthetic()
    engine = StrategyEngine(StrategyParams(fast_period=9, slow_period=21, adx_filter=True, adx_threshold=15))
    full_ctx = engine.precompute(bars)

    for i in range(300, 340):
        with_future = engine.evaluate_at("X", "5m", bars, i, full_ctx)
        truncated = bars[: i + 1]
        without_future = engine.evaluate_at("X", "5m", truncated, i, engine.precompute(truncated))
        assert (with_future is None) == (without_future is None), f"look-ahead at bar {i}"
        if with_future is not None:
            assert with_future.side == without_future.side
            assert with_future.price == without_future.price
