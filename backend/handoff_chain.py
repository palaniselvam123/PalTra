"""Hand-off chain: where a handed-off stock goes back to, and how a backtest plays the hand-offs.

Bot 1 → Bot 2 (recovery / losing V-turn hand-off, recovery.py, slope_exit.py) and Bot 2 → Bot 3 (breakout
hand-off, breakout.py) take a stock off one bot's Trade list and arm it on the next. Nothing ever brought it back,
so over the days the source bot's list only shrank.

**Send back at the end of the day** (`on`, off by default; `handoff_return.json` next to the SMA DB,
`HANDOFF_RETURN_FILE`): every hand-off between Bots 1-4 is noted (`note`, called by `recovery.handoff`) with the
bot the stock first came from (its origin, kept through a chain Bot 1 → Bot 2 → Bot 3). After the close (`at`,
15:35 IST by default, and any time outside 09:00 to that time, so a missed evening is caught up the next morning
before the open) `run` moves each noted stock off the bot holding it and back onto its origin's Trade list
(`send_back`, source chip "Back from Bot N"), to wait there for its next cross. A stock still held by its bot waits
for the next pass. A LIVE origin keeps the one-LIVE-bot-per-stock rule. Fast-mover hand-offs to Bot 5 are not
noted (Bot 5 gives its stocks back itself, fast_bot.py). Arming only: no order is placed.

**Backtests** (`replay_snapshot` / `overrides`, read by replay.ReplayEngine): a replay plays one bot, so a hand-off
used to drop the stock from the run (and really move it on the live Trade lists). With "Play hand-offs" on (the
default), a handed-off stock stays in the backtest and trades from then on with the target bot's settings (Bot 2's,
with the chop recipe `recovery.HANDOFF_SETTINGS`; Bot 3's own), taken from the run's snapshot of those bots, so Bot
2 and Bot 3 can be judged after hours; the live Trade lists are not touched. With send-back on, each replayed day
starts with every stock on the replayed bot again.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger("sma.handoff_chain")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
POLL_SEC = 60.0
DEFAULT_AT = "15:35"
MARKET_START = dt.time(9, 0)
CHAIN_BOTS = (1, 2, 3, 4)  # Bot 5's fast-mover hand-off keeps its own give-back
PLAYED_BOTS = (2, 3)  # the bots a backtest can play a hand-off as

_lock = threading.Lock()


@dataclass
class Settings:
    on: bool = False
    at: str = DEFAULT_AT


def _path() -> Path:
    explicit = os.environ.get("HANDOFF_RETURN_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("handoff_return.json")
    return Path("handoff_return.json")


def _read() -> dict:
    try:
        data = json.loads(_path().read_text())
    except (OSError, ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def _hhmm(raw) -> dt.time | None:
    try:
        h, m = str(raw).strip().split(":")
        t = dt.time(int(h), int(m))
    except (ValueError, TypeError):
        return None
    return t if dt.time(15, 15) <= t <= dt.time(23, 59) else None


def load() -> Settings:
    raw = _read()
    at = raw.get("at")
    return Settings(on=bool(raw.get("on", False)), at=at if _hhmm(at) else DEFAULT_AT)


def save(on: bool, at: str | None = None) -> Settings:
    """Raises ValueError on a bad time."""
    if at is not None and _hhmm(at) is None:
        raise ValueError("the time must be HH:MM between 15:15 and 23:59 IST")
    with _lock:
        data = _read()
        data["on"] = bool(on)
        if at is not None:
            data["at"] = _hhmm(at).strftime("%H:%M")
        _write(data)
    return load()


def moved() -> dict[str, dict]:
    """{symbol: {"origin": bot, "on": bot, "at": iso time}} of every stock handed off and not yet sent back."""
    raw = _read().get("moved")
    return {str(k).upper(): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def _set_moved(rows: dict[str, dict]) -> None:
    data = _read()
    data["moved"] = rows
    _write(data)


def note(from_bot: int, to_bot: int, symbol: str) -> None:
    """A hand-off went through (recovery.handoff): remember where the stock first came from. Never raises."""
    try:
        a, b = int(from_bot), int(to_bot)
        name = (symbol or "").upper()
        if not name or a == b or a not in CHAIN_BOTS or b not in CHAIN_BOTS:
            return
        with _lock:
            rows = moved()
            origin = int(rows.get(name, {}).get("origin") or a)
            if b == origin:
                rows.pop(name, None)  # it came home
            else:
                rows[name] = {"origin": origin, "on": b, "at": dt.datetime.now(IST).isoformat(timespec="seconds")}
            _set_moved(rows)
    except Exception:  # noqa: BLE001 - bookkeeping must never stop a hand-off
        log.exception("could not note the hand-off of %s", symbol)


def due(now: dt.datetime, s: Settings) -> bool:
    """After the send-back time, or any time outside market hours (weekends, before 09:00)."""
    local = now.astimezone(IST)
    if local.weekday() >= 5:
        return True
    at = _hhmm(s.at) or _hhmm(DEFAULT_AT)
    return local.time() >= at or local.time() < MARKET_START


def send_back_one(symbol: str, row: dict, held) -> str:
    """Move one stock off the bot holding it, back onto its origin's Trade list. Returns what happened."""
    from database import session_factory
    from models import BotConfig
    import bots
    from strategy_engine import MAX_TRADE_SYMBOLS, apply_trade_sources, trade_names

    name = symbol.upper()
    origin, current = int(row.get("origin") or 1), int(row.get("on") or 0)
    if held(current, name):
        return f"{name}: still held on Bot {current}, sent back later"
    with session_factory()() as db:
        home = db.get(BotConfig, bots.config_id_for(origin))
        there = db.get(BotConfig, bots.config_id_for(current)) if current else None
        if home is None:
            return f"{name}: Bot {origin} is not set up"
        names = trade_names(home)
        if name not in names and len(names) >= MAX_TRADE_SYMBOLS:
            return f"{name}: Bot {origin}'s Trade list is full ({MAX_TRADE_SYMBOLS}), left on Bot {current}"
        if there is not None:
            left = [n for n in trade_names(there) if n != name]
            there.trade_symbols = ",".join(left)
            there.trade_sources = apply_trade_sources(there, {name: None}, left)
            db.commit()
        if (home.trading_mode or "PAPER").upper() == "LIVE":
            why = bots.live_conflict(origin, [name])
            if why:
                return f"{name}: off Bot {current}, not back on LIVE Bot {origin}: {why}"
        if name not in names:
            names.append(name)
        home.trade_symbols = ",".join(names)
        home.trade_sources = apply_trade_sources(home, {name: f"Back from Bot {current}"}, names)
        db.commit()
    return f"{name}: back on Bot {origin} from Bot {current}"


def send_back(engines: dict[int, object]) -> list[str]:
    """Every noted stock that is flat goes back to its origin bot. Returns one line per stock."""
    rows = moved()
    if not rows:
        return []

    def held(bot: int, name: str) -> bool:
        eng = engines.get(bot)
        return bool(eng is not None and (getattr(eng, "positions", {}) or {}).get(name) is not None)

    out, done = [], []
    for name, row in rows.items():
        try:
            text = send_back_one(name, row, held)
        except Exception as exc:  # noqa: BLE001
            log.exception("send back of %s failed", name)
            text = f"{name}: not sent back ({exc})"
        out.append(text)
        if "back on Bot" in text or "not set up" in text or "not back on LIVE" in text:
            done.append(name)
            origin = engines.get(int(row.get("origin") or 1))
            if origin is not None and "back on Bot" in text:
                origin.hold_for_next_cross([name])  # waits for its next cross there
    if done:
        with _lock:
            left = {k: v for k, v in moved().items() if k not in done}
            _set_moved(left)
    return out


def state() -> dict:
    s = load()
    return {**asdict(s), "moved": moved(), "last": _read().get("last") or []}


async def run(engines_fn, now_fn, poll: float = POLL_SEC) -> None:
    """Desk loop: send handed-off stocks back once they are due. Never raises."""
    while True:
        try:
            s = load()
            if s.on and moved() and due(now_fn(), s):
                lines = await asyncio.to_thread(send_back, engines_fn())
                if lines:
                    log.info("hand-off send back: %s", "; ".join(lines))
                    with _lock:
                        data = _read()
                        data["last"] = {"at": dt.datetime.now(IST).isoformat(timespec="seconds"), "lines": lines[-50:]}
                        _write(data)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("hand-off send back pass failed")
        await asyncio.sleep(poll)


# ---- backtests -------------------------------------------------------------------


def replay_snapshot(bot: int) -> dict:
    """What a new replay of `bot` needs to play its hand-offs: Bot 2's and Bot 3's strategy fields and per-stock
    settings, and whether stocks go back to the replayed bot each day."""
    from fast_bot import _column_default
    from database import session_factory
    from models import BotConfig
    import bots
    from strategy_engine import STOCK_FIELDS, stock_settings

    out: dict = {"bots": {}, "return_eod": load().on}
    with session_factory()() as db:
        for n in PLAYED_BOTS:
            if n == int(bot):
                continue
            row = db.get(BotConfig, bots.config_id_for(n))
            if row is None:
                continue
            fields = {}
            for name in STOCK_FIELDS:
                value = getattr(row, name, None)
                if value is None:
                    value = _column_default(name)
                if value is not None:
                    fields[name] = value
            out["bots"][str(n)] = {"fields": fields, "stocks": stock_settings(row)}
    return out


def overrides(snapshot: dict | None, to_bot: int, symbol: str, own_bot: int) -> dict:
    """The settings a handed-off stock plays with in a backtest, as that stock's override."""
    import recovery
    from strategy_engine import STOCK_FIELDS

    name = (symbol or "").upper()
    merged: dict = {}
    if int(to_bot) != int(own_bot):
        part = ((snapshot or {}).get("bots") or {}).get(str(int(to_bot))) or {}
        merged = {**(part.get("fields") or {}), **((part.get("stocks") or {}).get(name) or {})}
    if int(to_bot) == recovery.HANDOFF_BOT:
        merged = {**merged, **recovery.HANDOFF_SETTINGS}
    return {k: v for k, v in merged.items() if k in STOCK_FIELDS and v is not None}


def tag(why: str, bot: int) -> str:
    """Entry reason of a trade a backtest opened on another bot's settings: "SMA_CROSS@B2"."""
    return f"{why}@B{int(bot)}"


def played_as(entry_reason: str | None) -> int | None:
    """The bot a backtest trade was played as ("…@B2" → 2), else None."""
    text = entry_reason or ""
    if "@B" not in text:
        return None
    try:
        return int(text.rsplit("@B", 1)[1])
    except ValueError:
        return None
