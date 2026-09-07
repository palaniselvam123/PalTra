"""Detailed transaction reporting.

`trade_ledger` answers "what is my P&L right now" for the dashboard. This
module answers "what happened, and was it any good" — the filterable ledger,
per-trade forensics (R-multiple, charges drag, holding time), and the
aggregate statistics you need before believing a strategy.

Two deliberate choices:

* Timestamps are stored naive-UTC by the ledger, so every day/hour bucket here
  converts to IST first. A report that buckets an 19:00 IST close into the
  previous day would misstate daily P&L for exactly the hours a user reviews.
* Rows written before the reporting columns existed have `entry_charges == 0`.
  That is missing data, not a zero-cost trade, so those rows are reported with
  `charges_recorded: false` and excluded from charge totals rather than
  silently flattering the cost analysis.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from dataclasses import dataclass, field

from sqlalchemy import select

from app import state
from app.core.market_clock import IST
from app.models.database import Trade, async_session

UTC = dt.timezone.utc

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@dataclass
class ReportFilters:
    date_from: dt.date | None = None
    date_to: dt.date | None = None
    symbols: list[str] = field(default_factory=list)
    side: str | None = None          # BUY | SELL
    source: str | None = None        # MANUAL | BOT
    strategy: str | None = None
    status: str | None = None        # OPEN | CLOSED
    account: str | None = None       # AUTO | MANUAL
    outcome: str | None = None       # WIN | LOSS | BREAKEVEN
    search: str | None = None


def _to_ist(value: dt.datetime | None) -> dt.datetime | None:
    """Stored timestamps are naive UTC (`datetime.utcnow`). Attach UTC, then
    convert — never assume the host clock is the trading clock.
    """
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.astimezone(IST)


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def build_row(trade: Trade) -> dict:
    """One trade, with everything derivable about it computed once."""
    entry = trade.entry_price
    qty = trade.quantity
    turnover = entry * qty

    risk_per_share = abs(entry - trade.stop_loss) if trade.stop_loss else 0.0
    reward_per_share = abs(trade.target - entry) if trade.target else 0.0
    planned_risk = risk_per_share * qty
    planned_reward = reward_per_share * qty

    opened_ist = _to_ist(trade.opened_at)
    closed_ist = _to_ist(trade.closed_at)

    charges_recorded = bool(trade.entry_charges or trade.exit_charges)
    charges = round((trade.entry_charges or 0.0) + (trade.exit_charges or 0.0), 2)

    row = {
        "id": trade.id,
        "mode": trade.mode,
        "symbol": trade.symbol,
        "side": trade.side,
        "quantity": qty,
        "entry_price": _round(entry),
        "exit_price": _round(trade.exit_price),
        "stop_loss": _round(trade.stop_loss),
        "target": _round(trade.target),
        "status": trade.status,
        "strategy": trade.strategy,
        "source": trade.source or "MANUAL",
        "account": trade.account or "AUTO",
        "exit_reason": trade.exit_reason,
        "turnover": _round(turnover),
        "risk_per_share": _round(risk_per_share),
        "planned_risk": _round(planned_risk),
        "planned_reward": _round(planned_reward),
        "planned_rr": _round(planned_reward / planned_risk, 2) if planned_risk else None,
        "entry_charges": _round(trade.entry_charges or 0.0),
        "exit_charges": _round(trade.exit_charges or 0.0),
        "charges": charges,
        "charges_recorded": charges_recorded,
        "opened_at": opened_ist.isoformat() if opened_ist else None,
        "closed_at": closed_ist.isoformat() if closed_ist else None,
        "trade_date": (closed_ist or opened_ist).date().isoformat() if (closed_ist or opened_ist) else None,
        "entry_hour_ist": opened_ist.hour if opened_ist else None,
        "weekday": WEEKDAYS[opened_ist.weekday()] if opened_ist else None,
    }

    if trade.status == "CLOSED" and trade.exit_price is not None:
        net = trade.pnl or 0.0
        gross = (
            (trade.exit_price - entry) * qty if trade.side == "BUY" else (entry - trade.exit_price) * qty
        )
        holding = None
        if trade.opened_at and trade.closed_at:
            holding = int((trade.closed_at - trade.opened_at).total_seconds())
        row.update(
            {
                "gross_pnl": _round(gross),
                "net_pnl": _round(net),
                "pnl": _round(net),
                "charges_drag_pct": _round(abs(charges) / abs(gross) * 100, 1) if gross and charges_recorded else None,
                "return_on_turnover_pct": _round(net / turnover * 100, 3) if turnover else None,
                "r_multiple": _round(net / planned_risk, 2) if planned_risk else None,
                "holding_sec": holding,
                "outcome": "WIN" if net > 0 else "LOSS" if net < 0 else "BREAKEVEN",
                "unrealised_pnl": None,
            }
        )
    elif trade.status == "CANCELLED":
        # Voided, not open. Falling through to the open-position branch gave a
        # cancelled trade a live holding time and an unrealised P&L against a
        # position that no longer exists.
        holding = None
        if trade.opened_at and trade.closed_at:
            holding = int((trade.closed_at - trade.opened_at).total_seconds())
        row.update(
            {
                "gross_pnl": 0.0,
                "net_pnl": 0.0,
                "pnl": 0.0,
                "charges_drag_pct": None,
                "return_on_turnover_pct": None,
                "r_multiple": None,
                "holding_sec": holding,
                "outcome": "VOID",
                "unrealised_pnl": None,
            }
        )
    else:
        quote = state.latest_quotes.get(trade.symbol)
        ltp = quote["ltp"] if quote else None
        unrealised = None
        if ltp is not None:
            unrealised = (ltp - entry) * qty if trade.side == "BUY" else (entry - ltp) * qty
        row.update(
            {
                "gross_pnl": None,
                "net_pnl": None,
                "pnl": None,
                "charges_drag_pct": None,
                "return_on_turnover_pct": None,
                "r_multiple": None,
                "holding_sec": int((dt.datetime.utcnow() - trade.opened_at).total_seconds())
                if trade.opened_at
                else None,
                "outcome": "OPEN",
                "ltp": _round(ltp),
                "unrealised_pnl": _round(unrealised),
            }
        )

    return row


def _matches(row: dict, f: ReportFilters) -> bool:
    if f.symbols and row["symbol"] not in f.symbols:
        return False
    if f.side and row["side"] != f.side:
        return False
    if f.source and row["source"] != f.source:
        return False
    if f.strategy and row["strategy"] != f.strategy:
        return False
    if f.status and row["status"] != f.status:
        return False
    if f.account and row["account"] != f.account:
        return False
    if f.outcome and row["outcome"] != f.outcome:
        return False
    if f.search:
        needle = f.search.lower()
        haystack = " ".join(
            str(row.get(k) or "") for k in ("symbol", "side", "source", "strategy", "exit_reason")
        ).lower()
        if needle not in haystack:
            return False
    if f.date_from or f.date_to:
        if not row["trade_date"]:
            return False
        day = dt.date.fromisoformat(row["trade_date"])
        if f.date_from and day < f.date_from:
            return False
        if f.date_to and day > f.date_to:
            return False
    return True


async def _load_rows(f: ReportFilters) -> list[dict]:
    async with async_session() as session:
        trades = (await session.execute(select(Trade).order_by(Trade.id.desc()))).scalars().all()
    rows = [build_row(t) for t in trades]
    return [r for r in rows if _matches(r, f)]


def _streaks(outcomes: list[str]) -> tuple[int, int]:
    best_win = best_loss = run_win = run_loss = 0
    for outcome in outcomes:
        if outcome == "WIN":
            run_win += 1
            run_loss = 0
        elif outcome == "LOSS":
            run_loss += 1
            run_win = 0
        else:
            run_win = run_loss = 0
        best_win = max(best_win, run_win)
        best_loss = max(best_loss, run_loss)
    return best_win, best_loss


def _summarise(rows: list[dict], account: str = "AUTO") -> dict:
    """Aggregate stats over the CLOSED rows in the current filter."""
    closed = [r for r in rows if r["status"] == "CLOSED"]
    open_rows = [r for r in rows if r["status"] == "OPEN"]
    # Chronological, so streaks and the equity curve follow the real sequence.
    closed.sort(key=lambda r: r["closed_at"] or "")

    wins = [r for r in closed if r["outcome"] == "WIN"]
    losses = [r for r in closed if r["outcome"] == "LOSS"]

    net_pnl = sum(r["net_pnl"] or 0 for r in closed)
    gross_profit = sum(r["net_pnl"] or 0 for r in wins)
    gross_loss = abs(sum(r["net_pnl"] or 0 for r in losses))

    avg_win = gross_profit / len(wins) if wins else 0.0
    avg_loss = gross_loss / len(losses) if losses else 0.0
    win_rate = len(wins) / len(closed) if closed else 0.0

    r_values = [r["r_multiple"] for r in closed if r["r_multiple"] is not None]
    holding = [r["holding_sec"] for r in closed if r["holding_sec"] is not None]
    costed = [r for r in closed if r["charges_recorded"]]

    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    curve = []
    for r in closed:
        equity += r["net_pnl"] or 0
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        curve.append(
            {
                "trade_id": r["id"],
                "at": r["closed_at"],
                "pnl": r["net_pnl"],
                "cumulative": round(equity, 2),
                "drawdown": round(peak - equity, 2),
            }
        )

    best_win_streak, worst_loss_streak = _streaks([r["outcome"] for r in closed])
    # Drawdown is meaningless as a percentage unless measured against the
    # capital of the wallet that produced it.
    starting_capital = state.capital_for(account)

    return {
        "trades_total": len(rows),
        "trades_closed": len(closed),
        "trades_open": len(open_rows),
        "wins": len(wins),
        "losses": len(losses),
        "breakeven": len(closed) - len(wins) - len(losses),
        "win_rate_pct": round(win_rate * 100, 1),
        "net_pnl": round(net_pnl, 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss else (round(gross_profit, 2) if gross_profit else 0.0),
        "expectancy": round(win_rate * avg_win - (1 - win_rate) * avg_loss, 2) if closed else 0.0,
        "expectancy_r": round(sum(r_values) / len(r_values), 2) if r_values else None,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss else None,
        "largest_win": round(max((r["net_pnl"] or 0 for r in closed), default=0.0), 2),
        "largest_loss": round(min((r["net_pnl"] or 0 for r in closed), default=0.0), 2),
        "max_win_streak": best_win_streak,
        "max_loss_streak": worst_loss_streak,
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd / starting_capital * 100, 2) if starting_capital else 0.0,
        "avg_holding_sec": int(sum(holding) / len(holding)) if holding else None,
        "total_charges": round(sum(r["charges"] for r in costed), 2),
        "charges_coverage": f"{len(costed)}/{len(closed)}",
        "total_turnover": round(sum(r["turnover"] or 0 for r in rows), 2),
        "unrealised_open": round(sum(r.get("unrealised_pnl") or 0 for r in open_rows), 2),
        "equity_curve": curve,
    }


def _group(rows: list[dict], key: str) -> list[dict]:
    """P&L breakdown by any row field, closed trades only, worst last."""
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        if r["status"] != "CLOSED":
            continue
        buckets.setdefault(str(r.get(key) or "—"), []).append(r)

    out = []
    for name, group in buckets.items():
        wins = [g for g in group if g["outcome"] == "WIN"]
        net = sum(g["net_pnl"] or 0 for g in group)
        r_values = [g["r_multiple"] for g in group if g["r_multiple"] is not None]
        out.append(
            {
                "key": name,
                "trades": len(group),
                "wins": len(wins),
                "losses": len(group) - len(wins),
                "win_rate_pct": round(len(wins) / len(group) * 100, 1),
                "net_pnl": round(net, 2),
                "avg_pnl": round(net / len(group), 2),
                "avg_r": round(sum(r_values) / len(r_values), 2) if r_values else None,
                "best": round(max(g["net_pnl"] or 0 for g in group), 2),
                "worst": round(min(g["net_pnl"] or 0 for g in group), 2),
            }
        )
    return sorted(out, key=lambda d: -d["net_pnl"])


def _daily(rows: list[dict]) -> list[dict]:
    days = _group(rows, "trade_date")
    for day in days:
        day["date"] = day.pop("key")
    return sorted(days, key=lambda d: d["date"], reverse=True)


async def get_report(f: ReportFilters) -> dict:
    rows = await _load_rows(f)
    summary = _summarise(rows, f.account or "AUTO")
    equity_curve = summary.pop("equity_curve")
    return {
        "summary": summary,
        "equity_curve": equity_curve,
        "daily": _daily(rows),
        "by_symbol": _group(rows, "symbol"),
        "by_side": _group(rows, "side"),
        "by_source": _group(rows, "source"),
        "by_account": _group(rows, "account"),
        "by_strategy": _group(rows, "strategy"),
        "by_exit_reason": _group(rows, "exit_reason"),
        "by_weekday": _group(rows, "weekday"),
        "by_hour": sorted(_group(rows, "entry_hour_ist"), key=lambda d: d["key"]),
        "transactions": rows,
        "generated_at": dt.datetime.now(IST).isoformat(),
    }


async def get_filter_options() -> dict:
    async with async_session() as session:
        trades = (await session.execute(select(Trade))).scalars().all()
    dates = sorted({(_to_ist(t.closed_at or t.opened_at)).date().isoformat() for t in trades if t.opened_at})
    return {
        "symbols": sorted({t.symbol for t in trades}),
        "sources": sorted({t.source or "MANUAL" for t in trades}),
        "strategies": sorted({t.strategy for t in trades}),
        "exit_reasons": sorted({t.exit_reason for t in trades if t.exit_reason}),
        "date_min": dates[0] if dates else None,
        "date_max": dates[-1] if dates else None,
    }


CSV_COLUMNS = [
    "id",
    "trade_date",
    "opened_at",
    "closed_at",
    "symbol",
    "side",
    "source",
    "account",
    "strategy",
    "status",
    "outcome",
    "quantity",
    "entry_price",
    "exit_price",
    "stop_loss",
    "target",
    "turnover",
    "planned_risk",
    "planned_rr",
    "gross_pnl",
    "entry_charges",
    "exit_charges",
    "charges",
    "charges_recorded",
    "net_pnl",
    "r_multiple",
    "return_on_turnover_pct",
    "holding_sec",
    "exit_reason",
]


async def export_csv(f: ReportFilters) -> str:
    rows = await _load_rows(f)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
