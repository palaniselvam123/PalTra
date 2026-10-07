"""Bots 2-4: more SMA bots next to the main desk (bot 1), each able to trade LIVE.

Each bot is a full ``StrategyEngine`` with

* its own settings row (``BotConfig`` id ``config_id_for(bot)``: 3, 4, 5;
  id 2 is the research desk), first copied from bot 1 with an empty Trade
  list and PAPER, and its own name (``bot_name``);
* its own trade book: every row it writes carries ``TradeLog.bot`` = its
  number, so its trades, P&L, daily loss limit, trade cap and panic are its
  own (ids N2-1, P3-4, ...);
* its own order client (``BotGrowwClient``): its own PAPER/LIVE mode, paper
  fills and order ids, so one bot switching mode can never change where
  another bot's order goes. Quotes and candles come from the main desk's
  client, so the bots share one quote cache and one per-second price call.

One rule keeps real money safe: **a stock belongs to at most one LIVE bot.**
Groww keeps one net MIS position per stock, so two LIVE bots on one stock
would net each other out and each one's exchange stop would act on the other's
shares. ``live_conflict`` is checked when a stock is armed on a LIVE bot, when
a bot is switched to LIVE, and again just before every LIVE entry.
"""
from __future__ import annotations

import asyncio

import groww_client
from database import session_factory
from groww_client import GrowwClient, market_is_open
from models import BotConfig
from strategy_engine import StrategyEngine, trade_names

#: The extra bots. Bot 1 is the main desk's engine.
EXTRA_BOTS = (2, 3, 4)
ALL_BOTS = (1, *EXTRA_BOTS)


def config_id_for(bot: int) -> int:
    """Settings row of a bot: 1 for the main desk, 3-5 for bots 2-4 (2 is research)."""
    return 1 if int(bot) == 1 else int(bot) + 1


def default_name(bot: int) -> str:
    return f"Bot {int(bot)}"


class BotGrowwClient(GrowwClient):
    """A bot's own order client. Quotes come from the shared desk client.

    Orders, stops, order reads and Groww positions use this client's own mode
    and its own SDK session (the same saved Groww login), so a bot in PAPER
    never touches the SDK and a bot in LIVE never depends on another bot's mode.
    """

    def __init__(self, quotes: GrowwClient, mode: str = "PAPER"):
        keep = groww_client._sma_broker
        super().__init__(mode=mode)
        # The module keeps the desk client for re-login; this one is not it.
        groww_client._sma_broker = keep
        self._shared = quotes

    async def refresh(self, symbol: str):
        ltp, frame, source = await self._shared.refresh(symbol)
        self._ltp = ltp
        self.data_source = source
        return ltp, frame, source

    async def refresh_ltps(self, symbols) -> dict[str, float]:
        return await self._shared.refresh_ltps(symbols)

    def anchor_price(self, symbol: str, price: float | None) -> None:
        self._shared.anchor_price(symbol, price)


def ensure_bot_config(bot: int) -> None:
    """Create a bot's settings row from bot 1's, PAPER, with no stock armed."""
    cid = config_id_for(bot)
    with session_factory()() as db:
        if db.get(BotConfig, cid) is not None:
            return
        main = db.get(BotConfig, 1)
        if main is None:
            raise RuntimeError("BotConfig missing — init_db() was not called")
        data = {col.name: getattr(main, col.name) for col in BotConfig.__table__.columns if col.name != "id"}
        data.update(id=cid, trading_mode="PAPER", trade_symbols="", bot_name=default_name(bot), stock_settings="{}")
        db.add(BotConfig(**data))
        db.commit()


class BotEngine(StrategyEngine):
    """One of bots 2-4: the SMA bot with its own settings, book and order client."""

    def __init__(self, bot: int, quotes: GrowwClient):
        if int(bot) not in EXTRA_BOTS:
            raise ValueError(f"Bot {bot} is not one of {EXTRA_BOTS}")
        self.bot_id = int(bot)
        self.config_id = config_id_for(bot)
        super().__init__(broker=BotGrowwClient(quotes))

    def load_config(self) -> BotConfig:
        ensure_bot_config(self.bot_id)
        return super().load_config()

    def _refresh_tick_sizes(self) -> None:
        # Bot 1 loads Groww's tick sizes; the table is shared.
        return None

    async def run(self) -> None:
        """Bot 1's cadence while it has work; slower while it has nothing armed or held."""
        while not self._stop:
            try:
                await self.tick(self._now())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            cfg = self._cfg_cache
            idle = not self.positions and not (trade_names(cfg) if cfg is not None else [])
            if not market_is_open(self._now()) and self.status != "RUNNING":
                pause = 5.0
            elif idle:
                pause = 3.0
            else:
                pause = 0.5
            await self._sleep(pause)

    def _alert(self, message: str) -> None:
        cfg = self._cfg_cache
        name = (getattr(cfg, "bot_name", None) or default_name(self.bot_id)) if cfg is not None else default_name(self.bot_id)
        super()._alert(f"[{name}] {message}")



# ---- the one-LIVE-bot-per-stock rule ---------------------------------------------------------

_ENGINES: dict[int, StrategyEngine] = {}


def register(engine: StrategyEngine) -> None:
    """Called at boot for bot 1 and each extra bot, so the rule can see them all."""
    _ENGINES[int(engine.bot_id)] = engine
    engine.live_guard = live_conflict  # type: ignore[attr-defined]


def engines() -> dict[int, StrategyEngine]:
    return dict(_ENGINES)


def _row(bot: int) -> BotConfig | None:
    with session_factory()() as db:
        row = db.get(BotConfig, config_id_for(bot))
        if row is not None:
            db.expunge(row)
        return row


def bot_name(bot: int) -> str:
    row = _row(bot)
    return (getattr(row, "bot_name", None) or default_name(bot)) if row is not None else default_name(bot)


def live_claims(bot: int) -> set[str]:
    """Stocks a bot claims while LIVE: its armed stocks and its open LIVE positions."""
    row = _row(bot)
    if row is None or (row.trading_mode or "PAPER").upper() != "LIVE":
        return set()
    names = set(trade_names(row))
    engine = _ENGINES.get(int(bot))
    if engine is not None:
        names |= {s for s, p in engine.positions.items() if (p.mode or "").upper() == "LIVE"}
    return names


def live_conflict(bot: int, symbols) -> str | None:
    """Why these stocks cannot be traded LIVE by `bot`, or None.

    Another LIVE bot that has the stock armed or holds it on Groww owns it.
    """
    wanted = {(s or "").upper() for s in symbols if s}
    for other in ALL_BOTS:
        if other == int(bot):
            continue
        clash = sorted(wanted & live_claims(other))
        if clash:
            return (
                f"{', '.join(clash)} {'is' if len(clash) == 1 else 'are'} already traded LIVE by "
                f"{bot_name(other)}. A stock can belong to only one LIVE bot: Groww keeps one position per stock."
            )
    return None
