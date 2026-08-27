"""Durable historical candle store for research.

**Why a second store at all.** `services/candle_store.py` is an in-memory dict
capped at MAX_BARS=1500 per symbol/interval/source. It is the right shape for
its job — feed a chart and a scanner from a live tick stream — but it has two
properties that make research impossible: it holds roughly 20 trading days at
5-minute resolution, and it is lost whenever the process restarts. Running with
`--reload`, that means every code edit erases the history. Deep history cannot
live there without changing the live process's memory profile, and would still
not survive a restart.

**Why SQLite rather than Parquet.** Parquet is the usual answer for columnar
research data, but here it loses on the criteria that matter:

* `pyarrow` is not installed; SQLite is in the standard library and already
  this project's database (`sqlite+aiosqlite`), so this adds no dependency.
* Incremental update is the core requirement. Parquet files are immutable, so
  appending a day means rewriting a partition and inventing a dedupe pass.
  SQLite gets exact duplicate prevention from a primary key.
* Volume is small: 50 symbols x 5-minute bars x 12 months is roughly 900k rows,
  which SQLite indexes and serves in milliseconds.
* Windows file locking makes partial Parquet rewrites fragile; SQLite in WAL
  mode is transactional.

pandas is installed, so `read_frame` can hand a DataFrame to any research
workflow that prefers one, without the storage format forcing that choice.

**Timezone.** Timestamps are stored as epoch SECONDS in UTC — identical to
`indicators.OHLCV.ts`, so candles move between the two worlds without
conversion. Every calendar boundary (trading day, session start/end) is
computed in IST via `core.market_clock.IST`. Epoch seconds are unambiguous;
the IST conversion happens only where a human-meaningful day is needed.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.core.market_clock import IST
from app.services.indicators import OHLCV

# Separate file from trading.db on purpose: a research backfill must never be
# able to lock, bloat or corrupt the database the live app writes trades to.
DB_PATH = Path(__file__).resolve().parents[2] / "research_data" / "research.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS research_candles (
    symbol   TEXT    NOT NULL,
    interval TEXT    NOT NULL,
    source   TEXT    NOT NULL,
    ts       INTEGER NOT NULL,
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   INTEGER NOT NULL,
    ingested_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval, source, ts)
);

CREATE TABLE IF NOT EXISTS research_coverage (
    symbol   TEXT NOT NULL,
    interval TEXT NOT NULL,
    source   TEXT NOT NULL,
    day      TEXT NOT NULL,
    candles  INTEGER NOT NULL,
    status   TEXT NOT NULL,
    note     TEXT,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, interval, source, day)
);

CREATE TABLE IF NOT EXISTS research_manifests (
    dataset_id TEXT PRIMARY KEY,
    created_at INTEGER NOT NULL,
    symbols    TEXT NOT NULL,
    interval   TEXT NOT NULL,
    source     TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date   TEXT NOT NULL,
    trading_days  INTEGER NOT NULL,
    total_candles INTEGER NOT NULL,
    validation_status TEXT NOT NULL,
    git_commit TEXT,
    report     TEXT
);

CREATE INDEX IF NOT EXISTS idx_candles_lookup
    ON research_candles (symbol, interval, source, ts);
"""
# research_coverage records which (symbol, interval, day) slices have been
# fetched. This is what makes a backfill resumable and stops the same history
# being downloaded twice: the ingester asks this table what is missing rather
# than inferring it from the candles, which cannot distinguish "not fetched
# yet" from "fetched, genuinely empty" — a holiday, or a stock that had not
# listed yet. Both look like an absence of rows.


def ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def day_bounds(day: dt.date) -> tuple[int, int]:
    """[start, end) epoch seconds covering one IST calendar day."""
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST)
    return int(start.timestamp()), int((start + dt.timedelta(days=1)).timestamp())


@dataclass
class Candle:
    """A research candle: the live OHLCV fields plus the provenance research
    needs to keep two data sources from ever being mixed."""

    symbol: str
    interval: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str

    def to_ohlcv(self) -> OHLCV:
        return OHLCV(self.ts, self.open, self.high, self.low, self.close, self.volume)


class ResearchStore:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            # WAL keeps a long backfill from blocking readers, and survives an
            # interrupted write without corrupting the file.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- writes ---------------------------------------------------------

    def upsert(self, candles: list[Candle]) -> int:
        """Insert candles, replacing any row with the same key.

        Returns how many rows the table GREW by, not how many were passed in,
        so a caller can tell genuinely new history from a re-fetch of data it
        already had.
        """
        if not candles:
            return 0
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        rows = [
            (c.symbol, c.interval, c.source, c.ts, c.open, c.high, c.low, c.close, c.volume, now)
            for c in candles
        ]
        with self._conn() as conn:
            before = conn.execute("SELECT COUNT(*) FROM research_candles").fetchone()[0]
            conn.executemany(
                "INSERT OR REPLACE INTO research_candles "
                "(symbol, interval, source, ts, open, high, low, close, volume, ingested_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            after = conn.execute("SELECT COUNT(*) FROM research_candles").fetchone()[0]
        return after - before

    def mark_coverage(
        self,
        symbol: str,
        interval: str,
        source: str,
        day: dt.date,
        candles: int,
        status: str,
        note: str | None = None,
    ) -> None:
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_coverage "
                "(symbol, interval, source, day, candles, status, note, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (symbol, interval, source, day.isoformat(), candles, status, note, now),
            )

    # ---- reads ----------------------------------------------------------

    def covered_days(self, symbol: str, interval: str, source: str) -> set[dt.date]:
        """Days already attempted. FAILED is excluded so a retry re-fetches."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT day FROM research_coverage "
                "WHERE symbol=? AND interval=? AND source=? AND status != 'FAILED'",
                (symbol, interval, source),
            ).fetchall()
        return {dt.date.fromisoformat(r[0]) for r in rows}

    def coverage_rows(self, interval: str, source: str) -> list[tuple]:
        with self._conn() as conn:
            return conn.execute(
                "SELECT symbol, day, candles, status, note FROM research_coverage "
                "WHERE interval=? AND source=? ORDER BY symbol, day",
                (interval, source),
            ).fetchall()

    def read(
        self,
        symbol: str,
        interval: str,
        source: str = "live",
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> list[OHLCV]:
        """Candles as the live `OHLCV` type, so every existing research module
        — indicators, entry_diagnostics, hypothesis_lab, backtester — consumes
        this store with no adapter and no change to those modules."""
        sql = (
            "SELECT ts, open, high, low, close, volume FROM research_candles "
            "WHERE symbol=? AND interval=? AND source=?"
        )
        args: list = [symbol, interval, source]
        if start:
            sql += " AND ts >= ?"
            args.append(day_bounds(start)[0])
        if end:
            sql += " AND ts < ?"
            args.append(day_bounds(end)[1])
        sql += " ORDER BY ts"
        with self._conn() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [OHLCV(r[0], r[1], r[2], r[3], r[4], int(r[5])) for r in rows]

    def read_many(
        self,
        symbols: list[str],
        interval: str,
        source: str = "live",
        start: dt.date | None = None,
        end: dt.date | None = None,
    ) -> dict[str, list[OHLCV]]:
        out = {s: self.read(s, interval, source, start, end) for s in symbols}
        return {s: b for s, b in out.items() if b}

    def read_frame(self, symbol: str, interval: str, source: str = "live"):
        """pandas view, for research that prefers a DataFrame."""
        import pandas as pd

        with self._conn() as conn:
            df = pd.read_sql_query(
                "SELECT ts, open, high, low, close, volume FROM research_candles "
                "WHERE symbol=? AND interval=? AND source=? ORDER BY ts",
                conn,
                params=(symbol, interval, source),
            )
        if not df.empty:
            df["datetime_ist"] = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
        return df

    def symbols(self, interval: str, source: str = "live") -> list[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM research_candles WHERE interval=? AND source=? ORDER BY symbol",
                (interval, source),
            ).fetchall()
        return [r[0] for r in rows]

    def stats(self, interval: str, source: str = "live") -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*), MIN(ts), MAX(ts), COUNT(DISTINCT symbol) "
                "FROM research_candles WHERE interval=? AND source=?",
                (interval, source),
            ).fetchone()
        total, lo, hi, syms = row
        return {
            "total_candles": total,
            "symbols": syms,
            "start": ist_date(lo).isoformat() if lo else None,
            "end": ist_date(hi).isoformat() if hi else None,
        }

    # ---- manifests ------------------------------------------------------

    def save_manifest(self, manifest: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO research_manifests "
                "(dataset_id, created_at, symbols, interval, source, start_date, end_date, "
                " trading_days, total_candles, validation_status, git_commit, report) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    manifest["dataset_id"],
                    manifest["created_at"],
                    json.dumps(manifest["symbols"]),
                    manifest["interval"],
                    manifest["source"],
                    manifest["start_date"],
                    manifest["end_date"],
                    manifest["trading_days"],
                    manifest["total_candles"],
                    manifest["validation_status"],
                    manifest.get("git_commit"),
                    json.dumps(manifest.get("report", {})),
                ),
            )

    def get_manifest(self, dataset_id: str) -> dict | None:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM research_manifests WHERE dataset_id=?", (dataset_id,))
            cols = [d[0] for d in cur.description]
            r = cur.fetchone()
        if r is None:
            return None
        m = dict(zip(cols, r))
        m["symbols"] = json.loads(m["symbols"])
        m["report"] = json.loads(m["report"] or "{}")
        return m

    def list_manifests(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT dataset_id FROM research_manifests ORDER BY created_at DESC"
            ).fetchall()
        return [self.get_manifest(r[0]) for r in rows]


store = ResearchStore()
