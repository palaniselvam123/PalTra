"""Input-only audit of candidate tradeability-gate variables.

No forward returns. No BUY/SELL. No H006. Hold-out unread.

    python -m app.research.tradeability_gate_audit
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict

import numpy as np

from app.research.matched_controls import bucket_label, session_bucket
from app.research.store import ResearchStore
from app.research.tradeability import (
    DEV_END, DEV_START, LOOKBACK_BARS, MKT_REL_ATR_P25, MKT_REL_ATR_P75,
    assert_development_window, atr_series_for, classify_vol_regime,
    contemporaneous_return_pct, gap_over_atr, rel_atr_at,
)
from app.services.observation_window import SessionIndex, ist_date

MIN_CS = 50


def _pearson(a: np.ndarray, b: np.ndarray, min_n: int = 30) -> float | None:
    m = np.isfinite(a) & np.isfinite(b)
    if int(m.sum()) < min_n:
        return None
    if float(np.std(a[m])) == 0 or float(np.std(b[m])) == 0:
        return None
    return float(np.corrcoef(a[m], b[m])[0, 1])


def _eta_squared(values: np.ndarray, groups: np.ndarray) -> float | None:
    """Share of variance in `values` that sits between `groups`."""
    m = np.isfinite(values)
    v, g = values[m], groups[m]
    if v.size < 2:
        return None
    grand = float(np.mean(v))
    ss_tot = float(np.sum((v - grand) ** 2))
    if ss_tot == 0:
        return None
    ss_b = 0.0
    for label in np.unique(g):
        part = v[g == label]
        ss_b += part.size * (float(np.mean(part)) - grand) ** 2
    return ss_b / ss_tot


def run() -> dict:
    assert_development_window(DEV_START, DEV_END)
    store = ResearchStore()
    symbols = store.symbols("5m", "live")

    cs_rel: dict[int, list[float]] = defaultdict(list)
    cs_r12: dict[int, list[float]] = defaultdict(list)
    cs_rvol: dict[int, list[float]] = defaultdict(list)
    gaps: dict[int, list[float]] = defaultdict(list)

    for symbol in symbols:
        candles = store.read(symbol, "5m", "live", start=DEV_START, end=DEV_END)
        if not candles:
            continue
        index = SessionIndex(candles)
        series = atr_series_for(candles)
        close = [c.close for c in candles]
        for i, c in enumerate(candles):
            ra = rel_atr_at(candles, series, i)
            if ra is not None:
                cs_rel[c.ts].append(ra)
            rr = contemporaneous_return_pct(candles, index, i)
            if rr is not None:
                cs_r12[c.ts].append(rr)
            if (
                i >= LOOKBACK_BARS
                and index.session_start(i) <= i - LOOKBACK_BARS
                and close[i - LOOKBACK_BARS] > 0
            ):
                rets = [
                    (close[j] / close[j - 1] - 1.0) * 100
                    for j in range(i - LOOKBACK_BARS + 1, i + 1)
                    if close[j - 1] > 0
                ]
                if rets:
                    cs_rvol[c.ts].append(float(np.std(rets, ddof=0)))
            g = gap_over_atr(candles, index, series, i)
            if g is not None:
                gaps[c.ts].append(g)

    rows = []
    for ts, rels in cs_rel.items():
        if len(rels) < MIN_CS:
            continue
        r12 = cs_r12.get(ts, [])
        rvol = cs_rvol.get(ts, [])
        med_rel = float(statistics.median(rels))
        row = {
            "ts": ts,
            "bucket": session_bucket(ts),
            "day": str(ist_date(ts)),
            "mkt_rel_atr": med_rel,
            "regime": classify_vol_regime(med_rel, MKT_REL_ATR_P25, MKT_REL_ATR_P75),
            "cs_disp": (
                float(np.median(np.abs(np.array(r12) - np.median(r12))))
                if len(r12) >= MIN_CS else math.nan
            ),
            "breadth": (
                float(np.mean(np.array(r12) > 0) * 100)
                if len(r12) >= MIN_CS else math.nan
            ),
            "mkt_rvol": float(statistics.median(rvol)) if len(rvol) >= MIN_CS else math.nan,
            "gap_atr": float(statistics.median(gaps[ts])) if ts in gaps else math.nan,
            "n_names": len(rels),
        }
        rows.append(row)

    mkt = np.array([r["mkt_rel_atr"] for r in rows])
    disp = np.array([r["cs_disp"] for r in rows])
    rvol = np.array([r["mkt_rvol"] for r in rows])
    breadth = np.array([r["breadth"] for r in rows])
    gap = np.array([r["gap_atr"] for r in rows])
    bucket = np.array([r["bucket"] for r in rows], dtype=int)
    regime = np.array([r["regime"] for r in rows])

    by_bucket = {}
    for b in sorted(set(int(x) for x in bucket)):
        mask = bucket == b
        high = int(np.sum((regime == "HIGH") & mask))
        by_bucket[int(b)] = {
            "label": bucket_label(b),
            "n": int(mask.sum()),
            "mkt_rel_atr_p25": float(np.percentile(mkt[mask], 25)),
            "mkt_rel_atr_p50": float(np.median(mkt[mask])),
            "mkt_rel_atr_p75": float(np.percentile(mkt[mask], 75)),
            "share_high_pct": float(high / mask.sum() * 100) if mask.any() else None,
            "share_low_pct": float(np.mean(regime[mask] == "LOW") * 100),
            "cs_disp_p50": float(np.nanmedian(disp[mask])) if np.isfinite(disp[mask]).any() else None,
        }

    high_mask = regime == "HIGH"
    high_bucket_share = {}
    n_high = int(high_mask.sum())
    if n_high:
        for b in sorted(set(int(x) for x in bucket)):
            high_bucket_share[bucket_label(b)] = float(
                np.mean(bucket[high_mask] == b) * 100
            )

    return {
        "period": {"start": str(DEV_START), "end": str(DEV_END)},
        "hold_out": "NOT READ",
        "n_timestamps": len(rows),
        "n_days": len({r["day"] for r in rows}),
        "regime_counts": {
            k: int(np.sum(regime == k)) for k in ("LOW", "NORMAL", "HIGH")
        },
        "frozen_cuts": {"p25": MKT_REL_ATR_P25, "p75": MKT_REL_ATR_P75},
        "redundancy": {
            "mkt_rel_atr_vs_cs_disp": _pearson(mkt, disp),
            "mkt_rel_atr_vs_rvol": _pearson(mkt, rvol),
            "mkt_rel_atr_vs_breadth": _pearson(mkt, breadth),
            "mkt_rel_atr_vs_gap": _pearson(mkt, gap),
            "mkt_rel_atr_vs_bucket": _pearson(mkt, bucket.astype(float)),
            "cs_disp_vs_rvol": _pearson(disp, rvol),
            "cs_disp_vs_bucket": _pearson(disp, bucket.astype(float)),
            "rvol_vs_bucket": _pearson(rvol, bucket.astype(float)),
            "breadth_vs_bucket": _pearson(breadth, bucket.astype(float)),
        },
        "missingness": {
            "cs_disp_pct": float(np.mean(~np.isfinite(disp)) * 100),
            "rvol_pct": float(np.mean(~np.isfinite(rvol)) * 100),
            "gap_pct": float(np.mean(~np.isfinite(gap)) * 100),
            "note": "cs_disp and 12-bar realized vol require a same-session 12-bar lookback, so they are missing for the first hour. gap/ATR is defined only on the session-open bar.",
        },
        "tod_vs_vol": {
            "eta2_mkt_rel_atr_on_bucket": _eta_squared(mkt, bucket),
            "eta2_cs_disp_on_bucket": _eta_squared(disp, bucket),
            "by_bucket": by_bucket,
            "where_high_timestamps_sit_pct": high_bucket_share,
        },
        "h006": "NOT CREATED",
        "forward_test": "NOT RUN",
    }


def print_report(result: dict) -> None:
    print("=" * 72)
    print("TRADEABILITY GATE — INPUT-ONLY AUDIT  (no forwards, not a strategy)")
    print(f"period {result['period']['start']} .. {result['period']['end']}")
    print(f"timestamps={result['n_timestamps']}  days={result['n_days']}  "
          f"hold-out={result['hold_out']}  H006={result['h006']}")
    print(f"frozen cuts p25={result['frozen_cuts']['p25']} p75={result['frozen_cuts']['p75']}")
    print(f"regime counts {result['regime_counts']}")
    print("-- redundancy (timestamp-level Pearson) --")
    for k, v in result["redundancy"].items():
        print(f"  {k:32} {v if v is None else round(v, 4)}")
    print("-- missingness --")
    print(f"  {result['missingness']}")
    tod = result["tod_vs_vol"]
    print(f"-- time-of-day vs vol  eta2(mkt_rel_atr|bucket)={tod['eta2_mkt_rel_atr_on_bucket']}")
    print(f"   eta2(cs_disp|bucket)={tod['eta2_cs_disp_on_bucket']}")
    print(f"   HIGH timestamps by bucket % {tod['where_high_timestamps_sit_pct']}")
    for b, row in tod["by_bucket"].items():
        print(
            f"  {row['label']} n={row['n']:4d}  rel_atr p25={row['mkt_rel_atr_p25']:.5f} "
            f"p50={row['mkt_rel_atr_p50']:.5f} p75={row['mkt_rel_atr_p75']:.5f}  "
            f"HIGH={row['share_high_pct']:.1f}% LOW={row['share_low_pct']:.1f}%  "
            f"cs_disp p50={row['cs_disp_p50']}"
        )


def main() -> None:
    result = run()
    print_report(result)


if __name__ == "__main__":
    main()
