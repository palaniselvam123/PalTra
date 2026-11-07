"""Stock ranking: which stocks does my strategy win on? (practice money; the Scalp page's Backtest tab).

The owner's idea: backtest a bot's own strategy on many stocks, see which stocks give the best chance of a profit and how
much, keep the winners and trade those. A ranking job backtests the chosen bot (its settings, hand-offs to Bot 2 / Bot 3
played when ticked) on the picked stocks over the same past days, `batch` stocks per run, one run after another on that
bot's replay player at full speed (`trend_compare.play`, shared). Each batch is an ordinary run in Backtests.

`rank` then reads every closed trade of those runs (and, when asked, of the bot's earlier finished backtests of the
last N days; a stock-day played twice counts once, the newest run's) and gives each stock: trades, win %, green days,
P&L before / after charges, average return per trade in % of the trade's value, profit factor (money won ÷ money lost),
best / worst trade, and a verdict: STRONG (profit factor ≥ 1.5 and green on ≥ 55 % of its days), GOOD (≥ 1.2), WEAK
(in profit but thin), AVOID (lost money), FEW (under `MIN_TRADES` trades or `MIN_DAYS` days: too little to judge).

Jobs are kept in `stock_rank.json` next to the SMA DB (`STOCK_RANK_FILE`, the last `KEEP`). Nothing here places an order,
arms a stock or changes a bot's settings.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import statistics
from pathlib import Path

import trend_compare

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KEEP = 20
MAX_STOCKS = 250
BATCH_CHOICES = (10, 15, 20, 25, 30, 40, 50)
MIN_TRADES = 5
MIN_DAYS = 3
VERDICTS = ("STRONG", "GOOD", "WEAK", "AVOID", "FEW")


def _path() -> Path:
    explicit = os.environ.get("STOCK_RANK_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("stock_rank.json")
    return Path("stock_rank.json")


def load_all() -> list[dict]:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _save_all(items: list[dict]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-KEEP:]))
    tmp.replace(path)


def get(jid: int) -> dict | None:
    return next((c for c in load_all() if c.get("id") == jid), None)


def put(item: dict) -> None:
    items = [c for c in load_all() if c.get("id") != item["id"]]
    items.append(item)
    items.sort(key=lambda c: c["id"])
    _save_all(items)


def delete(jid: int) -> bool:
    items = load_all()
    kept = [c for c in items if c.get("id") != jid]
    if len(kept) == len(items):
        return False
    _save_all(kept)
    return True


def batches(symbols: list[str], size: int) -> list[list[str]]:
    return [symbols[i : i + size] for i in range(0, len(symbols), size)]


def new(start_date: str, end_date: str, symbols: list[str], bot: int, batch: int, play_handoffs: bool,
        lists: list[str] | None = None, now: dt.datetime | None = None) -> dict:
    items = load_all()
    jid = max((int(c.get("id") or 0) for c in items), default=0) + 1
    groups = batches(symbols, batch)
    item = {
        "id": jid,
        "created_at": (now or dt.datetime.now(IST)).isoformat(timespec="seconds"),
        "status": "RUNNING",
        "start_date": start_date,
        "end_date": end_date,
        "bot": bot,
        "batch": batch,
        "play_handoffs": play_handoffs,
        "lists": list(lists or []),
        "universe": len(symbols),
        "runs": [
            {"time": f"{i * batch + 1}-{i * batch + len(g)}", "symbols": g, "run_id": None, "status": "WAITING", "note": ""}
            for i, g in enumerate(groups)
        ],
        "note": "",
    }
    put(item)
    return item


def interrupt_running() -> None:
    """At boot: a job that was playing when the process stopped is marked INTERRUPTED (its finished batches stay)."""
    items = load_all()
    changed = False
    for c in items:
        if c.get("status") == "RUNNING":
            c["status"] = "INTERRUPTED"
            c["note"] = "The server restarted while it ran. The finished batches are kept and ranked."
            for r in c.get("runs") or []:
                if r.get("status") in ("WAITING", "RUNNING"):
                    r["status"] = "INTERRUPTED"
            changed = True
    if changed:
        _save_all(items)


async def play(item: dict, start_fn, info_fn, stop_fn=None, poll: float = trend_compare.POLL_SEC, should_stop=lambda: False):
    return await trend_compare.play(item, start_fn, info_fn, stop_fn=stop_fn, poll=poll, should_stop=should_stop,
                                    key="symbols", save=put)


# ---- the ranking ---------------------------------------------------------------------------------


def _verdict(row: dict) -> str:
    if row["trades"] < MIN_TRADES or row["days"] < MIN_DAYS:
        return "FEW"
    if row["gross"] <= 0:
        return "AVOID"
    pf = row["profit_factor"]
    if (pf is None or pf >= 1.5) and row["green_pct"] >= 55:
        return "STRONG"
    if pf is None or pf >= 1.2:
        return "GOOD"
    return "WEAK"


def rank(trades: list[dict]) -> list[dict]:
    """One row per stock from closed trades (dicts: run_id, symbol, date, gross, net, entry_price, qty).

    A stock-day found in more than one run counts once: the newest run's (highest run id)."""
    newest: dict[tuple[str, str], int] = {}
    for t in trades:
        k = (t["symbol"], t["date"])
        newest[k] = max(newest.get(k, 0), int(t["run_id"] or 0))
    by_stock: dict[str, list[dict]] = {}
    for t in trades:
        if int(t["run_id"] or 0) == newest[(t["symbol"], t["date"])]:
            by_stock.setdefault(t["symbol"], []).append(t)

    rows = []
    for symbol, ts in by_stock.items():
        gross = [float(t["gross"] or 0) for t in ts]
        net = [float(t["net"] if t["net"] is not None else t["gross"] or 0) for t in ts]
        rets = [
            float(t["gross"] or 0) / (float(t["entry_price"]) * abs(int(t["qty"]))) * 100
            for t in ts
            if t.get("entry_price") and t.get("qty")
        ]
        days: dict[str, float] = {}
        for t in ts:
            days[t["date"]] = days.get(t["date"], 0.0) + float(t["gross"] or 0)
        won = sum(g for g in gross if g > 0)
        lost = -sum(g for g in gross if g < 0)
        green = sum(1 for v in days.values() if v > 0)
        row = {
            "symbol": symbol,
            "trades": len(ts),
            "wins": sum(1 for g in gross if g > 0),
            "win_rate": round(100 * sum(1 for g in gross if g > 0) / len(ts), 1),
            "days": len(days),
            "green_days": green,
            "green_pct": round(100 * green / len(days), 1) if days else 0.0,
            "gross": round(sum(gross), 2),
            "net": round(sum(net), 2),
            "per_trade": round(sum(gross) / len(ts), 2),
            "per_day": round(sum(gross) / len(days), 2) if days else 0.0,
            "avg_return_pct": round(statistics.fmean(rets), 3) if rets else None,
            "profit_factor": round(won / lost, 2) if lost > 0 else None,
            "best_trade": round(max(gross), 2),
            "worst_trade": round(min(gross), 2),
            "best_day": round(max(days.values()), 2),
            "worst_day": round(min(days.values()), 2),
        }
        row["verdict"] = _verdict(row)
        rows.append(row)
    order = {v: i for i, v in enumerate(VERDICTS)}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["gross"], r["symbol"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def summary(rows: list[dict], tested: int) -> dict:
    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    keep = [r for r in rows if r["verdict"] in ("STRONG", "GOOD")]
    lines = []
    if not rows:
        lines.append("No closed trades yet. The table fills in as each batch of stocks finishes.")
    else:
        lines.append(
            f"{len(rows)} of {tested} stocks traded. {counts['STRONG']} strong, {counts['GOOD']} good, {counts['WEAK']} weak, "
            f"{counts['AVOID']} lost money, {counts['FEW']} with too few trades to judge."
        )
        if keep:
            total = sum(r["gross"] for r in keep)
            all_total = sum(r["gross"] for r in rows)
            lines.append(
                f"Trading only the {len(keep)} strong and good stocks would have made ₹{total:,.0f} before charges, "
                f"against ₹{all_total:,.0f} for every stock traded."
            )
            best = keep[0]
            lines.append(
                f"Best: {best['symbol']} — {best['win_rate']:.0f}% winning trades, green on {best['green_days']} of "
                f"{best['days']} days, ₹{best['gross']:,.0f} before charges."
            )
        lines.append(
            "A past result is not a promise: re-run this every week or two and keep the stocks that stay strong."
        )
    return {"counts": counts, "answer": lines}


def trades_for_runs(run_ids: list[int]) -> list[dict]:
    """Closed trades of these runs (blocking DB read)."""
    if not run_ids:
        return []
    from database import session_factory
    from models import TradeLog

    with session_factory()() as s:
        rows = (
            s.query(TradeLog)
            .filter(TradeLog.run_id.in_(run_ids), TradeLog.exit_price.isnot(None))
            .all()
        )
        return [
            {"run_id": r.run_id, "symbol": (r.symbol or "").upper(), "date": r.date, "gross": r.gross_pnl,
             "net": r.net_pnl, "entry_price": r.entry_price, "qty": r.qty}
            for r in rows
        ]


def earlier_runs(bot: int, since: dt.date) -> list[int]:
    """The bot's finished backtests whose days end on or after `since` (blocking DB read)."""
    from database import session_factory
    from models import ReplayRun

    out = []
    with session_factory()() as s:
        for run in s.query(ReplayRun).filter(ReplayRun.status == "FINISHED", ReplayRun.end_date >= since.isoformat()):
            try:
                settings = json.loads(run.settings or "{}")
            except ValueError:
                continue
            if int(settings.get("bot") or 1) == bot:
                out.append(int(run.id))
    return out


def view(item: dict, earlier_days: int = 0, today: dt.date | None = None) -> dict:
    """A job with its ranking (blocking). `earlier_days` > 0 adds the bot's earlier finished backtests of those days."""
    run_ids = [int(r["run_id"]) for r in item.get("runs") or [] if r.get("run_id") is not None]
    if earlier_days > 0:
        since = (today or dt.date.today()) - dt.timedelta(days=earlier_days)
        run_ids = sorted(set(run_ids) | set(earlier_runs(int(item.get("bot") or 1), since)))
    rows = rank(trades_for_runs(run_ids))
    tested = item.get("universe") or len(rows)
    return {**item, "earlier_days": earlier_days, "runs_used": len(run_ids), "rows": rows, **summary(rows, tested)}


_JOBS = trend_compare.Comparer()


def jobs() -> trend_compare.Comparer:
    return _JOBS
