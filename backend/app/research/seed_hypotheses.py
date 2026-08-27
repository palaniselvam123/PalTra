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
        "After a measurable directional impulse, price retraces part of that impulse and then "
        "resumes. Tests whether the resumption point carries directional information — that is, "
        "whether joining an established move after a counter-move is better located than "
        "entering at an arbitrary moment in the same session."
    ),
    status="PROPOSED",
    rule_definition={
        "state": "proposed only — no exact parameters committed, no generator implemented",
        "structure": [
            "1. impulse: a measured directional move over a defined lookback",
            "2. pullback: a counter-move of bounded depth and duration",
            "3. trigger: resumption in the impulse direction",
        ],
        "note": "parameters are deliberately absent until the rule is pre-registered as DEFINED",
    },
    notes=(
        "Proposed, not defined. The exact thresholds must be written into rule_definition and "
        "the status moved to DEFINED before any evaluation is run, so the rule cannot be "
        "adjusted after seeing results."
    ),
)


def seed() -> list[Hypothesis]:
    return [registry.register(h) for h in (H001, H002, H003)]


if __name__ == "__main__":
    for h in seed():
        print(f"{h.hypothesis_id} {h.version:4} {h.status:10} {h.name}")
