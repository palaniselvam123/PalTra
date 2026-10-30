"""Fast-mover hand-off (fast_bot.py): a held stock that starts running moves to Bot 5 with a bigger size.

Off by default (`on`), PAPER and LIVE. Bots 1-4 trade many stocks with a small quantity; when one of
the stocks a source bot holds moves fast **in that trade's favour**, the watcher (`FastBot.run`,
started in `boot_engine` on the desk only, every `POLL_SEC` 5 s while NSE is open):

1. checks Bot 5 can take it (`ready`: RUNNING, market hours, before its entry cut-off and square-off,
   under its trade cap, not already holding the stock, and both in the same mode: a LIVE Bot 5 never takes
   a practice trade, and a LIVE trade is never cut for a PAPER Bot 5);
2. closes the source bot's trade (`close_symbol`, exit reason `FAST_MOVE_HANDOFF`; a LIVE stop is
   cancelled first), and in LIVE waits until Groww shows no shares (`FLAT_WAIT_SEC`);
3. moves the stock off the source bot's Trade list onto Bot 5's (`recovery.handoff(to_bot=5)`, source
   chip "Handoff from Bot N"; the one-LIVE-bot-per-stock rule applies);
4. has Bot 5 enter the same side at once (`StrategyEngine.direct_order`, entry reason `FAST_MOVE`):
   no SMA cross, VWAP / RSI or other entry check is read; Bot 5's own quantity, stop and exits run
   the trade.
5. after `bot5_trades` (1) Bot 5 trades on the stock (0 = no limit: it stays on Bot 5 for the day), Bot 5
   takes no more entries on it (`gate`, `StrategyEngine.entry_gate`) and, once Bot 5 is flat on it, the
   stock goes back to the bot it came from (`give_back` → `recovery.handoff(5, to_bot=source)`), which trades
   it on its own settings and waits for its next cross. It is not moved again that day. Memory only: after
   a restart a moved stock stays on Bot 5.

What counts as fast (`rule`), on the bots' own last prices (no extra Groww call):

* `MOVE`: the price went at least `pct` % the trade's way within `minutes` (from the lowest price in
  that window for a long, the highest for a short);
* `SPEED`: over the last `speed_minutes` the price moved at least `speed` % per minute the trade's way;
* `EITHER`: whichever comes first.

and only while the trade is already at least `min_profit_pct` (0.3) % in profit from its entry (`in_profit`; 0 =
any trade), so a losing trade that only bounced off its low is never moved.

At most `max_day` hand-offs per IST day. **Replays too** (`replay_snapshot` / `bot5_overrides`, played by
`replay.ReplayEngine._fast_replay`): a replay of a source bot with this switch on cuts a held stock that moves
fast its way (`FAST_MOVE_HANDOFF`) and, in the same run, opens the same side at once with Bot 5's settings
(its quantity, stop and exits, until its `bot5_trades` are done, then the replayed bot's own settings again;
entry reason `FAST_MOVE`), so the idea can be
checked on past days. The run keeps a copy of these settings and Bot 5's, so Resume plays the same. Settings in `fast_bot.json` next to the SMA DB
(`FAST_BOT_FILE`). Bot 5 is never a source. Replays and research never hand off.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from groww_client import IST, market_is_open

log = logging.getLogger("sma.fastbot")

TARGET_BOT = 5
SOURCE_BOTS = (1, 2, 3, 4)
RULES = ("MOVE", "SPEED", "EITHER")
POLL_SEC = 5
FLAT_WAIT_SEC = 6
EXIT_REASON = "FAST_MOVE_HANDOFF"
ENTRY_REASON = "FAST_MOVE"
MAX_PCT = 10.0
MAX_MINUTES = 30
MAX_SPEED = 2.0
MAX_BOT5_TRADES = 20


@dataclass
class Settings:
    on: bool = False
    rule: str = "MOVE"
    pct: float = 0.5
    minutes: int = 3
    speed: float = 0.15
    speed_minutes: int = 5
    sources: list[int] = field(default_factory=lambda: list(SOURCE_BOTS))
    max_day: int = 10
    # Only a trade already this much in profit moves (% of its entry price, before charges); 0 = any trade.
    min_profit_pct: float = 0.3
    # Bot 5 trades on a moved stock before it goes back to its own bot; 0 = it stays on Bot 5 all day.
    bot5_trades: int = 1


def settings_path() -> Path:
    explicit = os.environ.get("FAST_BOT_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("fast_bot.json")
    return Path("fast_bot.json")


def clean(raw: dict) -> Settings:
    s = Settings()

    def num(key, cast, low, high):
        value = raw.get(key)
        if value is None:
            return getattr(s, key)
        try:
            return max(low, min(high, cast(value)))
        except (TypeError, ValueError):
            return getattr(s, key)

    s.on = bool(raw.get("on", s.on))
    rule = str(raw.get("rule") or s.rule).upper()
    s.rule = rule if rule in RULES else s.rule
    s.pct = num("pct", float, 0.05, MAX_PCT)
    s.minutes = num("minutes", int, 1, MAX_MINUTES)
    s.speed = num("speed", float, 0.01, MAX_SPEED)
    s.speed_minutes = num("speed_minutes", int, 1, MAX_MINUTES)
    s.max_day = num("max_day", int, 1, 100)
    s.min_profit_pct = num("min_profit_pct", float, 0.0, MAX_PCT)
    s.bot5_trades = num("bot5_trades", int, 0, MAX_BOT5_TRADES)
    sources = raw.get("sources")
    if isinstance(sources, list):
        picked = []
        for b in sources:
            try:
                n = int(b)
            except (TypeError, ValueError):
                continue
            if n in SOURCE_BOTS and n not in picked:
                picked.append(n)
        s.sources = sorted(picked)
    return s


def load() -> Settings:
    try:
        return clean(json.loads(settings_path().read_text()))
    except FileNotFoundError:
        return Settings()
    except Exception:  # noqa: BLE001
        log.exception("could not read the fast-mover hand-off settings")
        return Settings()


def save(raw: dict) -> Settings:
    s = clean({**asdict(load()), **{k: v for k, v in raw.items() if v is not None}})
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s)))
    return s


def _move_hit(hist, direction: str, now: float, s: Settings) -> str | None:
    """MOVE: at least `pct` % the trade's way within `minutes`, from the window's extreme."""
    window = [p for t, p in hist if now - t <= s.minutes * 60]
    if len(window) < 2:
        return None
    price = window[-1]
    if direction == "LONG":
        low = min(window)
        move = (price / low - 1) * 100 if low > 0 else 0.0
    else:
        high = max(window)
        move = (1 - price / high) * 100 if high > 0 else 0.0
    if move >= s.pct:
        return f"{move:+.2f}% its way within {s.minutes} min"
    return None


def _speed_hit(hist, direction: str, now: float, s: Settings) -> str | None:
    """SPEED: over the last `speed_minutes`, at least `speed` % per minute the trade's way."""
    span = s.speed_minutes * 60
    start = None
    for t, p in hist:
        if now - t <= span:
            start = (t, p)
            break
    if start is None:
        return None
    t0, p0 = start
    elapsed = now - t0
    if elapsed < 60 or p0 <= 0:
        return None
    price = hist[-1][1]
    speed = (price / p0 - 1) * 100 / (elapsed / 60)
    if direction == "SHORT":
        speed = -speed
    if speed >= s.speed:
        return f"{speed:.3f}%/min its way over {int(round(elapsed / 60))} min"
    return None


def profit_pct(direction: str, entry: float | None, price: float) -> float | None:
    """The open trade's move from its entry, in % of the entry price, signed with the trade (+ = in profit)."""
    if not entry or entry <= 0 or not price:
        return None
    move = (price - entry) / entry * 100
    return move if direction == "LONG" else -move


def in_profit(pos, price: float, s: Settings) -> bool:
    """The trade is at least `min_profit_pct` in profit (any trade when it is 0). A losing trade never moves."""
    if s.min_profit_pct <= 0:
        return True
    gain = profit_pct(pos.direction, getattr(pos, "entry_price", None), price)
    return gain is not None and gain >= s.min_profit_pct


def fast(hist, direction: str, now: float, s: Settings) -> str | None:
    """The reason the stock counts as a fast mover the trade's way, or None."""
    if direction not in ("LONG", "SHORT") or not hist:
        return None
    if s.rule in ("MOVE", "EITHER"):
        hit = _move_hit(hist, direction, now, s)
        if hit:
            return hit
    if s.rule in ("SPEED", "EITHER"):
        return _speed_hit(hist, direction, now, s)
    return None


def ready(target, symbol: str, source_live: bool) -> str | None:
    """Why Bot 5 cannot take the stock now, or None when it can (checked before the source trade is cut)."""
    from strategy_engine import _entry_cutoff, _parse_hhmm

    if target is None:
        return f"Bot {TARGET_BOT} is not running"
    if target.status != "RUNNING":
        return f"Bot {TARGET_BOT} is {target.status}, not RUNNING"
    if symbol in target.positions:
        return f"Bot {TARGET_BOT} already holds {symbol}"
    cfg = target.load_config()
    clock = target._now()  # noqa: SLF001
    if not market_is_open(clock):
        return "market is closed"
    if clock.time() >= _parse_hhmm(cfg.square_off_time) or clock.time() >= _entry_cutoff(cfg):
        return f"past Bot {TARGET_BOT}'s entry cut-off"
    if target.trades_today >= int(cfg.max_trades_per_day):
        return f"Bot {TARGET_BOT}'s trade cap is reached"
    target_live = (cfg.trading_mode or "PAPER").upper() == "LIVE"
    if target_live and not source_live:
        return f"Bot {TARGET_BOT} is LIVE and this trade was practice money"
    if source_live and not target_live:
        # Never close a real trade only to open a practice one in its place.
        return f"this trade is LIVE and Bot {TARGET_BOT} is PAPER (switch Bot {TARGET_BOT} to LIVE, or the source bot to PAPER)"
    return None


class FastBot:
    """Price history of the held stocks and the day's hand-offs (memory only)."""

    def __init__(self) -> None:
        self.hist: dict[str, deque] = {}
        self.busy: set[str] = set()
        self.day: str | None = None
        self.moved_today: list[str] = []
        self.recent: list[dict] = []
        # Stocks on Bot 5 by a hand-off: {symbol: {"from": bot, "live": bool, "opened": Bot 5 entries}}.
        self.handed: dict[str, dict] = {}
        self._task: asyncio.Task | None = None

    def _roll(self, now: float) -> None:
        today = dt.datetime.fromtimestamp(now, IST).date().isoformat()
        if self.day != today:
            self.day = today
            self.moved_today = []
            for h in self.handed.values():
                # Yesterday's hand-off ends: the stock goes back at the first pass with Bot 5 flat on it.
                h["due"] = True

    def gate(self, engine, symbol: str) -> str | None:
        """Bot 5's last check before an entry (`StrategyEngine.entry_gate`): a moved stock takes at most
        `bot5_trades` Bot 5 entries; each one let through is counted."""
        if int(getattr(engine, "bot_id", 0)) != TARGET_BOT:
            return None
        h = self.handed.get((symbol or "").upper())
        if h is None:
            return None
        limit = load().bot5_trades
        if h.get("due") or (limit and h["opened"] >= limit):
            return f"Bot {TARGET_BOT} has made its {limit} trade(s) on {symbol}: it goes back to Bot {h['from']}"
        h["opened"] += 1
        return None

    def give_back(self, target, s: Settings, now: float) -> list[str]:
        """Moved stocks whose Bot 5 trades are done (and Bot 5 is flat on them) go back to their own bot."""
        import recovery

        done = []
        for name, h in list(self.handed.items()):
            if not (h.get("due") or (s.bot5_trades and h["opened"] >= s.bot5_trades)):
                continue
            if target is not None and target.positions.get(name) is not None:
                continue
            del self.handed[name]
            back = recovery.handoff(TARGET_BOT, name, h["live"], to_bot=h["from"], chop=False)
            result = f"back to Bot {h['from']} after {h['opened']} Bot {TARGET_BOT} trade(s): {back}"
            self._record(now, symbol=name, from_bot=TARGET_BOT, why="trades done", result=result)
            if target is not None:
                target._signals[name] = f"{name} {result}"  # noqa: SLF001
                target._alert(f"{name} {result}")  # noqa: SLF001
            done.append(f"{name} {result}")
        return done

    def note(self, symbol: str, price: float, now: float, s: Settings) -> deque:
        keep = max(s.minutes, s.speed_minutes) * 60 + POLL_SEC * 2
        q = self.hist.setdefault(symbol, deque())
        q.append((now, float(price)))
        while q and now - q[0][0] > keep:
            q.popleft()
        return q

    def _record(self, now: float, **row) -> None:
        stamp = dt.datetime.fromtimestamp(now, IST).isoformat(timespec="seconds")
        self.recent.insert(0, {"at": stamp, **row})
        del self.recent[50:]

    def candidates(self, engines: dict[int, object], s: Settings, now: float) -> list[tuple[object, str, object, str]]:
        """(source engine, stock, position, why) for each held stock moving fast its trade's way."""
        out = []
        seen: set[str] = set()
        for bot in s.sources:
            eng = engines.get(bot)
            if eng is None or bot == TARGET_BOT:
                continue
            for name, pos in list(eng.positions.items()):
                if pos is None:
                    continue
                price = eng._ltps.get(name)  # noqa: SLF001
                if not price:
                    continue
                seen.add(name)
                q = self.note(name, price, now, s)
                if name in self.busy or name in self.moved_today or not in_profit(pos, price, s):
                    continue
                why = fast(q, pos.direction, now, s)
                if why:
                    out.append((eng, name, pos, why))
        for gone in [k for k in self.hist if k not in seen]:
            self.hist.pop(gone, None)
        return out

    async def move(self, src, symbol: str, pos, why: str, target, s: Settings, now: float) -> str:
        """Cut the source trade and have Bot 5 open the same side at once. Returns what happened."""
        import recovery
        from strategy_engine import ForceRefused

        source_bot = int(getattr(src, "bot_id", 1))
        source_live = (pos.mode or "PAPER").upper() == "LIVE"
        direction = pos.direction
        head = f"{symbol} fast move on Bot {source_bot} ({why})"
        reason = ready(target, symbol, source_live)
        if reason is None and len(self.moved_today) >= s.max_day:
            reason = f"{s.max_day} hand-offs today already"
        if reason is None and source_live and pos.stop_active and not pos.sl_order_id:
            reason = "the LIVE position was restored without its stop id; left to that stop"
        if reason is not None:
            self._record(now, symbol=symbol, from_bot=source_bot, why=why, result=f"not moved: {reason}")
            return f"{head}: not moved — {reason}"
        self.busy.add(symbol)
        try:
            try:
                closed = await src.close_symbol(symbol, reason=EXIT_REASON)
            except ForceRefused as exc:
                result = f"not moved: Bot {source_bot}'s trade could not be closed ({exc})"
                self._record(now, symbol=symbol, from_bot=source_bot, why=why, result=result)
                return f"{head}: {result}"
            if source_live:
                flat = False
                for _ in range(FLAT_WAIT_SEC):
                    if await src._groww_net(symbol) == 0:  # noqa: SLF001
                        flat = True
                        break
                    await asyncio.sleep(1)
                if not flat:
                    result = f"{closed}; not moved: Groww still shows shares"
                    self._record(now, symbol=symbol, from_bot=source_bot, why=why, result=result)
                    src._alert(f"{head}: {result}")  # noqa: SLF001
                    return f"{head}: {result}"
            moved = recovery.handoff(source_bot, symbol, source_live, to_bot=TARGET_BOT)
            self.moved_today.append(symbol)
            if moved.startswith("moved"):
                self.handed[symbol] = {"from": source_bot, "live": source_live, "opened": 0}
            else:
                result = f"{closed}; {moved}"
                self._record(now, symbol=symbol, from_bot=source_bot, why=why, result=result)
                src._alert(f"{head}: {result}")  # noqa: SLF001
                return f"{head}: {result}"
            signal = "BULLISH" if direction == "LONG" else "BEARISH"
            word = "buys" if direction == "LONG" else "sells"
            try:
                opened = await target.direct_order(
                    symbol, signal, why=ENTRY_REASON, note=f"fast mover from Bot {source_bot}: {why}"
                )
            except ForceRefused as exc:
                opened = f"not ordered: {exc}"
            except Exception as exc:  # noqa: BLE001 - the stock stays armed on Bot 5 for its next cross
                log.exception("fast-mover order failed for %s", symbol)
                opened = f"not ordered: {exc}"
            result = f"Bot {source_bot} trade cut; Bot {TARGET_BOT} {word} at once — {opened}"
            self._record(now, symbol=symbol, from_bot=source_bot, why=why, side=direction, result=result)
            text = f"{head}: {result}"
            target._signals[symbol] = text  # noqa: SLF001
            target._alert(text)  # noqa: SLF001
            return text
        finally:
            self.busy.discard(symbol)

    async def pass_once(self, engines: dict[int, object], now: float | None = None) -> list[str]:
        now = time.time() if now is None else now
        self._roll(now)
        s = load()
        if not s.on:
            self.hist.clear()
            return []
        target = engines.get(TARGET_BOT)
        if target is not None and getattr(target, "entry_gate", None) is None:
            target.entry_gate = self.gate
        done = self.give_back(target, s, now)
        for src, name, pos, why in self.candidates(engines, s, now):
            done.append(await self.move(src, name, pos, why, target, s, now))
        return done

    async def run(self, engines: Callable[[], dict[int, object]]) -> None:
        while True:
            try:
                if market_is_open(dt.datetime.now(IST)):
                    await self.pass_once(engines())
                else:
                    self.hist.clear()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("fast-mover hand-off pass")
            await asyncio.sleep(POLL_SEC)

    def start(self, engines: Callable[[], dict[int, object]]) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run(engines))

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def snapshot(self) -> dict:
        return {
            "day": self.day,
            "moved_today": list(self.moved_today),
            "recent": list(self.recent),
            "on_bot5": {k: {"from_bot": v["from"], "trades": v["opened"]} for k, v in self.handed.items()},
        }


watcher = FastBot()


# ---- replays ------------------------------------------------------------------------------


def _bot5_row():
    from bots import config_id_for
    from database import session_factory
    from models import BotConfig

    with session_factory()() as db:
        row = db.get(BotConfig, config_id_for(TARGET_BOT))
        if row is not None:
            db.expunge(row)
        return row


def replay_snapshot(bot: int) -> dict | None:
    """What a new replay of `bot` needs to play the hand-off: these settings and Bot 5's strategy
    settings. None when the switch is off, `bot` is not a source, or Bot 5 has no settings yet."""
    from strategy_engine import STOCK_FIELDS, stock_settings

    s = load()
    if not s.on or int(bot) not in s.sources or int(bot) == TARGET_BOT:
        return None
    row = _bot5_row()
    if row is None:
        return None
    fields = {name: getattr(row, name) for name in STOCK_FIELDS if getattr(row, name, None) is not None}
    return {"settings": asdict(s), "bot5": fields, "bot5_stocks": stock_settings(row)}


def bot5_overrides(snapshot: dict | None, symbol: str) -> dict:
    """Bot 5's settings for one stock (its own per-stock settings on top), as a replay's stock override."""
    from strategy_engine import STOCK_FIELDS

    if not snapshot:
        return {}
    name = (symbol or "").upper()
    merged = {**(snapshot.get("bot5") or {}), **((snapshot.get("bot5_stocks") or {}).get(name) or {})}
    return {k: v for k, v in merged.items() if k in STOCK_FIELDS and v is not None}
