"""Compare backtest runs: which strategy, which stocks, which days (read-only; the blotter's Backtests tab → Compare).

Owner's request: tick several runs and see, on one page, which strategy did best overall, on which stocks and on which
days, one run (the base) against many. Nothing here trades, arms or changes a setting; it reads the runs' saved
settings and their closed REPLAY trades.

Fair ground first. Runs can cover different days and stocks, and a total over more days is not a better strategy. So
every run is measured on the **common ground**: the days all of them finished (`ReplayRun.days[:days_done]`) and the
stocks all of them replayed. A day or stock a run played without a trade counts as ₹0 (that is a result), one it never
played is left out. When the runs share no day (or no stock, e.g. Scalp-pick runs that pick different stocks), that
filter is dropped and a note says so. Trades closed flat by Stop (`REPLAY_STOPPED`) and trades of a day the run had not
finished are left out.

The ranking is the average place over P&L (counted twice), green days %, the biggest drop of the day-by-day running
total, profit factor, P&L per trade and days won (best of all runs that day). The verdict also names the run with the
most money, the steadiest and the best per trade, how clearly the best beat the runner-up day by day, whether its
profit leans on one stock or one day, and per stock where another run did better (a per-stock setting to try).
`basis` "gross" (before charges, as the screens) or "net".
"""
from __future__ import annotations

import datetime as dt
import json
from collections import defaultdict

MAX_RUNS = 8  # one colour each on the chart (a categorical palette has 8), and the tables stay readable
MIN_RUNS = 2
STOPPED_REASON = "REPLAY_STOPPED"
# Places in the ranking: P&L counts twice.
WEIGHTS = {"pnl": 2.0, "green_pct": 1.0, "max_drawdown": 1.0, "profit_factor": 1.0, "avg_trade": 1.0, "days_won": 1.0}
# Confidence of "best beat the runner-up": share of decided common days won, and how many days that needs.
CLEAR_SHARE = 0.65
LIKELY_SHARE = 0.55
MIN_DAYS = 3
MIN_DECIDED = 5
MIN_TRADES = 10
# "Most of the profit from one stock / one day".
LEAN_SHARE = 0.5
LEAN_MIN_ITEMS = 3
MAX_SUGGESTIONS = 12
SUGGEST_MIN_TRADES = 5  # a per-stock setting is only suggested from at least this many trades on that stock
MAX_STOCK_FIELDS = 300
# Settings that are not strategy choices, or are compared in their own way.
SKIP_KEYS = {"stock_settings", "scalp_pick", "fast_bot", "chain", "bot_name"}
PICK_SKIP = {"picks", "universe", "universe_symbols", "active_hits", "resumable", "active_days"}
EXTRA_KEYS = ("bot", "as_armed", "play_handoffs")


def _r(value: float | None) -> float | None:
    return None if value is None else round(float(value), 2)


def _column_default(name: str):
    from models import BotConfig

    column = BotConfig.__table__.columns.get(name)
    default = getattr(column, "default", None) if column is not None else None
    arg = getattr(default, "arg", None)
    return None if callable(arg) else arg


def _settings(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# ---- loading --------------------------------------------------------------------------------------------------------


def load(run_ids: list[int]) -> tuple[list[dict], list[dict]]:
    """The runs (in the order asked) and their closed trades as plain dicts. Raises ValueError for unknown runs."""
    from database import session_factory
    from models import ReplayRun, TradeLog

    with session_factory()() as db:
        rows = {r.id: r for r in db.query(ReplayRun).filter(ReplayRun.id.in_(run_ids)).all()}
        missing = [i for i in run_ids if i not in rows]
        if missing:
            raise ValueError(f"No backtest run #{', #'.join(str(i) for i in missing)} (deleted?).")
        trades = (
            db.query(TradeLog)
            .filter(TradeLog.run_id.in_(run_ids), TradeLog.exit_price.isnot(None))
            .order_by(TradeLog.id)
            .all()
        )
        runs = []
        for run_id in run_ids:
            run = rows[run_id]
            settings = _settings(run.settings)
            days = [d for d in (run.days or "").split(",") if d]
            runs.append(
                {
                    "id": run.id,
                    "status": run.status,
                    "start_date": run.start_date,
                    "end_date": run.end_date,
                    "created_at": run.created_at.isoformat() if run.created_at else None,
                    "days_total": int(run.days_total or 0),
                    "days_done": int(run.days_done or 0),
                    "days": days,
                    "symbols": [s.upper() for s in (run.symbols or "").split(",") if s],
                    "settings": settings,
                }
            )
        out = [
            {
                "run_id": int(t.run_id),
                "date": t.date,
                "symbol": (t.symbol or "").upper(),
                "gross": float(t.gross_pnl or 0.0),
                "net": float(t.net_pnl if t.net_pnl is not None else (t.gross_pnl or 0.0)),
                "charges": float(t.brokerage_and_taxes or 0.0),
                "exit_reason": t.exit_reason or "",
                "entry_time": t.entry_time,
            }
            for t in trades
        ]
    return runs, out


# ---- arithmetic -----------------------------------------------------------------------------------------------------


def finished_days(run: dict, trade_days: set[str]) -> list[str]:
    """The days the run finished: its day list up to days_done (a run still playing or cut short has fewer). Old runs
    without a day list fall back to the days they traded."""
    days = run.get("days") or []
    if days:
        return sorted(days[: max(0, int(run.get("days_done") or 0))])
    return sorted(trade_days)


def metrics(trades: list[dict], days: list[str], key: str) -> dict:
    """One run's numbers on a set of trades and the days they belong to (a day with no trade counts as ₹0)."""
    pnl = sum(t[key] for t in trades)
    profit = sum(t[key] for t in trades if t[key] > 0)
    loss = sum(t[key] for t in trades if t[key] < 0)
    wins = sum(1 for t in trades if t[key] > 0)
    losses = sum(1 for t in trades if t[key] < 0)
    by_day = {d: 0.0 for d in days}
    for t in trades:
        by_day[t["date"]] = by_day.get(t["date"], 0.0) + t[key]
    ordered = sorted(by_day)
    running = peak = drop = 0.0
    for d in ordered:
        running += by_day[d]
        peak = max(peak, running)
        drop = min(drop, running - peak)
    by_stock: dict[str, float] = defaultdict(float)
    for t in trades:
        by_stock[t["symbol"]] += t[key]
    best_day = max(ordered, key=lambda d: by_day[d], default=None)
    worst_day = min(ordered, key=lambda d: by_day[d], default=None)
    n = len(trades)
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(100.0 * wins / n, 1) if n else None,
        "pnl": _r(pnl),
        "profit": _r(profit),
        "loss": _r(loss),
        "charges": _r(sum(t["charges"] for t in trades)),
        "profit_factor": round(profit / -loss, 2) if loss < 0 else None,
        "no_loss": loss == 0 and profit > 0,
        "avg_trade": _r(pnl / n) if n else None,
        "days": len(ordered),
        "avg_day": _r(pnl / len(ordered)) if ordered else None,
        "green_days": sum(1 for d in ordered if by_day[d] > 0),
        "red_days": sum(1 for d in ordered if by_day[d] < 0),
        "green_pct": round(100.0 * sum(1 for d in ordered if by_day[d] > 0) / len(ordered), 1) if ordered else None,
        "max_drawdown": _r(drop),
        "best_day": {"date": best_day, "pnl": _r(by_day[best_day])} if best_day else None,
        "worst_day": {"date": worst_day, "pnl": _r(by_day[worst_day])} if worst_day else None,
        "stocks": len(by_stock),
        "stocks_profitable": sum(1 for v in by_stock.values() if v > 0),
    }


def _rank_value(m: dict, name: str, extra: dict) -> float | None:
    if name == "days_won":
        return float(extra.get("days_won", 0))
    if not m.get("trades") and name in ("max_drawdown", "profit_factor", "avg_trade"):
        return None  # a run that never traded is not "steady": it is last on these
    if name == "profit_factor":
        if m.get("no_loss"):
            return float("inf")
        return m.get("profit_factor") if m.get("trades") else None
    value = m.get(name)
    return None if value is None else float(value)


def places(values: dict[int, float | None]) -> dict[int, int]:
    """Competition places, best (highest) first: 1, 1, 3. A missing value is last."""
    present = sorted((v for v in values.values() if v is not None), reverse=True)
    last = len(values)
    out = {}
    for run_id, v in values.items():
        out[run_id] = last if v is None else 1 + sum(1 for p in present if p > v)
    return out


def _unique_top(cells: dict[int, float]) -> int | None:
    """The run with the highest value when it is the only one that high and above the rest; None on a tie."""
    if len(cells) < 2:
        return None
    top = max(cells.values())
    leaders = [k for k, v in cells.items() if v == top]
    return leaders[0] if len(leaders) == 1 else None


def head_to_head(a: dict[str, float], b: dict[str, float], days: list[str]) -> dict:
    """Day by day, run a against run b on the days both played."""
    won = lost = tied = 0
    for d in days:
        if d not in a or d not in b:
            continue
        if a[d] > b[d] + 0.005:
            won += 1
        elif b[d] > a[d] + 0.005:
            lost += 1
        else:
            tied += 1
    return {"won": won, "lost": lost, "tied": tied, "days": won + lost + tied}


def confidence(h2h: dict, days: int, trades: int) -> str:
    """How clearly one run beat another day by day: clear / likely / close / too_few."""
    if days < MIN_DAYS or trades < MIN_TRADES:
        return "too_few"
    decided = h2h["won"] + h2h["lost"]
    if decided == 0:
        return "close"
    share = h2h["won"] / decided
    if share >= CLEAR_SHARE and decided >= MIN_DECIDED:
        return "clear"
    if share >= LIKELY_SHARE:
        return "likely"
    return "close"


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b) if (a is not None and b is not None) else a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) < 1e-9
    if isinstance(a, str) and isinstance(b, str):
        return a.strip().upper() == b.strip().upper()
    return json.dumps(a, sort_keys=True, default=str) == json.dumps(b, sort_keys=True, default=str)


def _setting_value(settings: dict, key: str):
    if key in settings:
        return settings[key]
    if key == "bot":
        return 1
    if key in ("as_armed", "play_handoffs"):
        return False
    return _column_default(key)


def _on(settings: dict, key: str) -> bool:
    return bool(_setting_value(settings, key))


def _stop(settings: dict, *types: str) -> bool:
    if not _on(settings, "use_stop"):
        return False
    return str(_setting_value(settings, "stop_type") or "ATR").upper() in types


def _mode(settings: dict) -> str:
    return str(_setting_value(settings, "entry_mode") or "SMA").upper()


# A setting that only does something while its switch is on (the stop type, the entry mode, the check's own tick).
# Only clear cases are listed: a setting shared by several rules is always treated as in use, so a real difference
# is never hidden. A difference in a setting no run uses is dropped; one used by some runs shows "not used" for the rest.
_PREFIX_RULES: tuple[tuple[str, object], ...] = (
    ("tsl_", lambda s: _stop(s, "TSL")),
    ("smart_", lambda s: _stop(s, "SMART")),
    ("colour_", lambda s: _mode(s) == "COLOUR"),
    ("nth_", lambda s: _mode(s) == "NTH_CANDLE"),
    ("rsi_", lambda s: _on(s, "use_rsi")),
    ("macd_", lambda s: _on(s, "use_macd") or _on(s, "macd_exit")),
    ("decel_", lambda s: _on(s, "decel_exit")),
    ("uturn_", lambda s: _on(s, "uturn_on")),
    ("review_", lambda s: _on(s, "review_on")),
    ("recovery_", lambda s: _on(s, "recovery_hold")),
    ("breakout_", lambda s: _on(s, "breakout_handoff")),
    ("day_extreme_", lambda s: _on(s, "day_extreme_guard")),
    ("tall_", lambda s: _on(s, "tall_pullback")),
    ("small_exit_", lambda s: bool(str(_setting_value(s, "small_exit_minutes") or "").strip())),
    ("sc_", lambda s: bool(str(_setting_value(s, "sc_minutes") or "").strip())),
    ("gap_entry_", lambda s: _on(s, "use_gap_mode")),
    ("gap_exit_", lambda s: _on(s, "use_gap_mode")),
    ("gap_fade_", lambda s: _on(s, "use_gap_mode")),
    ("or_volume_", lambda s: _on(s, "use_or_volume")),
    ("or_spread_", lambda s: _on(s, "use_or_spread")),
    ("plan_", lambda s: _stop(s, "ATR") and _on(s, "stop_plan")),
)
_KEY_RULES: dict[str, object] = {
    "stop_type": lambda s: _on(s, "use_stop"),
    "vwap_stop_atr": lambda s: _stop(s, "VWAP"),
    "gap_sl_mult": lambda s: _stop(s, "SMA_GAP"),
    "gap_tp_mult": lambda s: _stop(s, "SMA_GAP"),
    "gap_min_pct": lambda s: _stop(s, "SMA_GAP"),
    "atr_target_mult": lambda s: _stop(s, "ATR", "SUPERTREND", "VWAP"),
    "stop_plan": lambda s: _stop(s, "ATR"),
    "adx_threshold": lambda s: _on(s, "use_adx_filter"),
    "volume_min_ratio": lambda s: _on(s, "use_volume"),
    "density_min_pct": lambda s: _on(s, "use_density"),
    "bb_min_width_pct": lambda s: _on(s, "use_bollinger"),
    "gap_long_min": lambda s: _on(s, "use_gap_long"),
    "gap_long_max": lambda s: _on(s, "use_gap_long"),
    "gap_short_min": lambda s: _on(s, "use_gap_short"),
    "gap_short_max": lambda s: _on(s, "use_gap_short"),
    "gap_giveback_pct": lambda s: _on(s, "use_gap_mode"),
    "candle_dir_count": lambda s: _on(s, "use_candle_dir"),
    "candle_dir_rule": lambda s: _on(s, "use_candle_dir"),
    "join_after_min": lambda s: _on(s, "join_trend"),
    "always_in_reverse": lambda s: _on(s, "always_in"),
    "counter_rule": lambda s: int(_setting_value(s, "counter_exit_count") or 0) > 0 or _on(s, "counter_entry"),
    "counter_need_fast": lambda s: int(_setting_value(s, "counter_exit_count") or 0) > 0 or _on(s, "counter_entry"),
    "counter_min_body": lambda s: int(_setting_value(s, "counter_exit_count") or 0) > 0,
    "counter_reenter": lambda s: int(_setting_value(s, "counter_exit_count") or 0) > 0,
    "counter_reenter_max": lambda s: int(_setting_value(s, "counter_exit_count") or 0) > 0 and _on(s, "counter_reenter"),
    "ema_fast": lambda s: _on(s, "use_ema") or _on(s, "ema_exit"),
    "ema_slow": lambda s: _on(s, "use_ema") or _on(s, "ema_exit"),
    "cvd_lookback": lambda s: _on(s, "use_cvd"),
    "cvd_div_lookback": lambda s: _on(s, "cvd_div_exit"),
    "level_line": lambda s: _on(s, "use_level"),
    "fresh_rvol": lambda s: _on(s, "use_fresh"),
    "high52_side": lambda s: _on(s, "use_high52"),
    "high52_pct": lambda s: _on(s, "use_high52"),
    "extreme_anchor": lambda s: _on(s, "extreme_entry"),
    "extreme_invalid_pct": lambda s: _on(s, "extreme_entry"),
    "extreme_expiry_candles": lambda s: _on(s, "extreme_entry"),
    "extreme_cancel_on_cross": lambda s: _on(s, "extreme_entry"),
    "entry_candle_max": lambda s: _on(s, "entry_candle_confirm") or str(_setting_value(s, "handoff_candle") or "OFF").upper() != "OFF",
    "pattern_set": lambda s: _mode(s) == "PATTERN",
    "pattern_trend": lambda s: _mode(s) == "PATTERN",
    "pattern_min_edge": lambda s: _mode(s) == "PATTERN",
    "pattern_tf": lambda s: _mode(s) in ("PATTERN", "COLOUR"),
    "reverse_colour_exit": lambda s: bool(str(_setting_value(s, "reverse_on_exit") or "").strip()),
    "reverse_settings": lambda s: bool(str(_setting_value(s, "reverse_on_exit") or "").strip()),
    "exit_group": lambda s: str(_setting_value(s, "exit_join") or "OR").upper() == "AND",
    "exit_need": lambda s: str(_setting_value(s, "exit_join") or "OR").upper() == "AND",
    "exit_within": lambda s: str(_setting_value(s, "exit_join") or "OR").upper() == "AND",
    "vturn_handoff": lambda s: _on(s, "vturn_exit"),
    "stop_handoff_loss": lambda s: str(_setting_value(s, "stop_handoff") or "OFF").upper() != "OFF",
}


def in_use(key: str, settings: dict) -> bool:
    """Does this setting do anything in a run with these settings? True unless its switch is clearly off."""
    rule = _KEY_RULES.get(key)
    if rule is None:
        rule = next((r for prefix, r in _PREFIX_RULES if key.startswith(prefix)), None)
    if rule is None:
        return True
    try:
        return bool(rule(settings))  # type: ignore[operator]
    except (TypeError, ValueError):
        return True


def setting_differences(runs: list[dict]) -> tuple[list[dict], list[dict]]:
    """The strategy settings that are not the same in every run (shared ones, then each stock's own)."""
    from strategy_engine import SNAPSHOT_FIELDS

    keys: list[str] = list(EXTRA_KEYS) + [k for k in SNAPSHOT_FIELDS if k not in EXTRA_KEYS]
    for run in runs:
        for k in run["settings"]:
            if k not in keys and k not in SKIP_KEYS and not isinstance(run["settings"][k], (dict, list)):
                keys.append(k)
    fields = []
    for key in keys:
        values = {run["id"]: _setting_value(run["settings"], key) for run in runs}
        first = next(iter(values.values()))
        if any(not _same(first, v) for v in values.values()):
            unused = [run["id"] for run in runs if not in_use(key, run["settings"])]
            if len(unused) == len(runs):
                continue  # different, but no run uses it (trailing-stop numbers when no run trails)
            fields.append({"key": key, "values": values, "unused": unused})
    # Scalp-page pick rules (which stocks a run picked each day), then the fast-mover hand-off.
    picks = {run["id"]: run["settings"].get("scalp_pick") for run in runs}
    if any(isinstance(p, dict) for p in picks.values()):
        pick_keys: list[str] = []
        for p in picks.values():
            for k in (p or {}) if isinstance(p, dict) else ():
                if k not in PICK_SKIP and k not in pick_keys:
                    pick_keys.append(k)
        for k in pick_keys:
            values = {rid: (p.get(k) if isinstance(p, dict) else None) for rid, p in picks.items()}
            first = next(iter(values.values()))
            if any(not _same(first, v) for v in values.values()):
                fields.append({"key": f"pick.{k}", "values": values})
    fast = {run["id"]: bool(run["settings"].get("fast_bot")) for run in runs}
    if len(set(fast.values())) > 1:
        fields.append({"key": "fast_bot", "values": fast})
    # Each stock's own settings ("Settings for: stock"), where the runs differ.
    stock_fields = []
    own = {run["id"]: run["settings"].get("stock_settings") if isinstance(run["settings"].get("stock_settings"), dict) else {} for run in runs}
    symbols = sorted({s for o in own.values() for s in o})
    for symbol in symbols:
        skeys = sorted({k for o in own.values() for k in (o.get(symbol) or {})})
        for k in skeys:
            values = {rid: (o.get(symbol) or {}).get(k) for rid, o in own.items()}
            first = next(iter(values.values()))
            if any(not _same(first, v) for v in values.values()):
                stock_fields.append({"symbol": symbol, "key": k, "values": values})
                if len(stock_fields) >= MAX_STOCK_FIELDS:
                    return fields, stock_fields
    return fields, stock_fields


# ---- the comparison -------------------------------------------------------------------------------------------------


def analyze(runs: list[dict], trades: list[dict], base: int | None = None, basis: str = "gross") -> dict:
    """Everything the Compare page shows, from the runs and their closed trades (see the module note)."""
    if not MIN_RUNS <= len(runs) <= MAX_RUNS:
        raise ValueError(f"Pick {MIN_RUNS} to {MAX_RUNS} runs to compare.")
    key = "net" if basis == "net" else "gross"
    ids = [r["id"] for r in runs]
    base = base if base in ids else ids[0]
    trades = [t for t in trades if t["exit_reason"] != STOPPED_REASON]
    by_run: dict[int, list[dict]] = {i: [] for i in ids}
    for t in trades:
        if t["run_id"] in by_run:
            by_run[t["run_id"]].append(t)

    # What each run played: its finished days, and its stocks (the run's list plus any it traded).
    played_days: dict[int, list[str]] = {}
    played_stocks: dict[int, set[str]] = {}
    for run in runs:
        rid = run["id"]
        played_days[rid] = finished_days(run, {t["date"] for t in by_run[rid]})
        days = set(played_days[rid])
        by_run[rid] = [t for t in by_run[rid] if t["date"] in days]
        played_stocks[rid] = set(run["symbols"]) | {t["symbol"] for t in by_run[rid]}
    empty = [rid for rid in ids if not played_days[rid]]

    # The common ground: days every run finished and stocks every run replayed (each dropped when there is none).
    active = [rid for rid in ids if played_days[rid]]
    common_days = sorted(set.intersection(*(set(played_days[r]) for r in active))) if active else []
    common_stocks = sorted(set.intersection(*(played_stocks[r] for r in active))) if active else []
    days_filtered = bool(common_days)
    stocks_filtered = bool(common_stocks)
    notes = []
    if empty:
        notes.append({"code": "no_finished_day", "runs": empty})
    if active and not days_filtered:
        notes.append({"code": "no_common_days"})
    elif any(len(played_days[r]) != len(common_days) for r in active):
        notes.append({"code": "different_days", "days": len(common_days)})
    if active and not stocks_filtered:
        notes.append({"code": "no_common_stocks"})
    elif any(played_stocks[r] != set(common_stocks) for r in active):
        notes.append({"code": "different_stocks", "stocks": len(common_stocks)})

    def ground_days(rid: int) -> list[str]:
        if not played_days[rid]:
            return []  # a run with no finished day played nothing
        return common_days if days_filtered else played_days[rid]

    def on_ground(rid: int, stocks: bool = True) -> list[dict]:
        days = set(ground_days(rid))
        keep = set(common_stocks) if (stocks and stocks_filtered) else None
        return [t for t in by_run[rid] if t["date"] in days and (keep is None or t["symbol"] in keep)]

    ground = {rid: on_ground(rid) for rid in ids}
    day_pnl: dict[int, dict[str, float]] = {}
    for rid in ids:
        cells = {d: 0.0 for d in ground_days(rid)}
        for t in ground[rid]:
            cells[t["date"]] = cells.get(t["date"], 0.0) + t[key]
        day_pnl[rid] = cells

    day_count: dict[int, dict[str, int]] = {rid: defaultdict(int) for rid in ids}
    for rid in ids:
        for t in ground[rid]:
            day_count[rid][t["date"]] += 1
    # Days won: the best of all runs that day (only days every run played when there is a common ground).
    union_days = sorted({d for rid in ids for d in day_pnl[rid]})
    days_won = {rid: 0 for rid in ids}
    day_rows = []
    for d in union_days:
        cells = {rid: day_pnl[rid][d] for rid in ids if d in day_pnl[rid]}
        top = _unique_top(cells) if len(cells) == len(active) and len(cells) >= 2 else None
        if top is not None and cells[top] != 0:
            days_won[top] += 1
        else:
            top = None
        counts = {rid: day_count[rid].get(d, 0) for rid in ids}
        present = list(cells.values())
        day_rows.append(
            {
                "date": d,
                "cells": {rid: ({"pnl": _r(cells[rid]), "trades": counts.get(rid, 0)} if rid in cells else None) for rid in ids},
                "best": top,
                "worst": min(cells, key=lambda r: cells[r]) if len(cells) >= 2 and min(present) < max(present) else None,
                "all_red": len(cells) >= 2 and all(v < 0 for v in present),
                "all_green": len(cells) >= 2 and all(v > 0 for v in present),
            }
        )

    # Stocks: each run's P&L on each stock over the ground days (all stocks listed, a run that never replayed one: none).
    stock_trades: dict[tuple[int, str], list[dict]] = defaultdict(list)
    for rid in ids:
        for t in on_ground(rid, stocks=False):
            stock_trades[(rid, t["symbol"])].append(t)
    all_stocks = sorted({s for rid in ids for s in played_stocks[rid]})
    stocks_won = {rid: 0 for rid in ids}
    stock_rows = []
    for symbol in all_stocks:
        cells: dict[int, dict | None] = {}
        for rid in ids:
            if symbol not in played_stocks[rid] or not played_days[rid]:
                cells[rid] = None
                continue
            rows = stock_trades.get((rid, symbol), [])
            cells[rid] = {
                "pnl": _r(sum(t[key] for t in rows)),
                "trades": len(rows),
                "wins": sum(1 for t in rows if t[key] > 0),
            }
        have = {rid: c for rid, c in cells.items() if c is not None}
        traded = {rid: c for rid, c in have.items() if c["trades"] > 0}
        values = {rid: c["pnl"] for rid, c in have.items()}
        top = _unique_top(values) if traded else None
        if top is not None and have[top]["trades"] > 0 and values[top] > 0:
            stocks_won[top] += 1
        else:
            top = None
        if not traded:
            verdict = "NO_TRADES"
        elif len(have) < 2:
            verdict = "ONLY_ONE"
        elif all(c["pnl"] > 0 for c in traded.values()) and len(traded) == len(have):
            verdict = "ALL_WIN"
        elif all(c["pnl"] < 0 for c in traded.values()) and len(traded) == len(have):
            verdict = "ALL_LOSE"
        elif max(values.values()) > 0 > min(values.values()):
            verdict = "DEPENDS"
        else:
            verdict = "MIXED"
        stock_rows.append(
            {
                "symbol": symbol,
                "cells": cells,
                "best": top,
                "worst": min(values, key=lambda r: values[r]) if len(values) >= 2 and min(values.values()) < max(values.values()) else None,
                "spread": _r(max(values.values()) - min(values.values())) if len(values) >= 2 else None,
                "verdict": verdict,
            }
        )

    # The overall table on the common ground, plus each run's own totals over everything it finished.
    overall = []
    for rid in ids:
        overall.append(
            {
                "run_id": rid,
                "metrics": metrics(ground[rid], ground_days(rid), key),
                "metrics_all": metrics(by_run[rid], played_days[rid], key),
                "days_won": days_won[rid],
                "stocks_won": stocks_won[rid],
            }
        )
    by_id = {row["run_id"]: row for row in overall}
    contenders = [rid for rid in ids if played_days[rid]]
    place_of: dict[str, dict[int, int]] = {}
    for name in WEIGHTS:
        values = {rid: _rank_value(by_id[rid]["metrics"], name, by_id[rid]) for rid in contenders}
        place_of[name] = places(values)
    total_weight = sum(WEIGHTS.values())
    for row in overall:
        rid = row["run_id"]
        if rid not in contenders:
            row["places"], row["avg_place"] = {}, None
            continue
        row["places"] = {name: place_of[name][rid] for name in WEIGHTS}
        row["avg_place"] = round(sum(WEIGHTS[n] * place_of[n][rid] for n in WEIGHTS) / total_weight, 2)
    ranked = sorted(
        overall,
        key=lambda r: (r["avg_place"] is None, r["avg_place"] or 0, -(r["metrics"]["pnl"] or 0), r["run_id"]),
    )
    for i, row in enumerate(ranked):
        row["rank"] = i + 1 if row["avg_place"] is not None else None

    # Against the base: P&L difference, day by day, and the settings that changed.
    fields, stock_fields = setting_differences(runs)
    base_days = day_pnl.get(base, {})
    vs_base = []
    for rid in ids:
        h2h = head_to_head(day_pnl[rid], base_days, union_days)
        changed = [
            f["key"]
            for f in fields
            if not _same(f["values"][rid], f["values"][base])
            and not (rid in f.get("unused", []) and base in f.get("unused", []))
        ]
        changed_stocks = sorted({f["symbol"] for f in stock_fields if not _same(f["values"][rid], f["values"][base])})
        mine, theirs = by_id[rid]["metrics"], by_id[base]["metrics"]
        by_id[rid]["vs_base"] = {
            "pnl_diff": _r((mine["pnl"] or 0) - (theirs["pnl"] or 0)),
            "trades_diff": mine["trades"] - theirs["trades"],
            "head_to_head": h2h,
        }
        if rid != base:
            vs_base.append(
                {
                    "run_id": rid,
                    "changed": changed,
                    "changed_stocks": changed_stocks,
                    "pnl_diff": by_id[rid]["vs_base"]["pnl_diff"],
                    "head_to_head": h2h,
                }
            )

    # Setting effects (3+ runs): runs grouped by the value of each setting that differs, their average P&L.
    effects = []
    if len(contenders) >= 3:
        for f in fields:
            groups: dict[str, dict] = {}
            for rid in contenders:
                if rid in f.get("unused", []):
                    continue  # the setting did nothing in this run
                value = f["values"][rid]
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    norm = float(value)
                elif isinstance(value, str):
                    norm = value.strip().upper()  # "smart" and "SMART" are one choice, as in the differences
                else:
                    norm = value
                label = json.dumps(norm, sort_keys=True, default=str)
                g = groups.setdefault(label, {"value": value, "runs": [], "pnl": 0.0})
                g["runs"].append(rid)
                g["pnl"] += by_id[rid]["metrics"]["pnl"] or 0.0
            if len(groups) < 2:
                continue
            rows = [{"value": g["value"], "runs": g["runs"], "avg_pnl": _r(g["pnl"] / len(g["runs"]))} for g in groups.values()]
            rows.sort(key=lambda g: -(g["avg_pnl"] or 0))
            effects.append({"key": f["key"], "groups": rows, "spread": _r((rows[0]["avg_pnl"] or 0) - (rows[-1]["avg_pnl"] or 0))})
        effects.sort(key=lambda e: -(e["spread"] or 0))
        effects = effects[:12]

    # Exit reasons per run on the common ground.
    reasons: dict[str, dict[int, dict]] = defaultdict(dict)
    for rid in ids:
        for t in ground[rid]:
            cell = reasons[t["exit_reason"] or "—"].setdefault(rid, {"count": 0, "pnl": 0.0})
            cell["count"] += 1
            cell["pnl"] += t[key]
    reason_rows = [
        {
            "reason": reason,
            "cells": {rid: ({"count": c["count"], "pnl": _r(c["pnl"])} if (c := cells.get(rid)) else None) for rid in ids},
            "count": sum(c["count"] for c in cells.values()),
        }
        for reason, cells in reasons.items()
    ]
    reason_rows.sort(key=lambda r: -r["count"])

    verdict = _verdict(ranked, by_id, day_pnl, union_days, ground, key, stock_rows, contenders)
    curve_days = union_days
    curves = {}
    for rid in ids:
        running = 0.0
        series = []
        for d in curve_days:
            if d in day_pnl[rid]:
                running += day_pnl[rid][d]
                series.append(_r(running))
            else:
                series.append(None)
        curves[rid] = series

    return {
        "basis": key,
        "base": base,
        "runs": [
            {
                "id": run["id"],
                "status": run["status"],
                "start_date": run["start_date"],
                "end_date": run["end_date"],
                "created_at": run["created_at"],
                "days_total": run["days_total"],
                "days_done": run["days_done"],
                "finished_days": len(played_days[run["id"]]),
                "symbols": sorted(played_stocks[run["id"]]),
                "bot": int(run["settings"].get("bot") or 1),
                "bot_name": run["settings"].get("bot_name") or f"Bot {int(run['settings'].get('bot') or 1)}",
                "settings": _page_settings(run["settings"]),
            }
            for run in runs
        ],
        "ground": {
            "days": common_days,
            "stocks": common_stocks,
            "days_filtered": days_filtered,
            "stocks_filtered": stocks_filtered,
            "notes": notes,
        },
        "overall": ranked,
        "verdict": verdict,
        "curve": {"days": curve_days, "runs": curves},
        "by_stock": {"rows": stock_rows, **_stock_summary(stock_rows, verdict.get("best"), ids)},
        "by_day": {
            "rows": day_rows,
            "all_red": [r["date"] for r in day_rows if r["all_red"]],
            "all_green": [r["date"] for r in day_rows if r["all_green"]],
        },
        "differences": {"fields": fields, "stock_fields": stock_fields, "vs_base": vs_base, "effects": effects},
        "by_reason": reason_rows,
    }


def _page_settings(settings: dict) -> dict:
    """A run's settings for the page (it names each strategy in words): without the big lists a run keeps for
    Resume and the hand-offs (the stock list it picks from, each day's picks, Bot 2 / Bot 3's settings)."""
    out = {k: v for k, v in settings.items() if k != "chain"}
    pick = out.get("scalp_pick")
    if isinstance(pick, dict):
        out["scalp_pick"] = {k: v for k, v in pick.items() if k not in ("universe_symbols", "active_hits", "picks")}
    return out


def _stock_summary(rows: list[dict], best: int | None, ids: list[int]) -> dict:
    """Per stock: what each stock on its own best run would have made (hindsight), and the stocks where another run
    clearly beat the overall best (a per-stock setting worth trying)."""
    if best is None:
        return {"each_best_total": None, "best_run_total": None, "uplift": None, "suggestions": []}
    each_best = best_total = 0.0
    suggestions = []
    for row in rows:
        mine = row["cells"].get(best)
        if mine is None:
            continue
        values = {rid: c["pnl"] for rid, c in row["cells"].items() if c is not None}
        top = max(values, key=lambda r: values[r])
        each_best += values[top]  # each stock on whichever run did best on it (hindsight)
        best_total += values[best]
        other = row["cells"][top]
        if top != best and values[top] > 0 and values[top] > values[best] and other["trades"] >= SUGGEST_MIN_TRADES:
            suggestions.append(
                {
                    "symbol": row["symbol"],
                    "run_id": top,
                    "pnl": values[top],
                    "best_run_pnl": values[best],
                    "gain": _r(values[top] - values[best]),
                    "trades": other["trades"],
                }
            )
    suggestions.sort(key=lambda s: -(s["gain"] or 0))
    return {
        "each_best_total": _r(each_best),
        "best_run_total": _r(best_total),
        "uplift": _r(each_best - best_total),
        "suggestions": suggestions[:MAX_SUGGESTIONS],
    }


def _verdict(
    ranked: list[dict],
    by_id: dict[int, dict],
    day_pnl: dict[int, dict[str, float]],
    days: list[str],
    ground: dict[int, list[dict]],
    key: str,
    stock_rows: list[dict],
    contenders: list[int],
) -> dict:
    """Which run is best, the runner-up, the specialists, how sure, and what to watch out for."""
    ordered = [r for r in ranked if r["rank"] is not None]
    if not ordered:
        return {"best": None, "warnings": ["nothing_to_compare"]}
    best = ordered[0]["run_id"]
    runner = ordered[1]["run_id"] if len(ordered) > 1 else None

    def top_by(name: str) -> int | None:
        traded = [rid for rid in contenders if by_id[rid]["metrics"]["trades"]]
        vals = {rid: _rank_value(by_id[rid]["metrics"], name, by_id[rid]) for rid in traded}
        present = {rid: v for rid, v in vals.items() if v is not None}
        return max(present, key=lambda rid: (present[rid], -rid)) if present else None

    m = by_id[best]["metrics"]
    h2h = head_to_head(day_pnl[best], day_pnl[runner], days) if runner is not None else None
    sure = confidence(h2h, h2h["days"], min(m["trades"], by_id[runner]["metrics"]["trades"])) if h2h else "too_few"
    warnings = []
    if all((by_id[rid]["metrics"]["pnl"] or 0) <= 0 for rid in contenders):
        warnings.append("all_lost")
    # Does the best run's profit lean on one stock or one day?
    lean = {"stock": None, "day": None}
    pnl = m["pnl"] or 0.0
    if pnl > 0:
        per_stock: dict[str, float] = defaultdict(float)
        for t in ground[best]:
            per_stock[t["symbol"]] += t[key]
        if len(per_stock) >= LEAN_MIN_ITEMS:
            symbol = max(per_stock, key=lambda s: per_stock[s])
            share = per_stock[symbol] / pnl
            if share > LEAN_SHARE:
                lean["stock"] = {"symbol": symbol, "pnl": _r(per_stock[symbol]), "share": round(100 * share, 1)}
        cells = day_pnl[best]
        if len(cells) >= LEAN_MIN_ITEMS:
            day = max(cells, key=lambda d: cells[d])
            share = cells[day] / pnl
            if share > LEAN_SHARE:
                lean["day"] = {"date": day, "pnl": _r(cells[day]), "share": round(100 * share, 1)}
    if lean["stock"]:
        warnings.append("leans_on_stock")
    if lean["day"]:
        warnings.append("leans_on_day")
    if m["days"] < MIN_DAYS:
        warnings.append("few_days")
    if m["trades"] < MIN_TRADES:
        warnings.append("few_trades")
    return {
        "best": best,
        "runner_up": runner,
        "lead": _r((m["pnl"] or 0) - (by_id[runner]["metrics"]["pnl"] or 0)) if runner is not None else None,
        "head_to_head": h2h,
        "confidence": sure,
        "most_pnl": top_by("pnl"),
        "steadiest": top_by("green_pct"),
        "smallest_drop": top_by("max_drawdown"),
        "best_per_trade": top_by("avg_trade"),
        "most_days_won": top_by("days_won"),
        "lean": lean,
        "warnings": warnings,
        "good_on_all": [r["symbol"] for r in stock_rows if r["verdict"] == "ALL_WIN"],
        "bad_on_all": [r["symbol"] for r in stock_rows if r["verdict"] == "ALL_LOSE"],
    }


def compare(run_ids: list[int], base: int | None = None, basis: str = "gross") -> dict:
    """Load the runs and compare them. Raises ValueError for a bad request (shown to the person as is)."""
    ids: list[int] = []
    for raw in run_ids:
        rid = int(raw)
        if rid not in ids:
            ids.append(rid)
    if not MIN_RUNS <= len(ids) <= MAX_RUNS:
        raise ValueError(f"Pick {MIN_RUNS} to {MAX_RUNS} runs to compare.")
    runs, trades = load(ids)
    out = analyze(runs, trades, base, basis)
    out["made_at"] = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat(timespec="seconds")
    return out
