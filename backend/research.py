"""Research desk: a second SMA bot, paper only, next to the live one.

The live desk has one bot and one PAPER/LIVE switch. Testing an idea on it
during market hours meant switching the live bot to PAPER. The research desk
is a separate StrategyEngine that runs alongside it:

* its settings and Trade list are their own BotConfig row
  (``RESEARCH_CONFIG_ID``), first copied from the live settings with an empty
  Trade list; saving them never touches the live bot;
* it reads the same live quotes and candles through the live desk's Groww
  client (``refresh`` only, so the two share the quote cache), and fills
  locally through ``ResearchBroker``, which has no code path to the Groww
  order API at all, whatever mode the live desk is in;
* its trades are tagged ``RESEARCH`` (ids Q-1, Q-2, ...), with their own book,
  P&L, daily loss limit and trade count. The live book, P&L and caps never
  see them, and the live panic square-off does not touch them;
* it sends no Telegram/WhatsApp alerts;
* it is capped at ``MAX_RESEARCH_SYMBOLS`` armed stocks and ticks less often
  than the live bot, so it cannot crowd the live bot's quotes or CPU.

Market hours apply as they do for PAPER: no entry outside the session,
before 09:20 or after the entry cut-off, and square-off at the set time.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

import pandas as pd

from database import session_factory
from groww_client import market_is_open
from models import BotConfig
from replay import LocalFills
from strategy_engine import StrategyEngine, trade_names

RESEARCH_CONFIG_ID = 2
BOOK = "RESEARCH"
# Armed stocks on the research desk. Each one is a Groww quote the live bot
# shares the rate limit with.
MAX_RESEARCH_SYMBOLS = 10
# Seconds between research ticks. The live bot ticks every 0.5 s; the research
# desk yields to it. Slower again while it has nothing to do.
TICK_SECONDS = 1.0
IDLE_SECONDS = 3.0
CLOSED_SECONDS = 5.0

Quote = Callable[[str], Awaitable[tuple[float, pd.DataFrame, str]]]


class ResearchBroker(LocalFills):
    """Live quotes from the desk's client, local fills. No Groww order path.

    Only the client's bound ``refresh`` is kept, never the client itself, so
    nothing here can reach ``place_entry``/``place_sl`` of a real session.
    """

    ORDER_PREFIX = "RESEARCH"

    def __init__(self, quote: Quote):
        super().__init__()
        self._quote = quote
        self.data_source = "SIMULATOR"

    async def refresh(self, symbol: str) -> tuple[float, pd.DataFrame, str]:
        ltp, frame, source = await self._quote(symbol)
        self.data_source = source
        return ltp, frame, source


def ensure_research_config() -> None:
    """Create the research settings row from the live one, with no stock armed."""
    with session_factory()() as db:
        if db.get(BotConfig, RESEARCH_CONFIG_ID) is not None:
            return
        live = db.get(BotConfig, 1)
        if live is None:
            raise RuntimeError("BotConfig missing — init_db() was not called")
        data = {
            col.name: getattr(live, col.name)
            for col in BotConfig.__table__.columns
            if col.name != "id"
        }
        data.update(id=RESEARCH_CONFIG_ID, trading_mode="PAPER", trade_symbols="")
        db.add(BotConfig(**data))
        db.commit()


class ResearchEngine(StrategyEngine):
    """The SMA bot on today's live market, paper only, with its own book."""

    config_id = RESEARCH_CONFIG_ID
    uses_wallet = False  # practice money of its own, never the bots' wallet

    def __init__(self, quote: Quote):
        super().__init__(broker=ResearchBroker(quote))

    def _alert(self, message: str) -> None:  # noqa: ARG002
        return None

    def _restores(self, mode: str) -> bool:
        return mode == BOOK

    def load_config(self) -> BotConfig:
        ensure_research_config()
        row = super().load_config()
        # Detached: the book tag never reaches the stored row.
        row.trading_mode = BOOK
        names = trade_names(row)
        if len(names) > MAX_RESEARCH_SYMBOLS:
            row.trade_symbols = ",".join(names[:MAX_RESEARCH_SYMBOLS])
        self._cfg_cache = row
        return row

    def _refresh_tick_sizes(self) -> None:
        # The live engine loads Groww's tick sizes; the table is shared.
        return None

    async def run(self) -> None:
        while not self._stop:
            try:
                await self.tick(self._now())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            if not market_is_open(self._now()):
                pause = CLOSED_SECONDS
            elif self.status != "RUNNING" and not self.positions:
                pause = IDLE_SECONDS
            else:
                pause = TICK_SECONDS
            await self._sleep(pause)

    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["desk"] = "research"
        snap["max_research_symbols"] = MAX_RESEARCH_SYMBOLS
        return snap
