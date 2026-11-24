"""Trend readings of a backtest (trend_log.py): what the bot read on each armed stock at every candle close.

Owner's request: an auto-arm backtest works the Trend quality reading out by itself from the 1-minute candles (it never
reads the saved Trend quality scans), so nothing showed what it saw at 10:45 or why it waited. Each replayed engine
(`StrategyEngine._record_trend`) now keeps, once per closed candle of the candles it trades (`_exit_minutes`), per armed
or held stock: the price, the trend side and score (the 7 strong-trend checks, `trend_strength`), the checks, the phase
(`trend_phases`) and since when, the crosses and score label (`chop_scan.score_for`), and what the bot did with it
(`decision`: waiting and why, the order, held, exit). Saved per run in its own SQLite file next to the SMA DB
(`trend_reads.db`, `TREND_READS_FILE`); a run's rows go with the run, an unfinished day's with a resume / rewind.

Runs saved before this have no rows: `work_out` reads them again from the cached 1-minute candles with the run's own
settings (SMA periods, candle size, the trend entry level and phase box), without the bot's positions, so its decision
says only what the trend rule would have said (`source` "after").

Display only: nothing in the strategy reads this; no order path.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Iterable

log = logging.getLogger("sma.trendlog")

_lock = threading.Lock()
CHECKS_ORDER = ("stack", "vwap", "slope", "fan", "hold", "structure", "run", "volume")


def path() -> Path:
    """TREND_READS_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("TREND_READS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("trend_reads.db")
    return Path("trend_reads.db")


def _connect() -> sqlite3.Connection:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(file, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS trend_reads (
            run_id INTEGER NOT NULL,
            bot INTEGER,
            day TEXT NOT NULL,
            ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            data TEXT NOT NULL,
            PRIMARY KEY (run_id, symbol, ts)
        );
        CREATE INDEX IF NOT EXISTS trend_reads_day ON trend_reads (run_id, day);
        """
    )
    return con


def row_from(read: dict | None, *, ts: int, price: float | None, minutes: int, decision: str, held: str | None = None) -> dict:
    """One reading (a plain dict, also what the chart gets)."""
    r = read or {}
    checks = r.get("trend_checks") or {}
    return {
        "ts": int(ts),
        "price": round(float(price), 2) if price else None,
        "minutes": int(minutes),
        "side": r.get("trend_side"),
        "score": r.get("trend_score"),
        "strong": r.get("trend"),
        "mode": r.get("trend_mode"),
        "checks": {k: bool(checks[k]) for k in CHECKS_ORDER if k in checks},
        "label": r.get("score"),
        "crosses": r.get("crosses"),
        "phase": r.get("phase"),
        "phase_since": r.get("phase_since"),
        "phase_early": bool(r.get("phase_early")),
        "held": held,
        "decision": decision,
    }


def _day_of(ts: int) -> str:
    from groww_client import IST

    return dt.datetime.fromtimestamp(int(ts), IST).date().isoformat()


def save(run_id: int, bot: int, rows: Iterable[tuple[str, dict]]) -> int:
    """Store (symbol, row) readings of a run. Never raises: the log must not hurt the replay."""
    data = [
        (int(run_id), int(bot), _day_of(row["ts"]), int(row["ts"]), symbol, json.dumps(row, separators=(",", ":")))
        for symbol, row in rows
    ]
    if not data:
        return 0
    try:
        with _lock, _connect() as con:
            con.executemany(
                "INSERT OR REPLACE INTO trend_reads (run_id, bot, day, ts, symbol, data) VALUES (?, ?, ?, ?, ?, ?)", data
            )
        return len(data)
    except Exception:  # noqa: BLE001
        log.exception("could not save run %s's trend readings", run_id)
        return 0


def delete_run(run_id: int, days: list[str] | None = None) -> int:
    """Drop a run's readings (all, or those days). Never raises."""
    try:
        with _lock, _connect() as con:
            if days is None:
                cur = con.execute("DELETE FROM trend_reads WHERE run_id = ?", (int(run_id),))
            else:
                cur = con.executemany(
                    "DELETE FROM trend_reads WHERE run_id = ? AND day = ?", [(int(run_id), d) for d in days]
                )
            return int(cur.rowcount or 0)
    except Exception:  # noqa: BLE001
        log.exception("could not drop run %s's trend readings", run_id)
        return 0


def for_run(run_id: int, symbol: str | None = None, day: str | None = None) -> list[dict]:
    """A run's saved readings, oldest first, each with its `symbol` and `day`."""
    sql = "SELECT symbol, day, data FROM trend_reads WHERE run_id = ?"
    args: list = [int(run_id)]
    if symbol:
        sql += " AND symbol = ?"
        args.append(symbol.upper())
    if day:
        sql += " AND day = ?"
        args.append(day)
    sql += " ORDER BY day, symbol, ts"
    try:
        with _lock, _connect() as con:
            rows = con.execute(sql, args).fetchall()
    except Exception:  # noqa: BLE001
        log.exception("could not read run %s's trend readings", run_id)
        return []
    out = []
    for r in rows:
        item = json.loads(r["data"])
        item["symbol"] = r["symbol"]
        item["day"] = r["day"]
        item.setdefault("source", "live")
        out.append(item)
    return out


def summary(run_id: int) -> list[dict]:
    """Which stocks and days a run has readings for (the picker)."""
    try:
        with _lock, _connect() as con:
            rows = con.execute(
                "SELECT day, symbol, COUNT(*) AS n FROM trend_reads WHERE run_id = ? GROUP BY day, symbol ORDER BY day, symbol",
                (int(run_id),),
            ).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"day": r["day"], "symbol": r["symbol"], "count": int(r["n"])} for r in rows]


# ---- runs saved before: worked out again from the cached candles -----------------------------------------


def _after_decision(read: dict | None, cfg) -> str:
    import trend_trade

    if not trend_trade.entry_on(cfg):
        side = trend_trade._trend_side(read)  # noqa: SLF001
        if side is None:
            return "no trend side yet"
        return f"trend {'up' if side == 'LONG' else 'down'}, score {trend_trade.score_for(read, side)} (trend entry off)"
    side, note = trend_trade.entry_call(read, cfg)
    return f"would enter {side}: {note}" if side else note


def work_out(frame, day: dt.date, cfg, minutes: int, fast: int, slow: int) -> list[dict]:
    """Every candle close of `day` (09:15 + one candle to 15:30): the reading on candles closed before it.

    `frame` is the stock's 1-minute tape (earlier days included, for the SMAs). Without the bot's positions, so the
    decision only says what the trend entry rule would have said."""
    import stock_regime
    from groww_client import IST

    out: list[dict] = []
    span = max(1, int(minutes))
    t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=span)
    end = dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST)
    ts_col = frame["ts"].astype("int64")
    while t <= end:
        part = frame[ts_col < int(t.timestamp())]
        if not part.empty:
            try:
                read = stock_regime.read(part, fast, slow, span, t)
            except Exception:  # noqa: BLE001
                read = None
            price = float(part["close"].iloc[-1])
            row = row_from(read, ts=int(t.timestamp()), price=price, minutes=span, decision=_after_decision(read, cfg))
            row["source"] = "after"
            row["day"] = day.isoformat()
            out.append(row)
        t += dt.timedelta(minutes=span)
    return out
