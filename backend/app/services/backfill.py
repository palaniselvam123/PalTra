"""Historical candle backfill, shared by the chart and the scanner.

Both need "make sure this symbol/interval has real history before you draw or
evaluate it", and both must respect the same rule: history for the LIVE feed
comes from Groww, while the simulated feed genuinely has no past before the
app started. Keeping one implementation means the scanner cannot accidentally
evaluate a crossover against candles the chart would never show.
"""
from __future__ import annotations

from app.services.candle_store import INTERVALS, candle_store
from app.services.indicators import OHLCV
from app.services.market_data import DataSource, market_data
from app.services.volume_contract import UNKNOWN, derive_rows

# How much history to request per interval. Longer bars need a wider window —
# asking for 5 days of daily candles would return five bars.
BACKFILL_DAYS = {"1m": 3, "5m": 10, "15m": 25, "1h": 90, "1d": 400}


async def ensure_backfilled(symbol: str, interval: str) -> str | None:
    """Fills the store from Groww when possible.

    Returns None on success, or a human-readable note explaining why history
    is thin — so callers can surface the reason instead of leaving the user
    staring at an unexplained empty chart or a scanner that never fires.
    """
    from app.api.routes_auth import _active_clients  # local: avoids an import cycle

    source = market_data.source.value
    if not candle_store.needs_backfill(symbol, interval, source):
        return None
    if market_data.source is not DataSource.LIVE:
        return (
            "Simulated feed — no real history exists. Candles build up from ticks as they arrive. "
            "Switch to LIVE NSE for historical data."
        )

    client = _active_clients.get("groww")
    if client is None:
        return "No Groww session — connect live data to backfill real history."

    try:
        rows = await client.get_candles(symbol, INTERVALS[interval] // 60, BACKFILL_DAYS.get(interval, 10))
    except Exception as exc:  # noqa: BLE001
        # Rate limits and transient broker errors land here. Deliberately not
        # cached as "backfilled", so the next call retries rather than
        # permanently treating this symbol as having no history.
        return f"Historical backfill unavailable for {symbol}: {exc}"

    if not rows:
        return f"Groww returned no historical candles for {symbol} at {interval}."

    # Groww returns volume cumulative from the session open. Seeding it raw put
    # a cumulative counter in the same field the tick path fills with per-bar
    # volume, so one chart column carried two different quantities. Derive to
    # the canonical contract first, and keep the raw figures alongside.
    derived = derive_rows(rows)
    candle_store.seed(
        symbol,
        interval,
        source,
        [
            OHLCV(ts=ts, open=o, high=h, low=lo, close=c, volume=bar_vol or 0)
            for ts, o, h, lo, c, bar_vol, _raw, _q in derived
        ],
        quality={row[0]: row[7] for row in derived},
        raw_cumulative={row[0]: row[6] for row in derived},
    )
    unknown = sum(1 for row in derived if row[7] == UNKNOWN)
    if unknown:
        return (
            f"{symbol} {interval}: {unknown} bar(s) have unrecoverable volume "
            "(broker counter reset near the close); their volume reads 0 and must not "
            "be treated as a quiet bar."
        )
    return None
