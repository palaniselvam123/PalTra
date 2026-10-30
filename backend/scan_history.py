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

KINDS = ("cross", "chop", "active")
# The terminal's History tab lists these; "active" (the desk's Most active scan) has its own history view.
SCAN_KINDS = ("cross", "chop")
KIND_LABEL = {"cross": "Cross scan", "chop": "Trend quality", "active": "Most active"}
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
        for k in ("sma_fast", "sma_slow", "quiet_min", "market_open", "universe", "scanned")
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


def save_check(run_id: int, result: dict) -> None:
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    with _lock, _connect() as con:
        con.execute(
            "INSERT OR REPLACE INTO scan_checks (run_id, checked_at, result) VALUES (?, ?, ?)",
            (run_id, now, _pack(result)),
        )
