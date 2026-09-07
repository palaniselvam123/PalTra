"""Automated Technical Scanner & Alert Engine API.

Mounted under /api/scan to avoid colliding with the existing /api/scanner
opening-range endpoint, which is a different thing entirely.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.encryption import get_vault
from app.core.market_clock import ist_now
from app.models.database import AlertChannel, ScannerConfig, async_session
from app.services.alert_notifier import CALLMEBOT, TWILIO, alert_notifier, format_message
from app.services import patterns as candle_patterns
from app.services.candle_store import INTERVALS
from app.services.scanner_worker import scanner_worker

router = APIRouter(prefix="/api/scan", tags=["scanner-engine"])

VALID_MA = {"EMA", "SMA"}
VALID_SIGNALS = {"GOLDEN_CROSS", "DEATH_CROSS", "BOTH"}
VALID_UNIVERSE = {"WATCHLIST", "CORE", "CUSTOM", "GAINERS"}


class ConfigRequest(BaseModel):
    timeframe: str | None = None
    fast_period: int | None = Field(None, ge=5, le=400)
    fast_type: str | None = None
    slow_period: int | None = Field(None, ge=8, le=400)
    slow_type: str | None = None
    signal_type: str | None = None
    trend_filter: bool | None = None
    trend_period: int | None = Field(None, ge=20, le=400)
    volume_filter: bool | None = None
    volume_multiplier: float | None = Field(None, ge=0.1, le=20)
    volume_lookback: int | None = Field(None, ge=2, le=200)
    pattern_filter: bool | None = None
    pattern_lookback: int | None = Field(None, ge=1, le=10)
    adx_filter: bool | None = None
    adx_threshold: float | None = Field(None, ge=5, le=60)
    rsi_filter: bool | None = None
    rsi_overbought: float | None = Field(None, ge=50, le=90)
    min_price: float | None = Field(None, ge=0, le=1_000_000)
    max_price: float | None = Field(None, ge=0, le=1_000_000)
    cooldown_minutes: int | None = Field(None, ge=0, le=1440)
    once_per_session: bool | None = None
    universe: str | None = None
    intrabar: bool | None = None
    custom_symbols: str | None = None


class ChannelRequest(BaseModel):
    provider: str
    enabled: bool = True
    # CallMeBot: target = phone, secret = apikey.
    # Twilio: target = destination number, secret = "account_sid:auth_token", extra = from number.
    target: str
    secret: str
    extra: str = ""


def _config_dict(cfg: ScannerConfig) -> dict:
    return {
        "timeframe": cfg.timeframe,
        "fast_period": cfg.fast_period,
        "fast_type": cfg.fast_type,
        "slow_period": cfg.slow_period,
        "slow_type": cfg.slow_type,
        "signal_type": cfg.signal_type,
        "trend_filter": cfg.trend_filter,
        "trend_period": cfg.trend_period,
        "volume_filter": cfg.volume_filter,
        "volume_multiplier": cfg.volume_multiplier,
        "volume_lookback": cfg.volume_lookback,
        "pattern_filter": cfg.pattern_filter,
        "pattern_lookback": cfg.pattern_lookback,
        "adx_filter": cfg.adx_filter,
        "adx_threshold": cfg.adx_threshold,
        "rsi_filter": bool(getattr(cfg, "rsi_filter", True)),
        "rsi_overbought": float(getattr(cfg, "rsi_overbought", 70.0) or 70.0),
        "min_price": cfg.min_price,
        "max_price": cfg.max_price,
        "cooldown_minutes": cfg.cooldown_minutes,
        "once_per_session": cfg.once_per_session,
        "universe": cfg.universe,
        "intrabar": cfg.intrabar,
        "custom_symbols": cfg.custom_symbols,
    }


@router.get("/config")
async def get_config():
    cfg = await scanner_worker.load_config()
    return {
        "config": _config_dict(cfg),
        "options": {
            "timeframes": list(INTERVALS.keys()),
            "ma_types": sorted(VALID_MA),
            "signal_types": sorted(VALID_SIGNALS),
            "universes": sorted(VALID_UNIVERSE),
            "patterns": candle_patterns.ALL_PATTERNS,
        },
    }


@router.post("/config")
async def set_config(body: ConfigRequest):
    if scanner_worker.running:
        raise HTTPException(409, "Stop the scanner before changing its configuration.")

    async with async_session() as session:
        cfg = await session.get(ScannerConfig, 1)
        if cfg is None:
            cfg = ScannerConfig(id=1)
            session.add(cfg)

        for field_name, value in body.model_dump(exclude_none=True).items():
            setattr(cfg, field_name, value)

        if cfg.timeframe not in INTERVALS:
            raise HTTPException(400, f"timeframe must be one of {list(INTERVALS)}")
        if cfg.fast_type.upper() not in VALID_MA or cfg.slow_type.upper() not in VALID_MA:
            raise HTTPException(400, "fast_type and slow_type must be EMA or SMA")
        if cfg.signal_type not in VALID_SIGNALS:
            raise HTTPException(400, f"signal_type must be one of {sorted(VALID_SIGNALS)}")
        if cfg.universe not in VALID_UNIVERSE:
            raise HTTPException(400, f"universe must be one of {sorted(VALID_UNIVERSE)}")
        # A fast period at or above the slow period can never produce a
        # meaningful crossover — the lines would track or invert permanently.
        if cfg.fast_period >= cfg.slow_period:
            raise HTTPException(400, "fast_period must be smaller than slow_period")
        if cfg.slow_period - cfg.fast_period < 5:
            raise HTTPException(
                400,
                "fast and slow MAs must be at least 5 periods apart — "
                "EMA2/EMA3 is tick noise and the bot will not trade it",
            )
        if cfg.max_price > 0 and cfg.min_price > cfg.max_price:
            raise HTTPException(400, "min_price cannot be greater than max_price")
        if cfg.universe == "CUSTOM" and not (cfg.custom_symbols or "").strip():
            raise HTTPException(400, "CUSTOM universe needs at least one symbol")

        cfg.fast_type = cfg.fast_type.upper()
        cfg.slow_type = cfg.slow_type.upper()
        await session.commit()
        await session.refresh(cfg)
        return {"config": _config_dict(cfg)}


@router.get("/status")
async def status():
    return {
        **scanner_worker.snapshot(),
        "channels": await alert_notifier.configured_providers(),
    }


@router.post("/start")
async def start():
    await scanner_worker.start()
    return scanner_worker.snapshot()


@router.post("/stop")
async def stop():
    await scanner_worker.stop()
    return scanner_worker.snapshot()


@router.post("/scan-now")
async def scan_now():
    """Runs one scan cycle immediately, without starting the loop. Useful for
    checking a configuration change actually matches something.
    """
    fired = await scanner_worker.scan_once()
    return {
        **scanner_worker.snapshot(),
        "signals_this_scan": [
            {"symbol": s.symbol, "side": s.side, "price": s.price, "reasons": s.reasons} for s in fired
        ],
    }


@router.get("/signals")
async def signals(limit: int = 50):
    return await scanner_worker.recent_signals(limit=min(max(limit, 1), 500))


@router.get("/channels")
async def channels():
    return await alert_notifier.configured_providers()


@router.post("/channels")
async def save_channel(body: ChannelRequest):
    provider = body.provider.lower()
    if provider not in (CALLMEBOT, TWILIO):
        raise HTTPException(400, f"provider must be '{CALLMEBOT}' or '{TWILIO}'")
    if not body.target or not body.secret:
        raise HTTPException(400, "target and secret are both required")
    if provider == TWILIO and ":" not in body.secret:
        raise HTTPException(400, "For Twilio, secret must be 'account_sid:auth_token'")
    if provider == TWILIO and not body.extra:
        raise HTTPException(400, "For Twilio, extra must be the WhatsApp-enabled 'from' number")

    vault = get_vault()
    async with async_session() as session:
        row = (
            await session.execute(select(AlertChannel).where(AlertChannel.provider == provider))
        ).scalar_one_or_none()
        if row is None:
            row = AlertChannel(provider=provider)
            session.add(row)
        row.target_encrypted = vault.encrypt(body.target)
        row.secret_encrypted = vault.encrypt(body.secret)
        row.extra_encrypted = vault.encrypt(body.extra) if body.extra else ""
        row.enabled = body.enabled
        row.updated_at = dt.datetime.utcnow()

        # Only one channel delivers at a time; enabling this one disables the
        # rest so a signal cannot fan out to two providers unexpectedly.
        if body.enabled:
            others = (
                await session.execute(select(AlertChannel).where(AlertChannel.provider != provider))
            ).scalars().all()
            for other in others:
                other.enabled = False

        await session.commit()

    return await alert_notifier.configured_providers()


@router.delete("/channels/{provider}")
async def delete_channel(provider: str):
    async with async_session() as session:
        row = (
            await session.execute(select(AlertChannel).where(AlertChannel.provider == provider.lower()))
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, f"No saved channel for '{provider}'")
        await session.delete(row)
        await session.commit()
    return await alert_notifier.configured_providers()


@router.post("/test-alert")
async def test_alert():
    """Sends a real message through the enabled channel, so credentials are
    proven before a live signal depends on them.
    """
    message = format_message(
        side="BUY",
        symbol="TEST",
        timeframe="5m",
        price=1234.56,
        fast_label="EMA9",
        slow_label="EMA21",
        when=ist_now(),
        extra_reasons=["This is a test message from your scanner — no real signal fired."],
    )
    result = await alert_notifier.send(message)
    if not result.ok:
        raise HTTPException(400, result.error or result.skipped_reason or "Send failed")
    return {"ok": True, "provider": result.provider}
