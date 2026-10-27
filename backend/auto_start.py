"""Auto-start for PAPER bots (`auto_start`, off by default, per bot 1-4).

The bots boot STOPPED after every deploy or restart, and a finished day rolls back to STOPPED, so a bot
otherwise waits for Start each morning. With `auto_start` on, `run` (started in `boot_engine` on the desk
only) presses Start for a PAPER bot that is STOPPED while NSE is open, through the same `_start_bot`
as the button: a cross already on the tape is not traded, the all-bots guard and halts still apply.

Never for a LIVE bot (Start on LIVE stays a person's press), never a PAUSED bot (Pause is a person's
choice) and never a HALTED or DAY_COMPLETED one (loss limit, trade cap, panic, square-off).
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("sma.auto_start")

POLL_SEC = 15.0


def wants(cfg) -> bool:
    """On for this bot, and the bot is in PAPER."""
    if cfg is None or not bool(getattr(cfg, "auto_start", False)):
        return False
    return (getattr(cfg, "trading_mode", None) or "PAPER").upper() != "LIVE"


async def pass_once(engines, start_fn) -> list[int]:
    """Start every STOPPED PAPER bot that has auto-start on. Returns the bot ids started."""
    started: list[int] = []
    for eng in engines:
        if eng.status != "STOPPED":
            continue
        cfg = eng._cfg_cache
        if cfg is None:
            cfg = await asyncio.to_thread(eng.load_config)
        if not wants(cfg):
            continue
        try:
            await start_fn(eng)
        except Exception as exc:  # noqa: BLE001  (an HTTPException from a refused Start)
            log.info("auto-start of bot %s refused: %s", getattr(eng, "bot_id", 1), getattr(exc, "detail", exc))
            continue
        if eng.status == "RUNNING":
            eng.last_signal = "Auto-started (PAPER). The next cross is traded, not one already on the chart."
            started.append(int(getattr(eng, "bot_id", 1) or 1))
            log.info("auto-started bot %s (PAPER)", getattr(eng, "bot_id", 1))
    return started


async def run(engines_fn, start_fn, is_open_fn, poll: float = POLL_SEC) -> None:
    while True:
        try:
            if is_open_fn():
                await pass_once(list(engines_fn()), start_fn)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("auto-start pass failed")
        await asyncio.sleep(poll)
