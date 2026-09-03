"""Input-only census of the frozen 1-ATR extreme-move event.

No forward returns. No BUY/SELL. No H006. Hold-out unread.

    python -m app.research.extreme_move_audit
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict

from app.research.episodes import SignalPoint, episode_report, group_episodes
from app.research.extreme_move import (
    DEV_END, DEV_START, EPISODE_SEPARATION_BARS, EXTREME_CUT, FADE_HORIZONS,
    FADE_HORIZON_RATIONALE, FADE_PRIMARY_HORIZON, MKT_REL_ATR_P25, MKT_REL_ATR_P75,
    assert_development_window, atr_series_for, event_direction, extreme_score,
    is_extreme_event, opposite_session_room, same_session_predecessor,
    trailing_ok_volume,
)
from app.research.matched_controls import bucket_label, session_bucket
from app.research.store import ResearchStore
from app.research.tradeability import classify_vol_regime, rel_atr_at
from app.services.observation_window import SessionIndex, ist_date

MIN_CS = 50
OUT_PATH = "research_data/extreme_move_audit_dev.json"


def _pctiles(xs: list[float]) -> dict:
    a = [x for x in xs if x is not None and math.isfinite(x)]
    if not a:
        return {"n": 0}
    a.sort()
    n = len(a)

    def q(p: float) -> float:
        if n == 1:
            return a[0]
        idx = (n - 1) * p / 100.0
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return a[lo] * (1 - frac) + a[hi] * frac

    return {
        "n": n,
        "mean": sum(a) / n,
        "p10": q(10),
        "p25": q(25),
        "p50": q(50),
        "p75": q(75),
        "p90": q(90),
        "p95": q(95),
    }


def _share(n: int, d: int) -> float | None:
    if d == 0:
        return None
    return n / d * 100.0


def run() -> dict:
    assert_development_window(DEV_START, DEV_END)
    store = ResearchStore()
    symbols = store.symbols("5m", "live")
    if not symbols:
        raise RuntimeError("research store has no 5m symbols")

    # Pass 1: per-timestamp median rel-ATR (regime is a timestamp property).
    cs_rel: dict[int, list[float]] = defaultdict(list)
    loaded: list[str] = []
    per_symbol: dict[str, dict] = {}

    for symbol in symbols:
        candles = store.read(symbol, "5m", "live", start=DEV_START, end=DEV_END)
        if not candles:
            continue
        unknown = store.unknown_volume_timestamps(symbol, "5m", "live")
        index = SessionIndex(candles)
        series = atr_series_for(candles)
        for i, c in enumerate(candles):
            ra = rel_atr_at(candles, series, i)
            if ra is not None:
                cs_rel[c.ts].append(ra)
        per_symbol[symbol] = {
            "candles": candles,
            "unknown": unknown,
            "index": index,
            "series": series,
        }
        loaded.append(symbol)

    mkt_rel_of: dict[int, float] = {}
    regime_of: dict[int, str] = {}
    for ts, xs in cs_rel.items():
        if len(xs) < MIN_CS:
            continue
        med = statistics.median(xs)
        mkt_rel_of[ts] = med
        regime_of[ts] = classify_vol_regime(med, MKT_REL_ATR_P25, MKT_REL_ATR_P75)

    # Pass 2: events and the eligible-bar denominator (score defined, not flat).
    events: list[dict] = []
    points: list[SignalPoint] = []
    n_bars = 0
    n_score_defined = 0
    n_flat = 0
    n_scored_nonflat = 0
    bucket_eligible: Counter[int] = Counter()
    bucket_events: Counter[int] = Counter()
    regime_eligible: Counter[str] = Counter()
    regime_events: Counter[str] = Counter()
    days: set = set()
    scores_all: list[float] = []
    consecutive_pairs = 0

    for symbol, pack in per_symbol.items():
        candles = pack["candles"]
        unknown = pack["unknown"]
        index = pack["index"]
        series = pack["series"]
        volumes = [c.volume for c in candles]
        timestamps = [c.ts for c in candles]
        last_event_i: int | None = None

        for i, c in enumerate(candles):
            n_bars += 1
            days.add(ist_date(c.ts))
            score = extreme_score(candles, series, i)
            if score is None:
                continue
            n_score_defined += 1
            direction = event_direction(candles, i)
            if direction is None:
                n_flat += 1
                continue
            n_scored_nonflat += 1
            scores_all.append(score)
            bucket = session_bucket(c.ts)
            bucket_eligible[bucket] += 1
            rg = regime_of.get(c.ts)
            if rg:
                regime_eligible[rg] += 1

            if not is_extreme_event(candles, series, i):
                continue

            bucket_events[bucket] += 1
            if rg:
                regime_events[rg] += 1
            if (
                last_event_i is not None
                and i == last_event_i + 1
                and index.session_start(i) == index.session_start(last_event_i)
            ):
                consecutive_pairs += 1
            last_event_i = i

            same_sess = same_session_predecessor(index, i)
            vol, rvol = trailing_ok_volume(
                volumes, unknown, timestamps, index.session_start(i), i
            )
            room = opposite_session_room(candles, index, i, direction)
            move_pct = abs(c.close / candles[i - 1].close - 1.0) * 100 if candles[i - 1].close > 0 else None

            events.append({
                "symbol": symbol,
                "index": i,
                "ts": c.ts,
                "day": ist_date(c.ts).isoformat(),
                "direction": direction,
                "score": score,
                "bucket": bucket,
                "regime": rg,
                "same_session_predecessor": same_sess,
                "session_open": i == index.session_start(i),
                "volume": vol,
                "rvol": rvol,
                "opposite_room": room,
                "move_pct": move_pct,
            })
            side = "UP" if direction > 0 else "DOWN"
            points.append(SignalPoint(symbol, i, c.ts, side))

    n_events = len(events)
    n_up = sum(1 for e in events if e["direction"] > 0)
    n_down = n_events - n_up
    n_open = sum(1 for e in events if e["session_open"])
    n_same = sum(1 for e in events if e["same_session_predecessor"])
    event_days = {e["day"] for e in events}
    event_syms = {e["symbol"] for e in events}

    ep = episode_report(points, EPISODE_SEPARATION_BARS)
    sizes = [e.size for e in group_episodes(points, EPISODE_SEPARATION_BARS)]
    if sizes:
        sizes_sorted = sorted(sizes)
        p90_i = int(round(0.9 * (len(sizes_sorted) - 1)))
        ep["median_episode_size"] = float(statistics.median(sizes))
        ep["p90_episode_size"] = float(sizes_sorted[p90_i])
    else:
        ep["median_episode_size"] = None
        ep["p90_episode_size"] = None

    by_bucket = {}
    for b in sorted(set(bucket_eligible) | set(bucket_events)):
        elig = bucket_eligible[b]
        n = bucket_events[b]
        by_bucket[str(b)] = {
            "label": bucket_label(b),
            "events": n,
            "eligible_bars": elig,
            "event_rate_pct": _share(n, elig),
            "up": sum(1 for e in events if e["bucket"] == b and e["direction"] > 0),
            "down": sum(1 for e in events if e["bucket"] == b and e["direction"] < 0),
            "session_open": sum(1 for e in events if e["bucket"] == b and e["session_open"]),
            "median_score": statistics.median(
                [e["score"] for e in events if e["bucket"] == b]
            ) if n else None,
            "median_opposite_room": statistics.median(
                [e["opposite_room"] for e in events if e["bucket"] == b and e["opposite_room"] is not None]
            ) if any(e["bucket"] == b and e["opposite_room"] is not None for e in events) else None,
        }

    by_regime = {}
    for rg in ("LOW", "NORMAL", "HIGH"):
        elig = regime_eligible[rg]
        n = regime_events[rg]
        by_regime[rg] = {
            "events": n,
            "eligible_bars": elig,
            "event_rate_pct": _share(n, elig),
            "share_of_events_pct": _share(n, n_events),
            "up": sum(1 for e in events if e["regime"] == rg and e["direction"] > 0),
            "down": sum(1 for e in events if e["regime"] == rg and e["direction"] < 0),
        }

    rvols = [e["rvol"] for e in events if e["rvol"] is not None]
    n_vol_unknown = sum(1 for e in events if e["volume"] is None)
    n_vol_thin = sum(1 for e in events if e["volume"] is not None and e["rvol"] is None)
    rooms = [e["opposite_room"] for e in events if e["opposite_room"] is not None]
    same_sess_rooms = [
        e["opposite_room"]
        for e in events
        if e["same_session_predecessor"] and e["opposite_room"] is not None
    ]

    events_per_day = [
        sum(1 for e in events if e["day"] == d) for d in sorted(event_days)
    ]

    # Power is computed from counts only. No outcome is read.
    n_dev_days = len(days)
    n_signal_days = ep.get("signal_days") or 0
    avg_events_per_day = (n_events / n_dev_days) if n_dev_days else None
    # Day-level SE for a 50% coin-flip of event-level signs, if each day is one
    # Bernoulli. Detecting a 10pp day-level gap needs a much smaller n than
    # detecting a 2pp gap.
    power = {
        "events": n_events,
        "development_sessions": n_dev_days,
        "signal_days": n_signal_days,
        "symbols_with_events": len(event_syms),
        "universe_loaded": len(loaded),
        "episodes": ep.get("episodes"),
        "events_per_episode": ep.get("signals_per_episode"),
        "average_events_per_development_day": avg_events_per_day,
        "median_events_per_signal_day": statistics.median(events_per_day) if events_per_day else None,
        "directional_balance": {
            "up": n_up,
            "down": n_down,
            "up_pct": _share(n_up, n_events),
            "down_pct": _share(n_down, n_events),
        },
        "inference_unit": "trading day",
        "effective_n_upper_bound": n_dev_days,
        "note": (
            "Event count is not the independent sample size. Day-level block "
            "inference has at most 38 observations. A large reversal (day-mean "
            "signed move well away from zero, or a ~60/40 reversal rate that "
            "is stable across days) is detectable. A 52/48 split of the kind "
            "already seen descriptively is not: it is inside ordinary day-to-day "
            "noise at n=38."
        ),
    }

    result = {
        "study": "extreme_move_event_audit_v1",
        "period": {"start": str(DEV_START), "end": str(DEV_END), "sessions": n_dev_days},
        "hold_out": "NOT READ",
        "validation": "NOT READ",
        "h006": "NOT CREATED",
        "forward_experiment": "NOT RUN",
        "frozen": {
            "H001": "REJECTED",
            "H002": "REJECTED",
            "H003": "REJECTED",
            "H004": "REJECTED",
            "H005": "REJECTED",
            "tradeability_gate": "REJECTED",
        },
        "event_definition": {
            "score": "abs(close[t]-close[t-1]) / ATR(14)[t-1]",
            "cut": EXTREME_CUT,
            "direction": "+1 if close[t]>close[t-1], -1 if close[t]<close[t-1], flat excluded",
            "atr_index": "t-1, never t",
            "threshold_search": "NOT PERFORMED",
        },
        "proposed_fade_measurement": {
            "horizons": list(FADE_HORIZONS),
            "primary_horizon": FADE_PRIMARY_HORIZON,
            "rationale": FADE_HORIZON_RATIONALE,
            "signed_future_move": "future_move(t,h) * event_direction(t); negative = reversion",
            "cost_in_definition": False,
            "implemented_on_store": False,
        },
        "universe_loaded": len(loaded),
        "eligibility": {
            "bars_loaded": n_bars,
            "score_defined": n_score_defined,
            "flat_excluded": n_flat,
            "scored_nonflat": n_scored_nonflat,
            "events": n_events,
            "event_rate_among_scored_nonflat_pct": _share(n_events, n_scored_nonflat),
            "event_rate_among_all_bars_pct": _share(n_events, n_bars),
        },
        "census": {
            "events": n_events,
            "up": n_up,
            "down": n_down,
            "up_pct": _share(n_up, n_events),
            "down_pct": _share(n_down, n_events),
            "sessions_represented": len(event_days),
            "sessions_with_no_event": n_dev_days - len(event_days),
            "symbols_represented": len(event_syms),
            "symbols_loaded": len(loaded),
            "session_open_events": n_open,
            "session_open_pct": _share(n_open, n_events),
            "same_session_predecessor": n_same,
            "same_session_predecessor_pct": _share(n_same, n_events),
            "consecutive_event_pairs": consecutive_pairs,
            "consecutive_overlap_pct": _share(consecutive_pairs, n_events),
        },
        "score_distribution": {
            "all_scored_nonflat": _pctiles(scores_all),
            "events": _pctiles([e["score"] for e in events]),
            "event_move_pct": _pctiles([e["move_pct"] for e in events if e["move_pct"] is not None]),
        },
        "mechanical_room": {
            "opposite_session_room_all": _pctiles(rooms),
            "opposite_session_room_same_session_only": _pctiles(same_sess_rooms),
            "note": (
                "Position in the printed session range, through bar t. A value "
                "near 1 means most of the day's printed range already sits "
                "opposite the event. That is a selection effect of a large "
                "bar, not a forward return."
            ),
        },
        "time_of_day": by_bucket,
        "vol_regime": by_regime,
        "volume": {
            "canonical_field": "bar_volume",
            "unknown_excluded": n_vol_unknown,
            "thin_baseline": n_vol_thin,
            "rvol_defined": len(rvols),
            "rvol": _pctiles(rvols),
            "pct_rvol_ge_1": _share(sum(1 for x in rvols if x >= 1), len(rvols)),
            "pct_rvol_ge_1_5": _share(sum(1 for x in rvols if x >= 1.5), len(rvols)),
            "pct_rvol_ge_2": _share(sum(1 for x in rvols if x >= 2), len(rvols)),
            "note": (
                "RVOL = event bar_volume / median of the prior same-session OK "
                "bars (up to 12). UNKNOWN bars excluded. Future bars unused. "
                "Volume does not select events."
            ),
        },
        "episodes": ep,
        "power": power,
        "controls_designed_not_run": {
            "A": (
                "Same-bar random direction: keep symbol, day, timestamp, event "
                "bar and volatility; shuffle +1/−1 preserving counts. Null: "
                "the extreme-move sign is independent of the later displacement "
                "sign. Statistic: day-level mean of signed_future_move."
            ),
            "B": (
                "Large-move random side: the event population already IS the "
                "large-move set, so B is the same shuffle as A. Its job is the "
                "magnitude tables under shuffled labels, so unusual subsequent "
                "volatility is not read as a directional fade."
            ),
            "mechanical_room_match": (
                "Proposed, not run: match non-event bars on (symbol, day, "
                "bucket, session-position tercile) and assign the event "
                "direction. If those bars fade too, the effect is location-in-"
                "range rather than the 1-ATR event."
            ),
        },
        "cost": {
            "floor_pct": 0.1831,
            "used_in_event_definition": False,
            "used_in_this_audit": False,
        },
    }
    return result


def main() -> None:
    result = run()
    out = OUT_PATH
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)
    c = result["census"]
    e = result["eligibility"]
    p = result["power"]
    ep = result["episodes"]
    print(
        f"extreme-move audit  period={result['period']['start']}..{result['period']['end']}  "
        f"hold-out={result['hold_out']}  H006={result['h006']}  "
        f"forward={result['forward_experiment']}"
    )
    print(f"  bars={e['bars_loaded']:,}  scored_nonflat={e['scored_nonflat']:,}  "
          f"events={c['events']:,}  rate={e['event_rate_among_scored_nonflat_pct']:.2f}%")
    print(f"  up={c['up']:,} ({c['up_pct']:.1f}%)  down={c['down']:,} ({c['down_pct']:.1f}%)  "
          f"sessions={c['sessions_represented']}/{result['period']['sessions']}  "
          f"symbols={c['symbols_represented']}/{c['symbols_loaded']}")
    print(f"  session_open={c['session_open_events']:,} ({c['session_open_pct']:.1f}%)  "
          f"same_session_pred={c['same_session_predecessor_pct']:.1f}%  "
          f"consecutive_pairs={c['consecutive_event_pairs']:,} "
          f"({c['consecutive_overlap_pct']:.1f}%)")
    print(f"  episodes={ep.get('episodes')}  events/episode={ep.get('signals_per_episode')}  "
          f"median_size={ep.get('median_episode_size')}  p90_size={ep.get('p90_episode_size')}")
    print(f"  power: days={p['development_sessions']}  effective_n<={p['effective_n_upper_bound']}  "
          f"avg events/day={p['average_events_per_development_day']:.1f}")
    print("  TOD event rates:")
    for _b, row in result["time_of_day"].items():
        print(f"    {row['label']}  n={row['events']:5d}  rate={row['event_rate_pct']:.2f}%  "
              f"up={row['up']} down={row['down']}  open={row['session_open']}")
    print("  regime:")
    for rg, row in result["vol_regime"].items():
        print(f"    {rg:6s}  n={row['events']:5d}  rate={row['event_rate_pct']:.2f}%  "
              f"share={row['share_of_events_pct']:.1f}%")
    vol = result["volume"]
    print(f"  volume rvol n={vol['rvol_defined']}  p50={vol['rvol'].get('p50')}  "
          f">=1.5x={vol['pct_rvol_ge_1_5']}")
    room = result["mechanical_room"]["opposite_session_room_same_session_only"]
    print(f"  opposite session room (same-session events) p50={room.get('p50')}  "
          f"p90={room.get('p90')}")
    print(f"  wrote {out}")


if __name__ == "__main__":
    main()
