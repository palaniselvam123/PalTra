"""P&L log (open_pnl_log.py): each desk's day P&L, kept minute by minute.

The terminal's Stocks bar shows the open (unrealized) P&L of every held stock right
now, and it changes every second. This records it once a minute while NSE is open,
for the main desk (bot 1), bots 2-4 and the research desk, so the day can be read
back afterwards: what the open P&L was at 10:42, its high and low and when.

Each point: the minute (IST), the desk, its mode (PAPER / LIVE), the open P&L before
charges (the screen's figure) and after charges, and every held stock (side, qty,
entry, last price, open P&L). Its own small SQLite file next to the SMA terminal's
database (`open_pnl.db`, `OPEN_PNL_FILE`), kept `OPEN_PNL_DAYS` (default 90) days.

Each point also keeps today's **closed** P&L of the desk's current book at that minute (the engine's
`realized_gross` / `realized_net`, refreshed after every close from the screen's KPI sum, so no extra DB
read), and `total_*` = closed + open: the day's P&L as it stood then. Rows saved before closed P&L was
kept read 0 closed.

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
            closed_gross REAL,
            closed_net REAL,
            PRIMARY KEY (desk, minute)
        );
        CREATE INDEX IF NOT EXISTS open_pnl_day ON open_pnl (day, desk);
        CREATE TABLE IF NOT EXISTS replay_pnl (
            run_id INTEGER NOT NULL,
            bot INTEGER NOT NULL,
            day TEXT NOT NULL,
            minute TEXT NOT NULL,
            gross REAL,
            net REAL,
            held INTEGER,
            stocks TEXT,
            saved_at TEXT NOT NULL,
            closed_gross REAL,
            closed_net REAL,
            PRIMARY KEY (run_id, minute)
        );
        CREATE INDEX IF NOT EXISTS replay_pnl_bot ON replay_pnl (bot, run_id);
        """
    )
    # Files made before closed P&L was kept: add the two columns (older rows read 0 closed).
    for table in ("open_pnl", "replay_pnl"):
        have = {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
        for col in ("closed_gross", "closed_net"):
            if col not in have:
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} REAL")
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
    closed_gross = float(getattr(engine, "realized_gross", 0.0) or 0.0)
    closed_net = float(getattr(engine, "realized_net", 0.0) or 0.0)
    return {
        "gross": round(gross, 2),
        "net": round(net, 2),
        "held": len(stocks),
        "stocks": stocks,
        "closed_gross": round(closed_gross, 2),
        "closed_net": round(closed_net, 2),
    }


def _point(r, mode: str) -> dict:
    """One stored row as the API sends it: open, closed and their total (the day's P&L at that minute)."""
    cg = float(r["closed_gross"] or 0.0)
    cn = float(r["closed_net"] or 0.0)
    return {
        "t": r["minute"],
        "mode": mode,
        "gross": r["gross"],
        "net": r["net"],
        "closed_gross": round(cg, 2),
        "closed_net": round(cn, 2),
        "total_gross": round(cg + float(r["gross"] or 0.0), 2),
        "total_net": round(cn + float(r["net"] or 0.0), 2),
        "held": r["held"],
        "stocks": json.loads(r["stocks"] or "[]"),
    }


def _mode(engine) -> str:
    cfg = getattr(engine, "_cfg_cache", None)
    return str(getattr(cfg, "trading_mode", None) or "PAPER") if cfg is not None else "PAPER"


def save(minute: dt.datetime, rows: Iterable[tuple[str, str, dict]]) -> int:
    """Store one minute's points ((desk, mode, sample) each). Never raises: the log must not hurt the bots."""
    stamp = minute.astimezone(IST).replace(second=0, microsecond=0)
    day, at = stamp.date().isoformat(), stamp.isoformat(timespec="minutes")
    data = [
        (day, at, desk, mode, s["gross"], s["net"], s["held"], json.dumps(s["stocks"], separators=(",", ":")),
         s.get("closed_gross", 0.0), s.get("closed_net", 0.0))
        for desk, mode, s in rows
    ]
    if not data:
        return 0
    try:
        with _lock, _connect() as con:
            con.executemany(
                "INSERT OR REPLACE INTO open_pnl (day, minute, desk, mode, gross, net, held, stocks, closed_gross, closed_net)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                data,
            )
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
            "SELECT minute, mode, gross, net, held, stocks, closed_gross, closed_net FROM open_pnl"
            " WHERE day = ? AND desk = ? ORDER BY minute",
            (day, desk),
        ).fetchall()
    return [_point(r, r["mode"]) for r in out]


def by_stock(series: list[dict], closed: list[tuple[str, str, float]]) -> dict[str, list[dict]]:
    """Each stock's P&L through the day, one point per logged minute (before charges).

    `closed` is the day's closed trades of that book as (symbol, exit "HH:MM", gross P&L). At each minute a
    stock's total is its closed trades up to that minute plus its open P&L in the point."""
    names = sorted({s.get("symbol") for p in series for s in p.get("stocks") or [] if s.get("symbol")} | {c[0] for c in closed})
    out: dict[str, list[dict]] = {}
    for name in names:
        mine = sorted((at, g) for sym, at, g in closed if sym == name)
        rows = []
        for p in series:
            minute = str(p["t"])[11:16]
            done = round(sum(g for at, g in mine if at <= minute), 2)
            held = next((s for s in p.get("stocks") or [] if s.get("symbol") == name), None)
            opened = float(held.get("gross", held.get("net")) or 0.0) if held else 0.0
            rows.append({"t": p["t"], "closed": done, "open": round(opened, 2), "total": round(done + opened, 2),
                         "side": held.get("side") if held else None})
        out[name] = rows
    return out


def summary(series: list[dict], key: str = "total_gross") -> dict | None:
    """High, low and last of the day's P&L (closed + open, before charges by default), with when."""
    if not series:
        return None
    val = lambda p: p.get(key, p["gross"])  # noqa: E731
    high = max(series, key=val)
    low = min(series, key=val)
    return {
        "high": val(high),
        "high_at": high["t"],
        "low": val(low),
        "low_at": low["t"],
        "last": val(series[-1]),
        "last_at": series[-1]["t"],
        "basis": key,
        "open": summary(series, "gross") if key != "gross" else None,
    }


# ---- replays: the same figure, one point per replayed minute, kept per run ------------------


def save_replay(run_id: int, bot: int, rows: Iterable[tuple[dt.datetime, dict]]) -> int:
    """Store a replay's points ((replay minute, sample) each). Never raises: the log must not hurt the replay."""
    now = dt.datetime.now(dt.timezone.utc)
    data = []
    for minute, s in rows:
        stamp = minute.astimezone(IST).replace(second=0, microsecond=0)
        data.append(
            (
                int(run_id), int(bot), stamp.date().isoformat(), stamp.isoformat(timespec="minutes"),
                s["gross"], s["net"], s["held"], json.dumps(s["stocks"], separators=(",", ":")),
                now.isoformat(timespec="seconds"), s.get("closed_gross", 0.0), s.get("closed_net", 0.0),
            )
        )
    if not data:
        return 0
    try:
        with _lock, _connect() as con:
            con.executemany(
                "INSERT OR REPLACE INTO replay_pnl (run_id, bot, day, minute, gross, net, held, stocks, saved_at,"
                " closed_gross, closed_net) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                data,
            )
            cutoff = (now - dt.timedelta(days=keep_days())).isoformat(timespec="seconds")
            con.execute("DELETE FROM replay_pnl WHERE saved_at < ?", (cutoff,))
        return len(data)
    except Exception:  # noqa: BLE001
        log.exception("could not save the replay's open P&L log")
        return 0


def delete_replay(run_id: int) -> None:
    try:
        with _lock, _connect() as con:
            con.execute("DELETE FROM replay_pnl WHERE run_id = ?", (int(run_id),))
    except Exception:  # noqa: BLE001
        log.exception("could not delete a replay's open P&L log")


def replay_runs(bot: int, limit: int = 100) -> list[dict]:
    """A bot's replay runs with recorded points, newest first: their days and point counts."""
    with _lock, _connect() as con:
        out = con.execute(
            "SELECT run_id, MIN(day) AS first, MAX(day) AS last, COUNT(DISTINCT day) AS days, COUNT(*) AS points,"
            " MAX(saved_at) AS saved_at FROM replay_pnl WHERE bot = ? GROUP BY run_id ORDER BY run_id DESC LIMIT ?",
            (int(bot), limit),
        ).fetchall()
    return [dict(r) for r in out]


def replay_days(run_id: int) -> list[dict]:
    with _lock, _connect() as con:
        out = con.execute(
            "SELECT day, COUNT(*) AS points, SUM(held > 0) AS held FROM replay_pnl WHERE run_id = ? GROUP BY day ORDER BY day",
            (int(run_id),),
        ).fetchall()
    return [{"day": r["day"], "points": r["points"], "held_points": r["held"] or 0} for r in out]


def replay_points(run_id: int, day: str) -> list[dict]:
    with _lock, _connect() as con:
        out = con.execute(
            "SELECT minute, gross, net, held, stocks, closed_gross, closed_net FROM replay_pnl"
            " WHERE run_id = ? AND day = ? ORDER BY minute",
            (int(run_id), day),
        ).fetchall()
    return [_point(r, "REPLAY") for r in out]


class ReplayRecorder:
    """Collects one point per replayed minute and saves them in batches, off the event loop."""

    BATCH = 30

    def __init__(self, run_id: int | None, bot: int) -> None:
        self.run_id = run_id
        self.bot = int(bot)
        self._last: str | None = None
        self._buffer: list[tuple[dt.datetime, dict]] = []
        self._tasks: set = set()

    def note(self, engine, clock: dt.datetime) -> None:
        """Called after each replay tick: keeps the first tick of each new replay minute."""
        if self.run_id is None:
            return
        key = clock.strftime("%Y-%m-%d %H:%M")
        if key == self._last:
            return
        self._last = key
        try:
            self._buffer.append((clock, sample(engine)))
        except Exception:  # noqa: BLE001
            log.exception("could not read the replay's open P&L")
            return
        if len(self._buffer) >= self.BATCH:
            self.flush()

    def flush(self) -> None:
        if not self._buffer or self.run_id is None:
            return
        rows, self._buffer = self._buffer, []
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            save_replay(self.run_id, self.bot, rows)
            return
        task = loop.create_task(asyncio.to_thread(save_replay, self.run_id, self.bot, rows))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


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
