"""Development-only census of intraday movement vs the existing cost floor.

Not a strategy. Not H006. Hold-out unread. Run from `backend`:

    python -m app.research.tradeability_evaluate
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from collections import defaultdict

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from app.research.cross_sectional import MIN_SECTOR_SIZE, SECTOR_OF, sector_median_loo
from app.research.matched_controls import bucket_label, session_bucket
from app.research.store import ResearchStore
from app.research.tradeability import (
    COST_MULTIPLES, DEV_END, DEV_START, HORIZONS, LARGE_MOVE_ATR, LOOKBACK_BARS,
    POSITION_VALUE, assert_development_window, atr_series_for, bar_move_in_atr,
    classify_vol_regime, contemporaneous_return_pct, cost_constants,
    cost_floor_pct, gap_over_atr, rel_atr_at,
    round_trip_cost_audit, session_range_over_atr,
)
from app.services.observation_window import SessionIndex, ist_date

MIN_CS = 50
MIN_CORR_OVERLAP = 30
PERSIST_HORIZONS = (1, 3, 6, 12)


def _pctiles(arr: np.ndarray) -> dict:
    a = np.asarray(arr, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {"n": 0}
    qs = np.percentile(a, [10, 25, 50, 75, 90, 95])
    return {
        "n": int(a.size),
        "mean": float(np.mean(a)),
        "p10": float(qs[0]),
        "p25": float(qs[1]),
        "p50": float(qs[2]),
        "p75": float(qs[3]),
        "p90": float(qs[4]),
        "p95": float(qs[5]),
    }


def _share(flag: np.ndarray) -> float | None:
    if flag.size == 0:
        return None
    return float(np.mean(flag) * 100)


def _window_extrema(arr: np.ndarray, horizon: int) -> np.ndarray:
    """out[i] = max/min of arr[i+1 : i+1+horizon]; NaN where the window does not fit."""
    n = len(arr)
    out = np.full(n, np.nan)
    if n <= horizon:
        return out
    view = sliding_window_view(arr, horizon)
    out[: n - horizon] = view[1 : n - horizon + 1].max(axis=1)
    return out


def _window_minima(arr: np.ndarray, horizon: int) -> np.ndarray:
    n = len(arr)
    out = np.full(n, np.nan)
    if n <= horizon:
        return out
    view = sliding_window_view(arr, horizon)
    out[: n - horizon] = view[1 : n - horizon + 1].min(axis=1)
    return out


def _pearson(a: np.ndarray, b: np.ndarray) -> float | None:
    m = np.isfinite(a) & np.isfinite(b)
    if int(m.sum()) < MIN_CORR_OVERLAP:
        return None
    if np.std(a[m]) == 0 or np.std(b[m]) == 0:
        return None
    return float(np.corrcoef(a[m], b[m])[0, 1])


def run() -> dict:
    assert_development_window(DEV_START, DEV_END)
    store = ResearchStore()
    symbols = store.symbols("5m", "live")
    if not symbols:
        raise RuntimeError("research store has no 5m symbols")

    # ---- load + input-side features --------------------------------------
    per_symbol: dict[str, dict] = {}
    input_rel_atr: list[float] = []
    input_session_range: list[float] = []
    input_rvol: list[float] = []
    input_gaps: list[float] = []
    n_bars = 0
    n_atr_missing = 0
    n_r12_missing = 0
    days: set[dt.date] = set()

    # timestamp -> lists for cross-section
    cs_rel: dict[int, list[float]] = defaultdict(list)
    cs_r12: dict[int, dict[str, float]] = defaultdict(dict)
    cs_srange: dict[int, list[float]] = defaultdict(list)
    r12_by_symbol: dict[str, dict[int, float]] = {}

    for symbol in symbols:
        candles = store.read(symbol, "5m", "live", start=DEV_START, end=DEV_END)
        if not candles:
            continue
        unknown = store.unknown_volume_timestamps(symbol, "5m", "live")
        index = SessionIndex(candles)
        series = atr_series_for(candles)
        n = len(candles)
        close = np.array([c.close for c in candles], dtype=float)
        high = np.array([c.high for c in candles], dtype=float)
        low = np.array([c.low for c in candles], dtype=float)
        vol = np.array([c.volume for c in candles], dtype=float)
        ts = np.array([c.ts for c in candles], dtype=np.int64)
        sess_end = np.array([index.session_end(i) for i in range(n)], dtype=int)
        sess_start = np.array([index.session_start(i) for i in range(n)], dtype=int)

        rel = np.full(n, np.nan)
        r12 = np.full(n, np.nan)
        srange = np.full(n, np.nan)
        rvol12 = np.full(n, np.nan)
        buckets = np.array([session_bucket(int(t)) for t in ts], dtype=int)
        day_of = [ist_date(int(t)) for t in ts]
        days.update(day_of)

        turnovers = []
        prices = []
        rels_sym = []
        r12_map: dict[int, float] = {}

        for i, c in enumerate(candles):
            n_bars += 1
            ra = rel_atr_at(candles, series, i)
            if ra is None:
                n_atr_missing += 1
            else:
                rel[i] = ra
                input_rel_atr.append(ra)
                cs_rel[c.ts].append(ra)
                rels_sym.append(ra)
            rr = contemporaneous_return_pct(candles, index, i)
            if rr is None:
                n_r12_missing += 1
            else:
                r12[i] = rr
                cs_r12[c.ts][symbol] = rr
                r12_map[c.ts] = rr
            sr = session_range_over_atr(candles, index, series, i)
            if sr is not None:
                srange[i] = sr
                input_session_range.append(sr)
                cs_srange[c.ts].append(sr)
            g = gap_over_atr(candles, index, series, i)
            if g is not None:
                input_gaps.append(g)
            if (
                i >= LOOKBACK_BARS
                and index.session_start(i) <= i - LOOKBACK_BARS
                and close[i - LOOKBACK_BARS] > 0
            ):
                rets = np.diff(close[i - LOOKBACK_BARS : i + 1]) / close[i - LOOKBACK_BARS : i]
                rvol12[i] = float(np.std(rets, ddof=0))
                if np.isfinite(rvol12[i]):
                    input_rvol.append(float(rvol12[i]) * 100)
            if c.ts not in unknown and c.volume > 0 and c.close > 0:
                turnovers.append(c.close * c.volume)
                prices.append(c.close)

        r12_by_symbol[symbol] = r12_map
        per_symbol[symbol] = {
            "candles": candles,
            "index": index,
            "series": series,
            "close": close,
            "high": high,
            "low": low,
            "vol": vol,
            "ts": ts,
            "sess_end": sess_end,
            "sess_start": sess_start,
            "rel": rel,
            "r12": r12,
            "srange": srange,
            "rvol12": rvol12,
            "buckets": buckets,
            "day_of": day_of,
            "unknown": unknown,
            "median_turnover": statistics.median(turnovers) if turnovers else math.nan,
            "median_price": statistics.median(prices) if prices else math.nan,
            "median_volume": statistics.median(
                [c.volume for c in candles if c.ts not in unknown and c.volume > 0]
            ) if any(c.ts not in unknown and c.volume > 0 for c in candles) else math.nan,
            "median_rel_atr": statistics.median(rels_sym) if rels_sym else math.nan,
            "ok_volume_bars": sum(1 for c in candles if c.ts not in unknown and c.volume > 0),
            "unknown_volume_bars": sum(1 for c in candles if c.ts in unknown),
        }

    loaded = sorted(per_symbol)
    if len(loaded) != 125:
        # still proceed; report the count
        pass

    # liquidity terciles from predictor-side median turnover
    turns = [(s, per_symbol[s]["median_turnover"]) for s in loaded if np.isfinite(per_symbol[s]["median_turnover"])]
    turns.sort(key=lambda x: x[1])
    n_t = len(turns)
    liq_of: dict[str, str] = {}
    for i, (s, _) in enumerate(turns):
        if i < n_t / 3:
            liq_of[s] = "LOW"
        elif i < 2 * n_t / 3:
            liq_of[s] = "MID"
        else:
            liq_of[s] = "HIGH"

    # ---- market-wide series (input only) ---------------------------------
    ts_keys = sorted(t for t, xs in cs_rel.items() if len(xs) >= MIN_CS)
    mkt_rel = np.array([statistics.median(cs_rel[t]) for t in ts_keys], dtype=float)
    p25 = float(np.percentile(mkt_rel, 25))
    p75 = float(np.percentile(mkt_rel, 75))
    regime_of_ts: dict[int, str] = {
        t: classify_vol_regime(float(np.median(cs_rel[t])), p25, p75) for t in ts_keys
    }

    cs_disp = []
    mkt_abs_r12 = []
    breadth = []
    decomp_rows = []
    regime_disp = defaultdict(list)
    regime_idio = defaultdict(list)

    for t in ts_keys:
        rmap = cs_r12.get(t, {})
        if len(rmap) < MIN_CS:
            continue
        vals = np.array(list(rmap.values()), dtype=float)
        med = float(np.median(vals))
        disp = float(np.median(np.abs(vals - med)))
        cs_disp.append(disp)
        mkt_abs_r12.append(abs(med))
        breadth.append(float(np.mean(vals > 0) * 100))
        var_r = float(np.var(vals))
        if var_r <= 0:
            continue
        idio = []
        vs_mkt = np.abs(vals - med)
        for sym, r in rmap.items():
            rs = sector_median_loo(rmap, sym)
            if rs is None:
                continue
            idio.append(r - rs)
        if len(idio) < MIN_SECTOR_SIZE:
            continue
        idio_arr = np.array(idio)
        idio_share = float(np.var(idio_arr) / var_r)
        row = {
            "cs_sector_r2": float(1.0 - idio_share),
            "idio_share": idio_share,
            "median_abs_r": float(np.median(np.abs(vals))),
            "median_abs_idio": float(np.median(np.abs(idio_arr))),
            "median_abs_market": abs(med),
            "median_abs_vs_market": float(np.median(vs_mkt)),
        }
        decomp_rows.append(row)
        rg = regime_of_ts[t]
        regime_disp[rg].append(disp)
        regime_idio[rg].append(idio_share)

    # pairwise time-series correlations of contemporaneous 12-bar returns
    all_ts = sorted({t for d in r12_by_symbol.values() for t in d})
    ts_index = {t: j for j, t in enumerate(all_ts)}
    n_ts = len(all_ts)
    mat = np.full((len(loaded), n_ts), np.nan)
    for si, s in enumerate(loaded):
        for t, r in r12_by_symbol[s].items():
            mat[si, ts_index[t]] = r
    within, cross = [], []
    for i in range(len(loaded)):
        for j in range(i + 1, len(loaded)):
            corr = _pearson(mat[i], mat[j])
            if corr is None:
                continue
            si, sj = loaded[i], loaded[j]
            same = SECTOR_OF.get(si) is not None and SECTOR_OF.get(si) == SECTOR_OF.get(sj)
            (within if same else cross).append(corr)

    # stock vs market / sector R² (time series, contemporaneous)
    mkt_series = np.full(n_ts, np.nan)
    for t, j in ts_index.items():
        rmap = cs_r12.get(t)
        if rmap and len(rmap) >= MIN_CS:
            mkt_series[j] = float(np.median(list(rmap.values())))
    r2_mkt, r2_sec = [], []
    for si, s in enumerate(loaded):
        r2 = _pearson(mat[si], mkt_series)
        if r2 is not None:
            r2_mkt.append(r2 ** 2)
        sec = SECTOR_OF.get(s)
        if sec is None:
            continue
        peers = [loaded.index(p) for p in loaded if p != s and SECTOR_OF.get(p) == sec]
        if len(peers) < MIN_SECTOR_SIZE - 1:
            continue
        sec_med = np.nanmedian(mat[peers], axis=0)
        r2s = _pearson(mat[si], sec_med)
        if r2s is not None:
            r2_sec.append(r2s ** 2)

    mkt_srange = np.array(
        [statistics.median(cs_srange[t]) for t in ts_keys if t in cs_srange], dtype=float
    )
    # align cs_disp length to ts_keys that had r12
    ts_with_r12 = [t for t in ts_keys if len(cs_r12.get(t, {})) >= MIN_CS]
    mkt_rel_r12 = np.array([float(np.median(cs_rel[t])) for t in ts_with_r12], dtype=float)
    cs_disp_a = np.array(cs_disp, dtype=float)
    redundancy = {
        "corr_mkt_rel_atr_cs_disp": _pearson(mkt_rel_r12, cs_disp_a),
        "corr_mkt_rel_atr_abs_mkt_r12": _pearson(mkt_rel_r12, np.array(mkt_abs_r12)),
        "corr_cs_disp_abs_mkt_r12": _pearson(cs_disp_a, np.array(mkt_abs_r12)),
        "corr_mkt_rel_atr_mkt_session_range": _pearson(
            np.array([float(np.median(cs_rel[t])) for t in ts_keys if t in cs_srange]),
            mkt_srange,
        ),
    }

    n_reg = {k: sum(1 for t in ts_keys if regime_of_ts[t] == k) for k in ("LOW", "NORMAL", "HIGH")}

    input_block = {
        "bars": n_bars,
        "symbols": len(loaded),
        "sessions": len(days),
        "atr_missing_bars": n_atr_missing,
        "atr_missing_pct": round(n_atr_missing / n_bars * 100, 2) if n_bars else None,
        "r12_missing_bars": n_r12_missing,
        "r12_missing_pct": round(n_r12_missing / n_bars * 100, 2) if n_bars else None,
        "rel_atr": _pctiles(np.array(input_rel_atr)),
        "session_range_atr": _pctiles(np.array(input_session_range)),
        "realized_vol_12_pct": _pctiles(np.array(input_rvol)),
        "gap_atr": _pctiles(np.array(input_gaps)),
        "mkt_rel_atr": _pctiles(mkt_rel),
        "mkt_rel_atr_p25": p25,
        "mkt_rel_atr_p75": p75,
        "regime_timestamp_counts": n_reg,
        "cs_disp_12bar_pct": _pctiles(cs_disp_a),
        "breadth_up_pct": _pctiles(np.array(breadth)),
        "redundancy": redundancy,
        "decomp": {
            "timestamps": len(decomp_rows),
            "note": (
                "A timestamp-level market median is constant across stocks, so it "
                "cannot explain cross-sectional variance. Market commonality is "
                "the time-series R² and pairwise correlations. Sector R² is "
                "1 - var(r - r_sector)/var(r) at each timestamp."
            ),
            "cs_sector_r2": _pctiles(np.array([r["cs_sector_r2"] for r in decomp_rows])),
            "idio_share": _pctiles(np.array([r["idio_share"] for r in decomp_rows])),
            "median_abs_r": _pctiles(np.array([r["median_abs_r"] for r in decomp_rows])),
            "median_abs_idio": _pctiles(np.array([r["median_abs_idio"] for r in decomp_rows])),
            "median_abs_market": _pctiles(np.array([r["median_abs_market"] for r in decomp_rows])),
            "median_abs_vs_market": _pctiles(np.array([r["median_abs_vs_market"] for r in decomp_rows])),
            "idio_over_abs": _pctiles(
                np.array([
                    r["median_abs_idio"] / r["median_abs_r"]
                    for r in decomp_rows if r["median_abs_r"] > 0
                ])
            ),
            "vs_market_over_abs": _pctiles(
                np.array([
                    r["median_abs_vs_market"] / r["median_abs_r"]
                    for r in decomp_rows if r["median_abs_r"] > 0
                ])
            ),
            "idio_share_by_regime": {
                k: _pctiles(np.array(v)) for k, v in sorted(regime_idio.items())
            },
            "cs_disp_by_regime": {
                k: _pctiles(np.array(v)) for k, v in sorted(regime_disp.items())
            },
        },
        "pairwise_corr_12bar": {
            "within_sector": _pctiles(np.array(within)),
            "cross_sector": _pctiles(np.array(cross)),
        },
        "timeseries_r2": {
            "vs_market_median": _pctiles(np.array(r2_mkt)),
            "vs_sector_median": _pctiles(np.array(r2_sec)),
        },
        "liquidity_predictor": {
            k: {
                "n_symbols": sum(1 for s in loaded if liq_of.get(s) == k),
                "median_turnover": statistics.median(
                    [per_symbol[s]["median_turnover"] for s in loaded if liq_of.get(s) == k]
                ),
                "median_price": statistics.median(
                    [per_symbol[s]["median_price"] for s in loaded if liq_of.get(s) == k]
                ),
                "median_volume": statistics.median(
                    [per_symbol[s]["median_volume"] for s in loaded if liq_of.get(s) == k]
                ),
                "median_rel_atr": statistics.median(
                    [per_symbol[s]["median_rel_atr"] for s in loaded if liq_of.get(s) == k]
                ),
            }
            for k in ("LOW", "MID", "HIGH")
        },
    }

    # ---- forward descriptive pass ----------------------------------------
    # Lists keyed by horizon, then optional slice key.
    fwd: dict[int, dict[str, list]] = {
        h: {k: [] for k in (
            "abs", "signed", "up", "down", "rng", "cost", "cov_abs",
            "bucket", "regime", "liq", "day", "symbol",
        )}
        for h in HORIZONS
    }
    persist = {
        h: {k: [] for k in (
            "continue", "reverse", "flat", "cont_mag", "rev_mag", "regime", "abs_next",
        )}
        for h in PERSIST_HORIZONS
    }
    n_large = 0

    for symbol, d in per_symbol.items():
        candles = d["candles"]
        series = d["series"]
        close, high, low = d["close"], d["high"], d["low"]
        ts, sess_end, buckets, day_of = d["ts"], d["sess_end"], d["buckets"], d["day_of"]
        n = len(candles)
        liq = liq_of.get(symbol, "MID")
        idx = np.arange(n)
        costs = np.array([
            cost_floor_pct(float(c.close), POSITION_VALUE) if c.close > 0 else math.nan
            for c in candles
        ], dtype=float)
        regimes = np.array([regime_of_ts.get(int(t), "") for t in ts])

        for h in HORIZONS:
            mx = _window_extrema(high, h)
            mn = _window_minima(low, h)
            fc = np.full(n, np.nan)
            if n > h:
                fc[: n - h] = close[h:]
            ok = (
                (idx + h <= sess_end)
                & (close > 0)
                & np.isfinite(mx)
                & np.isfinite(mn)
                & np.isfinite(fc)
                & np.isfinite(costs)
            )
            if not ok.any():
                continue
            entry = close[ok]
            signed = (fc[ok] / entry - 1.0) * 100
            abs_pct = np.abs(signed)
            up = np.maximum(0.0, mx[ok] - entry) / entry * 100
            down = np.maximum(0.0, entry - mn[ok]) / entry * 100
            rng = (mx[ok] - mn[ok]) / entry * 100
            cost = costs[ok]
            cov = np.divide(abs_pct, cost, out=np.full_like(abs_pct, np.nan), where=cost > 0)
            slot = fwd[h]
            slot["abs"].extend(abs_pct.tolist())
            slot["signed"].extend(signed.tolist())
            slot["up"].extend(up.tolist())
            slot["down"].extend(down.tolist())
            slot["rng"].extend(rng.tolist())
            slot["cost"].extend(cost.tolist())
            slot["cov_abs"].extend(cov.tolist())
            slot["bucket"].extend(buckets[ok].tolist())
            slot["regime"].extend(regimes[ok].tolist())
            slot["liq"].extend([liq] * int(ok.sum()))
            slot["day"].extend([day_of[i] for i in idx[ok]])
            slot["symbol"].extend([symbol] * int(ok.sum()))

        # persistence: rare by construction; loop only candidate bars
        for i in range(1, n):
            move = bar_move_in_atr(candles, series, i)
            if move is None or abs(move) < LARGE_MOVE_ATR:
                continue
            n_large += 1
            signed_bar = close[i] - close[i - 1]
            if signed_bar == 0:
                continue
            entry = float(close[i])
            if entry <= 0:
                continue
            rg = regimes[i]
            for h in PERSIST_HORIZONS:
                if i + h > sess_end[i]:
                    continue
                nxt = close[i + h] - close[i]
                mag = abs(nxt / entry * 100)
                if nxt == 0:
                    persist[h]["flat"].append(1)
                    persist[h]["continue"].append(0)
                    persist[h]["reverse"].append(0)
                elif (nxt > 0) == (signed_bar > 0):
                    persist[h]["continue"].append(1)
                    persist[h]["reverse"].append(0)
                    persist[h]["flat"].append(0)
                    persist[h]["cont_mag"].append(mag)
                else:
                    persist[h]["continue"].append(0)
                    persist[h]["reverse"].append(1)
                    persist[h]["flat"].append(0)
                    persist[h]["rev_mag"].append(mag)
                persist[h]["regime"].append(rg or "")
                persist[h]["abs_next"].append(mag)

    def _coverage_table(
        abs_arr: np.ndarray, up: np.ndarray, down: np.ndarray,
        signed: np.ndarray, cost: np.ndarray,
    ) -> dict:
        out: dict = {}
        for kind, arr in (("abs", abs_arr), ("upside_mfe", up), ("downside_mae", down)):
            out[kind] = {}
            for m in COST_MULTIPLES:
                out[kind][f"{m}x"] = _share(arr >= m * cost)
        out["signed_up"] = {f"{m}x": _share(signed >= m * cost) for m in COST_MULTIPLES}
        out["signed_down"] = {f"{m}x": _share(signed <= -m * cost) for m in COST_MULTIPLES}
        return out

    def _coverage_bins(cov: np.ndarray) -> dict:
        finite = cov[np.isfinite(cov)]
        if finite.size == 0:
            return {k: None for k in ("<1x", "1-2x", "2-3x", "3-5x", ">5x")}
        return {
            "<1x": float(np.mean(finite < 1) * 100),
            "1-2x": float(np.mean((finite >= 1) & (finite < 2)) * 100),
            "2-3x": float(np.mean((finite >= 2) & (finite < 3)) * 100),
            "3-5x": float(np.mean((finite >= 3) & (finite < 5)) * 100),
            ">5x": float(np.mean(finite >= 5) * 100),
        }

    def _signed_split(signed: np.ndarray) -> dict:
        pos = signed[signed > 0]
        neg = signed[signed < 0]
        return {
            "positive": _pctiles(pos),
            "negative": _pctiles(neg),
            "pct_positive": _share(signed > 0),
            "pct_negative": _share(signed < 0),
            "pct_flat": _share(signed == 0),
        }

    movement = {}
    for h in HORIZONS:
        s = fwd[h]
        abs_a = np.array(s["abs"])
        signed_a = np.array(s["signed"])
        up_a = np.array(s["up"])
        down_a = np.array(s["down"])
        rng_a = np.array(s["rng"])
        cost_a = np.array(s["cost"])
        cov_a = np.array(s["cov_abs"])
        movement[h] = {
            "n": int(abs_a.size),
            "n_sessions": len(set(s["day"])),
            "n_symbols": len(set(s["symbol"])),
            "abs_return": _pctiles(abs_a),
            "signed_return": _pctiles(signed_a),
            "signed_split": _signed_split(signed_a),
            "mfe_upside": _pctiles(up_a),
            "mae_downside": _pctiles(down_a),
            "range_expansion": _pctiles(rng_a),
            "cost_floor": _pctiles(cost_a),
            "cost_coverage_abs": _pctiles(cov_a),
            "coverage_bins_abs": _coverage_bins(cov_a),
            "exceeds_cost": _coverage_table(abs_a, up_a, down_a, signed_a, cost_a),
            "by_regime": {},
            "by_liq": {},
        }
        for rg in ("LOW", "NORMAL", "HIGH"):
            mask = np.array([x == rg for x in s["regime"]], dtype=bool)
            if not mask.any():
                continue
            movement[h]["by_regime"][rg] = {
                "n": int(mask.sum()),
                "abs_p50": float(np.median(abs_a[mask])),
                "abs_p90": float(np.percentile(abs_a[mask], 90)),
                "up_p50": float(np.median(up_a[mask])),
                "down_p50": float(np.median(down_a[mask])),
                "exceeds_1x": _share(abs_a[mask] >= cost_a[mask]),
                "exceeds_2x": _share(abs_a[mask] >= 2 * cost_a[mask]),
                "exceeds_3x": _share(abs_a[mask] >= 3 * cost_a[mask]),
            }
        for liq in ("LOW", "MID", "HIGH"):
            mask = np.array([x == liq for x in s["liq"]], dtype=bool)
            if not mask.any():
                continue
            movement[h]["by_liq"][liq] = {
                "n": int(mask.sum()),
                "abs_p50": float(np.median(abs_a[mask])),
                "abs_p90": float(np.percentile(abs_a[mask], 90)),
                "up_p50": float(np.median(up_a[mask])),
                "exceeds_1x": _share(abs_a[mask] >= cost_a[mask]),
                "exceeds_2x": _share(abs_a[mask] >= 2 * cost_a[mask]),
                "exceeds_3x": _share(abs_a[mask] >= 3 * cost_a[mask]),
            }

    tod = {}
    # primary description at every horizon, every 30-minute bucket
    for h in HORIZONS:
        s = fwd[h]
        abs_a = np.array(s["abs"])
        up_a = np.array(s["up"])
        down_a = np.array(s["down"])
        cost_a = np.array(s["cost"])
        buckets = np.array(s["bucket"])
        tod[h] = {}
        for b in sorted(set(int(x) for x in buckets)):
            mask = buckets == b
            days_b = {d for d, m in zip(s["day"], mask) if m}
            syms_b = {sy for sy, m in zip(s["symbol"], mask) if m}
            tod[h][b] = {
                "label": bucket_label(b),
                "n": int(mask.sum()),
                "n_sessions": len(days_b),
                "n_symbols": len(syms_b),
                "abs_p50": float(np.median(abs_a[mask])),
                "abs_p75": float(np.percentile(abs_a[mask], 75)),
                "abs_p90": float(np.percentile(abs_a[mask], 90)),
                "mfe_p50": float(np.median(up_a[mask])),
                "mae_p50": float(np.median(down_a[mask])),
                "exceeds_1x": _share(abs_a[mask] >= cost_a[mask]),
                "exceeds_2x": _share(abs_a[mask] >= 2 * cost_a[mask]),
                "exceeds_3x": _share(abs_a[mask] >= 3 * cost_a[mask]),
            }

    persistence = {"n_large_bars": n_large, "definition": f"|close[t]-close[t-1]| / ATR[t-1] >= {LARGE_MOVE_ATR}", "horizons": {}}
    for h in PERSIST_HORIZONS:
        p = persist[h]
        n = len(p["continue"])
        cont = np.array(p["continue"], dtype=float)
        rev = np.array(p["reverse"], dtype=float)
        flat = np.array(p["flat"], dtype=float)
        rg = np.array(p["regime"])
        block = {
            "n": n,
            "continuation_pct": float(np.mean(cont) * 100) if n else None,
            "reversal_pct": float(np.mean(rev) * 100) if n else None,
            "flat_pct": float(np.mean(flat) * 100) if n else None,
            "median_continuation_pct": float(np.median(p["cont_mag"])) if p["cont_mag"] else None,
            "median_reversal_pct": float(np.median(p["rev_mag"])) if p["rev_mag"] else None,
            "by_regime": {},
        }
        for name in ("LOW", "NORMAL", "HIGH"):
            mask = rg == name
            if not mask.any():
                continue
            block["by_regime"][name] = {
                "n": int(mask.sum()),
                "continuation_pct": float(np.mean(cont[mask]) * 100),
                "reversal_pct": float(np.mean(rev[mask]) * 100),
            }
        persistence["horizons"][h] = block

    cost_audit = {
        "constants": cost_constants(),
        "worked_examples": {
            str(p): round_trip_cost_audit(float(p))
            for p in (100, 500, 1136, 2000, 5000, 8000)
        },
    }

    return {
        "study": "market_tradeability_v1",
        "period": {"start": str(DEV_START), "end": str(DEV_END), "sessions": len(days)},
        "hold_out": "NOT READ",
        "validation": "NOT READ",
        "universe_loaded": len(loaded),
        "cost": cost_audit,
        "inputs": input_block,
        "movement": {str(h): movement[h] for h in HORIZONS},
        "time_of_day": {str(h): {str(b): tod[h][b] for b in tod[h]} for h in HORIZONS},
        "persistence": persistence,
        "hypotheses_frozen": {
            "H001": "REJECTED", "H002": "REJECTED", "H003": "REJECTED",
            "H004": "REJECTED", "H005": "REJECTED",
        },
        "h006": "NOT CREATED",
    }


def _fmt_pctiles(d: dict, keys=("p25", "p50", "p75", "p90")) -> str:
    if not d or d.get("n", 0) == 0:
        return "empty"
    parts = [f"n={d['n']:,}", f"mean={d['mean']:.4f}"]
    parts += [f"{k}={d[k]:.4f}" for k in keys if k in d]
    return "  ".join(parts)


def print_report(result: dict) -> None:
    print("=" * 72)
    print("TRADEABILITY CENSUS  (development only; not a strategy)")
    print(f"period {result['period']['start']} .. {result['period']['end']}  "
          f"sessions={result['period']['sessions']}  symbols={result['universe_loaded']}")
    print(f"hold-out={result['hold_out']}  validation={result['validation']}  H006={result['h006']}")
    print("=" * 72)
    print("\n-- cost floor worked examples --")
    for p, a in result["cost"]["worked_examples"].items():
        print(f"  Px {p:>5}  qty={a['qty']:>4}  charges={a['charges_pct']:.4f}%  "
              f"slip={a['slippage_round_trip_pct']:.2f}%  floor={a['total_floor_pct']:.4f}%")
    inp = result["inputs"]
    print("\n-- input distributions --")
    print(f"  bars={inp['bars']:,}  ATR missing={inp['atr_missing_pct']}%  "
          f"r12 missing={inp['r12_missing_pct']}%")
    print(f"  rel_atr           {_fmt_pctiles(inp['rel_atr'])}")
    print(f"  mkt_rel_atr       {_fmt_pctiles(inp['mkt_rel_atr'])}")
    print(f"  REGIME CUTS       p25={inp['mkt_rel_atr_p25']:.6f}  p75={inp['mkt_rel_atr_p75']:.6f}  "
          f"counts={inp['regime_timestamp_counts']}")
    print(f"  cs_disp 12bar     {_fmt_pctiles(inp['cs_disp_12bar_pct'])}")
    print(f"  session_range/ATR {_fmt_pctiles(inp['session_range_atr'])}")
    print(f"  gap/ATR           {_fmt_pctiles(inp['gap_atr'])}")
    print(f"  realized vol 12   {_fmt_pctiles(inp['realized_vol_12_pct'])}")
    print(f"  redundancy        {inp['redundancy']}")
    d = inp["decomp"]
    print("\n-- contemporaneous 12-bar decomposition --")
    print(f"  {d['note']}")
    print(f"  cs sector R2      {_fmt_pctiles(d['cs_sector_r2'])}")
    print(f"  idio share        {_fmt_pctiles(d['idio_share'])}")
    print(f"  |r|               {_fmt_pctiles(d['median_abs_r'])}")
    print(f"  |r_m|             {_fmt_pctiles(d['median_abs_market'])}")
    print(f"  |r - r_m|         {_fmt_pctiles(d['median_abs_vs_market'])}")
    print(f"  |idio|/|r|        {_fmt_pctiles(d['idio_over_abs'])}")
    print(f"  |r-r_m|/|r|       {_fmt_pctiles(d['vs_market_over_abs'])}")
    print(f"  idio share by rg  { {k: v.get('p50') for k, v in d['idio_share_by_regime'].items()} }")
    print(f"  pairwise within   {_fmt_pctiles(inp['pairwise_corr_12bar']['within_sector'])}")
    print(f"  pairwise cross    {_fmt_pctiles(inp['pairwise_corr_12bar']['cross_sector'])}")
    print(f"  R2 vs mkt         {_fmt_pctiles(inp['timeseries_r2']['vs_market_median'])}")
    print(f"  R2 vs sector      {_fmt_pctiles(inp['timeseries_r2']['vs_sector_median'])}")
    print("\n-- liquidity (predictor-side terciles) --")
    for k, v in inp["liquidity_predictor"].items():
        print(f"  {k:5} n={v['n_symbols']:3}  turn={v['median_turnover']:.0f}  "
              f"px={v['median_price']:.1f}  vol={v['median_volume']:.0f}  rel_atr={v['median_rel_atr']:.5f}")
    print("\n-- forward movement --")
    for h, m in result["movement"].items():
        ex = m["exceeds_cost"]["abs"]
        print(f"  h={h:>2} n={m['n']:,}  abs {_fmt_pctiles(m['abs_return'])}")
        print(f"       up  {_fmt_pctiles(m['mfe_upside'])}")
        print(f"       dn  {_fmt_pctiles(m['mae_downside'])}")
        print(f"       rng {_fmt_pctiles(m['range_expansion'])}")
        print(f"       cov {_fmt_pctiles(m['cost_coverage_abs'])}")
        print(f"       bins {m['coverage_bins_abs']}")
        print(f"       exceeds abs 1x={ex['1x']:.1f}% 2x={ex['2x']:.1f}% "
              f"3x={ex['3x']:.1f}% 5x={ex['5x']:.1f}%")
        print(f"       exceeds MFE {m['exceeds_cost']['upside_mfe']}")
        print(f"       exceeds MAE {m['exceeds_cost']['downside_mae']}")
        print(f"       signed +cost {m['exceeds_cost']['signed_up']}")
        print(f"       signed -cost {m['exceeds_cost']['signed_down']}")
        print(f"       signed split {m['signed_split']['pct_positive']:.1f}% pos / "
              f"{m['signed_split']['pct_negative']:.1f}% neg")
        print(f"       by regime {m['by_regime']}")
        print(f"       by liq    {m['by_liq']}")
    print("\n-- time of day (h=12) --")
    for b, row in result["time_of_day"]["12"].items():
        print(
            f"  {row['label']} n={row['n']:,} sess={row['n_sessions']} sym={row['n_symbols']}  "
            f"abs p50={row['abs_p50']:.4f} p75={row['abs_p75']:.4f} p90={row['abs_p90']:.4f}  "
            f"MFE={row['mfe_p50']:.4f} MAE={row['mae_p50']:.4f}  "
            f"1x={row['exceeds_1x']:.1f}% 2x={row['exceeds_2x']:.1f}% 3x={row['exceeds_3x']:.1f}%"
        )
    print("\n-- persistence after |dc|/ATR[t-1] >= 1 --")
    print(f"  large bars={result['persistence']['n_large_bars']:,}")
    for h, p in result["persistence"]["horizons"].items():
        print(f"  h={h} n={p['n']:,}  cont={p['continuation_pct']:.1f}%  "
              f"rev={p['reversal_pct']:.1f}%  flat={p['flat_pct']:.1f}%  "
              f"med_cont={p['median_continuation_pct']:.4f}  med_rev={p['median_reversal_pct']:.4f}  "
              f"by_rg={p['by_regime']}")
    print("\n-- frozen hypotheses --")
    print(result["hypotheses_frozen"])


def main() -> None:
    result = run()
    print_report(result)
    print(f"\nJSON_OK period={result['period']} hold_out={result['hold_out']}")
    # Compact JSON for the report writer; floats rounded.
    path = __file__.replace("tradeability_evaluate.py", "")
    dest = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "research_data" / "tradeability_dev.json"
    )
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
    print(f"wrote {dest}")


if __name__ == "__main__":
    main()
