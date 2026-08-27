"""Seed the registry with the hypotheses tested so far, and the next proposal.

H001 and H002 are recorded as REJECTED with the numbers that rejected them, so
the verdicts survive independently of any transcript. H003 is PROPOSED — the
market behaviour is stated, but no generator exists and no parameters are
committed to yet.

Idempotent: re-running updates the same (id, version) rows rather than
duplicating them.
"""
from __future__ import annotations

from app.research.hypothesis_registry import Hypothesis, registry

DATASET = "research_5m_v1"

H001 = Hypothesis(
    hypothesis_id="H001",
    version="v1",
    name="EMA 9/21 Crossover",
    description=(
        "Enter long when the 9-period EMA crosses above the 21-period EMA, short on the "
        "opposite cross. Tests whether a moving-average crossover carries directional "
        "information at intraday resolution."
    ),
    status="REJECTED",
    rule_definition={
        "entry_long": "EMA(9)[t] > EMA(21)[t] AND EMA(9)[t-1] <= EMA(21)[t-1]",
        "entry_short": "EMA(9)[t] < EMA(21)[t] AND EMA(9)[t-1] >= EMA(21)[t-1]",
        "interval": "5m",
        "signal_bar": "cross confirmed on the close of bar t; no data after t is used",
        "implementation": "app/services/scanner_engine.py StrategyEngine.evaluate_at",
    },
    dataset_id=DATASET,
    result_summary={
        "signals": 8166,
        "horizons_bars": [6, 12, 24],
        "mfe_mae_ratio": [0.83, 0.89, 0.93],
        "control_same_bar_ratio": [0.99, 1.00, 1.00],
        "edge_vs_same_bar": [-0.161, -0.111, -0.070],
        "block_permutation_p": [0.000, 0.000, 0.000],
        "holdout_edge": [-0.07, -0.01, 0.02],
        "long_vs_short_ratio": [0.82, 0.84],
        "verdict": "worse than a same-bar random-side control at every horizon",
    },
    notes=(
        "Rejected on dataset research_5m_v1 (39 symbols, 63 trading days). Do not modify, "
        "optimise or attempt to rescue. An earlier 8-day sample suggested shorts outperformed "
        "longs; that was market drift, and the balanced sample shows long and short are "
        "identical."
    ),
)

H002 = Hypothesis(
    hypothesis_id="H002",
    version="v1",
    name="Opening Range Breakout",
    description=(
        "Measure the high and low of the 09:15-09:30 IST opening range, then enter on the "
        "first 5-minute close outside it — long above the high, short below the low. Tests "
        "whether the opening range marks a level whose break carries information."
    ),
    status="REJECTED",
    rule_definition={
        "opening_range": "high/low of bars with IST time in [09:15, 09:30)",
        "entry_long": "close[t] > range_high, first occurrence of the day",
        "entry_short": "close[t] < range_low, first occurrence of the day",
        "cutoff": "no entries after 14:45 IST",
        "interval": "5m",
        "implementation": "app/services/orb_hypothesis.py generate()",
    },
    dataset_id=DATASET,
    result_summary={
        "signals": 2296,
        "horizons_bars": [6, 12, 24],
        "mfe_mae_ratio": [0.74, 0.83, 0.88],
        "control_same_bar_ratio": [0.98, 1.02, 1.00],
        "edge_vs_same_bar": [-0.241, -0.198, -0.117],
        "block_permutation_p": [0.000, 0.000, 0.000],
        "holdout_edge": [-0.30, -0.18, -0.13],
        "long_vs_short_ratio": [0.76, 0.72],
        "verdict": "worse than both controls at every horizon; stays negative out of sample",
    },
    notes=(
        "Rejected on dataset research_5m_v1. Do not modify, optimise or attempt to rescue. "
        "An RVOL-gated variant left only 23 signals on the earlier sample and was not readable; "
        "it was not re-run, and would be a new version if it ever were."
    ),
)

H003 = Hypothesis(
    hypothesis_id="H003",
    version="v1",
    name="Pullback Continuation",
    description=(
        "After a measurable ordered directional impulse, price retraces part of that impulse "
        "and then resumes. Tests whether the continuation trigger after a defined pullback "
        "carries directional information beyond an arbitrary entry following the same impulse."
    ),
    status="DEFINED",
    rule_definition={
        "frozen_at": "pre-registration, before any forward outcome was read",
        "lookback_bars": 12,
        "atr_period": 14,
        "k_impulse_atr": 3.5,
        "k_provenance": (
            "rounded 75th percentile of the causal impulse_score distribution measured on the "
            "development period only (p75 = 3.5186, n = 134,365). Predictor distribution only; "
            "no forward outcome was involved. Must not be re-calibrated against performance."
        ),
        "stage_a_impulse": (
            "LONG: high_idx = argmax(high) over [t-12, t-1] (earliest tie); "
            "low_idx = argmin(low) over [t-12, high_idx]; require low_idx < high_idx. "
            "SHORT mirrors. Lookback may not cross a session boundary. "
            "R = impulse_high - impulse_low; require R / ATR(14)[t] >= 3.5"
        ),
        "stage_b_pullback": (
            "pullback_bar_count = t - end_idx - 1; require 1 <= pullback_bar_count <= impulse_bars. "
            "LONG: pullback_low = min(low) over [end_idx+1, t-1]; "
            "retrace = (impulse_high - pullback_low) / R. SHORT: pullback_high = max(high) over "
            "the same bars; retrace = (pullback_high - impulse_low) / R. "
            "Depth is measured from COMPLETED pullback bars only; bar t contributes the trigger."
        ),
        "stage_c_valid": "LONG: pullback_low > impulse_low. SHORT: pullback_high < impulse_high",
        "stage_d_trigger": "LONG: close[t] > high[t-1]. SHORT: close[t] < low[t-1]",
        "variants": {
            "A": {"depth_min": 0.20, "depth_max": 0.40, "band": "[0.20, 0.40)", "label": "shallow"},
            "B": {"depth_min": 0.40, "depth_max": 0.65, "band": "[0.40, 0.65]", "label": "medium"},
        },
        "variant_boundary_note": (
            "The registered bands share 0.40. A is half-open at the top so exactly one variant "
            "claims any depth; the bands are otherwise unchanged."
        ),
        "no_filters": (
            "No EMA, RSI, MACD, ADX, Supertrend, VWAP, Bollinger, volume or candle-count "
            "condition. ATR appears only to normalise impulse size across instruments."
        ),
        "implementation": "app/research/pullback_hypothesis.py",
        "evaluation_plan": {
            "horizons_bars": [6, 12, 24],
            "primary_metric": "net_move_pct = (close[t+h] - close[t]) * dir / close[t] * 100",
            "secondary": ["mfe_pct", "mae_pct", "mfe_mae_ratio"],
            "controls": {
                "A": "same bar, same symbol, same day, random side",
                "B": "same day, same symbol, same direction, random bar",
                "C": "same day, same symbol, same direction, random bar among stage-A passing bars",
            },
            "statistics": "day-level block permutation on a difference of means",
            "comparisons": 6,
            "significance_threshold": 0.008,
            "split": "60/20/20 chronological by trading day",
        },
        "gates": {
            "0": ">= 300 signals and >= 25 signal-days in development, else UNDERPOWERED",
            "1": "directional edge over Control A",
            "2": "structural edge over Control C",
            "3": "sign preserved in validation",
            "4": "sign preserved in hold-out",
            "label": "mean MFE > 0.183% -> movement potentially sufficient for further "
                     "economic testing (a label, not a gate; MFE is a best-case excursion)",
        },
    },
    notes=(
        "Parameters frozen after input-only calibration and design review. Rule must not be "
        "modified after results are seen; a revision becomes H003 v2 with v1 preserved."
    ),
)


def seed() -> list[Hypothesis]:
    return [registry.register(h) for h in (H001, H002, H003)]


if __name__ == "__main__":
    for h in seed():
        print(f"{h.hypothesis_id} {h.version:4} {h.status:10} {h.name}")
