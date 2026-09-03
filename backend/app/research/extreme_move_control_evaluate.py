"""Exploratory test: does the 1-ATR event fade beyond its session position?

Development only. Hold-out unread. H006 NOT CREATED. No BUY/SELL, no entry
rule, no stops, no targets, no threshold search.

    python -m app.research.extreme_move_control_evaluate

Event population is the frozen 1-ATR bar, restricted here to same-session
predecessors so overnight gaps are a separate question. Control C is a matched
non-event bar (same symbol, day, 30-minute bucket, near-identical session
position). Control A is the direction shuffle. Inference unit is the trading
day.
"""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict

from app.research.episodes import SignalPoint, episode_report, group_episodes
from app.research.extreme_move import (
    DEV_END, DEV_START, EPISODE_SEPARATION_BARS, EXTREME_CUT, FADE_HORIZONS,
    FADE_PRIMARY_HORIZON, assert_development_window, atr_series_for,
    event_direction, extreme_score, is_extreme_event, same_session_predecessor,
    shuffle_directions, signed_future_move,
)
from app.research.extreme_move_control import (
    MATCH_HORIZON, POSITION_BINS, POSITION_CALIPER, MatchStats,
    match_nearest_position, match_within_class, position_class,
    session_positions, tercile_cuts,
)
from app.research.matched_controls import session_bucket
from app.research.store import ResearchStore
from app.services.observation_window import SessionIndex, ist_date

PERM_SEED = 23
PERM_ITERS = 4000
COST_FLOOR_PCT = 0.1831
OUT_PATH = "research_data/extreme_move_control_dev.json"


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _outcome_block(signed: list[float]) -> dict:
    """A-F for one population at one horizon. Signed is in percent."""
    if not signed:
        return {"n": 0}
    rev = [s for s in signed if s < 0]
    cont = [s for s in signed if s > 0]
    flat = len(signed) - len(rev) - len(cont)
    return {
        "n": len(signed),
        "p_reversion_pct": len(rev) / len(signed) * 100,
        "p_continuation_pct": len(cont) / len(signed) * 100,
        "p_flat_pct": flat / len(signed) * 100,
        "median_reversal_magnitude_pct": abs(_median(rev)) if rev else None,
        "median_continuation_magnitude_pct": _median(cont) if cont else None,
        "mean_signed_pct": _mean(signed),
        "median_signed_pct": _median(signed),
    }


def _diff(a: dict, b: dict) -> dict:
    keys = (
        "p_reversion_pct", "p_continuation_pct", "median_reversal_magnitude_pct",
        "median_continuation_magnitude_pct", "mean_signed_pct", "median_signed_pct",
    )
    out = {}
    for k in keys:
        x, y = a.get(k), b.get(k)
        out[k] = (x - y) if (x is not None and y is not None) else None
    return out


def _paired_day_test(pairs: list[tuple], rng: random.Random, iters: int = PERM_ITERS) -> dict:
    """Day-level paired randomisation on (day, event_signed, control_signed).

    Statistic: mean over days of (day mean event − day mean control). The null
    swaps event and control inside each matched pair, which is exactly "the
    event bar is interchangeable with a same-place non-event bar". Days are the
    dependence unit; timestamps are never treated as independent.
    """
    if not pairs:
        return {"days": 0}
    by_day: dict = defaultdict(list)
    for day, ev, ct in pairs:
        by_day[day].append((ev, ct))

    def statistic(flip: bool) -> float:
        day_vals = []
        for rows in by_day.values():
            diffs = []
            for ev, ct in rows:
                if flip and rng.random() < 0.5:
                    ev, ct = ct, ev
                diffs.append(ev - ct)
            day_vals.append(sum(diffs) / len(diffs))
        return sum(day_vals) / len(day_vals)

    observed = statistic(False)
    null = [statistic(True) for _ in range(iters)]
    null.sort()
    n = len(null)
    more_negative = sum(1 for x in null if x <= observed)
    more_extreme = sum(1 for x in null if abs(x) >= abs(observed))
    return {
        "days": len(by_day),
        "pairs": len(pairs),
        "observed_mean_day_diff_pct": observed,
        "null_mean": sum(null) / n,
        "null_p05": null[int(0.05 * (n - 1))],
        "null_p95": null[int(0.95 * (n - 1))],
        "p_one_sided_event_more_reverting": (more_negative + 1) / (n + 1),
        "p_two_sided": (more_extreme + 1) / (n + 1),
        "iterations": n,
        "seed": PERM_SEED,
    }


def _control_a(events: list[dict], rng: random.Random, horizon: int, iters: int = PERM_ITERS) -> dict:
    """Does the event's own sign carry information?

    Keeps the bar, timestamp, day and volatility; shuffles +1/−1 within the day
    preserving counts. Statistic: mean over days of the day-mean signed move.
    """
    by_day: dict = defaultdict(list)
    for e in events:
        pct = e["fwd"].get(horizon)
        if pct is None:
            continue
        by_day[ist_date(e["ts"])].append((pct, e["direction"]))
    if not by_day:
        return {"days": 0}

    def statistic(dirs_by_day) -> float:
        vals = []
        for day, rows in by_day.items():
            dirs = dirs_by_day[day]
            vals.append(sum(signed_future_move(p, d) for (p, _), d in zip(rows, dirs)) / len(rows))
        return sum(vals) / len(vals)

    actual = {day: [d for _, d in rows] for day, rows in by_day.items()}
    observed = statistic(actual)
    null = []
    for _ in range(iters):
        shuffled = {day: shuffle_directions(dirs, rng) for day, dirs in actual.items()}
        null.append(statistic(shuffled))
    null.sort()
    n = len(null)
    return {
        "days": len(by_day),
        "observations": sum(len(v) for v in by_day.values()),
        "observed_mean_day_signed_pct": observed,
        "null_mean": sum(null) / n,
        "null_p05": null[int(0.05 * (n - 1))],
        "null_p95": null[int(0.95 * (n - 1))],
        "p_one_sided_more_reverting": (sum(1 for x in null if x <= observed) + 1) / (n + 1),
        "p_two_sided": (sum(1 for x in null if abs(x) >= abs(observed)) + 1) / (n + 1),
        "iterations": n,
        "seed": PERM_SEED,
    }


def _pctiles(xs: list[float]) -> dict:
    a = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not a:
        return {"n": 0}
    n = len(a)

    def q(p):
        if n == 1:
            return a[0]
        idx = (n - 1) * p / 100
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        return a[lo] * (1 - (idx - lo)) + a[hi] * (idx - lo)

    return {"n": n, "mean": sum(a) / n, "p10": q(10), "p25": q(25),
            "p50": q(50), "p75": q(75), "p90": q(90)}


def run() -> dict:
    assert_development_window(DEV_START, DEV_END)
    store = ResearchStore()
    symbols = store.symbols("5m", "live")
    if not symbols:
        raise RuntimeError("research store has no 5m symbols")

    n_events_all = 0
    n_gap_events = 0
    n_events_same_session = 0
    n_event_infeasible = 0
    n_candidates = 0

    per_symbol: dict[str, dict] = {}
    all_positions: list[float] = []
    event_positions: list[float] = []

    for symbol in symbols:
        candles = store.read(symbol, "5m", "live", start=DEV_START, end=DEV_END)
        if not candles:
            continue
        index = SessionIndex(candles)
        series = atr_series_for(candles)
        pos = session_positions(candles, index)

        events: list[dict] = []
        candidates: list[dict] = []
        for i, c in enumerate(candles):
            score = extreme_score(candles, series, i)
            if score is None:
                continue
            direction = event_direction(candles, i)
            if direction is None:
                continue
            event = is_extreme_event(candles, series, i)
            if event:
                n_events_all += 1
            same_session = same_session_predecessor(index, i)
            if event and not same_session:
                n_gap_events += 1
                continue
            if not same_session:
                continue
            p = pos[i]
            if p is None:
                continue
            # Eligibility is decided here, BEFORE any outcome is computed, and
            # at the longest horizon so the population is identical for h=1..12.
            feasible = index.is_forward_window_valid(i, MATCH_HORIZON)
            if event:
                n_events_same_session += 1
                if not feasible:
                    n_event_infeasible += 1
                    continue
                events.append({
                    "symbol": symbol, "index": i, "ts": c.ts,
                    "day": ist_date(c.ts), "bucket": session_bucket(c.ts),
                    "direction": direction, "score": score, "pos": p,
                    "bar_in_session": i - index.session_start(i),
                })
                event_positions.append(p)
            else:
                if not feasible:
                    continue
                candidates.append({
                    "index": i, "ts": c.ts, "day": ist_date(c.ts),
                    "bucket": session_bucket(c.ts), "pos": p,
                    "bar_in_session": i - index.session_start(i),
                })
                n_candidates += 1
            all_positions.append(p)

        if events:
            per_symbol[symbol] = {
                "candles": candles, "events": events, "candidates": candidates,
            }

    # Position bins come from the predictor-side distribution of eligible bars.
    cuts = tercile_cuts(all_positions, POSITION_BINS)

    rng_tercile = random.Random(PERM_SEED)
    matched: list[dict] = []
    matched_tercile: list[dict] = []
    stats_near = MatchStats()
    stats_terc = MatchStats()

    for symbol, pack in per_symbol.items():
        candles = pack["candles"]
        close = [c.close for c in candles]
        by_day_events: dict = defaultdict(list)
        by_day_pool: dict = defaultdict(list)
        for e in pack["events"]:
            by_day_events[e["day"]].append(e)
        for c in pack["candidates"]:
            by_day_pool[c["day"]].append(c)

        for day, evs in by_day_events.items():
            pool_rows = by_day_pool.get(day, [])
            pool_pos: dict[int, list[tuple[int, float]]] = defaultdict(list)
            pool_cls: dict[tuple[int, int], list[int]] = defaultdict(list)
            pos_of: dict[int, float] = {}
            bis_of: dict[int, int] = {}
            bucket_of: dict[int, int] = {}
            for r in pool_rows:
                pool_pos[r["bucket"]].append((r["index"], r["pos"]))
                cls = position_class(r["pos"], cuts)
                pool_cls[(r["bucket"], cls)].append(r["index"])
                pos_of[r["index"]] = r["pos"]
                bis_of[r["index"]] = r["bar_in_session"]
                bucket_of[r["index"]] = r["bucket"]

            evs.sort(key=lambda e: e["index"])
            near, s1 = match_nearest_position(
                [(e["index"], e["bucket"], e["pos"]) for e in evs],
                pool_pos, POSITION_CALIPER,
            )
            terc, s2 = match_within_class(
                [(e["index"], e["bucket"], position_class(e["pos"], cuts)) for e in evs],
                pool_cls, rng_tercile,
            )
            for attr in ("exact", "widened", "skipped_no_pool", "skipped_caliper"):
                setattr(stats_near, attr, getattr(stats_near, attr) + getattr(s1, attr))
                setattr(stats_terc, attr, getattr(stats_terc, attr) + getattr(s2, attr))

            for e in evs:
                fwd_e = {}
                for h in FADE_HORIZONS:
                    j = e["index"] + h
                    base = close[e["index"]]
                    fwd_e[h] = (close[j] / base - 1.0) * 100 if base > 0 else None
                e["fwd"] = fwd_e

                for kind, table, sink in (
                    ("near", near, matched), ("tercile", terc, matched_tercile),
                ):
                    ci = table.get(e["index"])
                    if ci is None:
                        continue
                    base_c = close[ci]
                    if base_c <= 0:
                        continue
                    fwd_c = {
                        h: (close[ci + h] / base_c - 1.0) * 100 for h in FADE_HORIZONS
                    }
                    sink.append({
                        "symbol": symbol, "day": day, "bucket": e["bucket"],
                        "direction": e["direction"],
                        "event_index": e["index"], "control_index": ci,
                        "event_pos": e["pos"], "control_pos": pos_of[ci],
                        "event_bis": e["bar_in_session"], "control_bis": bis_of[ci],
                        "control_bucket": bucket_of[ci],
                        "event_fwd": fwd_e, "control_fwd": fwd_c,
                    })

    all_events = [e for p in per_symbol.values() for e in p["events"]]

    # ---- outcome tables --------------------------------------------------
    def signed_lists(rows: list[dict], h: int) -> tuple[list[float], list[float]]:
        ev, ct = [], []
        for r in rows:
            e_pct, c_pct = r["event_fwd"].get(h), r["control_fwd"].get(h)
            if e_pct is None or c_pct is None:
                continue
            ev.append(signed_future_move(e_pct, r["direction"]))
            ct.append(signed_future_move(c_pct, r["direction"]))
        return ev, ct

    horizons: dict = {}
    for h in FADE_HORIZONS:
        ev, ct = signed_lists(matched, h)
        ev_block = _outcome_block(ev)
        ct_block = _outcome_block(ct)
        rng = random.Random(PERM_SEED + h)
        pairs = [
            (r["day"],
             signed_future_move(r["event_fwd"][h], r["direction"]),
             signed_future_move(r["control_fwd"][h], r["direction"]))
            for r in matched
            if r["event_fwd"].get(h) is not None and r["control_fwd"].get(h) is not None
        ]
        ev_t, ct_t = signed_lists(matched_tercile, h)
        horizons[str(h)] = {
            "event": ev_block,
            "control_c": ct_block,
            "event_minus_control_c": _diff(ev_block, ct_block),
            "day_level_paired_test": _paired_day_test(pairs, rng),
            "tercile_match_robustness": {
                "event": _outcome_block(ev_t),
                "control_c": _outcome_block(ct_t),
                "event_minus_control_c": _diff(_outcome_block(ev_t), _outcome_block(ct_t)),
            },
        }

    control_a = {
        str(h): _control_a(all_events, random.Random(PERM_SEED + 100 + h), h)
        for h in FADE_HORIZONS
    }

    # ---- match quality ---------------------------------------------------
    gaps = [abs(r["event_pos"] - r["control_pos"]) for r in matched]
    match_quality = {
        "matcher": "nearest session position within (symbol, day, 30-min bucket)",
        "caliper": POSITION_CALIPER,
        "max_widen_buckets": 1,
        "without_replacement": True,
        "nearest": stats_near.as_dict(),
        "tercile": stats_terc.as_dict(),
        "tercile_cuts": list(cuts),
        "abs_position_gap": _pctiles(gaps),
        "event_position": _pctiles([r["event_pos"] for r in matched]),
        "control_position": _pctiles([r["control_pos"] for r in matched]),
        "mean_bar_in_session_event": _mean([float(r["event_bis"]) for r in matched]),
        "mean_bar_in_session_control": _mean([float(r["control_bis"]) for r in matched]),
        "mean_bucket_offset": _mean(
            [float(r["control_bucket"] - r["bucket"]) for r in matched]
        ),
        "note": (
            "Mean bar-in-session is reported to show controls did not drift "
            "earlier: both populations were filtered for a valid 12-bar forward "
            "window before matching, never after."
        ),
    }

    # ---- episodes on the same-session event population --------------------
    points = [
        SignalPoint(e["symbol"], e["index"], e["ts"], "UP" if e["direction"] > 0 else "DOWN")
        for e in all_events
    ]
    ep = episode_report(points, EPISODE_SEPARATION_BARS)
    sizes = [x.size for x in group_episodes(points, EPISODE_SEPARATION_BARS)]
    if sizes:
        s = sorted(sizes)
        ep["median_episode_size"] = float(statistics.median(sizes))
        ep["p90_episode_size"] = float(s[int(round(0.9 * (len(s) - 1)))])

    primary = horizons[str(FADE_PRIMARY_HORIZON)]
    ev_med_rev = primary["event"].get("median_reversal_magnitude_pct")
    return {
        "study": "extreme_move_mechanical_confound_v1",
        "period": {"start": str(DEV_START), "end": str(DEV_END)},
        "hold_out": "NOT READ",
        "validation": "NOT READ",
        "h006": "NOT CREATED",
        "frozen": {
            "H001": "REJECTED", "H002": "REJECTED", "H003": "REJECTED",
            "H004": "REJECTED", "H005": "REJECTED",
            "tradeability_gate": "REJECTED",
        },
        "definition": {
            "event": "abs(close[t]-close[t-1]) / ATR(14)[t-1] >= 1.0, same-session close[t-1]",
            "cut": EXTREME_CUT,
            "threshold_search": "NOT PERFORMED",
            "horizons": list(FADE_HORIZONS),
            "primary_horizon": FADE_PRIMARY_HORIZON,
            "signed_future_move": "(close[t+h]/close[t]-1)*100 * direction; negative = reversion",
            "eligibility": f"SessionIndex.is_forward_window_valid(i, {MATCH_HORIZON}) before sampling",
        },
        "population": {
            "events_all": n_events_all,
            "events_opening_gap_excluded": n_gap_events,
            "opening_gap_excluded_pct": (n_gap_events / n_events_all * 100) if n_events_all else None,
            "events_same_session": n_events_same_session,
            "events_dropped_horizon_infeasible": n_event_infeasible,
            "events_eligible": len(all_events),
            "control_candidates_eligible": n_candidates,
            "matched_pairs_nearest": len(matched),
            "matched_pairs_tercile": len(matched_tercile),
        },
        "match_quality": match_quality,
        "horizons": horizons,
        "control_a_direction_shuffle": control_a,
        "episodes": ep,
        "economics": {
            "round_trip_floor_pct": COST_FLOOR_PCT,
            "used_in_definition": False,
            "primary_event_median_reversal_magnitude_pct": ev_med_rev,
            "coverage_of_floor": (ev_med_rev / COST_FLOOR_PCT) if ev_med_rev else None,
            "note": (
                "Median reversal magnitude is the size of the moves that "
                "happened to go the other way; it is not an expected return, "
                "not capturable, and not profit."
            ),
        },
    }


def main() -> None:
    r = run()
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(r, f, indent=2)
    p = r["population"]
    print(f"extreme-move Control C  {r['period']['start']}..{r['period']['end']}  "
          f"hold-out={r['hold_out']}  H006={r['h006']}")
    print(f"  events_all={p['events_all']:,}  gap_excluded={p['events_opening_gap_excluded']:,} "
          f"({p['opening_gap_excluded_pct']:.1f}%)  same_session={p['events_same_session']:,}")
    print(f"  horizon-infeasible dropped={p['events_dropped_horizon_infeasible']:,}  "
          f"eligible={p['events_eligible']:,}  control pool={p['control_candidates_eligible']:,}")
    mq = r["match_quality"]
    print(f"  matched(nearest)={p['matched_pairs_nearest']:,}  "
          f"rate={mq['nearest']['match_rate_pct']:.1f}%  exact={mq['nearest']['exact_share_pct']:.1f}%  "
          f"skip_caliper={mq['nearest']['skipped_caliper']:,}")
    print(f"  |pos gap| p50={mq['abs_position_gap']['p50']:.4f}  p90={mq['abs_position_gap']['p90']:.4f}  "
          f"event_pos p50={mq['event_position']['p50']:.3f}  control_pos p50={mq['control_position']['p50']:.3f}")
    print(f"  mean bar-in-session event={mq['mean_bar_in_session_event']:.1f}  "
          f"control={mq['mean_bar_in_session_control']:.1f}  "
          f"bucket offset={mq['mean_bucket_offset']:+.3f}")
    for h in ("1", "3", "6", "12"):
        blk = r["horizons"][h]
        e, c, d = blk["event"], blk["control_c"], blk["event_minus_control_c"]
        t = blk["day_level_paired_test"]
        star = " <-- PRIMARY" if h == str(FADE_PRIMARY_HORIZON) else ""
        print(f"  h={h:>2}  n={e['n']:,}{star}")
        print(f"      EVENT    P(rev)={e['p_reversion_pct']:.2f}%  med|rev|={e['median_reversal_magnitude_pct']:.4f}  "
              f"med_cont={e['median_continuation_magnitude_pct']:.4f}  mean_signed={e['mean_signed_pct']:+.5f}")
        print(f"      CONTROL  P(rev)={c['p_reversion_pct']:.2f}%  med|rev|={c['median_reversal_magnitude_pct']:.4f}  "
              f"med_cont={c['median_continuation_magnitude_pct']:.4f}  mean_signed={c['mean_signed_pct']:+.5f}")
        print(f"      DIFF     P(rev)={d['p_reversion_pct']:+.2f}pp  mean_signed={d['mean_signed_pct']:+.5f}  "
              f"day_diff={t['observed_mean_day_diff_pct']:+.5f}  p1={t['p_one_sided_event_more_reverting']:.4f}  "
              f"p2={t['p_two_sided']:.4f}")
    print("  Control A (direction shuffle):")
    for h in ("1", "3", "6", "12"):
        a = r["control_a_direction_shuffle"][h]
        print(f"      h={h:>2}  obs={a['observed_mean_day_signed_pct']:+.5f}  "
              f"null={a['null_mean']:+.5f}  p1={a['p_one_sided_more_reverting']:.4f}  "
              f"p2={a['p_two_sided']:.4f}")
    ep = r["episodes"]
    print(f"  episodes={ep['episodes']:,}  events/episode={ep['signals_per_episode']}  "
          f"median_size={ep['median_episode_size']}  p90={ep['p90_episode_size']}  days={ep['signal_days']}")
    ec = r["economics"]
    print(f"  economics: med|rev| h=6 = {ec['primary_event_median_reversal_magnitude_pct']:.4f}%  "
          f"floor={ec['round_trip_floor_pct']}%  coverage={ec['coverage_of_floor']:.2f}x")
    print(f"  wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
