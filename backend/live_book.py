"""A bot's own book of real days (NSE live or practice trades) as one "run" on the Compare page (owner's request:
compare what the bot really did on a day with a backtest of the same day and strategy).

Backtest runs are ReplayRun rows with positive ids; a live book is a **negative id** that carries what it is:

    -(first day YYYYMMDD × 10_000 + span in days × 100 + bot × 10 + book)

`book` 1 = LIVE (real Groww orders), 2 = PAPER (practice), 3 = RESEARCH (the research desk, bot 0). `span` is the
calendar days after the first day (0 = one day, at most MAX_SPAN). backtest_compare.load and backtest_detail.load
read these trades straight from the trade book (`mode`, `bot`, `date`, no `run_id`), so every part of the Compare
page works on them. The settings shown are the most common strategy snapshot saved on the trades at their entry
(`TradeLog.strategy`); how many different ones there were is in `settings_variants`.

Read-only: nothing here orders, changes a setting or arms a stock.
"""
from __future__ import annotations

import datetime as dt
import json
from collections import Counter

BOOKS = {1: "LIVE", 2: "PAPER", 3: "RESEARCH"}
BOOK_CODE = {v: k for k, v in BOOKS.items()}
MAX_SPAN = 45
BOOK_WORD = {"LIVE": "NSE live", "PAPER": "practice", "RESEARCH": "research desk"}


def is_live(run_id: int) -> bool:
    return int(run_id) < 0


def encode(bot: int, book: str, start: str, end: str | None = None) -> int:
    """The id of a bot's book over start..end (YYYY-MM-DD). Raises ValueError for a bad request."""
    book = str(book).upper()
    if book not in BOOK_CODE:
        raise ValueError("book must be LIVE, PAPER or RESEARCH")
    bot = 0 if book == "RESEARCH" else int(bot)
    if book != "RESEARCH" and not 1 <= bot <= 5:
        raise ValueError("bot must be 1-5")
    first = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end) if end else first
    span = (last - first).days
    if not 0 <= span <= MAX_SPAN:
        raise ValueError(f"A live book covers 1 to {MAX_SPAN + 1} calendar days, last day after the first.")
    return -(int(first.strftime("%Y%m%d")) * 10_000 + span * 100 + bot * 10 + BOOK_CODE[book])


def decode(run_id: int) -> dict:
    """{bot, book, start, end, days} of a negative id. Raises ValueError for one that is not a live book."""
    raw = -int(run_id)
    if raw <= 0:
        raise ValueError("not a live book id")
    code, raw = raw % 10, raw // 10
    bot, raw = raw % 10, raw // 10
    span, day = raw % 100, raw // 100
    if code not in BOOKS:
        raise ValueError(f"No live book {run_id}.")
    try:
        first = dt.datetime.strptime(str(day), "%Y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"No live book {run_id}.") from exc
    last = first + dt.timedelta(days=span)
    days = [(first + dt.timedelta(days=i)).isoformat() for i in range(span + 1)
            if (first + dt.timedelta(days=i)).weekday() < 5]
    return {"bot": bot, "book": BOOKS[code], "start": first.isoformat(), "end": last.isoformat(), "days": days}


def label(run_id: int) -> str:
    """Plain name, e.g. "Bot 1 NSE live · 17 Nov" or "Research desk · 12-14 Nov"."""
    d = decode(run_id)
    first, last = dt.date.fromisoformat(d["start"]), dt.date.fromisoformat(d["end"])
    when = first.strftime("%-d %b") if first == last else f"{first.strftime('%-d %b')} - {last.strftime('%-d %b')}"
    who = "Research desk" if d["book"] == "RESEARCH" else f"Bot {d['bot']} {BOOK_WORD[d['book']]}"
    return f"{who} · {when}"


def desk(run_id: int) -> str:
    """The open P&L log's desk name ('1'-'5' or 'research')."""
    d = decode(run_id)
    return "research" if d["book"] == "RESEARCH" else str(d["bot"])


def _query(db, run_id: int):
    from models import TradeLog

    d = decode(run_id)
    q = db.query(TradeLog).filter(
        TradeLog.mode == d["book"],
        TradeLog.run_id.is_(None),
        TradeLog.date >= d["start"],
        TradeLog.date <= d["end"],
        TradeLog.exit_price.isnot(None),
    )
    if d["book"] != "RESEARCH":
        q = q.filter((TradeLog.bot == d["bot"]) | (TradeLog.bot.is_(None)) if d["bot"] == 1 else TradeLog.bot == d["bot"])
    return q.order_by(TradeLog.entry_time, TradeLog.id)


def trades(db, run_id: int) -> list:
    """The book's closed TradeLog rows, oldest first."""
    return _query(db, run_id).all()


def _armed(run_id: int, days: list[str]) -> set[str]:
    """Stocks the bot had armed on those days (arm log), for the common ground of the comparison."""
    try:
        import arm_log

        d = decode(run_id)
        cid = arm_log.config_id_of("research" if d["book"] == "RESEARCH" else d["bot"])
        out: set[str] = set()
        for day in days:
            out |= {s.upper() for s in arm_log.windows(cid, dt.date.fromisoformat(day))}
        return out
    except Exception:  # noqa: BLE001
        return set()


def run(db, run_id: int, rows: list | None = None) -> dict:
    """The book as a backtest_compare run dict (same keys as a ReplayRun's)."""
    d = decode(run_id)
    rows = trades(db, run_id) if rows is None else rows
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    days = [x for x in d["days"] if x <= today]
    snaps = Counter(t.strategy for t in rows if t.strategy)
    settings: dict = {}
    if snaps:
        try:
            settings = json.loads(snaps.most_common(1)[0][0]) or {}
        except (TypeError, ValueError):
            settings = {}
    settings = {k: v for k, v in settings.items() if k != "qty"} | {
        "bot": d["bot"] or 1,
        "bot_name": label(run_id),
        "book": d["book"],
    }
    symbols = {(t.symbol or "").upper() for t in rows} | _armed(run_id, days)
    return {
        "id": int(run_id),
        "kind": "live",
        "label": label(run_id),
        "book": d["book"],
        "status": "LIVE_BOOK",
        "start_date": d["start"],
        "end_date": d["end"],
        "created_at": None,
        "days_total": len(days),
        "days_done": len(days),
        "days": days,
        "symbols": sorted(s for s in symbols if s),
        "settings": settings,
        "settings_variants": len(snaps),
    }


def days_with_trades(bot: int, book: str, limit: int = 60) -> list[dict]:
    """The recent days this bot's book traded, newest first: [{day, id, trades, gross}] for the blotter's picker."""
    from sqlalchemy import func

    from database import session_factory
    from models import TradeLog

    book = str(book).upper()
    if book not in BOOK_CODE:
        raise ValueError("book must be LIVE, PAPER or RESEARCH")
    with session_factory()() as db:
        q = db.query(TradeLog.date, func.count(TradeLog.id), func.sum(TradeLog.gross_pnl)).filter(
            TradeLog.mode == book, TradeLog.run_id.is_(None), TradeLog.exit_price.isnot(None)
        )
        if book != "RESEARCH":
            q = q.filter((TradeLog.bot == int(bot)) | (TradeLog.bot.is_(None)) if int(bot) == 1 else TradeLog.bot == int(bot))
        rows = q.group_by(TradeLog.date).order_by(TradeLog.date.desc()).limit(int(limit)).all()
    return [
        {"day": day, "id": encode(bot, book, day), "trades": int(n or 0), "gross": round(float(g or 0.0), 2)}
        for day, n, g in rows
        if day
    ]
