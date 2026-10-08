"""Practice wallet for the PAPER SMA bots (bots 1-4), like one Groww account.

Money is loaded once (Add money, Rs 1 to Rs 100 crore a time). Each PAPER
entry blocks a share of its value as intraday margin (``margin_pct``,
default 20% = 5x: 100 shares at Rs 10 block Rs 200). When the trade closes
the margin comes back with its P&L before charges (the owner's choice).
When the free balance is short of an entry's margin the order still goes:
the wallet takes a loan for the shortfall (``loan``), and the screens say
how much is borrowed until it is repaid from the free balance.

Nothing is stored but the money loaded (and borrowed), the loan, the margin
rate and the start time:

    free = loaded + borrowed + P&L of PAPER trades opened since the start and closed
           - margin of every open PAPER trade

so the balance always matches the trade book, restarts included. With no
money loaded the wallet is off and PAPER trades exactly as before. LIVE,
replay and research trades never touch it.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select

from database import session_factory
from models import PaperWallet, TradeLog

IST = ZoneInfo("Asia/Kolkata")
MIN_LOAD = 1.0
MAX_LOAD = 1_000_000_000.0  # Rs 100 crore per load
DEFAULT_MARGIN_PCT = 20.0

#: The latest loan taken this process, for the screens to pop up once (by its "at").
LAST_LOAN: dict | None = None


def _now() -> dt.datetime:
    return dt.datetime.now(IST).replace(tzinfo=None)


def _row(db) -> PaperWallet | None:
    return db.get(PaperWallet, 1)


def _money(v: float) -> str:
    return f"Rs {v:,.2f}"


def margin_for(price: float, qty: int, margin_pct: float) -> float:
    return abs(float(price) * int(qty)) * float(margin_pct) / 100.0


def summary() -> dict:
    """The wallet as the screens show it; ``active`` is False until money is loaded."""
    with session_factory()() as db:
        row = _row(db)
        if row is None or row.funds <= 0:
            margin = row.margin_pct if row is not None else DEFAULT_MARGIN_PCT
            return {"active": False, "funds": 0.0, "loan": 0.0, "realized": 0.0, "blocked": 0.0,
                    "available": 0.0, "margin_pct": margin, "since": None, "open": [], "last_loan": None}
        realized = db.execute(
            select(func.coalesce(func.sum(TradeLog.gross_pnl), 0.0)).where(
                and_(TradeLog.mode == "PAPER", TradeLog.exit_time.is_not(None), TradeLog.entry_time >= row.since)
            )
        ).scalar_one()
        open_rows = db.execute(
            select(TradeLog.symbol, TradeLog.bot, TradeLog.direction, TradeLog.qty, TradeLog.entry_price).where(
                and_(TradeLog.mode == "PAPER", TradeLog.exit_time.is_(None))
            )
        ).all()
        opened = [
            {
                "symbol": r.symbol, "bot": int(r.bot or 1), "direction": r.direction, "qty": int(r.qty),
                "entry_price": float(r.entry_price),
                "margin": round(margin_for(r.entry_price, r.qty, row.margin_pct), 2),
            }
            for r in open_rows
        ]
        blocked = sum(o["margin"] for o in opened)
        return {
            "active": True,
            "funds": round(row.funds, 2),
            "loan": round(row.loan or 0.0, 2),
            "realized": round(float(realized), 2),
            "blocked": round(blocked, 2),
            "available": round(row.funds + float(realized) - blocked, 2),
            "margin_pct": row.margin_pct,
            "since": row.since.isoformat(),
            "open": opened,
            "last_loan": LAST_LOAN,
        }


def cover(symbol: str, qty: int, price: float) -> dict | None:
    """Make sure a PAPER entry's margin is there; borrow the shortfall when it is not.

    Returns None when the wallet is off or the free balance covers it, else
    the loan just taken (and the total now owed), for the screens to show.
    The order is never refused for money.
    """
    w = summary()
    if not w["active"]:
        return None
    need = round(margin_for(price, qty, w["margin_pct"]), 2)
    short = round(need - max(w["available"], 0.0), 2)
    if short <= 0.005:
        return None
    with session_factory()() as db:
        row = _row(db)
        row.funds += short
        row.loan = round((row.loan or 0.0) + short, 2)
        row.updated_at = _now()
        owed = row.loan
        db.commit()
    global LAST_LOAN
    LAST_LOAN = {
        "symbol": symbol.upper(),
        "qty": int(qty),
        "price": round(float(price), 2),
        "need": need,
        "available": w["available"],
        "borrowed": short,
        "loan": owed,
        "at": _now().isoformat(),
        "text": (
            f"Balance was low: {symbol.upper()} x{int(qty)} needed {_money(need)} margin "
            f"({w['margin_pct']:g}% of {_money(abs(price * qty))}) and {_money(max(w['available'], 0.0))} was free. "
            f"Borrowed {_money(short)} to place it. Loan to pay back: {_money(owed)}."
        ),
    }
    return LAST_LOAN


def repay(amount: float | None = None) -> dict:
    """Pay the loan back from the free balance (all that is owed and free when no amount is given)."""
    w = summary()
    if not w["active"] or w["loan"] <= 0:
        raise ValueError("There is no loan to repay.")
    free = max(w["available"], 0.0)
    pay = min(w["loan"], free) if amount is None else float(amount)
    if pay <= 0 or pay > free + 1e-6:
        raise ValueError(f"Only the free balance ({_money(free)}) can repay the loan. Add money or wait for trades to close.")
    pay = min(pay, w["loan"])
    with session_factory()() as db:
        row = _row(db)
        row.funds -= pay
        row.loan = round(max(0.0, (row.loan or 0.0) - pay), 2)
        row.updated_at = _now()
        db.commit()
    return summary()


def add(amount: float) -> dict:
    amount = float(amount)
    if not MIN_LOAD <= amount <= MAX_LOAD:
        raise ValueError("Load between Rs 1 and Rs 100,00,00,000 (100 crore) at a time.")
    with session_factory()() as db:
        row = _row(db)
        now = _now()
        if row is None:
            row = PaperWallet(id=1, funds=0.0, loan=0.0, margin_pct=DEFAULT_MARGIN_PCT, since=now, updated_at=now)
            db.add(row)
        if row.funds <= 0:
            # A fresh (or emptied) account starts counting closed trades from now.
            row.since = now
            row.funds = 0.0
            row.loan = 0.0
        row.funds += amount
        row.updated_at = now
        db.commit()
    return summary()


def withdraw(amount: float) -> dict:
    amount = float(amount)
    w = summary()
    if not w["active"]:
        raise ValueError("No money is loaded.")
    if amount <= 0 or amount > w["available"] + 1e-6:
        raise ValueError(f"You can withdraw up to {_money(max(w['available'], 0.0))}, the money not blocked in trades.")
    with session_factory()() as db:
        row = _row(db)
        row.funds -= amount
        row.updated_at = _now()
        db.commit()
    return summary()


def reset() -> dict:
    """Close the account: no money loaded, so PAPER trades without a wallet again."""
    with session_factory()() as db:
        row = _row(db)
        if row is not None:
            row.funds = 0.0
            row.loan = 0.0
            row.since = _now()
            row.updated_at = _now()
            db.commit()
    return summary()


def set_margin(margin_pct: float) -> dict:
    margin_pct = float(margin_pct)
    if not 1.0 <= margin_pct <= 100.0:
        raise ValueError("Margin is 1% to 100% of the trade value (100% = no leverage).")
    with session_factory()() as db:
        row = _row(db)
        now = _now()
        if row is None:
            row = PaperWallet(id=1, funds=0.0, loan=0.0, margin_pct=margin_pct, since=now, updated_at=now)
            db.add(row)
        row.margin_pct = margin_pct
        row.updated_at = now
        db.commit()
    return summary()
