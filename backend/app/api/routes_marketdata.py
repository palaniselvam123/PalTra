from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import state
from app.api.routes_auth import _active_clients
from app.brokers.groww_client import GrowwClient
from app.core.market_clock import seconds_until_open, session_state
from app.services.broadcaster import broadcaster
from app.services.market_data import market_data
from app.services.strategy_runner import strategy_runner

router = APIRouter(prefix="/api/marketdata", tags=["marketdata"])


class SourceRequest(BaseModel):
    source: str                       # simulated | live
    poll_interval_sec: float = 2.0


@router.get("/status")
async def status():
    health = asdict(market_data.health())
    health["last_tick_at"] = health["last_tick_at"].isoformat() if health["last_tick_at"] else None
    health["last_change_at"] = health["last_change_at"].isoformat() if health["last_change_at"] else None
    health["seconds_until_open"] = seconds_until_open()
    health["broker_session"] = "groww" in _active_clients
    return health


@router.get("/probe")
async def probe():
    """Reports what the installed growwapi SDK actually exposes. Use this if
    live data fails with a method-not-found error — it tells you exactly which
    call to map in GrowwClient.
    """
    return GrowwClient().probe_sdk()


@router.get("/diagnose")
async def diagnose():
    """Tells you exactly why live data is or isn't available: bad session,
    missing market-data entitlement, or a call-signature mismatch.
    """
    client = _active_clients.get("groww")
    if client is None:
        return {
            "verdict": "NO_SESSION",
            "detail": "No active Groww session. Save credentials in Settings and click Connect Live Data.",
            "checks": [],
        }
    return await client.diagnose_permissions()


@router.post("/source")
async def set_source(body: SourceRequest):
    if body.source not in ("simulated", "live"):
        raise HTTPException(400, "source must be 'simulated' or 'live'")

    if strategy_runner.enabled:
        raise HTTPException(409, "Stop the bot before switching the market data source.")

    # Open positions are priced against whichever series they were opened on.
    # Switching underneath them means the eventual close reads a completely
    # unrelated price — the mechanism behind exits like SHIPROCKET 136 -> 3309.
    # The close path voids such trades as a safety net, but losing a position
    # is a poor outcome; refusing the switch keeps it tradeable instead.
    open_positions = list(state.paper_engine.positions) + list(state.manual_engine.positions)
    if open_positions and body.source != market_data.source.value:
        raise HTTPException(
            409,
            f"Close your open position(s) first: {', '.join(sorted(set(open_positions)))}. "
            "They were opened against the current price feed, and switching underneath them means any "
            "exit would be priced off an unrelated series.",
        )

    if body.source == "simulated":
        await market_data.use_simulated()
        await broadcaster.publish(
            "log", {"level": "WARN", "message": "Market data source → SIMULATED (synthetic prices)."}
        )
        return await status()

    client = _active_clients.get("groww")
    if client is None:
        raise HTTPException(
            428,
            "No active Groww session. Save your API key/secret/TOTP in Settings, then run the "
            "login step before switching to live market data.",
        )

    try:
        await market_data.use_live(client, poll_interval_sec=body.poll_interval_sec)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not start live market data: {exc}") from exc

    await broadcaster.publish(
        "log",
        {
            "level": "INFO",
            "message": f"Market data source → LIVE Groww quotes (session {session_state()}). "
            "Money remains VIRTUAL — no real orders are sent.",
        },
    )
    return await status()
