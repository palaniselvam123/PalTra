"""Buyers / sellers test (read-only; the Scalp page's Most active card, "Buyers / sellers test").

The owner's idea: when buyers hold 75 % or more of the waiting orders the price goes up, and when sellers do it goes
down. This measures it on the saved Most active scans (`scan_history`, kind `active`, one every few minutes while NSE
is open), whose rows carry the real last traded price (`ltp`) and the buyers' share of the order book (`buy_share`).

A **signal** is the scan in which a stock's buyers (BUY) or sellers (SELL) first reach `pct` % after a scan in which
they did not; the same stock and side gives a new signal only `gap_min` minutes later, so one long one-sided stretch is
not counted fifty times. For each signal the price is followed in the later saved scans of the same day: `HORIZONS`
minutes later (the scan nearest that time, within `TOLERANCE_MIN`) and at the close (the day's last scan from
`CLOSE_FROM`), signed the signal's way (+ = it went the way the idea says), plus the best and worst move within an hour.
The **base rate** is how often any stock, at any scan, moved that way over the same time: the idea is only worth
something when the signals beat it (`edge`, in points).

No Groww call, no order, nothing in the strategy reads it.
"""
from __future__ import annotations

import bisect
import statistics
import threading

from active_study import CLOSE_FROM, MAX_DAYS, MIN_DAYS, _clock, _f

HORIZONS = (5, 15, 30, 60)
TOLERANCE_MIN = 4
THRESHOLDS = (60, 65, 70, 75, 80, 85, 90)
MIN_SIGNALS = 30          # fewer signals than this: "few"
STRONG, SOME = 10.0, 5.0  # edge in points
BASE_EVERY = 3            # the base rate reads every 3rd scan of each stock (plenty, and quick)
KEEP_SIGNALS = 200
SESSION_FROM, SESSION_TO = 9 * 60 + 15, 15 * 60 + 30  # IST minutes: scans outside the session are left out
FROZEN_SHARE = 0.05       # a day on which under 5 % of the stocks' prices ever changed: the market was closed
_lock = threading.Lock()
_cache: dict[tuple, dict] = {}


def _minute(t) -> int:
    return t.hour * 60 + t.minute


def in_session(meta: dict) -> bool:
    """A scan taken while NSE trades (09:15-15:30 IST): a scan after the close (a Scan now in the evening) reads an
    order book nobody trades against, so it is no signal and its price is no trade."""
    t = _clock(meta.get("as_of"))
    return t is not None and SESSION_FROM <= _minute(t) < SESSION_TO


def frozen(series: dict) -> bool:
    """True when hardly any stock's price changed across the day's scans: a holiday (the auto scan has no holiday list,
    so on such a day it saves Groww's last prices over and over)."""
    long = [v[1] for v in series.values() if len(v[1]) >= 3]
    if len(long) < 5:
        return False
    return sum(1 for p in long if len(set(p)) > 1) / len(long) < FROZEN_SHARE


def day_series(runs: list[dict], rows_of) -> dict[str, tuple[list[int], list[float], list[float | None], list[str]]]:
    """Each stock's (minutes, prices, buyers' shares, read clocks) over one day's saved scans, in time order.

    A row's own `read_at` (when that stock's order book was read) is used when the scan saved it; older scans only
    have the scan's start (`as_of`), so their clock is HH:MM and the stock may really have been read a little later."""
    out: dict[str, tuple[list[int], list[float], list[float | None], list[str]]] = {}
    for run in sorted(runs, key=lambda r: r.get("as_of") or ""):
        start = _clock(run.get("as_of"))
        if start is None or not in_session(run):
            continue
        for row in rows_of(run):
            price = _f(row.get("ltp"))
            symbol = row.get("symbol")
            if not symbol or not price:
                continue
            read = _clock(row.get("read_at")) if row.get("read_at") else None
            t = read or start
            minute = _minute(t)
            times, prices, shares, clocks = out.setdefault(symbol, ([], [], [], []))
            if times and times[-1] >= minute:
                continue
            times.append(minute)
            prices.append(price)
            shares.append(_f(row.get("buy_share")))
            clocks.append(t.strftime("%H:%M:%S") if read else t.strftime("%H:%M"))
    return out


def _at(times: list[int], minute: int) -> int | None:
    """Index of the scan nearest `minute`, within TOLERANCE_MIN; None when the stock was not scanned then."""
    i = bisect.bisect_left(times, minute)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(times) and abs(times[j] - minute) <= TOLERANCE_MIN:
            if best is None or abs(times[j] - minute) < abs(times[best] - minute):
                best = j
    return best


def _moves(times: list[int], prices: list[float], i: int, sign: int) -> dict:
    """Signed % moves after scan `i`: each horizon, the close, and the best / worst within an hour."""
    entry = prices[i]
    out: dict = {}
    for h in HORIZONS:
        j = _at(times, times[i] + h)
        out[str(h)] = round((prices[j] - entry) / entry * 100 * sign, 3) if j is not None and j > i else None
    last = len(times) - 1
    closed = times[last] >= _minute(CLOSE_FROM) and last > i
    out["close"] = round((prices[last] - entry) / entry * 100 * sign, 3) if closed else None
    window = [(prices[j] - entry) / entry * 100 * sign for j in range(i + 1, len(times)) if times[j] - times[i] <= 60]
    out["best"] = round(max(window), 3) if window else None
    out["worst"] = round(min(window), 3) if window else None
    return out


def signals_for(series: dict, pct: float, gap_min: int, day: str) -> list[dict]:
    """Every fresh buyers / sellers signal of one day at `pct` %."""
    need = pct / 100.0
    found = []
    for symbol, (times, prices, shares, *_clocks) in series.items():
        last_at = {"BUY": -10_000, "SELL": -10_000}
        was = {"BUY": True, "SELL": True}  # the day's first scan never starts a signal: nothing came before it
        for i, share in enumerate(shares):
            if share is None:
                was = {"BUY": False, "SELL": False}
                continue
            now = {"BUY": share >= need, "SELL": 1.0 - share >= need}
            for side in ("BUY", "SELL"):
                if now[side] and not was[side] and times[i] - last_at[side] >= gap_min:
                    last_at[side] = times[i]
                    held = share if side == "BUY" else 1.0 - share
                    found.append({
                        "day": day, "time": f"{times[i] // 60:02d}:{times[i] % 60:02d}", "symbol": symbol, "side": side,
                        "share_pct": round(held * 100, 1), "price": prices[i],
                        **_moves(times, prices, i, 1 if side == "BUY" else -1),
                    })
            was = now
    return found


def base_moves(series: dict) -> dict[str, list[float]]:
    """Unsigned % moves of every stock after every 3rd scan, per horizon: what the price does anyway."""
    out: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    out["close"] = []
    for times, prices, *_rest in series.values():
        for i in range(0, len(times), BASE_EVERY):
            got = _moves(times, prices, i, 1)
            for key in out:
                if got[key] is not None:
                    out[key].append(got[key])
    return out


def _verdict(edge: float | None, n: int) -> str:
    if n < MIN_SIGNALS or edge is None:
        return "few"
    return "strong" if edge >= STRONG else "some" if edge >= SOME else "weak" if edge > 0 else "none"


def _side_stats(signals: list[dict], base: dict[str, list[float]], side: str) -> dict:
    mine = [s for s in signals if s["side"] == side]
    sign = 1 if side == "BUY" else -1
    rows = []
    for key in [str(h) for h in HORIZONS] + ["close"]:
        moves = [s[key] for s in mine if s[key] is not None]
        pool = base.get(key) or []
        right = round(sum(m > 0 for m in moves) / len(moves) * 100, 1) if moves else None
        base_right = round(sum(m * sign > 0 for m in pool) / len(pool) * 100, 1) if pool else None
        edge = round(right - base_right, 1) if right is not None and base_right is not None else None
        rows.append({
            "after": key, "signals": len(moves), "right_pct": right,
            "avg_pct": round(statistics.fmean(moves), 3) if moves else None,
            "median_pct": round(statistics.median(moves), 3) if moves else None,
            "base_right_pct": base_right, "edge": edge, "verdict": _verdict(edge, len(moves)),
        })
    best = [s["best"] for s in mine if s["best"] is not None]
    worst = [s["worst"] for s in mine if s["worst"] is not None]
    return {
        "side": side, "signals": len(mine), "after": rows,
        "avg_best_pct": round(statistics.fmean(best), 3) if best else None,
        "avg_worst_pct": round(statistics.fmean(worst), 3) if worst else None,
    }


def _answer(sides: list[dict], pct: float, days: int) -> list[str]:
    lines = []
    for s in sides:
        who = "buyers" if s["side"] == "BUY" else "sellers"
        way = "up" if s["side"] == "BUY" else "down"
        row = next((r for r in s["after"] if r["after"] == "30"), None)
        if not s["signals"]:
            lines.append(f"No scan had {who} at {pct:g}% or more in these days.")
            continue
        if row is None or row["right_pct"] is None:
            lines.append(f"{s['signals']} times {who} reached {pct:g}%, but no later scan to follow them.")
            continue
        text = (f"When {who} reached {pct:g}%, the price was {way} 30 minutes later {row['right_pct']:g}% of the time "
                f"({row['signals']} signals, average move {row['avg_pct']:+.2f}% that way).")
        if row["base_right_pct"] is not None:
            text += f" Any stock at any time went {way} {row['base_right_pct']:g}% of the time, so the edge is {row['edge']:+g} points:"
            text += {"strong": " a strong sign.", "some": " a useful sign.", "weak": " barely better than chance.",
                     "none": " no better than chance.", "few": " too few signals to trust yet."}[row["verdict"]]
        lines.append(text)
    if days < 5:
        lines.append(f"Only {days} day{'' if days == 1 else 's'} of saved scans: treat this as a first look, not proof.")
    return lines


def run(days: int = 20, pct: float = 75.0, gap_min: int = 30, history=None) -> dict:
    """The test over the last `days` days with saved Most active scans. Blocking (reads the scan history)."""
    if history is None:
        import scan_history as history  # noqa: PLC0415
    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
    pct = max(55.0, min(95.0, float(pct)))
    gap_min = max(5, min(240, int(gap_min)))
    day_list = [d["day"] for d in history.days("active", limit=days)]
    metas = {d: history.runs(d, "active") for d in day_list}
    latest = max((r["id"] for rs in metas.values() for r in rs), default=0)
    key = (tuple(day_list), pct, gap_min, latest)
    with _lock:
        if key in _cache:
            return _cache[key]

    def rows_of(meta: dict) -> list[dict]:
        return (history.get(meta["id"]) or {}).get("rows") or []

    thresholds = sorted({*THRESHOLDS, pct})
    signals: list[dict] = []
    by_thr: dict[float, list[dict]] = {t: [] for t in thresholds}
    base: dict[str, list[float]] = {str(h): [] for h in HORIZONS}
    base["close"] = []
    used = []
    for day in sorted(day_list):
        series = day_series(metas[day], rows_of)
        if not series or frozen(series):
            continue
        used.append({"day": day, "scans": sum(1 for m in metas[day] if in_session(m))})
        for t in thresholds:
            by_thr[t] += signals_for(series, t, gap_min, day)
        for k, v in base_moves(series).items():
            base[k] += v
    signals = by_thr[pct]
    sides = [_side_stats(signals, base, side) for side in ("BUY", "SELL")]
    table = []
    for t in thresholds:
        for side in ("BUY", "SELL"):
            row = next(r for r in _side_stats(by_thr[t], base, side)["after"] if r["after"] == "30")
            table.append({"pct": t, "side": side, **{k: row[k] for k in ("signals", "right_pct", "avg_pct", "edge", "verdict")}})
    out = {
        "days": used, "pct": pct, "gap_min": gap_min, "horizons": list(HORIZONS), "tolerance_min": TOLERANCE_MIN,
        "sides": sides, "by_threshold": table, "answer": _answer(sides, pct, len(used)),
        "signals": sorted(signals, key=lambda s: (s["day"], s["time"]), reverse=True)[:KEEP_SIGNALS],
        "total_signals": len(signals),
    }
    with _lock:
        if len(_cache) > 8:
            _cache.clear()
        _cache[key] = out
    return out


# ---- Day list: every stock that reached the share on a day, and whether the price then moved enough -------------

def reached_for_day(series: dict, pct: float, move_pct: float, day: str) -> list[dict]:
    """Each stock and side that reached `pct` % at any saved scan of the day (the first time counts), and what the
    real price did afterwards: the furthest it went its way, whether it reached `move_pct` %, and when."""
    need = pct / 100.0
    rows = []
    for symbol, (times, prices, shares, *more) in series.items():
        clocks = more[0] if more else [f"{m // 60:02d}:{m % 60:02d}" for m in times]
        for side in ("BUY", "SELL"):
            held = [None if s is None else (s if side == "BUY" else 1.0 - s) for s in shares]
            hits = [i for i, h in enumerate(held) if h is not None and h >= need]
            if not hits:
                continue
            i, sign, entry = hits[0], (1 if side == "BUY" else -1), prices[hits[0]]
            after = [((prices[j] - entry) / entry * 100 * sign, j) for j in range(i + 1, len(times))]
            best = max(after, default=None)
            worst = min(after, default=None)
            got = next(((m, j) for m, j in after if m >= move_pct), None)
            clock = lambda j: f"{times[j] // 60:02d}:{times[j] % 60:02d}"  # noqa: E731
            below = next((k for k in range(i - 1, -1, -1) if held[k] is not None and held[k] < need), None)
            trade = times[i] + 1  # the share was read at first_at; an order can go at the open of the next minute
            rows.append({
                "day": day, "symbol": symbol, "side": side, "first_at": clocks[i],
                "trade_at": f"{trade // 60:02d}:{trade % 60:02d}",
                # The share is only read at each scan: it crossed `pct` after the last read below it (`below_at`,
                # None when the day's first read already had it) and at or before `first_at`.
                "below_at": clocks[below] if below is not None else None, "share_pct": round(held[i] * 100, 1),
                "max_share_pct": round(max(held[k] for k in hits) * 100, 1), "scans_at": len(hits), "price": entry,
                "best_pct": round(best[0], 3) if best else None, "best_at": clock(best[1]) if best else None,
                "worst_pct": round(worst[0], 3) if worst else None,
                "last_pct": round(after[-1][0], 3) if after else None, "last_price": prices[-1],
                "moved": bool(got) if after else None,
                "moved_at": clock(got[1]) if got else None,
                "minutes_to_move": (times[got[1]] - times[i]) if got else None,
            })
    rows.sort(key=lambda r: (r["day"], r["first_at"], r["symbol"]))
    return rows


def _day_stats(rows: list[dict], move_pct: float) -> list[dict]:
    out = []
    for key, label in (("ALL", "Buyers and sellers"), ("BUY", "Buyers → price up"), ("SELL", "Sellers → price down")):
        mine = [r for r in rows if (key == "ALL" or r["side"] == key) and r["moved"] is not None]
        right = [r for r in mine if r["moved"]]
        mins = [r["minutes_to_move"] for r in right]
        best = [r["best_pct"] for r in mine if r["best_pct"] is not None]
        ended = [r for r in mine if (r["last_pct"] or 0) > 0]
        out.append({
            "side": key, "label": label, "stocks": len(mine), "moved": len(right),
            "correct_pct": round(len(right) / len(mine) * 100, 1) if mine else None,
            "avg_minutes": round(statistics.fmean(mins)) if mins else None,
            "avg_best_pct": round(statistics.fmean(best), 3) if best else None,
            "ended_right_pct": round(len(ended) / len(mine) * 100, 1) if mine else None,
        })
    return out


def reached(days: int = 20, pct: float = 75.0, move_pct: float = 0.5, day: str | None = None, history=None) -> dict:
    """Every stock that reached `pct` % buyers / sellers, per day, and how often the price then moved `move_pct` %
    its way before the day's last saved scan. `day` limits it to one day. Blocking (reads the scan history)."""
    if history is None:
        import scan_history as history  # noqa: PLC0415
    days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
    pct = max(50.0, min(99.0, float(pct)))
    move_pct = max(0.05, min(20.0, float(move_pct)))
    listed = [d["day"] for d in history.days("active", limit=days)]
    wanted = [d for d in listed if day in (None, "", d)]

    def rows_of(meta: dict) -> list[dict]:
        return (history.get(meta["id"]) or {}).get("rows") or []

    rows: list[dict] = []
    used, left_out = [], []
    for d in sorted(wanted):
        metas = history.runs(d, "active")
        inside = sum(1 for m in metas if in_session(m))
        if inside < len(metas):
            left_out.append({"day": d, "why": "after_hours", "scans": len(metas) - inside})
        series = day_series(metas, rows_of)
        if not series:
            continue
        if frozen(series):
            left_out.append({"day": d, "why": "closed", "scans": inside})
            continue
        got = reached_for_day(series, pct, move_pct, d)
        used.append({"day": d, "scans": inside, "stocks": len(got)})
        rows += got
    stats = _day_stats(rows, move_pct)
    answer = []
    judged = stats[0]
    if not rows:
        answer.append(f"No stock reached {pct:g}% buyers or sellers in the saved scans of {'that day' if day else 'these days'}.")
    else:
        for s in stats[1:]:
            if s["stocks"]:
                who, way = ("buyers", "up") if s["side"] == "BUY" else ("sellers", "down")
                answer.append(
                    f"{s['stocks']} time{'s' if s['stocks'] != 1 else ''} {who} reached {pct:g}%: the price then went {way} "
                    f"{move_pct:g}% or more {s['moved']} time{'s' if s['moved'] != 1 else ''} ({s['correct_pct']:g}% correct)"
                    + (f", taking about {s['avg_minutes']} minutes." if s["avg_minutes"] is not None else ".")
                )
        if judged["correct_pct"] is None:
            answer.append("No later saved scan that day yet, so whether the price moved is not known.")
        else:
            answer.append(f"Overall your assumption was right {judged['correct_pct']:g}% of the time at a {move_pct:g}% move ({judged['moved']} of {judged['stocks']}).")
        if judged["stocks"] < MIN_SIGNALS:
            answer.append(f"Only {judged['stocks']} cases: too few to trust yet. Add more days.")
    return {
        "days": used, "all_days": listed, "day": day or None, "pct": pct, "move_pct": move_pct, "left_out": left_out,
        "stats": stats, "answer": answer, "rows": rows[-500:], "total_rows": len(rows),
    }
