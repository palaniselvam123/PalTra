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
    strategy: str | None = None
    gainers_top_n: int | None = None
    gainers_allocation_pct: float | None = None
    gainers_stop_loss_pct: float | None = None
    gainers_target_pct: float | None = None
    gainers_min_gain_pct: float | None = None
    gainers_scan_interval_sec: float | None = None
    gainers_max_positions: int | None = None
    last_entry_buffer_min: int | None = None
    scanner_min_hold_sec: int | None = None
    scanner_close_on_sell: bool | None = None


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

    if body.strategy is not None:
        if body.strategy not in ("orb", "gainers", "scanner"):
            raise HTTPException(400, "strategy must be 'orb', 'gainers' or 'scanner'")
        cfg.strategy = body.strategy

    # Bounds are validated here rather than in the strategy so a bad value is
    # rejected at the edge with a clear message, instead of silently producing
    # a zero-share order later.
    if body.gainers_allocation_pct is not None and not (0 < body.gainers_allocation_pct <= 100):
        raise HTTPException(400, "gainers_allocation_pct must be between 0 and 100")
    if body.gainers_stop_loss_pct is not None and not (0 < body.gainers_stop_loss_pct < 100):
        raise HTTPException(400, "gainers_stop_loss_pct must be between 0 and 100")
    if body.gainers_target_pct is not None and body.gainers_target_pct <= 0:
        raise HTTPException(400, "gainers_target_pct must be positive")
    if body.gainers_top_n is not None and not (1 <= body.gainers_top_n <= 100):
        raise HTTPException(400, "gainers_top_n must be between 1 and 100")
    # A floor of 1s: the scan queries the account, so an unthrottled loop would
    # hit the database on every tick across the whole universe.
    if body.gainers_scan_interval_sec is not None and not (1 <= body.gainers_scan_interval_sec <= 300):
        raise HTTPException(400, "gainers_scan_interval_sec must be between 1 and 300 seconds")
    if body.gainers_max_positions is not None and not (1 <= body.gainers_max_positions <= 20):
        raise HTTPException(400, "gainers_max_positions must be between 1 and 20")
    if body.last_entry_buffer_min is not None and not (0 <= body.last_entry_buffer_min <= 180):
        raise HTTPException(400, "last_entry_buffer_min must be between 0 and 180")
    if body.scanner_min_hold_sec is not None and not (0 <= body.scanner_min_hold_sec <= 3600):
        raise HTTPException(400, "scanner_min_hold_sec must be between 0 and 3600")

    for field_name in (
        "gainers_top_n",
        "gainers_allocation_pct",
        "gainers_stop_loss_pct",
        "gainers_target_pct",
        "gainers_min_gain_pct",
        "gainers_scan_interval_sec",
        "gainers_max_positions",
        "scanner_close_on_sell",
        "last_entry_buffer_min",
        "scanner_min_hold_sec",
    ):
        value = getattr(body, field_name)
        if value is not None:
            setattr(cfg, field_name, value)

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
