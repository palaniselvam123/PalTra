"""Market data layer: where prices come from.

Two sources, switchable at runtime:

* `simulated` — the synthetic feed. No credentials, works any hour.
* `live`      — real NSE quotes polled from Groww via an authenticated session.

Switching to `live` changes ONLY the prices. Execution stays virtual: fills
are always simulated by the paper engine and no order is ever sent to the
broker. That combination — real prices, virtual money — is forward paper
trading, and it is the whole point of this mode.

Live quotes are polled rather than streamed. Groww enforces rate limits, so
the poll interval is deliberately conservative and one batch call covers the
whole universe.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass
from enum import Enum

from app.core.market_clock import ist_now, is_market_open, session_state
from app.services.tick_feed import SimulatedTickFeed, Tick
from app.strategies.scanner import NIFTY50_UNIVERSE

# How long quotes may go unchanged before we call the feed stale. Outside
# market hours every quote is frozen at last close, which is expected — this
# exists to catch a feed that has silently died DURING the session.
STALE_AFTER_SEC = 90


class DataSource(str, Enum):
    SIMULATED = "simulated"
    LIVE = "live"


@dataclass
class FeedHealth:
    source: str
    session: str
    market_open: bool
    connected: bool
    last_tick_at: dt.datetime | None
    last_change_at: dt.datetime | None
    stale: bool
    error: str | None
    symbols: int
    poll_interval_sec: float


class LiveGrowwFeed:
    """Polls Groww for real quotes and yields them as ticks.

    Depth (bid/ask) costs one call per symbol, so it is fetched on a slower
    cadence than LTP; between refreshes the last known spread is reused. The
    spread guard therefore acts on depth that is at most `depth_every` polls
    old — acceptable for a paper engine, and flagged in the README.
    """

    def __init__(self, client, symbols: list[str], poll_interval_sec: float = 2.0, depth_every: int = 10):
        self.client = client
        self.symbols = symbols
        self.poll_interval_sec = poll_interval_sec
        self.depth_every = depth_every
        self._spreads: dict[str, tuple[float, float]] = {}   # symbol -> (bid_offset, ask_offset)
        self._volumes: dict[str, int] = {}
        self._poll_count = 0
        self.last_error: str | None = None

    async def _refresh_depth(self) -> None:
        for symbol in self.symbols:
            try:
                quote = await self.client.get_full_quote(symbol)
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
                continue
            if quote.ltp > 0:
                self._spreads[symbol] = (quote.ltp - quote.bid, quote.ask - quote.ltp)
            if quote.volume:
                self._volumes[symbol] = quote.volume
            await asyncio.sleep(0.05)  # be gentle with rate limits

    async def stream(self):
        while True:
            try:
                if self._poll_count % self.depth_every == 0:
                    await self._refresh_depth()
                self._poll_count += 1

                ltps = await self.client.get_ltp_batch(self.symbols)
                self.last_error = None

                for symbol, ltp in ltps.items():
                    if ltp <= 0:
                        continue
                    bid_off, ask_off = self._spreads.get(symbol, (ltp * 0.0002, ltp * 0.0002))
                    yield Tick(
                        symbol=symbol,
                        ltp=round(ltp, 2),
                        bid=round(ltp - bid_off, 2),
                        ask=round(ltp + ask_off, 2),
                        volume=self._volumes.get(symbol, 0),
                    )
            except Exception as exc:  # noqa: BLE001
                # Never let a transient broker error kill the feed task.
                self.last_error = str(exc)
                await asyncio.sleep(5)

            await asyncio.sleep(self.poll_interval_sec)


class MarketDataManager:
    def __init__(self):
        self.source = DataSource.SIMULATED
        self.generation = 0          # bumped on switch so the tick loop rebuilds
        # Was NIFTY50_UNIVERSE[:10] — half the configured universe was
        # silently discarded, so ASIANPAINT and friends never appeared
        # anywhere in the UI despite being listed in the constant.
        self.symbols = list(NIFTY50_UNIVERSE)
        self._simulated = SimulatedTickFeed(self.symbols)
        self._live: LiveGrowwFeed | None = None
        self.error: str | None = None

        self.last_tick_at: dt.datetime | None = None
        self.last_change_at: dt.datetime | None = None
        self._last_prices: dict[str, float] = {}

    def add_symbol(self, symbol: str) -> bool:
        """Adds a symbol to live streaming beyond the bot's fixed 20-name
        universe — the mechanism behind watchlist search-and-add.

        `self.symbols` is the SAME list object both `_simulated` and `_live`
        were constructed with (not a copy), so appending here is visible to
        whichever feed is active without rebuilding it. The simulated feed
        additionally needs a lazily-created price state; the live feed needs
        nothing extra; its per-symbol dicts already default missing entries.
        """
        symbol = symbol.upper()
        if symbol in self.symbols:
            return False
        self.symbols.append(symbol)
        self._simulated.ensure_symbol(symbol)
        return True

    def remove_symbol(self, symbol: str) -> bool:
        symbol = symbol.upper()
        if symbol not in self.symbols:
            return False
        if symbol in NIFTY50_UNIVERSE:
            # The bot's strategy universe must always stream — bracket
            # enforcement and the ORB scanner depend on every one of these 20
            # ticking regardless of what the user has added on top.
            return False
        self.symbols.remove(symbol)
        self._simulated.remove_symbol(symbol)
        return True

    def active_feed(self):
        if self.source is DataSource.LIVE and self._live is not None:
            return self._live
        return self._simulated

    def note_tick(self, tick: Tick) -> None:
        now = ist_now()
        self.last_tick_at = now
        if self._last_prices.get(tick.symbol) != tick.ltp:
            self._last_prices[tick.symbol] = tick.ltp
            self.last_change_at = now

    async def use_simulated(self) -> None:
        self.source = DataSource.SIMULATED
        self._live = None
        self.error = None
        self.generation += 1

    async def use_live(self, client, poll_interval_sec: float = 2.0) -> None:
        """Verify the session can actually fetch quotes before switching — a
        feed that fails after the switch would silently freeze the bot.
        """
        probe = await client.get_ltp_batch(self.symbols[:2])
        if not probe:
            raise RuntimeError(
                "Groww returned no prices for a test batch. Check that your API key has "
                "market-data access and that the symbols are valid NSE cash symbols."
            )
        self._live = LiveGrowwFeed(client, self.symbols, poll_interval_sec)
        self.source = DataSource.LIVE
        self.error = None
        self.generation += 1

    def health(self) -> FeedHealth:
        now = ist_now()
        stale = False
        if self.source is DataSource.LIVE and is_market_open(now) and self.last_change_at:
            stale = (now - self.last_change_at).total_seconds() > STALE_AFTER_SEC

        live_error = self._live.last_error if self._live else None
        return FeedHealth(
            source=self.source.value,
            session=session_state(now),
            market_open=is_market_open(now),
            connected=self.last_tick_at is not None
            and (now - self.last_tick_at).total_seconds() < 30,
            last_tick_at=self.last_tick_at,
            last_change_at=self.last_change_at,
            stale=stale,
            error=self.error or live_error,
            symbols=len(self.symbols),
            poll_interval_sec=self._live.poll_interval_sec if self._live else 1.0,
        )


market_data = MarketDataManager()
