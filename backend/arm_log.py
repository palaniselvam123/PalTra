"""Arm log (arm_log.py): when each stock was armed on, or taken off, a desk's Trade list.

Bookkeeping only; nothing here places or changes an order. Every change of
`BotConfig.trade_symbols` made through the ORM (Settings, the terminal's Arm switch,
the scanner link, copy settings, the recovery / breakout handoff, the chat and MCP
tools, which all save through the same routes) is noted by a `before_flush` hook as
one `ArmLog` row per stock: ARM or UNARM, with the stock's source chip and the desk's
PAPER / LIVE mode. At boot `baseline` adds an ARMED row for each stock that is armed
but whose last row is not an arm, so a stock armed before the log existed still has
a start.

`windows(config_id, day)` turns a desk's rows into the stretches of that day each
stock was armed, for a replay that arms each stock when it was armed live
(`ReplayEngine.arm_windows`).
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from models import ArmLog, BotConfig

IST = ZoneInfo("Asia/Kolkata")
ON = ("ARM", "ARMED")


def _names(raw) -> list[str]:
    return [part.strip().upper() for part in str(raw or "").split(",") if part.strip()]


def _sources(row: BotConfig) -> dict[str, str]:
    try:
        from strategy_engine import trade_sources

        return trade_sources(row)
    except Exception:  # noqa: BLE001
        return {}


def desk_of(config_id: int) -> str:
    """'1'-'5' or 'research' for a BotConfig row id."""
    cid = int(config_id)
    if cid == 2:
        return "research"
    return "1" if cid == 1 else str(cid - 1)


def config_id_of(desk: str | int) -> int:
    """The BotConfig row of a desk: '1'-'4' or 'research'."""
    text = str(desk).strip().lower()
    if text == "research":
        return 2
    bot = int(text)
    if bot not in (1, 2, 3, 4, 5):
        raise ValueError("desk must be 1-5 or research")
    return 1 if bot == 1 else bot + 1


def _now() -> dt.datetime:
    return dt.datetime.now(IST).replace(tzinfo=None)


@event.listens_for(Session, "before_flush")
def _note_trade_list_changes(session: Session, _context, _instances) -> None:
    """One ArmLog row per stock armed or taken off, for each saved Trade list change."""
    for row in list(session.dirty):
        if not isinstance(row, BotConfig) or row.id is None:
            continue
        history = inspect(row).attrs.trade_symbols.history
        if not history.has_changes():
            continue
        before = _names(history.deleted[0]) if history.deleted else []
        after = _names(row.trade_symbols)
        added = [name for name in after if name not in before]
        removed = [name for name in before if name not in after]
        if not added and not removed:
            continue
        at = _now()
        mode = (row.trading_mode or "PAPER").upper()
        sources = _sources(row)
        for name in added:
            session.add(ArmLog(at=at, config_id=int(row.id), symbol=name, action="ARM", source=sources.get(name), mode=mode))
        for name in removed:
            session.add(ArmLog(at=at, config_id=int(row.id), symbol=name, action="UNARM", mode=mode))


def baseline() -> int:
    """At boot: an ARMED row for each armed stock whose last row is not an arm. Returns rows added."""
    from database import session_factory

    added = 0
    with session_factory()() as db:
        last: dict[tuple[int, str], str] = {}
        for config_id, symbol, action in db.query(ArmLog.config_id, ArmLog.symbol, ArmLog.action).order_by(ArmLog.id):
            last[(int(config_id), symbol)] = action
        at = _now()
        for row in db.query(BotConfig).all():
            sources = _sources(row)
            for name in _names(row.trade_symbols):
                if last.get((int(row.id), name)) in ON:
                    continue
                db.add(
                    ArmLog(
                        at=at,
                        config_id=int(row.id),
                        symbol=name,
                        action="ARMED",
                        source=sources.get(name),
                        mode=(row.trading_mode or "PAPER").upper(),
                    )
                )
                added += 1
        db.commit()
    return added


def rows(config_id: int, day: dt.date) -> list[dict]:
    """A desk's arm / unarm rows on one IST day, oldest first."""
    from database import session_factory

    start = dt.datetime.combine(day, dt.time(0, 0))
    with session_factory()() as db:
        found = (
            db.query(ArmLog)
            .filter(ArmLog.config_id == int(config_id), ArmLog.at >= start, ArmLog.at < start + dt.timedelta(days=1))
            .order_by(ArmLog.at, ArmLog.id)
            .all()
        )
        return [
            {
                "id": r.id,
                "at": r.at.isoformat(),
                "symbol": r.symbol,
                "action": r.action,
                "source": r.source,
                "mode": r.mode,
            }
            for r in found
        ]


def windows(config_id: int, day: dt.date) -> dict[str, list[tuple[int, int]]]:
    """Each stock's armed stretches on `day` as (start, end) epoch seconds, from the desk's rows.

    A stock armed before the day (and not taken off) is armed from midnight; one still
    armed at the end of the day stays armed to midnight. Stocks never armed that day are left out.
    """
    from database import session_factory

    day_start = dt.datetime.combine(day, dt.time(0, 0))
    day_end = day_start + dt.timedelta(days=1)
    with session_factory()() as db:
        found = (
            db.query(ArmLog.symbol, ArmLog.action, ArmLog.at)
            .filter(ArmLog.config_id == int(config_id), ArmLog.at < day_end)
            .order_by(ArmLog.at, ArmLog.id)
            .all()
        )
    since: dict[str, dt.datetime | None] = {}
    out: dict[str, list[tuple[int, int]]] = {}

    def epoch(when: dt.datetime) -> int:
        return int(when.replace(tzinfo=IST).timestamp())

    for symbol, action, at in found:
        armed_from = since.get(symbol)
        if action in ON:
            if armed_from is None:
                since[symbol] = max(at, day_start)
        elif armed_from is not None:
            since[symbol] = None
            if at > day_start:
                out.setdefault(symbol, []).append((epoch(armed_from), epoch(at)))
    for symbol, armed_from in since.items():
        if armed_from is not None:
            out.setdefault(symbol, []).append((epoch(armed_from), epoch(day_end)))
    return out


def armed_at(spans: list[tuple[int, int]], when: int) -> bool:
    return any(start <= when < end for start, end in spans)
