"""All-bots day target and stop loss (all_bots_guard.py).

Off by default. Two numbers on bot 1's settings row (`BotConfig` id 1):
`all_bots_target` and `all_bots_stop`, in ₹, 0 = off. While NSE is open the
watcher (`run`, started in `boot_engine`) adds up today's P&L of bots 1-4,
each in its own current book (PAPER or LIVE): closed trades plus open positions
at their last price, before charges, the same figure the screens show. The
research desk and replays are not counted.

When the total reaches the target, or falls to minus the stop, every bot gets
the same panic as "Panic all bots" (`StrategyEngine.kill`: open trades squared
off, the LIVE stop cancelled first, the bot HALTED) with a reason starting
`HALT_PREFIX`. Start then refuses for the rest of the day, unless the limits are
changed so the total no longer hits them (`may_restart`). Bookkeeping and the
existing kill path only: nothing here places an order of its own.
"""
from __future__ import annotations

import asyncio
import logging
import math

log = logging.getLogger("sma.all_bots_guard")

HALT_PREFIX = "All-bots day"
POLL_SEC = 5.0
MAX_AMOUNT = 1e9


def _amount(value) -> float:
    try:
        out = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(out) or out < 0:
        return 0.0
    return min(out, MAX_AMOUNT)


def limits(cfg) -> tuple[float, float]:
    """(target, stop) in ₹ from bot 1's settings row; 0 = off."""
    return _amount(getattr(cfg, "all_bots_target", 0)), _amount(getattr(cfg, "all_bots_stop", 0))


def verdict(total: float, target: float, stop: float) -> str | None:
    """'target' / 'stop' when the day's total has reached a limit, else None."""
    if target > 0 and total >= target:
        return "target"
    if stop > 0 and total <= -stop:
        return "stop"
    return None


def closed_total(engines) -> float:
    """Today's closed P&L of every bot, each in its current book (a DB read: off the event loop)."""
    out = 0.0
    for engine in engines:
        cfg = engine.load_config()
        mode = (cfg.trading_mode or "PAPER").upper()
        out += float(engine._kpis(mode)["actual_gross"])  # noqa: SLF001 - the screen's own sum
    return out


def open_total(engines) -> float:
    """Open P&L of every held stock at its last price. On the event loop: _open_pnl moves the focus."""
    out = 0.0
    for engine in engines:
        for name in list(engine.positions):
            value = engine._open_pnl(name)  # noqa: SLF001
            if value:
                out += float(value["gross"])
    return out


def total(engines) -> float:
    return round(closed_total(engines) + open_total(engines), 2)


def is_guard_halt(engine) -> bool:
    return engine.status == "HALTED" and (engine.halt_reason or "").startswith(HALT_PREFIX)


def may_restart(engines, cfg) -> bool:
    """A bot halted by this guard may start again today only if the limits no longer hit."""
    target, stop = limits(cfg)
    return verdict(total(engines), target, stop) is None


async def trip(engines, kind: str, amount: float, target: float, stop: float) -> str:
    """Panic every bot. Returns the reason given to each."""
    limit = target if kind == "target" else -stop
    word = "target" if kind == "target" else "stop loss"
    reason = f"{HALT_PREFIX} {word} hit: P&L ₹{amount:,.2f} (limit ₹{limit:,.2f}) — every bot squared off"
    for eng in engines:
        if is_guard_halt(eng) and not eng.positions:
            continue
        try:
            await eng.kill(reason)
        except Exception:  # noqa: BLE001 - one bot's failure must not leave the others running
            log.exception("all-bots guard: kill failed on bot %s", getattr(eng, "bot_id", "?"))
    return reason


async def run(engines_fn, config_fn, is_open_fn, poll: float = POLL_SEC) -> None:
    """Watch the day's total while the market is open; trip once a limit is reached."""
    while True:
        try:
            if is_open_fn():
                cfg = config_fn()
                target, stop = limits(cfg)
                if target > 0 or stop > 0:
                    engines = list(engines_fn())
                    closed = await asyncio.to_thread(closed_total, engines)
                    amount = round(closed + open_total(engines), 2)
                    kind = verdict(amount, target, stop)
                    if kind and any(not is_guard_halt(eng) or eng.positions for eng in engines):
                        reason = await trip(engines, kind, amount, target, stop)
                        log.warning("all-bots guard tripped: %s", reason)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("all-bots guard pass failed")
        await asyncio.sleep(poll)
