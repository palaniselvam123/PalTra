from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import state
from app.services.strategy_runner import strategy_runner

router = APIRouter(prefix="/api/bot", tags=["bot"])

# Candle/range presets. "market" matches the spec's 5-min candles over a
# 15-min opening range; "demo" compresses it so the engine can be exercised
# outside market hours.
PRESETS = {
    "market": {"candle_interval_sec": 300, "range_duration_sec": 900, "rvol_threshold": 2.0},
    "demo": {"candle_interval_sec": 15, "range_duration_sec": 60, "rvol_threshold": 1.2},
}


class BotConfigRequest(BaseModel):
    session_mode: str | None = None
    candle_interval_sec: int | None = None
    range_duration_sec: int | None = None
    rvol_threshold: float | None = None
    risk_reward: float | None = None
    trailing_enabled: bool | None = None
    adx_filter_enabled: bool | None = None
    adx_threshold: float | None = None


@router.get("/status")
async def bot_status():
    return asdict(strategy_runner.snapshot())


@router.post("/start")
async def start_bot():
    if state.kill_switch_active:
        raise HTTPException(423, "Kill switch is active — reset it before starting the bot.")
    if state.risk_manager.state.locked:
        raise HTTPException(423, state.risk_manager.state.lock_reason)
    await strategy_runner.start()
    return asdict(strategy_runner.snapshot())


@router.post("/stop")
async def stop_bot():
    await strategy_runner.stop()
    return asdict(strategy_runner.snapshot())


@router.post("/config")
async def set_bot_config(body: BotConfigRequest):
    if strategy_runner.enabled:
        raise HTTPException(409, "Stop the bot before changing its configuration.")

    cfg = strategy_runner.config
    if body.session_mode is not None:
        if body.session_mode not in PRESETS:
            raise HTTPException(400, "session_mode must be 'market' or 'demo'")
        cfg.session_mode = body.session_mode
        for key, value in PRESETS[body.session_mode].items():
            setattr(cfg, key, value)

    for field_name in (
        "candle_interval_sec",
        "range_duration_sec",
        "rvol_threshold",
        "risk_reward",
        "trailing_enabled",
        "adx_filter_enabled",
        "adx_threshold",
    ):
        value = getattr(body, field_name)
        if value is not None:
            setattr(cfg, field_name, value)

    if cfg.candle_interval_sec <= 0 or cfg.range_duration_sec < cfg.candle_interval_sec:
        raise HTTPException(400, "range_duration_sec must be >= candle_interval_sec, and both positive.")

    strategy_runner.candles.set_interval(cfg.candle_interval_sec)
    await strategy_runner.publish_status()
    return asdict(strategy_runner.snapshot())
