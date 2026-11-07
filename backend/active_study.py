"""Most active trend test (read-only; the Scalp page's Most active card, "Which column finds the trend?").

Which Most active column picks the stocks that keep trending? From the saved Most active scans (`scan_history`, kind
`active`, one every few minutes while NSE is open), for each past day and each check time (10:00, 11:00…), the scan
nearest that time is read: every stock is bullish (up on the day, `change_pct` > 0) or bearish (down). Within each side
the stocks are ranked by each column (`PARAMS`: volume vs usual, day move, distance from the average price, buyer /
seller imbalance, speed, day range, money traded, the bias, and all of them together) and the top `top` / bottom `top`
are kept. Their result is read from later saved scans of the same day: the price about an hour later (`HOUR_MIN`) and at
the close (the day's last scan from `CLOSE_FROM`), signed the stock's way. A column that finds the trend has a top whose
"kept going" rate is well above all the stocks' rate (`edge`) and above its bottom (`spread`).

No Groww call, no order, nothing in the strategy reads it.
"""
from __future__ import annotations

import datetime as dt
import statistics
import threading

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DEFAULT_TIMES = ("10:00", "11:00", "12:00", "13:00", "14:00")
TOLERANCE_MIN = 10       # a scan within this many minutes of a check time stands for it
HOUR_MIN = 60
CLOSE_FROM = dt.time(15, 10)  # the day's last scan from here on is "the close"
MIN_DAYS, MAX_DAYS = 1, 60
MIN_SAMPLES = 30         # fewer top picks than this: "few"
STRONG, SOME = 10.0, 5.0  # edge in points


def _f(v) -> float | None:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


# key → (label, help, reader(row, sign) → number or None; bigger = stronger trend that way)
PARAMS: dict[str, tuple[str, str, object]] = {
    "rvol": ("Volume vs usual", "today's volume against its usual for this time", lambda r, s: _f(r.get("rvol"))),
    "change": ("Day move %", "how far it has moved from yesterday's close", lambda r, s: abs(_f(r.get("change_pct")) or 0) or None),
    "vwap": ("Distance from average price", "price above (bullish) / below (bearish) today's average price",
             lambda r, s: (_f(r.get("vwap_dist_pct")) * s) if _f(r.get("vwap_dist_pct")) is not None else None),
    "pressure": ("Buyer / seller imbalance", "more buyers waiting (bullish) / sellers (bearish)",
                 lambda r, s: ((_f(r.get("buy_share")) - 0.5) * s) if _f(r.get("buy_share")) is not None else None),
    "speed": ("Speed now", "% per minute since the scan before, its way",
              lambda r, s: (_f(r.get("speed_pct_min")) * s) if _f(r.get("speed_pct_min")) is not None else None),
    "range": ("Day range %", "today's high − low as % of the open", lambda r, s: _f(r.get("range_pct"))),
    "value": ("Money traded", "₹ crore traded today", lambda r, s: _f(r.get("value_cr"))),
    "bias": ("Bias agrees", "the row's bias is LONG (bullish) / SHORT (bearish): price and average price agree",
             lambda r, s: 1.0 if r.get("bias") == ("LONG" if s > 0 else "SHORT") else 0.0),
}
COMBINED = "combined"
COMBINED_LABEL = "All columns together"
NUMERIC = [k for k in PARAMS if k != "bias"]


def clean_times(raw) -> list[str]:
    """Sorted unique HH:MM between 09:20 and 14:30. Raises ValueError."""
    out: set[str] = set()
    for value in raw or []:
        try:
            h, m = str(value).strip().split(":")
            t = dt.time(int(h), int(m))
        except (ValueError, TypeError):
            raise ValueError(f"{value!r} is not a time (HH:MM).") from None
        if not (dt.time(9, 20) <= t <= dt.time(14, 30)):
            raise ValueError("Check times must be between 09:20 and 14:30 IST.")
        out.add(t.strftime("%H:%M"))
    if not out:
        raise ValueError("Pick at least one check time.")
    return sorted(out)


def _clock(as_of: str) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(as_of)
    except (TypeError, ValueError):
        return None
    return (t if t.tzinfo else t.replace(tzinfo=IST)).astimezone(IST)


def nearest(runs: list[dict], at: dt.datetime, tolerance: int = TOLERANCE_MIN) -> dict | None:
    """The run whose time is nearest `at`, within `tolerance` minutes."""
    best, gap = None, None
    for run in runs:
        t = _clock(run.get("as_of"))
        if t is None:
            continue
        d = abs((t - at).total_seconds())
        if d <= tolerance * 60 and (gap is None or d < gap):
            best, gap = run, d
    return best


def close_run(runs: list[dict]) -> dict | None:
    """The day's last scan from CLOSE_FROM on, or None (the day's scans stopped early)."""
    late = [r for r in runs if (_clock(r.get("as_of")) or dt.datetime.min.replace(tzinfo=IST)).time() >= CLOSE_FROM]
    return max(late, key=lambda r: r["as_of"]) if late else None


def _prices(rows: list[dict]) -> dict[str, float]:
    return {r["symbol"]: float(r["ltp"]) for r in rows or [] if r.get("symbol") and _f(r.get("ltp"))}


def samples_for_day(day: str, runs: list[dict], load_rows, times: list[str]) -> list[dict]:
    """Each stock at each check time of one day, with its later moves. `load_rows(run)` gives a run's rows."""
    out = []
    date = dt.date.fromisoformat(day)
    closing = close_run(runs)
    close_px = _prices(load_rows(closing)) if closing else {}
    for at in times:
        h, m = at.split(":")
        when = dt.datetime.combine(date, dt.time(int(h), int(m)), tzinfo=IST)
        run = nearest(runs, when)
        if run is None:
            continue
        later = nearest(runs, when + dt.timedelta(minutes=HOUR_MIN))
        hour_px = _prices(load_rows(later)) if later else {}
        for row in load_rows(run) or []:
            symbol, price, change = row.get("symbol"), _f(row.get("ltp")), _f(row.get("change_pct"))
            if not symbol or not price or not change:
                continue
            sign = 1 if change > 0 else -1

            def move(px: dict[str, float]) -> float | None:
                p = px.get(symbol)
                return round((p - price) / price * 100 * sign, 4) if p else None

            out.append({
                "day": day,
                "time": at,
                "side": "LONG" if sign > 0 else "SHORT",
                "symbol": symbol,
                "values": {k: PARAMS[k][2](row, sign) for k in PARAMS},
                "hour": move(hour_px),
                "close": move(close_px),
            })
    return out


def _add_combined(group: list[dict]) -> None:
    """Each stock's average place (0 = weakest, 1 = strongest) over the numeric columns, within its group."""
    ranks: dict[int, list[float]] = {id(s): [] for s in group}
    for key in NUMERIC:
        have = [s for s in group if s["values"].get(key) is not None]
        if len(have) < 2:
            continue
        have.sort(key=lambda s: s["values"][key])
        for i, s in enumerate(have):
            ranks[id(s)].append(i / (len(have) - 1))
    for s in group:
        r = ranks[id(s)]
        s["values"][COMBINED] = round(sum(r) / len(r), 4) if r else None


def _stats(rows: list[dict]) -> dict:
    out = {"n": len(rows)}
    for h in ("hour", "close"):
        moves = [s[h] for s in rows if s[h] is not None]
        out[f"{h}_n"] = len(moves)
        out[f"{h}_hit"] = round(100 * sum(1 for m in moves if m > 0) / len(moves), 1) if moves else None
        out[f"{h}_avg"] = round(statistics.fmean(moves), 3) if moves else None
    return out


def _pick(group: list[dict], key: str, top: int) -> tuple[list[dict], list[dict]]:
    """The strongest and weakest `top` of one day / time / side by `key` (a flag splits into yes / no)."""
    have = [s for s in group if s["values"].get(key) is not None]
    if key == "bias":
        return [s for s in have if s["values"][key] > 0], [s for s in have if s["values"][key] <= 0]
    if len({s["values"][key] for s in have}) < 2:
        return [], []  # every stock reads the same: the column tells them apart not at all
    have.sort(key=lambda s: (-s["values"][key], s["symbol"]))
    n = min(top, len(have) // 2)
    return (have[:n], have[-n:]) if n else ([], [])


def _diff(a, b):
    return round(a - b, 1) if a is not None and b is not None else None


def _verdict(edge, top_n) -> str:
    if edge is None or top_n < MIN_SAMPLES:
        return "few"
    return "strong" if edge >= STRONG else "some" if edge >= SOME else "weak" if edge > 0 else "none"


def summarize(samples: list[dict], top: int, times: list[str]) -> dict:
    groups: dict[tuple, list[dict]] = {}
    for s in samples:
        groups.setdefault((s["day"], s["time"], s["side"]), []).append(s)
    for g in groups.values():
        _add_combined(g)

    keys = [*PARAMS, COMBINED]
    tables = []
    for at in ["ALL", *times]:
        for side in ("BOTH", "LONG", "SHORT"):
            chosen = [g for (d, t, sd), g in groups.items() if (at == "ALL" or t == at) and (side == "BOTH" or sd == side)]
            base_rows = [s for g in chosen for s in g]
            base = _stats(base_rows)
            rows = []
            for key in keys:
                tops, bottoms = [], []
                for g in chosen:
                    t_, b_ = _pick(g, key, top)
                    tops += t_
                    bottoms += b_
                ts, bs = _stats(tops), _stats(bottoms)
                label = COMBINED_LABEL if key == COMBINED else PARAMS[key][0]
                help_ = "each stock's average place over the other columns" if key == COMBINED else PARAMS[key][1]
                edge = _diff(ts["close_hit"], base["close_hit"])
                rows.append({
                    "key": key, "label": label, "help": help_, "top": ts, "bottom": bs,
                    "edge_close": edge, "edge_hour": _diff(ts["hour_hit"], base["hour_hit"]),
                    "spread_close": _diff(ts["close_hit"], bs["close_hit"]),
                    "spread_hour": _diff(ts["hour_hit"], bs["hour_hit"]),
                    "verdict": _verdict(edge, ts["close_n"]),
                })
            rows.sort(key=lambda r: (-(r["edge_close"] if r["edge_close"] is not None else -999), r["key"]))
            tables.append({"time": at, "side": side, "base": base, "params": rows})
    return {"tables": tables, "answer": _answer(tables, times, top)}


def _answer(tables: list[dict], times: list[str], top: int) -> list[str]:
    get = {(t["time"], t["side"]): t for t in tables}
    overall = get.get(("ALL", "BOTH"))
    if not overall or not overall["base"]["close_n"]:
        return ["No saved Most active scans with a closing scan in these days yet. The auto scan saves one every few "
                "minutes while the market is open; come back after a few days of it."]
    lines = []
    best = overall["params"][0]
    base = overall["base"]
    if best["edge_close"] is not None and best["edge_close"] > 0:
        lines.append(
            f"Best column overall: {best['label']}. Its top {top} kept moving their way to the close "
            f"{best['top']['close_hit']:.0f}% of the time, against {base['close_hit']:.0f}% for all stocks "
            f"({best['edge_close']:+.0f} points)."
        )
    else:
        lines.append(
            f"No column beat the plain rate: all stocks kept moving their way to the close {base['close_hit']:.0f}% of the "
            "time, and no column's top did better."
        )
    for side, word in (("LONG", "bullish"), ("SHORT", "bearish")):
        t = get.get(("ALL", side))
        if t and t["params"] and t["params"][0]["edge_close"] is not None and t["params"][0]["edge_close"] > 0:
            p = t["params"][0]
            lines.append(f"For {word} stocks: {p['label']} ({p['top']['close_hit']:.0f}% vs {t['base']['close_hit']:.0f}%).")
    best_time = None
    for at in times:
        t = get.get((at, "BOTH"))
        if t and t["params"] and t["params"][0]["edge_close"] is not None and t["params"][0]["top"]["close_n"] >= MIN_SAMPLES:
            p = t["params"][0]
            if best_time is None or p["top"]["close_hit"] > best_time[1]["top"]["close_hit"]:
                best_time = (at, p)
    if best_time:
        at, p = best_time
        lines.append(f"Best time to look: {at}, by {p['label']} ({p['top']['close_hit']:.0f}% kept going to the close).")
    if best["top"]["close_n"] < MIN_SAMPLES:
        lines.append(f"Only {best['top']['close_n']} top picks were measured: too few to trust. Use more days.")
    return lines


_cache: dict[tuple, dict] = {}
_lock = threading.Lock()


def run(days: int, times: list[str], top: int, history=None) -> dict:
    """The test over the last `days` days with saved Most active scans. Blocking (reads the scan history)."""
    if history is None:
        import scan_history as history  # noqa: PLC0415
    day_list = [d["day"] for d in history.days("active", limit=days)]
    metas = {d: history.runs(d, "active") for d in day_list}
    latest = max((r["id"] for rs in metas.values() for r in rs), default=0)
    key = (tuple(day_list), tuple(times), top, latest)
    with _lock:
        if key in _cache:
            return _cache[key]
    loaded: dict[int, list[dict]] = {}

    def rows_of(run_meta: dict | None) -> list[dict]:
        if run_meta is None:
            return []
        rid = run_meta["id"]
        if rid not in loaded:
            got = history.get(rid)
            loaded[rid] = (got or {}).get("rows") or []
        return loaded[rid]

    samples, used = [], []
    for day in sorted(day_list):
        got = samples_for_day(day, metas[day], rows_of, times)
        if got:
            samples += got
            used.append({"day": day, "scans": len(metas[day]), "closed": close_run(metas[day]) is not None})
    out = {
        "days": used,
        "times": times,
        "top": top,
        "samples": len(samples),
        "tolerance_min": TOLERANCE_MIN,
        **summarize(samples, top, times),
    }
    with _lock:
        if len(_cache) > 8:
            _cache.clear()
        _cache[key] = out
    return out
