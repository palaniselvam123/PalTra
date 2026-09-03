"""H005 v1 forward evaluation — frozen rule, no parameter changes.

Gate operationalization is inherited from H003, whose registry gate text this
rule copies. Fixed here before any return is computed:

    Gate 0  n >= 300 eligible signals AND >= 25 signal-days in development
            (checked on every horizon; UNDERPOWERED if any fails)
    Gate 1  mean(net_move_pct_signal) - mean(net_move_pct_ControlA) > 0
            at >= 2 of 3 horizons, AND day-block permutation p < 0.05 at >= 1
    Gate 2  the same edge vs Control C > 0 at the horizons that passed Gate 1
    Gate 3  sign of those edges preserved in validation (not run unless 0-2 pass)
    Gate 4  hold-out — not run
    Gate 5  economic — not assessed unless directional gates pass

Seeds, horizons, cuts, universe and split are taken from the registered rule.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import statistics
from collections import defaultdict

from app.core.market_clock import IST
from app.research.compression_expansion import (
    C2_MAX, C3_MIN, HORIZONS, PARAMS, compression_without_expansion,
    generate_detailed,
)
from app.research.matched_controls import MatchStats, TimeMatchedSampler
from app.research.store import ResearchStore
from app.services.hypothesis_lab import block_permutation_means
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex, forward_return_pct, ist_date

DEV_START = dt.date(2026, 6, 1)
DEV_END = dt.date(2026, 7, 23)
VAL_START = dt.date(2026, 7, 24)
VAL_END = dt.date(2026, 8, 10)

CONTROL_A_SEED = 17
CONTROL_C_SEED = 19
PERM_SEED = 13
PERM_ITERS = 4000


def _cohens_d(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(b) < 2:
        return math.nan
    va = statistics.pvariance(a)
    vb = statistics.pvariance(b)
    n = len(a) + len(b)
    pooled = math.sqrt(((len(a) * va) + (len(b) * vb)) / n)
    if pooled == 0:
        return math.nan
    return (statistics.fmean(a) - statistics.fmean(b)) / pooled


def _mfe_mae_pct(candles: list[OHLCV], i: int, side: str, horizon: int) -> tuple[float, float]:
    """Secondary excursions over [i+1, i+horizon], matching the forward window."""
    entry = candles[i].close
    window = candles[i + 1 : i + horizon + 1]
    if side == "BUY":
        mfe = max(b.high for b in window) - entry
        mae = entry - min(b.low for b in window)
    else:
        mfe = entry - min(b.low for b in window)
        mae = max(b.high for b in window) - entry
    return 100.0 * mfe / entry, 100.0 * mae / entry


def evaluate_period(event_start: dt.date, event_end: dt.date, load_end: dt.date) -> dict:
    """Evaluate events whose bar date is in [event_start, event_end].

    Candles are loaded from DEV_START through `load_end` so ATR is causal.
    Hold-out candles are never requested: load_end must be <= VAL_END.
    """
    if load_end > VAL_END:
        raise ValueError("refusing to load past the validation window; hold-out is unread")

    store = ResearchStore()
    symbols = store.symbols("5m", "live")
    rng_a = random.Random(CONTROL_A_SEED)
    rng_c = random.Random(CONTROL_C_SEED)

    per_h = {
        h: {
            "sig": [], "sig_side": [], "sig_day": [],
            "a": [], "a_day": [],
            "c": [], "c_day": [],
            "mfe": [], "mae": [],
            "buy": 0, "sell": 0,
            "skipped_horizon": 0, "skipped_c": 0,
            "missing_return": 0,
        }
        for h in HORIZONS
    }
    stats_c = {h: MatchStats() for h in HORIZONS}

    n_raw_events = 0
    event_days = set()
    event_symbols = set()
    n_symbols_loaded = 0

    for si, symbol in enumerate(sorted(symbols)):
        candles = store.read(symbol, "5m", "live", start=DEV_START, end=load_end)
        if not candles:
            continue
        n_symbols_loaded += 1
        index = SessionIndex(candles)
        events = [
            e for e in generate_detailed(symbol, candles, PARAMS)
            if event_start <= ist_date(e.ts) <= event_end
        ]
        n_raw_events += len(events)
        pool = compression_without_expansion(candles, PARAMS)
        samplers = {}
        for h in HORIZONS:
            samplers[h] = TimeMatchedSampler(candles, pool, h, session_index=index)
            stats_c[h].population += samplers[h].stats_population
            stats_c[h].horizon_eligible += samplers[h].stats_eligible

        for e in events:
            event_days.add(ist_date(e.ts))
            event_symbols.add(symbol)
            day = ist_date(e.ts)
            for h in HORIZONS:
                rec = per_h[h]
                if not index.is_forward_window_valid(e.index, h):
                    rec["skipped_horizon"] += 1
                    continue
                ret = forward_return_pct(candles, index, e.index, e.side, h)
                if ret is None:
                    rec["missing_return"] += 1
                    continue
                rec["sig"].append(ret)
                rec["sig_side"].append(e.side)
                rec["sig_day"].append(day)
                if e.side == "BUY":
                    rec["buy"] += 1
                else:
                    rec["sell"] += 1
                mfe, mae = _mfe_mae_pct(candles, e.index, e.side, h)
                rec["mfe"].append(mfe)
                rec["mae"].append(mae)

                a_side = rng_a.choice(["BUY", "SELL"])
                a_ret = forward_return_pct(candles, index, e.index, a_side, h)
                if a_ret is None:
                    rec["missing_return"] += 1
                else:
                    rec["a"].append(a_ret)
                    rec["a_day"].append(day)

                st = stats_c[h]
                pick = samplers[h].sample(e.index, e.side, rng_c, st)
                if pick is None:
                    rec["skipped_c"] += 1
                    continue
                c_ret = forward_return_pct(candles, index, pick, e.side, h)
                if c_ret is None:
                    rec["skipped_c"] += 1
                    continue
                rec["c"].append(c_ret)
                rec["c_day"].append(ist_date(candles[pick].ts))

        if (si + 1) % 25 == 0:
            print(f"  {si+1}/{len(symbols)} symbols  events={n_raw_events}", flush=True)

    out = {
        "event_start": event_start.isoformat(),
        "event_end": event_end.isoformat(),
        "load_end": load_end.isoformat(),
        "hold_out": "NOT READ",
        "symbols_loaded": n_symbols_loaded,
        "raw_events": n_raw_events,
        "event_days": len(event_days),
        "event_symbols": len(event_symbols),
        "c2_max": C2_MAX,
        "c3_min": C3_MIN,
        "horizons": {},
    }
    for h in HORIZONS:
        rec = per_h[h]
        sig_by = defaultdict(list)
        a_by = defaultdict(list)
        c_by = defaultdict(list)
        for v, d in zip(rec["sig"], rec["sig_day"]):
            sig_by[d].append(v)
        for v, d in zip(rec["a"], rec["a_day"]):
            a_by[d].append(v)
        for v, d in zip(rec["c"], rec["c_day"]):
            c_by[d].append(v)

        edge_a, p_a = block_permutation_means(sig_by, a_by, PERM_ITERS, PERM_SEED)
        edge_c, p_c = block_permutation_means(sig_by, c_by, PERM_ITERS, PERM_SEED + h)

        sig_mean = statistics.fmean(rec["sig"]) if rec["sig"] else math.nan
        a_mean = statistics.fmean(rec["a"]) if rec["a"] else math.nan
        c_mean = statistics.fmean(rec["c"]) if rec["c"] else math.nan
        mfe = statistics.fmean(rec["mfe"]) if rec["mfe"] else math.nan
        mae = statistics.fmean(rec["mae"]) if rec["mae"] else math.nan
        days = sorted(set(sig_by) | set(a_by) | set(c_by))

        out["horizons"][h] = {
            "n_signal": len(rec["sig"]),
            "n_control_a": len(rec["a"]),
            "n_control_c": len(rec["c"]),
            "buy": rec["buy"],
            "sell": rec["sell"],
            "signal_days": len(set(rec["sig_day"])),
            "day_blocks": len(days),
            "skipped_horizon": rec["skipped_horizon"],
            "skipped_control_c": rec["skipped_c"],
            "missing_return": rec["missing_return"],
            "signal_mean": sig_mean,
            "control_a_mean": a_mean,
            "control_c_mean": c_mean,
            "edge_vs_a": edge_a,
            "p_vs_a": p_a,
            "d_vs_a": _cohens_d(rec["sig"], rec["a"]),
            "edge_vs_c": edge_c,
            "p_vs_c": p_c,
            "d_vs_c": _cohens_d(rec["sig"], rec["c"]),
            "mfe_pct": mfe,
            "mae_pct": mae,
            "mfe_mae_ratio": (mfe / mae) if mae and mae > 0 else math.nan,
            "match_c": stats_c[h].as_dict(),
        }
    return out


def print_period(label: str, result: dict) -> None:
    print(f"\n========== {label} ==========")
    print(f"events {result['event_start']} .. {result['event_end']}")
    print(f"candles loaded through {result['load_end']}; hold-out={result['hold_out']}")
    print(
        f"raw events={result['raw_events']:,}  days={result['event_days']}  "
        f"symbols={result['event_symbols']} / {result['symbols_loaded']}"
    )
    print(
        f"{'h':>4} {'n':>7} {'days':>5} {'BUY':>6} {'SELL':>6} "
        f"{'sig%':>8} {'A%':>8} {'edgeA':>8} {'pA':>7} {'dA':>7} "
        f"{'C%':>8} {'edgeC':>8} {'pC':>7} {'dC':>7} {'skipH':>6} {'skipC':>6}"
    )
    for h, r in result["horizons"].items():
        print(
            f"{h:4d} {r['n_signal']:7d} {r['signal_days']:5d} {r['buy']:6d} {r['sell']:6d} "
            f"{r['signal_mean']:+8.4f} {r['control_a_mean']:+8.4f} {r['edge_vs_a']:+8.4f} "
            f"{r['p_vs_a']:7.4f} {r['d_vs_a']:+7.3f} "
            f"{r['control_c_mean']:+8.4f} {r['edge_vs_c']:+8.4f} {r['p_vs_c']:7.4f} {r['d_vs_c']:+7.3f} "
            f"{r['skipped_horizon']:6d} {r['skipped_control_c']:6d}"
        )


def gate_0(dev: dict) -> tuple[bool, str]:
    reasons = []
    ok = True
    for h, r in dev["horizons"].items():
        if r["n_signal"] < 300 or r["signal_days"] < 25:
            ok = False
            reasons.append(f"h={h} n={r['n_signal']} days={r['signal_days']}")
    return ok, ("PASS" if ok else "UNDERPOWERED: " + "; ".join(reasons))


def gate_1(dev: dict) -> tuple[bool, list[int], str]:
    pos = [h for h, r in dev["horizons"].items() if r["edge_vs_a"] > 0]
    sig = [h for h, r in dev["horizons"].items() if r["edge_vs_a"] > 0 and r["p_vs_a"] < 0.05]
    ok = len(pos) >= 2 and len(sig) >= 1
    return ok, pos, f"positive at {pos}; p<0.05 at {sig}"


def gate_2(dev: dict, gate1_horizons: list[int]) -> tuple[bool, str]:
    if not gate1_horizons:
        return False, "not reached"
    edges = {h: dev["horizons"][h]["edge_vs_c"] for h in gate1_horizons}
    ok = all(v > 0 for v in edges.values())
    return ok, f"Control C edge at Gate-1 horizons: {edges}"
