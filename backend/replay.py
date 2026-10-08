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

import numpy as np
import pandas as pd

from candle_history import HistoryError, fetch_frame
from database import session_factory
from groww_client import IST, OrderAck, market_is_open
from models import BotConfig, ReplayRun, TradeLog
from strategy_engine import MAX_TRADE_SYMBOLS, StrategyEngine, settings_snapshot, trade_names
from tick_sizes import round_price
from scalp_picks import PickRule, picks_for_day

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
    uses_wallet = True  # a replay takes margin from the bots' practice wallet (owner's choice)

    def __init__(
        self,
        feed: ReplayFeed,
        symbols: list[str],
        run_id: int | None = None,
        bot: int = 1,
        settings: dict | None = None,
    ):
        self.feed = feed
        self.run_id = run_id
        # A resumed run plays the settings it was started with, not today's.
        self.saved_settings = dict(settings) if settings else None
        # Whose settings it plays: bot 1 (the main desk) or bots 2-4 (settings rows 3-5).
        # Only read here; a replay never writes to a bot's settings row.
        self.bot_id = int(bot)
        self.config_id = 1 if self.bot_id == 1 else self.bot_id + 1
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
        if self.saved_settings:
            for name, value in self.saved_settings.items():
                if name == "stock_settings":
                    data[name] = value if isinstance(value, str) else json.dumps(value or {})
                elif name in data and name not in ("id", "symbol", "trade_symbols", "trading_mode"):
                    data[name] = value
        data["trading_mode"] = "REPLAY"
        data["max_trades_per_day"] = REPLAY_TRADE_CAP
        data["trade_symbols"] = ",".join(self.replay_symbols)
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


def resumable_runs(bot: int | None = None) -> list[dict]:
    """Runs cut short (a server restart, or Stop) with days still to play, newest first.

    Scalp-pick runs are left out: their stock universe is not kept on the run.
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
            if settings.get("scalp_pick"):
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
                }
            )
    return out


def _drop_unfinished_days(run_id: int, days: list[str]) -> int:
    """Delete a run's trades on days it had not finished, so a resume plays them afresh."""
    if not days:
        return 0
    with session_factory()() as db:
        n = db.query(TradeLog).filter(TradeLog.run_id == run_id, TradeLog.date.in_(days)).delete(synchronize_session=False)
        db.commit()
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


def _run_dict(run: ReplayRun, trades: list[TradeLog], with_days: bool) -> dict:
    day_rows = _day_rows(trades)
    out = {
        "id": run.id,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "start_date": run.start_date,
        "end_date": run.end_date,
        "start_time": run.start_time,
        "symbols": [s for s in (run.symbols or "").split(",") if s],
        "settings": json.loads(run.settings or "{}"),
        "status": run.status,
        "days_total": run.days_total,
        "days_done": run.days_done,
        "totals": _totals(day_rows),
        "capital": _capital(trades),
    }
    if with_days:
        out["days"] = day_rows
        out["stocks"] = _stock_rows(trades, out["symbols"])
    return out


def list_runs(limit: int = 50) -> list[dict]:
    with session_factory()() as db:
        runs = db.query(ReplayRun).order_by(ReplayRun.id.desc()).limit(limit).all()
        ids = [r.id for r in runs]
        trades = db.query(TradeLog).filter(TradeLog.run_id.in_(ids)).all() if ids else []
        by_run: dict[int, list[TradeLog]] = {}
        for t in trades:
            by_run.setdefault(int(t.run_id), []).append(t)
        return [_run_dict(r, by_run.get(r.id, []), with_days=False) for r in runs]


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
        db.delete(run)
        db.commit()
        return True


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
    _settings: dict = field(default_factory=dict)
    engine: ReplayEngine | None = None
    _task: asyncio.Task | None = None
    _stop: bool = False
    _want: int = 0

    @property
    def active(self) -> bool:
        return self.status in ("LOADING", "PLAYING", "PAUSED", "FINISHED")

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
            "pick_rule": self.rule.as_dict() if self.rule is not None else None,
            "picks": self.picks,
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
            if settings.get("scalp_pick"):
                raise ValueError("A Scalp-pick run cannot be resumed. Start it again from the Scalp page.")
            if run.status not in ("INTERRUPTED", "STOPPED") or run.days_done >= run.days_total:
                raise ValueError(f"Run #{run.id} has nothing left to play.")
            all_days = [dt.date.fromisoformat(d) for d in (run.days or "").split(",") if d]
            done = int(run.days_done or 0)
            symbols = [x for x in (run.symbols or "").split(",") if x]
            start = dt.datetime.strptime(run.start_time or "09:15", "%H:%M").time()
            bot = int(settings.get("bot") or 1)
            bot_name = str(settings.get("bot_name") or f"Bot {bot}")
        if not symbols or done >= len(all_days):
            raise ValueError(f"Run #{run_id} has nothing left to play.")
        await self.stop("REPLAY_STOPPED")
        _drop_unfinished_days(int(run_id), [d.isoformat() for d in all_days[done:]])
        self.bot, self.bot_name = bot, bot_name
        self.rule, self.picks, self.universe = None, {}, []
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
        done = 0
        if resume is not None:
            # The run's own day list: finished days count, the rest play now.
            _, all_days, done = resume
            days = all_days
        self.days = days
        if self.rule is not None:
            settings = {**settings, "scalp_pick": {**self.rule.as_dict(), "universe": len(frames), "picks": {}}}
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
        feed = ReplayFeed(frames, dt.datetime.combine(days[done], self.start if done == 0 else SESSION_OPEN, tzinfo=IST))
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
            if self.rule is not None:
                # Pick as the live Scalp page would have at the pick time, then
                # trade only those stocks from that minute on.
                picked = picks_for_day(frames, day, self.rule)
                self.picks[day.isoformat()] = picked
                self._save_picks()
                symbols = [p["symbol"] for p in picked]
                start = self.rule.pick_time
                if not symbols:
                    self._mark_run(days_done=index + 1)
                    continue
            self.symbols = symbols
            clock = dt.datetime.combine(day, start, tzinfo=IST)
            feed.clock = clock
            # A fresh engine per day: its own trade count, loss limit and P&L.
            engine = ReplayEngine(feed, symbols, run_id=self.run_id, bot=self.bot, settings=saved)
            engine.load_config()
            await engine.tick(clock)
            engine.hold_for_next_cross(symbols)
            engine.status = "PAUSED" if bot_paused else "RUNNING"
            self.engine = engine
            if self.status not in ("PLAYING", "PAUSED"):
                self.status = "PLAYING"
            await self._play()
            if self._stop:
                return
            bot_paused = engine.status == "PAUSED"
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
        end = dt.datetime.combine(self.day, SESSION_END, tzinfo=IST)
        last = time.monotonic()
        owed = 0.0  # replay seconds earned but not yet played
        try:
            await self._play_loop(eng, end, last, owed)
        finally:
            _PLAYING.discard(id(self))

    async def _play_loop(self, eng, end: dt.datetime, last: float, owed: float) -> None:
        while not self._stop:
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

    async def _finish_day(self) -> None:
        eng = self.engine
        if eng is not None and eng.positions:
            await eng._square_off("EOD_SQUARE_OFF")
        if eng is not None and eng.status in ("RUNNING", "PAUSED"):
            keep = eng.status
            eng.status = "DAY_COMPLETED" if keep == "RUNNING" else keep
            eng.halt_reason = "Replay day finished at 15:30"

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
        # Only a run still playing is cut short; a finished one keeps FINISHED.
        if self.status in ("LOADING", "PLAYING", "PAUSED"):
            self._mark_run(status="STOPPED")
        # Only this replay's own leftovers: other bots' replays may be playing.
        if self.run_id is not None:
            close_orphan_replay_rows(self.run_id)
        self.engine = None
        self.run_id = None
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


def parse_replay_range(start_text: str, end_text: str | None, now: dt.datetime | None = None) -> tuple[dt.date, dt.date]:
    """From/To for a run: both finished days, To on or after From, at most MAX_RANGE_DAYS (45) calendar days."""
    if not end_text or end_text.strip() == (start_text or "").strip():
        first = parse_replay_day(start_text, now)
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
    first = parse_replay_day(first.isoformat(), now)
    if last < first:
        raise ValueError("To must be on or after From.")
    if (last - first).days > MAX_RANGE_DAYS:
        raise ValueError(f"Pick a range of {MAX_RANGE_DAYS} days or less (about 30 trading days).")
    # The last day must have closed too; weekends inside the range are skipped.
    while last.weekday() >= 5 and last > first:
        last -= dt.timedelta(days=1)
    parse_replay_day(last.isoformat(), now)
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
