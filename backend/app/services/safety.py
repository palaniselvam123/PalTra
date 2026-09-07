"""Platform-wide emergency stop.

One implementation, called from everywhere a hard stop can originate: the
manual kill-switch button, the daily-loss circuit breaker (whether tripped by
a manual close or a bot auto-exit), and the end-of-day IST cut-off.

Keeping this in one place matters — an earlier version had the bot's circuit
breaker only stop the bot while leaving open positions running, which is
exactly the failure the breaker exists to prevent.
"""
from __future__ import annotations

from app import state
from app.services import notifications
from app.services.broadcaster import broadcaster
from app.services.trade_ledger import close_and_settle


async def trigger_kill_switch(reason: str, *, square_off: bool = True) -> None:
    from app.services.strategy_runner import strategy_runner  # local import: avoids a cycle

    state.kill_switch_active = True

    if strategy_runner.enabled:
        await strategy_runner.force_halt()

    if square_off:
        for symbol in list(state.paper_engine.positions.keys()):
            await close_and_settle(symbol, "KILL SWITCH SQUARE-OFF")

    # Hitting the profit target stops the day just like a loss breach does,
    # but it is good news — don't report it as an emergency.
    kind = state.risk_manager.state.lock_kind
    if kind == "PROFIT_TARGET":
        level, headline = "INFO", "DAILY PROFIT TARGET REACHED"
    elif kind == "LOSS_LIMIT":
        level, headline = "ERROR", "DAILY LOSS LIMIT HIT"
    else:
        level, headline = "ERROR", "KILL SWITCH TRIGGERED"

    await broadcaster.publish("log", {"level": level, "message": f"{headline}: {reason}"})
    await broadcaster.publish("kill_switch", {"active": True, "reason": reason, "kind": kind})
    # Trading has stopped for the day — worth interrupting for, even if the
    # dashboard is not the tab in front of you.
    await notifications.alert(headline, reason, level=level)
    await strategy_runner.publish_status()


async def reset_kill_switch() -> None:
    """Operator reset. Clears BOTH halts.

    A loss-limit breach sets two things: the kill switch and the risk
    manager's day lock. Clearing only the first left the platform still
    locked, so the button that says "reset to resume" did not resume
    anything — the bot went on refusing to start with the old lock reason.
    The reset is an explicit operator override, so it says so in the log
    rather than quietly discarding a safety stop.
    """
    from app.services.strategy_runner import strategy_runner

    was_locked = state.risk_manager.state.locked
    lock_reason = state.risk_manager.state.lock_reason

    state.kill_switch_active = False
    state.risk_manager.release_lock()

    if was_locked:
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": f"Day lock cleared by operator reset — was: {lock_reason}",
            },
        )

    await broadcaster.publish("kill_switch", {"active": False, "reason": ""})
    await strategy_runner.publish_status()
