"""P&L dashboard: when in the day is the bots' P&L best? (read-only; the desk's home page, `/`).

Reads the minute-by-minute P&L log (`open_pnl_log`: each desk's closed + open P&L, before charges by default) for
market days or backtest (replay) runs and turns it into what the owner needs to pick a time to exit every trade:

* each day's curve on one 09:15-15:30 minute grid (`day_curve`: the last logged point at or before each minute, 0
  before the first one, the last one carried after it), its high, low and close and when they came;
* for every time of day (`step` minutes apart), across the picked days: average / median / worst / best P&L "if
  everything were closed now", how often that beats holding to the close, and the summed P&L of all days;
* the best exit time (highest average, and highest median), the hours the day's high and low tend to come in, and
  how much of the high is given back by the close;
* the same per stock (each stock's closed trades by that minute plus its open P&L, before charges).

Nothing here places, changes or reads an order; nothing in the strategy reads it.
"""
from __future__ import annotations

import datetime as dt
import statistics
from typing import Iterable

OPEN = (9, 15)
CLOSE = (15, 30)
STEPS = (1, 5, 10, 15, 30)
BUCKET_MIN = 30
MAX_DAYS = 120


def minutes_grid() -> list[str]:
    """Every minute of the session as "HH:MM", 09:15 through 15:30."""
    out = []
    t = dt.datetime(2000, 1, 1, *OPEN)
    end = dt.datetime(2000, 1, 1, *CLOSE)
    while t <= end:
        out.append(t.strftime("%H:%M"))
        t += dt.timedelta(minutes=1)
    return out


GRID = minutes_grid()


def _hhmm(t: str) -> str:
    """'2026-10-20T11:42+05:30' or '11:42' → '11:42'."""
    s = str(t)
    return s[11:16] if len(s) >= 16 and s[10] in "T " else s[:5]


def day_curve(points: Iterable[tuple[str, float]]) -> list[float]:
    """One value per GRID minute: the last point at or before it (0 before the first, the last carried after)."""
    pts = sorted((_hhmm(t), float(v or 0.0)) for t, v in points)
    out: list[float] = []
    i, cur = 0, 0.0
    for m in GRID:
        while i < len(pts) and pts[i][0] <= m:
            cur = pts[i][1]
            i += 1
        out.append(cur)
    return out


def add_curves(curves: list[list[float]]) -> list[float]:
    if not curves:
        return [0.0] * len(GRID)
    return [round(sum(c[i] for c in curves), 2) for i in range(len(GRID))]


def _stats(curve: list[float], first: int, last: int) -> dict:
    """High, low and close of one curve inside its logged span (first..last minute index)."""
    span = curve[first : last + 1] or [0.0]
    hi = max(span)
    lo = min(span)
    hi_i = first + span.index(hi)
    lo_i = first + span.index(lo)
    close = curve[last] if curve else 0.0
    return {
        "high": round(hi, 2),
        "high_at": GRID[hi_i],
        "low": round(lo, 2),
        "low_at": GRID[lo_i],
        "close": round(close, 2),
        "giveback": round(hi - close, 2),
    }


def _bucket(hhmm: str) -> str:
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    total = h * 60 + m
    start = max(OPEN[0] * 60 + OPEN[1], total - (total - (OPEN[0] * 60 + OPEN[1])) % BUCKET_MIN)
    return f"{start // 60:02d}:{start % 60:02d}"


def _buckets() -> list[str]:
    out, seen = [], set()
    for m in GRID:
        b = _bucket(m)
        if b not in seen:
            seen.add(b)
            out.append(b)
    return out


def _grid_at(step: int) -> list[int]:
    """GRID indexes every `step` minutes from 09:15, always ending on 15:30."""
    idx = list(range(0, len(GRID), step))
    if idx[-1] != len(GRID) - 1:
        idx.append(len(GRID) - 1)
    return idx


def by_time(days: list[dict], step: int) -> list[dict]:
    """For each time of day: what closing every trade then would have given, across the days."""
    out = []
    closes = [d["close"] for d in days]
    for i in _grid_at(step):
        vals = [d["curve"][i] for d in days]
        if not vals:
            continue
        beat = sum(1 for v, c in zip(vals, closes) if v > c)
        out.append(
            {
                "t": GRID[i],
                "avg": round(statistics.fmean(vals), 2),
                "median": round(statistics.median(vals), 2),
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "sum": round(sum(vals), 2),
                "pos_pct": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
                "beat_close_pct": round(100 * beat / len(vals), 1),
                "vs_close": round(statistics.fmean(v - c for v, c in zip(vals, closes)), 2),
            }
        )
    return out


def best_exit(times: list[dict], days: list[dict]) -> dict | None:
    """The time with the highest average (and median) P&L, set against holding every day to the close."""
    if not times or not days:
        return None
    by_avg = max(times, key=lambda r: (r["avg"], r["median"]))
    by_med = max(times, key=lambda r: (r["median"], r["avg"]))
    avg_close = statistics.fmean(d["close"] for d in days)
    return {
        "by_avg": by_avg["t"],
        "avg_at_best": by_avg["avg"],
        "sum_at_best": by_avg["sum"],
        "beat_close_pct": by_avg["beat_close_pct"],
        "by_median": by_med["t"],
        "median_at_best": by_med["median"],
        "avg_close": round(avg_close, 2),
        "sum_close": round(sum(d["close"] for d in days), 2),
        "gain_vs_close": round(by_avg["avg"] - avg_close, 2),
    }


def _when_hist(days: list[dict], key: str) -> list[dict]:
    counts = {b: 0 for b in _buckets()}
    for d in days:
        counts[_bucket(d[key])] = counts.get(_bucket(d[key]), 0) + 1
    return [{"bucket": b, "days": n} for b, n in counts.items()]


def build_day(key: str, label: str, curve: list[float], first: int, last: int, stocks: dict[str, list[float]]) -> dict:
    return {"key": key, "label": label, "curve": curve, "first": GRID[first], "last": GRID[last], **_stats(curve, first, last),
            "stocks": stocks}


def span(points: Iterable[tuple[str, float]]) -> tuple[int, int] | None:
    """The GRID indexes of the first and last logged minute (None without a point inside the session)."""
    idx = [GRID.index(m) for m in (_hhmm(t) for t, _ in points) if m in GRID]
    return (min(idx), max(idx)) if idx else None


def stock_table(days: list[dict], step: int) -> list[dict]:
    """Per stock, across the days it traded: close, high, low, best exit time and its edge over the close."""
    names = sorted({s for d in days for s in d["stocks"]})
    out = []
    for name in names:
        rows = [
            {"curve": d["stocks"][name], "first": GRID.index(d["first"]), "last": GRID.index(d["last"])}
            for d in days
            if name in d["stocks"] and any(abs(v) > 1e-9 for v in d["stocks"][name])
        ]
        if not rows:
            continue
        stats = [_stats(r["curve"], r["first"], r["last"]) for r in rows]
        mine = [{**s, "curve": r["curve"]} for s, r in zip(stats, rows)]
        times = by_time(mine, step)
        best = best_exit(times, mine) or {}
        out.append(
            {
                "symbol": name,
                "days": len(rows),
                "sum_close": round(sum(s["close"] for s in stats), 2),
                "avg_close": round(statistics.fmean(s["close"] for s in stats), 2),
                "avg_high": round(statistics.fmean(s["high"] for s in stats), 2),
                "avg_low": round(statistics.fmean(s["low"] for s in stats), 2),
                "win_days": sum(1 for s in stats if s["close"] > 0),
                "best_exit": best.get("by_avg"),
                "avg_at_best": best.get("avg_at_best"),
                "gain_vs_close": best.get("gain_vs_close"),
            }
        )
    out.sort(key=lambda r: r["sum_close"], reverse=True)
    return out


def analyze(days: list[dict], step: int = 5, stocks: list[str] | None = None) -> dict:
    """The whole dashboard from built days (`build_day`), optionally only the picked stocks' P&L."""
    step = step if step in STEPS else 5
    days = [d for d in days if any(abs(v) > 1e-9 for v in d["curve"])]  # a day with no P&L at all had no trade
    if stocks:
        want = {s.upper() for s in stocks}
        picked = []
        for d in days:
            mine = [c for s, c in d["stocks"].items() if s in want]
            if not mine:
                continue
            curve = add_curves(mine)
            first, last = GRID.index(d["first"]), GRID.index(d["last"])
            picked.append({**d, "curve": curve, **_stats(curve, first, last)})
        days = picked
    times = by_time(days, step)
    closes = [d["close"] for d in days]
    totals = None
    if days:
        totals = {
            "days": len(days),
            "sum_close": round(sum(closes), 2),
            "avg_close": round(statistics.fmean(closes), 2),
            "win_days": sum(1 for c in closes if c > 0),
            "loss_days": sum(1 for c in closes if c < 0),
            "avg_high": round(statistics.fmean(d["high"] for d in days), 2),
            "avg_low": round(statistics.fmean(d["low"] for d in days), 2),
            "best_day": max(days, key=lambda d: d["close"])["label"],
            "best_close": round(max(closes), 2),
            "worst_day": min(days, key=lambda d: d["close"])["label"],
            "worst_close": round(min(closes), 2),
            "avg_giveback": round(statistics.fmean(d["giveback"] for d in days), 2),
        }
    return {
        "grid": [GRID[i] for i in _grid_at(step)],
        "step": step,
        "days": [
            {k: v for k, v in d.items() if k not in ("stocks", "curve")} | {"curve": [d["curve"][i] for i in _grid_at(step)]}
            for d in days
        ],
        "by_time": times,
        "best_exit": best_exit(times, days),
        "high_when": _when_hist(days, "high_at"),
        "low_when": _when_hist(days, "low_at"),
        "totals": totals,
        "stocks": stock_table(days, step),
    }
