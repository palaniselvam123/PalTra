"""Background recorder: persists the live minute prices the app already sees.

**Why this exists.** The tick feed is already polling every symbol every two
seconds and `candle_store` is already folding those ticks into 1-minute bars.
All of that is in memory, capped at 1500 bars, and lost on restart. This task
copies those minute bars onto disk while the session runs, so the morning can
still be examined in the evening.

**It costs no extra broker calls.** The recorder reads `candle_store`, never the
API. Adding a second polling loop against Groww would risk the rate limit that
the live quote loop depends on, for data the app already has.

**It only records what it observed.** A symbol with no ticks yet produces no
row. The recorder never carries a price forward to fill a quiet minute, because
a fabricated price is indistinguishable downstream from an observed one — the
same reasoning that made unknown volume NULL rather than zero.

**Source is part of the key.** Simulated and live prices are different worlds,
and a movers table that blended them would rank synthetic moves against real
ones. Every row records which feed produced it and every query filters on it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.core.market_clock import IST, ist_now, session_state
from app.research.snapshots import ist_date, snapshot_store
from app.services.candle_store import candle_store
from app.services.market_data import market_data

log = logging.getLogger("recorder")

RECORD_INTERVAL_SEC = 30.0      # how often to flush; bars are 1-minute so this is ample
INTERVAL = "1m"


class MarketRecorder:
    """Copies candle_store's 1-minute bars into the durable snapshot table."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self.running = False
        self.last_run_at: dt.datetime | None = None
        self.last_written = 0
        self.total_written = 0
        self.last_error: str | None = None

    # ---- lifecycle -------------------------------------------------------

    async def start(self) -> dict:
        if self.running:
            return self.status()
        self.running = True
        self._task = asyncio.create_task(self._loop())
        log.info("recorder.start interval=%ss", RECORD_INTERVAL_SEC)
        return self.status()

    async def stop(self) -> dict:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        log.info("recorder.stop")
        return self.status()

    def status(self) -> dict:
        return {
            "running": self.running,
            "interval_sec": RECORD_INTERVAL_SEC,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "last_written": self.last_written,
            "total_written": self.total_written,
            "last_error": self.last_error,
            "session": session_state(),
            "source": market_data.source.value,
            "store": snapshot_store.stats(market_data.source.value),
        }

    # ---- the loop --------------------------------------------------------

    async def _loop(self) -> None:
        while self.running:
            try:
                if session_state() == "OPEN":
                    self.last_written = self.record_once()
                    self.total_written += self.last_written
                    self.last_run_at = ist_now()
                    self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # A recorder that dies silently is worse than one that says so:
                # the gap only becomes visible hours later when the data is wanted.
                self.last_error = f"{type(exc).__name__}: {exc}"
                log.warning("recorder.error %s", self.last_error)
            await asyncio.sleep(RECORD_INTERVAL_SEC)

    def record_once(self) -> int:
        """One pass over every symbol currently in the candle store."""
        source = market_data.source.value
        today = ist_date(int(ist_now().timestamp()))
        rows: list[tuple[str, int, str, float, float | None, int | None]] = []

        for symbol in sorted(market_data.symbols):
            bars = candle_store.get(symbol, INTERVAL, source, limit=500)
            todays = [b for b in bars if ist_date(b.ts) == today]
            if not todays:
                continue
            session_open = todays[0].open
            for b in todays:
                rows.append((symbol, b.ts, source, b.close, session_open, b.volume))

        return snapshot_store.record(rows)


market_recorder = MarketRecorder()
