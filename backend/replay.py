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
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from candle_history import HistoryError, fetch_frame
from database import session_factory
from groww_client import IST, OrderAck, market_is_open
from models import BotConfig, TradeLog
from strategy_engine import MAX_TRADE_SYMBOLS, StrategyEngine, trade_names
from tick_sizes import round_price

SPEEDS = (1, 10, 60, 300)
SESSION_OPEN = dt.time(9, 15)
SESSION_END = dt.time(15, 30)
# Days of candles before the replayed day so SMA 21, ATR 14 and the day
# change are already formed at 09:15.
WARMUP_DAYS = 4
# Bars handed to the engine per quote: one full session plus 125 bars of the
# previous one, enough for SMA 21, ATR 14, RSI, the volume average and the
# previous close. More only slows every tick.
FRAME_BARS = 500
# Share of each real second the replay may spend ticking, so the live engine
# and the API stay responsive on the one CPU. Past this it plays slower.
CPU_SHARE = 0.4
LOOP_SECONDS = 0.2
# Replay seconds per engine tick. Six ticks a minute land on the candle's
# open, both extremes and its close.
STEP_SECONDS = 10
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


class ReplayFeed:
    """Candles for each replayed stock, cut at the replay clock."""

    def __init__(self, frames: dict[str, pd.DataFrame], clock: dt.datetime):
        self.clock = clock
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
        start = max(0, closed_end - FRAME_BARS)
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


class ReplayBroker:
    """Local fills only. There is deliberately no Groww SDK in here."""

    mode = "PAPER"
    token = ""

    def __init__(self, feed: ReplayFeed):
        self.feed = feed
        self.data_source = "REPLAY"
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

    async def refresh(self, symbol: str) -> tuple[float, pd.DataFrame, str]:
        ltp, frame = self.feed.quote(symbol)
        return ltp, frame, "REPLAY"

    async def place_entry(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:  # noqa: ARG002
        px = round_price(symbol, ltp)
        oid = self._id("REPLAY")
        self._orders[oid] = ("FILLED", px)
        return OrderAck(oid, "FILLED", px)

    async def place_exit(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:  # noqa: ARG002
        return await self.place_entry(symbol, side, qty, ltp)

    async def place_sl(self, symbol: str, side: str, qty: int, trigger: float) -> OrderAck:  # noqa: ARG002
        oid = self._id("REPLAYSL")
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


class ReplayEngine(StrategyEngine):
    """The SMA bot on a replayed day: its own clock, books and broker."""

    def __init__(self, feed: ReplayFeed, symbols: list[str]):
        self.feed = feed
        self.replay_symbols = [s.upper() for s in symbols][:MAX_TRADE_SYMBOLS]
        super().__init__(broker=ReplayBroker(feed))
        self._session_date = feed.clock.date().isoformat()
        with session_factory()() as db:
            top = db.query(TradeLog.id).order_by(TradeLog.id.desc()).first()
        self._min_trade_id = int(top[0]) if top else 0

    def _now(self) -> dt.datetime:
        return self.feed.clock

    def _alert(self, message: str) -> None:  # noqa: ARG002
        return None

    def load_config(self) -> BotConfig:
        row = super().load_config()
        data = {col.name: getattr(row, col.name) for col in BotConfig.__table__.columns}
        data["trading_mode"] = "REPLAY"
        data["trade_symbols"] = ",".join(self.replay_symbols)
        view = (data.get("symbol") or "").upper()
        data["symbol"] = view if view in self.replay_symbols else (self.replay_symbols[0] if self.replay_symbols else "")
        cfg = BotConfig(**data)
        self._cfg_cache = cfg
        return cfg

    def _refresh_tick_sizes(self) -> None:
        return None


def close_orphan_replay_rows() -> int:
    """A restart mid-replay leaves REPLAY rows open. Close them flat."""
    now = dt.datetime.now(IST).replace(tzinfo=None)
    with session_factory()() as db:
        rows = (
            db.query(TradeLog)
            .filter(TradeLog.exit_time.is_(None), TradeLog.mode == "REPLAY")
            .all()
        )
        for row in rows:
            row.exit_time = now
            row.exit_price = row.entry_price
            row.exit_reason = "REPLAY_STOPPED"
            row.gross_pnl = 0.0
            row.brokerage_and_taxes = 0.0
            row.net_pnl = 0.0
        db.commit()
        return len(rows)


@dataclass
class ReplaySession:
    """One replay at a time: download, then play the day at the chosen speed."""

    status: str = "IDLE"  # IDLE | LOADING | PLAYING | PAUSED | FINISHED | ERROR
    day: dt.date | None = None
    start: dt.time = SESSION_OPEN
    speed: int = 60
    symbols: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    loaded: int = 0
    error: str = ""
    effective_speed: float = 0.0
    engine: ReplayEngine | None = None
    _task: asyncio.Task | None = None
    _stop: bool = False

    @property
    def active(self) -> bool:
        return self.status in ("LOADING", "PLAYING", "PAUSED", "FINISHED")

    def info(self) -> dict:
        eng = self.engine
        clock = eng.feed.clock if eng else None
        return {
            "status": self.status,
            "date": self.day.isoformat() if self.day else None,
            "start": self.start.strftime("%H:%M"),
            "clock": clock.isoformat() if clock else None,
            "speed": self.speed,
            "effective_speed": round(self.effective_speed, 1),
            "speeds": list(SPEEDS),
            "symbols": self.symbols,
            "skipped": self.skipped,
            "loaded": self.loaded,
            "total": len(self.symbols) + len(self.skipped) if self.status != "LOADING" else self._want,
            "error": self.error,
        }

    _want: int = 0

    async def begin(self, broker, symbols: list[str], day: dt.date, start: dt.time, speed: int) -> None:
        await self.stop("REPLAY_STOPPED")
        self.status = "LOADING"
        self.day, self.start, self.speed = day, start, speed
        self.symbols, self.skipped, self.loaded, self.error = [], [], 0, ""
        self._want = len(symbols)
        self.engine = None
        self._stop = False
        self._task = asyncio.create_task(self._load_and_play(broker, symbols))

    async def _load_and_play(self, broker, symbols: list[str]) -> None:
        assert self.day is not None
        day = self.day
        frames: dict[str, pd.DataFrame] = {}
        first = dt.datetime.combine(day - dt.timedelta(days=WARMUP_DAYS), dt.time(9, 0))
        last = dt.datetime.combine(day, SESSION_END)
        day_start = int(dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST).timestamp())
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
                if frame.empty or not (frame["ts"] >= day_start).any():
                    self.skipped.append(symbol)
                    continue
                frames[symbol] = frame[frame["ts"] < day_start + 86_400].reset_index(drop=True)
                self.loaded += 1
            if not frames:
                raise HistoryError(
                    f"Groww has no 1-minute candles for {day:%d %b %Y}. Pick a trading day "
                    "(not a weekend or holiday) within the last few months."
                )
        except HistoryError as exc:
            self.status = "ERROR"
            self.error = str(exc)
            return
        self.symbols = list(frames)
        clock = dt.datetime.combine(day, self.start, tzinfo=IST)
        feed = ReplayFeed(frames, clock)
        engine = ReplayEngine(feed, self.symbols)
        engine.load_config()
        # Read the tape at the start time, then trade only crosses after it.
        await engine.tick(clock)
        engine.hold_for_next_cross(self.symbols)
        engine.status = "RUNNING"
        self.engine = engine
        self.error = "" if not self.skipped else f"No candles for {', '.join(self.skipped)} on that day."
        self.status = "PLAYING"
        await self._play()

    async def _play(self) -> None:
        eng = self.engine
        assert eng is not None and self.day is not None
        end = dt.datetime.combine(self.day, SESSION_END, tzinfo=IST)
        last = time.monotonic()
        while not self._stop:
            await asyncio.sleep(LOOP_SECONDS)
            now_m = time.monotonic()
            real = now_m - last
            last = now_m
            if self.status != "PLAYING":
                self.effective_speed = 0.0
                continue
            budget = real * self.speed
            played = 0.0
            began = time.monotonic()
            while budget > 0 and not self._stop and self.status == "PLAYING":
                step = min(STEP_SECONDS, budget)
                budget -= step
                played += step
                eng.feed.clock = min(end, eng.feed.clock + dt.timedelta(seconds=step))
                try:
                    await eng.tick(eng.feed.clock)
                except Exception as exc:  # noqa: BLE001
                    eng.last_error = str(exc)
                if eng.feed.clock >= end:
                    await self._finish()
                    return
                # Let the live engine and the API run between ticks.
                await asyncio.sleep(0)
                if time.monotonic() - began > CPU_SHARE * max(real, LOOP_SECONDS):
                    break  # behind: drop the rest, play slower instead of piling up
            spent = time.monotonic() - now_m + LOOP_SECONDS
            sample = played / spent if spent > 0 else 0.0
            self.effective_speed = sample if self.effective_speed == 0 else 0.7 * self.effective_speed + 0.3 * sample

    async def _finish(self) -> None:
        eng = self.engine
        if eng is not None and eng.positions:
            await eng._square_off("EOD_SQUARE_OFF")
        if eng is not None and eng.status == "RUNNING":
            eng.status = "DAY_COMPLETED"
            eng.halt_reason = "Replay finished at 15:30"
        self.status = "FINISHED"

    def control(self, action: str, speed: int | None = None) -> None:
        if speed is not None:
            if speed not in SPEEDS:
                raise ValueError(f"Speed must be one of {', '.join(f'{s}×' for s in SPEEDS)}")
            self.speed = speed
        if action == "pause" and self.status == "PLAYING":
            self.status = "PAUSED"
        elif action == "play" and self.status == "PAUSED":
            self.status = "PLAYING"

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
        eng = self.engine
        if eng is not None and eng.positions:
            try:
                await eng._square_off(reason)
            except Exception:  # noqa: BLE001
                pass
        close_orphan_replay_rows()
        self.engine = None
        self.status = "IDLE"


def parse_replay_day(text: str, now: dt.datetime | None = None) -> dt.date:
    """A finished weekday: before today, or today after 15:30 IST."""
    try:
        day = dt.date.fromisoformat((text or "").strip())
    except ValueError as exc:
        raise ValueError("Pick a date as YYYY-MM-DD.") from exc
    now = (now or dt.datetime.now(IST)).astimezone(IST)
    if day.weekday() >= 5:
        raise ValueError("That is a weekend. Pick a trading day.")
    if day > now.date() or (day == now.date() and (market_is_open(now) or now.time() < SESSION_END)):
        raise ValueError("Pick a day that has already closed. Today replays after 15:30 IST.")
    return day


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
