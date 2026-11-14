"""Replay a past NSE session on real Groww 1-minute candles, practice only.

A replay runs a second, separate StrategyEngine:

* its clock is the replayed day (``ReplayFeed.clock``), moved forward by
  ``ReplaySession`` at 1×, 10×, 60× or 300×;
* its quotes come from candles downloaded once from Groww's history API.
  Inside each minute the price walks open → low → high → close (or
  open → high → low → close for a red candle), so stops and targets are hit
  the way they would be intraday;
* its broker is ``ReplayBroker``: it fills locally and has no code path to
  the Groww order API at all, whatever mode the desk is in;
* its trades are tagged ``REPLAY`` with the replayed date, so they never
  touch today's PAPER or LIVE books, caps or P&L;
* it sends no Telegram/WhatsApp alerts.

The live engine keeps running untouched while a replay plays.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

import numpy as np
import pandas as pd
from sqlalchemy import case, func

import high52
from candle_history import HistoryError, fetch_frame
from database import session_factory
from groww_client import IST, OrderAck, market_is_open
from models import BotConfig, ReplayRun, ReviewLog, TradeLog
import arm_log
from strategy_engine import (
    MAX_TRADE_SYMBOLS,
    ForceRefused,
    StrategyEngine,
    settings_for,
    settings_snapshot,
    stock_settings,
    trade_names,
)
from tick_sizes import round_price
import all_bots_guard
import fast_bot
import handoff_chain
import handoff_log
import open_pnl_log
import recovery
import auto_arm
from scalp_picks import PickRule, picks_for_day, resumable as pick_resumable, rule_from_dict

SPEEDS = (1, 10, 60, 300)
# Longest range one run may cover, in calendar days (about 22 trading days).
# 45 calendar days: room for 30 trading days (six weeks with holidays).
MAX_RANGE_DAYS = 45
# Settings saved with each run, so runs with different strategies compare.
# No trade cap: a replay has none (REPLAY_TRADE_CAP).
SESSION_OPEN = dt.time(9, 15)
SESSION_END = dt.time(15, 30)
# Days of candles before the replayed day so SMA 21, ATR 14 and the day
# change are already formed at 09:15.
WARMUP_DAYS = 4
# Bars handed to the engine per quote: one full session plus 125 bars of the
# previous one, enough for SMA 21, ATR 14, RSI, the volume average and the
# previous close. More only slows every tick.
FRAME_BARS = 500
# Longer candles need more 1-minute bars: the slow SMA plus about a session of
# candles that already have it, so the chart is not blank at its left edge.
FRAME_EXTRA_CANDLES = 40


def frame_bars(cfg) -> int:
    """1-minute bars handed to the engine per quote for a stock with these settings."""
    from candles import candle_minutes

    try:
        slow = max(1, int(getattr(cfg, "sma_slow", None) or 21))
    except (TypeError, ValueError):
        slow = 21
    return max(FRAME_BARS, candle_minutes(cfg) * (slow + FRAME_EXTRA_CANDLES))
# Share of each real second the replays together may spend ticking, so the
# live engine and the API stay responsive (one Python process, one event loop).
# Several bots replaying at once split it. Past this a replay plays slower.
CPU_SHARE = 0.4

#: Replays playing right now (one per bot can play at once); they split CPU_SHARE.
_PLAYING: set[int] = set()
LOOP_SECONDS = 0.2
# Replay seconds per engine tick. Six ticks a minute land on the candle's
# open, both extremes and its close.
STEP_SECONDS = 10
# A replay has no daily trade cap: it is practice on a past day, and the cap
# exists to protect a real session. The daily loss limit still applies.
REPLAY_TRADE_CAP = 1_000_000
COLUMNS = ("ts", "open", "high", "low", "close", "volume")


def _path_price(o: float, h: float, low: float, c: float, f: float) -> tuple[float, float, float]:
    """(price, high so far, low so far) at fraction f ∈ [0, 1] of the minute."""
    first, second = (low, h) if c >= o else (h, low)
    points = (o, first, second, c)
    f = min(max(f, 0.0), 1.0)
    pos = f * 3.0
    seg = min(int(pos), 2)
    frac = pos - seg
    price = points[seg] + (points[seg + 1] - points[seg]) * frac
    visited = list(points[: seg + 1]) + [price]
    return price, max(visited), min(visited)


def _to_next_grid(clock: dt.datetime) -> float:
    """Replay seconds from `clock` to the next STEP_SECONDS grid point.

    The grid is anchored on whole minutes (:00, :10, … for a 10 s step), so
    every replay of a day samples each candle at the same instants.
    """
    into = (clock.second + clock.microsecond / 1_000_000) % STEP_SECONDS
    return float(STEP_SECONDS) - into


class ReplayFeed:
    """Candles for each replayed stock, cut at the replay clock."""

    def __init__(self, frames: dict[str, pd.DataFrame], clock: dt.datetime, bars: dict[str, int] | None = None):
        self.clock = clock
        # 1-minute bars per quote, per stock (frame_bars); FRAME_BARS when not given.
        self._bars = {k.upper(): int(v) for k, v in (bars or {}).items()}
        self._arrays: dict[str, dict[str, np.ndarray]] = {}
        for symbol, frame in frames.items():
            frame = frame.sort_values("ts").drop_duplicates("ts")
            self._arrays[symbol.upper()] = {
                col: frame[col].to_numpy(dtype="float64") for col in COLUMNS
            }

    @property
    def symbols(self) -> list[str]:
        return list(self._arrays)

    def quote(self, symbol: str) -> tuple[float, pd.DataFrame]:
        data = self._arrays.get((symbol or "").upper())
        if data is None:
            raise RuntimeError(f"{symbol} is not part of this replay")
        now = self.clock.astimezone(IST)
        minute = now.replace(second=0, microsecond=0)
        minute_ts = float(minute.timestamp())
        f = (now - minute).total_seconds() / 60.0
        ts = data["ts"]
        closed_end = int(np.searchsorted(ts, minute_ts, side="left"))
        start = max(0, closed_end - self._bars.get((symbol or "").upper(), FRAME_BARS))
        cols = {col: data[col][start:closed_end] for col in COLUMNS}
        has_bar = closed_end < len(ts) and ts[closed_end] == minute_ts
        # Groww's volume is the session's running total, so the forming bar's
        # volume climbs from the last closed print toward this bar's print.
        same_day = closed_end and int(ts[closed_end - 1] + 19_800) // 86_400 == int(minute_ts + 19_800) // 86_400
        prev_cum = float(data["volume"][closed_end - 1]) if same_day else 0.0
        if has_bar:
            o, h, low, c = (data[k][closed_end] for k in ("open", "high", "low", "close"))
            price, hi, lo = _path_price(o, h, low, c, f)
            cum = prev_cum + max(0.0, float(data["volume"][closed_end]) - prev_cum) * f
            forming = (minute_ts, o, hi, lo, price, cum)
        else:
            # No trade in this minute (or outside the session): a flat bar at
            # the last price keeps "the last row is the forming bar" true.
            price = float(cols["close"][-1]) if closed_end else 0.0
            forming = (minute_ts, price, price, price, price, prev_cum)
        frame = pd.DataFrame(
            {col: np.append(cols[col], value) for col, value in zip(COLUMNS, forming)}
        )
        frame["ts"] = frame["ts"].astype("int64")
        frame["volume"] = frame["volume"].astype("int64")
        return float(price), frame


class LocalFills:
    """Fills at the quoted price, locally. There is deliberately no Groww SDK in here.

    Shared by the replay and the research desk: neither can reach the Groww
    order API, whatever mode the live desk is in.
    """

    mode = "PAPER"
    token = ""
    ORDER_PREFIX = "LOCAL"

    def __init__(self):
        self.last_error = ""
        self._seq = 0
        self._orders: dict[str, tuple[str, float | None]] = {}

    def set_mode(self, mode: str, token: str | None = None) -> None:  # noqa: ARG002
        self.mode = "PAPER"

    def adopt_saved_session(self, *, force: bool = False) -> None:  # noqa: ARG002
        return None

    def _id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:06d}"

    async def place_entry(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:  # noqa: ARG002
        px = round_price(symbol, ltp)
        oid = self._id(self.ORDER_PREFIX)
        self._orders[oid] = ("FILLED", px)
        return OrderAck(oid, "FILLED", px)

    async def place_exit(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:  # noqa: ARG002
        return await self.place_entry(symbol, side, qty, ltp)

    async def place_sl(self, symbol: str, side: str, qty: int, trigger: float) -> OrderAck:  # noqa: ARG002
        oid = self._id(f"{self.ORDER_PREFIX}SL")
        self._orders[oid] = ("TRIGGER_PENDING", None)
        return OrderAck(oid, "TRIGGER_PENDING", None)

    async def cancel_order(self, order_id: str) -> None:
        if order_id in self._orders and self._orders[order_id][0] != "FILLED":
            self._orders[order_id] = ("CANCELLED", None)

    async def get_order_status(self, order_id: str) -> str:
        return self._orders.get(order_id, ("", None))[0]

    async def read_order(self, order_id: str) -> tuple[str, float | None]:
        return self._orders.get(order_id, ("", None))

    async def net_quantity(self, symbol: str) -> int | None:  # noqa: ARG002
        return None


class ReplayBroker(LocalFills):
    """Replayed quotes, local fills."""

    ORDER_PREFIX = "REPLAY"

    def __init__(self, feed: ReplayFeed):
        super().__init__()
        self.feed = feed
        self.data_source = "REPLAY"

    async def refresh(self, symbol: str) -> tuple[float, pd.DataFrame, str]:
        ltp, frame = self.feed.quote(symbol)
        return ltp, frame, "REPLAY"


class ReplayEngine(StrategyEngine):
    """The SMA bot on a replayed day: its own clock, books and broker."""

    records_ticks = False  # its prices are walked from minute candles
    # The all-bots day target / stop loss / square-off time (all_bots_guard.py), judged on this
    # replayed day's own P&L and clock, so a backtest shows what the rule would have done.
    day_guard = True
    _guard_minute: dt.datetime | None = None
    uses_wallet = True  # a replay takes margin from the bots' practice wallet (owner's choice)

    def __init__(
        self,
        feed: ReplayFeed,
        symbols: list[str],
        run_id: int | None = None,
        bot: int = 1,
        settings: dict | None = None,
        arm_windows: dict[str, list[tuple[int, int]]] | None = None,
        fast: dict | None = None,
        chain: dict | None = None,
        on_bot: dict[str, int] | None = None,
    ):
        self.feed = feed
        # Play the hand-offs (handoff_chain.py): a stock handed to Bot 2 / Bot 3 stays in the run and trades
        # with that bot's settings from the run's snapshot. None = the old way (dropped from the run).
        self.chain = chain
        self._on_bot: dict[str, int] = {}
        self._chain_fields: dict[str, dict] = {}
        self._chain_orders: list[tuple[str, str]] = []
        self.chain_log: list[dict] = []
        # Fast-mover hand-off to Bot 5 (fast_bot.replay_snapshot): None = off for this run.
        self.fast = fast
        self._fast_watch = None
        self._fast_moved: dict[str, dict] = {}
        # Bot 5 entries on each moved stock (`bot5_trades`), and the stocks already sent back to this bot.
        self._fast_opened: dict[str, int] = {}
        self._fast_back: set[str] = set()
        # Each fast move this day (time, stock, side, why, what happened), for the Bot 5 tab's view of the replay.
        self.fast_log: list[dict] = []
        if fast:
            self.entry_gate = self._fast_gate
        # "Replay as armed" (arm_log.py): each stock trades only while it was armed live that
        # day, {symbol: [(start, end) epoch seconds]}. None = every replayed stock all day.
        self.arm_windows = arm_windows
        self._armed_before: set[str] | None = None
        self.run_id = run_id
        # A resumed run plays the settings it was started with, not today's.
        self.saved_settings = dict(settings) if settings else None
        # Whose settings it plays: bot 1 (the main desk) or bots 2-4 (settings rows 3-5).
        # Only read here; a replay never writes to a bot's settings row.
        self.bot_id = int(bot)
        self.config_id = 1 if self.bot_id == 1 else self.bot_id + 1
        self.replay_symbols = [s.upper() for s in symbols][:MAX_TRADE_SYMBOLS]
        # Set by ReplaySession: a "Recovered" exit moves the stock to Bot 2 (recovery.py).
        self.on_handoff = None
        super().__init__(broker=ReplayBroker(feed))
        if chain is not None:
            self.breakout_arrival = self._chain_arrival
            self.handoff_order = self._chain_order
            for name, to_bot in (on_bot or {}).items():
                self._play_as(name, int(to_bot))
        self._session_date = feed.clock.date().isoformat()
        with session_factory()() as db:
            top = db.query(TradeLog.id).order_by(TradeLog.id.desc()).first()
        self._min_trade_id = int(top[0]) if top else 0

    def _now(self) -> dt.datetime:
        return self.feed.clock

    def _armed_now(self) -> list[str]:
        if self.arm_windows is None:
            return self.replay_symbols
        when = int(self._now().timestamp())
        return [s for s in self.replay_symbols if arm_log.armed_at(self.arm_windows.get(s, []), when)]

    async def tick(self, now: dt.datetime) -> None:
        if self.arm_windows is not None:
            armed = set(self._armed_now())
            if self._armed_before is not None:
                # Armed now, as it was live: a cross already on the tape is not traded (main.py's arm route).
                for name in armed - self._armed_before:
                    self._skip_cross_until[name] = None
            self._armed_before = armed
        await super().tick(now)
        while self._chain_orders:
            # Bot 2 orders at once after a hand-off (handoff_now), played here after the tick that moved it.
            name, direction = self._chain_orders.pop(0)
            signal = "BULLISH" if direction == "LONG" else "BEARISH"
            try:
                opened = await self.direct_order(name, signal, why="HANDOFF", note=f"hand-off to Bot {self._on_bot.get(name, self.bot_id)}")
            except ForceRefused as exc:
                opened = f"not ordered: {exc}"
            self._signals[name] = f"{name}: Bot {self._on_bot.get(name, self.bot_id)} at once — {opened}"
        if self.fast and self.status == "RUNNING":
            await self._fast_replay(now)
        if self.day_guard and self.status != "HALTED":
            await self._check_day_guard(now)

    async def _check_day_guard(self, now: dt.datetime) -> None:
        """Once per replayed minute: this day's P&L (closed + open, before charges) against the
        all-bots target / stop loss, and the replay clock against the all-bots square-off time.
        A hit squares off and stops this replayed day, as the live guard does to every bot."""
        minute = now.replace(second=0, microsecond=0)
        if minute == self._guard_minute:
            return
        self._guard_minute = minute
        with session_factory()() as db:
            row = db.get(BotConfig, 1)
            if row is None:
                return
            target, stop = all_bots_guard.limits(row)
            at = all_bots_guard.squareoff_at(row)
        if not (target or stop or at):
            return
        amount = all_bots_guard.total([self])
        kind = all_bots_guard.verdict(amount, target, stop, now, at)
        if kind:
            await all_bots_guard.trip([self], kind, amount, target, stop, at)

    async def _fast_replay(self, now: dt.datetime) -> None:
        """The fast-mover hand-off on a replayed day (fast_bot.py): a held stock moving fast its way is cut
        (FAST_MOVE_HANDOFF) and opened again the same side at once with Bot 5's settings, which it keeps for
        the rest of this day (FAST_MOVE). Each stock once a day, at most `max_day` a day."""
        raw = dict(self.fast.get("settings") or {})
        # A run started before the profit check plays as it did then (Resume keeps the result).
        raw.setdefault("min_profit_pct", 0.0)
        raw.setdefault("bot5_trades", 0)
        raw.setdefault("entry_checks", False)  # a run saved before the check plays without it
        settings = fast_bot.clean(raw)
        if not settings.on or self.bot_id not in settings.sources:
            return
        if self._fast_watch is None:
            self._fast_watch = fast_bot.FastBot()
        limit = settings.bot5_trades
        for name in list(self._fast_moved):
            if limit and self._fast_opened.get(name, 0) >= limit and self.positions.get(name) is None:
                # Bot 5's trades are done: back to this bot's own settings for the rest of the day.
                self._fast_moved.pop(name)
                self._fast_back.add(name)
                self._signals[name] = f"{name}: back to Bot {self.bot_id} after {limit} Bot {fast_bot.TARGET_BOT} trade(s)"
        stamp = now.timestamp()
        for name, pos in list(self.positions.items()):
            price = self._ltps.get(name)
            if pos is None or not price or name in self._fast_moved or name in self._fast_back:
                continue
            hist = self._fast_watch.note(name, price, stamp, settings)
            if len(self._fast_moved) >= settings.max_day or not fast_bot.in_profit(pos, price, settings):
                continue
            why = fast_bot.fast(hist, pos.direction, stamp, settings)
            if why:
                await self._fast_move(name, pos.direction, why)

    def _fast_gate(self, engine, symbol: str) -> str | None:  # noqa: ARG002
        """A moved stock takes at most `bot5_trades` entries on Bot 5's settings (StrategyEngine.entry_gate)."""
        name = (symbol or "").upper()
        if name not in self._fast_moved:
            return None
        raw = dict(self.fast.get("settings") or {})
        raw.setdefault("bot5_trades", 0)
        limit = fast_bot.clean(raw).bot5_trades
        if limit and self._fast_opened.get(name, 0) >= limit:
            return f"Bot {fast_bot.TARGET_BOT} has made its {limit} trade(s) on {name}: back to Bot {self.bot_id}"
        self._fast_opened[name] = self._fast_opened.get(name, 0) + 1
        return None

    def _note_fast(self, name: str, direction: str, why: str, result: str, moved: bool) -> None:
        self.fast_log.append(
            {
                "at": self._now().strftime("%H:%M"),
                "symbol": name,
                "side": direction,
                "why": why,
                "result": result,
                "moved": moved,
                "from_bot": self.bot_id,
            }
        )
        del self.fast_log[:-100]

    async def _fast_move(self, name: str, direction: str, why: str) -> None:
        await self._fast_move_inner(name, direction, why)
        note = self._signals.get(name, "")
        result = note.split("): ", 1)[-1] if note else ""
        moved = name in self._fast_moved
        self._note_fast(name, direction, why, result, moved)
        # The hand-off trail (handoff_log.py): in a replay the stock stays in this bot's book on Bot 5's settings.
        self._log_handoff(
            name,
            fast_bot.TARGET_BOT,
            "FAST_MOVE",
            why,
            (f"moved to Bot {fast_bot.TARGET_BOT}'s settings (replay) — {result}" if moved else result or "not moved"),
            self._cfg_cache,
            None,
            False,
        )

    async def _fast_move_inner(self, name: str, direction: str, why: str) -> None:
        raw = dict(self.fast.get("settings") or {})
        raw.setdefault("entry_checks", False)
        side = fast_bot.bot5_side(direction, fast_bot.clean(raw))  # flip = Bot 5 takes the other side
        if fast_bot.clean(raw).entry_checks:
            # Bot 5's own entry checks, with its settings for this stock, before the trade is cut.
            from strategy_engine import _cfg_for

            cfg = _cfg_for(self.load_config(), name)
            for key, value in fast_bot.bot5_overrides(self.fast, name).items():
                setattr(cfg, key, value)
            refused = fast_bot.entry_refusal(self._frames.get(name), cfg, side, self._ltps.get(name))
            if refused:
                self._signals[name] = f"{name} fast move ({why}): not moved, Bot {fast_bot.TARGET_BOT}'s entry check says no: {refused}"
                return
        if fast_bot.clean(raw).keep_trade:
            # Keep mode: the trade stays; a replay plays one bot, so Bot 5's order next to it cannot be
            # played here (the run shows the move in the signal line only).
            self._fast_back.add(name)
            self._signals[name] = (
                f"{name} fast move ({why}): trade kept (keep mode); Bot {fast_bot.TARGET_BOT}'s own order is not played in a replay"
            )
            return
        try:
            await self.close_symbol(name, reason=fast_bot.EXIT_REASON)
        except ForceRefused as exc:
            self._signals[name] = f"{name} fast move ({why}): not moved, the trade could not be closed ({exc})"
            return
        self._fast_moved[name] = fast_bot.bot5_overrides(self.fast, name)
        signal = "BULLISH" if side == "LONG" else "BEARISH"
        flip = " (flip)" if side != direction else ""
        try:
            opened = await self.direct_order(
                name, signal, why=fast_bot.ENTRY_REASON, note=f"fast mover to Bot {fast_bot.TARGET_BOT}: {why}{flip}"
            )
        except ForceRefused as exc:
            opened = f"not ordered: {exc}"
        self._signals[name] = f"{name} fast move ({why}): cut; Bot {fast_bot.TARGET_BOT}'s settings {opened}"

    def _play_as(self, symbol: str, to_bot: int) -> None:
        name = (symbol or "").upper()
        fields = handoff_chain.overrides(self.chain, to_bot, name, self.bot_id)
        if to_bot == self.bot_id and not fields:
            self._on_bot.pop(name, None)
            self._chain_fields.pop(name, None)
            return
        self._on_bot[name] = to_bot
        self._chain_fields[name] = fields

    def _origin_bot(self, symbol: str) -> int | None:
        """In a backtest that plays the hand-offs, "back" is the replayed bot (stop_handoff.py)."""
        if getattr(self, "chain", None) is not None:
            name = (symbol or "").upper()
            return self.bot_id if self._on_bot.get(name, self.bot_id) != self.bot_id else None
        return super()._origin_bot(symbol)

    def _acting_bot(self, symbol: str) -> int:
        """With hand-offs played, a handed-off stock trades as the bot it was moved to."""
        if getattr(self, "chain", None) is None:
            return int(self.bot_id)
        return int(self._on_bot.get((symbol or "").upper(), self.bot_id))

    def _handoff_origin(self, symbol: str) -> int | None:
        """In a backtest every stock starts on the replayed bot; a stock played on another bot came from it."""
        if getattr(self, "chain", None) is None:
            return None
        return int(self.bot_id) if self._acting_bot(symbol) != int(self.bot_id) else None

    def _keeps_both(self) -> bool:
        """One trade per stock here: a trend hand-off (breakout_when IN_PROFIT) hands the trade over."""
        return False

    def _chain_hand_off(self, from_bot: int, symbol: str, to_bot: int) -> str:
        """Play a hand-off inside this backtest (handoff_chain.py): the stock stays and trades with the target bot's
        settings from now on. The live Trade lists are not touched."""
        name = (symbol or "").upper()
        to_bot = int(to_bot)
        current = self._on_bot.get(name, self.bot_id)
        if current == to_bot:
            return f"already on Bot {to_bot}: no handoff"
        self._play_as(name, to_bot)
        self.hold_for_next_cross([name])
        self.chain_log.append(
            {"at": self._now().strftime("%H:%M"), "day": self._session_date, "symbol": name, "from_bot": current, "to_bot": to_bot}
        )
        del self.chain_log[:-200]
        return f"moved to Bot {to_bot} (played in this backtest) — waits for its next cross"

    def _chain_arrival(self, to_bot: int, symbol: str, arrival) -> str:
        """Bot 3 enters on the breakout's side at once (breakout.arrive on the live bots), here on this engine."""
        self.queue_breakout_entry(symbol, arrival)
        side = "buy" if arrival.direction == "LONG" else "sell"
        return f"Bot {to_bot} will {side} on a pullback to {arrival.limit:.2f}"

    def _chain_order(self, to_bot: int, symbol: str, direction: str) -> str:
        """Bot 2 orders at once (handoff_now, recovery.order_now on the live bots), after this tick."""
        self._chain_orders.append(((symbol or "").upper(), direction))
        return f"Bot {to_bot} {'buys' if direction == 'LONG' else 'sells'} at once"

    async def _open(self, direction, cross_price, atr, cfg, now, why: str = "SMA_CROSS") -> None:
        played = self._on_bot.get((cfg.symbol or "").upper())
        if played is not None and played != self.bot_id:
            why = handoff_chain.tag(why, played)  # this trade is the other bot's, played in the backtest
        await super()._open(direction, cross_price, atr, cfg, now, why=why)

    def _hand_off(self, from_bot: int, symbol: str, source_live: bool, to_bot: int = recovery.HANDOFF_BOT, chop: bool | None = None, keep_source: bool = False) -> str:  # noqa: ARG002
        """Hand-off in a replay. With "Play hand-offs" on (`chain`), it is played inside the run (`_chain_hand_off`).
        Off, the stock really moves (to Bot 2 by recovery.py, back to Bot 3 by breakout.py).

        The owner checks the hand-off after hours on replays. Practice money only, so a
        LIVE target bot is never armed. The stock is then dropped from the rest of this replay,
        as the bot it left would stop trading it.
        """
        if getattr(self, "chain", None) is not None:
            return self._chain_hand_off(from_bot, symbol, to_bot)
        text = recovery.handoff(from_bot, symbol, source_live=False, to_bot=to_bot)
        if text.startswith("moved"):
            name = (symbol or "").upper()
            self.replay_symbols = [s for s in self.replay_symbols if s != name]
            if self.on_handoff is not None:
                self.on_handoff(name)
        return text

    def _alert(self, message: str, critical: bool = False, call: bool = False, kind: str = "trade") -> None:  # noqa: ARG002
        return None

    def _fade_unarm(self, symbol: str) -> str:
        """Trend faded in a replay (fade_unarm.py): the stock leaves this run only; the live Trade list is untouched."""
        name = (symbol or "").upper()
        self.replay_symbols = [s for s in self.replay_symbols if s != name]
        return "dropped from the rest of this replay day"

    def load_config(self) -> BotConfig:
        row = super().load_config()
        data = {col.name: getattr(row, col.name) for col in BotConfig.__table__.columns}
        if self.saved_settings:
            for name, value in self.saved_settings.items():
                if name == "stock_settings":
                    data[name] = value if isinstance(value, str) else json.dumps(value or {})
                elif name in data and name not in ("id", "symbol", "trade_symbols", "trading_mode"):
                    data[name] = value
        if self._chain_fields:
            # A stock handed to Bot 2 / Bot 3 in this backtest plays with that bot's settings (handoff_chain.py).
            own = stock_settings(SimpleNamespace(stock_settings=data.get("stock_settings")))
            for name, fields in self._chain_fields.items():
                own[name] = {**own.get(name, {}), **fields}
            data["stock_settings"] = json.dumps(own)
        if self._fast_moved:
            # A stock the fast-mover hand-off moved plays the rest of the day on Bot 5's settings.
            own = stock_settings(SimpleNamespace(stock_settings=data.get("stock_settings")))
            for name, fields in self._fast_moved.items():
                own[name] = {**own.get(name, {}), **fields}
            data["stock_settings"] = json.dumps(own)
        data["trading_mode"] = "REPLAY"
        data["max_trades_per_day"] = REPLAY_TRADE_CAP
        data["trade_symbols"] = ",".join(self._armed_now())
        view = (data.get("symbol") or "").upper()
        data["symbol"] = view if view in self.replay_symbols else (self.replay_symbols[0] if self.replay_symbols else "")
        cfg = BotConfig(**data)
        self._cfg_cache = cfg
        return cfg

    def _refresh_tick_sizes(self) -> None:
        return None

    def _marker_book(self, cfg: BotConfig | None):  # noqa: ARG002
        """Only this run's trades. Replaying the same day again must not stack markers."""
        if self.run_id is not None:
            return TradeLog.run_id == self.run_id
        return (TradeLog.mode == "REPLAY") & (TradeLog.id > self._min_trade_id)

    def snapshot(self) -> dict:
        snap = super().snapshot()
        snap["max_trades"] = None  # no cap on a replay
        return snap


def _is_fast(reason: str | None) -> bool:
    return (reason or "").startswith(fast_bot.ENTRY_REASON)


def bot5_view(eng: "ReplayEngine", snap: dict) -> dict:
    """The Bot 5 tab's view of a source bot's replay (owner's choice): only the stocks the fast-mover hand-off
    sent to Bot 5, their Bot 5 trades (entry reason FAST_MOVE) and P&L, plus the day's hand-off list. Display
    only: the trades stay in the replayed bot's book and run, played by that one engine."""
    rows: list[TradeLog] = []
    if eng.run_id is not None:
        with session_factory()() as db:
            rows = (
                db.query(TradeLog)
                .filter(TradeLog.run_id == eng.run_id, TradeLog.date == eng._session_date)  # noqa: SLF001
                .all()
            )
    rows = [r for r in rows if _is_fast(r.entry_reason)]
    closed = [r for r in rows if r.exit_price is not None]
    by_symbol: dict[str, dict] = {}
    gross = net = charges = 0.0
    wins = gross_wins = 0
    for r in closed:
        per = by_symbol.setdefault((r.symbol or "").upper(), {"net": 0.0, "gross": 0.0, "trades": 0})
        per["net"] += float(r.net_pnl or 0)
        per["gross"] += float(r.gross_pnl or 0)
        per["trades"] += 1
        gross += float(r.gross_pnl or 0)
        net += float(r.net_pnl or 0)
        charges += float(r.brokerage_and_taxes or 0)
        wins += 1 if (r.net_pnl or 0) > 0 else 0
        gross_wins += 1 if (r.gross_pnl or 0) > 0 else 0
    held = {name for name, pos in eng.positions.items() if pos is not None and _is_fast(pos.entry_why)}
    names = set(eng._fast_moved) | held | {(r.symbol or "").upper() for r in rows}  # noqa: SLF001
    books = []
    open_net = open_gross = 0.0
    for book in snap.get("books") or []:
        name = book.get("symbol")
        if name not in names:
            continue
        mine = name in held
        done = by_symbol.get(name, {"net": 0.0, "gross": 0.0, "trades": 0})
        on = book.get("open_net") if mine else None
        og = book.get("open_gross") if mine else None
        open_net += on or 0.0
        open_gross += og or 0.0
        flat = {"direction": "FLAT", "qty": 0, "entry_price": None, "sl_trigger": None, "stop_active": None,
                "target": None, "trailing": None, "tsl_step": None}
        books.append(
            {
                **book,
                **({} if mine else flat),
                "open_net": on,
                "open_gross": og,
                "closed_net": done["net"],
                "closed_gross": done["gross"],
                "closed_trades": done["trades"],
                "day_net": done["net"] + (on or 0.0),
                "day_gross": done["gross"] + (og or 0.0),
            }
        )
    n = len(closed)
    kpis = {
        **(snap.get("kpis") or {}),
        "actual_gross": gross,
        "theoretical_gross": gross,
        "total_charges": charges,
        "net": net,
        "trades": n,
        "wins": wins,
        "gross_wins": gross_wins,
        "win_rate": (wins / n * 100) if n else 0.0,
        "by_symbol": by_symbol,
    }
    focus_mine = (snap.get("symbol") or "").upper() in held
    out = {
        **snap,
        "bot": fast_bot.TARGET_BOT,
        "bot_name": f"Bot {fast_bot.TARGET_BOT}",
        "books": books,
        "trade_symbols": sorted(eng._fast_moved),  # noqa: SLF001
        "kpis": kpis,
        "realized_net_pnl": net,
        "open_net_total": open_net,
        "open_gross_total": open_gross,
        "trades_today": len(rows),
        "follows_bot": eng.bot_id,
        "fast_log": list(eng.fast_log),
    }
    if not focus_mine:
        out.update(
            {
                "position": None,
                "active_sl_trigger": None,
                "unrealized_gross_pnl": 0.0,
                "unrealized_net_pnl": 0.0,
                "estimated_charges": 0.0,
                "sl_room": None,
                "sl_room_pct": None,
                "charge_estimate": None,
            }
        )
    return out


def close_orphan_replay_rows(run_id: int | None = None, interrupted: bool = False) -> int:
    """A restart mid-replay leaves REPLAY rows open. Close them flat.

    With `run_id`, only that run's rows: each bot has its own replay, and
    stopping one must not touch another bot's replay still playing.
    `interrupted` (at boot): a run that was playing is marked INTERRUPTED, so
    it can be resumed from the first day it had not finished.
    """
    now = dt.datetime.now(IST).replace(tzinfo=None)
    with session_factory()() as db:
        query = db.query(TradeLog).filter(TradeLog.exit_time.is_(None), TradeLog.mode == "REPLAY")
        if run_id is not None:
            query = query.filter(TradeLog.run_id == run_id)
        rows = query.all()
        for row in rows:
            row.exit_time = now
            row.exit_price = row.entry_price
            row.exit_reason = "REPLAY_STOPPED"
            row.gross_pnl = 0.0
            row.brokerage_and_taxes = 0.0
            row.net_pnl = 0.0
        # A run that was playing when the server stopped did not finish.
        runs = db.query(ReplayRun).filter(ReplayRun.status == "RUNNING")
        if run_id is not None:
            runs = runs.filter(ReplayRun.id == run_id)
        for run in runs.all():
            run.status = "INTERRUPTED" if interrupted else "STOPPED"
        db.commit()
        return len(rows)


def _run_settings(run: ReplayRun) -> dict:
    try:
        data = json.loads(run.settings or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _settings_for_page(raw: str | None) -> dict:
    """A run's saved settings as the pages get them: a Scalp-page backtest's resume data (the stock list it picks
    from, the auto-arm hits) is replaced by `resumable` and a count, so the runs list stays small."""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    pick = data.get("scalp_pick")
    if isinstance(pick, dict) and ("universe_symbols" in pick or "active_hits" in pick):
        pick = {k: v for k, v in pick.items() if k not in ("universe_symbols", "active_hits")}
        pick["resumable"] = True
        data = {**data, "scalp_pick": pick}
    return data


def resumable_runs(bot: int | None = None) -> list[dict]:
    """Runs cut short (a server restart, or Stop) with days still to play, newest first.

    Scalp-pick runs count only when they saved the stocks they pick from (`universe_symbols`, kept since resume
    was added for them); older ones cannot rebuild their picks for the days left.
    """
    out: list[dict] = []
    with session_factory()() as db:
        rows = (
            db.query(ReplayRun)
            .filter(ReplayRun.status.in_(("INTERRUPTED", "STOPPED")), ReplayRun.days_done < ReplayRun.days_total)
            .order_by(ReplayRun.id.desc())
            .limit(20)
            .all()
        )
        for run in rows:
            settings = _run_settings(run)
            pick = settings.get("scalp_pick")
            if pick and not pick_resumable(pick):
                continue
            run_bot = int(settings.get("bot") or 1)
            if bot is not None and run_bot != int(bot):
                continue
            out.append(
                {
                    "id": run.id,
                    "bot": run_bot,
                    "status": run.status,
                    "start_date": run.start_date,
                    "end_date": run.end_date,
                    "days_total": run.days_total,
                    "days_done": run.days_done,
                    "next_day": (run.days or "").split(",")[run.days_done] if run.days else None,
                    "symbols": [s for s in (run.symbols or "").split(",") if s],
                    # Which Scalp-page backtest it is (TREND / ACTIVE / AUTOARM / SCALP), None for a plain replay.
                    "kind": (str(pick.get("kind") or "SCALP").upper() if pick else None),
                }
            )
    return out


def _drop_unfinished_days(run_id: int, days: list[str]) -> int:
    """Delete a run's trades on days it had not finished, so a resume plays them afresh."""
    if not days:
        return 0
    with session_factory()() as db:
        n = db.query(TradeLog).filter(TradeLog.run_id == run_id, TradeLog.date.in_(days)).delete(synchronize_session=False)
        # Its 1-minute reviews on those days go too; the replay raises them again.
        for day in days:
            start = dt.datetime.fromisoformat(day)
            db.query(ReviewLog).filter(
                ReviewLog.run_id == run_id,
                ReviewLog.created_at >= start,
                ReviewLog.created_at < start + dt.timedelta(days=1),
            ).delete(synchronize_session=False)
        db.commit()
    handoff_log.delete_run(run_id, days)
    return int(n)


def _day_rows(trades: list[TradeLog]) -> list[dict]:
    days: dict[str, dict] = {}
    for row in trades:
        if row.exit_price is None:
            continue
        d = days.setdefault(
            row.date,
            {"date": row.date, "trades": 0, "wins": 0, "losses": 0, "profit": 0.0, "loss": 0.0, "charges": 0.0, "net": 0.0},
        )
        gross = float(row.gross_pnl or 0.0)
        d["trades"] += 1
        if gross > 0:
            d["wins"] += 1
            d["profit"] += gross
        elif gross < 0:
            d["losses"] += 1
            d["loss"] += gross
        d["charges"] += float(row.brokerage_and_taxes or 0.0)
        d["net"] += float(row.net_pnl if row.net_pnl is not None else gross)
    out = []
    running = 0.0
    for key in sorted(days):
        d = days[key]
        d["gross"] = d["profit"] + d["loss"]
        running += d["net"]
        d["cumulative"] = running
        out.append({k: round(v, 2) if isinstance(v, float) else v for k, v in d.items()})
    return out


def _totals(day_rows: list[dict]) -> dict:
    keys = ("trades", "wins", "losses", "profit", "loss", "charges", "net")
    total = {k: sum(d[k] for d in day_rows) for k in keys}
    total["gross"] = total["profit"] + total["loss"]
    total["win_rate"] = round(100.0 * total["wins"] / total["trades"], 1) if total["trades"] else 0.0
    peak = 0.0
    worst = 0.0
    for d in day_rows:
        peak = max(peak, d["cumulative"])
        worst = min(worst, d["cumulative"] - peak)
    total["max_drawdown"] = round(worst, 2)
    total["green_days"] = sum(1 for d in day_rows if d["net"] > 0)
    total["red_days"] = sum(1 for d in day_rows if d["net"] < 0)
    return {k: round(v, 2) if isinstance(v, float) else v for k, v in total.items()}


def _stock_rows(trades: list[TradeLog], symbols: list[str]) -> list[dict]:
    """The run split by stock: each stock's totals and its own day-wise P&L.

    Every stock the run replayed is listed, a stock that never traded with
    zero rows, so a quiet stock is visible rather than missing. The strategy
    is the snapshot on the stock's first trade (a stock can have its own
    settings), or None when it did not trade.
    """
    by_symbol: dict[str, list[TradeLog]] = {s.upper(): [] for s in symbols}
    for row in trades:
        by_symbol.setdefault((row.symbol or "").upper(), []).append(row)
    out = []
    for symbol, rows in by_symbol.items():
        days = _day_rows(rows)
        first = min((r for r in rows if r.strategy), key=lambda r: r.id, default=None)
        try:
            strategy = json.loads(first.strategy) if first is not None else None
        except (TypeError, ValueError):
            strategy = None
        out.append({"symbol": symbol, "totals": _totals(days), "days": days, "strategy": strategy})
    out.sort(key=lambda r: (r["totals"]["trades"] > 0, r["totals"]["net"]), reverse=True)
    return out


def _capital(trades: list[TradeLog]) -> dict:
    """The most money the run had in open trades at one moment (entry price x qty).

    Trades overlap when several stocks are held together; their entry values
    add up. A trade still open (stopped run) counts until the end of its day.
    Returned per run and per day, with when the peak happened and how many
    positions were open then. Margin and the trader's own capital are applied
    on screen; this is the full value of the shares.
    """
    events: dict[str, list[tuple[dt.datetime, int, float]]] = {}
    for row in trades:
        if row.entry_time is None or not row.entry_price or not row.qty:
            continue
        value = float(row.entry_price) * abs(int(row.qty))
        start = row.entry_time
        end = row.exit_time or dt.datetime.combine(start.date(), dt.time(23, 59))
        day = events.setdefault(row.date or start.date().isoformat(), [])
        # A close at the same moment as another entry frees its money first (order 0 before 1).
        day.append((start, 1, value))
        day.append((end, 0, -value))
    by_day = []
    best = {"peak_value": 0.0, "peak_at": None, "peak_positions": 0}
    for date in sorted(events):
        open_value, open_count = 0.0, 0
        top = {"date": date, "peak_value": 0.0, "peak_at": None, "peak_positions": 0}
        for when, kind, change in sorted(events[date], key=lambda e: (e[0], e[1])):
            open_value += change
            open_count += 1 if kind == 1 else -1
            if kind == 1 and open_value > top["peak_value"] + 1e-9:
                top.update(peak_value=open_value, peak_at=when.isoformat(), peak_positions=open_count)
        top["peak_value"] = round(top["peak_value"], 2)
        by_day.append(top)
        if top["peak_value"] > best["peak_value"]:
            best = {k: top[k] for k in ("peak_value", "peak_at", "peak_positions")}
    return {**best, "days": by_day}


def _fast_move_part(trades: list[TradeLog]) -> dict | None:
    """The fast-mover hand-off's part of a run (fast_bot.py): the trades cut on the source bot and the
    trades opened at once with Bot 5's settings, closed trades only. None when the run had none."""
    cut = [t for t in trades if t.exit_reason == fast_bot.EXIT_REASON]
    opened = [t for t in trades if (t.entry_reason or "").startswith(fast_bot.ENTRY_REASON) and t.exit_time is not None]
    if not cut and not opened:
        return None

    def total(rows, key):
        return round(sum(float(getattr(t, key) or 0) for t in rows), 2)

    return {
        "cut": len(cut),
        "cut_gross": total(cut, "gross_pnl"),
        "trades": len(opened),
        "gross": total(opened, "gross_pnl"),
        "net": total(opened, "net_pnl"),
        "wins": sum(1 for t in opened if (t.gross_pnl or 0) > 0),
    }


def _handoff_part(trades: list[TradeLog]) -> list[dict] | None:
    """Played hand-offs (handoff_chain.py): closed trades per bot they were played as (Bot 2, Bot 3). None when none."""
    groups: dict[int, list[TradeLog]] = {}
    for t in trades:
        bot = handoff_chain.played_as(t.entry_reason)
        if bot is not None and t.exit_time is not None:
            groups.setdefault(bot, []).append(t)
    if not groups:
        return None
    return [
        {
            "bot": bot,
            "trades": len(rows),
            "stocks": len({(t.symbol or "").upper() for t in rows}),
            "gross": round(sum(float(t.gross_pnl or 0) for t in rows), 2),
            "net": round(sum(float(t.net_pnl or 0) for t in rows), 2),
            "wins": sum(1 for t in rows if (t.gross_pnl or 0) > 0),
        }
        for bot, rows in sorted(groups.items())
    ]


def _run_dict(run: ReplayRun, trades: list[TradeLog], with_days: bool) -> dict:
    day_rows = _day_rows(trades)
    out = {
        "id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "start_time": run.start_time,
        "symbols": [s for s in (run.symbols or "").split(",") if s],
        "settings": _settings_for_page(run.settings),
        "status": run.status,
        "days_total": run.days_total,
        "days_done": run.days_done,
        "totals": _totals(day_rows),
        "capital": _capital(trades),
        "fast_move": _fast_move_part(trades),
        "handoffs": _handoff_part(trades),
    }
    if with_days:
        out["days"] = day_rows
        out["stocks"] = _stock_rows(trades, out["symbols"])
        # Choppy / trending at each trade's entry, and where each stock was handed (handoff_log.py; display only).
        out["by_regime"] = handoff_log.by_regime(trades)
        out["handoff_trail"] = handoff_log.rows(run_id=run.id)
    return out


# Run summaries for the Backtests list, kept until the run or its trades change
# (status, days done, trade count, open trades, newest trade id). The tab polls
# every few seconds; re-reading every trade of every listed run each time
# blocked the shared event loop for seconds once there were many runs.
_RUN_CACHE: dict[int, tuple[tuple, dict]] = {}
_RUN_CACHE_MAX = 600


def list_runs(limit: int = 50) -> list[dict]:
    """The newest runs with their totals. Blocking: call it off the event loop."""
    with session_factory()() as db:
        runs = db.query(ReplayRun).order_by(ReplayRun.id.desc()).limit(limit).all()
        ids = [r.id for r in runs]
        stamps: dict[int, tuple] = {}
        if ids:
            rows = (
                db.query(
                    TradeLog.run_id,
                    func.count(TradeLog.id),
                    func.sum(case((TradeLog.exit_price.is_(None), 1), else_=0)),
                    func.max(TradeLog.id),
                )
                .filter(TradeLog.run_id.in_(ids))
                .group_by(TradeLog.run_id)
                .all()
            )
            stamps = {int(r[0]): (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows}
        keys = {r.id: (r.status, r.days_done, r.days_total, stamps.get(r.id, (0, 0, 0))) for r in runs}
        stale = [r.id for r in runs if _RUN_CACHE.get(r.id, (None,))[0] != keys[r.id]]
        by_run: dict[int, list[TradeLog]] = {}
        if stale:
            for t in db.query(TradeLog).filter(TradeLog.run_id.in_(stale)).all():
                by_run.setdefault(int(t.run_id), []).append(t)
        out = []
        for r in runs:
            if r.id in stale:
                _RUN_CACHE[r.id] = (keys[r.id], _run_dict(r, by_run.get(r.id, []), with_days=False))
            out.append(_RUN_CACHE[r.id][1])
    if len(_RUN_CACHE) > _RUN_CACHE_MAX:
        for run_id in sorted(_RUN_CACHE)[: len(_RUN_CACHE) - _RUN_CACHE_MAX]:
            _RUN_CACHE.pop(run_id, None)
    return out


def run_trades(run_id: int, symbol: str | None = None) -> list[dict] | None:
    """One run's closed trades with how far each went for and against it.

    `best_points` / `worst_points` are the most favourable and adverse moves from
    the entry price while the trade was open (from the recorded high / low), in
    rupees per share, for the real position's side.
    """
    with session_factory()() as db:
        if db.get(ReplayRun, run_id) is None:
            return None
        query = db.query(TradeLog).filter(TradeLog.run_id == run_id)
        if symbol:
            query = query.filter(TradeLog.symbol == symbol.upper())
        rows = query.order_by(TradeLog.id).all()
    out = []
    for r in rows:
        long = (r.direction or "").upper() in ("LONG", "BUY")
        entry = float(r.entry_price or 0)
        high, low = r.max_high, r.max_low
        best = worst = None
        if entry and high is not None and low is not None:
            best = round((high - entry) if long else (entry - low), 2)
            worst = round((entry - low) if long else (high - entry), 2)
        out.append(
            {
                "date": r.date,
                "symbol": r.symbol,
                "direction": r.direction,
                "qty": r.qty,
                "entry_time": r.entry_time,
                "entry_price": r.entry_price,
                "exit_time": r.exit_time,
                "exit_price": r.exit_price,
                "exit_reason": r.exit_reason,
                "entry_reason": getattr(r, "entry_reason", None),
                "entry_regime": handoff_log.parse(getattr(r, "entry_regime", None)),
                "gross_pnl": r.gross_pnl,
                "charges": r.brokerage_and_taxes,
                "net_pnl": r.net_pnl,
                "best_points": best,
                "worst_points": worst,
            }
        )
    return out


def get_run(run_id: int) -> dict | None:
    with session_factory()() as db:
        run = db.get(ReplayRun, run_id)
        if run is None:
            return None
        trades = db.query(TradeLog).filter(TradeLog.run_id == run_id).all()
        return _run_dict(run, trades, with_days=True)


def delete_run(run_id: int) -> bool:
    with session_factory()() as db:
        run = db.get(ReplayRun, run_id)
        if run is None:
            return False
        db.query(TradeLog).filter(TradeLog.run_id == run_id).delete()
        db.query(ReviewLog).filter(ReviewLog.run_id == run_id).delete()
        db.delete(run)
        db.commit()
    handoff_log.delete_run(run_id)
    _RUN_CACHE.pop(run_id, None)
    open_pnl_log.delete_replay(run_id)
    return True


def _wants_high52(settings: dict, symbol: str) -> bool:
    """Does the replayed bot (or this stock's own settings) read the 52-week check?"""
    own = (settings.get("stock_settings") or {}).get(symbol) if isinstance(settings.get("stock_settings"), dict) else None
    if isinstance(own, dict) and "use_high52" in own:
        return bool(own.get("use_high52"))
    return bool(settings.get("use_high52"))


@dataclass
class ReplaySession:
    """One bot's replay: download the range, then play each day in turn (one per bot can run together)."""

    status: str = "IDLE"  # IDLE | LOADING | PLAYING | PAUSED | FINISHED | ERROR
    day: dt.date | None = None
    end_day: dt.date | None = None
    start: dt.time = SESSION_OPEN
    speed: int = 60
    symbols: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    days: list[dt.date] = field(default_factory=list)
    day_index: int = 0
    run_id: int | None = None
    #: The SMA bot whose settings the replay plays (1 = main desk, 2-4).
    bot: int = 1
    bot_name: str = "Bot 1"
    loaded: int = 0
    error: str = ""
    effective_speed: float = 0.0
    #: Scalp-pick mode: each day trades only that day's picks, from the pick time.
    rule: PickRule | None = None
    picks: dict[str, list[dict]] = field(default_factory=dict)
    universe: list[str] = field(default_factory=list)
    #: Auto-arm replay: how far the reads that find each stock's arm minute have got (None when not reading).
    finding: str | None = None
    #: Stocks a "Recovered" exit moved to Bot 2 during this run (recovery.py); not traded after it.
    handed_off: list[str] = field(default_factory=list)
    #: "Play hand-offs" (handoff_chain.py): which bot each handed-off stock plays as, carried from day to day
    #: (unless stocks go back at the end of the day), and each hand-off of the run.
    chain_on: dict[str, int] = field(default_factory=dict)
    chain_log: list[dict] = field(default_factory=list)
    _settings: dict = field(default_factory=dict)
    engine: ReplayEngine | None = None
    _task: asyncio.Task | None = None
    _stop: bool = False
    _want: int = 0
    #: Fast-forward target on the current day (the loop ticks the grid as fast as it can up to it).
    _seek: dt.datetime | None = None
    #: Rewind target: the day restarts from its first candle and fast-forwards to it (its trades so far are dropped).
    _rewind: dt.datetime | None = None

    @property
    def active(self) -> bool:
        return self.status in ("LOADING", "PLAYING", "PAUSED", "FINISHED")

    @property
    def config_id(self) -> int:
        return 1 if self.bot == 1 else self.bot + 1

    def info(self) -> dict:
        eng = self.engine
        clock = eng.feed.clock if eng else None
        return {
            "status": self.status,
            "date": self.day.isoformat() if self.day else None,
            "end_date": self.end_day.isoformat() if self.end_day else None,
            "start": self.start.strftime("%H:%M"),
            "clock": clock.isoformat() if clock else None,
            "speed": self.speed,
            "effective_speed": round(self.effective_speed, 1),
            "speeds": list(SPEEDS),
            "symbols": self.symbols,
            "skipped": self.skipped,
            "days": [d.isoformat() for d in self.days],
            "day_index": self.day_index,
            "days_total": len(self.days),
            "run_id": self.run_id,
            "bot": self.bot,
            "bot_name": self.bot_name,
            "loaded": self.loaded,
            "total": len(self.universe or self.symbols) + len(self.skipped) if self.status != "LOADING" else self._want,
            "error": self.error,
            "handed_off": self.handed_off,
            "chain": self._chain_info(),
            "pick_rule": self.rule.as_dict() if self.rule is not None else None,
            "picks": self.picks,
            # Auto-arm replay: where the reads that find each stock's arm minute have got ("day 1 of 5 (…): reading 10:35").
            "finding": self.finding,
            # Where a seek is heading (fast-forward or, after a rewind, the restarted day catching up), else None.
            "seeking": (self._rewind or self._seek).isoformat() if (self._rewind or self._seek) else None,
        }

    def _chain_info(self) -> dict | None:
        if not isinstance(self._settings.get("chain"), dict):
            return None
        eng = self.engine
        live = list(eng.chain_log) if eng is not None else []
        return {
            "on_bot": dict(eng._on_bot) if eng is not None else dict(self.chain_on),  # noqa: SLF001
            "log": (self.chain_log + live)[-200:],
            "return_eod": bool(self._settings["chain"].get("return_eod")),
        }

    async def begin(
        self,
        broker,
        symbols: list[str],
        day: dt.date,
        start: dt.time,
        speed: int,
        end_day: dt.date | None = None,
        settings: dict | None = None,
        rule: PickRule | None = None,
        bot: int = 1,
        bot_name: str | None = None,
    ) -> None:
        await self.stop("REPLAY_STOPPED")
        self.bot, self.bot_name = int(bot), bot_name or f"Bot {int(bot)}"
        self.rule, self.picks, self.universe = rule, {}, []
        self.finding = None
        self.handed_off = []
        self.chain_on, self.chain_log = {}, []
        self.status = "LOADING"
        self.day, self.end_day, self.start, self.speed = day, end_day or day, start, speed
        self.symbols, self.skipped, self.loaded, self.error = [], [], 0, ""
        self.days, self.day_index, self.run_id = [], 0, None
        self._want = len(symbols)
        self.engine = None
        self._stop = False
        self._task = asyncio.create_task(self._load_and_play(broker, symbols, settings or {}))

    async def resume(self, broker, run_id: int, speed: int) -> None:
        """Carry on a run that was cut short, from the first day it had not finished.

        The finished days keep their trades; the unfinished day's partial
        trades are dropped and that day plays again from 09:15, with the
        settings the run was started with.
        """
        with session_factory()() as db:
            run = db.get(ReplayRun, int(run_id))
            if run is None:
                raise ValueError("No such replay run.")
            settings = _run_settings(run)
            pick = settings.get("scalp_pick") if isinstance(settings.get("scalp_pick"), dict) else None
            if pick is not None and not pick_resumable(pick):
                raise ValueError(
                    f"Run #{run.id} was started before Scalp-page backtests could be resumed (it did not save the "
                    "stocks it picks from). Start it again from the Scalp page."
                )
            if run.status not in ("INTERRUPTED", "STOPPED") or run.days_done >= run.days_total:
                raise ValueError(f"Run #{run.id} has nothing left to play.")
            all_days = [dt.date.fromisoformat(d) for d in (run.days or "").split(",") if d]
            done = int(run.days_done or 0)
            symbols = [x for x in (run.symbols or "").split(",") if x]
            rule = None
            if pick is not None:
                # A Scalp-page backtest: the whole list it picks from, and its rule as it was started.
                symbols = [x for x in pick.get("universe_symbols") or [] if isinstance(x, str) and x]
                rule = rule_from_dict(pick, int(settings.get("sma_fast") or 9), int(settings.get("sma_slow") or 21))
            start = dt.datetime.strptime(run.start_time or "09:15", "%H:%M").time()
            bot = int(settings.get("bot") or 1)
            bot_name = str(settings.get("bot_name") or f"Bot {bot}")
        if not symbols or done >= len(all_days):
            raise ValueError(f"Run #{run_id} has nothing left to play.")
        await self.stop("REPLAY_STOPPED")
        _drop_unfinished_days(int(run_id), [d.isoformat() for d in all_days[done:]])
        self.bot, self.bot_name = bot, bot_name
        self.rule, self.universe = rule, []
        # The finished days keep the stocks they picked; the days left pick again as they play.
        kept = (pick or {}).get("picks") if isinstance((pick or {}).get("picks"), dict) else {}
        finished = {d.isoformat() for d in all_days[:done]}
        self.picks = {d: v for d, v in kept.items() if d in finished}
        self.finding = None
        self.handed_off = []
        self.chain_on, self.chain_log = {}, []
        self.status = "LOADING"
        self.day, self.end_day, self.start, self.speed = all_days[done], all_days[-1], start, speed
        self.symbols, self.skipped, self.loaded, self.error = [], [], 0, ""
        self.days, self.day_index, self.run_id = [], done, None
        self._want = len(symbols)
        self.engine = None
        self._stop = False
        self._task = asyncio.create_task(
            self._load_and_play(broker, symbols, settings, resume=(int(run_id), all_days, done))
        )

    async def _load_and_play(
        self, broker, symbols: list[str], settings: dict, resume: tuple[int, list[dt.date], int] | None = None
    ) -> None:
        assert self.day is not None and self.end_day is not None
        first_day, last_day = self.day, self.end_day
        # Today, replayed during the session, ends at the last minute closed now (session_cut).
        self._loaded_at = dt.datetime.now(IST)
        frames: dict[str, pd.DataFrame] = {}
        first = dt.datetime.combine(first_day - dt.timedelta(days=WARMUP_DAYS), dt.time(9, 0))
        last = dt.datetime.combine(last_day, SESSION_END)
        range_start = int(dt.datetime.combine(first_day, dt.time(0, 0), tzinfo=IST).timestamp())
        range_end = int(dt.datetime.combine(last_day, dt.time(0, 0), tzinfo=IST).timestamp()) + 86_400
        traded_days: set[dt.date] = set()
        try:
            for symbol in symbols:
                if self._stop:
                    return
                try:
                    frame = await fetch_frame(broker, symbol, first, last)
                except HistoryError as exc:
                    if "Log in to Groww" in str(exc):
                        raise
                    self.skipped.append(symbol)
                    self.error = str(exc)
                    continue
                inside = frame[(frame["ts"] >= range_start) & (frame["ts"] < range_end)] if not frame.empty else frame
                if inside.empty:
                    self.skipped.append(symbol)
                    continue
                for ts in inside["ts"].astype("int64").unique():
                    traded_days.add(dt.datetime.fromtimestamp(int(ts), IST).date())
                frames[symbol] = frame[frame["ts"] < range_end].reset_index(drop=True)
                self.loaded += 1
            days = sorted(d for d in traded_days if d.weekday() < 5)
            if not frames or not days:
                span = f"{first_day:%d %b %Y}" + ("" if first_day == last_day else f" to {last_day:%d %b %Y}")
                raise HistoryError(
                    f"Groww has no 1-minute candles for {span}. Pick trading days "
                    "(not weekends or holidays) within the last few months."
                )
        except HistoryError as exc:
            self.status = "ERROR"
            self.error = str(exc)
            return
        self.symbols = list(frames)
        self.universe = list(frames)
        # The 52-week levels for the stocks whose entry check reads them: one daily download per stock.
        for symbol in list(frames):
            if self._stop:
                return
            if _wants_high52(settings, symbol):
                await high52.preload(broker, symbol, days)
        done = 0
        if resume is not None:
            # The run's own day list: finished days count, the rest play now.
            _, all_days, done = resume
            days = all_days
        self.days = days
        if self.rule is not None and resume is None:
            pick = {**self.rule.as_dict(), "universe": len(frames), "picks": {}}
            # Kept so a run cut short (a deploy, Stop) can be resumed: the stocks it picks from, and for the auto-arm
            # replay the Most active hits it read before the run (they may not be readable again later).
            pick["universe_symbols"] = list(frames)
            if getattr(self.rule, "kind", None) == auto_arm.KIND:
                pick["active_hits"] = {k: [list(h) for h in v] for k, v in getattr(self.rule, "active_hits", {}).items()}
            settings = {**settings, "scalp_pick": pick}
        elif self.rule is not None:
            # Resume: the saved rule, stock list and hits stay; the finished days' picks are carried on.
            pick = dict(settings.get("scalp_pick") or {})
            pick["picks"] = dict(self.picks)
            settings = {**settings, "scalp_pick": pick}
        if resume is None:
            # The fast-mover hand-off to Bot 5 as set now, kept on the run so Resume plays the same.
            fast = fast_bot.replay_snapshot(self.bot)
            if fast:
                settings = {**settings, "fast_bot": fast}
            if settings.get("play_handoffs"):
                # Bot 2's and Bot 3's settings as they are now, so the hand-offs play the same on Resume.
                settings = {**settings, "chain": handoff_chain.replay_snapshot(self.bot)}
        self._settings = settings
        if resume is not None:
            self.run_id = resume[0]
            self._mark_run(status="RUNNING")
        else:
            with session_factory()() as db:
                run = ReplayRun(
                    created_at=dt.datetime.now(IST).replace(tzinfo=None),
                    start_date=days[0].isoformat(),
                    end_date=days[-1].isoformat(),
                    start_time=self.start.strftime("%H:%M"),
                    symbols=",".join(self.symbols),
                    settings=json.dumps(settings, default=str),
                    status="RUNNING",
                    days_total=len(days),
                    days_done=0,
                    days=",".join(d.isoformat() for d in days),
                )
                db.add(run)
                db.commit()
                self.run_id = int(run.id)
        notes = []
        if self.skipped:
            notes.append(f"No candles for {', '.join(self.skipped)}.")
        self.error = " ".join(notes)
        snap = SimpleNamespace(**settings)
        bars = {name: frame_bars(settings_for(snap, name)) for name in frames}
        fast_snap = settings.get("fast_bot") if isinstance(settings.get("fast_bot"), dict) else None
        chain_snap = settings.get("chain") if isinstance(settings.get("chain"), dict) else None
        if chain_snap:
            # A handed-off stock may switch to Bot 2's / Bot 3's candle size: hand enough bars for any of them.
            for name in frames:
                for n in handoff_chain.PLAYED_BOTS:
                    played = handoff_chain.overrides(chain_snap, n, name, self.bot)
                    if played:
                        bars[name] = max(bars[name], frame_bars(SimpleNamespace(**{**vars(settings_for(snap, name)), **played})))
        if fast_snap:
            # A moved stock may switch to Bot 5's candle size: hand enough bars for either.
            for name in frames:
                moved = SimpleNamespace(**{**vars(settings_for(snap, name)), **fast_bot.bot5_overrides(fast_snap, name)})
                bars[name] = max(bars[name], frame_bars(moved))
        feed = ReplayFeed(frames, dt.datetime.combine(days[done], self.start if done == 0 else SESSION_OPEN, tzinfo=IST), bars)
        bot_paused = False
        saved = settings if resume is not None else None
        for index, day in enumerate(days):
            if index < done:
                continue  # finished before the run was cut short
            if self._stop:
                return
            self.day_index, self.day = index, day
            start = self.start if index == 0 else SESSION_OPEN
            symbols = self.universe
            auto_windows = None
            if self.rule is not None:
                # Pick as the live Scalp page would have at the pick time, then
                # trade only those stocks from that minute on.
                # In a worker thread: a strong-trend pick reads every stock of the universe.
                if getattr(self.rule, "kind", None) == auto_arm.KIND:
                    # The replay bar shows how far the arm-minute reads have got (auto_arm.picks_for_day).
                    label = f"day {index + 1} of {len(days)} ({day.isoformat()})"
                    self.rule.on_step = lambda at, label=label: setattr(self, "finding", f"{label}: reading {at}")
                    self.finding = f"{label}: starting"
                try:
                    picked = await asyncio.to_thread(picks_for_day, frames, day, self.rule)
                finally:
                    self.finding = None
                self.picks[day.isoformat()] = picked
                self._save_picks()
                symbols = [p["symbol"] for p in picked]
                start = self.rule.pick_time
                if getattr(self.rule, "kind", None) == auto_arm.KIND and picked:
                    # Auto-arm replay: each stock armed from the minute the rule picked it (auto_arm.py), as live.
                    auto_windows = auto_arm.arm_windows(day, picked)
                    first = min(p["at"] for p in picked)
                    start = dt.time(int(first[:2]), int(first[3:5]))
                if not symbols:
                    self._mark_run(days_done=index + 1)
                    continue
            # A stock handed off to Bot 2 earlier in the run is no longer this bot's (recovery.py).
            symbols = [s for s in symbols if s not in self.handed_off]
            windows = None
            if settings.get("as_armed"):
                # Each stock only while it was armed live on this bot that day (arm_log.py).
                windows = arm_log.windows(self.config_id, day)
                symbols = [s for s in symbols if s in windows]
            if auto_windows is not None:
                windows = auto_windows
            if not symbols:
                self._mark_run(days_done=index + 1)
                continue
            self.symbols = symbols
            # Played hand-offs: each day starts where the last ended, or back on this bot with "send back".
            if chain_snap and chain_snap.get("return_eod"):
                self.chain_on = {}
            day_on = {k: v for k, v in self.chain_on.items() if k in symbols}
            while True:
                clock = dt.datetime.combine(day, start, tzinfo=IST)
                feed.clock = clock
                # A fresh engine per day: its own trade count, loss limit and P&L.
                engine = ReplayEngine(
                    feed, symbols, run_id=self.run_id, bot=self.bot, settings=saved, arm_windows=windows, fast=fast_snap,
                    chain=chain_snap, on_bot=day_on,
                )
                engine.handoff = engine._hand_off
                engine.on_handoff = lambda name: self.handed_off.append(name)
                # A 1-minute review pauses the replay so it can be answered as it
                # would have been live. Play again without answering = no answer.
                engine.on_review = lambda _item: self.control("pause")
                engine.load_config()
                await engine.tick(clock)
                engine.hold_for_next_cross(symbols)
                engine.status = "PAUSED" if bot_paused else "RUNNING"
                self.engine = engine
                # The Open P&L log of this run, one point per replayed minute (read-only).
                self._pnl_log = open_pnl_log.ReplayRecorder(self.run_id, self.bot)
                if self.status not in ("PLAYING", "PAUSED"):
                    self.status = "PLAYING"
                await self._play()
                if self._stop:
                    return
                bot_paused = engine.status == "PAUSED"
                if self._rewind is not None:
                    # Rewind: the day plays again from its first candle up to the target (same grid, same
                    # settings, so the same trades up to there); what it had booked after the day's start goes.
                    target, self._rewind = self._rewind, None
                    if self.run_id is not None:
                        _drop_unfinished_days(self.run_id, [day.isoformat()])
                        open_pnl_log.delete_replay_day(self.run_id, day)
                    self._pnl_log = None
                    self._seek = target
                    continue
                break
            if chain_snap:
                self.chain_on = dict(engine._on_bot)  # noqa: SLF001
                self.chain_log = (self.chain_log + engine.chain_log)[-200:]
                engine.chain_log = []
            self._mark_run(days_done=index + 1)
        self._mark_run(status="FINISHED")
        self.status = "FINISHED"

    def _save_picks(self) -> None:
        """Keep the picks on the run: its settings name them per day, and its
        stock list is the stocks actually picked (not the whole universe)."""
        if self.run_id is None or self.rule is None:
            return
        picked: list[str] = []
        for day_picks in self.picks.values():
            for p in day_picks:
                if p["symbol"] not in picked:
                    picked.append(p["symbol"])
        self._settings["scalp_pick"]["picks"] = self.picks
        with session_factory()() as db:
            run = db.get(ReplayRun, self.run_id)
            if run is None:
                return
            run.settings = json.dumps(self._settings, default=str)
            run.symbols = ",".join(picked)
            db.commit()

    def _mark_run(self, *, days_done: int | None = None, status: str | None = None) -> None:
        if self.run_id is None:
            return
        with session_factory()() as db:
            run = db.get(ReplayRun, self.run_id)
            if run is None:
                return
            if days_done is not None:
                run.days_done = days_done
            if status is not None:
                run.status = status
            db.commit()

    async def _play(self) -> None:
        """Play the current day until 15:30, then square it off.

        The engine ticks only on a fixed grid of replay time (every
        STEP_SECONDS, on :00/:10/…/:50 of each minute), whatever the speed or
        the server's load. Speed only sets how fast real time walks that grid,
        so the same day with the same settings always sees the same prices and
        gives the same trades. When the server falls behind, owed time is
        dropped: the replay plays slower, never on a different grid.
        """
        eng = self.engine
        assert eng is not None and self.day is not None
        end = self._day_end()
        last = time.monotonic()
        owed = 0.0  # replay seconds earned but not yet played
        try:
            await self._play_loop(eng, end, last, owed)
        finally:
            _PLAYING.discard(id(self))

    async def _fast_forward(self, eng, end: dt.datetime) -> bool:
        """Tick the grid as fast as the CPU share allows up to `_seek`. True when the day finished on the way.

        The same 10-second grid as normal play, so the trades are the ones the day would give at any speed;
        only real time is skipped. Works while PAUSED too (the replay stays paused at the target)."""
        _PLAYING.add(id(self))
        began = time.monotonic()
        try:
            while not self._stop and self._seek is not None and eng.feed.clock < self._seek:
                gap = _to_next_grid(eng.feed.clock)
                eng.feed.clock = min(end, eng.feed.clock + dt.timedelta(seconds=gap))
                try:
                    await eng.tick(eng.feed.clock)
                except Exception as exc:  # noqa: BLE001
                    eng.last_error = str(exc)
                self._note_pnl(eng)
                if eng.feed.clock >= end:
                    self._seek = None
                    await self._finish_day()
                    return True
                if time.monotonic() - began > CPU_SHARE / max(1, len(_PLAYING)):
                    # Give the live engine and the API their share between bursts.
                    await asyncio.sleep(LOOP_SECONDS)
                    began = time.monotonic()
                else:
                    await asyncio.sleep(0)
        finally:
            self._seek = None
        return False

    async def _play_loop(self, eng, end: dt.datetime, last: float, owed: float) -> None:
        while not self._stop:
            if self._rewind is not None:
                return  # the day loop restarts this day and fast-forwards to the target
            if self._seek is not None:
                if await self._fast_forward(eng, end):
                    return
                last, owed = time.monotonic(), 0.0
                continue
            await asyncio.sleep(LOOP_SECONDS)
            now_m = time.monotonic()
            real = now_m - last
            last = now_m
            if self.status != "PLAYING":
                _PLAYING.discard(id(self))
                self.effective_speed = 0.0
                continue
            _PLAYING.add(id(self))
            owed += real * self.speed
            played = 0.0
            began = time.monotonic()
            while not self._stop and self.status == "PLAYING":
                gap = _to_next_grid(eng.feed.clock)
                if owed < gap:
                    break
                owed -= gap
                played += gap
                eng.feed.clock = min(end, eng.feed.clock + dt.timedelta(seconds=gap))
                try:
                    await eng.tick(eng.feed.clock)
                except Exception as exc:  # noqa: BLE001
                    eng.last_error = str(exc)
                self._note_pnl(eng)
                if eng.feed.clock >= end:
                    await self._finish_day()
                    return
                # Let the live engine and the API run between ticks.
                await asyncio.sleep(0)
                share = CPU_SHARE / max(1, len(_PLAYING))
                if time.monotonic() - began > share * max(real, LOOP_SECONDS):
                    owed = 0.0  # behind: drop the rest, play slower instead of piling up
                    break
            spent = time.monotonic() - now_m + LOOP_SECONDS
            sample = played / spent if spent > 0 else 0.0
            self.effective_speed = sample if self.effective_speed == 0 else 0.7 * self.effective_speed + 0.3 * sample

    def _note_pnl(self, eng) -> None:
        log_ = getattr(self, "_pnl_log", None)
        if log_ is not None:
            log_.note(eng, eng.feed.clock)

    def _flush_pnl(self) -> None:
        log_ = getattr(self, "_pnl_log", None)
        if log_ is not None:
            log_.flush()

    def _day_end(self) -> dt.datetime:
        assert self.day is not None
        return session_cut(self.day, getattr(self, "_loaded_at", None))

    async def _finish_day(self) -> None:
        self._flush_pnl()
        eng = self.engine
        if eng is not None and eng.positions:
            await eng._square_off("EOD_SQUARE_OFF")
        if eng is not None and eng.status in ("RUNNING", "PAUSED"):
            keep = eng.status
            eng.status = "DAY_COMPLETED" if keep == "RUNNING" else keep
            end = self._day_end() if self.day is not None else None
            eng.halt_reason = f"Replay day finished at {end:%H:%M}" if end is not None else "Replay day finished at 15:30"

    def control(self, action: str, speed: int | None = None, at: str | None = None) -> None:
        if speed is not None:
            if speed not in SPEEDS:
                raise ValueError(f"Speed must be one of {', '.join(f'{s}×' for s in SPEEDS)}")
            self.speed = speed
        if action == "pause" and self.status == "PLAYING":
            self.status = "PAUSED"
        elif action == "play" and self.status == "PAUSED":
            self.status = "PLAYING"
        elif action == "seek":
            self.seek(at or "")

    def seek(self, at: str) -> dt.datetime:
        """Move the replayed day's clock to `at` (IST HH:MM): forward = fast-forward on the same grid; back =
        the day restarts from its first candle and fast-forwards to there (its trades after that go). Returns
        the target. Only while playing or paused; the pause state is kept."""
        eng = self.engine
        if eng is None or self.day is None or self.status not in ("PLAYING", "PAUSED"):
            raise ValueError("Nothing is playing to move.")
        try:
            hh, mm = str(at).strip().split(":")
            when = dt.time(int(hh), int(mm))
        except (TypeError, ValueError) as exc:
            raise ValueError("Give the time as HH:MM (IST).") from exc
        first = self.start if self.day_index == 0 else SESSION_OPEN
        if self.rule is not None:
            first = max(first, self.rule.pick_time)
        lo = dt.datetime.combine(self.day, first, tzinfo=IST)
        hi = self._day_end() - dt.timedelta(seconds=STEP_SECONDS)
        target = min(hi, max(lo, dt.datetime.combine(self.day, when, tzinfo=IST)))
        clock = eng.feed.clock
        if target > clock:
            self._rewind = None
            self._seek = target
        elif target < clock:
            self._seek = None
            self._rewind = target
        return target

    async def stop(self, reason: str = "REPLAY_STOPPED") -> None:
        """End the replay. Its open practice positions close at the replay price."""
        self._stop = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._task = None
        self._flush_pnl()
        eng = self.engine
        if eng is not None and eng.positions:
            try:
                await eng._square_off(reason)
            except Exception:  # noqa: BLE001
                pass
        # Only a run still playing is cut short; a finished one keeps FINISHED.
        if self.status in ("LOADING", "PLAYING", "PAUSED"):
            self._mark_run(status="STOPPED")
        # Only this replay's own leftovers: other bots' replays may be playing.
        if self.run_id is not None:
            close_orphan_replay_rows(self.run_id)
        self.engine = None
        self.run_id = None
        self.status = "IDLE"


def parse_replay_day(text: str, now: dt.datetime | None = None, allow_today: bool = False) -> dt.date:
    """A finished weekday: before today, or today after 15:30 IST. With `allow_today` (the backtest server, and the
    desk unless DESK_REPLAY_HOURS=after) today may be replayed during the session too, up to the last closed minute
    (`session_cut`)."""
    try:
        day = dt.date.fromisoformat((text or "").strip())
    except ValueError as exc:
        raise ValueError("Pick a date as YYYY-MM-DD.") from exc
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    if day.weekday() >= 5:
        raise ValueError("That is a weekend. Pick a trading day.")
    if day > now.date():
        raise ValueError("Pick a day that has already closed. Today replays after 15:30 IST.")
    if day == now.date() and (market_is_open(now) or now.time() < SESSION_END):
        if not allow_today:
            raise ValueError("Pick a day that has already closed. Today replays after 15:30 IST.")
        if now.time() < TODAY_FROM:
            raise ValueError(f"Today's session has only just begun: replay today from {TODAY_FROM:%H:%M} IST.")
    return day


# Today replayed during the session: from this time on, so a few closed candles exist.
TODAY_FROM = dt.time(9, 20)


def session_cut(day: dt.date, now: dt.datetime | None = None) -> dt.datetime:
    """Where a replayed day ends: 15:30 IST, or for today while the session is on, the start of the last closed
    minute when the candles were loaded (a forming candle is never played)."""
    close = dt.datetime.combine(day, SESSION_END, tzinfo=IST)
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    if day != now.date():
        return close
    return min(close, now.replace(second=0, microsecond=0) - dt.timedelta(minutes=1))


def parse_replay_range(
    start_text: str, end_text: str | None, now: dt.datetime | None = None, allow_today: bool = False
) -> tuple[dt.date, dt.date]:
    """From/To for a run: both finished days (today too with `allow_today`), To on or after From, at most
    MAX_RANGE_DAYS (45) calendar days."""
    if not end_text or end_text.strip() == (start_text or "").strip():
        first = parse_replay_day(start_text, now, allow_today)
        return first, first
    try:
        first = dt.date.fromisoformat((start_text or "").strip())
        last = dt.date.fromisoformat(end_text.strip())
    except ValueError as exc:
        raise ValueError("Pick the dates as YYYY-MM-DD.") from exc
    if last < first:
        raise ValueError("To must be on or after From.")
    # A range may start on a weekend: it begins on the next trading day.
    while first.weekday() >= 5 and first < last:
        first += dt.timedelta(days=1)
    first = parse_replay_day(first.isoformat(), now, allow_today)
    if last < first:
        raise ValueError("To must be on or after From.")
    if (last - first).days > MAX_RANGE_DAYS:
        raise ValueError(f"Pick a range of {MAX_RANGE_DAYS} days or less (about 30 trading days).")
    # The last day must have closed too; weekends inside the range are skipped.
    while last.weekday() >= 5 and last > first:
        last -= dt.timedelta(days=1)
    parse_replay_day(last.isoformat(), now, allow_today)
    return first, last


def parse_start(text: str) -> dt.time:
    try:
        hh, mm = (text or "09:15").split(":")
        start = dt.time(int(hh), int(mm))
    except (ValueError, TypeError) as exc:
        raise ValueError("Start time must be HH:MM.") from exc
    if not (SESSION_OPEN <= start < dt.time(15, 15)):
        raise ValueError("Start between 09:15 and 15:15 IST.")
    return start


def armed_symbols(cfg: BotConfig) -> list[str]:
    return trade_names(cfg)
