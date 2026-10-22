"""All-bots P&L alerts (pnl_alerts.py): a message and / or a phone call when the
combined P&L of bots 1-4 today reaches a profit level or falls to a loss level.

Off by default. Settings on bot 1's settings row (`BotConfig` id 1):
`pnl_alert_profit` / `pnl_alert_loss` in ₹ (0 = off), `pnl_alert_telegram` and
`pnl_alert_call` (which ways to tell the owner, both on by default) and
`pnl_alert_basis` (`GROSS`: before charges, the figure the screens show; `NET`:
after charges). The total is the same one the all-bots guard watches
(all_bots_guard.py): every bot's closed trades plus its open positions at their
last price, each in its own PAPER or LIVE book; not the research desk or replays.

While NSE is open `run` checks every `POLL_SEC` seconds. Each level alerts once
per IST day (`PnlAlerts.sent`, memory only): the profit level when the total is
at or above it, the loss level when it is at or below minus it. The message goes
to the trade alert channel (`alert_notifier.send`: Telegram / WhatsApp) and the
call through the phone-call channel (`alert_notifier.place_call`, market hours
and its 5-minute cooldown). Alerts only: nothing here trades, stops a bot or
changes a setting; the all-bots day target / stop loss is the one that acts.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math

import all_bots_guard

log = logging.getLogger("sma.pnl_alerts")

POLL_SEC = 5.0
MAX_AMOUNT = 1e9
BASES = ("GROSS", "NET")


def _amount(value) -> float:
    try:
        out = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(out) or out < 0:
        return 0.0
    return min(out, MAX_AMOUNT)


def settings(cfg) -> dict:
    """The alert settings from bot 1's row, with their defaults."""
    basis = str(getattr(cfg, "pnl_alert_basis", None) or "GROSS").upper()
    return {
        "profit": _amount(getattr(cfg, "pnl_alert_profit", 0)),
        "loss": _amount(getattr(cfg, "pnl_alert_loss", 0)),
        "telegram": getattr(cfg, "pnl_alert_telegram", None) is not False,
        "call": getattr(cfg, "pnl_alert_call", None) is not False,
        "basis": basis if basis in BASES else "GROSS",
    }


def due(total: float, profit: float, loss: float, sent: set[str]) -> list[str]:
    """The levels reached and not yet alerted today: "profit" and / or "loss"."""
    out = []
    if profit > 0 and total >= profit and "profit" not in sent:
        out.append("profit")
    if loss > 0 and total <= -loss and "loss" not in sent:
        out.append("loss")
    return out


def _inr(value: float) -> str:
    sign = "-" if value < 0 else "+"
    return f"{sign}₹{abs(value):,.2f}"


def message(kind: str, total: float, level: float, basis: str, by_bot: dict) -> str:
    word = "profit" if kind == "profit" else "loss"
    limit = level if kind == "profit" else -level
    charges = "after charges" if basis == "NET" else "before charges"
    bots = ", ".join(f"Bot {bot} {_inr(value)}" for bot, value in sorted(by_bot.items()))
    return (
        f"PalTra P&L alert: all bots today {_inr(total)} ({charges}) reached your {word} level "
        f"{_inr(limit)}. {bots}. Alert only: nothing was squared off."
    )


async def _deliver(text: str, telegram: bool, call: bool) -> None:
    from app.services.alert_notifier import alert_notifier

    if telegram:
        result = await alert_notifier.send(text)
        if not result.ok:
            log.warning("P&L alert message not sent: %s", result.skipped_reason or result.error)
    if call:
        result = await alert_notifier.place_call(text)
        if not result.ok:
            log.warning("P&L alert call not made: %s", result.skipped_reason or result.error)


class PnlAlerts:
    """Remembers which levels alerted today. Memory only: a restart may alert a level once more."""

    def __init__(self, deliver=None) -> None:
        self.day: dt.date | None = None
        self.sent: dict[str, dict] = {}
        self.deliver = deliver or _deliver

    def sent_today(self, today: dt.date) -> dict[str, dict]:
        if self.day != today:
            self.day, self.sent = today, {}
        return self.sent

    async def check(self, engines, cfg, today: dt.date, now: dt.datetime | None = None) -> list[str]:
        """One pass: alert every level reached and not yet alerted today. Returns the kinds alerted."""
        conf = settings(cfg)
        if conf["profit"] <= 0 and conf["loss"] <= 0:
            return []
        if not (conf["telegram"] or conf["call"]):
            return []
        sent = self.sent_today(today)
        net = conf["basis"] == "NET"
        closed = await asyncio.to_thread(all_bots_guard.closed_by_bot, engines, net)
        opened = all_bots_guard.open_by_bot(engines, net)
        by_bot = {bot: round(closed.get(bot, 0.0) + opened.get(bot, 0.0), 2) for bot in closed}
        total = round(sum(by_bot.values()), 2)
        kinds = due(total, conf["profit"], conf["loss"], set(sent))
        for kind in kinds:
            level = conf["profit"] if kind == "profit" else conf["loss"]
            text = message(kind, total, level, conf["basis"], by_bot)
            sent[kind] = {"at": (now or dt.datetime.now()).isoformat(timespec="seconds"), "total": total}
            log.warning("%s", text)
            try:
                await self.deliver(text, conf["telegram"], conf["call"])
            except Exception:  # noqa: BLE001 - an alert failure is only logged
                log.exception("P&L alert delivery failed")
        return kinds


alerts = PnlAlerts()


async def run(engines_fn, config_fn, is_open_fn, now_fn, poll: float = POLL_SEC, watcher: PnlAlerts | None = None) -> None:
    """Check the day's total while the market is open."""
    watch = watcher or alerts
    while True:
        try:
            if is_open_fn():
                now = now_fn()
                await watch.check(list(engines_fn()), config_fn(), now.date(), now)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("P&L alert pass failed")
        await asyncio.sleep(poll)
