"""Scan history (scan_history.py): every finished Cross scan, Trend quality and Most active pass, kept.

The Scalp page's tables are replaced on every scan, so what a scan said at 10:42
was gone by 11:12. Each finished pass is saved here with its time, its choices
(candle size, SMA periods, quiet minutes, price range) and every row exactly as
the page got it, so the History tab can show any past scan after hours, download
it, and check how it turned out (scan_accuracy.py).

Its own small SQLite file next to the SMA terminal's database (the Fly volume),
`SCAN_HISTORY_FILE` to move it. Rows are stored gzip-compressed JSON (a 213-stock
Trend quality pass is about 30 KB), and runs older than `SCAN_HISTORY_DAYS`
(default 90) are dropped on the next save. Read-only towards trading: nothing in
the strategy reads it, no order path.
"""
from __future__ import annotations

import datetime as dt
import gzip
import json
import logging
import os
import sqlite3
import threading
from pathlib import Path

log = logging.getLogger("sma.scanhistory")

KINDS = ("cross", "chop", "active", "closebook")
# The terminal's History tab lists these; "active" (the desk's Most active scan) has its own history view.
SCAN_KINDS = ("cross", "chop")
KIND_LABEL = {"cross": "Cross scan", "chop": "Trend quality", "active": "Most active", "closebook": "After the close"}
DEFAULT_KEEP_DAYS = 90
_lock = threading.Lock()


def keep_days() -> int:
    try:
        return max(1, int(os.environ.get("SCAN_HISTORY_DAYS", DEFAULT_KEEP_DAYS)))
    except ValueError:
        return DEFAULT_KEEP_DAYS


def path() -> Path:
    """SCAN_HISTORY_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("SCAN_HISTORY_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("scan_history.db")
    return Path("scan_history.db")


def _connect() -> sqlite3.Connection:
    file = path()
    file.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(file, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            day TEXT NOT NULL,
            as_of TEXT NOT NULL,
            finished_at TEXT,
            minutes INTEGER,
            total INTEGER,
            failed INTEGER,
            row_count INTEGER,
            error TEXT,
            meta TEXT,
            rows BLOB
        );
        CREATE INDEX IF NOT EXISTS scan_runs_day ON scan_runs (day, kind);
        CREATE TABLE IF NOT EXISTS scan_checks (
            run_id INTEGER PRIMARY KEY,
            checked_at TEXT NOT NULL,
            result BLOB
        );
        """
    )
    return con


def _pack(value) -> bytes:
    return gzip.compress(json.dumps(value, separators=(",", ":"), default=str).encode())


def _unpack(blob) -> object:
    return json.loads(gzip.decompress(blob).decode()) if blob else None


def _ist_day(as_of: str) -> str:
    try:
        return dt.datetime.fromisoformat(as_of).date().isoformat()
    except ValueError:
        return as_of[:10]


def save(kind: str, snapshot: dict, meta: dict | None = None) -> int | None:
    """Keep one finished pass (the scanner's snapshot()). Never raises: history must not break a scan."""
    if kind not in KINDS or not snapshot.get("as_of"):
        return None
    rows = snapshot.get("rows") or []
    info = {
        k: snapshot.get(k)
        for k in ("sma_fast", "sma_slow", "quiet_min", "market_open", "universe", "scanned", "missed")
        if snapshot.get(k) is not None
    }
    info.update(meta or {})
    try:
        with _lock, _connect() as con:
            cur = con.execute(
                "INSERT INTO scan_runs (kind, day, as_of, finished_at, minutes, total, failed, row_count, error, meta, rows)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    kind,
                    _ist_day(snapshot["as_of"]),
                    snapshot["as_of"],
                    snapshot.get("finished_at"),
                    snapshot.get("minutes"),
                    snapshot.get("total"),
                    snapshot.get("failed"),
                    len(rows),
                    snapshot.get("error"),
                    json.dumps(info, default=str),
                    _pack(rows),
                ),
            )
            run_id = cur.lastrowid
            _prune(con)
        return run_id
    except Exception:  # noqa: BLE001
        log.exception("could not save the scan to history")
        return None


def _prune(con: sqlite3.Connection) -> None:
    cutoff = (dt.date.today() - dt.timedelta(days=keep_days())).isoformat()
    con.execute("DELETE FROM scan_checks WHERE run_id IN (SELECT id FROM scan_runs WHERE day < ?)", (cutoff,))
    con.execute("DELETE FROM scan_runs WHERE day < ?", (cutoff,))


def _meta_row(row: sqlite3.Row, checked_at: str | None = None) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "label": KIND_LABEL.get(row["kind"], row["kind"]),
        "day": row["day"],
        "as_of": row["as_of"],
        "finished_at": row["finished_at"],
        "minutes": row["minutes"],
        "total": row["total"],
        "failed": row["failed"],
        "row_count": row["row_count"],
        "error": row["error"],
        "meta": json.loads(row["meta"] or "{}"),
        "checked_at": checked_at,
    }


def days(kind: str | None = None, limit: int = 120) -> list[dict]:
    """The days with saved scans, newest first, with how many runs each."""
    where, args = ("WHERE kind = ?", [kind]) if kind in KINDS else ("WHERE kind IN ('cross', 'chop')", [])
    with _lock, _connect() as con:
        out = con.execute(
            f"SELECT day, COUNT(*) AS runs FROM scan_runs {where} GROUP BY day ORDER BY day DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
    return [{"day": r["day"], "runs": r["runs"]} for r in out]


def runs(day: str | None = None, kind: str | None = None, limit: int = 500) -> list[dict]:
    """One day's runs (or the latest), oldest first within the day, without their rows."""
    clauses, args = [], []
    if day:
        clauses.append("r.day = ?")
        args.append(day)
    if kind in KINDS:
        clauses.append("r.kind = ?")
        args.append(kind)
    else:
        clauses.append("r.kind IN ('cross', 'chop')")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order = "r.as_of ASC" if day else "r.as_of DESC"
    with _lock, _connect() as con:
        out = con.execute(
            "SELECT r.id, r.kind, r.day, r.as_of, r.finished_at, r.minutes, r.total, r.failed, r.row_count, r.error, r.meta,"
            f" c.checked_at FROM scan_runs r LEFT JOIN scan_checks c ON c.run_id = r.id {where} ORDER BY {order}, r.id LIMIT ?",
            (*args, limit),
        ).fetchall()
    return [_meta_row(r, r["checked_at"]) for r in out]


def get(run_id: int) -> dict | None:
    """One run with its saved rows, and its accuracy check when one was made."""
    with _lock, _connect() as con:
        row = con.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None
        check = con.execute("SELECT checked_at, result FROM scan_checks WHERE run_id = ?", (run_id,)).fetchone()
    out = _meta_row(row, check["checked_at"] if check else None)
    out["rows"] = _unpack(row["rows"]) or []
    out["check"] = _unpack(check["result"]) if check else None
    return out


# The fields of a Trend quality row the History tab's "Across days" filter and table read (chop_rows): the rest
# (phases, notes, activity) stays in the saved run, so several days of scans fit in one answer.
CHOP_KEEP = (
    "symbol", "ltp", "score", "trend", "trend_score", "trend_mode", "fresh", "phase", "phase_early", "phase_since_ts",
    "phase_move_pct", "phase_candles", "trend_checks", "speed_pct_per_min", "crosses_today", "day_move_pct", "crosses_by_minutes",
    "last_cross_side_by_minutes", "side_by_minutes", "trend_by_minutes", "run_candles_by_minutes",
    "run_since_ts_by_minutes", "gap_pct_by_minutes", "gap_peak_pct_by_minutes",
)
MAX_ACROSS_DAYS = 10


def chop_rows(days: list[str], minutes: int | None = None, candles: str | None = None) -> list[dict]:
    """Every saved Trend quality run of the given days (oldest first), each with its rows cut to CHOP_KEEP.
    `minutes` keeps one candle size, `candles` NORMAL / HA one candle kind (a run saved without it is NORMAL)."""
    picked = [d for d in dict.fromkeys(days) if d][:MAX_ACROSS_DAYS]
    if not picked:
        return []
    marks = ",".join("?" for _ in picked)
    with _lock, _connect() as con:
        found = con.execute(
            f"SELECT * FROM scan_runs WHERE kind = 'chop' AND day IN ({marks}) ORDER BY as_of ASC, id ASC", picked
        ).fetchall()
    out = []
    for row in found:
        meta = _meta_row(row)
        kind = "HA" if (meta["meta"] or {}).get("candles") == "HA" else "NORMAL"
        if minutes and meta["minutes"] != minutes:
            continue
        if candles in ("NORMAL", "HA") and kind != candles:
            continue
        rows = _unpack(row["rows"]) or []
        meta["candles"] = kind
        meta["rows"] = [{k: r.get(k) for k in CHOP_KEEP if k in r} for r in rows if isinstance(r, dict)]
        out.append(meta)
    return out


# The fields of a Most active row the Most active History's "Across days" view reads (active_rows), sent as one list
# per row in this order, so a day of scans every few minutes stays small.
ACTIVE_KEEP = (
    "symbol", "ltp", "change_pct", "value_cr", "rvol", "buy_share", "vwap_dist_pct", "range_pct", "bias",
    "speed_pct_min",
)


def active_rows(days: list[str]) -> list[dict]:
    """Every saved Most active run of the given days (oldest first): {id, day, as_of, total, failed, rows}, each row a
    list in ACTIVE_KEEP order (numbers rounded). Read-only."""
    picked = [d for d in dict.fromkeys(days) if d][:MAX_ACROSS_DAYS]
    if not picked:
        return []
    marks = ",".join("?" for _ in picked)
    with _lock, _connect() as con:
        found = con.execute(
            f"SELECT id, day, as_of, total, failed, rows FROM scan_runs WHERE kind = 'active' AND day IN ({marks}) "
            "ORDER BY as_of ASC, id ASC",
            picked,
        ).fetchall()

    def cell(v):
        return round(v, 4) if isinstance(v, float) else v

    out = []
    for row in found:
        rows = _unpack(row["rows"]) or []
        out.append({
            "id": int(row["id"]), "day": row["day"], "as_of": row["as_of"], "total": row["total"], "failed": row["failed"],
            "rows": [[cell(r.get(k)) for k in ACTIVE_KEEP] for r in rows if isinstance(r, dict) and r.get("symbol")],
        })
    return out


def save_check(run_id: int, result: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _lock, _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO scan_checks (run_id, checked_at, result) VALUES (?, ?, ?)",
            (run_id, now, _pack(result)),
        )


def find(kind: str, as_of: str) -> int | None:
    """The id of the saved run of `kind` taken at `as_of` (a scan copied from another server), else None."""
    with _lock, _connect() as con:
        row = con.execute(
            "SELECT id FROM scan_runs WHERE kind = ? AND as_of = ? ORDER BY id DESC LIMIT 1", (kind, as_of)
        ).fetchone()
    return int(row["id"]) if row else None


def save_copy(kind: str, snapshot: dict, meta: dict | None = None) -> int | None:
    """Save a scan sent from another server once (a resend of the same scan is ignored)."""
    if kind in KINDS and snapshot.get("as_of") and find(kind, snapshot["as_of"]) is not None:
        return None
    return save(kind, snapshot, meta)


# Buyers' share per stock from a day's saved Most active scans (the chart's buyers / sellers strip, pressure_exit.series).
# Each day is read once and then only the runs saved after it (a Most active pass every few minutes), so the chart's
# frequent refresh does not unpack every scan again.
# day -> (last run id read, {symbol: [(epoch, share 0..1, volume vs usual or None, money traded ₹ cr or None)]}).
_ACTIVE_CACHE: dict[str, tuple[int, dict[str, list[tuple[float, float, float | None, float | None]]]]] = {}
_ACTIVE_CACHE_DAYS = 6
# The bots read it on every pass (pressure_exit): the file is asked for new runs at most this often per day.
ACTIVE_RECHECK_SEC = 15.0
_ACTIVE_CHECKED: dict[str, float] = {}


def _epoch(text, fallback: float | None = None) -> float | None:
    if not text:
        return fallback
    try:
        when = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return fallback
    if when.tzinfo is None:
        from zoneinfo import ZoneInfo

        when = when.replace(tzinfo=ZoneInfo("Asia/Kolkata"))
    return when.timestamp()


def active_shares(day: str) -> dict[str, list[tuple[float, float]]]:
    """{symbol: [(epoch of the stock's order-book read, buyers' share 0..1)]} from that IST day's saved Most active
    scans, oldest first. Never raises (an empty dict when the file cannot be read)."""
    return {sym: [(at, share) for at, share, _, _ in reads] for sym, reads in active_reads(day).items()}


def _opt(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out >= 0 else None


def active_reads(day: str) -> dict[str, list[tuple[float, float, float | None, float | None]]]:
    """{symbol: [(epoch read, buyers' share 0..1, volume vs usual ×, money traded ₹ cr)]} from that IST day's saved Most
    active scans, oldest first (the chart's reading line and the backtest PDF). Never raises."""
    last_id, by_symbol = _ACTIVE_CACHE.get(day, (0, {}))
    import time as _time

    now = _time.monotonic()
    if day in _ACTIVE_CACHE and now - _ACTIVE_CHECKED.get(day, 0.0) < ACTIVE_RECHECK_SEC:
        return by_symbol
    _ACTIVE_CHECKED[day] = now
    try:
        with _lock, _connect() as con:
            found = con.execute(
                "SELECT id, as_of, rows FROM scan_runs WHERE kind = 'active' AND day = ? AND id > ? ORDER BY as_of, id",
                (day, last_id),
            ).fetchall()
    except Exception:  # noqa: BLE001
        log.exception("could not read the saved Most active scans")
        return by_symbol
    if not found and day in _ACTIVE_CACHE:
        return by_symbol
    by_symbol = {k: list(v) for k, v in by_symbol.items()}
    for run in found:
        last_id = max(last_id, int(run["id"]))
        start = _epoch(run["as_of"])
        for row in _unpack(run["rows"]) or []:
            if not isinstance(row, dict) or row.get("buy_share") is None:
                continue
            try:
                share = float(row["buy_share"])
            except (TypeError, ValueError):
                continue
            at = _epoch(row.get("read_at"), start)
            symbol = str(row.get("symbol") or "").upper()
            if not symbol or at is None or not 0.0 <= share <= 1.0:
                continue
            by_symbol.setdefault(symbol, []).append((at, share, _opt(row.get("rvol")), _opt(row.get("value_cr"))))
    for reads in by_symbol.values():
        reads.sort(key=lambda r: r[0])
    _ACTIVE_CACHE[day] = (last_id, by_symbol)
    for old in sorted(_ACTIVE_CACHE)[:-_ACTIVE_CACHE_DAYS]:
        _ACTIVE_CACHE.pop(old, None)
    return by_symbol
