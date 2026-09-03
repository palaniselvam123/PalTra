"""Fetch a missing day from the broker, on demand, and keep it.

Saying "nothing was recorded at 11:00" is the right answer only when the price
is genuinely unobtainable. It usually is not: Groww serves intraday candles for
months back, so a miss should trigger a fetch rather than a shrug.

**What is fetched is kept.** The result goes into the research store through the
same ingestion path the bulk backfill uses, so the second question about that
day is answered from disk. A cache that only lived in memory would re-request
the same day every time the page refreshed.

**One day, one symbol, at a time.** The bulk backfill exists for whole
universes; this is the interactive path, where someone is waiting. Fetching
their symbol's day is one request against a rate limit the live quote loop also
depends on.

**Guarded rather than eager.** A fetch is skipped when it cannot help or would
mislead:

* no broker session — the honest answer is that the app is not connected;
* a future date, or a weekend, where no data exists to fetch;
* a question about *today* while the feed is simulated, since answering it with
  real NSE data would silently swap one world for the other.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from app.core.market_clock import IST
from app.research import ingestion
from app.research.store import store as research_store

log = logging.getLogger("price_fetch")

# 1-minute bars, so an interactive "what was it at 11:00" is answered at the
# resolution the question implies rather than rounded to a 5-minute bucket.
FETCH_INTERVAL = "1m"

# One in-flight fetch per (symbol, day, interval). Without this, a page whose
# panels all miss the same day would fire several identical requests at once.
_inflight: dict[tuple[str, str, str], asyncio.Task] = {}


def broker_client():
    """The live Groww session, or None when the app is not connected."""
    try:
        from app.api.routes_auth import _active_clients

        return _active_clients.get("groww")
    except Exception:  # noqa: BLE001
        return None


def already_stored(symbol: str, day: dt.date, interval: str = FETCH_INTERVAL) -> bool:
    return bool(research_store.read(symbol.upper(), interval, "live", start=day, end=day))


def fetch_blocked_reason(symbol: str, day: dt.date, source: str = "live") -> str | None:
    """Why a fetch would not help, or None when it is worth trying."""
    today = dt.datetime.now(IST).date()
    if day > today:
        return f"{day.isoformat()} is in the future."
    if day.weekday() >= 5:
        return f"{day.isoformat()} is a weekend — the market was closed."
    if source != "live" and day >= today:
        return (
            "The feed is on SIMULATED, so today's question is about synthetic prices. "
            "Real broker history is deliberately not used to answer it."
        )
    if broker_client() is None:
        return (
            "Not connected to Groww, so the price cannot be fetched. Save your API "
            "credentials in Settings and run the login step, then try again."
        )
    return None


async def ensure_day(
    symbol: str, day: dt.date, source: str = "live", interval: str = FETCH_INTERVAL
) -> dict:
    """Make sure a symbol's day is on disk, fetching it if it is not.

    Returns what happened, so a caller can tell "already had it" from "fetched
    it just now" from "could not".
    """
    symbol = symbol.upper()
    if already_stored(symbol, day, interval):
        return {"fetched": False, "cached": True, "symbol": symbol, "day": day.isoformat()}

    blocked = fetch_blocked_reason(symbol, day, source)
    if blocked:
        return {"fetched": False, "cached": False, "symbol": symbol,
                "day": day.isoformat(), "reason": blocked}

    key = (symbol, day.isoformat(), interval)
    task = _inflight.get(key)
    if task is None:
        task = asyncio.create_task(_do_fetch(symbol, day, interval))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))

    try:
        report = await task
    except Exception as exc:  # noqa: BLE001
        log.warning("price_fetch.failed %s %s: %s", symbol, day, exc)
        return {"fetched": False, "cached": False, "symbol": symbol, "day": day.isoformat(),
                "reason": f"Fetch from Groww failed: {exc}"}

    if report.candles_fetched == 0:
        return {"fetched": False, "cached": False, "symbol": symbol, "day": day.isoformat(),
                "reason": (
                    f"Groww returned no {interval} candles for {symbol} on {day.isoformat()} — "
                    "the date is likely outside the history it retains, or the symbol did not "
                    "trade that day."
                )}

    return {"fetched": True, "cached": False, "symbol": symbol, "day": day.isoformat(),
            "candles": report.candles_fetched, "interval": interval}


async def _do_fetch(symbol: str, day: dt.date, interval: str):
    """The actual request, through the same ingestion path the backfill uses."""
    log.info("price_fetch.start symbol=%s day=%s interval=%s", symbol, day, interval)
    return await ingestion.backfill(
        [symbol], interval, day, day, broker_client(), "live", research_store
    )
