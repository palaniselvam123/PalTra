"""All-bots day target, stop loss and square-off time (all_bots_guard.py).

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
changed so the total no longer hits them (`may_restart`).

A third setting, `all_bots_squareoff` ("HH:MM" IST, empty = off, between 09:15 and
15:30), does the same at that time of day: from then on every bot is squared off
and stays stopped for the rest of the day, whatever the P&L (each bot's own
`square_off_time` still applies too). Bookkeeping and the existing kill path only:
nothing here places an order of its own.

**Profit safety net** (`all_bots_trail`, off by default; owner's request): once today's combined P&L has reached
`all_bots_trail_start` (₹5,000), a floor sits at `all_bots_trail_keep` (70) % of the day's best P&L so far
(`peak`); from `all_bots_trail_start2` (₹10,000, 0 = no second step) it keeps `all_bots_trail_keep2` (75) % of
the best. The floor only rises with the best; the P&L falling to it trips the same panic (`kind` "trail"). The
best is kept per IST day in memory (`note_peak`), seeded after a restart from the open P&L log
(`seed_peak`). Backtests keep their own day's best on the replay engine.

**In % of capital** (`all_bots_unit` "PCT", owner's request; "RS" = the ₹ fields, the default): the target, stop
loss and both Profit Arm levels are read from their `*_pct` fields as that % of the practice wallet's money
(`capital`: `PaperWallet.funds`, the money loaded); with no money loaded they are off. The keep % are unchanged.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import math
import re

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


def capital() -> float:
    """The practice wallet's money loaded (₹), 0 when none. A DB read."""
    try:
        from database import session_factory
        from models import PaperWallet

        with session_factory()() as db:
            row = db.get(PaperWallet, 1)
            return max(0.0, float(row.funds or 0.0)) if row is not None else 0.0
    except Exception:  # noqa: BLE001
        log.exception("all-bots guard: could not read the wallet")
        return 0.0


def in_pct(cfg) -> bool:
    return str(getattr(cfg, "all_bots_unit", "RS") or "RS").upper() == "PCT"


def _pct_amount(cfg, field: str, cap: float | None) -> float:
    """`field`_pct of the capital in ₹ (0 when the % or the capital is 0)."""
    cap = capital() if cap is None else cap
    try:
        pct = float(getattr(cfg, f"{field}_pct", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(pct) or pct <= 0 or cap <= 0:
        return 0.0
    return round(min(pct, 1000.0) * cap / 100.0, 2)


def level(cfg, field: str, cap: float | None = None) -> float:
    """One level in ₹: the ₹ field, or its % of capital when the unit is PCT."""
    return _pct_amount(cfg, field, cap) if in_pct(cfg) else _amount(getattr(cfg, field, 0))


def limits(cfg, cap: float | None = None) -> tuple[float, float]:
    """(target, stop) in ₹ from bot 1's settings row; 0 = off."""
    return level(cfg, "all_bots_target", cap), level(cfg, "all_bots_stop", cap)


EARLIEST, LATEST = dt.time(9, 15), dt.time(15, 30)
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def parse_time(value) -> dt.time | None:
    """'HH:MM' inside the session (09:15-15:30) as a time, else None (off)."""
    match = _HHMM.match(str(value or "").strip())
    if not match:
        return None
    at = dt.time(int(match.group(1)), int(match.group(2)))
    return at if EARLIEST <= at <= LATEST else None


def squareoff_at(cfg) -> dt.time | None:
    return parse_time(getattr(cfg, "all_bots_squareoff", None))


def _pct(value, default: float) -> float:
    try:
        out = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return min(99.0, max(1.0, out)) if math.isfinite(out) else default


def trail_rule(cfg, cap: float | None = None) -> tuple[float, float, float, float] | None:
    """(start, keep %, start2, keep2 %) of the profit safety net, or None when it is off."""
    if not bool(getattr(cfg, "all_bots_trail", False)):
        return None
    if in_pct(cfg):
        cap = capital() if cap is None else cap
        start, start2 = _pct_amount(cfg, "all_bots_trail_start", cap), _pct_amount(cfg, "all_bots_trail_start2", cap)
    else:
        start = _amount(getattr(cfg, "all_bots_trail_start", 5000.0))
        start2 = _amount(getattr(cfg, "all_bots_trail_start2", 0.0))
    if start <= 0:
        return None
    return (start, _pct(getattr(cfg, "all_bots_trail_keep", 70.0), 70.0),
            start2 if start2 > start else 0.0, _pct(getattr(cfg, "all_bots_trail_keep2", 75.0), 75.0))


def trail_floor(peak: float, rule) -> float | None:
    """The safety net for the day's best P&L `peak` (None until the best reaches the start)."""
    if not rule or peak < rule[0]:
        return None
    keep = rule[3] if rule[2] and peak >= rule[2] else rule[1]
    return round(peak * keep / 100.0, 2)


def verdict(total: float, target: float, stop: float, now: dt.datetime | None = None, at: dt.time | None = None,
            floor: float | None = None) -> str | None:
    """'time' / 'target' / 'stop' / 'trail' when a limit has been reached today, else None."""
    if at is not None and now is not None and now.time() >= at:
        return "time"
    if target > 0 and total >= target:
        return "target"
    if stop > 0 and total <= -stop:
        return "stop"
    if floor is not None and total <= floor:
        return "trail"
    return None


# The day's best combined P&L (live guard), per IST day, memory only.
_PEAK: dict = {"day": None, "peak": None}


def peak_today(day: dt.date) -> float | None:
    return _PEAK["peak"] if _PEAK["day"] == day else None


def note_peak(day: dt.date, amount: float) -> float:
    """Raise today's best to `amount` when higher; returns the best."""
    if _PEAK["day"] != day or _PEAK["peak"] is None:
        _PEAK["day"], _PEAK["peak"] = day, float(amount)
    elif amount > _PEAK["peak"]:
        _PEAK["peak"] = float(amount)
    return _PEAK["peak"]


def seed_peak(day: dt.date, desks=("1", "2", "3", "4")) -> float | None:
    """After a restart: today's best from the open P&L log (bots 1-4 added up per minute). Never raises."""
    if _PEAK["day"] == day and _PEAK["peak"] is not None:
        return _PEAK["peak"]
    try:
        import open_pnl_log

        per_minute: dict[str, float] = {}
        for desk in desks:
            for p in open_pnl_log.points(day.isoformat(), desk):
                per_minute[p["t"]] = per_minute.get(p["t"], 0.0) + float(p.get("total_gross") or 0.0)
    except Exception:  # noqa: BLE001
        log.exception("all-bots guard: could not read today's P&L log")
        return None
    if per_minute:
        return note_peak(day, max(per_minute.values()))
    return None


def closed_by_bot(engines, net: bool = False) -> dict:
    """Today's closed P&L of each bot, in its current book: before charges, or after with `net`.
    A DB read: call it off the event loop."""
    out = {}
    for engine in engines:
        cfg = engine.load_config()
        mode = (cfg.trading_mode or "PAPER").upper()
        kpis = engine._kpis(mode)  # noqa: SLF001 - the screen's own sum
        out[engine.bot_id] = float(kpis["net"] if net else kpis["actual_gross"])
    return out


def _ist_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))


class PriceMissing(Exception):
    """A held stock has no last price yet (right after a restart): its open P&L is unknown, not ₹0."""


def open_by_bot(engines, net: bool = False, strict: bool = False) -> dict:
    """Open P&L of each bot's held stocks at their last price. On the event loop: _open_pnl moves the focus.

    `strict` (the guard and the P&L alerts) raises PriceMissing when a held stock has no price yet: counted as ₹0,
    a restart in market hours could trip the day target / stop on the closed P&L alone and panic every bot."""
    out = {}
    for engine in engines:
        total = 0.0
        for name in list(engine.positions):
            ltps = getattr(engine, "_ltps", None)
            if strict and ltps is not None and not float(ltps.get(name) or 0.0):
                raise PriceMissing(f"bot {getattr(engine, 'bot_id', '?')}: no price for {name} yet")
            value = engine._open_pnl(name)  # noqa: SLF001
            if value:
                total += float(value["net" if net else "gross"])
        out[engine.bot_id] = total
    return out


def closed_total(engines, net: bool = False) -> float:
    """Today's closed P&L of every bot, each in its current book (a DB read: off the event loop)."""
    return sum(closed_by_bot(engines, net).values())


def open_total(engines, net: bool = False, strict: bool = False) -> float:
    """Open P&L of every held stock at its last price. On the event loop: _open_pnl moves the focus."""
    return sum(open_by_bot(engines, net, strict).values())


def total(engines) -> float:
    return round(closed_total(engines) + open_total(engines), 2)


def is_guard_halt(engine) -> bool:
    return engine.status == "HALTED" and (engine.halt_reason or "").startswith(HALT_PREFIX)


def may_restart(engines, cfg, now: dt.datetime | None = None) -> bool:
    """A bot halted by this guard may start again today only if no limit hits any more."""
    target, stop = limits(cfg)
    amount = total(engines)
    peak = peak_today(now.date()) if now else None
    floor = trail_floor(max(peak or amount, amount), trail_rule(cfg))
    return verdict(amount, target, stop, now, squareoff_at(cfg), floor) is None


async def trip(engines, kind: str, amount: float, target: float, stop: float, at: dt.time | None = None,
               floor: float | None = None, peak: float | None = None) -> str:
    """Panic every bot. Returns the reason given to each."""
    if kind == "trail":
        reason = (f"{HALT_PREFIX} profit safety net hit: P&L ₹{amount:,.2f} fell to the net ₹{(floor or 0):,.2f}"
                  f" (best today ₹{(peak or 0):,.2f}) — every bot squared off")
    elif kind == "time":
        when = at.strftime("%H:%M") if at else "?"
        reason = f"{HALT_PREFIX} square-off time {when} reached: P&L ₹{amount:,.2f} — every bot squared off"
    else:
        limit = target if kind == "target" else -stop
        word = "target" if kind == "target" else "stop loss"
        reason = f"{HALT_PREFIX} {word} hit: P&L ₹{amount:,.2f} (limit ₹{limit:,.2f}) — every bot squared off"
    todo = [eng for eng in engines if not (is_guard_halt(eng) and not eng.positions)]
    # Every bot at once, like "Panic all bots": one after another, the last bot's exits waited for every other
    # bot's stop cancels, exits and fill waits while the loss kept running.
    results = await asyncio.gather(*(eng.kill(reason) for eng in todo), return_exceptions=True)
    for eng, result in zip(todo, results):
        if isinstance(result, BaseException):  # one bot's failure must not leave the others running
            log.error("all-bots guard: kill failed on bot %s: %r", getattr(eng, "bot_id", "?"), result)
    return reason


async def run(engines_fn, config_fn, is_open_fn, poll: float = POLL_SEC, now_fn=None) -> None:
    """Watch the day's total and the clock while the market is open; trip once a limit is reached."""
    while True:
        try:
            if is_open_fn():
                cfg = config_fn()
                cap = await asyncio.to_thread(capital) if in_pct(cfg) else 0.0
                target, stop = limits(cfg, cap)
                at = squareoff_at(cfg)
                rule = trail_rule(cfg, cap)
                floor = peak = None
                if target > 0 or stop > 0 or at is not None or rule:
                    engines = list(engines_fn())
                    closed = await asyncio.to_thread(closed_total, engines)
                    try:
                        amount = round(closed + open_total(engines, strict=True), 2)
                        now = now_fn() if now_fn else _ist_now()
                        if rule and _PEAK["day"] != now.date():
                            await asyncio.to_thread(seed_peak, now.date())
                        peak = note_peak(now.date(), amount)
                        floor = trail_floor(peak, rule)
                        kind = verdict(amount, target, stop, now_fn() if now_fn else None, at, floor)
                    except PriceMissing as exc:
                        # The P&L is not known this pass: only the time of day can trip.
                        log.info("all-bots guard: %s; P&L limits not judged this pass", exc)
                        amount = round(closed, 2)
                        kind = verdict(amount, 0.0, 0.0, now_fn() if now_fn else None, at)
                    if kind and any(not is_guard_halt(eng) or eng.positions for eng in engines):
                        reason = await trip(engines, kind, amount, target, stop, at, floor, peak)
                        log.warning("all-bots guard tripped: %s", reason)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("all-bots guard pass failed")
        await asyncio.sleep(poll)
