"""Approved one-off tradeability-gate experiment — development only.

Question: within the same 30-minute bucket, do HIGH mkt_rel_atr timestamps
have larger available movement / cost than STANDBY?

Not a live strategy. Not H006. No BUY/SELL. Hold-out unread.

    python -m app.research.tradeability_gate_evaluate
"""
from __future__ import annotations

import json
import math
import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from app.research.matched_controls import bucket_label, session_bucket
from app.research.store import ResearchStore
from app.research.tradeability import (
    DEV_END, DEV_START, GATE_HIGH_CUT, GATE_PRIMARY_HORIZON,
    GATE_SECONDARY_HORIZONS, POSITION_VALUE, assert_development_window,
    atr_series_for, comparable_day_effects, cost_coverage_ratio,
    cost_floor_pct, gate_label, rel_atr_at, shuffle_labels_within_buckets,
)
from app.services.observation_window import SessionIndex, ist_date

MIN_CS = 50
PERM_SEED = 23
PERM_ITERS = 4000
HORIZONS = (GATE_PRIMARY_HORIZON,) + GATE_SECONDARY_HORIZONS


def _median(xs: np.ndarray) -> float:
    return float(np.median(xs)) if xs.size else float("nan")


def _share_ge(xs: np.ndarray, k: float) -> float | None:
    if xs.size == 0:
        return None
    return float(np.mean(xs >= k) * 100)


def _build_cells(
    ts_day: dict[int, object],
    ts_bucket: dict[int, int],
    ts_cov: dict[int, np.ndarray],
    labels: dict[int, str],
) -> dict[tuple, tuple[list[float], list[float]]]:
    high: dict[tuple, list[float]] = defaultdict(list)
    standby: dict[tuple, list[float]] = defaultdict(list)
    for ts, cov in ts_cov.items():
        lab = labels.get(ts)
        if lab is None or cov.size == 0:
            continue
        key = (ts_day[ts], ts_bucket[ts])
        (high if lab == "HIGH" else standby)[key].extend(cov.tolist())
    return {k: (high.get(k, []), standby.get(k, [])) for k in set(high) | set(standby)}


def run() -> dict:
    assert_development_window(DEV_START, DEV_END)
    store = ResearchStore()
    symbols = store.symbols("5m", "live")

    cs_rel: dict[int, list[float]] = defaultdict(list)
    per_symbol: list[tuple] = []

    for symbol in symbols:
        candles = store.read(symbol, "5m", "live", start=DEV_START, end=DEV_END)
        if not candles:
            continue
        index = SessionIndex(candles)
        series = atr_series_for(candles)
        close = np.array([c.close for c in candles], dtype=float)
        ts = np.array([c.ts for c in candles], dtype=np.int64)
        sess_end = np.array([index.session_end(i) for i in range(len(candles))], dtype=int)
        for i, c in enumerate(candles):
            ra = rel_atr_at(candles, series, i)
            if ra is not None:
                cs_rel[c.ts].append(ra)
        per_symbol.append((close, ts, sess_end))

    labels: dict[int, str] = {}
    ts_bucket: dict[int, int] = {}
    ts_day: dict[int, object] = {}
    unlabeled = 0
    for t, rels in cs_rel.items():
        if len(rels) < MIN_CS:
            unlabeled += 1
            continue
        labels[t] = gate_label(float(np.median(rels)), GATE_HIGH_CUT)
        ts_bucket[t] = session_bucket(t)
        ts_day[t] = ist_date(t)

    n_high_ts = sum(1 for v in labels.values() if v == "HIGH")
    n_standby_ts = len(labels) - n_high_ts

    cov_by_h: dict[int, dict[int, list[float]]] = {h: defaultdict(list) for h in HORIZONS}
    eligible = {h: {"HIGH": 0, "STANDBY": 0, "skipped_horizon": 0, "no_label": 0} for h in HORIZONS}

    for close, ts, sess_end in per_symbol:
        n = len(close)
        idx = np.arange(n)
        costs = np.array([
            cost_floor_pct(float(p), POSITION_VALUE) if p > 0 else float("nan")
            for p in close
        ], dtype=float)
        for h in HORIZONS:
            ok = (idx + h <= sess_end) & (close > 0) & np.isfinite(costs)
            if n > h:
                abs_pct = np.full(n, np.nan)
                abs_pct[: n - h] = np.abs(close[h:] / close[: n - h] - 1.0) * 100
            else:
                abs_pct = np.full(n, np.nan)
            kept = 0
            skipped = 0
            unlabeled_bars = 0
            for i in np.nonzero(ok)[0]:
                t = int(ts[i])
                if t not in labels:
                    unlabeled_bars += 1
                    continue
                cov = cost_coverage_ratio(float(abs_pct[i]), float(costs[i]))
                if cov is None:
                    skipped += 1
                    continue
                cov_by_h[h][t].append(cov)
                eligible[h][labels[t]] += 1
                kept += 1
            labeled_ineligible = 0
            for i in np.nonzero(~ok)[0]:
                if int(ts[i]) in labels:
                    labeled_ineligible += 1
            eligible[h]["skipped_horizon"] += labeled_ineligible + skipped
            eligible[h]["no_label"] += unlabeled_bars

    ts_cov = {
        h: {t: np.asarray(v, dtype=float) for t, v in cov_by_h[h].items() if v}
        for h in HORIZONS
    }

    def bucket_table(h: int) -> dict:
        by_b: dict[int, dict[str, list[float]]] = defaultdict(lambda: {"HIGH": [], "STANDBY": []})
        n_ts: dict[int, dict[str, int]] = defaultdict(lambda: {"HIGH": 0, "STANDBY": 0})
        for t, arr in ts_cov[h].items():
            lab = labels[t]
            b = ts_bucket[t]
            by_b[b][lab].extend(arr.tolist())
            n_ts[b][lab] += 1
        out = {}
        for b in sorted(by_b):
            hi = np.asarray(by_b[b]["HIGH"], dtype=float)
            st = np.asarray(by_b[b]["STANDBY"], dtype=float)
            med_h, med_s = _median(hi), _median(st)
            days_h = {ts_day[t] for t in ts_cov[h] if labels[t] == "HIGH" and ts_bucket[t] == b}
            days_s = {ts_day[t] for t in ts_cov[h] if labels[t] == "STANDBY" and ts_bucket[t] == b}
            out[int(b)] = {
                "label": bucket_label(b),
                "n_high_obs": int(hi.size),
                "n_standby_obs": int(st.size),
                "n_high_ts": n_ts[b]["HIGH"],
                "n_standby_ts": n_ts[b]["STANDBY"],
                "n_sessions_high": len(days_h),
                "n_sessions_standby": len(days_s),
                "median_high": med_h,
                "median_standby": med_s,
                "difference": (med_h - med_s) if hi.size and st.size else None,
                "comparable": bool(hi.size and st.size),
                "high_ge_1x": _share_ge(hi, 1.0),
                "high_ge_2x": _share_ge(hi, 2.0),
                "standby_ge_1x": _share_ge(st, 1.0),
                "standby_ge_2x": _share_ge(st, 2.0),
            }
        return out

    def horizon_block(h: int) -> dict:
        cells = _build_cells(ts_day, ts_bucket, ts_cov[h], labels)
        day_fx = comparable_day_effects(cells)
        buckets = bucket_table(h)
        comparable_buckets = [b for b, r in buckets.items() if r["comparable"]]
        diffs = [buckets[b]["difference"] for b in comparable_buckets]
        return {
            "eligibility": eligible[h],
            "n_days_with_comparable_cell": len(day_fx),
            "n_comparable_day_bucket_cells": sum(1 for hi, st in cells.values() if hi and st),
            "mean_day_effect": float(np.mean(list(day_fx.values()))) if day_fx else None,
            "median_day_effect": float(np.median(list(day_fx.values()))) if day_fx else None,
            "n_comparable_buckets": len(comparable_buckets),
            "n_buckets_positive_diff": sum(1 for d in diffs if d is not None and d > 0),
            "n_buckets_negative_diff": sum(1 for d in diffs if d is not None and d < 0),
            "unweighted_mean_bucket_diff": float(np.mean(diffs)) if diffs else None,
            "buckets": buckets,
            "day_effects": {str(d): v for d, v in sorted(day_fx.items())},
        }

    primary = horizon_block(GATE_PRIMARY_HORIZON)
    cells6 = _build_cells(ts_day, ts_bucket, ts_cov[GATE_PRIMARY_HORIZON], labels)
    observed_days = comparable_day_effects(cells6)
    observed = float(np.mean(list(observed_days.values()))) if observed_days else float("nan")

    rng = random.Random(PERM_SEED)
    hits = 0
    null_means: list[float] = []
    for _ in range(PERM_ITERS):
        shuffled = shuffle_labels_within_buckets(labels, ts_bucket, rng)
        perm_days = comparable_day_effects(
            _build_cells(ts_day, ts_bucket, ts_cov[GATE_PRIMARY_HORIZON], shuffled)
        )
        if not perm_days:
            continue
        m = float(np.mean(list(perm_days.values())))
        null_means.append(m)
        if m >= observed:
            hits += 1
    p = hits / PERM_ITERS if PERM_ITERS else 1.0

    comparable = [r for r in primary["buckets"].values() if r["comparable"]]
    n_pos = sum(1 for r in comparable if r["difference"] is not None and r["difference"] > 0)
    if not observed_days or math.isnan(observed):
        classification = "INCONCLUSIVE"
    elif observed <= 0 or n_pos == 0:
        classification = "REJECTED"
    elif p < 0.05 and n_pos >= max(1, (len(comparable) + 1) // 2):
        classification = "PROMISING"
    else:
        classification = "INCONCLUSIVE"

    return {
        "study": "tradeability_gate_within_bucket_v1",
        "period": {"start": str(DEV_START), "end": str(DEV_END)},
        "hold_out": "NOT READ",
        "validation": "NOT READ",
        "h006": "NOT CREATED",
        "registered": False,
        "live_scanner": "NOT WIRED",
        "high_cut": GATE_HIGH_CUT,
        "primary_horizon": GATE_PRIMARY_HORIZON,
        "n_labeled_timestamps": len(labels),
        "n_high_timestamps": n_high_ts,
        "n_standby_timestamps": n_standby_ts,
        "unlabeled_sparse_timestamps": unlabeled,
        "n_days": len(set(ts_day.values())),
        "primary": primary,
        "permutation": {
            "seed": PERM_SEED,
            "iterations": PERM_ITERS,
            "observed_mean_day_effect": observed,
            "p_one_sided": p,
            "null_mean": float(np.mean(null_means)) if null_means else None,
            "null_p95": float(np.percentile(null_means, 95)) if null_means else None,
            "scheme": (
                "shuffle HIGH/STANDBY labels within each 30-minute bucket, "
                "preserving that bucket's HIGH count; recompute day-level "
                "mean of comparable within-bucket median differences"
            ),
        },
        "secondary": {str(h): horizon_block(h) for h in GATE_SECONDARY_HORIZONS},
        "classification": classification,
        "hypotheses_frozen": {
            "H001": "REJECTED", "H002": "REJECTED", "H003": "REJECTED",
            "H004": "REJECTED", "H005": "REJECTED",
        },
    }


def print_report(result: dict) -> None:
    print("=" * 72)
    print("TRADEABILITY GATE EXPERIMENT  (within-bucket; not a strategy)")
    print(
        f"period {result['period']['start']} .. {result['period']['end']}  "
        f"hold-out={result['hold_out']}  H006={result['h006']}"
    )
    print(f"cut HIGH >= {result['high_cut']}  primary h={result['primary_horizon']}")
    print(
        f"labeled ts HIGH={result['n_high_timestamps']} "
        f"STANDBY={result['n_standby_timestamps']}"
    )
    print(f"CLASSIFICATION: {result['classification']}")
    p = result["primary"]
    print(f"\n-- eligibility h=6 {p['eligibility']}")
    print(
        f"   comparable day-bucket cells={p['n_comparable_day_bucket_cells']}  "
        f"days={p['n_days_with_comparable_cell']}"
    )
    print(
        f"   mean day effect={p['mean_day_effect']}  "
        f"median day effect={p['median_day_effect']}"
    )
    perm = result["permutation"]
    print(
        f"\n-- permutation  observed={perm['observed_mean_day_effect']:.6f}  "
        f"p={perm['p_one_sided']:.4f}  null_mean={perm['null_mean']}  "
        f"null_p95={perm['null_p95']}"
    )
    print("\n-- within-bucket h=6 --")
    print(
        f"{'bucket':<13} {'nH':>7} {'nS':>7} {'medH':>8} {'medS':>8} "
        f"{'diff':>8} {'H>=1x':>7} {'S>=1x':>7} {'H>=2x':>7} {'S>=2x':>7}"
    )
    for row in p["buckets"].values():
        d = row["difference"]
        print(
            f"{row['label']:<13} {row['n_high_obs']:7d} {row['n_standby_obs']:7d} "
            f"{row['median_high']:8.3f} {row['median_standby']:8.3f} "
            f"{(d if d is not None else float('nan')):8.3f} "
            f"{(row['high_ge_1x'] or 0):7.1f} {(row['standby_ge_1x'] or 0):7.1f} "
            f"{(row['high_ge_2x'] or 0):7.1f} {(row['standby_ge_2x'] or 0):7.1f}"
        )
    for h, blk in result["secondary"].items():
        print(
            f"\n-- secondary h={h}  mean day effect={blk['mean_day_effect']}  "
            f"buckets +/- = {blk['n_buckets_positive_diff']}/"
            f"{blk['n_buckets_negative_diff']}"
        )


def main() -> None:
    result = run()
    dest = Path(__file__).resolve().parents[2] / "research_data" / "tradeability_gate_dev.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    def _round(o):
        if isinstance(o, float):
            return None if math.isnan(o) else round(o, 6)
        if isinstance(o, dict):
            return {k: _round(v) for k, v in o.items()}
        if isinstance(o, list):
            return [_round(v) for v in o]
        return o

    dest.write_text(json.dumps(_round(result), indent=2), encoding="utf-8")
    print_report(result)
    print(f"\nwrote {dest}")


if __name__ == "__main__":
    main()
