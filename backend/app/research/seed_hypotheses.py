"""Seed the registry with the hypotheses tested so far, and the next proposal.

H001–H004 are recorded with their verdicts. H005 v1 is DEFINED — the exact
rule is frozen, no forward outcome has been read. Idempotent: re-running
updates the same (id, version) rows rather than duplicating them. A version
that already carries a result is never overwritten.
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


H004 = Hypothesis(
    hypothesis_id="H004",
    version="v1",
    name="Cross-Sectional Sector-Neutral Relative Strength",
    description=(
        "At a fixed 5-minute timestamp, does a stock's volatility-adjusted return relative to "
        "the median return of its own sector contain information about its subsequent "
        "sector-relative return? A new hypothesis, not a variant of H001-H003: those condition "
        "on a completed directional move in one instrument and ask whether it continues, while "
        "this compares instruments to one another at a single instant and never conditions on a "
        "move having occurred."
    ),
    status="DEFINED",
    rule_definition={
        "frozen_at": "pre-registration, before any forward outcome was read",
        "interval": "5m",
        "lookback_bars": 12,
        "atr_period": 14,
        "min_sector_size": 5,
        "universe": {
            "verified": 125,
            "excluded_sectors": ["TELECOM(3)", "REALTY(2)", "CONGLOM(2)"],
            "research_universe": 118,
            "verification": "every symbol resolved against the Groww instrument master, series=EQ",
            "note": "LTIM and ZOMATO were proposed and REJECTED as absent from the master (renamed)",
        },
        "sector_rule": (
            "Hand-assigned from the issuer's line of business; reference information containing "
            "no return data. Deterministic, one sector per symbol, frozen in "
            "app/research/cross_sectional.py SECTORS."
        ),
        "score": (
            "sector_return_i(t) = median{ return_j(t) : j in sector(i), j != i }  [LEAVE-ONE-OUT]; "
            "sector_relative_return_i(t) = return_i(t) - sector_return_i(t); "
            "H004_score_i(t) = sector_relative_return_i(t) / (ATR_i(14,t) / close_i(t))"
        ),
        "leave_one_out_rationale": (
            "A sector median including the stock makes the median member's relative return exactly "
            "zero by construction - 6.81% of observations measured. Leave-one-out reduces that to "
            "0.02% and correlates +0.988 with the naive version."
        ),
        "min_sector_size_rationale": (
            "Structural, not tuned. With leave-one-out a sector of n benchmarks each member against "
            "n-1 peers; at n=2 or 3 the peer median is a single stock. Five gives a four-peer "
            "median, the smallest genuine group statistic. No performance figure consulted."
        ),
        "bucketing": (
            "Terciles computed INDEPENDENTLY within each sector, then pooled. Global terciles would "
            "tilt extremes toward small sectors: measured score dispersion is ~15% wider in "
            "five-member sectors (stdev 2.202) than twelve-member ones (1.922)."
        ),
        "outcome": (
            "future_sector_relative_return_i(t,h) = future_return_i(t,h) - "
            "median{ future_return_j(t,h) : j in sector(i), j != i }. Same leave-one-out "
            "construction as the score's benchmark."
        ),
        "horizons_bars": [6, 12, 24],
        "no_filters": (
            "No EMA, RSI, MACD, ADX, VWAP, Supertrend, volume or any other indicator. ATR enters "
            "only as a scale for cross-instrument comparability, never as a filter."
        ),
        "excluded_data": (
            "Volume is not used, so the 15:00-15:30 volume unreliability does not apply; price "
            "coverage there is 97.9-98.4% against a midday 99.6% with zero timestamp defects, so "
            "the full session is used."
        ),
        "null": (
            "Within each fixed timestamp AND sector, randomise only the score-to-stock assignment. "
            "Preserved: timestamp, universe, sector membership, market move, sector move, "
            "volatility environment, and the within-sector marginal distributions of both score "
            "and outcome. Permuting across the whole cross-section would break sector membership "
            "and let a sector-driven result beat the null."
        ),
        "inference": "day-level block permutation; stock-timestamps are NOT independent",
        "split": "chronological development / validation / hold-out; hold-out unread",
        "implementation": "app/research/cross_sectional.py",
        "gates": {
            "0_sample": "sufficient development sample; else UNDERPOWERED, not REJECTED",
            "1_relationship": "positive relationship between score and future sector-relative return",
            "2_significance": "day-level block permutation",
            "3_horizon_consistency": "directional consistency across 6, 12 and 24 bars",
            "4_validation": "sign preserved in validation",
            "5_holdout": "sign preserved in hold-out",
            "6_economic": "assessed ONLY after the directional gates pass; statistical "
                          "significance alone never declares a trading strategy",
        },
        "known_limitations": (
            "63 sessions, ~38 in development, a single market regime, and a broker price-adjustment "
            "policy that remains UNKNOWN / NOT VERIFIED."
        ),
    },
    notes=(
        "Pre-registered before any forward outcome was computed. Immutable: a changed rule becomes "
        "H004 v2 and v1's record stands."
    ),
)


H005 = Hypothesis(
    hypothesis_id="H005",
    version="v1",
    name="Compression → Expansion",
    description=(
        "After a period of unusually small realized true range relative to ATR, does the first "
        "bar whose true range expands materially beyond that window's own scale carry directional "
        "information about the subsequent move? A research hypothesis, not a trading strategy. "
        "The phenomenon is compression → expansion → direction. It is not an opening-range "
        "breakout and not a close beyond the prior 12-bar high/low."
    ),
    status="DEFINED",
    rule_definition={
        "frozen_at": "pre-registration, before any forward outcome was read",
        "interval": "5m",
        "lookback_bars": 12,
        "atr_period": 14,
        "c2_max": 0.794,
        "c3_min": 2.0,
        "c2_definition": (
            "C2(t) = mean(TR[i], i in [t-12, t-1]) / ATR(14)[t-1]. "
            "Lower C2 = more compressed recent bar size. Bar t is excluded. "
            "ATR is taken at t-1 because Wilder ATR[t] includes TR[t]."
        ),
        "c3_definition": (
            "C3(t) = TR[t] / mean(TR[i], i in [t-12, t-1]). "
            "Larger C3 = stronger expansion relative to the compression window itself. "
            "Not replaced by TR[t]/ATR[t-1], which would test 'large candle vs normal "
            "volatility' rather than expansion out of compression."
        ),
        "direction": (
            "+1 when close[t] > open[t] (BUY); -1 when close[t] < open[t] (SELL). "
            "close[t] == open[t] is excluded from directional-event observations. "
            "Not replaced by close[t] vs the prior 12-bar high/low."
        ),
        "temporal_structure": {
            "compression_window": "bars [t-12, t-1], same session, dropped not truncated",
            "transition_bar": "bar t",
            "observation": "close of bar t",
            "forward_window": "begins only after bar t",
        },
        "event": "C2(t) <= 0.794 AND C3(t) >= 2.0 AND close[t] != open[t]",
        "c2_max_provenance": (
            "development-period 10th percentile of C2 on 298,865 eligible bars, 38 sessions "
            "(2026-06-01..2026-07-23), 125 symbols. Raw p10 = 0.7938, registered as 0.794. "
            "Predictor distribution only. 'Unusually compressed' is the left tail (p10), not "
            "p25. No forward outcome was involved. Must not be re-calibrated against performance."
        ),
        "c3_min_provenance": (
            "a-priori round multiple: twice the compression window's own mean true range. "
            "C3 = 1 is the natural neutral. 2.0 is 'materially expanded' relative to that "
            "window, not a percentile of C3 and not compared against forward returns. Must "
            "not be re-calibrated against performance."
        ),
        "no_filters": (
            "No EMA, RSI, MACD, ADX, VWAP, Supertrend, Bollinger, volume or sector map. "
            "ATR enters only to scale recent mean TR. C1 (unordered span / (ATR*sqrt(K))) "
            "is not used: it is an identity of impulse_score."
        ),
        "universe": {
            "symbols": 125,
            "note": "single-instrument time series; the full 125-symbol research store, "
                    "not H004's sector-size-filtered 118.",
        },
        "dataset_id": DATASET,
        "split": {
            "rule": "chronological by trading day; hold-out unread until gates 1-3 are decided",
            "development": "2026-06-01 .. 2026-07-23 (38 sessions)",
            "validation": "2026-07-24 .. 2026-08-10 (12 sessions)",
            "hold_out": "unread",
        },
        "horizons_bars": [6, 12, 24],
        "primary_metric": "net_move_pct = (close[t+h] - close[t]) * dir / close[t] * 100",
        "secondary": ["mfe_pct", "mae_pct", "mfe_mae_ratio"],
        "controls": {
            "A": (
                "same timestamp, same symbol/day, random direction. The observation bar is "
                "the signal bar (control_a_index). Tests whether the transition knows which way. "
                "Eligibility is SessionIndex.is_forward_window_valid on that same bar."
            ),
            "C": (
                "same symbol, same day, same direction, same 30-minute session bucket, "
                "drawn from bars with C2 <= 0.794 and a non-flat body but C3 < 2.0. "
                "Population constructed in full before sampling; TimeMatchedSampler applies "
                "the forward-window eligibility layer at construction, never after a draw. "
                "Tests whether expansion adds information beyond the compressed state."
            ),
        },
        "forward_window_eligibility": (
            "SessionIndex.is_forward_window_valid: [i+1, i+h] must lie inside bar i's "
            "own session. Shared with every other hypothesis. Applied to Control C "
            "candidates before sampling."
        ),
        "inference": (
            "day-level block permutation on a difference of means; stock-bar observations "
            "are NOT independent. Existing permutation infrastructure "
            "(hypothesis_lab / day-blocked shuffle). Method frozen before results."
        ),
        "implementation": "app/research/compression_expansion.py",
        "gates": {
            "0": ">= 300 signals and >= 25 distinct signal-days in development, else UNDERPOWERED",
            "1": "directional edge over Control A",
            "2": "structural edge over Control C (expansion adds information beyond compression)",
            "3": "sign preserved in validation",
            "4": "sign preserved in hold-out",
            "5": "economic assessment ONLY after directional gates pass",
        },
        "known_limitations": (
            "C2 vs ATR(14) is tightly concentrated (std 0.125) because SMA(12) of TR and "
            "Wilder ATR(14) have similar memory. 38 development days, one market regime. "
            "Events cluster into episodes (typical 1.13 signals/episode at these cuts) and "
            "are not independent observations."
        ),
    },
    notes=(
        "Pre-registered after the input-only framing audit and before any forward outcome. "
        "Immutable: a changed rule becomes H005 v2 and v1's record stands. Do not run the "
        "forward experiment until this version is explicitly approved for testing."
    ),
)


def seed() -> list[Hypothesis]:
    """Register the pre-registrations, without ever clobbering a recorded verdict.

    A plain re-register would reset a hypothesis that has since been tested back
    to its DEFINED state — which is exactly what happened to H003 v1 when H004
    was added, silently reverting a REJECTED verdict. The registry exists to
    make results immutable, so seeding must refuse to overwrite any version that
    already carries a result.
    """
    out: list[Hypothesis] = []
    for h in (H001, H002, H003, H004, H005):
        existing = registry.get(h.hypothesis_id, h.version)
        if existing is not None and existing.result_summary is not None:
            out.append(existing)      # a tested hypothesis is left exactly as recorded
            continue
        out.append(registry.register(h))
    return out


if __name__ == "__main__":
    for h in seed():
        print(f"{h.hypothesis_id} {h.version:4} {h.status:10} {h.name}")
