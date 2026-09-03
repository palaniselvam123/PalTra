from __future__ import annotations

import asyncio
import datetime as dt
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import state
from app.api import (
    routes_ai,
    routes_auth,
    routes_bot,
    routes_chat,
    routes_chart,
    routes_instruments,
    routes_manual,
    routes_marketdata,
    routes_watchlist,
    routes_orders,
    routes_reports,
    routes_scanner,
    routes_movers,
    routes_research,
    routes_scanner_engine,
    routes_ws,
)
from app.core.config import get_settings
from app.core.market_clock import ist_now
from app.models.database import init_db
from app.services.broadcaster import broadcaster
from app.services.candle_store import candle_store
from app.services import manual_desk
from app.services.market_data import market_data
from app.services.scanner_worker import scanner_worker
from app.services.strategy_runner import strategy_runner
from app.services.trade_ledger import close_and_settle, restore_open_positions
from app.services import watchlist

settings = get_settings()


async def _tick_feed_loop() -> None:
    """Supervises whichever market data source is active. A source switch
    bumps `market_data.generation`, which breaks the inner loop so the new
    feed is picked up without restarting the server.
    """
    while True:
        feed = market_data.active_feed()
        generation = market_data.generation
        # Captured alongside `generation`, for the same reason: flipping the
        # data source sets market_data.source immediately, but this loop keeps
        # yielding from the OLD feed until the generation check breaks it. A
        # tick read from the simulated generator must never be filed as live
        # data — that mislabels a synthetic price as a real one.
        feed_source = market_data.source.value
        try:
            async for tick in feed.stream():
                state.latest_quotes[tick.symbol] = {
                    "ltp": tick.ltp,
                    "bid": tick.bid,
                    "ask": tick.ask,
                    "volume": tick.volume,
                }
                market_data.note_tick(tick)
                await broadcaster.publish("tick", tick.__dict__)

                # Bracket enforcement runs on every tick, bot on or off.
                await strategy_runner.monitor_tick(tick.symbol, tick.ltp)
                await manual_desk.monitor_tick(tick.symbol, tick.ltp)

                # Chart history is kept separately from the strategy's single
                # configured interval, and is fed whether or not the bot runs.
                candle_store.on_tick(tick.symbol, tick.ltp, tick.volume, feed_source)

                completed = strategy_runner.candles.on_tick(tick.symbol, tick.ltp, tick.volume)
                if completed is not None:
                    await strategy_runner.on_candle_close(completed)

                if market_data.generation != generation:
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            market_data.error = str(exc)
            await broadcaster.publish("log", {"level": "ERROR", "message": f"Market data feed error: {exc}"})
            await asyncio.sleep(3)


async def _feed_health_loop() -> None:
    while True:
        await broadcaster.publish("feed_health", _health_payload())
        await asyncio.sleep(5)


def _health_payload() -> dict:
    h = market_data.health()
    return {
        "source": h.source,
        "session": h.session,
        "market_open": h.market_open,
        "connected": h.connected,
        "stale": h.stale,
        "error": h.error,
        "symbols": h.symbols,
        "poll_interval_sec": h.poll_interval_sec,
        "last_tick_at": h.last_tick_at.isoformat() if h.last_tick_at else None,
    }


async def _square_off_scheduler_loop() -> None:
    """Polls every 15s; at/after the configured cut-off (default 15:30 IST,
    the NSE close) it force-closes every open paper position and blocks new
    entries for the rest of the day via the risk manager's own time check.

    The poll is deliberately tighter than the old once-a-minute: with the
    cut-off sitting on the closing bell, a 60s sleep could square off a full
    minute after the market had already stopped ticking.
    """
    already_squared_off_date: dt.date | None = None
    while True:
        # Always IST: the risk manager's own cut-off check uses IST, and a host
        # running in another timezone must not square off at a different hour
        # than the gate that blocks new entries.
        now = ist_now()
        cutoff = state.risk_manager.config.square_off_time_ist
        if now.time() >= cutoff and already_squared_off_date != now.date():
            for symbol in list(state.paper_engine.positions.keys()):
                await close_and_settle(symbol, f"{cutoff.strftime('%H:%M')} IST HARD CUT-OFF")
            for symbol in list(state.manual_engine.positions.keys()):
                await close_and_settle(
                    symbol, f"{cutoff.strftime('%H:%M')} IST HARD CUT-OFF", account=state.ACCOUNT_MANUAL
                )
            if strategy_runner.enabled:
                await strategy_runner.stop()
            already_squared_off_date = now.date()
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    # A stored Groww token is good until end of day, so a restart should not
    # cost the user their live session.
    try:
        restored = await routes_auth.restore_broker_sessions()
    except Exception as exc:  # noqa: BLE001
        restored = []
        await broadcaster.publish(
            "log", {"level": "WARN", "message": f"Could not restore broker session: {exc}"}
        )
    if restored:
        await broadcaster.publish(
            "log",
            {
                "level": "INFO",
                "message": f"Restored saved {', '.join(restored)} session from its stored token — "
                "switch the data source to LIVE NSE to use it.",
            },
        )

    # Open positions must come back with their brackets, or a restart leaves
    # unprotected risk on the book and an OPEN row nothing can ever close.
    reopened, voided = await restore_open_positions()
    if reopened:
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": f"Restored {len(reopened)} open position(s) from the ledger: "
                f"{', '.join(reopened)}. Stop-loss and target are being enforced again.",
            },
        )
    if voided:
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": f"Voided {len(voided)} position(s) opened against a price series that did "
                f"not survive the restart ({', '.join(voided)}). They are marked CANCELLED at zero P&L "
                "rather than closed against unrelated prices.",
            },
        )

    # Symbols the user searched for and added beyond the bot's fixed 20-name
    # universe. Restored before the tick loop starts so the feed is streaming
    # the full watchlist from its first tick, not just the core symbols.
    watchlist_restored = await watchlist.restore()
    if watchlist_restored:
        await broadcaster.publish(
            "log",
            {
                "level": "INFO",
                "message": f"Restored {len(watchlist_restored)} watchlist symbol(s): "
                f"{', '.join(watchlist_restored)}.",
            },
        )

    # The recorder copies the minute bars the tick loop already produces onto
    # disk. Started with the app because a morning that was not recorded cannot
    # be recovered later — there is no backfill for "what was it at 11:00".
    from app.services.market_recorder import market_recorder
    await market_recorder.start()

    tick_task = asyncio.create_task(_tick_feed_loop())
    square_off_task = asyncio.create_task(_square_off_scheduler_loop())
    health_task = asyncio.create_task(_feed_health_loop())
    await broadcaster.publish("log", {"level": "INFO", "message": "Backend started in PAPER TRADING mode."})
    yield
    if scanner_worker.running:
        await scanner_worker.stop()
    tick_task.cancel()
    square_off_task.cancel()
    health_task.cancel()


app = FastAPI(title="Intraday ORB Trading Bot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_auth.router)
app.include_router(routes_orders.router)
app.include_router(routes_bot.router)
app.include_router(routes_marketdata.router)
app.include_router(routes_scanner.router)
app.include_router(routes_reports.router)
app.include_router(routes_ai.router)
app.include_router(routes_chat.router)
app.include_router(routes_manual.router)
app.include_router(routes_instruments.router)
app.include_router(routes_watchlist.router)
app.include_router(routes_chart.router)
app.include_router(routes_scanner_engine.router)
app.include_router(routes_movers.router)
app.include_router(routes_research.router)
app.include_router(routes_ws.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": state.mode}
