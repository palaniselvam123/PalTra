"""Open P&L log (open_pnl_log.py): the "Open +₹…" figure of each desk, kept minute by minute.

The terminal's Stocks bar shows the open (unrealized) P&L of every held stock right
now, and it changes every second. This records it once a minute while NSE is open,
for the main desk (bot 1), bots 2-4 and the research desk, so the day can be read
back afterwards: what the open P&L was at 10:42, its high and low and when.

Each point: the minute (IST), the desk, its mode (PAPER / LIVE), the open P&L before
charges (the screen's figure) and after charges, and every held stock (side, qty,
entry, last price, open P&L). Its own small SQLite file next to the SMA terminal's
database (`open_pnl.db`, `OPEN_PNL_FILE`), kept `OPEN_PNL_DAYS` (default 90) days.

Read-only: it only reads the engines' positions and last prices (`_open_pnl`, the
same arithmetic as the screen); no order path, nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Callable, Iterable

from groww_client import IST, market_is_open

log = logging.getLogger("sma.openpnl")

DEFAULT_KEEP_DAYS = 90
EVERY_SEC = 60
_lock = threading.Lock()


def keep_days() -> int:
    try:
        return max(1, int(os.environ.get("OPEN_PNL_DAYS", DEFAULT_KEEP_DAYS)))
    except ValueError:
        return DEFAULT_KEEP_DAYS


def path() -> Path:
    """OPEN_PNL_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("OPEN_PNL_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("open_pnl.db")
    return Path("open_pnl.db")


def _connect() -> sqlite3.Connection:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(file, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS open_pnl (
            day TEXT NOT NULL,
            minute TEXT NOT NULL,
            desk TEXT NOT NULL,
            mode TEXT,
            gross REAL,
            net REAL,
            held INTEGER,
            stocks TEXT,
            PRIMARY KEY (desk, minute)
        );
        CREATE INDEX IF NOT EXISTS open_pnl_day ON open_pnl (day, desk);
        """
    )
    return con


def sample(engine) -> dict:
    """One desk's open P&L right now, from its positions and last prices (the screen's arithmetic)."""
    stocks = []
    gross = net = 0.0
    for name in list(getattr(engine, "positions", {}) or {}):
        book = engine.positions.get(name)
        value = engine._open_pnl(name)  # noqa: SLF001 - the same figure the Stocks bar shows
        if book is None or not value:
            continue
        g, n = float(value["gross"]), float(value["net"])
        gross += g
        net += n
        ltp = engine._ltps.get(name)  # noqa: SLF001
        stocks.append(
            {
                "symbol": name,
                "side": book.direction,
                "qty": book.qty,
                "entry": round(float(book.entry_price), 2) if book.entry_price else None,
                "ltp": round(float(ltp), 2) if ltp else None,
                "gross": round(g, 2),
                "net": round(n, 2),
            }
        )
    return {"gross": round(gross, 2), "net": round(net, 2), "held": len(stocks), "stocks": stocks}


def _mode(engine) -> str:
    cfg = getattr(engine, "_cfg_cache", None)
    return str(getattr(cfg, "trading_mode", None) or "PAPER") if cfg is not None else "PAPER"


def save(minute: dt.datetime, rows: Iterable[tuple[str, str, dict]]) -> int:
    """Store one minute's points ((desk, mode, sample) each). Never raises: the log must not hurt the bots."""
    stamp = minute.astimezone(IST).replace(second=0, microsecond=0)
    day, at = stamp.date().isoformat(), stamp.isoformat(timespec="minutes")
    data = [
        (day, at, desk, mode, s["gross"], s["net"], s["held"], json.dumps(s["stocks"], separators=(",", ":")))
        for desk, mode, s in rows
    ]
    if not data:
        return 0
    try:
        with _lock, _connect() as con:
            con.executemany("INSERT OR REPLACE INTO open_pnl VALUES (?, ?, ?, ?, ?, ?, ?, ?)", data)
            cutoff = (stamp.date() - dt.timedelta(days=keep_days())).isoformat()
            con.execute("DELETE FROM open_pnl WHERE day < ?", (cutoff,))
        return len(data)
    except Exception:  # noqa: BLE001
        log.exception("could not save the open P&L log")
        return 0


def days(desk: str | None = None, limit: int = 120) -> list[dict]:
    where, args = ("WHERE desk = ?", [desk]) if desk else ("", [])
    with _lock, _connect() as con:
        out = con.execute(
            f"SELECT day, COUNT(*) AS points, SUM(held > 0) AS held FROM open_pnl {where} GROUP BY day ORDER BY day DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
    return [{"day": r["day"], "points": r["points"], "held_points": r["held"] or 0} for r in out]


def points(day: str, desk: str) -> list[dict]:
    with _lock, _connect() as con:
        out = con.execute(
            "SELECT minute, mode, gross, net, held, stocks FROM open_pnl WHERE day = ? AND desk = ? ORDER BY minute",
            (day, desk),
        ).fetchall()
    return [
        {
            "t": r["minute"],
            "mode": r["mode"],
            "gross": r["gross"],
            "net": r["net"],
            "held": r["held"],
            "stocks": json.loads(r["stocks"] or "[]"),
        }
        for r in out
    ]


def summary(series: list[dict]) -> dict | None:
    """High, low and last of the day's open P&L (before charges), with when."""
    if not series:
        return None
    high = max(series, key=lambda p: p["gross"])
    low = min(series, key=lambda p: p["gross"])
    return {
        "high": high["gross"],
        "high_at": high["t"],
        "low": low["gross"],
        "low_at": low["t"],
        "last": series[-1]["gross"],
        "last_at": series[-1]["t"],
    }


class OpenPnlRecorder:
    """Every minute while NSE is open, one point per desk."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None

    def start(self, desks: Callable[[], list[tuple[str, object]]]) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.get_running_loop().create_task(self._loop(desks))

    def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()

    async def _loop(self, desks: Callable[[], list[tuple[str, object]]]) -> None:
        while True:
            now = dt.datetime.now(IST)
            # Wake just after each minute starts, so a point is that minute's figure.
            await asyncio.sleep(max(1.0, EVERY_SEC - now.second - now.microsecond / 1e6 + 1))
            try:
                await self.record_once(desks)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("open P&L sample failed")

    async def record_once(self, desks: Callable[[], list[tuple[str, object]]], now: dt.datetime | None = None) -> int:
        now = now or dt.datetime.now(IST)
        if not market_is_open(now):
            return 0
        rows = []
        for desk, engine in desks():
            try:
                rows.append((desk, "RESEARCH" if desk == "research" else _mode(engine), sample(engine)))
            except Exception:  # noqa: BLE001
                log.exception("could not read desk %s's open P&L", desk)
        return await asyncio.to_thread(save, now, rows)
