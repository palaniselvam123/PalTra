"""Compare backtests, trade by trade (read-only; the Compare page's detailed part, next to backtest_compare.py).

backtest_compare.py answers "which run is best" from day and stock totals. This module answers "what did the runs do
differently": every closed trade of each run (entry / exit time and reason, time in the trade, the buyers % read at
its entry), each run's wins and losers, how far two runs took the same trades (`match` / `overlap`: the overlap
rate, same trade but a different exit, the same moment on the other side, trades only one run took, stocks only one
run traded), plain-words `takeaways`, and for one stock and day each run's P&L minute by minute (`stock_day`, from
the replay P&L log) with its entries and exits and the day's buyers % reads.

No order path; nothing in the strategy reads it.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import defaultdict

STOPPED_REASON = "REPLAY_STOPPED"
MAX_TRADES = 6000          # sent to the page (the newest are kept when a comparison has more)
MATCH_MIN = 5              # entries this many minutes apart on the same stock, day and side are the same trade
MAX_LIST = 60              # rows per discrepancy / unmatched list
MIN_READS = 8              # buyers % takeaways need at least this many trades with a read
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _r(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _epoch(naive_ist: dt.datetime | None) -> float | None:
    return None if naive_ist is None else naive_ist.replace(tzinfo=IST).timestamp()


def _trend(raw: str | None) -> str | None:
    try:
        data = json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None
    return str(data.get("phase") or "") or None if isinstance(data, dict) else None


def load(run_ids: list[int], reads: bool = True) -> dict[int, list[dict]]:
    """Each run's closed trades (a run stopped mid-day leaves REPLAY_STOPPED rows: left out), oldest first."""
    from database import session_factory
    from models import TradeLog

    with session_factory()() as db:
        rows = (
            db.query(TradeLog)
            .filter(TradeLog.run_id.in_(run_ids), TradeLog.exit_price.isnot(None))
            .order_by(TradeLog.entry_time, TradeLog.id)
            .all()
        )
        out: dict[int, list[dict]] = {int(i): [] for i in run_ids}
        for t in rows:
            if (t.exit_reason or "") == STOPPED_REASON or t.entry_time is None or t.exit_time is None:
                continue
            entry_ts, exit_ts = _epoch(t.entry_time), _epoch(t.exit_time)
            qty = int(t.qty or 0)
            sign = 1.0 if (t.direction or "LONG").upper() == "LONG" else -1.0
            best = worst = None
            if t.max_high is not None and t.max_low is not None and t.entry_price:
                up, down = float(t.max_high) - float(t.entry_price), float(t.max_low) - float(t.entry_price)
                best, worst = (up, down) if sign > 0 else (-down, -up)
            out[int(t.run_id)].append(
                {
                    "id": int(t.id),
                    "run_id": int(t.run_id),
                    "date": t.date,
                    "symbol": (t.symbol or "").upper(),
                    "side": (t.direction or "LONG").upper(),
                    "qty": qty,
                    "entry_at": t.entry_time.strftime("%H:%M:%S"),
                    "exit_at": t.exit_time.strftime("%H:%M:%S"),
                    "entry_ts": entry_ts,
                    "exit_ts": exit_ts,
                    "entry_price": _r(t.entry_price),
                    "exit_price": _r(t.exit_price),
                    "gross": _r(t.gross_pnl or 0.0),
                    "net": _r(t.net_pnl if t.net_pnl is not None else (t.gross_pnl or 0.0)),
                    "entry_reason": t.entry_reason or "",
                    "exit_reason": t.exit_reason or "",
                    "hold_min": _r(max(0.0, (exit_ts - entry_ts) / 60.0), 1),
                    "best_pts": _r(best),
                    "worst_pts": _r(worst),
                    "trend": _trend(t.entry_regime),
                    "buy_pct": None,
                    "rvol": None,
                    "value_cr": None,
                }
            )
    if reads:
        add_reads([t for rows_ in out.values() for t in rows_])
    return out


def add_reads(trades: list[dict]) -> None:
    """The buyers % (and volume vs usual, money traded) of the latest saved Most active read before each entry."""
    try:
        import pressure_exit
    except Exception:  # noqa: BLE001
        return
    for t in trades:
        try:
            read = pressure_exit.read_before(t["symbol"], float(t["entry_ts"]))
        except Exception:  # noqa: BLE001
            read = None
        if read:
            t["buy_pct"] = read.get("buy_pct")
            t["rvol"] = read.get("rvol")
            t["value_cr"] = read.get("value_cr")
            t["read_at"] = dt.datetime.fromtimestamp(float(read["time"]), IST).strftime("%H:%M")


def side_pct(trade: dict) -> float | None:
    """The share of the waiting orders on the trade's own side at its entry (buyers for a buy, sellers for a sell)."""
    buy = trade.get("buy_pct")
    if buy is None:
        return None
    return float(buy) if trade["side"] == "LONG" else 100.0 - float(buy)


def _avg(values: list[float]) -> float | None:
    return _r(sum(values) / len(values)) if values else None


def _group(trades: list[dict], field: str, key: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[str(t.get(field) or "-").split(":")[0]].append(t)
    return sorted(
        (
            {
                "name": name,
                "trades": len(rows),
                "wins": sum(1 for t in rows if t[key] > 0),
                "losses": sum(1 for t in rows if t[key] < 0),
                "pnl": _r(sum(t[key] for t in rows)),
                "avg_hold": _avg([t["hold_min"] for t in rows]),
            }
            for name, rows in groups.items()
        ),
        key=lambda g: -g["trades"],
    )


def stats(trades: list[dict], key: str = "gross") -> dict:
    """One run's trades in numbers: wins and losers, stocks, sides, time in the trade, by entry and exit reason."""
    wins = [t for t in trades if t[key] > 0]
    losses = [t for t in trades if t[key] < 0]
    by_stock: dict[str, float] = defaultdict(float)
    for t in trades:
        by_stock[t["symbol"]] += t[key]
    holds = [t["hold_min"] for t in trades]
    longs = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]
    top = max(trades, key=lambda t: t[key], default=None)
    bottom = min(trades, key=lambda t: t[key], default=None)
    n = len(trades)
    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "flat": n - len(wins) - len(losses),
        "win_rate": _r(100.0 * len(wins) / n, 1) if n else None,
        "pnl": _r(sum(t[key] for t in trades)),
        "avg_win": _avg([t[key] for t in wins]),
        "avg_loss": _avg([t[key] for t in losses]),
        "stocks": len(by_stock),
        "stocks_won": sum(1 for v in by_stock.values() if v > 0),
        "stocks_lost": sum(1 for v in by_stock.values() if v < 0),
        "avg_hold": _avg(holds),
        "median_hold": _r(statistics.median(holds), 1) if holds else None,
        "avg_hold_win": _avg([t["hold_min"] for t in wins]),
        "avg_hold_loss": _avg([t["hold_min"] for t in losses]),
        "longs": len(longs),
        "long_wins": sum(1 for t in longs if t[key] > 0),
        "long_pnl": _r(sum(t[key] for t in longs)),
        "shorts": len(shorts),
        "short_wins": sum(1 for t in shorts if t[key] > 0),
        "short_pnl": _r(sum(t[key] for t in shorts)),
        "best_trade": {"symbol": top["symbol"], "date": top["date"], "pnl": top[key]} if top else None,
        "worst_trade": {"symbol": bottom["symbol"], "date": bottom["date"], "pnl": bottom[key]} if bottom else None,
        "by_entry": _group(trades, "entry_reason", key),
        "by_exit": _group(trades, "exit_reason", key),
    }


# ---- the same trade in two runs -------------------------------------------------------------------------------------


def _pair(a: list[dict], b: list[dict], tol_sec: float) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
    """Pair the nearest entries of two lists (same stock and day) within the tolerance; the rest are left over."""
    pairs: list[tuple[dict, dict]] = []
    free = list(b)
    left: list[dict] = []
    for ta in a:
        best = None
        for tb in free:
            gap = abs(tb["entry_ts"] - ta["entry_ts"])
            if gap <= tol_sec and (best is None or gap < best[0]):
                best = (gap, tb)
        if best is None:
            left.append(ta)
        else:
            free.remove(best[1])
            pairs.append((ta, best[1]))
    return pairs, left, free


def match(a: list[dict], b: list[dict], tol_min: float = MATCH_MIN) -> dict:
    """Which trades of run A are the same trades in run B.

    `same`: same stock, day and side, entered within `tol_min` minutes. `opposite`: the same stock and moment but the
    other side (one run bought where the other sold). `only_a` / `only_b`: trades the other run did not take."""
    tol = float(tol_min) * 60.0
    by_a: dict[tuple, list[dict]] = defaultdict(list)
    by_b: dict[tuple, list[dict]] = defaultdict(list)
    for t in a:
        by_a[(t["date"], t["symbol"])].append(t)
    for t in b:
        by_b[(t["date"], t["symbol"])].append(t)
    same: list[tuple[dict, dict]] = []
    opposite: list[tuple[dict, dict]] = []
    only_a: list[dict] = []
    only_b: list[dict] = []
    for spot in sorted(set(by_a) | set(by_b)):
        left_a, left_b = by_a.get(spot, []), by_b.get(spot, [])
        here: list[tuple[dict, dict]] = []
        for side in ("LONG", "SHORT"):
            pairs, _, _ = _pair([t for t in left_a if t["side"] == side], [t for t in left_b if t["side"] == side], tol)
            here.extend(pairs)
        same.extend(here)
        taken = {id(x) for p in here for x in p}
        left_a = [t for t in left_a if id(t) not in taken]
        left_b = [t for t in left_b if id(t) not in taken]
        pairs, rest_a, rest_b = _pair(left_a, left_b, tol)
        opposite.extend(pairs)
        only_a.extend(rest_a)
        only_b.extend(rest_b)
    return {"same": same, "opposite": opposite, "only_a": only_a, "only_b": only_b}


def _brief(t: dict, key: str) -> dict:
    return {
        "date": t["date"], "symbol": t["symbol"], "side": t["side"], "entry_at": t["entry_at"][:5],
        "exit_at": t["exit_at"][:5], "entry_reason": t["entry_reason"], "exit_reason": t["exit_reason"],
        "hold_min": t["hold_min"], "pnl": t[key],
    }


def overlap(a: list[dict], b: list[dict], key: str = "gross", tol_min: float = MATCH_MIN) -> dict:
    """How far run B took run A's trades, and where the two parted."""
    m = match(a, b, tol_min)
    same, opposite = m["same"], m["opposite"]
    union = len(same) + len(opposite) + len(m["only_a"]) + len(m["only_b"])
    tol = float(tol_min) * 60.0
    diff_reason = [(x, y) for x, y in same if (x["exit_reason"] or "") != (y["exit_reason"] or "")]
    diff_time = [(x, y) for x, y in same if abs(x["exit_ts"] - y["exit_ts"]) > tol]
    same_exit = [(x, y) for x, y in same if (x["exit_reason"] or "") == (y["exit_reason"] or "")
                 and abs(x["exit_ts"] - y["exit_ts"]) <= tol]
    kept = {(id(x), id(y)) for x, y in same_exit}
    apart = sorted(
        [(x, y) for x, y in same if (id(x), id(y)) not in kept], key=lambda p: -abs(p[1][key] - p[0][key])
    )
    stocks_a, stocks_b = {t["symbol"] for t in a}, {t["symbol"] for t in b}
    return {
        "tol_min": tol_min,
        "same": len(same),
        "opposite": len(opposite),
        "only_a": len(m["only_a"]),
        "only_b": len(m["only_b"]),
        "union": union,
        "rate": _r(100.0 * len(same) / union, 1) if union else None,
        "same_exit": len(same_exit),
        "diff_exit_reason": len(diff_reason),
        "diff_exit_time": len(diff_time),
        "same_pnl_a": _r(sum(x[key] for x, _ in same)),
        "same_pnl_b": _r(sum(y[key] for _, y in same)),
        "opposite_pnl_a": _r(sum(x[key] for x, _ in opposite)),
        "opposite_pnl_b": _r(sum(y[key] for _, y in opposite)),
        "only_a_pnl": _r(sum(t[key] for t in m["only_a"])),
        "only_b_pnl": _r(sum(t[key] for t in m["only_b"])),
        "stocks_both": len(stocks_a & stocks_b),
        "stocks_only_a": sorted(stocks_a - stocks_b),
        "stocks_only_b": sorted(stocks_b - stocks_a),
        "apart": [{"a": _brief(x, key), "b": _brief(y, key), "diff": _r(y[key] - x[key])} for x, y in apart[:MAX_LIST]],
        "apart_total": len(apart),
        "opposite_rows": [
            {"a": _brief(x, key), "b": _brief(y, key), "diff": _r(y[key] - x[key])}
            for x, y in sorted(opposite, key=lambda p: -abs(p[1][key] - p[0][key]))[:MAX_LIST]
        ],
        "only_a_rows": [_brief(t, key) for t in sorted(m["only_a"], key=lambda t: -abs(t[key]))[:MAX_LIST]],
        "only_b_rows": [_brief(t, key) for t in sorted(m["only_b"], key=lambda t: -abs(t[key]))[:MAX_LIST]],
    }


# ---- what to take away ----------------------------------------------------------------------------------------------


def _money(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{'-' if value < 0 else ''}₹{abs(value):,.0f}"


def _mins(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.0f} min" if value < 120 else f"{value / 60:.1f} h"


def takeaways(ids: list[int], base: int, per_run: dict[int, dict], overlaps: dict[int, dict],
              trades: dict[int, list[dict]], key: str) -> list[dict]:
    """Plain-words findings, each with a tone (good / bad / info) for the page's colours."""
    out: list[dict] = []
    ranked = sorted(ids, key=lambda i: -(per_run[i]["pnl"] or 0.0))
    top = ranked[0]
    s = per_run[top]
    if s["trades"]:
        out.append({"tone": "good" if (s["pnl"] or 0) > 0 else "bad", "title": "Most money",
                    "text": f"Run #{top} made {_money(s['pnl'])} from {s['trades']} trades: {s['wins']} winners, "
                            f"{s['losses']} losers ({s['win_rate']:g}% won) on {s['stocks']} stocks."})
    rates = [(i, per_run[i]["win_rate"]) for i in ids if per_run[i]["win_rate"] is not None]
    if len(rates) > 1:
        hi = max(rates, key=lambda r: r[1])
        lo = min(rates, key=lambda r: r[1])
        if hi[0] != top and hi[1] - lo[1] >= 3:
            out.append({"tone": "info", "title": "Most winners",
                        "text": f"Run #{hi[0]} won most often ({hi[1]:g}% of its trades) but did not make the most "
                                f"money: its winners are smaller or its losers bigger than run #{top}'s."})
    for i in ids:
        s = per_run[i]
        if s["avg_win"] is not None and s["avg_loss"] is not None and s["avg_loss"] < 0:
            ratio = s["avg_win"] / -s["avg_loss"]
            if ratio < 1 and (s["win_rate"] or 0) < 60:
                out.append({"tone": "bad", "title": f"Run #{i}: losers bigger than winners",
                            "text": f"An average winner makes {_money(s['avg_win'])} and an average loser costs "
                                    f"{_money(s['avg_loss'])}, while only {s['win_rate']:g}% of trades win."})
    for other, o in overlaps.items():
        if not o["union"]:
            continue
        tone = "info"
        out.append({"tone": tone, "title": f"Runs #{base} and #{other}: {o['rate']:g}% the same trades",
                    "text": f"{o['same']} of {o['union']} trades were the same in both (same stock, side and entry "
                            f"within {o['tol_min']:g} min); run #{base} took {o['only_a']} that #{other} did not, "
                            f"#{other} took {o['only_b']} that #{base} did not"
                            + (f", and {o['opposite']} times they went opposite ways" if o["opposite"] else "") + "."})
        gap_same = (o["same_pnl_b"] or 0) - (o["same_pnl_a"] or 0)
        gap_only = (o["only_b_pnl"] or 0) - (o["only_a_pnl"] or 0)
        if abs(gap_same) + abs(gap_only) > 0:
            if abs(gap_only) >= abs(gap_same):
                out.append({"tone": "good" if gap_only > 0 else "bad", "title": "The gap is in which trades were taken",
                            "text": f"Trades only run #{other} took made {_money(o['only_b_pnl'])}; trades only run "
                                    f"#{base} took made {_money(o['only_a_pnl'])}. That explains more of the "
                                    f"difference than the exits do: look at the entry rules and checks."})
            else:
                out.append({"tone": "good" if gap_same > 0 else "bad", "title": "The gap is in the exits",
                            "text": f"On the {o['same']} trades both took, run #{other} made {_money(o['same_pnl_b'])} "
                                    f"and run #{base} {_money(o['same_pnl_a'])}; {o['diff_exit_reason']} of them closed "
                                    f"for a different reason. Look at the exit rules and stops."})
        if o["stocks_only_a"] or o["stocks_only_b"]:
            out.append({"tone": "info", "title": "Stocks only one run traded",
                        "text": f"Run #{base} only: {', '.join(o['stocks_only_a'][:8]) or 'none'}"
                                f"{' …' if len(o['stocks_only_a']) > 8 else ''}. Run #{other} only: "
                                f"{', '.join(o['stocks_only_b'][:8]) or 'none'}{' …' if len(o['stocks_only_b']) > 8 else ''}."})
    holds = [(i, per_run[i]["avg_hold"]) for i in ids if per_run[i]["avg_hold"] is not None]
    if len(holds) > 1:
        long_, short_ = max(holds, key=lambda h: h[1]), min(holds, key=lambda h: h[1])
        if long_[1] >= 1.3 * max(short_[1], 0.1):
            out.append({"tone": "info", "title": "Time in a trade",
                        "text": f"Run #{long_[0]} stays in a trade {_mins(long_[1])} on average, run #{short_[0]} "
                                f"{_mins(short_[1])}."})
    for i in ids:
        s = per_run[i]
        if s["avg_hold_win"] and s["avg_hold_loss"] and s["avg_hold_loss"] >= 1.5 * s["avg_hold_win"] and s["losses"] >= 5:
            out.append({"tone": "bad", "title": f"Run #{i}: losers are held longer than winners",
                        "text": f"Losers stay open {_mins(s['avg_hold_loss'])} on average, winners "
                                f"{_mins(s['avg_hold_win'])}: the exit is slow to cut a losing trade."})
    for i in ids:
        with_read = [(side_pct(t), t[key]) for t in trades[i] if side_pct(t) is not None]
        strong = [p for share, p in with_read if share >= 60]
        weak = [p for share, p in with_read if share <= 40]
        if len(strong) >= MIN_READS and len(weak) >= MIN_READS:
            a, b = sum(strong) / len(strong), sum(weak) / len(weak)
            out.append({"tone": "good" if a > b else "bad", "title": f"Run #{i}: buyers / sellers at the entry",
                        "text": f"Trades entered with their own side holding 60% or more of the waiting orders made "
                                f"{_money(a)} each on average ({len(strong)} trades); with 40% or less, {_money(b)} "
                                f"({len(weak)} trades)."})
    return out


# ---- the page's payload ---------------------------------------------------------------------------------------------


def detail(run_ids: list[int], base: int | None = None, basis: str = "gross") -> dict:
    """Everything the Compare page's trade-by-trade part shows. Raises ValueError for a bad request."""
    ids: list[int] = []
    for raw in run_ids:
        if int(raw) not in ids:
            ids.append(int(raw))
    if not 2 <= len(ids) <= 8:
        raise ValueError("Pick 2 to 8 runs to compare.")
    key = "net" if basis == "net" else "gross"
    base_id = int(base) if base is not None and int(base) in ids else ids[0]
    trades = load(ids)
    per_run = {i: stats(trades[i], key) for i in ids}
    overlaps = {i: overlap(trades[base_id], trades[i], key) for i in ids if i != base_id}
    rows = sorted((t for i in ids for t in trades[i]), key=lambda t: (t["entry_ts"], t["run_id"]))
    total = len(rows)
    if total > MAX_TRADES:
        rows = rows[-MAX_TRADES:]
    return {
        "runs": ids,
        "base": base_id,
        "basis": key,
        "match_min": MATCH_MIN,
        "stats": per_run,
        "overlap": overlaps,
        "takeaways": takeaways(ids, base_id, per_run, overlaps, trades, key),
        "trades": [{**t, "pnl": t[key]} for t in rows],
        "trades_total": total,
        "reads": sum(1 for t in rows if t["buy_pct"] is not None),
    }


def stock_day(run_ids: list[int], symbol: str, day: str) -> dict:
    """One stock on one replayed day in each run: its P&L minute by minute (closed trades + the open one, before
    charges, from the replay P&L log), its trades, and the day's buyers % reads for the strip under the chart."""
    import open_pnl_log

    name = (symbol or "").upper()
    ids = [int(i) for i in run_ids][:8]
    trades = load(ids)
    runs = []
    for i in ids:
        mine = [t for t in trades[i] if t["symbol"] == name and t["date"] == day]
        closed = [(name, t["exit_at"][:5], float(t["gross"] or 0.0)) for t in mine]
        try:
            series = open_pnl_log.replay_points(i, day)
            curve = open_pnl_log.by_stock(series, closed).get(name, [])
        except Exception:  # noqa: BLE001
            curve = []
        runs.append({
            "run_id": i,
            "trades": mine,
            "curve": [{"t": str(p["t"])[11:16], "pnl": p["total"]} for p in curve],
        })
    pressure: list[dict] = []
    try:
        import pressure_exit

        start = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(9, 15), tzinfo=IST).timestamp()
        end = dt.datetime.combine(dt.date.fromisoformat(day), dt.time(15, 30), tzinfo=IST).timestamp()
        pressure = [
            {"t": dt.datetime.fromtimestamp(float(p["time"]), IST).strftime("%H:%M"), "buy_pct": p["buy_pct"]}
            for p in pressure_exit.series(name, start, end)
        ]
    except Exception:  # noqa: BLE001
        pressure = []
    return {"symbol": name, "day": day, "runs": runs, "pressure": pressure}
