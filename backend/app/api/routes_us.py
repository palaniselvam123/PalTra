"""US practice desk routes (`/api/us/*`). Practice fills only: nothing here reaches any broker's order API.

The Alpaca key pair is write-only over the API (saved encrypted, never returned).
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from app.core.encryption import get_vault
from app.models.database import UsCredential, UsDeskConfig, UsPaperTrade, async_session
from app.services import alpaca, us_desk as desk_mod
from app.services.us_desk import CANDLE_MINUTES, parse_symbols, trade_out, us_desk

router = APIRouter(prefix="/api/us", tags=["us"])


@router.get("/state")
async def state():
    return await us_desk.snapshot()


@router.get("/chart")
async def chart(symbol: str):
    found = parse_symbols(symbol)
    if not found:
        raise HTTPException(400, "Unknown symbol")
    return await us_desk.chart(found[0])


class ConfigBody(BaseModel):
    symbols: list[str] | str | None = None
    candle_minutes: int | None = None
    sma_fast: int | None = Field(None, ge=2, le=200)
    sma_slow: int | None = Field(None, ge=3, le=400)
    atr_period: int | None = Field(None, ge=2, le=100)
    stop_on: bool | None = None
    atr_mult: float | None = Field(None, gt=0, le=10)
    allow_short: bool | None = None
    dollars_per_trade: float | None = Field(None, ge=10, le=1_000_000)
    max_trades_per_day: int | None = Field(None, ge=0, le=500)
    daily_loss_limit: float | None = Field(None, ge=0, le=1_000_000)
    entry_start: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    entry_cutoff: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    square_off: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")
    groww_check_symbol: str | None = Field(None, max_length=10)


def _check_times(start: str, cutoff: str, square: str) -> None:
    def t(x: str) -> dt.time:
        try:
            return dt.time(*map(int, x.split(":")))
        except ValueError as exc:
            raise HTTPException(422, f"Bad time {x}") from exc

    s, c, q = t(start), t(cutoff), t(square)
    if not (dt.time(9, 30) <= s < c <= q <= dt.time(16, 0)):
        raise HTTPException(422, "Times must run 09:30 ≤ entry start < cut-off ≤ square-off ≤ 16:00 (New York).")


@router.put("/config")
async def save_config(body: ConfigBody):
    patch = body.model_dump(exclude_none=True)
    if "candle_minutes" in patch and patch["candle_minutes"] not in CANDLE_MINUTES:
        raise HTTPException(422, f"Candle size must be one of {CANDLE_MINUTES} minutes")
    if "symbols" in patch:
        raw = patch["symbols"]
        patch["symbols"] = ",".join(parse_symbols(",".join(raw) if isinstance(raw, list) else raw))
    if "groww_check_symbol" in patch:
        patch["groww_check_symbol"] = (parse_symbols(patch["groww_check_symbol"]) or ["AAPL"])[0]
    async with async_session() as session:
        cfg = await session.get(UsDeskConfig, 1) or UsDeskConfig(id=1)
        session.add(cfg)
        for key, value in patch.items():
            setattr(cfg, key, value)
        fast = cfg.sma_fast if cfg.sma_fast is not None else 9
        slow = cfg.sma_slow if cfg.sma_slow is not None else 21
        if fast >= slow:
            raise HTTPException(422, "SMA fast must be shorter than SMA slow")
        _check_times(cfg.entry_start or "09:35", cfg.entry_cutoff or "15:30", cfg.square_off or "15:50")
        await session.commit()
        await session.refresh(cfg)
        return desk_mod.config_out(cfg)


class RunBody(BaseModel):
    running: bool


@router.post("/run")
async def run(body: RunBody):
    async with async_session() as session:
        cfg = await session.get(UsDeskConfig, 1) or UsDeskConfig(id=1)
        session.add(cfg)
        cfg.running = body.running
        await session.commit()
    us_desk.note("Desk started (practice money)" if body.running else "Desk stopped: no new entries; open trades keep their stop and square-off")
    return {"running": body.running}


@router.post("/trades/{trade_id}/close")
async def close_trade(trade_id: int):
    try:
        row = await us_desk.close_trade(trade_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if row is None:
        raise HTTPException(404, "No open trade with that id")
    return trade_out(row)


@router.post("/close-all")
async def close_all():
    """Stop the desk and close every open practice trade at the last price."""
    async with async_session() as session:
        cfg = await session.get(UsDeskConfig, 1)
        if cfg is not None:
            cfg.running = False
            await session.commit()
    closed = await us_desk.close_all("KILL_SWITCH")
    us_desk.note(f"Close all: desk stopped, {closed} trade(s) closed", "WARN")
    return {"closed": closed}


@router.get("/trades")
async def trades(limit: int = 300):
    async with async_session() as session:
        rows = list((await session.scalars(
            select(UsPaperTrade).order_by(UsPaperTrade.id.desc()).limit(max(1, min(limit, 2000)))
        )).all())
    return {"trades": [trade_out(r) for r in rows]}


# ---- Alpaca key pair ----


class KeysBody(BaseModel):
    key_id: str = Field(min_length=8, max_length=100)
    secret: str = Field(min_length=8, max_length=200)


@router.get("/keys")
async def keys_status():
    async with async_session() as session:
        row = await session.scalar(select(UsCredential).where(UsCredential.provider == "alpaca"))
    env = alpaca.env_keys() is not None
    return {"saved": row is not None, "env": env, "source": "saved" if row is not None else ("env" if env else None)}


@router.put("/keys")
async def save_keys(body: KeysBody):
    """Checks the pair against Alpaca's paper account (read-only), then saves it encrypted."""
    keys = alpaca.Keys(body.key_id, body.secret, "saved")
    client = alpaca.AlpacaData(keys)
    try:
        account = await client.account()
    except alpaca.AlpacaError as exc:
        raise HTTPException(400, str(exc)) from exc
    finally:
        await client.aclose()
    vault = get_vault()
    async with async_session() as session:
        row = await session.scalar(select(UsCredential).where(UsCredential.provider == "alpaca"))
        if row is None:
            row = UsCredential(provider="alpaca", key_id_encrypted="", secret_encrypted="")
            session.add(row)
        row.key_id_encrypted = vault.encrypt(keys.key_id)
        row.secret_encrypted = vault.encrypt(keys.secret)
        row.updated_at = dt.datetime.utcnow()
        await session.commit()
    us_desk.note("Alpaca key saved: real US prices from the next pass")
    return {"saved": True, "account": account}


@router.delete("/keys")
async def delete_keys():
    async with async_session() as session:
        await session.execute(delete(UsCredential).where(UsCredential.provider == "alpaca"))
        await session.commit()
    us_desk.note("Alpaca key removed: back on the simulated feed")
    return {"saved": False}


# ---- Groww US check ----


class GrowwCheckBody(BaseModel):
    symbol: str | None = Field(None, max_length=10)


@router.post("/groww-check")
async def groww_check(body: GrowwCheckBody):
    """Read-only: asks Groww's API for a US price with the desk's Groww session. No order call."""
    cfg = await us_desk.config()
    return await desk_mod.run_groww_check(body.symbol or cfg.groww_check_symbol or "AAPL", alert=True)
