"""Durable minute-by-minute price record, and point-in-time lookup.

**What this is for.** The Groww website shows a stock's current price, but there
is no record of what it was at 11:00 once 11:00 has passed. This table is that
record: for every symbol in the universe, the price at each minute of the
session, kept on disk so it can still be answered in the evening.

**Why a separate table from `research_candles`.** The research store holds
5-minute OHLCV fetched from broker history — excellent for backtesting, but it
arrives after the fact and its resolution is five minutes. This table is written
live, minute by minute, from the tick stream the app is already consuming. The
two coexist: history for research, this for "what was it at 11:00".

**The session open is denormalised onto every row.** Percentage-from-open is the
question actually being asked ("which stocks are going up"), and computing it
per query would mean a self-join against the day's first row for every symbol on
every request. Storing it costs one float per row and makes the movers query a
single scan.

**Nothing is invented.** A minute with no observed price is simply absent, and
`price_at` reports the timestamp it actually found rather than pretending it has
the exact minute asked for. A gap is visible as a gap.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from app.core.market_clock import MARKET_OPEN, IST
from app.research.store import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS intraday_prices (
    symbol      TEXT    NOT NULL,
    ts          INTEGER NOT NULL,   -- epoch seconds, minute-aligned
    source      TEXT    NOT NULL,   -- live | simulated; never mixed in a query
    price       REAL    NOT NULL,   -- close of that minute
    open_price  REAL,               -- that session's opening price, denormalised
    volume      INTEGER,            -- canonical per-bar volume, may be NULL
    recorded_at INTEGER NOT NULL,
    PRIMARY KEY (symbol, ts, source)
);

CREATE INDEX IF NOT EXISTS idx_intraday_ts ON intraday_prices (ts, source);
CREATE INDEX IF NOT EXISTS idx_intraday_symbol_ts ON intraday_prices (symbol, source, ts);
"""


def ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def ist_time_str(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).strftime("%H:%M")


def day_bounds(day: dt.date) -> tuple[int, int]:
    """[start, end) epoch seconds covering one IST calendar day."""
    start = dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST)
    return int(start.timestamp()), int((start + dt.timedelta(days=1)).timestamp())


@dataclass(frozen=True)
class PricePoint:
    symbol: str
    ts: int
    price: float
    open_price: float | None

    @property
    def time_ist(self) -> str:
        return ist_time_str(self.ts)

    @property
    def pct_from_open(self) -> float | None:
        if not self.open_price:
            return None
        return (self.price - self.open_price) / self.open_price * 100


# 09:15 IST plus a few minutes' grace. A first observation later than this means
# the recorder was not running at the open, so its baseline is not the session
# open and must not be presented as one.
SESSION_OPEN_GRACE_MIN = 20


@dataclass(frozen=True)
class Mover:
    symbol: str
    open_price: float
    last_price: float
    last_ts: int
    pct_from_open: float
    high_price: float
    low_price: float
    points: int
    first_ts: int = 0

    @property
    def last_time_ist(self) -> str:
        return ist_time_str(self.last_ts)

    @property
    def first_time_ist(self) -> str:
        return ist_time_str(self.first_ts) if self.first_ts else ""

    @property
    def baseline_is_session_open(self) -> bool:
        """Whether `open_price` really is the day's opening price.

        If the recorder started at 11:00, its first observation is an 11:00
        price — a perfectly good baseline, but calling the result "% from open"
        would be a quiet lie. Callers surface this so the label can match the
        data.
        """
        if not self.first_ts:
            return False
        t = dt.datetime.fromtimestamp(self.first_ts, tz=dt.timezone.utc).astimezone(IST)
        minutes = (t.hour * 60 + t.minute) - (9 * 60 + 15)
        return 0 <= minutes <= SESSION_OPEN_GRACE_MIN


class SnapshotStore:
    def __init__(self, path: Path | str = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- write ----------------------------------------------------------

    def record(self, rows: list[tuple[str, int, str, float, float | None, int | None]]) -> int:
        """Upsert `(symbol, ts, source, price, open_price, volume)` rows.

        Re-recording the same minute replaces it rather than duplicating: the
        recorder re-reads the current (still forming) minute on every pass, so
        the last write for a minute is the most complete one.
        """
        if not rows:
            return 0
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        with self._conn() as conn:
            before = conn.execute("SELECT COUNT(*) FROM intraday_prices").fetchone()[0]
            conn.executemany(
                "INSERT OR REPLACE INTO intraday_prices "
                "(symbol, ts, source, price, open_price, volume, recorded_at) "
                "VALUES (?,?,?,?,?,?,?)",
                [(s, ts, src, p, op, v, now) for s, ts, src, p, op, v in rows],
            )
            after = conn.execute("SELECT COUNT(*) FROM intraday_prices").fetchone()[0]
        return after - before

    # ---- read -----------------------------------------------------------

    def price_at(
        self, symbol: str, when: dt.datetime, source: str = "live", tolerance_min: int = 15
    ) -> PricePoint | None:
        """The recorded price at or before `when`, within the same session.

        Returns the point actually found, so a caller can see it is answering
        with 10:58 when asked for 11:00. Never searches across a day boundary —
        yesterday's close is not an answer to "what was it at 11:00 today".
        """
        target = int(when.timestamp())
        lo, _hi = day_bounds(ist_date(target))
        floor = max(lo, target - tolerance_min * 60)
        with self._conn() as conn:
            row = conn.execute(
                "SELECT symbol, ts, price, open_price FROM intraday_prices "
                "WHERE symbol=? AND source=? AND ts <= ? AND ts >= ? "
                "ORDER BY ts DESC LIMIT 1",
                (symbol, source, target, floor),
            ).fetchone()
        return PricePoint(*row) if row else None

    def session_series(
        self, symbol: str, day: dt.date, source: str = "live"
    ) -> list[PricePoint]:
        lo, hi = day_bounds(day)
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT symbol, ts, price, open_price FROM intraday_prices "
                "WHERE symbol=? AND source=? AND ts >= ? AND ts < ? ORDER BY ts",
                (symbol, source, lo, hi),
            ).fetchall()
        return [PricePoint(*r) for r in rows]

    def movers(
        self,
        day: dt.date,
        source: str = "live",
        as_of: dt.datetime | None = None,
        since: dt.datetime | None = None,
    ) -> list[Mover]:
        """Every symbol's move across a window of the day, ranked best first.

        `as_of` restricts to prices recorded at or before that moment, which is
        what makes an honest "as it stood at 10:30" view possible after the fact.
        `since` moves the other edge, so the move can be measured from 10:00
        rather than from the open.

        The baseline follows the window. With no `since` (or one at or before
        09:15) it is the recorded session open, so the percentage means what
        "from open" normally means. Once a later start is asked for, the
        denormalised open would answer a different question than the one on
        screen, so the first price inside the window becomes the baseline.
        """
        lo, hi = day_bounds(day)
        if as_of is not None:
            hi = min(hi, int(as_of.timestamp()) + 1)
        windowed = False
        if since is not None:
            start = int(since.timestamp())
            if start > int(dt.datetime.combine(day, MARKET_OPEN, tzinfo=IST).timestamp()):
                windowed = True
            lo = max(lo, start)
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT symbol,
                       MIN(open_price)                       AS open_price,
                       MAX(ts)                               AS last_ts,
                       MAX(price)                            AS high_price,
                       MIN(price)                            AS low_price,
                       COUNT(*)                              AS points,
                       MIN(ts)                               AS first_ts
                FROM intraday_prices
                WHERE source=? AND ts >= ? AND ts < ? AND open_price IS NOT NULL
                GROUP BY symbol
                """,
                (source, lo, hi),
            ).fetchall()
            out: list[Mover] = []
            for symbol, open_price, last_ts, high_price, low_price, points, first_ts in rows:
                if windowed:
                    row = conn.execute(
                        "SELECT price FROM intraday_prices WHERE symbol=? AND source=? AND ts=?",
                        (symbol, source, first_ts),
                    ).fetchone()
                    open_price = row[0] if row else None
                if not open_price:
                    continue
                last = conn.execute(
                    "SELECT price FROM intraday_prices WHERE symbol=? AND source=? AND ts=?",
                    (symbol, source, last_ts),
                ).fetchone()
                if not last:
                    continue
                last_price = last[0]
                out.append(
                    Mover(
                        symbol=symbol,
                        open_price=open_price,
                        last_price=last_price,
                        last_ts=last_ts,
                        pct_from_open=(last_price - open_price) / open_price * 100,
                        high_price=high_price,
                        low_price=low_price,
                        points=points,
                        first_ts=first_ts,
                    )
                )
        return sorted(out, key=lambda m: m.pct_from_open, reverse=True)

    def recorded_days(self, source: str = "live") -> list[dt.date]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT DISTINCT ts FROM intraday_prices WHERE source=? ORDER BY ts", (source,)
            ).fetchall()
        return sorted({ist_date(r[0]) for r in rows})

    def stats(self, source: str = "live") -> dict:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT symbol), MIN(ts), MAX(ts) "
                "FROM intraday_prices WHERE source=?",
                (source,),
            ).fetchone()
        total, symbols, lo, hi = row
        return {
            "points": total or 0,
            "symbols": symbols or 0,
            "first": ist_date(lo).isoformat() if lo else None,
            "last": ist_date(hi).isoformat() if hi else None,
            "last_time_ist": ist_time_str(hi) if hi else None,
        }


snapshot_store = SnapshotStore()
