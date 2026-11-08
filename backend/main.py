"""SMA(9, 21) + 1.5× ATR intraday terminal API.

Run from `backend/`:

    uvicorn main:app --port 8001

WebSocket `/ws/stream` pushes a 1-second snapshot (LTP, SMAs, ATR, position,
stop, P&L, bot status). REST lives under `/api`.
"""
from __future__ import annotations

import asyncio
import logging
import datetime as dt
import os
import json
import csv
import io
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import gzip

from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, ValidationError, field_validator

from candle_history import HistoryError, fetch_frame, load_history
import all_bots_guard
import pnl_alerts
import pivots
import auto_start
import trend_rank
import scan_arm
import turn_entry as turn_entry_mod
import day_extreme as day_extreme_mod
import fast_bot
import tsl_calc as tsl_calc_mod
import regime_study
import second_candle
import strategy_lab
import morning_study
import trend_picks
import active_picks
import trend_compare
import stock_rank
import exit_join as exit_join_mod
import strategy_presets
import fast_move
import level_check
import news_entry
import arm_log
import handoff_log
import chop_auto
import chop_scan
import high52
import morning_scan
import carry_scan
import turn_scan
import groww_pressure
import open_pnl_log
import pnl_dashboard
import handoff_chain
import scan_accuracy
import scan_history
import scan_sync
import scan_worker
import backtest_server
import server_role
import counter_candles
import ema_filter
import heikin_ashi
import macd
import cum_delta
import reverse_exit
import supertrend
import tall_pullback as tall_pb
import extreme_entry as extreme_mod
import entry_candle as entry_candle_mod
import slope_exit as slope_mod
import stop_handoff as stop_handoff_mod
import stop_plan as stop_plan_mod
import decel_guard as decel_mod
import recovery as recovery_mod
import breakout as breakout_mod
import opening_range
import always_in
import join_trend
from join_trend import MAX_AFTER_MIN as JOIN_AFTER_MAX
import late_entry
import exit_candle
import small_candles
from chop_scan import ChopScanner
from cross_scan import CrossScanner, MAX_SYMBOLS as CROSS_SCAN_MAX
from stall_tracer import tracer as stall_tracer
import deep_health as deep_health_mod
from replay import (
    SPEEDS,
    ReplaySession,
    close_orphan_replay_rows,
    resumable_runs,
    delete_run,
    get_run,
    run_trades,
    list_runs,
    parse_replay_range,
    session_cut,
    parse_start,
    settings_snapshot,
)
import gap_mode
from groww_client import IST, market_is_open, preferred_quote_token
from scalp_picks import MAX_UNIVERSE, PickRule
from database import init_db, session_factory
from models import BotConfig
import candle_patterns
import nth_candle
import paper_wallet
import review
import tick_store
from research import MAX_RESEARCH_SYMBOLS, ResearchEngine, ensure_research_config
import bots as bots_mod
from bots import EXTRA_BOTS, BotEngine, ensure_bot_config
from strategy_engine import (
    BOOK_LIMIT,
    BOOKS,
    STOCK_FIELDS,
    _cfg_for,
    pack_strategies,
    stock_settings,
    MAX_TRADE_SYMBOLS,
    ForceRefused,
    StrategyEngine,
    attach_market_prices,
    apply_trade_sources,
    is_nse_symbol,
    trade_names,
    trade_sources,
)

engine = StrategyEngine()
cross_scanner = CrossScanner()
chop_scanner = ChopScanner()
# The Scalp page's 09:30 Morning tab: five opening checks per stock (morning_scan.py).
morning_scanner = morning_scan.MorningScanner()
# The Scalp page's 52-week high tab: each stock's distance from its 52-week high / low (high52.py).
high52_scanner = high52.High52Scanner()
# The Scalp page's Carry-over tab: did yesterday's SMA trend carry on today (carry_scan.py).
carry_scanner = carry_scan.CarryScanner()
# The Scalp page's Turning tab: candles turning against the SMA trend before the cross (turn_scan.py).
turn_scanner = turn_scan.TurnScanner()
_turn_alert = {"on": False}
_turn_sent = turn_scan.AlertMemory()


def _turn_done(rows: list[dict], minutes: int) -> None:
    """Telegram the stocks newly turning (each stock and way once a day), in market hours, when asked for."""
    now = dt.datetime.now(IST)
    if not _turn_alert["on"] or not rows or not market_is_open(now):
        return
    fresh = _turn_sent.fresh(rows, now)
    if fresh:
        _chop_alert(turn_scan.alert_text(fresh, minutes))


turn_scanner.on_done = _turn_done
# Trend quality re-scan run by the server every N minutes (chop_auto.py).
chop_autos = chop_auto.ChopAuto()
# Scan & arm presets: a Trend quality pass, then arm the matches on a bot, now or at set times (scan_arm.py).
scan_arm_runner = scan_arm.Runner()
scan_checker = scan_accuracy.AccuracyChecker()
open_pnl_recorder = open_pnl_log.OpenPnlRecorder()


def _save_scan(kind: str, snapshot: dict, meta: dict) -> None:
    """Keep a finished pass here, and on the backtest server also in the desk's history (worker thread)."""
    scan_history.save(kind, snapshot, meta)
    backtest_server.push_scan(kind, snapshot, meta)


def _save_check(run: dict):
    """Save an accuracy check here, and on the backtest server also on the desk's copy of the scan."""

    def save(run_id: int, result: dict) -> None:
        scan_history.save_check(run_id, result)
        backtest_server.push_check(run.get("kind"), run.get("as_of"), result)

    return save


def _keep_scan(kind: str):
    """Save each finished pass to the scan history (off the event loop). Never raises."""

    def keep(snapshot: dict, meta: dict) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(asyncio.to_thread(_save_scan, kind, snapshot, meta))
        _history_tasks.add(task)
        task.add_done_callback(_history_tasks.discard)

    return keep


_history_tasks: set = set()
cross_scanner.on_done = _keep_scan("cross")
_keep_chop = _keep_scan("chop")


def _chop_done(snapshot: dict, meta: dict) -> None:
    """A finished Trend quality pass: keep it in the history, then the trend-rank alert (trend_rank.py)."""
    _keep_chop(snapshot, meta)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _send(message: str) -> None:
        from app.services.alert_notifier import alert_notifier

        await alert_notifier.send_quiet(message)

    async def _call(message: str) -> None:
        from app.services.alert_notifier import alert_notifier

        await alert_notifier.place_call(message)

    task = loop.create_task(trend_rank.after_scan(snapshot, _send, _call))
    _history_tasks.add(task)
    task.add_done_callback(_history_tasks.discard)
    task = loop.create_task(_fast_move_scan(snapshot))
    _history_tasks.add(task)
    task.add_done_callback(_history_tasks.discard)


async def _fast_move_send(message: str) -> None:
    from app.services.alert_notifier import alert_notifier

    await alert_notifier.send_fast(message)


async def _fast_move_call(message: str) -> None:
    from app.services.alert_notifier import alert_notifier

    await alert_notifier.place_call(message)


async def _fast_move_scan(snapshot: dict) -> None:
    """Fast-move alert on a finished Trend quality pass (fast_move.py, `scan_on`). Never raises."""
    try:
        s = await asyncio.to_thread(fast_move.load)
        if not s.scan_on or not market_is_open(dt.datetime.now(IST)):
            return
        hits = fast_move.watcher.scan_hits(snapshot.get("rows") or [], s)
        if hits:
            await _fast_move_send(fast_move.scan_text(hits, s))
            if s.call:
                await _fast_move_call(fast_move.call_text(hits))
    except Exception:  # noqa: BLE001
        logging.getLogger("sma.fast_move").exception("fast-move scan alert failed")


def _fast_move_prices() -> dict[str, float]:
    """Last prices of every stock armed or held on bots 1-5 (the bots' own quotes; no extra Groww call)."""
    out: dict[str, float] = {}
    for eng in _all_bots():
        cfg = getattr(eng, "_cfg_cache", None)
        names = set(trade_names(cfg)) if cfg is not None else set()
        names |= {name for name, pos in eng.positions.items() if pos is not None}
        for name in names:
            price = eng._ltps.get(name)
            if price:
                out[name] = float(price)
    return out


chop_scanner.on_done = _chop_done
#: One replay player per SMA bot (1-4), so all four bots can replay at once
#: (meant for after market hours; the machine has 4 CPUs). Bot 1's is `replay`.
replays: dict[int, ReplaySession] = {n: ReplaySession() for n in (1, *EXTRA_BOTS)}
replay = replays[1]


def _replay_for(bot: int) -> ReplaySession:
    session = replays.get(int(bot))
    if session is None:
        raise HTTPException(422, f"bot must be 1 to {EXTRA_BOTS[-1]}")
    return session


_ACTIVE_REPLAY = ("LOADING", "PLAYING", "PAUSED", "FINISHED")


def _bot5_follows() -> ReplaySession | None:
    """The Bot 5 tab follows another bot's replay (owner's choice): when Bot 5 has no replay of its own and a
    Bot 1-4 replay plays with the fast-mover hand-off, the Bot 5 tab shows that run's Bot 5 part
    (replay.bot5_view). The one playing (else paused, else the last finished) is followed."""
    own = replays.get(fast_bot.TARGET_BOT)
    if own is not None and own.status in _ACTIVE_REPLAY:
        return None
    best = None
    for n, session in replays.items():
        if n == fast_bot.TARGET_BOT or session.status not in _ACTIVE_REPLAY:
            continue
        eng = session.engine
        if eng is None or not getattr(eng, "fast", None):
            continue
        rank = ("FINISHED", "LOADING", "PAUSED", "PLAYING").index(session.status)
        if best is None or rank > best[0]:
            best = (rank, session)
    return best[1] if best else None


def _replay_view(bot: int) -> tuple[ReplaySession, bool]:
    """The replay a desk shows: its own, or for Bot 5 the run it follows. (session, following)."""
    if int(bot) == fast_bot.TARGET_BOT:
        source = _bot5_follows()
        if source is not None:
            return source, True
    return _replay_for(bot), False
# Paper-only second bot on the same live quotes (research.py). It reads quotes
# through the live client's refresh and nothing else.


async def _live_quote(symbol: str):
    return await engine.broker.refresh(symbol)


research_engine = ResearchEngine(_live_quote)
# Bots 2-4 (bots.py): full SMA bots with their own settings, book and order
# client; quotes come through the main desk's client.
bot_engines: dict[int, BotEngine] = {n: BotEngine(n, engine.broker) for n in EXTRA_BOTS}


_booted = False
_task: asyncio.Task | None = None
_research_task: asyncio.Task | None = None
_bot_tasks: list[asyncio.Task] = []
_guard_task: asyncio.Task | None = None
_pnl_alert_task: asyncio.Task | None = None
_auto_start_task: asyncio.Task | None = None
_handoff_return_task: asyncio.Task | None = None


_trend_picks_task: asyncio.Task | None = None


def _start_trend_picks_loop() -> None:
    """The daily strong-trend pick backtest (trend_picks.run, off until switched on); one loop per process."""
    global _trend_picks_task
    if _trend_picks_task is None or _trend_picks_task.done():
        _trend_picks_task = asyncio.create_task(trend_picks.run(_trend_picks_daily))


def boot_engine() -> asyncio.Task | None:
    """Start the 1-second loop once. Safe if both the standalone app and a mount call it."""
    global _booted, _task, _research_task
    if _booted:
        return _task
    _booted = True
    # First, so a stall while the rest boots is traced too. Logging only; see stall_tracer.py.
    stall_tracer.start()
    init_db()
    # A deploy or restart mid-replay: those runs can be resumed where they stopped.
    close_orphan_replay_rows(interrupted=True)
    trend_compare.interrupt_running()
    stock_rank.interrupt_running()
    if server_role.is_backtest():
        return _boot_backtest_server()
    cfg = engine.load_config()
    tick_store.set_enabled(getattr(cfg, "second_ticks", None) is not False)
    engine.restore_open_books()
    engine.restore_trades_today()
    engine.broker.set_mode(cfg.trading_mode)
    engine.broker.adopt_saved_session(force=True)
    _task = asyncio.create_task(engine.run())
    chop_autos.reload()
    chop_autos.start_loop(_chop_auto_start)
    scan_arm_runner.start_loop(_scan_arm_scheduled, _scan_arm_can_scan)
    _start_trend_picks_loop()
    open_pnl_recorder.start(_open_pnl_desks)
    ensure_research_config()
    research_engine.restore_open_books()
    research_engine.restore_trades_today()
    _research_task = asyncio.create_task(research_engine.run())
    bots_mod.register(engine)
    for eng in bot_engines.values():
        ensure_bot_config(eng.bot_id)
        bots_mod.register(eng)
        eng.restore_open_books()
        eng.restore_trades_today()
        _bot_tasks.append(asyncio.create_task(eng.run()))
    # Arm log (arm_log.py): stocks already armed get a start row, so "replay as armed" can place them.
    try:
        arm_log.baseline()
    except Exception:  # noqa: BLE001
        logging.getLogger("sma.arm_log").exception("arm log baseline failed")
    # All-bots day target / stop loss (all_bots_guard.py): off until set on Settings.
    global _guard_task
    _guard_task = asyncio.create_task(
        all_bots_guard.run(_all_bots, engine.load_config, lambda: market_is_open(engine._now()), now_fn=engine._now)
    )
    # All-bots P&L alerts (pnl_alerts.py): a message / call at a profit or loss level; alerts only.
    global _pnl_alert_task
    _pnl_alert_task = asyncio.create_task(
        pnl_alerts.run(_all_bots, engine.load_config, lambda: market_is_open(engine._now()), engine._now)
    )
    # Fast-move alerts on armed / held stocks (fast_move.py): off until turned on; alerts only.
    if not server_role.no_orders():
        fast_move.watcher.start(_fast_move_prices, _fast_move_send, _fast_move_call)
        # Fast-mover hand-off (fast_bot.py): a held stock running its trade's way moves to Bot 5.
        fast_bot.watcher.start(_bots_by_number)
    # Auto-start (auto_start.py): presses Start for a STOPPED PAPER bot with it on while NSE is open.
    global _auto_start_task, _handoff_return_task
    if not server_role.no_orders():
        _auto_start_task = asyncio.create_task(
            auto_start.run(_all_bots, _start_bot, lambda: market_is_open(engine._now()))
        )
        # Send handed-off stocks back to the bot they came from after the close (handoff_chain.py); off by default.
        _handoff_return_task = asyncio.create_task(handoff_chain.run(bots_mod.engines, engine._now))
    return _task


def _boot_backtest_server() -> asyncio.Task:
    """Backtest server (backtest_server.py): settings rows and replay players only. No live bot loop,
    scans, guard or alerts run here; the desk's settings are copied in and its Groww session is used."""
    global _task
    engine.load_config()
    ensure_research_config()
    bots_mod.register(engine)
    for eng in bot_engines.values():
        ensure_bot_config(eng.bot_id)
        bots_mod.register(eng)
    engine.broker.set_mode("PAPER")
    # A scan pass (on demand, its own Groww login) keeps the machine awake like a playing replay.
    backtest_server.add_busy_check(
        lambda: any(s.snapshot().get("running") for s in (cross_scanner, chop_scanner, morning_scanner, high52_scanner, carry_scanner, turn_scanner))
    )
    backtest_server.add_busy_check(lambda: scan_arm_runner.busy)
    backtest_server.add_busy_check(lambda: regime_study.study().running)
    backtest_server.add_busy_check(lambda: strategy_lab.lab().running)
    backtest_server.add_busy_check(lambda: second_candle.test().running)
    backtest_server.add_busy_check(lambda: morning_study.study().running)
    backtest_server.add_busy_check(lambda: trend_compare.comparer().running)
    backtest_server.add_busy_check(lambda: stock_rank.jobs().running)
    # The Trend quality auto re-scan runs here too, and keeps the machine awake while it is on in market hours,
    # so it does not stop itself between two passes.
    chop_autos.reload()
    chop_autos.start_loop(_chop_auto_start)
    backtest_server.add_busy_check(_auto_scans_due)
    scan_arm_runner.start_loop(_scan_arm_scheduled, _scan_arm_can_scan)
    _start_trend_picks_loop()
    _task = asyncio.create_task(backtest_server.run(import_bot_settings, lambda: replays))
    return _task


_backtest_bots_on = False
_bt_bot1_task: asyncio.Task | None = None  # bot 1's loop on the backtest server (_task there is the idle watcher)


def _bot_running_here() -> bool:
    """Backtest server: a practice bot is running or paused, or holds a trade (keeps the machine awake)."""
    return any(e.status in ("RUNNING", "PAUSED") or e.positions for e in (engine, *bot_engines.values()))


async def _start_backtest_bots() -> None:
    """Backtest server: Start bot runs the bots here on today's live prices, practice money only (PAPER forced;
    server_role.refuse_orders stops any Groww order, and no alert or call is sent). The first Start starts every
    bot's loop and the fast-mover hand-off, and switches this server to its own Groww login for good (until it
    restarts), so the desk's rate limit is never used by them. Refused without that login."""
    global _backtest_bots_on
    from groww_client import _read_desk_token

    if not await asyncio.to_thread(_read_desk_token):
        raise HTTPException(
            409,
            "Log in to Groww on this backtest server first (Settings → Broker keys). Its practice bots use its own "
            "Groww login, never the main desk's, so they do not slow the desk's bots.",
        )
    if _backtest_bots_on:
        return
    _backtest_bots_on = True
    backtest_server.use_own_login()
    engine.broker.adopt_saved_session(force=True)
    for eng in (engine, *bot_engines.values()):
        eng.broker.set_mode("PAPER")
        eng.restore_open_books()
        eng.restore_trades_today()
    global _bt_bot1_task
    _bt_bot1_task = asyncio.create_task(engine.run())
    for eng in bot_engines.values():
        _bot_tasks.append(asyncio.create_task(eng.run()))
    fast_bot.watcher.start(_bots_by_number)
    backtest_server.add_busy_check(_bot_running_here)


def _auto_scans_due(now: dt.datetime | None = None) -> bool:
    """Backtest server: the auto re-scan or a scheduled Scan & arm wants this machine during market hours."""
    now = now or dt.datetime.now(IST)
    if _scans_paused or not market_is_open(now):
        return False
    if chop_autos.settings.on:
        return True
    return any(p.on and p.times for p in scan_arm.load())


def _bots_by_number() -> dict[int, StrategyEngine]:
    """Every SMA bot by its number (1 = the main desk)."""
    return {1: engine, **bot_engines}


def _all_bots() -> list[StrategyEngine]:
    """Bots 1-4, the ones the all-bots target / stop loss adds up and stops (not research)."""
    return [engine, *bot_engines.values()]


async def announce_shutdown_all() -> None:
    """One alert (and a call) for every SMA bot when the process stops."""
    from strategy_engine import announce_process_stop

    await announce_process_stop([engine, *bot_engines.values()])


def stop_engine() -> None:
    stall_tracer.stop()
    chop_autos.stop()
    fast_move.watcher.stop()
    fast_bot.watcher.stop()
    scan_arm_runner.stop()
    if _trend_picks_task is not None and not _trend_picks_task.done():
        _trend_picks_task.cancel()
    open_pnl_recorder.stop()
    scan_checker.stop("Stopped: the app is shutting down.")
    scan_worker.shutdown()
    engine.stop()
    research_engine.stop()
    for eng in bot_engines.values():
        eng.stop()
    for task in (_task, _research_task, _guard_task, _pnl_alert_task, _auto_start_task, _handoff_return_task, _bt_bot1_task, *_bot_tasks):
        if task is not None and not task.done():
            task.cancel()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = boot_engine()
    try:
        yield
    finally:
        stop_engine()
        if task is not None:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(title="SMA ATR Intraday Terminal", lifespan=lifespan)
# A full trade book is a few MB of repetitive JSON; gzip cuts it ~10x.
app.add_middleware(GZipMiddleware, minimum_size=16384)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ],
    # Phone browsers send Origin http://<your-LAN-ip>:3000, not localhost.
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1|192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3})(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ConfigUpdate(BaseModel):
    symbol: str | None = None
    qty: int | None = Field(default=None, ge=1, le=100_000)
    sma_fast: int | None = Field(default=None, ge=2, le=100)
    sma_medium: int | None = Field(default=None, ge=3, le=200)
    sma_slow: int | None = Field(default=None, ge=3, le=300)
    atr_period: int | None = Field(default=None, ge=2, le=100)
    atr_multiplier: float | None = Field(default=None, gt=0, le=10)
    use_adx_filter: bool | None = None
    use_stop: bool | None = None
    stop_type: Literal["ATR", "SMA_GAP", "TSL", "SUPERTREND", "VWAP"] | None = None
    # VWAP + ATR buffer stop: stop = VWAP − this × ATR for a buy (+ for a sell), tighter only each candle.
    vwap_stop_atr: float | None = Field(default=None, ge=0, le=5)
    tsl_sl_points: float | None = Field(default=None, gt=0, le=100000)
    tsl_trail_points: float | None = Field(default=None, gt=0, le=100000)
    tsl_target_points: float | None = Field(default=None, ge=0, le=100000)
    tsl_mode: Literal["POINTS", "PERCENT", "ATR"] | None = None
    tsl_sl_pct: float | None = Field(default=None, gt=0, le=50)
    tsl_trail_pct: float | None = Field(default=None, gt=0, le=50)
    tsl_target_pct: float | None = Field(default=None, ge=0, le=100)
    # ATR multiples (tsl_mode ATR): sized from the stock's own ATR at entry, so one setting fits every stock.
    tsl_sl_atr: float | None = Field(default=None, gt=0, le=20)
    tsl_trail_atr: float | None = Field(default=None, gt=0, le=20)
    tsl_target_atr: float | None = Field(default=None, ge=0, le=50)
    gap_sl_mult: float | None = Field(default=None, gt=0, le=10)
    gap_tp_mult: float | None = Field(default=None, gt=0, le=20)
    gap_min_pct: float | None = Field(default=None, ge=0.01, le=5)
    adx_threshold: float | None = Field(default=None, ge=0, le=100)
    use_vwap: bool | None = None
    use_volume: bool | None = None
    volume_min_ratio: float | None = Field(default=None, gt=0, le=10)
    use_density: bool | None = None
    density_min_pct: float | None = Field(default=None, ge=1, le=100)
    use_rsi: bool | None = None
    rsi_long_min: float | None = Field(default=None, ge=0, le=100)
    rsi_long_max: float | None = Field(default=None, ge=0, le=100)
    rsi_short_min: float | None = Field(default=None, ge=0, le=100)
    rsi_short_max: float | None = Field(default=None, ge=0, le=100)
    use_bollinger: bool | None = None
    bb_period: int | None = Field(default=None, ge=5, le=100)
    bb_std: float | None = Field(default=None, gt=0, le=5)
    bb_min_width_pct: float | None = Field(default=None, ge=0, le=10)
    bb_exit: Literal["OFF", "BAND", "MIDDLE", "BOTH"] | None = None
    use_gap_long: bool | None = None
    gap_long_min: float | None = Field(default=None, ge=-10, le=10)
    gap_long_max: float | None = Field(default=None, ge=-10, le=10)
    use_gap_short: bool | None = None
    gap_short_min: float | None = Field(default=None, ge=-10, le=10)
    gap_short_max: float | None = Field(default=None, ge=-10, le=10)
    use_gap_mode: bool | None = None
    use_candle_dir: bool | None = None
    candle_dir_count: int | None = Field(default=None, ge=1, le=10)
    candle_dir_rule: Literal["CLOSES", "COLOUR", "BOTH"] | None = None
    gap_entry_long: float | None = Field(default=None, ge=-10, le=10)
    gap_exit_long: float | None = Field(default=None, ge=-10, le=10)
    gap_entry_short: float | None = Field(default=None, ge=-10, le=10)
    gap_exit_short: float | None = Field(default=None, ge=-10, le=10)
    gap_giveback_pct: float | None = Field(default=None, ge=0, le=100)
    gap_fade_confirm_sma: bool | None = None
    gap_fade_min_candles: int | None = Field(default=None, ge=0, le=30)
    gap_fade_intrabar: bool | None = None
    gap_fade_minute: bool | None = None
    flip_orders: bool | None = None
    cross_exit: bool | None = None
    late_entry_min: int | None = Field(default=None, ge=0, le=late_entry.MAX_MIN)
    join_trend: bool | None = None
    always_in: bool | None = None
    always_in_reverse: bool | None = None
    counter_exit_count: int | None = Field(default=None, ge=0, le=counter_candles.MAX_COUNT)
    counter_entry: bool | None = None
    counter_rule: Literal["COLOUR", "CLOSES", "BOTH"] | None = None
    counter_need_fast: bool | None = None
    counter_min_body: float | None = Field(default=None, ge=0, le=counter_candles.MAX_MIN_BODY)
    counter_reenter: bool | None = None
    counter_reenter_max: int | None = Field(default=None, ge=1, le=counter_candles.MAX_REENTER)
    use_ema: bool | None = None
    ema_exit: bool | None = None
    ema_fast: int | None = Field(default=None, ge=1, le=ema_filter.MAX_LEN)
    ema_slow: int | None = Field(default=None, ge=1, le=ema_filter.MAX_LEN)
    use_macd: bool | None = None
    macd_exit: bool | None = None
    macd_fast: int | None = Field(default=None, ge=1, le=macd.MAX_LEN)
    macd_slow: int | None = Field(default=None, ge=1, le=macd.MAX_LEN)
    macd_signal: int | None = Field(default=None, ge=1, le=macd.MAX_LEN)
    use_cvd: bool | None = None
    cvd_lookback: int | None = Field(default=None, ge=1, le=cum_delta.MAX_LOOKBACK)
    cvd_div_exit: bool | None = None
    cvd_div_lookback: int | None = Field(default=None, ge=cum_delta.MIN_DIV_LOOKBACK, le=cum_delta.MAX_DIV_LOOKBACK)
    # Reverse instead of only closing (reverse_exit.py): CSV of VTURN / TURN / CANDLES, "" = off.
    reverse_on_exit: str | None = Field(default=None, max_length=40)

    @field_validator("reverse_on_exit", mode="before")
    @classmethod
    def _reverse_keys(cls, value):
        return None if value is None else reverse_exit.clean(value)

    # A reversed trade closes on the first candle of the other colour (reverse_exit.colour_exit_on).
    reverse_colour_exit: bool | None = None
    # How exits combine (exit_join.py): OR = any one closes; AND = the ticked group (CSV of exit_join.KEYS) must agree,
    # exit_need of them (0 = all) within exit_within candles.
    exit_join: Literal["OR", "AND"] | None = None
    exit_group: str | None = Field(default=None, max_length=120)
    exit_need: int | None = Field(default=None, ge=0, le=len(exit_join_mod.KEYS))
    exit_within: int | None = Field(default=None, ge=1, le=exit_join_mod.MAX_WITHIN)

    @field_validator("breakout_trend_checks", mode="before")
    @classmethod
    def _trend_checks(cls, value):
        """Strong-trend checks for the breakout rule (breakout.parse_trend_rules); stored as the full list."""
        if value is None:
            return None
        rules = breakout_mod.parse_trend_rules(value)
        return ",".join(f"{k}:{v}" for k, v in rules.items())

    @field_validator("colour_body_by_tf", mode="before")
    @classmethod
    def _colour_body_by_tf(cls, value):
        """Smallest candle-colour body per candle size (candle_patterns.clean_body_by_tf): "5:0.05,15:0.15"."""
        import candle_patterns

        return None if value is None else candle_patterns.clean_body_by_tf(value)

    @field_validator("exit_group", mode="before")
    @classmethod
    def _exit_group_keys(cls, value):
        return None if value is None else exit_join_mod.clean_group(value)

    # Reversed trades' own entry checks / exits / stop (reverse_exit.FIELDS), a JSON object; "" = none.
    reverse_settings: str | dict | None = None

    @field_validator("reverse_settings", mode="before")
    @classmethod
    def _reverse_settings(cls, value):
        if value is None:
            return None

        def check(fields: dict) -> dict:
            try:
                cleaned = ConfigUpdate(**fields)
            except ValidationError as exc:
                first = exc.errors()[0]
                where = ".".join(str(p) for p in first.get("loc", ()))
                raise ValueError(f"reverse_settings {where}: {first.get('msg')}") from None
            return cleaned.model_dump(include=set(fields), exclude_none=True)

        return reverse_exit.clean_settings(value, check)
    ha_colours: bool | None = None
    use_supertrend: bool | None = None
    use_level: bool | None = None
    level_line: str | None = Field(default=None, pattern="^(BB_MIDDLE|BB_UPPER|PIVOT_P|PIVOT_R1)$")
    use_high52: bool | None = None
    high52_side: str | None = Field(default=None, pattern="^(LONG|SHORT|BOTH)$")
    high52_pct: float | None = Field(default=None, ge=0, le=high52.MAX_PCT)
    st_exit: bool | None = None
    st_period: int | None = Field(default=None, ge=1, le=supertrend.MAX_PERIOD)
    st_mult: float | None = Field(default=None, ge=supertrend.MIN_FACTOR, le=supertrend.MAX_FACTOR)
    tall_pullback: bool | None = None
    tall_atr_mult: float | None = Field(default=None, ge=tall_pb.MIN_MULT, le=tall_pb.MAX_MULT)
    tall_expiry_candles: int | None = Field(default=None, ge=1, le=tall_pb.MAX_EXPIRY)
    extreme_entry: bool | None = None
    extreme_anchor: Literal["CANDLE_EXTREME", "SMA_9"] | None = None
    extreme_invalid_pct: float | None = Field(default=None, ge=0, le=extreme_mod.MAX_INVALID_PCT)
    extreme_expiry_candles: int | None = Field(default=None, ge=1, le=extreme_mod.MAX_EXPIRY)
    extreme_cancel_on_cross: bool | None = None
    entry_candle_confirm: bool | None = None
    entry_candle_pct: float | None = Field(default=None, ge=0, le=entry_candle_mod.MAX_PCT)
    entry_candle_max: int | None = Field(default=None, ge=1, le=entry_candle_mod.MAX_CANDLES)
    slope_exit: bool | None = None
    slope_lookback: int | None = Field(default=None, ge=1, le=slope_mod.MAX_LOOKBACK)
    slope_bend_pct: float | None = Field(default=None, ge=0, le=100)
    slope_turn: bool | None = None
    slope_turn_pct: float | None = Field(default=None, ge=0, le=slope_mod.MAX_TURN_PCT)
    vturn_exit: bool | None = None
    turn_alert: bool | None = None
    turn_entry: bool | None = None
    uturn_on: bool | None = None
    turn_toward_cross: bool | None = None
    turn_exit_candle: bool | None = None
    turn_colour_exit: bool | None = None
    uturn_run: int | None = Field(default=None, ge=2, le=turn_entry_mod.MAX_U_RUN)
    uturn_min_pct: float | None = Field(default=None, ge=0, le=turn_entry_mod.MAX_U_MIN)
    day_extreme_guard: bool | None = None
    day_extreme_pct: float | None = Field(default=None, ge=0, le=day_extreme_mod.MAX_PCT)
    day_extreme_pull_pct: float | None = Field(default=None, ge=0, le=day_extreme_mod.MAX_PCT)
    day_extreme_candles: int | None = Field(default=None, ge=1, le=day_extreme_mod.MAX_CANDLES)
    vturn_move_pct: float | None = Field(default=None, ge=0, le=slope_mod.MAX_TURN_PCT)
    vturn_turn_pct: float | None = Field(default=None, ge=0, le=slope_mod.MAX_TURN_PCT)
    vturn_within: int | None = Field(default=None, ge=1, le=slope_mod.MAX_VTURN_WITHIN)
    vturn_handoff: bool | None = None
    stop_handoff: Literal["OFF", "BACK", "BOT2", "BOT3"] | None = None
    stop_handoff_loss: bool | None = None
    handoff_now: bool | None = None
    auto_start: bool | None = None
    pivot_target: Literal["OFF", "R1", "R2"] | None = None
    stop_plan: bool | None = None
    plan_be_r: float | None = Field(default=None, gt=0, le=stop_plan_mod.MAX_R)
    plan_trail_r: float | None = Field(default=None, gt=0, le=stop_plan_mod.MAX_R)
    decel_exit: bool | None = None
    decel_lookback: int | None = Field(default=None, ge=1, le=10)
    decel_arm_atr: float | None = Field(default=None, ge=0, le=5)
    decel_drop_pct: float | None = Field(default=None, ge=0, le=100)
    decel_min_atr: float | None = Field(default=None, ge=0, le=5)
    decel_candles: int | None = Field(default=None, ge=1, le=5)
    decel_confirm: Literal["OFF", "CLOSE"] | None = None
    decel_pierce_atr: float | None = Field(default=None, ge=0, le=5)
    decel_peak_from: Literal["ENTRY", "RUN"] | None = None
    slow_sma_exit: bool | None = None
    recovery_hold: bool | None = None
    recovery_tolerance_pct: float | None = Field(default=None, ge=0, le=5)
    recovery_emergency_mult: float | None = Field(default=None, ge=1, le=10)
    recovery_atr_mult: float | None = Field(default=None, ge=0.5, le=20)
    recovery_max_candles: int | None = Field(default=None, ge=1, le=recovery_mod.MAX_CANDLES)
    recovery_handoff: bool | None = None
    breakout_handoff: bool | None = None
    breakout_atr_mult: float | None = Field(default=None, ge=0.5, le=10)
    breakout_gap_pct: float | None = Field(default=None, ge=0, le=5)
    breakout_volume_mult: float | None = Field(default=None, ge=1, le=10)
    breakout_rule: Literal["CANDLE", "STRONG"] | None = None
    breakout_cut: bool | None = None
    breakout_trend_checks: str | None = Field(default=None, max_length=160)
    breakout_trend_need: int | None = Field(default=None, ge=1, le=7)
    breakout_when: Literal["ANY", "IN_PROFIT"] | None = None
    chop_losses: int | None = Field(default=None, ge=0, le=10)
    use_or_volume: bool | None = None
    use_or_vwap: bool | None = None
    use_or_spread: bool | None = None
    use_or_break: bool | None = None
    or_volume_ratio: float | None = Field(default=None, ge=0, le=20)
    or_spread_pct: float | None = Field(default=None, ge=0, le=5)
    join_after_min: int | None = Field(default=None, ge=0, le=JOIN_AFTER_MAX)
    entry_mode: Literal["SMA", "PATTERN", "COLOUR", "NTH_CANDLE"] | None = None
    pattern_tf: Literal[1, 3, 5, 10, 15, 30, 60] | None = None
    candle_minutes: Literal[1, 2, 3, 5, 10, 15] | None = None
    exit_candle_minutes: Literal[0, 1, 2, 3, 5, 10, 15, 30, 60] | None = None
    # Watch smaller candles while in a trade (small_candles.py): CSV of sizes, "" = off.
    small_exit_minutes: str | None = Field(default=None, max_length=32)
    small_exit_cross: bool | None = None
    small_exit_slope: bool | None = None
    small_exit_counter: int | None = Field(default=None, ge=0, le=small_candles.MAX_COUNTER)
    small_exit_loss_pct: float | None = Field(default=None, ge=0, le=50)
    small_exit_agree: int | None = Field(default=None, ge=1, le=small_candles.MAX_AGREE)
    slope_minutes: Literal[0, 1, 2, 3, 5, 10, 15] | None = None
    pattern_trend: bool | None = None
    pattern_set: Literal["STRONG", "ALL"] | None = None
    pattern_min_edge: float | None = Field(default=None, ge=0, le=10)
    colour_count: int | None = Field(default=None, ge=1, le=5)
    colour_min_body: float | None = Field(default=None, ge=0, le=2)
    colour_body_unit: Literal["PCT", "ATR"] | None = None
    colour_body_by_tf: str | None = Field(default=None, max_length=120)
    colour_body_atr: float | None = Field(default=None, ge=0, le=5)
    # Second candle entries (nth_candle.py).
    nth_candle_no: int | None = Field(default=None, ge=1, le=nth_candle.CANDLE_NO_MAX)
    nth_candle_minutes: Literal[1, 3, 5, 10, 15] | None = None
    nth_min_body_pct: float | None = Field(default=None, ge=0, le=nth_candle.BODY_MAX)
    nth_min_rvol: float | None = Field(default=None, ge=0, le=nth_candle.RVOL_MAX)
    review_on: bool | None = None
    review_gap_pct: float | None = Field(default=None, gt=0, le=2)
    review_cooldown_min: int | None = Field(default=None, ge=0, le=375)
    review_check_minutes: list[Literal[1, 2, 3, 5, 10, 15]] | Literal[1, 2, 3, 5, 10, 15] | None = None
    review_check_mode: Literal["ALWAYS", "ONLY_IF_AGAINST", "ONLY_IF_NOT_WITH"] | None = None
    review_default_answer: Literal["PROMPT", "EXIT", "REVERSE", "CONTINUE"] | None = None
    review_conditions: str | None = Field(default=None, max_length=32, pattern=r"^(GAP|SLOPE|STEEP)(,(GAP|SLOPE|STEEP)){0,2}$")
    review_join: Literal["OR", "AND"] | None = None
    review_slope_candles: int | None = Field(default=None, ge=1, le=review.MAX_SLOPE_CANDLES)
    review_slope_flat: float | None = Field(default=None, ge=-1, le=1)
    review_steep_pct: float | None = Field(default=None, gt=0, le=2)
    bot_name: str | None = Field(default=None, min_length=1, max_length=24)
    gap_entry_delay_min: int | None = Field(default=None, ge=0, le=120)
    gap_entry_window_min: int | None = Field(default=None, ge=0, le=375)
    max_daily_loss: float | None = Field(default=None, gt=0)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=100)
    # Entries one stock may take per day on this bot (0 = no cap); also a per-stock override.
    max_trades_per_stock: int | None = Field(default=None, ge=0, le=100)
    square_off_time: str | None = None
    entry_cutoff_time: str | None = None


class ModeUpdate(BaseModel):
    mode: str
    confirm_live: bool = False


def _config_dict(row: BotConfig) -> dict:
    return {
        "bot_name": getattr(row, "bot_name", None) or None,
        "symbol": row.symbol,
        "trade_symbols": trade_names(row),
        "trade_sources": trade_sources(row),
        "exchange": row.exchange,
        "qty": row.qty,
        "sma_fast": row.sma_fast,
        "sma_medium": getattr(row, "sma_medium", None),
        "sma_slow": row.sma_slow,
        "atr_period": row.atr_period,
        "atr_multiplier": row.atr_multiplier,
        "use_adx_filter": row.use_adx_filter,
        "use_stop": True if row.use_stop is None else bool(row.use_stop),
        "stop_type": (getattr(row, "stop_type", None) or "ATR").upper(),
        "gap_sl_mult": float(getattr(row, "gap_sl_mult", 1.0) or 1.0),
        "gap_tp_mult": float(getattr(row, "gap_tp_mult", 2.0) or 2.0),
        "gap_min_pct": float(getattr(row, "gap_min_pct", 0.2) or 0.2),
        "tsl_sl_points": float(getattr(row, "tsl_sl_points", 20.0) or 20.0),
        "tsl_trail_points": float(getattr(row, "tsl_trail_points", 10.0) or 10.0),
        "tsl_target_points": float(getattr(row, "tsl_target_points", 0.0) or 0.0),
        "tsl_mode": str(getattr(row, "tsl_mode", None) or "POINTS").upper(),
        "tsl_sl_pct": float(getattr(row, "tsl_sl_pct", 1.0) or 1.0),
        "tsl_trail_pct": float(getattr(row, "tsl_trail_pct", 0.5) or 0.5),
        "tsl_target_pct": float(getattr(row, "tsl_target_pct", 0.0) or 0.0),
        "tsl_sl_atr": float(getattr(row, "tsl_sl_atr", 1.5) or 1.5),
        "tsl_trail_atr": float(getattr(row, "tsl_trail_atr", 0.5) or 0.5),
        "tsl_target_atr": 3.0 if getattr(row, "tsl_target_atr", None) is None else float(row.tsl_target_atr),
        "vwap_stop_atr": 0.5 if getattr(row, "vwap_stop_atr", None) is None else float(row.vwap_stop_atr),
        "adx_threshold": row.adx_threshold,
        "use_vwap": bool(getattr(row, "use_vwap", False)),
        "use_volume": bool(getattr(row, "use_volume", False)),
        "volume_min_ratio": float(getattr(row, "volume_min_ratio", 1.0) or 1.0),
        "use_density": bool(getattr(row, "use_density", False)),
        "density_min_pct": float(getattr(row, "density_min_pct", 50.0) or 50.0),
        "use_rsi": bool(getattr(row, "use_rsi", False)),
        "rsi_long_min": float(getattr(row, "rsi_long_min", 40.0) or 40.0),
        "rsi_long_max": float(getattr(row, "rsi_long_max", 70.0) or 70.0),
        "rsi_short_min": float(getattr(row, "rsi_short_min", 30.0) or 30.0),
        "rsi_short_max": float(getattr(row, "rsi_short_max", 60.0) or 60.0),
        "use_bollinger": bool(getattr(row, "use_bollinger", False)),
        "bb_period": int(getattr(row, "bb_period", 20) or 20),
        "bb_std": float(getattr(row, "bb_std", 2.0) or 2.0),
        "bb_min_width_pct": float(getattr(row, "bb_min_width_pct", 0.15) if getattr(row, "bb_min_width_pct", None) is not None else 0.15),
        "bb_exit": (getattr(row, "bb_exit", None) or "OFF").upper(),
        **_gap_dict(row),
        "use_candle_dir": bool(getattr(row, "use_candle_dir", False)),
        "candle_dir_count": int(getattr(row, "candle_dir_count", None) or 2),
        "candle_dir_rule": (getattr(row, "candle_dir_rule", None) or "CLOSES").upper(),
        "max_daily_loss": row.max_daily_loss,
        "max_trades_per_day": row.max_trades_per_day,
        "max_trades_per_stock": int(getattr(row, "max_trades_per_stock", 0) or 0),
        "square_off_time": row.square_off_time,
        "entry_cutoff_time": row.entry_cutoff_time or "15:00",
        "trading_mode": row.trading_mode,
        # Each stock's own strategy settings over the shared ones above.
        "stock_settings": stock_settings(row),
        "stock_fields": list(STOCK_FIELDS),
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "mode": engine.load_config().trading_mode, "bot": engine.status}


def deep_health() -> tuple[dict, bool]:
    """What a watcher needs: the loop is beating and every bot has ticked lately."""
    tasks = {1: _task, **{n: t for n, t in zip(bot_engines, _bot_tasks)}}
    rows = [deep_health_mod.bot_row(1, engine, tasks.get(1))]
    rows += [deep_health_mod.bot_row(n, eng, tasks.get(n)) for n, eng in bot_engines.items()]
    out = deep_health_mod.evaluate(
        market_open=market_is_open(engine._now()), tracer=stall_tracer.snapshot(), bots=rows
    )
    return out, out["ok"]


@app.get("/api/health/deep")
async def health_deep():
    body, ok = deep_health()
    return JSONResponse(body, status_code=200 if ok else 503)


async def _state(eng: StrategyEngine):
    return eng.snapshot()


@app.get("/api/state")
async def state():
    return await _state(engine)


@app.get("/api/research/state")
async def research_state():
    return await _state(_research())


def _chart_symbol(symbol: str | None) -> str | None:
    name = (symbol or "").strip().upper()
    if not name:
        return None
    if not is_nse_symbol(name):
        raise HTTPException(400, f"{symbol!r} is not an NSE trading symbol.")
    return name


async def _chart(eng: StrategyEngine, limit: int = 240, symbol: str | None = None, interval: int | None = None):
    # The 5/15/30/60-minute views build their bars from more 1-minute candles.
    # `interval` draws the 1-minute tape in candles of that size (finer than the bot's own).
    # Off the event loop: building the chart must not hold up /api/state.
    return await asyncio.to_thread(eng.chart_payload, max(30, min(int(limit), 2500)), _chart_symbol(symbol), interval)


@app.get("/api/chart")
async def chart(limit: int = 240, symbol: str | None = None, interval: int | None = None):
    return await _chart(engine, limit, symbol, interval)


@app.get("/api/research/chart")
async def research_chart(limit: int = 240, symbol: str | None = None, interval: int | None = None):
    return await _chart(_research(), limit, symbol, interval)


async def _history(eng: StrategyEngine, symbol: str, start: str, end: str, interval: int = 1, run_id: int | None = None):
    """Past candles from Groww for the chart's From/To view. Read-only.

    `run_id` marks one replay run's trades; without it, the practice/real
    trades plus the latest replay run in the range.
    """
    try:
        # Candles come through the live desk's Groww client; the research desk
        # marks only its own trades on them.
        book = "RESEARCH" if isinstance(eng, ResearchEngine) else None
        bot = None if isinstance(eng, ResearchEngine) else eng.bot_id
        return await load_history(engine.broker, symbol, start, end, eng.load_config(), interval, run_id, book, bot)
    except HistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/history")
async def history(symbol: str, start: str, end: str, interval: int = 1, run_id: int | None = None):
    return await _history(engine, symbol, start, end, interval, run_id)


@app.get("/api/research/history")
async def research_history(symbol: str, start: str, end: str, interval: int = 1, run_id: int | None = None):
    return await _history(_research(), symbol, start, end, interval, run_id)


async def _get_config(eng: StrategyEngine):
    return _config_dict(eng.load_config())


@app.get("/api/config")
async def get_config():
    return await _get_config(engine)


@app.get("/api/research/config")
async def research_get_config():
    return await _get_config(_research())


class TradeSymbolUpdate(BaseModel):
    symbol: str
    armed: bool
    # Where the arm came from, shown as a chip on the Stocks panel. Information only.
    source: str | None = Field(default=None, max_length=40)


class TradeSymbolCopy(BaseModel):
    """Copy stocks from one bot's Trade list onto another bot's. The source bot keeps them."""

    from_bot: Literal[1, 2, 3, 4, 5]
    to_bot: Literal[1, 2, 3, 4, 5]
    symbols: list[str] = Field(default_factory=list, max_length=MAX_TRADE_SYMBOLS)
    source: str | None = Field(default=None, max_length=40)


def _research() -> ResearchEngine:
    return research_engine


def _symbol_cap(eng: StrategyEngine) -> int:
    return MAX_RESEARCH_SYMBOLS if isinstance(eng, ResearchEngine) else MAX_TRADE_SYMBOLS


def _reload(eng: StrategyEngine, payload: dict) -> None:
    """Point the engine at the row just saved; report its book (RESEARCH on that desk)."""
    cfg = eng.load_config()
    if "trading_mode" in payload:
        payload["trading_mode"] = cfg.trading_mode


def _open_symbols(eng: StrategyEngine | None = None) -> set[str]:
    eng = engine if eng is None else eng
    return {symbol for symbol, pos in eng.positions.items() if pos is not None}


async def _set_trade_symbol(eng: StrategyEngine, body: TradeSymbolUpdate):
    """Arm or disarm a stock without changing the chart on screen."""
    symbol = (body.symbol or "").upper().strip()
    if not is_nse_symbol(symbol):
        raise HTTPException(400, "Symbol must be an NSE trading symbol")
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        names = trade_names(row)
        source_update: dict[str, str | None] = {}
        if body.armed:
            if symbol not in names:
                cap = _symbol_cap(eng)
                if len(names) >= cap:
                    raise HTTPException(409, f"Trade is limited to {cap} stocks at once")
                if (row.trading_mode or "PAPER").upper() == "LIVE":
                    why = bots_mod.live_conflict(eng.bot_id, [symbol])
                    if why:
                        raise HTTPException(409, why)
                names.append(symbol)
                eng.hold_for_next_cross([symbol])
            if body.source:
                source_update[symbol] = body.source
        else:
            if symbol in _open_symbols(eng):
                raise HTTPException(409, f"Close {symbol} before taking it off the trade buttons")
            names = [name for name in names if name != symbol]
            source_update[symbol] = None
        row.trade_symbols = ",".join(names)
        row.trade_sources = apply_trade_sources(row, source_update, names)
        db.commit()
        db.refresh(row)
        payload = _config_dict(row)
        db.expunge(row)
    _reload(eng, payload)
    return payload


@app.post("/api/trade-symbols")
async def set_trade_symbol(body: TradeSymbolUpdate):
    return await _set_trade_symbol(engine, body)


@app.post("/api/research/trade-symbols")
async def research_set_trade_symbol(body: TradeSymbolUpdate):
    return await _set_trade_symbol(_research(), body)


def _bot_engine_for(bot: int) -> StrategyEngine:
    return engine if bot == 1 else bot_engines[bot]


@app.post("/api/trade-symbols/copy")
async def copy_trade_symbols(body: TradeSymbolCopy):
    """Copy stocks onto another bot's Trade list. The source bot keeps them."""
    if body.from_bot not in (1, *EXTRA_BOTS) or body.to_bot not in (1, *EXTRA_BOTS):
        raise HTTPException(400, "Pick a bot between 1 and 4")
    if body.from_bot == body.to_bot:
        raise HTTPException(400, "Pick a different destination bot")
    wanted = [s.strip().upper() for s in body.symbols if is_nse_symbol((s or "").strip().upper())]
    wanted = [s for s in dict.fromkeys(wanted) if s]
    if not wanted:
        raise HTTPException(400, "No stocks to copy")
    src_eng = _bot_engine_for(body.from_bot)
    dst_eng = _bot_engine_for(body.to_bot)
    # In tests (and the first touch after a fresh DB) the extra bots' rows have not been
    # created yet; the live app does this at boot but tests ask ad-hoc. No-op otherwise.
    for bot in (body.from_bot, body.to_bot):
        if bot in EXTRA_BOTS:
            ensure_bot_config(bot)
    added: list[str] = []
    skipped: list[dict] = []
    with session_factory()() as db:
        src_row = db.get(BotConfig, src_eng.config_id)
        dst_row = db.get(BotConfig, dst_eng.config_id)
        if src_row is None or dst_row is None:
            raise HTTPException(500, "BotConfig missing")
        src_names = trade_names(src_row)
        on_source = [s for s in wanted if s in src_names]
        off_source = [s for s in wanted if s not in src_names]
        for name in off_source:
            skipped.append({"symbol": name, "why": f"not on bot {body.from_bot}'s Trade list"})
        dst_names = trade_names(dst_row)
        cap = _symbol_cap(dst_eng)
        source_label = (body.source or f"Copied from bot {body.from_bot}").strip() or f"Copied from bot {body.from_bot}"
        src_sources = trade_sources(src_row)
        source_update: dict[str, str | None] = {}
        is_live = (dst_row.trading_mode or "PAPER").upper() == "LIVE"
        for name in on_source:
            if name in dst_names:
                skipped.append({"symbol": name, "why": f"already on bot {body.to_bot}'s Trade list"})
                continue
            if len(dst_names) >= cap:
                skipped.append({"symbol": name, "why": f"bot {body.to_bot} is at its {cap} stock cap"})
                continue
            if is_live:
                why = bots_mod.live_conflict(dst_eng.bot_id, [name])
                if why:
                    skipped.append({"symbol": name, "why": why})
                    continue
            dst_names.append(name)
            added.append(name)
            # Keep each stock's own source note if any; else say which bot the copy came from.
            source_update[name] = src_sources.get(name) or source_label
        if added:
            dst_row.trade_symbols = ",".join(dst_names)
            dst_row.trade_sources = apply_trade_sources(dst_row, source_update, dst_names)
            db.commit()
            db.refresh(dst_row)
            dst_eng.hold_for_next_cross(added)
            payload = _config_dict(dst_row)
            db.expunge(dst_row)
        else:
            payload = _config_dict(dst_row)
            db.expunge(dst_row)
    if added:
        _reload(dst_eng, payload)
    return {"added": added, "skipped": skipped, "config": payload}


class SettingsCopy(BaseModel):
    """Copy one bot's strategy settings onto another bot (or the research desk)."""

    from_desk: Literal["1", "2", "3", "4", "5", "research"]
    to_desk: Literal["1", "2", "3", "4", "5", "research"]
    # Also replace the destination's own per-stock settings with the source's.
    stock_settings: bool = False
    # The destination trades real money: the page asked first.
    confirm_live: bool = False


# Never copied: the row's identity, its Trade list, its PAPER/LIVE switch, the
# chart focus, bookkeeping, and the desk-wide switches kept on bot 1's row.
COPY_SKIP = frozenset({
    "id", "symbol", "trade_symbols", "trade_sources", "trading_mode", "bot_name", "stock_settings",
    "second_ticks", "trade_count_reset_id", "trade_count_reset_date", "max_trades_bumped",
    "all_bots_target", "all_bots_stop", "all_bots_squareoff",
    "pnl_alert_profit", "pnl_alert_loss", "pnl_alert_telegram", "pnl_alert_call", "pnl_alert_basis",
    "news_entry_bot", "news_entry_impact", "news_entry_scope", "news_entry_max_age_min", "news_entry_max_day",
    "auto_start",
})


def _desk_engine(desk: str) -> StrategyEngine:
    if desk == "research":
        ensure_research_config()
        return _research()
    bot = int(desk)
    if bot in EXTRA_BOTS:
        ensure_bot_config(bot)
    return _bot_engine_for(bot)


@app.post("/api/bots/copy-settings")
async def copy_settings(body: SettingsCopy):
    """Replace one desk's strategy settings with another's. The Trade list, mode and name stay."""
    if body.from_desk == body.to_desk:
        raise HTTPException(400, "Pick a different bot to copy to")
    src_eng, dst_eng = _desk_engine(body.from_desk), _desk_engine(body.to_desk)
    with session_factory()() as db:
        src = db.get(BotConfig, src_eng.config_id)
        row = db.get(BotConfig, dst_eng.config_id)
        if src is None or row is None:
            raise HTTPException(500, "BotConfig missing")
        if (row.trading_mode or "PAPER").upper() == "LIVE" and not body.confirm_live:
            raise HTTPException(409, "That bot is LIVE: confirm before changing its settings")
        copied = [col.name for col in BotConfig.__table__.columns if col.name not in COPY_SKIP]
        own = stock_settings(src) if body.stock_settings else stock_settings(row)
        held = {symbol: pos.qty for symbol, pos in dst_eng.positions.items() if pos is not None}
        if any(qty != int(src.qty) for symbol, qty in held.items() if "qty" not in own.get(symbol, {})):
            raise HTTPException(409, "Close the open position before changing quantity")
        changed = [name for name in copied if getattr(row, name) != getattr(src, name)]
        for name in copied:
            setattr(row, name, getattr(src, name))
        if body.stock_settings:
            row.stock_settings = src.stock_settings or "{}"
        _check_settings(row)
        for symbol in own:
            _check_settings(_cfg_for(row, symbol), symbol)
        db.commit()
        db.refresh(row)
        payload = _config_dict(row)
        cap = int(row.max_trades_per_day)
        db.expunge(row)
    _reload(dst_eng, payload)
    if "max_trades_per_day" in changed:
        dst_eng.release_trade_cap(cap)
    return {"changed": changed, "config": payload}


# ---- Export / import settings (Settings → "Export / import settings") --------------------------------
# A JSON file of the desks' strategy settings, and optionally each stock's own settings, the Trade lists and
# the fast-mover card. The PAPER/LIVE switch, names and the desk-wide rows (COPY_SKIP) are never written by an
# import; values go through ConfigUpdate (bounds, choices) and the same checks as Save.
SETTINGS_FILE_KIND = "paltra-settings"
SETTINGS_FILE_VERSION = 1
SETTINGS_DESKS = ("1", "2", "3", "4", "5", "research")


def _desk_label(desk: str) -> str:
    return "Research" if desk == "research" else f"Bot {desk}"


def _export_desk(desk: str) -> dict:
    eng = _desk_engine(desk)
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        payload = _config_dict(row)
        return {
            "name": getattr(row, "bot_name", None) or _desk_label(desk),
            "mode": (row.trading_mode or "PAPER").upper(),
            "settings": _strategy_fields(payload),
            "stock_settings": stock_settings(row),
            "trade_symbols": trade_names(row),
        }


@app.get("/api/settings/export")
async def export_settings(desks: str = "1,2,3,4,5,research", fast: bool = True):
    """Every chosen desk's strategy settings, each stock's own settings and Trade list, and the fast-mover card,
    as one JSON file. Read-only."""
    picked = [d.strip() for d in desks.split(",") if d.strip() in SETTINGS_DESKS]
    if not picked:
        raise HTTPException(400, "Pick at least one bot to export")
    out = {
        "kind": SETTINGS_FILE_KIND,
        "version": SETTINGS_FILE_VERSION,
        "exported_at": dt.datetime.now(IST).isoformat(timespec="seconds"),
        "server": server_role.role(),
        "app_version": server_role.app_version(),
        "desks": {d: await asyncio.to_thread(_export_desk, d) for d in dict.fromkeys(picked)},
    }
    if fast:
        from dataclasses import asdict

        out["fast_bot"] = asdict(fast_bot.load())
    return out


class SettingsImport(BaseModel):
    file: dict
    # Which desks of the file to write ("1"-"5", "research"); each onto the same desk here.
    desks: list[str] = Field(default_factory=list)
    stock_settings: bool = True
    # Add the file's stocks to each Trade list (never removes one); PAPER bots only.
    trade_lists: bool = False
    fast_bot: bool = False
    # A LIVE bot trades real money: the page asked first.
    confirm_live: bool = False


def _import_desk(desk: str, data: dict, body: SettingsImport, dry: bool = False) -> dict:
    eng = _desk_engine(desk)
    raw = data.get("settings") if isinstance(data.get("settings"), dict) else {}
    allowed = set(ConfigUpdate.model_fields) - COPY_SKIP
    fields = {k: v for k, v in raw.items() if k in allowed}
    try:
        checked = ConfigUpdate(**fields).model_dump(exclude_unset=True)
    except ValidationError as exc:
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(x) for x in first.get("loc", ()))
        raise HTTPException(400, f"{_desk_label(desk)}: {where} {first.get('msg', 'is not valid')}".strip()) from exc
    # The same clean-ups as Save (_put_config).
    if "square_off_time" in checked:
        _validate_hhmm(checked["square_off_time"])
    if "entry_cutoff_time" in checked:
        _validate_hhmm(checked["entry_cutoff_time"], "entry_cutoff_time")
    if "review_check_minutes" in checked:
        checked["review_check_minutes"] = _as_check_minutes_csv(checked["review_check_minutes"])
    if "small_exit_minutes" in checked:
        checked["small_exit_minutes"] = small_candles.clean_sizes(checked["small_exit_minutes"])
    notes: list[str] = []
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        live = (row.trading_mode or "PAPER").upper() == "LIVE"
        if live and not body.confirm_live:
            raise HTTPException(409, f"{_desk_label(desk)} is LIVE: confirm before changing its settings")
        own_in = data.get("stock_settings") if isinstance(data.get("stock_settings"), dict) else {}
        own = {str(k).upper(): v for k, v in own_in.items() if isinstance(v, dict)} if body.stock_settings else stock_settings(row)
        qty = int(checked.get("qty", row.qty))
        held = {symbol: pos.qty for symbol, pos in eng.positions.items() if pos is not None}
        if any(int(q) != qty for symbol, q in held.items() if "qty" not in own.get(symbol, {})):
            raise HTTPException(409, f"{_desk_label(desk)}: close the open position before changing quantity")
        changed = sorted(name for name, value in checked.items() if getattr(row, name) != value)
        for name, value in checked.items():
            setattr(row, name, value)
        if body.stock_settings:
            for symbol, values in own.items():
                if not is_nse_symbol(symbol):
                    raise HTTPException(400, f"{_desk_label(desk)}: {symbol} is not an NSE trading symbol")
                bad = sorted(k for k in values if k not in STOCK_FIELDS)
                if bad:
                    raise HTTPException(400, f"{_desk_label(desk)}: {symbol} has unknown settings {', '.join(bad[:5])}")
                probe = dict(values)
                if isinstance(probe.get("review_check_minutes"), str):
                    probe["review_check_minutes"] = [int(x) for x in probe["review_check_minutes"].split(",") if x.strip()]
                try:
                    ConfigUpdate(**probe)
                    if "review_check_minutes" in values:
                        values["review_check_minutes"] = _as_check_minutes_csv(probe["review_check_minutes"])
                    if "small_exit_minutes" in values:
                        values["small_exit_minutes"] = small_candles.clean_sizes(values["small_exit_minutes"])
                except (ValidationError, ValueError, TypeError) as exc:
                    if not isinstance(exc, ValidationError):
                        raise HTTPException(400, f"{_desk_label(desk)}: {symbol} has a setting that is not valid") from exc
                    first = exc.errors()[0] if exc.errors() else {}
                    raise HTTPException(
                        400, f"{_desk_label(desk)}: {symbol} {'.'.join(str(x) for x in first.get('loc', ()))} {first.get('msg', '')}"
                    ) from exc
            if json.dumps(own, sort_keys=True) != json.dumps(stock_settings(row), sort_keys=True):
                changed.append("stock_settings")
            row.stock_settings = json.dumps(own)
        _check_settings(row)
        for symbol in stock_settings(row):
            _check_settings(_cfg_for(row, symbol), symbol)
        added: list[str] = []
        if body.trade_lists:
            if live:
                notes.append("Trade list not changed: the bot is LIVE (arm its stocks on the terminal)")
            else:
                names = trade_names(row)
                cap = _symbol_cap(eng)
                for symbol in data.get("trade_symbols") or []:
                    name = str(symbol or "").strip().upper()
                    if not is_nse_symbol(name) or name in names:
                        continue
                    if len(names) >= cap:
                        notes.append(f"Trade list full ({cap}): the rest were not added")
                        break
                    names.append(name)
                    added.append(name)
                if added:
                    row.trade_symbols = ",".join(names)
                    row.trade_sources = apply_trade_sources(row, {n: "Imported" for n in added}, names)
                    if not dry:
                        eng.hold_for_next_cross(added)
        if dry:
            db.rollback()
            return {"desk": desk, "changed": changed, "added": added, "notes": notes}
        db.commit()
        db.refresh(row)
        payload = _config_dict(row)
        cap_now = int(row.max_trades_per_day)
        db.expunge(row)
    _reload(eng, payload)
    if "max_trades_per_day" in changed:
        eng.release_trade_cap(cap_now)
    try:
        strategy_presets.remember(_preset_desk(eng), _strategy_fields(payload))
    except Exception:  # noqa: BLE001 - bookkeeping only
        logging.getLogger("sma.presets").exception("strategy history not written")
    return {"desk": desk, "changed": changed, "added": added, "notes": notes}


@app.post("/api/settings/import")
async def import_settings(body: SettingsImport):
    """Write a settings file from Export onto the chosen desks (and the fast-mover card). Each desk is checked
    first, so a bad value or an unconfirmed LIVE bot changes nothing. No order is placed."""
    if body.file.get("kind") != SETTINGS_FILE_KIND:
        raise HTTPException(400, "That is not a PalTra settings file (export one from Settings first)")
    desks_in = body.file.get("desks") if isinstance(body.file.get("desks"), dict) else {}
    picked = [d for d in dict.fromkeys(body.desks) if d in SETTINGS_DESKS and isinstance(desks_in.get(d), dict)]
    fast_in = body.file.get("fast_bot") if body.fast_bot and isinstance(body.file.get("fast_bot"), dict) else None
    if not picked and fast_in is None:
        raise HTTPException(400, "Nothing to import: tick a bot that is in the file")
    for desk in picked:  # LIVE first, before anything is written
        eng = _desk_engine(desk)
        cfg = eng.load_config()
        if (cfg.trading_mode or "PAPER").upper() == "LIVE" and not body.confirm_live:
            raise HTTPException(409, f"{_desk_label(desk)} is LIVE: confirm before changing its settings")
    if fast_in is not None and fast_in.get("on"):
        ensure_bot_config(fast_bot.TARGET_BOT)
        target = bot_engines[fast_bot.TARGET_BOT].load_config()
        if (target.trading_mode or "PAPER").upper() == "LIVE" and not body.confirm_live:
            raise HTTPException(
                409, f"Bot {fast_bot.TARGET_BOT} is LIVE: fast movers would place real Groww orders at its size. Confirm first."
            )
    fast_checked = None
    if fast_in is not None:
        allowed_fast = set(FastBotSettings.model_fields) - {"confirm_live"}
        try:
            fast_checked = FastBotSettings(**{k: v for k, v in fast_in.items() if k in allowed_fast})
        except ValidationError as exc:
            raise HTTPException(400, f"Fast movers: {exc.errors()[0].get('msg', 'not valid')}") from exc
    for desk in picked:  # every desk checked in full before any is written
        await asyncio.to_thread(_import_desk, desk, desks_in[desk], body, True)
    results = [await asyncio.to_thread(_import_desk, desk, desks_in[desk], body) for desk in picked]
    fast_done = None
    if fast_checked is not None:
        fast_bot.save(fast_checked.model_dump(exclude={"confirm_live"}, exclude_none=True))
        fast_done = True
    return {"desks": results, "fast_bot": fast_done}


async def _put_config(eng: StrategyEngine, body: ConfigUpdate):
    # The chart symbol can change while another stock stays open. Quantity is
    # shared, so an open book still blocks a size change. Raising the trade
    # cap must not be blocked by that check.
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        own = stock_settings(row)
        if (
            body.qty is not None
            and int(body.qty) != int(row.qty)
            and any(
                pos.qty != body.qty
                for symbol, pos in eng.positions.items()
                if "qty" not in own.get(symbol, {})
            )
        ):
            raise HTTPException(409, "Close the open position before changing quantity")
        data = body.model_dump(exclude_none=True)
        # sma_medium is the only nullable column that can be explicitly cleared to NULL;
        # exclude_none above would silently drop it, so re-insert when the client sent null.
        if "sma_medium" in body.model_fields_set and body.sma_medium is None:
            data["sma_medium"] = None
        if "symbol" in data:
            data["symbol"] = data["symbol"].upper().strip()
            if not is_nse_symbol(data["symbol"]):
                raise HTTPException(400, "Symbol must be an NSE trading symbol")
        if "square_off_time" in data:
            _validate_hhmm(data["square_off_time"])
        if "entry_cutoff_time" in data:
            _validate_hhmm(data["entry_cutoff_time"], "entry_cutoff_time")
        if "review_check_minutes" in data:
            data["review_check_minutes"] = _as_check_minutes_csv(data["review_check_minutes"])
        if "small_exit_minutes" in data:
            data["small_exit_minutes"] = small_candles.clean_sizes(data["small_exit_minutes"])
        if "sma_fast" in data and "sma_slow" in data and data["sma_fast"] >= data["sma_slow"]:
            raise HTTPException(400, "Fast SMA period must be shorter than the slow period")
        for key, value in data.items():
            setattr(row, key, value)
        if row.sma_fast >= row.sma_slow:
            raise HTTPException(400, "Fast SMA period must be shorter than the slow period")
        _check_sma_medium(row)
        if float(row.rsi_long_min) > float(row.rsi_long_max):
            raise HTTPException(400, "Buy RSI low must be at or below the buy RSI high")
        if float(row.rsi_short_min) > float(row.rsi_short_max):
            raise HTTPException(400, "Sell RSI low must be at or below the sell RSI high")
        _check_gap(row)
        for symbol in own:
            _check_settings(_cfg_for(row, symbol), symbol)
        db.commit()
        db.refresh(row)
        payload = _config_dict(row)
        cap = int(row.max_trades_per_day)
        changed_cap = "max_trades_per_day" in data
        db.expunge(row)
    _reload(eng, payload)
    if changed_cap:
        eng.release_trade_cap(cap)
        # A replay of this bot halted on the same cap resumes from the same Save.
        session = replays.get(getattr(eng, "bot_id", 0)) if eng is not research_engine else None
        if session is not None and session.engine is not None:
            session.engine.release_trade_cap(cap)
    # "What was it before?": every Save keeps the strategy fields in the desk's history (strategy_presets.py).
    try:
        strategy_presets.remember(_preset_desk(eng), _strategy_fields(payload))
    except Exception:  # noqa: BLE001 - bookkeeping only; a failed snapshot never fails the save
        logging.getLogger("sma.presets").exception("strategy history not written")
    return payload


def _preset_desk(eng: StrategyEngine) -> str:
    return "research" if eng is research_engine else str(getattr(eng, "bot_id", 1) or 1)


def _strategy_fields(settings: dict) -> dict:
    return strategy_presets.strategy_only(settings, set(ConfigUpdate.model_fields), COPY_SKIP)


class PresetBody(BaseModel):
    name: str
    desk: str = "1"
    settings: dict


@app.get("/api/config/presets")
async def get_presets(desk: str = "1"):
    """Saved strategies (every desk) and this desk's history of saves, newest first."""
    return strategy_presets.listing(desk)


@app.post("/api/config/presets")
async def post_preset(body: PresetBody):
    """Save the strategy fields sent as a named preset. Loading one is done in the form; Save then applies it."""
    settings = _strategy_fields(body.settings)
    if not settings:
        raise HTTPException(400, "Nothing to save: send the strategy settings")
    if not body.name.strip():
        raise HTTPException(400, "Give the preset a name")
    return strategy_presets.save_preset(body.name, body.desk, settings)


@app.delete("/api/config/presets/{pid}")
async def delete_preset(pid: int, desk: str = "1"):
    return strategy_presets.delete_preset(pid, desk)


class ResetBody(BaseModel):
    # The bot trades real money: the page asked first.
    confirm_live: bool = False
    # Also drop every stock's own settings, so each follows the defaults too.
    stock_settings: bool = False


def strategy_defaults() -> dict:
    """Every strategy field (the update fields minus COPY_SKIP) at its BotConfig column default."""
    out: dict = {}
    for name in ConfigUpdate.model_fields:
        if name in COPY_SKIP:
            continue
        column = BotConfig.__table__.columns.get(name)
        if column is None:
            continue
        arg = getattr(column.default, "arg", None)
        out[name] = None if column.default is None or callable(arg) else arg
    return out


async def _reset_config(eng: StrategyEngine, body: ResetBody) -> dict:
    """Put the desk's strategy settings back to the defaults, the way Copy settings replaces them.

    The Trade list, mode, name, per-stock settings (unless asked) and the all-bots rows stay.
    """
    defaults = strategy_defaults()
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        if (row.trading_mode or "PAPER").upper() == "LIVE" and not body.confirm_live:
            raise HTTPException(409, "That bot is LIVE: confirm before resetting its settings")
        own = {} if body.stock_settings else stock_settings(row)
        held = {symbol: pos.qty for symbol, pos in eng.positions.items() if pos is not None}
        if any(int(qty) != int(defaults["qty"]) for symbol, qty in held.items() if "qty" not in own.get(symbol, {})):
            raise HTTPException(409, "Close the open position before changing quantity")
        changed = sorted(name for name, value in defaults.items() if getattr(row, name) != value)
        for name, value in defaults.items():
            setattr(row, name, value)
        if body.stock_settings:
            row.stock_settings = "{}"
            changed.append("stock_settings")
        _check_settings(row)
        for symbol in own:
            _check_settings(_cfg_for(row, symbol), symbol)
        db.commit()
        db.refresh(row)
        payload = _config_dict(row)
        cap = int(row.max_trades_per_day)
        db.expunge(row)
    _reload(eng, payload)
    if "max_trades_per_day" in changed:
        eng.release_trade_cap(cap)
    try:
        strategy_presets.remember(_preset_desk(eng), _strategy_fields(payload))
    except Exception:  # noqa: BLE001 - bookkeeping only
        logging.getLogger("sma.presets").exception("strategy history not written")
    return {"changed": changed, "config": payload}


@app.get("/api/config/defaults")
async def get_config_defaults():
    """The default value of every strategy field, as "Reset to defaults" sets them."""
    return strategy_defaults()


@app.post("/api/config/reset")
async def reset_config(body: ResetBody):
    return await _reset_config(engine, body)


@app.post("/api/research/config/reset")
async def research_reset_config(body: ResetBody):
    # Like Copy settings: the research row (id 2) is created first if the desk has not been opened yet.
    return await _reset_config(_desk_engine("research"), body)


@app.put("/api/config")
async def put_config(body: ConfigUpdate):
    return await _put_config(engine, body)


@app.put("/api/research/config")
async def research_put_config(body: ConfigUpdate):
    return await _put_config(_research(), body)


def _check_sma_medium(cfg) -> None:
    med = getattr(cfg, "sma_medium", None)
    if med is None:
        return
    fast, slow = int(cfg.sma_fast), int(cfg.sma_slow)
    if not (fast < int(med) < slow):
        raise HTTPException(400, f"Medium SMA period ({med}) must be between fast ({fast}) and slow ({slow})")


def _check_settings(cfg: BotConfig, symbol: str = "") -> None:
    """The same consistency checks as Save, on one stock's settings."""
    who = f"{symbol}: " if symbol else ""
    if int(cfg.sma_fast) >= int(cfg.sma_slow):
        raise HTTPException(400, f"{who}Fast SMA period must be shorter than the slow period")
    _check_sma_medium(cfg)
    if float(cfg.rsi_long_min) > float(cfg.rsi_long_max):
        raise HTTPException(400, f"{who}Buy RSI low must be at or below the buy RSI high")
    if float(cfg.rsi_short_min) > float(cfg.rsi_short_max):
        raise HTTPException(400, f"{who}Sell RSI low must be at or below the sell RSI high")
    _check_gap(cfg, who)


_GAP_DEFAULTS = {"gap_long_min": 0.02, "gap_long_max": 0.5, "gap_short_min": -0.5, "gap_short_max": -0.02}


def _gap_value(row, key: str) -> float:
    value = getattr(row, key, None)
    return _GAP_DEFAULTS[key] if value is None else float(value)


def _gap_dict(row) -> dict:
    """The SMA gap range filter. 0 and negative numbers are real settings."""
    return {
        "use_gap_long": bool(getattr(row, "use_gap_long", False)),
        "use_gap_short": bool(getattr(row, "use_gap_short", False)),
        **{key: _gap_value(row, key) for key in _GAP_DEFAULTS},
        "use_gap_mode": bool(getattr(row, "use_gap_mode", False)),
        **{key: gap_mode.setting(row, key) for key in gap_mode.DEFAULTS},
        "gap_entry_delay_min": int(gap_mode.setting(row, "gap_entry_delay_min")),
        "gap_entry_window_min": int(gap_mode.setting(row, "gap_entry_window_min")),
        "gap_fade_min_candles": int(gap_mode.setting(row, "gap_fade_min_candles")),
        "gap_fade_confirm_sma": bool(getattr(row, "gap_fade_confirm_sma", False)),
        "gap_fade_intrabar": bool(getattr(row, "gap_fade_intrabar", False)),
        "gap_fade_minute": bool(getattr(row, "gap_fade_minute", False)),
        "flip_orders": bool(getattr(row, "flip_orders", False)),
        "cross_exit": getattr(row, "cross_exit", None) is not False,
        "late_entry_min": late_entry.window_min(row),
        "join_trend": join_trend.enabled(row),
        "always_in": always_in.enabled(row),
        "always_in_reverse": bool(getattr(row, "always_in_reverse", False)),
        "join_after_min": join_trend.after_min(row),
        "counter_exit_count": counter_candles.exit_count(row),
        "counter_entry": counter_candles.entry_on(row),
        "counter_rule": counter_candles.rule(row),
        "counter_need_fast": counter_candles.need_fast(row),
        "counter_min_body": counter_candles.min_body(row),
        "counter_reenter": bool(getattr(row, "counter_reenter", False)),
        "counter_reenter_max": counter_candles.reenter_max(row),
        "use_ema": ema_filter.entry_on(row),
        "ema_exit": ema_filter.exit_on(row),
        "ema_fast": ema_filter.lengths(row)[0],
        "ema_slow": ema_filter.lengths(row)[1],
        "use_macd": macd.entry_on(row),
        "macd_exit": macd.exit_on(row),
        "macd_fast": macd.lengths(row)[0],
        "macd_slow": macd.lengths(row)[1],
        "macd_signal": macd.lengths(row)[2],
        "use_cvd": cum_delta.entry_on(row),
        "cvd_lookback": cum_delta.lookback(row),
        "cvd_div_exit": cum_delta.exit_on(row),
        "cvd_div_lookback": cum_delta.div_lookback(row),
        "reverse_on_exit": ",".join(k for k in reverse_exit.KEYS if k in reverse_exit.keys(row)),
        "reverse_colour_exit": reverse_exit.colour_exit_on(row),
        "reverse_settings": reverse_exit.own_settings(row),
        "exit_join": "AND" if str(getattr(row, "exit_join", "") or "OR").upper() == "AND" else "OR",
        "exit_group": ",".join(exit_join_mod.group(row)),
        "exit_need": int(getattr(row, "exit_need", 0) or 0),
        "exit_within": exit_join_mod.within(row),
        "ha_colours": heikin_ashi.on(row),
        "use_supertrend": supertrend.entry_on(row),
        "use_level": bool(getattr(row, "use_level", False)),
        "use_high52": high52.on(row),
        "high52_side": high52.side_of(row),
        "high52_pct": high52.pct_of(row),
        "level_line": level_check.line_of(row),
        "st_exit": supertrend.exit_on(row),
        "st_period": supertrend.settings(row)[0],
        "st_mult": supertrend.settings(row)[1],
        "tall_pullback": tall_pb.on(row),
        "tall_atr_mult": tall_pb.mult(row),
        "tall_expiry_candles": tall_pb.expiry(row),
        "extreme_entry": extreme_mod.on(row),
        "extreme_anchor": extreme_mod.anchor(row),
        "extreme_invalid_pct": extreme_mod.invalid_pct(row),
        "extreme_expiry_candles": extreme_mod.expiry(row),
        "extreme_cancel_on_cross": extreme_mod.cancel_on_cross(row),
        "entry_candle_confirm": entry_candle_mod.on(row),
        "entry_candle_pct": entry_candle_mod.margin_pct(row),
        "entry_candle_max": entry_candle_mod.max_candles(row),
        "slope_exit": slope_mod.on(row),
        "slope_lookback": slope_mod.lookback(row),
        "slope_bend_pct": slope_mod.bend_pct(row),
        "slope_turn": slope_mod.turn_on(row),
        "slope_turn_pct": slope_mod.turn_pct(row),
        "vturn_exit": slope_mod.vturn_on(row),
        "turn_alert": bool(getattr(row, "turn_alert", False)),
        "turn_entry": bool(getattr(row, "turn_entry", False)),
        "uturn_on": turn_entry_mod.u_on(row),
        "turn_toward_cross": turn_entry_mod.toward_on(row),
        "turn_exit_candle": turn_entry_mod.exit_candle_on(row),
        "turn_colour_exit": turn_entry_mod.colour_exit_on(row),
        "uturn_run": turn_entry_mod.u_run(row),
        "uturn_min_pct": turn_entry_mod.u_min(row),
        "day_extreme_guard": day_extreme_mod.on(row),
        "day_extreme_pct": day_extreme_mod.near_pct(row),
        "day_extreme_pull_pct": day_extreme_mod.pull_pct(row),
        "day_extreme_candles": day_extreme_mod.candles(row),
        "vturn_move_pct": slope_mod.vturn_move_pct(row),
        "vturn_turn_pct": slope_mod.vturn_turn_pct(row),
        "vturn_within": slope_mod.vturn_within(row),
        "vturn_handoff": bool(getattr(row, "vturn_handoff", False)),
        "stop_handoff": stop_handoff_mod.choice(row),
        "stop_handoff_loss": stop_handoff_mod.loss_only(row),
        "handoff_now": recovery_mod.handoff_now_on(row),
        "auto_start": bool(getattr(row, "auto_start", False)),
        "pivot_target": pivots.target_mode(row),
        "stop_plan": stop_plan_mod.on(row),
        "plan_be_r": stop_plan_mod.be_r(row),
        "plan_trail_r": stop_plan_mod.trail_r(row),
        "decel_exit": decel_mod.on(row),
        **{f"decel_{key}": value for key, value in decel_mod.settings(row).items()},
        "slow_sma_exit": recovery_mod.slow_exit_on(row),
        "recovery_hold": recovery_mod.on(row),
        "recovery_tolerance_pct": recovery_mod.tolerance_pct(row),
        "recovery_emergency_mult": recovery_mod.emergency_mult(row),
        "recovery_atr_mult": recovery_mod.atr_mult(row),
        "recovery_max_candles": recovery_mod.max_candles(row),
        "recovery_handoff": recovery_mod.handoff_on(row),
        "breakout_handoff": breakout_mod.on(row),
        "breakout_atr_mult": breakout_mod.atr_mult(row),
        "breakout_gap_pct": breakout_mod.gap_pct(row),
        "breakout_volume_mult": breakout_mod.volume_mult(row),
        "breakout_rule": breakout_mod.rule(row),
        "breakout_cut": breakout_mod.cut(row),
        "breakout_trend_checks": ",".join(f"{k}:{v}" for k, v in breakout_mod.trend_rules(row).items()),
        "breakout_trend_need": breakout_mod.trend_need(row),
        "breakout_when": breakout_mod.when(row),
        "chop_losses": recovery_mod.chop_losses(row),
        "use_or_volume": bool(getattr(row, "use_or_volume", False)),
        "use_or_vwap": bool(getattr(row, "use_or_vwap", False)),
        "use_or_spread": bool(getattr(row, "use_or_spread", False)),
        "use_or_break": bool(getattr(row, "use_or_break", False)),
        "or_volume_ratio": opening_range.ratio(row),
        "or_spread_pct": opening_range.spread_pct(row),
        "candle_minutes": int(getattr(row, "candle_minutes", None) or 1),
        "exit_candle_minutes": exit_candle.minutes(row),
        "small_exit_minutes": ",".join(str(s) for s in small_candles.sizes(row)),
        "small_exit_cross": small_candles.cross_on(row),
        "small_exit_slope": small_candles.slope_on(row),
        "small_exit_counter": small_candles.counter(row),
        "small_exit_loss_pct": small_candles.loss_pct(row),
        "small_exit_agree": small_candles.agree(row),
        "slope_minutes": slope_mod.minutes(row),
        **{key: candle_patterns.setting(row, key) for key in candle_patterns.DEFAULTS},
        **{key: nth_candle.setting(row, key) for key in nth_candle.DEFAULTS},
        "review_on": review.review_on(row),
        "review_gap_pct": review.gap_band(row),
        "review_cooldown_min": int(review.cooldown(row).total_seconds() // 60),
        "review_check_minutes": review.check_minutes_list(row),
        "review_check_mode": review.check_mode(row),
        "review_default_answer": review.default_answer(row),
        "review_conditions": ",".join(review.conditions(row)),
        "review_join": review.join(row),
        "review_slope_candles": review.slope_candles(row),
        "review_slope_flat": review.slope_flat(row),
        "review_steep_pct": review.steep_pct(row),
    }


def _check_gap(cfg, who: str = "") -> None:
    if _gap_value(cfg, "gap_long_min") > _gap_value(cfg, "gap_long_max"):
        raise HTTPException(400, f"{who}Buy SMA gap min must be at or below the buy max")
    if _gap_value(cfg, "gap_short_min") > _gap_value(cfg, "gap_short_max"):
        raise HTTPException(400, f"{who}Sell SMA gap min must be at or below the sell max")
    problem = gap_mode.check(cfg)
    if problem:
        raise HTTPException(400, f"{who}{problem}")


class StockConfigUpdate(ConfigUpdate):
    """One stock's strategy settings. Account-wide fields are refused."""


async def _get_stock_config(eng: StrategyEngine, symbol: str):
    """This stock's settings as the bot will use them, and which are its own."""
    name = _stock_name(symbol)
    row = eng.load_config()
    return {**_config_dict(_cfg_for(row, name)), "symbol": name, "own": stock_settings(row).get(name, {})}


@app.get("/api/config/stock/{symbol}")
async def get_stock_config(symbol: str):
    return await _get_stock_config(engine, symbol)


@app.get("/api/research/config/stock/{symbol}")
async def research_get_stock_config(symbol: str):
    return await _get_stock_config(_research(), symbol)


async def _put_stock_config(eng: StrategyEngine, symbol: str, body: StockConfigUpdate):
    """Set this stock's own strategy settings.

    A value equal to the shared setting is not stored, so the stock keeps
    following the shared value when that changes later.
    """
    name = _stock_name(symbol)
    data = body.model_dump(exclude_none=True)
    # sma_medium can be explicitly cleared to NULL; re-insert when the client sent null.
    if "sma_medium" in body.model_fields_set and body.sma_medium is None:
        data["sma_medium"] = None
    data.pop("symbol", None)
    shared_only = sorted(k for k in data if k not in STOCK_FIELDS)
    if shared_only:
        raise HTTPException(
            400, f"{', '.join(shared_only)} apply to every stock. Change them with All stocks selected."
        )
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        everything = stock_settings(row)
        own = dict(everything.get(name, {}))
        if "review_check_minutes" in data:
            data["review_check_minutes"] = _as_check_minutes_csv(data["review_check_minutes"])
        if "small_exit_minutes" in data:
            data["small_exit_minutes"] = small_candles.clean_sizes(data["small_exit_minutes"])
        for key, value in data.items():
            if value == getattr(row, key):
                own.pop(key, None)
            else:
                own[key] = value
        pos = eng.positions.get(name)
        new_qty = int(own.get("qty", row.qty))
        if pos is not None and int(pos.qty) != new_qty:
            raise HTTPException(409, f"Close the open {name} position before changing its quantity")
        if own:
            everything[name] = own
        else:
            everything.pop(name, None)
        row.stock_settings = json.dumps(everything)
        _check_settings(_cfg_for(row, name), name)
        db.commit()
        db.refresh(row)
        payload = {**_config_dict(_cfg_for(row, name)), "symbol": name, "own": own}
        db.expunge(row)
    _reload(eng, payload)
    return payload


@app.put("/api/config/stock/{symbol}")
async def put_stock_config(symbol: str, body: StockConfigUpdate):
    return await _put_stock_config(engine, symbol, body)


@app.put("/api/research/config/stock/{symbol}")
async def research_put_stock_config(symbol: str, body: StockConfigUpdate):
    return await _put_stock_config(_research(), symbol, body)


async def _reset_stock_config(eng: StrategyEngine, symbol: str):
    """Drop this stock's own settings. It follows the shared ones again."""
    name = _stock_name(symbol)
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        everything = stock_settings(row)
        own = everything.pop(name, {})
        pos = eng.positions.get(name)
        if pos is not None and "qty" in own and int(pos.qty) != int(row.qty):
            raise HTTPException(409, f"Close the open {name} position before changing its quantity")
        row.stock_settings = json.dumps(everything)
        db.commit()
        db.refresh(row)
        payload = {**_config_dict(_cfg_for(row, name)), "symbol": name, "own": {}}
        db.expunge(row)
    _reload(eng, payload)
    return payload


@app.delete("/api/config/stock/{symbol}")
async def reset_stock_config(symbol: str):
    return await _reset_stock_config(engine, symbol)


@app.delete("/api/research/config/stock/{symbol}")
async def research_reset_stock_config(symbol: str):
    return await _reset_stock_config(_research(), symbol)


def _stock_name(symbol: str) -> str:
    name = (symbol or "").upper().strip()
    if not is_nse_symbol(name):
        raise HTTPException(400, "Symbol must be an NSE trading symbol")
    return name


def _as_check_minutes_csv(value) -> str:
    """Serialize `review_check_minutes` into the comma-separated string stored on the row."""
    if isinstance(value, (list, tuple, set)):
        parts = [int(v) for v in value]
    else:
        parts = [int(value)]
    allowed = {1, 2, 3, 5, 10, 15}
    kept: list[int] = []
    for v in parts:
        if v in allowed and v not in kept:
            kept.append(v)
    if not kept:
        raise HTTPException(400, "review_check_minutes must be at least one of 1, 2, 3, 5, 10, 15")
    return ",".join(str(v) for v in kept)


def _validate_hhmm(value: str, field: str = "square_off_time") -> None:
    try:
        parts = (value or "").split(":")
        if len(parts) != 2:
            raise ValueError
        hh, mm = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be HH:MM") from None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise HTTPException(400, f"{field} must be HH:MM")


def _live_token() -> str:
    """Desk login when Settings has one, otherwise the Fly secret."""
    return preferred_quote_token()


@app.post("/api/mode")
async def set_mode(body: ModeUpdate):
    """PAPER is the default. LIVE requires confirm_live and a Groww token."""
    return await _set_mode(engine, body)


async def _set_mode(eng: StrategyEngine, body: ModeUpdate):
    """One bot's PAPER/LIVE switch. LIVE needs confirm_live, a Groww session,
    and none of its stocks traded LIVE by another bot."""
    from strategy_engine import _ist_now

    eng._roll_session(_ist_now())
    mode = body.mode.upper()
    if mode not in ("PAPER", "LIVE"):
        raise HTTPException(400, "mode must be PAPER or LIVE")
    if mode == "LIVE":
        if server_role.no_orders():
            raise HTTPException(403, server_role.no_orders_text())
        if not body.confirm_live:
            raise HTTPException(
                400,
                "LIVE REAL MONEY requires explicit confirmation (confirm_live=true).",
            )
        token = _live_token()
        if not token:
            raise HTTPException(
                503,
                "No Groww session is saved. Connect Groww on the desk, then confirm LIVE again.",
            )
        # Overnight practice can halt itself. That must not block this confirm.
        eng.release_paper_halt()
    if eng.status == "HALTED" and mode == "LIVE":
        # Back to PAPER is the safe way and is always allowed; a halted bot never goes LIVE the same day.
        raise HTTPException(423, "Bot is halted for the day — restart tomorrow or reset after review")
    if eng.status == "HALTED" and (eng.load_config().trading_mode or "PAPER").upper() == "LIVE":
        # A LIVE halt going back to PAPER stays a LIVE halt for the day (no LIVE again before tomorrow).
        eng.live_halt_day = _ist_now().date()
    if _open_symbols(eng):
        raise HTTPException(409, "Close the open position before switching execution mode")
    with session_factory()() as db:
        row = db.get(BotConfig, eng.config_id)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        if mode == "LIVE":
            why = bots_mod.live_conflict(eng.bot_id, trade_names(row))
            if why:
                raise HTTPException(409, why)
        row.trading_mode = mode
        db.commit()
    eng.broker.set_mode(mode, _live_token())
    return {"trading_mode": mode}


async def _start_bot(eng: StrategyEngine):
    from strategy_engine import _ist_now

    if server_role.is_backtest():
        await _start_backtest_bots()

    eng._roll_session(_ist_now())
    eng.release_manual_panic()
    eng.release_trade_cap()
    if all_bots_guard.is_guard_halt(eng) and eng in _all_bots():
        # Halted by the all-bots target / stop loss: only once the limits no longer hit.
        closed = await asyncio.to_thread(all_bots_guard.closed_total, _all_bots())
        cfg = engine.load_config()
        target, stop = all_bots_guard.limits(cfg)
        amount = closed + all_bots_guard.open_total(_all_bots())
        if all_bots_guard.verdict(amount, target, stop, engine._now(), all_bots_guard.squareoff_at(cfg)) is None:
            eng.status, eng.halt_reason = "STOPPED", ""
    if eng.status == "HALTED":
        raise HTTPException(423, eng.halt_reason or "Halted for the day")
    if eng.status == "DAY_COMPLETED":
        raise HTTPException(423, eng.halt_reason or "Session already squared off")
    # A cross already on the tape is not an order. The next cross is.
    eng.hold_for_next_cross(trade_names(eng.load_config()))
    eng.status = "RUNNING"
    eng.halt_reason = ""
    return {"bot_status": eng.status}


@app.post("/api/bot/start")
async def start_bot():
    return await _start_bot(engine)


# ---- Backtest server (backtest_server.py): status, sync, idle time, stop ----


DESK_REPLAY_BLOCK = (dt.time(9, 0), dt.time(15, 45))


def desk_market_hours_free() -> bool:
    """True when the main desk may run backtests and accuracy checks in market hours too (the default, the
    owner's choice: he decides when). `DESK_REPLAY_HOURS=after` brings back the old 09:00-15:45 refusal."""
    return os.environ.get("DESK_REPLAY_HOURS", "any").lower() != "after"


def _replay_today_ok() -> bool:
    """Today may be replayed during the session (up to the last closed minute): always on the backtest server,
    and on the desk unless DESK_REPLAY_HOURS=after."""
    return server_role.is_backtest() or desk_market_hours_free()


def _refuse_desk_replay_in_market(now: dt.datetime | None = None) -> None:
    """With `DESK_REPLAY_HOURS=after`, backtests start on the main desk only outside market hours (09:00-15:45
    IST, Mon-Fri), so a heavy replay never slows the live bots. By default the desk replays at any time."""
    if not server_role.is_desk() or desk_market_hours_free():
        return
    now = now or dt.datetime.now(IST)
    if now.weekday() < 5 and DESK_REPLAY_BLOCK[0] <= now.time() < DESK_REPLAY_BLOCK[1]:
        raise HTTPException(
            409,
            "Backtests run on the main desk only after 15:45 IST (and on weekends), so they never slow the live bots. "
            "Use the backtest server during market hours.",
        )


class MarketHoursUpdate(BaseModel):
    on: bool
    stop_at: str | None = Field(None, pattern=r"^\d{2}:\d{2}$")


def _backtest_only() -> None:
    if not server_role.is_backtest():
        raise HTTPException(404, "Not the backtest server")


@app.get("/api/backtest/status")
async def backtest_status():
    _backtest_only()
    return backtest_server.status(replays)


@app.post("/api/backtest/sync")
async def backtest_sync():
    """Copy every bot's settings from the desk again (PAPER forced). Refused while a backtest plays."""
    _backtest_only()
    if backtest_server.busy(replays):
        raise HTTPException(409, "A backtest is running: sync once it has finished, so it keeps the settings it started with.")
    try:
        out = await backtest_server.sync(import_bot_settings)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not copy the settings from the desk: {exc}") from exc
    # A full copy: stocks armed here from the scanner are replaced like everything else.
    backtest_server.clear_scanner_armed()
    await backtest_server.refresh_token()
    return {**out, **backtest_server.status(replays)}


class IdleUpdate(BaseModel):
    minutes: int = Field(ge=backtest_server.IDLE_MIN, le=backtest_server.IDLE_MAX)


@app.put("/api/backtest/idle")
async def backtest_idle(body: IdleUpdate):
    _backtest_only()
    await asyncio.to_thread(backtest_server.set_idle_minutes, body.minutes)
    return backtest_server.status(replays)


class HandoffReturnUpdate(BaseModel):
    on: bool
    at: str | None = None


@app.get("/api/bots/handoff-return")
async def handoff_return_get():
    """Send handed-off stocks back to the bot they came from at the end of the day (handoff_chain.py)."""
    return await asyncio.to_thread(handoff_chain.state)


@app.put("/api/bots/handoff-return")
async def handoff_return_put(body: HandoffReturnUpdate):
    """Turn the end-of-day send-back on or off (arming only: no order is placed)."""
    try:
        await asyncio.to_thread(handoff_chain.save, body.on, body.at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return await asyncio.to_thread(handoff_chain.state)


@app.post("/api/bots/handoff-return/now")
async def handoff_return_now():
    """Send every handed-off stock that is flat back now, whatever the time (arming only)."""
    lines = await asyncio.to_thread(handoff_chain.send_back, bots_mod.engines())
    return {**(await asyncio.to_thread(handoff_chain.state)), "lines": lines}


class AutoSyncUpdate(BaseModel):
    on: bool


@app.put("/api/backtest/auto-sync")
async def backtest_auto_sync(body: AutoSyncUpdate):
    """Copy the desk's settings at every start of this server (off: only "Sync from desk" copies them)."""
    _backtest_only()
    await asyncio.to_thread(backtest_server.set_auto_sync, body.on)
    return backtest_server.status(replays)


@app.put("/api/backtest/hours")
async def backtest_hours(body: MarketHoursUpdate):
    """Market hours only: after the stop time (and on weekends) the server stops once nothing is running."""
    _backtest_only()
    try:
        await asyncio.to_thread(backtest_server.set_market_hours, body.on, body.stop_at)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return backtest_server.status(replays)


@app.post("/api/backtest/stop")
async def backtest_stop(force: bool = False):
    """Stop the backtest server now (Fly stops the machine; opening the page starts it again)."""
    _backtest_only()
    if backtest_server.busy(replays) and not force:
        raise HTTPException(409, "A backtest is running. Stop it first, or stop the server anyway (it can be resumed later).")
    asyncio.get_running_loop().call_later(1.0, backtest_server.stop_now, "stopped from the page")
    return {"stopping": True}


@app.post("/api/research/bot/start")
async def research_start_bot():
    return await _start_bot(_research())


class ForceOrder(BaseModel):
    symbol: str = ""


async def _force_order(eng: StrategyEngine, body: ForceOrder):
    """Manual check order. Starts the bot. Does not wait for a cross."""
    try:
        result = await eng.force_order(body.symbol)
    except ForceRefused as exc:
        code = 423 if eng.status in ("HALTED", "DAY_COMPLETED") else 400
        raise HTTPException(code, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/bot/force")
async def force_order(body: ForceOrder):
    return await _force_order(engine, body)


@app.post("/api/research/bot/force")
async def research_force_order(body: ForceOrder):
    return await _force_order(_research(), body)


async def _pause_bot(eng: StrategyEngine):
    if eng.status == "RUNNING":
        eng.status = "PAUSED"
    return {"bot_status": eng.status}


@app.post("/api/bot/pause")
async def pause_bot():
    return await _pause_bot(engine)


@app.post("/api/research/bot/pause")
async def research_pause_bot():
    return await _pause_bot(_research())


class CloseOrder(BaseModel):
    symbol: str = ""


async def _close_position(eng: StrategyEngine, body: CloseOrder):
    """Close one open stock. Does not halt the bot."""
    try:
        result = await eng.close_symbol(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/bot/close")
async def close_position(body: CloseOrder):
    return await _close_position(engine, body)


class ReverseOrder(BaseModel):
    symbol: str = ""
    confirm_live: bool = False


async def _reverse_position(eng: StrategyEngine, body: ReverseOrder):
    """Close one open stock and open it the other way at once (reverse_exit.py). A LIVE bot needs confirm_live."""
    try:
        live = (eng.load_config().trading_mode or "PAPER").upper() == "LIVE"
    except Exception:  # noqa: BLE001
        live = False
    if live and not body.confirm_live:
        raise HTTPException(409, "This bot is LIVE: a reverse sends two real Groww orders. Confirm to go ahead.")
    try:
        result = await eng.reverse_position(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/bot/reverse")
async def reverse_position(body: ReverseOrder):
    return await _reverse_position(engine, body)


@app.post("/api/research/bot/reverse")
async def research_reverse_position(body: ReverseOrder):
    return await _reverse_position(_research(), body)


@app.post("/api/bots/{bot}/bot/reverse")
async def bot_reverse(bot: int, body: ReverseOrder):
    return await _reverse_position(_bot(bot), body)


@app.post("/api/replay/bot/reverse")
async def replay_bot_reverse(body: ReverseOrder, bot: int = 1):
    return await _reverse_position(_replay_engine(bot), body)


async def _answer_review(eng: StrategyEngine, review_id: int, action: str):
    """EXIT or WAIT on a 1-minute review (review.py). Only EXIT sends an order."""
    if action not in ("exit", "wait"):
        raise HTTPException(404, "Answer exit or wait")
    try:
        return await eng.answer_review(review_id, action.upper())
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/bot/review/{review_id}/{action}")
async def answer_review(review_id: int, action: str):
    return await _answer_review(engine, review_id, action)


@app.post("/api/research/bot/review/{review_id}/{action}")
async def research_answer_review(review_id: int, action: str):
    return await _answer_review(_research(), review_id, action)


@app.post("/api/research/bot/close")
async def research_close_position(body: CloseOrder):
    return await _close_position(_research(), body)


async def _reset_trades(eng: StrategyEngine):
    """Set today's trade count back to 0 (the daily cap counts again from here)."""
    before = eng.reset_trade_count()
    return {"bot_status": eng.status, "trades_today": eng.trades_today, "was": before, "last_signal": eng.last_signal}


@app.post("/api/bot/reset-trades")
async def reset_trades():
    return await _reset_trades(engine)


@app.post("/api/research/bot/reset-trades")
async def research_reset_trades():
    return await _reset_trades(_research())


async def _kill_bot(eng: StrategyEngine):
    """Panic: cancel SL, flatten MIS, lock the strategy. Each desk only its own."""
    await eng.kill("Manual PANIC SQUARE-OFF (research)" if isinstance(eng, ResearchEngine) else "Manual PANIC SQUARE-OFF")
    return {"bot_status": eng.status, "halt_reason": eng.halt_reason}


@app.post("/api/bot/kill")
async def kill_bot():
    return await _kill_bot(engine)


@app.post("/api/research/bot/kill")
async def research_kill_bot():
    return await _kill_bot(_research())


class ReplayStart(BaseModel):
    date: str
    #: Last day of a multi-day run. Empty or equal to `date` replays one day.
    end_date: str | None = None
    start: str = "09:15"
    speed: int = 60
    #: Stocks to replay. Empty replays the armed stocks, as before. Lets the
    #: Scalp page test a shortlist without arming it.
    symbols: list[str] | None = None
    #: The SMA bot whose settings and armed stocks to replay: 1 (main desk, the
    #: default) or 2-4. Practice money whichever bot; never sends an order.
    bot: int = Field(default=1, ge=1, le=5)
    #: Replay as armed (arm_log.py): the stocks this bot had armed on those days, each traded
    #: only while it was armed live, from 09:15. `symbols` is ignored then.
    as_armed: bool = False
    #: Play the hand-offs (handoff_chain.py): a stock handed to Bot 2 / Bot 3 stays in the run and trades with
    #: that bot's settings, and the live Trade lists are not touched. Off = dropped from the run (and moved live).
    play_handoffs: bool = True


class ScalpPickStart(BaseModel):
    date: str
    end_date: str | None = None
    #: The stocks to choose from each day (the Scalp page's streaming list).
    universe: list[str]
    pick_time: str = "09:45"
    top_n: int = Field(default=3, ge=1, le=30)
    min_atr_pct: float = Field(default=0.08, ge=0, le=5)
    min_value_cr: float = Field(default=5.0, ge=0, le=100000)
    require_bias: bool = True
    speed: int = 300


class ReplayControl(BaseModel):
    action: Literal["play", "pause", "stop", "speed", "seek"]
    speed: int | None = None
    # seek: the replayed day's time to move to, IST HH:MM (forward = fast-forward, back = the day replays to there).
    at: str | None = Field(default=None, max_length=5)


def _replay_engine(bot: int = 1):
    eng = _replay_for(bot).engine
    if eng is None:
        raise HTTPException(409, "No replay is playing. Start one first.")
    return eng


@app.get("/api/replay")
async def replay_info(bot: int = 1):
    """One bot's replay (`bot` 1-5, default 1). Each bot has its own and they can run together. Bot 5 with
    none of its own follows a Bot 1-4 replay that plays the fast-mover hand-off (`follows`: that bot)."""
    session, following = _replay_view(bot)
    info = session.info()
    if following:
        info = {**info, "bot": fast_bot.TARGET_BOT, "follows": session.bot, "bot_name": f"Bot {fast_bot.TARGET_BOT}"}
    return info


@app.get("/api/replay/all")
async def replay_all():
    """Every bot's replay, for screens that show what the other bots are replaying."""
    return {str(n): s.info() for n, s in replays.items()}


@app.post("/api/replay/scalp-picks")
async def replay_scalp_picks(body: ScalpPickStart):
    """Backtest the Scalp page's picks: each past day, score the universe at
    the pick time as the live page would, then let the SMA bot trade only the
    top picks for the rest of that day. Practice money; never sends an order.
    """
    _refuse_desk_replay_in_market()
    cfg = engine.load_config()
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, "Switch to PAPER before starting a replay.")
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date, allow_today=_replay_today_ok())
        pick_time = parse_start(body.pick_time)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not (dt.time(9, 20) <= pick_time <= dt.time(14, 30)):
        raise HTTPException(400, "Pick a time between 09:20 and 14:30 IST.")
    universe: list[str] = []
    for raw in body.universe:
        name = (raw or "").strip().upper()
        if not is_nse_symbol(name):
            raise HTTPException(400, f"{raw!r} is not an NSE trading symbol.")
        if name not in universe:
            universe.append(name)
    if not universe:
        raise HTTPException(400, "Give at least one stock to pick from.")
    if len(universe) > MAX_UNIVERSE:
        raise HTTPException(400, f"Pick from at most {MAX_UNIVERSE} stocks.")
    if not engine.broker.token:
        engine.broker.adopt_saved_session()
    if not engine.broker.token:
        raise HTTPException(400, "Replay needs Groww candles. Log in to Groww on the desk Settings page first.")
    rule = PickRule(
        pick_time=pick_time,
        top_n=body.top_n,
        min_atr_pct=body.min_atr_pct,
        min_value_cr=body.min_value_cr,
        require_bias=body.require_bias,
    )
    await replay.begin(
        engine.broker,
        universe,
        day,
        pick_time,
        body.speed,
        end_day=end_day,
        settings={**settings_snapshot(cfg), "stock_settings": stock_settings(cfg)},
        rule=rule,
    )
    return replay.info()


class TrendPickStart(BaseModel):
    date: str
    end_date: str | None = None
    #: The stocks to choose from each day (the card's scan lists).
    universe: list[str]
    pick_time: str = "10:00"
    top_n: int = Field(default=10, ge=1, le=trend_picks.MAX_TOP_N)
    direction: Literal["BULLISH", "BEARISH", "BOTH"] = "BOTH"
    minutes: Literal[1, 3, 5, 10, 15] = 5
    bot: int = Field(default=1, ge=1, le=4)
    speed: int = 300
    #: Play Bot 1's hand-offs inside the run on Bot 2's / Bot 3's settings (handoff_chain.py), like the replay bar.
    play_handoffs: bool = True


async def _start_trend_picks(
    bot: int, universe: list[str], day: dt.date, end_day: dt.date, pick_time: dt.time, top_n: int, direction: str,
    minutes: int, speed: int, play_handoffs: bool = True, active: dict | None = None,
) -> dict:
    """Start a strong-trend pick backtest on `bot`'s replay player, or with `active` (rank, bias, min_value_cr) a Most
    active pick backtest (active_picks.py). Raises HTTPException on a refusal."""
    eng = engine if bot == 1 else bot_engines.get(bot)
    if eng is None:
        raise HTTPException(404, f"No bot {bot}")
    cfg = eng.load_config()
    bot_label = getattr(cfg, "bot_name", None) or f"Bot {bot}"
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, f"Switch {bot_label} to PAPER before replaying its settings.")
    names: list[str] = []
    for raw in universe:
        name = (raw or "").strip().upper()
        if is_nse_symbol(name) and name not in names:
            names.append(name)
    if not names:
        raise HTTPException(400, "Give at least one stock to pick from.")
    names = names[: trend_picks.MAX_UNIVERSE]
    if not engine.broker.token:
        engine.broker.adopt_saved_session()
    if not engine.broker.token:
        raise HTTPException(400, "Replay needs Groww candles. Log in to Groww on the Settings page first.")
    if active is not None:
        rule = active_picks.ActivePickRule(
            pick_time=pick_time, top_n=top_n, rank=active.get("rank", "rvol"), bias=active.get("bias", "BOTH"),
            min_value_cr=float(active.get("min_value_cr", 5.0)),
        )
    else:
        rule = trend_picks.TrendPickRule(
            pick_time=pick_time, top_n=top_n, direction=direction, minutes=minutes,
            sma_fast=int(cfg.sma_fast), sma_slow=int(cfg.sma_slow),
        )
    session = replays[bot]
    await session.begin(
        engine.broker,
        names,
        day,
        pick_time,
        speed,
        end_day=end_day,
        settings={
            **settings_snapshot(cfg),
            "stock_settings": stock_settings(cfg),
            "bot": bot,
            "bot_name": bot_label,
            **({"play_handoffs": True} if play_handoffs else {}),
        },
        rule=rule,
        bot=bot,
        bot_name=bot_label,
    )
    return session.info()


@app.post("/api/replay/trend-picks")
async def replay_trend_picks(body: TrendPickStart):
    """Backtest the strong-trend picks (trend_picks.py): each past day, read every stock as the Trend quality card would
    have at the pick time, keep the strong bullish / bearish ones, take the top N and let the chosen bot trade only those
    from that minute to the square-off. Practice money; never sends an order, never changes a Trade list."""
    _refuse_desk_replay_in_market()
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date, allow_today=_replay_today_ok())
        pick_time = parse_start(body.pick_time)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not (dt.time(9, 20) <= pick_time <= dt.time(14, 30)):
        raise HTTPException(400, "Pick a time between 09:20 and 14:30 IST.")
    return await _start_trend_picks(
        body.bot, body.universe, day, end_day, pick_time, body.top_n, body.direction, body.minutes, body.speed,
        play_handoffs=body.play_handoffs,
    )


class ActivePickStart(BaseModel):
    date: str
    end_date: str | None = None
    universe: list[str]
    pick_time: str = "10:00"
    top_n: int = Field(default=10, ge=1, le=active_picks.MAX_TOP_N)
    rank: Literal["value", "rvol", "change", "vwap", "speed", "range"] = "rvol"
    bias: Literal["BOTH", "LONG", "SHORT", "ANY"] = "BOTH"
    min_value_cr: float = Field(default=5.0, ge=0, le=10000)
    bot: int = Field(default=1, ge=1, le=4)
    speed: int = 300
    play_handoffs: bool = True


@app.post("/api/replay/active-picks")
async def replay_active_picks(body: ActivePickStart):
    """Backtest the Most active picks (active_picks.py): each past day, read every stock as the Most active card would
    have at the pick time from its 1-minute candles, rank them, take the top N and let the chosen bot trade only those
    from that minute to the square-off. Practice money; never sends an order, never changes a Trade list."""
    _refuse_desk_replay_in_market()
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date, allow_today=_replay_today_ok())
        pick_time = parse_start(body.pick_time)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not (dt.time(9, 20) <= pick_time <= dt.time(14, 30)):
        raise HTTPException(400, "Pick a time between 09:20 and 14:30 IST.")
    if trend_compare.comparer().running or stock_rank.jobs().running:
        raise HTTPException(409, "A comparison or stock ranking is using the replay players. Stop it first or wait for it.")
    return await _start_trend_picks(
        body.bot, body.universe, day, end_day, pick_time, body.top_n, "BOTH", 5, body.speed,
        play_handoffs=body.play_handoffs,
        active={"rank": body.rank, "bias": body.bias, "min_value_cr": body.min_value_cr},
    )


class TrendPicksAuto(BaseModel):
    on: bool | None = None
    run_at: str | None = None
    pick_time: str | None = None
    direction: Literal["BULLISH", "BEARISH", "BOTH"] | None = None
    top_n: int | None = Field(default=None, ge=1, le=trend_picks.MAX_TOP_N)
    minutes: Literal[1, 3, 5, 10, 15] | None = None
    bot: int | None = Field(default=None, ge=1, le=4)
    lists: list[Literal["armed", "fno", "nifty50", "nifty100", "niftybank"]] | None = Field(default=None, max_length=5)
    play_handoffs: bool | None = None


@app.get("/api/trend-picks/auto")
async def trend_picks_auto_get():
    """The daily automatic strong-trend pick backtest (trend_picks.py): its settings and the last day it ran."""
    from dataclasses import asdict

    return asdict(trend_picks.load())


@app.put("/api/trend-picks/auto")
async def trend_picks_auto_put(body: TrendPicksAuto):
    from dataclasses import asdict

    try:
        return asdict(trend_picks.update(**body.model_dump()))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


async def _trend_picks_daily(s: trend_picks.AutoSettings, day: dt.date) -> tuple[int | None, str]:
    """The daily run (trend_picks.run): today's strong-trend picks backtested on the chosen bot, at full speed."""
    session = replays[s.bot]
    if session.status in ("LOADING", "PLAYING", "PAUSED"):
        return None, ""  # that bot's replay player is busy: try again on the next pass
    try:
        names = await _auto_symbols(False, s.lists)
        h, m = s.pick_time.split(":")
        await _start_trend_picks(
            s.bot, names, day, day, dt.time(int(h), int(m)), s.top_n, s.direction, s.minutes, max(SPEEDS),
            play_handoffs=s.play_handoffs,
        )
    except HTTPException as exc:
        return None, f"not started: {exc.detail}"
    except Exception as exc:  # noqa: BLE001
        return None, f"not started: {exc}"
    which = {"BULLISH": "strong bullish", "BEARISH": "strong bearish"}.get(s.direction, "strong bullish and bearish")
    return session.run_id, f"backtested today's top {s.top_n} {which} picks at {s.pick_time} on Bot {s.bot}"


class TrendCompareStart(BaseModel):
    date: str
    end_date: str | None = None
    universe: list[str]
    #: 2-8 pick times (HH:MM, 09:20-14:30) to set side by side.
    times: list[str] = Field(min_length=trend_compare.MIN_TIMES, max_length=trend_compare.MAX_TIMES)
    top_n: int = Field(default=10, ge=1, le=trend_picks.MAX_TOP_N)
    direction: Literal["BULLISH", "BEARISH", "BOTH"] = "BOTH"
    minutes: Literal[1, 3, 5, 10, 15] = 5
    bot: int = Field(default=1, ge=1, le=4)
    lists: list[str] = Field(default_factory=list, max_length=5)
    play_handoffs: bool = True
    #: TREND (strong-trend picks, trend_picks.py) or ACTIVE (Most active picks, active_picks.py, with rank / bias /
    #: min_value_cr).
    source: Literal["TREND", "ACTIVE"] = "TREND"
    rank: Literal["value", "rvol", "change", "vwap", "speed", "range"] = "rvol"
    bias: Literal["BOTH", "LONG", "SHORT", "ANY"] = "BOTH"
    min_value_cr: float = Field(default=5.0, ge=0, le=10000)


@app.post("/api/trend-picks/compare")
async def trend_compare_start(body: TrendCompareStart):
    """Compare pick times (trend_compare.py): the same strong-trend pick backtest once per time, one after another on
    the bot's replay player at full speed, then side by side. Practice money; never sends an order."""
    _refuse_desk_replay_in_market()
    comp = trend_compare.comparer()
    if comp.running:
        raise HTTPException(409, "A pick-time comparison is already running. Stop it first.")
    if stock_rank.jobs().running:
        raise HTTPException(409, "A stock ranking is running on the replay player. Stop it first or wait for it.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date, allow_today=_replay_today_ok())
        times = trend_compare.clean_times(body.times)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    eng = engine if body.bot == 1 else bot_engines.get(body.bot)
    if eng is None:
        raise HTTPException(404, f"No bot {body.bot}")
    cfg = eng.load_config()
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, f"Switch {getattr(cfg, 'bot_name', None) or f'Bot {body.bot}'} to PAPER first.")
    names = [n for n in dict.fromkeys((s or "").strip().upper() for s in body.universe) if is_nse_symbol(n)]
    if not names:
        raise HTTPException(400, "Give at least one stock to pick from.")
    names = names[: trend_picks.MAX_UNIVERSE]
    if not engine.broker.token:
        engine.broker.adopt_saved_session()
    if not engine.broker.token:
        raise HTTPException(400, "Replay needs Groww candles. Log in to Groww on the Settings page first.")
    item = trend_compare.new(
        day.isoformat(), end_day.isoformat(), times, body.top_n, body.direction, body.minutes, body.bot, len(names),
        lists=body.lists,
    )
    item["play_handoffs"] = body.play_handoffs
    active = (
        {"rank": body.rank, "bias": body.bias, "min_value_cr": body.min_value_cr} if body.source == "ACTIVE" else None
    )
    item["source"] = body.source
    if active:
        item["active"] = active
    trend_compare.put(item)
    session = replays[body.bot]

    async def start(at: str) -> int | None:
        h, m = at.split(":")
        await _start_trend_picks(
            body.bot, names, day, end_day, dt.time(int(h), int(m)), body.top_n, body.direction, body.minutes,
            max(SPEEDS), play_handoffs=body.play_handoffs, active=active,
        )
        # The run id is known once the candles are in; wait for it (or for the load to fail).
        while session.run_id is None and session.status == "LOADING":
            await asyncio.sleep(1)
        if session.run_id is None:
            raise RuntimeError(session.error or f"the replay ended {session.status.lower()} before it started")
        return session.run_id

    def info() -> tuple[str, int | None]:
        return session.status, session.run_id

    async def stop() -> None:
        if session.status in trend_compare.BUSY:
            await session.stop("REPLAY_STOPPED")

    comp.start(item, start, info, stop)
    return item


def _compare_view(item: dict) -> dict:
    """A comparison with its side-by-side result (blocking: reads each run's trades)."""
    runs = {e["run_id"]: get_run(e["run_id"]) for e in item.get("runs") or [] if e.get("run_id") is not None}
    return {**item, "result": trend_compare.summarize(item, runs)}


@app.get("/api/trend-picks/compare")
async def trend_compare_list():
    """The saved pick-time comparisons, newest first (no results; open one for them)."""
    comp = trend_compare.comparer()
    items = list(reversed(trend_compare.load_all()))
    running_id = comp.current["id"] if comp.running and comp.current else None
    return {"running": running_id, "items": items}


@app.get("/api/trend-picks/compare/{cid}")
async def trend_compare_get(cid: int):
    comp = trend_compare.comparer()
    item = comp.current if comp.running and comp.current and comp.current["id"] == cid else trend_compare.get(cid)
    if item is None:
        raise HTTPException(404, "No such comparison")
    return await asyncio.to_thread(_compare_view, item)


@app.post("/api/trend-picks/compare/stop")
async def trend_compare_stop():
    """Stop the running comparison and its replay; the finished times stay."""
    comp = trend_compare.comparer()
    if not comp.running:
        return {"stopped": False}
    comp.stop()
    return {"stopped": True}


@app.delete("/api/trend-picks/compare/{cid}")
async def trend_compare_delete(cid: int):
    """Forget a comparison. Its runs stay in Backtests."""
    comp = trend_compare.comparer()
    if comp.running and comp.current and comp.current["id"] == cid:
        raise HTTPException(409, "Stop the comparison before deleting it.")
    if not trend_compare.delete(cid):
        raise HTTPException(404, "No such comparison")
    return {"deleted": cid}


class StockRankStart(BaseModel):
    date: str
    end_date: str | None = None
    #: The stocks to test (the card's scan lists), at most stock_rank.MAX_STOCKS.
    universe: list[str]
    bot: int = Field(default=1, ge=1, le=4)
    batch: Literal[10, 15, 20, 25, 30, 40, 50] = 25
    play_handoffs: bool = True
    lists: list[str] = Field(default_factory=list, max_length=5)


@app.post("/api/stock-rank")
async def stock_rank_start(body: StockRankStart):
    """Stock ranking (stock_rank.py): backtest the bot's own strategy on many stocks, `batch` stocks per run, one run
    after another on its replay player at full speed, then rank the stocks. Practice money; never sends an order."""
    _refuse_desk_replay_in_market()
    jobs = stock_rank.jobs()
    if jobs.running:
        raise HTTPException(409, "A stock ranking is already running. Stop it first.")
    if trend_compare.comparer().running:
        raise HTTPException(409, "A pick-time comparison is running on the replay player. Stop it first or wait for it.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date, allow_today=_replay_today_ok())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    eng = engine if body.bot == 1 else bot_engines.get(body.bot)
    if eng is None:
        raise HTTPException(404, f"No bot {body.bot}")
    cfg = eng.load_config()
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, f"Switch {getattr(cfg, 'bot_name', None) or f'Bot {body.bot}'} to PAPER first.")
    names = [n for n in dict.fromkeys((s or "").strip().upper() for s in body.universe) if is_nse_symbol(n)]
    if not names:
        raise HTTPException(400, "Give at least one stock to test.")
    names = names[: stock_rank.MAX_STOCKS]
    if not engine.broker.token:
        engine.broker.adopt_saved_session()
    if not engine.broker.token:
        raise HTTPException(400, "Replay needs Groww candles. Log in to Groww on the Settings page first.")
    item = stock_rank.new(
        day.isoformat(), end_day.isoformat(), names, body.bot, body.batch, body.play_handoffs, lists=body.lists
    )
    session = replays[body.bot]

    async def start(symbols: list[str]) -> int | None:
        await replay_start(
            ReplayStart(
                date=day.isoformat(), end_date=end_day.isoformat(), symbols=symbols, bot=body.bot,
                speed=max(SPEEDS), play_handoffs=body.play_handoffs,
            )
        )
        while session.run_id is None and session.status == "LOADING":
            await asyncio.sleep(1)
        if session.run_id is None:
            raise RuntimeError(session.error or f"the replay ended {session.status.lower()} before it started")
        return session.run_id

    def info() -> tuple[str, int | None]:
        return session.status, session.run_id

    async def stop() -> None:
        if session.status in trend_compare.BUSY:
            await session.stop("REPLAY_STOPPED")

    jobs.start(item, start, info, stop, key="symbols", save=stock_rank.put)
    return item


@app.get("/api/stock-rank")
async def stock_rank_list():
    """The saved ranking jobs, newest first (no rankings; open one for it)."""
    jobs = stock_rank.jobs()
    running_id = jobs.current["id"] if jobs.running and jobs.current else None
    return {"running": running_id, "items": list(reversed(stock_rank.load_all()))}


@app.get("/api/stock-rank/{jid}")
async def stock_rank_get(jid: int, earlier_days: int = Query(0, ge=0, le=90)):
    """One job with its stock ranking; `earlier_days` also counts the bot's finished backtests of those last days."""
    jobs = stock_rank.jobs()
    item = jobs.current if jobs.running and jobs.current and jobs.current["id"] == jid else stock_rank.get(jid)
    if item is None:
        raise HTTPException(404, "No such ranking")
    return await asyncio.to_thread(stock_rank.view, item, earlier_days)


@app.post("/api/stock-rank/stop")
async def stock_rank_stop():
    jobs = stock_rank.jobs()
    if not jobs.running:
        return {"stopped": False}
    jobs.stop()
    return {"stopped": True}


@app.delete("/api/stock-rank/{jid}")
async def stock_rank_delete(jid: int):
    """Forget a ranking. Its runs stay in Backtests."""
    jobs = stock_rank.jobs()
    if jobs.running and jobs.current and jobs.current["id"] == jid:
        raise HTTPException(409, "Stop the ranking before deleting it.")
    if not stock_rank.delete(jid):
        raise HTTPException(404, "No such ranking")
    return {"deleted": jid}


@app.post("/api/replay/start")
async def replay_start(body: ReplayStart):
    """Practice on a past day's Groww candles with one bot's settings. Never sends an order."""
    _refuse_desk_replay_in_market()
    eng = engine if body.bot == 1 else bot_engines.get(body.bot)
    if eng is None:
        raise HTTPException(404, f"No bot {body.bot}")
    cfg = eng.load_config()
    bot_label = getattr(cfg, "bot_name", None) or f"Bot {body.bot}"
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, f"Switch {bot_label} to PAPER before replaying its settings.")
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date, allow_today=_replay_today_ok())
        start = parse_start(body.start)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    cut = session_cut(day)
    if day == end_day and dt.datetime.combine(day, start, tzinfo=IST) >= cut:
        raise HTTPException(400, f"Today has closed candles only up to {cut:%H:%M} IST: pick an earlier start time.")
    if body.as_armed:
        symbols = []
        cid = arm_log.config_id_of(body.bot)
        day_at = day
        while day_at <= end_day:
            for name in arm_log.windows(cid, day_at):
                if name not in symbols:
                    symbols.append(name)
            day_at += dt.timedelta(days=1)
        if not symbols:
            raise HTTPException(400, f"{bot_label} had no stock armed on those days in the arm log.")
        symbols = symbols[:MAX_TRADE_SYMBOLS]
        start = parse_start("09:15")
    elif body.symbols:
        symbols = []
        for raw in body.symbols:
            name = (raw or "").strip().upper()
            if not is_nse_symbol(name):
                raise HTTPException(400, f"{raw!r} is not an NSE trading symbol.")
            if name not in symbols:
                symbols.append(name)
        if len(symbols) > MAX_TRADE_SYMBOLS:
            raise HTTPException(400, f"Replay at most {MAX_TRADE_SYMBOLS} stocks at once.")
    else:
        symbols = trade_names(cfg)
    if not symbols:
        raise HTTPException(400, "Arm at least one stock in the Stocks panel first.")
    if not engine.broker.token:
        engine.broker.adopt_saved_session()
    if not engine.broker.token:
        raise HTTPException(400, "Replay needs Groww candles. Log in to Groww on the desk Settings page first.")
    session = _replay_for(body.bot)
    await session.begin(
        engine.broker,
        symbols,
        day,
        start,
        body.speed,
        end_day=end_day,
        settings={
            **settings_snapshot(cfg),
            "stock_settings": stock_settings(cfg),
            "bot": body.bot,
            "bot_name": bot_label,
            **({"as_armed": True} if body.as_armed else {}),
            **({"play_handoffs": True} if body.play_handoffs else {}),
        },
        bot=body.bot,
        bot_name=bot_label,
    )
    return session.info()


class ReplayResume(BaseModel):
    run_id: int
    speed: int = 60


@app.get("/api/replay/resumable")
async def replay_resumable(bot: int = 1):
    """This bot's runs cut short (a restart, or Stop) with days left to play, newest first."""
    _replay_for(bot)
    return resumable_runs(bot)


@app.post("/api/replay/resume")
async def replay_resume(body: ReplayResume, bot: int = 1):
    """Carry on a cut-short run from the first day it had not finished, with its saved settings."""
    _refuse_desk_replay_in_market()
    runs = {r["id"]: r for r in resumable_runs(None)}
    found = runs.get(body.run_id)
    if found is None:
        raise HTTPException(404, f"Run #{body.run_id} cannot be resumed (finished, deleted or a Scalp-pick run).")
    run_bot = int(found["bot"])
    if run_bot != int(bot):
        raise HTTPException(409, f"Run #{body.run_id} belongs to Bot {run_bot}. Resume it from that bot's desk.")
    eng = engine if run_bot == 1 else bot_engines.get(run_bot)
    if eng is None:
        raise HTTPException(404, f"No bot {run_bot}")
    cfg = eng.load_config()
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, f"Switch {getattr(cfg, 'bot_name', None) or f'Bot {run_bot}'} to PAPER before replaying.")
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    if any(s.run_id == body.run_id and s.active for s in replays.values()):
        raise HTTPException(409, "That run is already playing.")
    if not engine.broker.token:
        engine.broker.adopt_saved_session()
    if not engine.broker.token:
        raise HTTPException(400, "Replay needs Groww candles. Log in to Groww on the desk Settings page first.")
    session = _replay_for(run_bot)
    try:
        await session.resume(engine.broker, body.run_id, body.speed)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return session.info()


@app.get("/api/replay/runs")
async def replay_runs(limit: int = 50):
    """Every replay run with its totals and the settings it used."""
    return await asyncio.to_thread(list_runs, max(1, min(limit, 500)))


@app.get("/api/replay/runs/{run_id}")
async def replay_run(run_id: int):
    """One run: day-wise P&L and the settings it used."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, "No such replay run")
    return run


@app.get("/api/replay/runs/{run_id}/trades")
async def replay_run_trades(run_id: int, symbol: str | None = None):
    """One run's trades with each one's best and worst move from entry (rupees per share)."""
    rows = await asyncio.to_thread(run_trades, run_id, symbol)
    if rows is None:
        raise HTTPException(404, "No such replay run")
    return rows


@app.delete("/api/replay/runs/{run_id}")
async def replay_run_delete(run_id: int):
    if any(s.run_id == run_id and s.active for s in replays.values()):
        raise HTTPException(409, "Stop this replay before deleting it.")
    if not delete_run(run_id):
        raise HTTPException(404, "No such replay run")
    return {"deleted": run_id}


@app.post("/api/replay/control")
async def replay_control(body: ReplayControl, bot: int = 1):
    session, _following = _replay_view(bot)
    if body.action == "stop":
        await session.stop()
        return session.info()
    try:
        session.control(body.action, body.speed, body.at)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return session.info()


@app.post("/api/replay/bot/review/{review_id}/{action}")
async def replay_answer_review(review_id: int, action: str, bot: int = 1):
    return await _answer_review(_replay_engine(bot), review_id, action)


@app.get("/api/replay/state")
async def replay_state(bot: int = 1):
    session, following = _replay_view(bot)
    if following and session.engine is not None:
        from replay import bot5_view

        return {**bot5_view(session.engine, session.engine.snapshot()), "replay": await replay_info(bot)}
    eng = _replay_engine(bot)
    return {**eng.snapshot(), "replay": _replay_for(bot).info()}


@app.get("/api/replay/chart")
async def replay_chart(limit: int = 240, symbol: str | None = None, bot: int = 1, interval: int | None = None):
    # Off the event loop: building the chart must not hold up /api/state.
    session, following = _replay_view(bot)
    if following and session.engine is not None:
        return await _chart(session.engine, limit, symbol, interval)
    return await _chart(_replay_engine(bot), limit, symbol, interval)


@app.post("/api/replay/bot/start")
async def replay_bot_start(bot: int = 1):
    eng = _replay_engine(bot)
    eng.release_manual_panic()
    eng.release_trade_cap()
    if eng.status in ("HALTED", "DAY_COMPLETED"):
        raise HTTPException(423, eng.halt_reason or "This replay day is finished")
    eng.hold_for_next_cross(eng.replay_symbols)
    eng.status = "RUNNING"
    eng.halt_reason = ""
    return {"bot_status": eng.status}


@app.post("/api/replay/bot/pause")
async def replay_bot_pause(bot: int = 1):
    eng = _replay_engine(bot)
    if eng.status == "RUNNING":
        eng.status = "PAUSED"
    return {"bot_status": eng.status}


@app.post("/api/replay/bot/force")
async def replay_bot_force(body: ForceOrder, bot: int = 1):
    eng = _replay_engine(bot)
    try:
        result = await eng.force_order(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/replay/bot/close")
async def replay_bot_close(body: CloseOrder, bot: int = 1):
    eng = _replay_engine(bot)
    try:
        result = await eng.close_symbol(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/replay/bot/kill")
async def replay_bot_kill(bot: int = 1):
    eng = _replay_engine(bot)
    await eng.kill("Manual PANIC SQUARE-OFF (replay)")
    return {"bot_status": eng.status, "halt_reason": eng.halt_reason}


def _scan_broker():
    """The Groww session the Scalp scans download with: the bots' one, or on the backtest server its
    own login (scan_session.py), so a scan there never shares the desk's rate limit with the bots."""
    if server_role.is_backtest():
        import scan_session

        return scan_session.own_session()
    return engine.broker


def _scan_job(fn, sma_fast: int, sma_slow: int, minutes: int, extra: tuple = (), cached: bool = False):
    """Each stock's read in the scanner process (scan_worker.py), or None to read in-process.

    `cached`: the job keeps each stock's earlier days, so later scans today download only today."""
    if not scan_worker.enabled():
        return None
    now = dt.datetime.now(IST)
    runner = scan_worker.run_cached if cached else scan_worker.run

    async def job(symbol, start, end):
        return await runner(_scan_broker(), fn, symbol, start, end, sma_fast, sma_slow, minutes, now, *extra)

    return job


class CrossScanStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=CROSS_SCAN_MAX)
    minutes: Literal[1, 2, 3, 5, 10, 15] = 5
    force: bool = False


# Scalp page "Pause scans" (memory only: a restart resumes). Refuses new Cross / Trend quality
# scans and stops a running one; the desk has its own switch for its Scalp work.
# SCANS=off: this desk's scans moved to the scanner server, so they stay paused for good.
_scans_paused = not server_role.scans_enabled()
_PAUSED_TEXT = "Scans are paused. Resume them at the top of the Scalp page."


class ScanPause(BaseModel):
    paused: bool


@app.get("/api/scans/pause")
async def scans_pause_status():
    return {"paused": _scans_paused, "scans_off": not server_role.scans_enabled()}


@app.put("/api/scans/pause")
async def scans_pause(body: ScanPause):
    """Pause or resume the terminal's Scalp scans. Read-only: candles only, no order path."""
    global _scans_paused
    if not body.paused and not server_role.scans_enabled():
        raise HTTPException(409, server_role.SCANS_OFF_TEXT)
    _scans_paused = bool(body.paused)
    if _scans_paused:
        cross_scanner.stop("Stopped: scans are paused.")
        chop_scanner.stop("Stopped: scans are paused.")
        morning_scanner.stop("Stopped: scans are paused.")
        high52_scanner.stop("Stopped: scans are paused.")
        carry_scanner.stop("Stopped: scans are paused.")
        turn_scanner.stop("Stopped: scans are paused.")
        scan_checker.stop("Stopped: scans are paused.")
    return {"paused": _scans_paused}


@app.post("/api/scan-stop/{kind}")
async def scan_stop(kind: str):
    """Stop one running Scalp scan (its card's Stop button); the last finished rows stay. Read-only scans only."""
    scanners = {
        "cross": cross_scanner,
        "chop": chop_scanner,
        "morning": morning_scanner,
        "high52": high52_scanner,
        "carry": carry_scanner,
        "turn": turn_scanner,
    }
    scanner = scanners.get(kind)
    if scanner is None:
        raise HTTPException(404, f"No scan called {kind!r}.")
    was = bool(scanner.snapshot().get("running"))
    scanner.stop("Stopped by you. The last finished rows are still shown.")
    return {**scanner.snapshot(), "stopped": was}


def _refuse_when_paused() -> None:
    if not server_role.scans_enabled():
        raise HTTPException(409, server_role.SCANS_OFF_TEXT)
    if _scans_paused:
        raise HTTPException(409, _PAUSED_TEXT)


@app.post("/api/cross-scan/start")
async def cross_scan_start(body: CrossScanStart):
    """Start a cross scan (read-only: candles only, no order). Returns at once; poll GET /api/cross-scan.

    Uses the main desk's SMA periods. The same candle (and the same stocks) is
    not scanned twice, and a rescan waits a minute.
    """
    _refuse_when_paused()
    cfg = engine.load_config()
    try:
        return cross_scanner.start(
            body.symbols,
            lambda symbol, start, end: fetch_frame(_scan_broker(), symbol, start, end),
            minutes=body.minutes,
            sma_fast=int(cfg.sma_fast),
            sma_slow=int(cfg.sma_slow),
            force=body.force,
            job=_scan_job(scan_worker.cross_job, int(cfg.sma_fast), int(cfg.sma_slow), body.minutes, cached=True),
            meta={"stocks": len(body.symbols), "source": "manual"},
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/cross-scan")
async def cross_scan_status():
    return {**cross_scanner.snapshot(), "paused": _scans_paused}


class ChopScanStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=CROSS_SCAN_MAX)
    minutes: Literal[1, 2, 3, 5, 10, 15] = 5
    quiet_min: int = Field(default=chop_scan.QUIET_MIN_DEFAULT, ge=1, le=375)
    alert_on_quiet: bool = False
    # Quiet-runner alerts only for stocks priced inside this range (₹; 0 = no bound).
    price_min: float = Field(default=0.0, ge=0)
    price_max: float = Field(default=0.0, ge=0)
    force: bool = False


def _chop_alert(message: str) -> None:
    """Send a quiet-runner Telegram to its own channel (else the trade one). Never raises."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _send() -> None:
        try:
            from app.services.alert_notifier import alert_notifier

            await alert_notifier.send_quiet(message)
        except Exception:  # noqa: BLE001
            logging.getLogger("sma.alerts").exception("quiet-runner alert failed")

    loop.create_task(_send())


def _start_chop(
    symbols: list[str],
    *,
    minutes: int,
    quiet_min: int,
    alert_on_quiet: bool,
    price_min: float,
    price_max: float,
    force: bool,
    source: str = "manual",
):
    cfg = engine.load_config()
    return chop_scanner.start(
        symbols,
        lambda symbol, start, end: fetch_frame(_scan_broker(), symbol, start, end),
        minutes=minutes,
        sma_fast=int(cfg.sma_fast),
        sma_slow=int(cfg.sma_slow),
        quiet_min=quiet_min,
        alert_on_quiet=alert_on_quiet,
        alert_cb=_chop_alert if alert_on_quiet else None,
        force=force,
        price_min=price_min,
        price_max=price_max,
        job=_scan_job(scan_worker.chop_job, int(cfg.sma_fast), int(cfg.sma_slow), minutes, extra=(quiet_min,), cached=True),
        meta={
            "stocks": len(symbols),
            "source": source,
            "alert_on_quiet": alert_on_quiet,
            "price_min": price_min,
            "price_max": price_max,
        },
    )


@app.post("/api/chop-scan/start")
async def chop_scan_start(body: ChopScanStart):
    """Start a chop/trend scan (read-only: candles only, no order). Returns at once; poll GET /api/chop-scan.

    Counts today's SMA fast / slow crosses on the N-minute candle for each stock,
    with the main desk's SMA periods, and labels each one TRENDING / MIXED /
    CHOPPY. No bot setting is read or changed. With `alert_on_quiet` on, the
    first time a stock reads as a quiet runner during NSE hours gets a Telegram
    alert; a stock is alerted at most once per day.
    """
    _refuse_when_paused()
    try:
        out = _start_chop(
            body.symbols,
            minutes=body.minutes,
            quiet_min=body.quiet_min,
            alert_on_quiet=body.alert_on_quiet,
            price_min=body.price_min,
            price_max=body.price_max,
            force=body.force,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    # A manual scan counts as a run: the next automatic one waits a full interval.
    chop_autos.note_run()
    return out


async def _auto_symbols(armed_only: bool, lists: list[str] | None = None) -> list[str]:
    """The stocks an automatic Trend quality scan reads: the card's ticked lists (armed stocks, F&O,
    Nifty 50 / 100 / Bank) joined, else every bot's Trade list or the F&O list."""
    if lists:
        names: list[str] = []
        if "armed" in lists:
            names.extend(await _auto_symbols(True))
        index = [k for k in lists if k != "armed"]
        if index:
            from app.api.routes_scalp import fno_universe

            out = await fno_universe(lists=",".join(index))
            if not out["symbols"] and not names:
                raise RuntimeError(out.get("error") or "The scan lists are empty, so there is nothing to scan.")
            names.extend(n for n in out["symbols"] if n not in names)
        return names[:CROSS_SCAN_MAX]
    if armed_only:
        if server_role.is_scanner():
            from app.api.routes_link import desk_armed

            body = await desk_armed()
            if body.get("error"):
                raise RuntimeError(body["error"])
        else:
            body = armed_lists()
        names = {str(s).upper() for b in body.get("bots", []) for s in (b.get("armed") or [])}
        names |= {str(s).upper() for s in (body.get("research") or [])}
        if not names:
            raise RuntimeError("No stocks are armed on any bot, so there is nothing to scan.")
        return sorted(names)
    from app.services.active_stocks import active_scanner

    names = active_scanner.universe_symbols()
    if not names:
        raise RuntimeError(active_scanner.error or "The F&O stock list is empty, so there is nothing to scan.")
    return list(names)[:CROSS_SCAN_MAX]


async def _chop_auto_start(s: chop_auto.AutoSettings) -> None:
    """One automatic Trend quality pass with the card's saved choices (read-only)."""
    if _scans_paused:
        raise RuntimeError("Scans are paused at the top of the Scalp page.")
    if chop_scanner.status.running:
        return
    symbols = await _auto_symbols(s.armed_only, s.lists)
    _start_chop(
        symbols,
        minutes=s.minutes,
        quiet_min=s.quiet_min,
        alert_on_quiet=s.alert_on_quiet,
        price_min=s.price_min,
        price_max=s.price_max,
        force=True,
        source="auto",
    )


class ChopAutoUpdate(BaseModel):
    on: bool | None = None
    every_min: Literal[5, 10, 15, 20, 30, 45, 60] | None = None
    minutes: Literal[1, 2, 3, 5, 10, 15] | None = None
    armed_only: bool | None = None
    quiet_min: int | None = Field(default=None, ge=1, le=375)
    alert_on_quiet: bool | None = None
    price_min: float | None = Field(default=None, ge=0)
    price_max: float | None = Field(default=None, ge=0)
    lists: list[Literal["armed", "fno", "nifty50", "nifty100", "niftybank"]] | None = None


@app.put("/api/chop-scan/auto")
async def chop_scan_auto(body: ChopAutoUpdate):
    """Turn the server's Trend quality re-scan on/off, set how often, and the choices it scans with."""
    if body.on and not server_role.scans_enabled():
        raise HTTPException(409, server_role.SCANS_OFF_TEXT)
    chop_autos.update(**body.model_dump())
    return chop_autos.snapshot()


@app.get("/api/chop-scan")
async def chop_scan_status():
    return {**chop_scanner.snapshot(), "paused": _scans_paused, "auto": chop_autos.snapshot()}


class TrendAlertUpdate(BaseModel):
    on: bool | None = None
    sizes: list[Literal[1, 2, 3, 5, 10, 15]] | None = None
    direction: Literal["UP", "DOWN", "SAME"] | None = None
    kind: Literal["SIDE", "STRONG"] | None = None
    top_n: int | None = Field(default=None, ge=1, le=trend_rank.MAX_TOP)
    max_age_min: int | None = Field(default=None, ge=0, le=24 * 60)
    min_gap_pct: float | None = Field(default=None, ge=0, le=5)
    skip_fading: bool | None = None
    telegram: bool | None = None
    call: bool | None = None


def _trend_alerts() -> dict:
    from dataclasses import asdict

    return {**asdict(trend_rank.load()), "recent": trend_rank.recent[:20]}


@app.get("/api/chop-scan/trend-alerts")
async def trend_alerts_get():
    """The trend-rank alert settings and the last alerts sent (trend_rank.py). Information only."""
    return _trend_alerts()


@app.put("/api/chop-scan/trend-alerts")
async def trend_alerts_put(body: TrendAlertUpdate):
    """Save the trend-rank alert: after each Trend quality pass in market hours, Telegram / call the top ranks."""
    data = body.model_dump(exclude_none=True)
    if data.get("on") and not (data.get("sizes") or trend_rank.load().sizes):
        raise HTTPException(400, "Tick at least one candle size under Trend on first")
    await asyncio.to_thread(trend_rank.save, data)
    return _trend_alerts()


# ---- Scan & arm (scan_arm.py) -------------------------------------------------------------------
# A Trend quality pass, then the matching stocks go on one bot's Trade list (arming only, no order).
# On the desk the bots are armed in-process; on the scanner (and the backtest server with the link
# token) through the desk link, so the desk's own checks apply (LIVE needs confirm_live).

SCAN_ARM_WAIT_SEC = 30 * 60


def _scan_arm_can_scan() -> bool:
    return server_role.scans_enabled() and not _scans_paused


def _scan_arm_remote() -> bool:
    """True where the bots to arm are the desk's, reached over the link."""
    return server_role.is_scanner() or (server_role.is_backtest() and bool(server_role.link_token()))


async def _scan_arm_bots() -> dict[int, dict]:
    if _scan_arm_remote():
        from app.api.routes_link import desk_armed

        body = await desk_armed()
        if body.get("error"):
            raise RuntimeError(body["error"])
    else:
        body = await asyncio.to_thread(armed_lists)
    return {int(b["bot"]): b for b in body.get("bots", []) if b.get("bot") in (1, *EXTRA_BOTS)}


async def _scan_arm_arm(bot: int, symbol: str, source: str, confirm_live: bool) -> str | None:
    """Put one stock on a bot's Trade list. None when armed, else why not."""
    if _scan_arm_remote():
        from app.api import routes_link

        try:
            res = await routes_link._to_desk(
                "POST",
                routes_link.ARM_PATH,
                {"target": str(bot), "symbol": symbol, "source": source, "confirm_live": confirm_live},
            )
        except HTTPException as exc:
            return str(exc.detail)
        finally:
            routes_link._cache.pop("desk", None)
        if res.status_code == 200:
            return None
        try:
            detail = json.loads(res.body).get("detail")
        except (ValueError, AttributeError):
            detail = None
        return str(detail or f"the desk answered {res.status_code}")
    try:
        await _set_trade_symbol(_bot_engine_for(bot), TradeSymbolUpdate(symbol=symbol, armed=True, source=source))
    except HTTPException as exc:
        return str(exc.detail)
    return None


async def _scan_arm_scan(symbols: list[str], minutes: int, progress) -> dict:
    """One Trend quality pass of these stocks (waits for a pass already running), then its snapshot."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + SCAN_ARM_WAIT_SEC
    while True:
        if not server_role.scans_enabled():
            raise RuntimeError(server_role.SCANS_OFF_TEXT)
        if _scans_paused:
            raise RuntimeError(_PAUSED_TEXT)
        if not chop_scanner.status.running:
            before, as_of = chop_scanner.passes, chop_scanner.status.as_of
            _start_chop(
                symbols, minutes=minutes, quiet_min=chop_scan.QUIET_MIN_DEFAULT, alert_on_quiet=False,
                price_min=0.0, price_max=0.0, force=True, source="scan-arm",
            )
            if chop_scanner.passes != before:
                break
            progress("Waiting a minute after the last scan")
        else:
            progress("Waiting for the Trend quality scan already running")
        if loop.time() > deadline:
            raise RuntimeError("The scan could not start in time.")
        await asyncio.sleep(3)
    while chop_scanner.status.running:
        st = chop_scanner.status
        progress(f"Scanning {st.done}/{st.total} stocks")
        if loop.time() > deadline:
            raise RuntimeError("The scan took too long.")
        await asyncio.sleep(2)
    snap = chop_scanner.snapshot()
    if snap.get("as_of") == as_of:
        raise RuntimeError(snap.get("error") or "The scan did not finish.")
    return snap


def _scan_arm_hooks() -> dict:
    return {
        "symbols": lambda lists: _auto_symbols(False, lists),
        "scan": _scan_arm_scan,
        "bots": _scan_arm_bots,
        "arm": _scan_arm_arm,
    }


async def _scan_arm_alert(message: str) -> None:
    from app.services.alert_notifier import alert_notifier

    await alert_notifier.send(message)


def _scan_arm_scheduled(p: scan_arm.Preset, at: str) -> None:
    """A preset's time came: run it (PAPER bots only) and send the result as a Telegram."""
    scan_arm_runner.start(p, trigger="schedule", at=at, notify=_scan_arm_alert, **_scan_arm_hooks())


class ScanArmPresetBody(BaseModel):
    id: str | None = Field(default=None, max_length=16)
    name: str | None = Field(default=None, max_length=40)
    bot: Literal[1, 2, 3, 4, 5] | None = None
    lists: list[Literal["armed", "fno", "nifty50", "nifty100", "niftybank"]] | None = None
    minutes: Literal[1, 2, 3, 5, 10, 15] | None = None
    sizes: list[Literal[1, 2, 3, 5, 10, 15]] | None = Field(default=None, max_length=6)
    direction: Literal["UP", "DOWN", "SAME"] | None = None
    kind: Literal["SIDE", "STRONG"] | None = None
    max_age_min: int | None = Field(default=None, ge=0, le=24 * 60)
    min_gap_pct: float | None = Field(default=None, ge=0, le=5)
    skip_fading: bool | None = None
    top_n: int | None = Field(default=None, ge=1, le=scan_arm.MAX_TOP)
    times: list[str] | None = Field(default=None, max_length=scan_arm.MAX_TIMES)
    on: bool | None = None


class ScanArmRunBody(BaseModel):
    preset_id: str | None = Field(default=None, max_length=16)
    # Or a one-off recipe, not saved.
    preset: ScanArmPresetBody | None = None
    # The person confirmed arming on a LIVE bot (terminal button only; MCP and the schedule never send it).
    confirm_live: bool = False
    source: Literal["manual", "mcp"] = "manual"


def _scan_arm_state() -> dict:
    return {
        "presets": [scan_arm.preset_dict(p) for p in scan_arm.load()],
        "running": {"name": scan_arm_runner.current, "stage": scan_arm_runner.stage} if scan_arm_runner.busy else None,
        "recent": scan_arm_runner.recent[:10],
        "scans_enabled": server_role.scans_enabled(),
        "paused": _scans_paused,
        "arms_desk": _scan_arm_remote(),
        "grace_min": scan_arm.GRACE_MIN,
    }


@app.get("/api/scan-arm")
async def scan_arm_get():
    """Scan & arm presets, the run in progress and the last runs (scan_arm.py)."""
    return await asyncio.to_thread(_scan_arm_state)


@app.put("/api/scan-arm/presets")
async def scan_arm_save(body: ScanArmPresetBody):
    """Add (no id) or change a Scan & arm preset. Times are IST "HH:MM" between 09:15 and 15:30."""
    data = body.model_dump(exclude_none=True)
    if body.sizes is not None and not body.sizes:
        raise HTTPException(400, "Tick at least one candle size")
    if body.times is not None:
        bad = [t for t in body.times if not scan_arm._clean_times([t])]
        if bad:
            raise HTTPException(400, f"Times are HH:MM between 09:15 and 15:30 IST (not {', '.join(bad[:3])})")
    if body.id and scan_arm.get(body.id) is None:
        raise HTTPException(404, "No such preset")
    try:
        p = await asyncio.to_thread(scan_arm.save, data)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return scan_arm.preset_dict(p)


@app.delete("/api/scan-arm/presets/{pid}")
async def scan_arm_delete(pid: str):
    if not await asyncio.to_thread(scan_arm.delete, pid):
        raise HTTPException(404, "No such preset")
    return {"deleted": pid}


@app.post("/api/scan-arm/run")
async def scan_arm_run(body: ScanArmRunBody):
    """Run a preset (or a one-off recipe) now, in the background: scan, then arm the matches.
    Poll GET /api/scan-arm. Arming only: no order. A LIVE bot needs confirm_live (never from MCP)."""
    if not server_role.scans_enabled():
        raise HTTPException(409, server_role.SCANS_OFF_TEXT)
    if _scans_paused:
        raise HTTPException(409, _PAUSED_TEXT)
    if scan_arm_runner.busy:
        raise HTTPException(409, "A Scan & arm run is already going; wait for it to finish.")
    if body.preset_id:
        p = await asyncio.to_thread(scan_arm.get, body.preset_id)
        if p is None:
            raise HTTPException(404, "No such preset")
    elif body.preset is not None:
        p = scan_arm.clean(body.preset.model_dump(exclude_none=True, exclude={"id", "times", "on"}))
        p.name = body.preset.name or "One-off scan"
    else:
        raise HTTPException(400, "Give a preset_id or a preset")
    confirm_live = body.confirm_live and body.source == "manual"
    try:
        bots_now = await _scan_arm_bots()
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc
    bot = bots_now.get(p.bot)
    if bot is None:
        raise HTTPException(404, f"Bot {p.bot} was not found")
    if (bot.get("mode") or "PAPER").upper() == "LIVE" and not confirm_live:
        raise HTTPException(
            409,
            f"{bot.get('name') or f'Bot {p.bot}'} is LIVE: confirm the LIVE arm in the terminal"
            + (" (outside tools only arm PAPER bots)." if body.source == "mcp" else "."),
        )
    scan_arm_runner.start(p, trigger=body.source, confirm_live=confirm_live, **_scan_arm_hooks())
    return {"started": True, "preset": scan_arm.preset_dict(p)}


class FastMoveUpdate(BaseModel):
    on: bool | None = None
    pct: float | None = Field(default=None, ge=0.05, le=fast_move.MAX_PCT)
    minutes: int | None = Field(default=None, ge=1, le=fast_move.MAX_MINUTES)
    scan_on: bool | None = None
    scan_speed: float | None = Field(default=None, ge=0.01, le=fast_move.MAX_SPEED)
    cooldown_min: int | None = Field(default=None, ge=1, le=240)
    call: bool | None = None


def _fast_moves() -> dict:
    from dataclasses import asdict

    return {
        **asdict(fast_move.load()),
        "recent": fast_move.watcher.recent[:20],
        "watches_armed": not server_role.no_orders(),
        "scans_enabled": server_role.scans_enabled(),
    }


@app.get("/api/fast-moves")
async def fast_moves_get():
    """Fast-move alert settings and the last alerts (fast_move.py). Alerts only."""
    return await asyncio.to_thread(_fast_moves)


@app.put("/api/fast-moves")
async def fast_moves_put(body: FastMoveUpdate):
    """Save the fast-move alerts: armed / held stocks moving `pct` % within `minutes`, and fast movers in the scans."""
    await asyncio.to_thread(fast_move.save, body.model_dump(exclude_none=True))
    return await asyncio.to_thread(_fast_moves)


class MorningScanStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=CROSS_SCAN_MAX)
    force: bool = False


@app.post("/api/morning-scan/start")
async def morning_scan_start(body: MorningScanStart):
    """Start the 09:30 morning checklist (read-only: candles only, no order). Poll GET /api/morning-scan.

    Five checks on the three 09:15-09:30 five-minute candles per stock (morning_scan.py), with the
    main desk's SMA periods. No bot setting is read or changed.
    """
    _refuse_when_paused()
    cfg = engine.load_config()
    try:
        return morning_scanner.start(
            body.symbols,
            lambda symbol, start, end: fetch_frame(_scan_broker(), symbol, start, end),
            sma_fast=int(cfg.sma_fast),
            sma_slow=int(cfg.sma_slow),
            force=body.force,
            job=_scan_job(scan_worker.morning_job, int(cfg.sma_fast), int(cfg.sma_slow), morning_scan.CANDLE_MIN),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/morning-scan")
async def morning_scan_status():
    return {**morning_scanner.snapshot(), "paused": _scans_paused}


class CarryScanStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=CROSS_SCAN_MAX)
    minutes: Literal[1, 3, 5, 10, 15] = 5
    force: bool = False


@app.post("/api/carry-scan/start")
async def carry_scan_start(body: CarryScanStart):
    """Start the carry-over scan (read-only: candles only, no order). Poll GET /api/carry-scan.

    Per stock, on the picked candle: which way the SMA fast / slow pointed at yesterday's close, and today whether it
    continued or reversed, the gap at the open, the deviation from yesterday's closing SMA and the best / worst move
    (carry_scan.py), with the main desk's SMA periods. No bot setting is read or changed.
    """
    _refuse_when_paused()
    cfg = engine.load_config()
    try:
        return carry_scanner.start(
            body.symbols,
            lambda symbol, start, end: fetch_frame(_scan_broker(), symbol, start, end),
            minutes=body.minutes,
            sma_fast=int(cfg.sma_fast),
            sma_slow=int(cfg.sma_slow),
            force=body.force,
            job=_scan_job(scan_worker.carry_job, int(cfg.sma_fast), int(cfg.sma_slow), body.minutes),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/carry-scan")
async def carry_scan_status():
    return {**carry_scanner.snapshot(), "paused": _scans_paused}


class TurnScanStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=CROSS_SCAN_MAX)
    minutes: Literal[1, 3, 5, 10, 15] = 5
    count: int = Field(default=turn_scan.DEFAULT_COUNT, ge=1, le=turn_scan.MAX_COUNT)
    rule: Literal["COLOUR", "CLOSES", "BOTH"] = "COLOUR"
    need_fast: bool = True
    alert: bool = False
    force: bool = False


@app.post("/api/turn-scan/start")
async def turn_scan_start(body: TurnScanStart):
    """Start the Turning scan (read-only: candles only, no order). Poll GET /api/turn-scan.

    Per stock, on the picked candle: the SMA trend now and, when the last `count` closed candles in a row went the other
    way (and, with `need_fast`, closed beyond the fast SMA), how close the cross is (turn_scan.py), with the main desk's
    SMA periods. `alert` sends the stocks newly turning by Telegram (quiet-runner channel) in market hours."""
    _refuse_when_paused()
    cfg = engine.load_config()
    _turn_alert["on"] = bool(body.alert)
    try:
        return turn_scanner.start(
            body.symbols,
            lambda symbol, start, end: fetch_frame(_scan_broker(), symbol, start, end),
            minutes=body.minutes,
            count=body.count,
            rule=body.rule,
            need_fast=body.need_fast,
            sma_fast=int(cfg.sma_fast),
            sma_slow=int(cfg.sma_slow),
            force=body.force,
            job=_scan_job(
                scan_worker.turn_job, int(cfg.sma_fast), int(cfg.sma_slow), body.minutes,
                extra=(body.count, body.rule, body.need_fast),
            ),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/turn-scan")
async def turn_scan_status():
    return {**turn_scanner.snapshot(), "alert": _turn_alert["on"], "paused": _scans_paused}


class High52ScanStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=CROSS_SCAN_MAX)
    force: bool = False


@app.post("/api/high52-scan/start")
async def high52_scan_start(body: High52ScanStart):
    """Start the 52-week high scan (read-only: Groww daily candles, no order). Poll GET /api/high52-scan.

    Each stock's distance from its 52-week high and low, whether it broke the high today, the day's and the
    year's move (high52.py). No bot setting is read or changed.
    """
    _refuse_when_paused()
    try:
        return high52_scanner.start(
            body.symbols,
            lambda symbol, start, end: high52.fetch_daily(_scan_broker(), symbol, start, end),
            force=body.force,
            job=_scan_job(scan_worker.high52_job, 0, 0, high52.DAILY_MINUTES),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/high52-scan")
async def high52_scan_status():
    return {**high52_scanner.snapshot(), "paused": _scans_paused}


# ---- open P&L log: each desk's "Open" figure, minute by minute (open_pnl_log.py)

OPEN_PNL_DESKS = ("1", "2", "3", "4", "5", "research")


def _open_pnl_desks() -> list[tuple[str, object]]:
    """Every live desk the log samples: the main desk (bot 1), bots 2-4 and the research desk."""
    out: list[tuple[str, object]] = [("1", engine)]
    out += [(str(n), eng) for n, eng in sorted(bot_engines.items())]
    out.append(("research", research_engine))
    return out


def _closed_by_stock(day: str, desk: str | None = None, modes: set[str] | None = None, run_id: int | None = None,
                     per_qty: int = 0):
    """The day's closed trades of one book as (symbol, exit "HH:MM", gross P&L), for the per-stock P&L log.

    `per_qty` > 0 restates each trade's gross P&L as if it had been that many shares (P&L dashboard's "Qty")."""
    from sqlalchemy import or_

    from models import TradeLog
    from strategy_engine import _mode_filter, bot_rows

    with session_factory()() as db:
        q = db.query(TradeLog.symbol, TradeLog.exit_time, TradeLog.gross_pnl, TradeLog.qty).filter(
            TradeLog.date == day, TradeLog.exit_time.isnot(None)
        )
        if run_id is not None:
            q = q.filter(TradeLog.run_id == run_id)
        elif desk == "research":
            q = q.filter(TradeLog.mode == "RESEARCH")
        else:
            q = q.filter(bot_rows(int(desk or 1)), or_(*[_mode_filter(m) for m in (modes or {"PAPER"})]))
        return [
            (str(sym).upper(), exit_at.strftime("%H:%M"), pnl_dashboard.per_qty(float(gross or 0.0), qty, per_qty))
            for sym, exit_at, gross, qty in q.all()
            if exit_at is not None
        ]


@app.get("/api/open-pnl")
async def open_pnl(
    desk: Literal["1", "2", "3", "4", "5", "research"] = "1",
    day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """One desk's open P&L through a day, one point a minute (the latest day when none is given). Read-only."""
    days = await asyncio.to_thread(open_pnl_log.days, desk)
    pick = day or (days[0]["day"] if days else None)
    series = await asyncio.to_thread(open_pnl_log.points, pick, desk) if pick else []
    modes = {str(p.get("mode") or "PAPER").upper() for p in series} or {"PAPER"}
    closed = await asyncio.to_thread(_closed_by_stock, pick, desk, modes) if pick else []
    return {
        "desk": desk,
        "day": pick,
        "days": days,
        "points": series,
        "by_stock": open_pnl_log.by_stock(series, closed),
        "summary": open_pnl_log.summary(series),
        "every_sec": open_pnl_log.EVERY_SEC,
        "keep_days": open_pnl_log.keep_days(),
    }


@app.get("/api/arm-log")
async def arm_log_day(
    desk: Literal["1", "2", "3", "4", "5", "research"] = "1",
    day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """When each stock was armed on / taken off a desk's Trade list on one IST day (today when none). Read-only."""
    pick = dt.date.fromisoformat(day) if day else dt.datetime.now(IST).date()
    cid = arm_log.config_id_of(desk)
    rows = await asyncio.to_thread(arm_log.rows, cid, pick)
    spans = await asyncio.to_thread(arm_log.windows, cid, pick)

    def clock(sec: int) -> str:
        return dt.datetime.fromtimestamp(sec, IST).strftime("%H:%M:%S")

    stocks = [
        {"symbol": name, "spans": [{"from": clock(a), "to": clock(b)} for a, b in sorted(parts)]}
        for name, parts in sorted(spans.items())
    ]
    return {"desk": desk, "day": pick.isoformat(), "rows": rows, "stocks": stocks}


@app.get("/api/handoffs")
async def handoffs(
    run_id: int | None = Query(None, ge=1),
    day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    mode: Literal["PAPER", "LIVE", "paper", "live"] | None = None,
    bot: int | None = Query(None, ge=1, le=5),
    limit: int = Query(500, ge=1, le=2000),
):
    """Stocks handed between the bots (recovery / V-turn → Bot 2, breakout → Bot 3, fast move ↔ Bot 5), newest first:
    a backtest run's (`run_id`), else a market day's (today when none). Bookkeeping only (handoff_log.py)."""
    pick = None if run_id is not None else (day or dt.datetime.now(IST).date().isoformat())
    rows = await asyncio.to_thread(handoff_log.rows, run_id=run_id, day=pick, mode=mode, bot=bot, limit=limit)
    return {"run_id": run_id, "day": pick, "rows": rows}


@app.get("/api/open-pnl/replays")
async def open_pnl_replays(bot: int = Query(1, ge=1, le=5)):
    """A bot's replay runs that have an open P&L log (newest first). Read-only."""
    return {"bot": bot, "runs": await asyncio.to_thread(open_pnl_log.replay_runs, bot)}


@app.get("/api/open-pnl/replay/{run_id}")
async def open_pnl_replay(run_id: int, day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """One replay run's open P&L through one of its days, one point per replayed minute. Read-only."""
    days = await asyncio.to_thread(open_pnl_log.replay_days, run_id)
    pick = day or (days[-1]["day"] if days else None)
    series = await asyncio.to_thread(open_pnl_log.replay_points, run_id, pick) if pick else []
    closed = await asyncio.to_thread(_closed_by_stock, pick, run_id=run_id) if pick else []
    return {
        "run_id": run_id,
        "day": pick,
        "days": days,
        "points": series,
        "by_stock": open_pnl_log.by_stock(series, closed),
        "summary": open_pnl_log.summary(series),
        "keep_days": open_pnl_log.keep_days(),
    }


# ---- P&L dashboard: when in the day is the P&L best? (pnl_dashboard.py, the desk's home page). Read-only.


def _dash_stocks(series: list[dict], closed) -> dict[str, list[float]]:
    return {
        name: pnl_dashboard.day_curve((r["t"], r["total"]) for r in rows)
        for name, rows in open_pnl_log.by_stock(series, closed).items()
    }


def _dash_replay_days(run_ids: list[int], basis: str, weekdays: set[int] | None, qty: int = 0) -> list[dict]:
    out = []
    for run_id in run_ids:
        for row in open_pnl_log.replay_days(run_id):
            day = row["day"]
            if weekdays is not None and dt.date.fromisoformat(day).weekday() not in weekdays:
                continue
            pts = pnl_dashboard.scale_points(open_pnl_log.replay_points(run_id, day), qty)
            sp = pnl_dashboard.span((p["t"], 0) for p in pts)
            if sp is None:
                continue
            stocks = _dash_stocks(pts, _closed_by_stock(day, run_id=run_id, per_qty=qty))
            curve = (pnl_dashboard.add_curves(list(stocks.values())) if qty
                     else pnl_dashboard.day_curve((p["t"], p[f"total_{basis}"]) for p in pts))
            label = f"R{run_id} · {day}" if len(run_ids) > 1 else day
            out.append(pnl_dashboard.build_day(f"{run_id}:{day}", label, curve, sp[0], sp[1], stocks))
    out.sort(key=lambda d: d["key"].split(":", 1)[1])
    return out[-pnl_dashboard.MAX_DAYS:]


def _dash_market_days(desks: list[str], start: str | None, end: str | None, basis: str, weekdays: set[int] | None,
                      qty: int = 0) -> list[dict]:
    picked = sorted(
        d["day"] for d in open_pnl_log.days(None, limit=400)
        if (not start or d["day"] >= start) and (not end or d["day"] <= end)
        and (weekdays is None or dt.date.fromisoformat(d["day"]).weekday() in weekdays)
    )[-pnl_dashboard.MAX_DAYS:]
    out = []
    for day in picked:
        curves, spans, stocks = [], [], {}
        for desk in desks:
            pts = pnl_dashboard.scale_points(open_pnl_log.points(day, desk), qty)
            sp = pnl_dashboard.span((p["t"], 0) for p in pts)
            if sp is None:
                continue
            spans.append(sp)
            modes = {str(p.get("mode") or "PAPER").upper() for p in pts} or {"PAPER"}
            desk_stocks = _dash_stocks(pts, _closed_by_stock(day, desk, modes, per_qty=qty))
            curves.append(pnl_dashboard.add_curves(list(desk_stocks.values())) if qty
                          else pnl_dashboard.day_curve((p["t"], p[f"total_{basis}"]) for p in pts))
            for name, c in desk_stocks.items():
                stocks[name] = pnl_dashboard.add_curves([stocks[name], c]) if name in stocks else c
        if not curves:
            continue
        first, last = min(s[0] for s in spans), max(s[1] for s in spans)
        out.append(pnl_dashboard.build_day(day, day, pnl_dashboard.add_curves(curves), first, last, stocks))
    return out


@app.get("/api/pnl-dashboard/options")
async def pnl_dashboard_options():
    """What the P&L dashboard can read: the backtest runs with a P&L log, and the market days per desk."""
    runs = await asyncio.to_thread(open_pnl_log.all_replay_runs)
    ids = [r["run_id"] for r in runs]

    def names() -> dict[int, dict]:
        from models import ReplayRun

        with session_factory()() as db:
            rows = db.query(ReplayRun).filter(ReplayRun.id.in_(ids)).all() if ids else []
            return {
                r.id: {"status": r.status, "start": r.start_date, "end": r.end_date,
                       "created_at": r.created_at.isoformat() if r.created_at else None}
                for r in rows
            }

    info = await asyncio.to_thread(names)
    market = await asyncio.to_thread(open_pnl_log.days, None, 400)
    return {
        "runs": [{**r, **info.get(r["run_id"], {})} for r in runs],
        "market_days": [d["day"] for d in market],
        "desks": list(OPEN_PNL_DESKS),
        "steps": list(pnl_dashboard.STEPS),
        "keep_days": open_pnl_log.keep_days(),
    }


@app.get("/api/pnl-dashboard")
async def pnl_dashboard_view(
    source: Literal["replay", "market"] = "replay",
    runs: str = Query("", description="Comma list of backtest run ids (source=replay)"),
    desks: str = Query("1", description="Comma list of desks 1-5, research (source=market)"),
    start: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    stocks: str = Query("", description="Comma list: only these stocks' P&L (before charges)"),
    basis: Literal["gross", "net"] = "gross",
    step: int = Query(5, ge=1, le=30),
    weekdays: str = Query("", description="Comma list 0-4 (Mon-Fri); empty = every day"),
    qty: int = Query(0, ge=0, le=1_000_000, description="Restate every trade as this many shares (0 = as traded)"),
):
    """Each day's P&L curve, high, low and close, the P&L by time of day across the days, the best time to close
    everything and the same per stock. Read-only."""
    days_of_week = {int(x) for x in weekdays.split(",") if x.strip().isdigit() and int(x) < 7} or None
    if source == "replay":
        ids = [int(x) for x in runs.split(",") if x.strip().isdigit()]
        if not ids:
            latest = await asyncio.to_thread(open_pnl_log.all_replay_runs, 1)
            ids = [latest[0]["run_id"]] if latest else []
        built = await asyncio.to_thread(_dash_replay_days, ids, basis, days_of_week, qty)
        picked = {"runs": ids}
    else:
        want = [d.strip() for d in desks.split(",") if d.strip() in OPEN_PNL_DESKS] or ["1"]
        built = await asyncio.to_thread(_dash_market_days, want, start, end, basis, days_of_week, qty)
        picked = {"desks": want, "start": start, "end": end}
    names = [s.strip().upper() for s in stocks.split(",") if s.strip()]
    out = await asyncio.to_thread(pnl_dashboard.analyze, built, step, names or None)
    all_stocks = sorted({s for d in built for s in d["stocks"]})
    return {"source": source, **picked, "basis": "gross" if names or qty else basis, "stocks_picked": names, "qty": qty,
            "all_stocks": all_stocks, **out}


# ---- scan history: every finished pass, kept (scan_history.py), and how it turned out (scan_accuracy.py)


@app.get("/api/scan-history")
async def scan_history_list(
    day: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    kind: Literal["cross", "chop"] | None = None,
):
    """The days with saved scans, and one day's runs (the latest day when none is given). Read-only."""
    days = await asyncio.to_thread(scan_history.days)
    pick = day or (days[0]["day"] if days else None)
    runs = await asyncio.to_thread(scan_history.runs, pick, kind) if pick else []
    return {"days": days, "day": pick, "runs": runs, "keep_days": scan_history.keep_days(), "checking": scan_checker.snapshot()}


@app.get("/api/scan-history/{run_id}")
async def scan_history_run(run_id: int):
    """One saved scan with every row as the page got it, and its accuracy check when one was made."""
    run = await asyncio.to_thread(scan_history.get, run_id)
    if run is None:
        raise HTTPException(404, "No such saved scan.")
    run["can_check"] = scan_accuracy.day_closed(run["as_of"])
    run["checking"] = scan_checker.snapshot()
    return run


def _check_job():
    if not scan_worker.enabled():
        return None

    async def job(symbol, start, end, *args):
        return await scan_worker.run(_scan_broker(), scan_worker.check_job, symbol, start, end, *args)

    return job


@app.post("/api/scan-history/pull")
async def scan_history_pull(days: int = Query(14, ge=1, le=scan_sync.MAX_DAYS)):
    """Desk only: copy the backtest server's saved scans of the last `days` days that this desk lacks
    (scan_sync.py: BACKTEST_URL + BACKTEST_LINK_TOKEN). Read-only on both sides."""
    if not server_role.is_desk():
        raise HTTPException(409, "Only the main desk pulls scans from the backtest server.")
    if not server_role.backtest_url():
        raise HTTPException(409, "BACKTEST_URL is not set on this desk, so it cannot reach the backtest server.")
    if not server_role.backtest_link_token():
        raise HTTPException(409, "BACKTEST_LINK_TOKEN is not set on this desk.")
    try:
        return await asyncio.to_thread(scan_sync.pull, scan_sync.http_fetch, days)
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/scan-history/{run_id}/check")
async def scan_history_check(run_id: int):
    """Download that day's candles again and measure what each stock did after the scan. Read-only."""
    _refuse_when_paused()
    if server_role.is_desk() and market_is_open() and not desk_market_hours_free():
        raise HTTPException(409, "During market hours the check runs only on the scanner server, so it never shares the bots' Groww limit.")
    run = await asyncio.to_thread(scan_history.get, run_id)
    if run is None:
        raise HTTPException(404, "No such saved scan.")
    try:
        return scan_checker.start(
            run,
            lambda symbol, start, end: fetch_frame(_scan_broker(), symbol, start, end),
            save=_save_check(run),
            job=_check_job(),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get("/api/stall")
async def stall_status():
    """Has the event loop stalled since boot, and for how long (see stall_tracer.py). Read-only."""
    return {**stall_tracer.snapshot(), "groww": groww_pressure.snapshot()}


@app.get("/api/ticks")
async def ticks(symbol: str, start: int, end: int):
    """Recorded second-by-second prices of one stock, start <= ts < end (epoch seconds).

    Live market hours only: Groww's history has no seconds, so replays and days
    before recording started return an empty list.
    """
    name = _stock_name(symbol)
    if end <= start:
        raise HTTPException(400, "end must be after start")
    rows = await asyncio.to_thread(tick_store.between, name, int(start), int(end))
    return {"symbol": name, "start": int(start), "end": int(end), "ticks": rows}


class TickFeedIn(BaseModel):
    on: bool


def _tick_feed() -> dict:
    return {"on": tick_store.enabled()}


@app.get("/api/ticks/feed")
async def tick_feed():
    """The second-by-second switch: one batched Groww price call a second, and its record."""
    return _tick_feed()


@app.put("/api/ticks/feed")
async def set_tick_feed(body: TickFeedIn):
    """Turn the per-second Groww price fetch on or off for both desks.

    Off: each stock is quoted on the normal few-second interval, nothing is
    recorded, and "Check the fade every second" judges at that pace instead.
    """
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(404, "settings not found")
        row.second_ticks = bool(body.on)
        db.commit()
    tick_store.set_enabled(body.on)
    return _tick_feed()


@app.get("/api/trades")
async def trades():
    ltps = {**research_engine._ltps}
    for eng in bot_engines.values():
        ltps.update(eng._ltps)
    ltps.update(engine._ltps)
    # engine.trades() reads 200 trades off SQLite. Run it off the event loop
    # so the bots' tick keeps up on a cold cache.
    rows = await asyncio.to_thread(engine.trades)
    return attach_market_prices(rows, ltps)


@app.get("/api/trades/book")
async def trades_book(mode: str = "PAPER", limit: int = BOOK_LIMIT, bot: int | None = None):
    """One book's newest trades, up to 20,000, for the blotter. `bot` keeps one SMA bot's PAPER/LIVE trades."""
    book = (mode or "PAPER").upper()
    if book not in BOOKS:
        raise HTTPException(status_code=422, detail=f"mode must be one of {', '.join(BOOKS)}")
    if bot is not None and bot not in (1, *EXTRA_BOTS):
        raise HTTPException(status_code=422, detail=f"bot must be 1 to {EXTRA_BOTS[-1]}")
    # Open research trades are priced and ranged by the research desk.
    src = research_engine if book == "RESEARCH" else bot_engines.get(bot or 1, engine)
    ltps = dict(src._ltps)
    only = bot if book in ("PAPER", "LIVE") else None

    def build() -> tuple[bytes, dict[str, str]]:
        data = src.book(book, limit, only)
        rows, strategies = pack_strategies(attach_market_prices(src.with_open_extremes(data["rows"]), ltps))
        payload = json.dumps({"mode": book, "total": data["total"], "rows": rows, "strategies": strategies}).encode()
        # Compress in this same thread when it is worth it, so the gzip middleware
        # does not do it on the event loop; the middleware skips a response that
        # already carries Content-Encoding.
        if len(payload) >= 16384:
            payload = gzip.compress(payload, compresslevel=5)
            return payload, {"Content-Encoding": "gzip"}
        return payload, {}

    # Thousands of rows take a moment; build them off the event loop so the
    # bot keeps ticking, and skip FastAPI's per-value encoder (plain values only).
    body, headers = await asyncio.to_thread(build)
    return Response(body, media_type="application/json", headers=headers)


@app.get("/api/trades/counts")
async def trades_counts(bot: int | None = None):
    return engine.book_counts(bot)


@app.get("/api/trades.csv")
async def trades_csv(mode: str = ""):
    book = (mode or "").upper()
    data = engine.book(book if book in BOOKS else None)
    rows = attach_market_prices(data["rows"], {**research_engine._ltps, **engine._ltps})
    buffer = io.StringIO()
    fields = [
        "trade_ref",
        "id",
        "date",
        "symbol",
        "direction",
        "qty",
        "entry_time",
        "entry_price",
        "market_price",
        "mark_pnl",
        "ma_cross_price",
        "fill_lag_points",
        "atr_at_entry",
        "sl_trigger_price",
        "entry_reason",
        "trend_at_entry",
        "stop_log",
        "exit_time",
        "exit_price",
        "exit_reason",
        "max_high",
        "max_low",
        "points",
        "gross_pnl",
        "brokerage_and_taxes",
        "net_pnl",
        "mode",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "trend_at_entry": handoff_log.trend_text(row.get("entry_regime"), row.get("direction"))})
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=trade_log.csv"},
    )


@app.websocket("/ws/stream")
async def stream(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            await websocket.send_json(engine.snapshot())
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    except Exception:
        return


# ---- bots 2-4 (bots.py): the same routes as the main desk, under /api/bots/{bot} ----------------


def _bot(bot: int) -> BotEngine:
    eng = bot_engines.get(int(bot))
    if eng is None:
        raise HTTPException(404, f"There is no bot {bot}. Bots 2 to {EXTRA_BOTS[-1]} are here; bot 1 is the main desk.")
    # Its settings row exists before any route reads or writes it.
    ensure_bot_config(eng.bot_id)
    return eng


def _bot_summary(eng: StrategyEngine) -> dict:
    cfg = eng.load_config()
    mode = (cfg.trading_mode or "PAPER").upper()
    kpis = eng._kpis(mode)
    return {
        "bot": eng.bot_id,
        "name": getattr(cfg, "bot_name", None) or f"Bot {eng.bot_id}",
        "mode": mode,
        "status": eng.status,
        "armed": trade_names(cfg),
        "held": sorted(eng.positions),
        "trades_today": eng.trades_today,
        "net_today": float(kpis["net"]),
        "gross_today": float(kpis["actual_gross"]),
    }


# ---- Backtest server link (backtest_server.py) ---------------------------------------------

#: Settings rows copied to the backtest server: bot 1, research, bots 2-4.
BACKTEST_CONFIG_IDS = (1, 2, 3, 4, 5, 6)


def _jsonable(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return value


def export_bot_settings() -> dict:
    """Desk side: every bot's settings row as plain JSON (no secrets live in BotConfig)."""
    rows: dict[str, dict] = {}
    with session_factory()() as db:
        for config_id in BACKTEST_CONFIG_IDS:
            row = db.get(BotConfig, config_id)
            if row is not None:
                rows[str(config_id)] = {c.name: _jsonable(getattr(row, c.name)) for c in BotConfig.__table__.columns}
    from dataclasses import asdict

    # The fast-mover hand-off settings too, so the backtest server's replays play it the same.
    return {"rows": rows, "fast_bot": asdict(fast_bot.load())}


def _column_value(column, value):
    if value is None:
        return None
    try:
        kind = column.type.python_type
    except NotImplementedError:
        return value
    if kind is dt.datetime and isinstance(value, str):
        return dt.datetime.fromisoformat(value)
    if kind is dt.date and isinstance(value, str):
        return dt.date.fromisoformat(value)
    return value


def import_bot_settings(rows: dict, keep: dict[int, list[str]] | None = None) -> list[str]:
    """Backtest server side: write the desk's settings rows here, PAPER forced. Returns the ids copied.

    `keep` (config id -> stocks the scanner armed here, the start-up copy only): each one still armed
    here stays on the Trade list on top of the desk's, with its own source and per-stock settings; the
    ones kept are saved back as the new scanner list (backtest_server.set_scanner_armed)."""
    if not server_role.is_backtest():
        raise RuntimeError("Settings are copied only on the backtest server.")
    columns = {c.name: c for c in BotConfig.__table__.columns}
    done: list[str] = []
    kept: dict[int, list[str]] = {}
    with session_factory()() as db:
        for key, data in rows.items():
            try:
                config_id = int(key)
            except (TypeError, ValueError):
                continue
            if config_id not in BACKTEST_CONFIG_IDS or not isinstance(data, dict):
                continue
            row = db.get(BotConfig, config_id)
            if row is None:
                row = BotConfig(id=config_id)
                db.add(row)
            ours = _scanner_arms(row, (keep or {}).get(config_id, []))
            for name, value in data.items():
                if name == "id" or name not in columns:
                    continue
                setattr(row, name, _column_value(columns[name], value))
            row.trading_mode = "PAPER"
            if ours:
                _keep_scanner_arms(row, ours)
                kept[config_id] = list(ours)
            done.append(str(config_id))
        db.commit()
    if keep is not None:
        backtest_server.set_scanner_armed(kept)
    return done


def _json_dict(raw) -> dict:
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _scanner_arms(row: BotConfig, names: list[str]) -> dict[str, tuple[str | None, dict | None]]:
    """The scanner-armed stocks still on this row's Trade list: symbol -> (source, per-stock settings)."""
    if not names or row.id is None:
        return {}
    armed = trade_names(row)
    sources = trade_sources(row)
    stocks = _json_dict(row.stock_settings)
    return {s: (sources.get(s), stocks.get(s)) for s in names if s in armed}


def _keep_scanner_arms(row: BotConfig, ours: dict[str, tuple[str | None, dict | None]]) -> None:
    names = trade_names(row)
    for sym in ours:
        if sym not in names:
            names.append(sym)
    cap = MAX_RESEARCH_SYMBOLS if row.id == research_engine.config_id else MAX_TRADE_SYMBOLS
    row.trade_symbols = ",".join(names[:cap])
    sources = _json_dict(row.trade_sources)
    stocks = _json_dict(row.stock_settings)
    for sym, (source, settings) in ours.items():
        if source:
            sources[sym] = source
        if isinstance(settings, dict):
            stocks[sym] = settings
    row.trade_sources = json.dumps(sources)
    row.stock_settings = json.dumps(stocks)


def link_config_id(target: int | str) -> int:
    """The settings row a link target (bot 1-4 or "research") is saved in."""
    if target == "research":
        return research_engine.config_id
    return _bot_engine_for(int(target)).config_id


def armed_lists() -> dict:
    """Each bot's Trade list and the research desk's, for the scanner server's link (read-only)."""

    def one(eng: StrategyEngine) -> dict:
        cfg = eng.load_config()
        return {
            "bot": eng.bot_id,
            "name": getattr(cfg, "bot_name", None) or f"Bot {eng.bot_id}",
            "mode": (cfg.trading_mode or "PAPER").upper(),
            "armed": trade_names(cfg),
        }

    return {
        "bots": [one(engine), *(one(eng) for eng in bot_engines.values())],
        "research": trade_names(research_engine.load_config()),
    }


def news_prices(symbols: list[str]) -> dict[str, float]:
    """The SMA bots' own last prices for these stocks, for the desk's news page and alerts (read-only)."""
    wanted = {(s or "").upper() for s in symbols}
    out: dict[str, float] = {}
    for eng in [engine, *bot_engines.values(), research_engine]:
        for sym, ltp in list(getattr(eng, "_ltps", {}).items()):
            if sym in wanted and sym not in out and ltp:
                out[sym] = float(ltp)
    return out


NEWS_CANDLE_DAYS = 6  # calendar days back, so the previous session's close is in the download


def _day_rows(frame, day: dt.date) -> tuple[list[list[float]], float | None]:
    """[[ts, open, high, low, close], ...] of `day`'s session and the close of the session before it."""
    if frame is None or getattr(frame, "empty", True):
        return [], None
    start = int(dt.datetime.combine(day, dt.time(0, 0), tzinfo=IST).timestamp())
    stop = start + 86400
    ts = frame["ts"].astype("int64")
    today = frame[(ts >= start) & (ts < stop)]
    before = frame[ts < start]
    prev = float(before["close"].iloc[-1]) if not before.empty else None
    rows = [[int(r.ts), float(r.open), float(r.high), float(r.low), float(r.close)] for r in today.itertuples()]
    return rows, prev


@app.get("/api/tsl-calc")
async def tsl_calc(
    symbol: str,
    sizes: str = "1,3,5,15",
    atr_period: int = 14,
    stop_mult: float = tsl_calc_mod.DEFAULT_STOP,
    trail_mult: float = tsl_calc_mod.DEFAULT_TRAIL,
    target_mult: float = tsl_calc_mod.DEFAULT_TARGET,
):
    """Trailing stop / trail / target sized from the stock's own candles, per candle size (tsl_calc.py).

    Today's armed stocks are read from the bots' 1-minute tapes (no Groww call); any other stock is downloaded
    once on the scans' session. Read-only: nothing is saved and no order is placed."""
    name = _chart_symbol(symbol)
    if name is None:
        raise HTTPException(400, "Give a stock symbol.")
    picked = []
    for part in str(sizes).split(","):
        try:
            n = int(part)
        except ValueError:
            continue
        if n in tsl_calc_mod.SIZES and n not in picked:
            picked.append(n)
    if not picked:
        picked = [1, 3, 5, 15]
    if not 2 <= int(atr_period) <= 100:
        raise HTTPException(400, "atr_period must be 2 to 100.")
    for value in (stop_mult, trail_mult, target_mult):
        if not 0 <= float(value) <= 20:
            raise HTTPException(400, "Multiples must be 0 to 20.")
    tape = _tape_for(name)
    source = "bot tape"
    if tape is None:
        now = dt.datetime.now(IST)
        start = dt.datetime.combine(now.date() - dt.timedelta(days=7), dt.time(9, 15), tzinfo=IST)
        try:
            await groww_pressure.wait_for_bots()
            tape = await fetch_frame(_scan_broker(), name, start, now)
        except HistoryError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(502, f"Could not download {name}'s candles: {exc}") from exc
        source = "groww"
    out = await asyncio.to_thread(
        tsl_calc_mod.suggest, name, tape, picked, int(atr_period), float(stop_mult), float(trail_mult), float(target_mult)
    )
    out["source"] = source
    return out


class RegimeStudyStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=regime_study.MAX_STOCKS)
    days: int = Field(default=10, ge=regime_study.MIN_DAYS, le=regime_study.MAX_DAYS)
    minutes: int = 5
    window: int = 60
    sma_fast: int | None = Field(default=None, ge=2, le=200)
    sma_slow: int | None = Field(default=None, ge=3, le=400)


@app.get("/api/regime-study")
async def regime_study_get():
    """The Sideways test (regime_study.py): progress and the last report. Read-only."""
    return regime_study.study().view()


@app.post("/api/regime-study/start")
async def regime_study_start(body: RegimeStudyStart):
    """Start the Sideways test: past days of each stock's Groww 1-minute candles, read-only, no order.

    On the main desk in market hours it is refused unless the desk runs backtests at any hour (the bots' Groww limit);
    the backtest server runs it any time on its own login."""
    _refuse_when_paused()
    if server_role.is_desk() and market_is_open() and not desk_market_hours_free():
        raise HTTPException(409, "During market hours run the Sideways test on the backtest server, so it never shares the bots' Groww limit.")
    if body.minutes not in regime_study.SIZES:
        raise HTTPException(400, f"Candle size must be one of {', '.join(map(str, regime_study.SIZES))} minutes.")
    if body.window not in regime_study.WINDOWS:
        raise HTTPException(400, f"Window must be one of {', '.join(map(str, regime_study.WINDOWS))} minutes.")
    cfg = engine.load_config()
    fast = int(body.sma_fast or cfg.sma_fast)
    slow = int(body.sma_slow or cfg.sma_slow)
    if fast >= slow:
        raise HTTPException(400, "Fast SMA period must be shorter than the slow period.")

    async def fetch(name, start, end):
        return await fetch_frame(_scan_broker(), name, start, end)

    try:
        return regime_study.study().start(
            body.symbols, fetch, body.days, body.minutes, body.window, fast, slow, wait=groww_pressure.wait_for_bots
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/regime-study/stop")
async def regime_study_stop():
    """Stop after the stock being read; the report covers the stocks read so far."""
    return regime_study.study().stop()


# ---------------------------------------------------------------------------
# Strategy lab (strategy_lab.py): which entry checks and exits make the strategy money, in seconds
# ---------------------------------------------------------------------------


class StrategyLabStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=strategy_lab.MAX_STOCKS)
    date: str
    end_date: str | None = None
    bot: int = Field(default=1, ge=1, le=4)
    label: str = ""
    # The candle to read every stock on (0 / None = the bot's own).
    minutes: Literal[0, 1, 2, 3, 5, 10, 15] | None = None


class StrategyLabChoice(BaseModel):
    entries: list[str] = Field(default_factory=list)
    exits: list[str] = Field(default_factory=list)
    stop: str = "ATR"
    targets: list[str] = Field(default_factory=list)
    entry_from: str = "09:20"
    entry_to: str = "15:30"
    qty: int = Field(default=0, ge=0, le=1_000_000)


def _lab_job_or_404(job_id: str) -> tuple[dict, list[dict]]:
    job = strategy_lab.lab().job(job_id)
    if job is None:
        raise HTTPException(404, "No such Strategy lab run.")
    rows = strategy_lab.lab().trades_of(job_id)
    if rows is None:
        raise HTTPException(409, "That run has no trades yet (still running, or its file is gone).")
    return job, rows


@app.get("/api/strategy-lab")
async def strategy_lab_get():
    """The Strategy lab (strategy_lab.py): the run going now and the saved runs. Read-only."""
    return {**strategy_lab.lab().view(), "rules": _lab_rules()}


def _lab_rules() -> dict:
    return {
        "entries": [{"name": k, "label": v} for k, v in strategy_lab.ENTRY_CHECKS.items()],
        "exits": [{"name": k, "label": v} for k, v in strategy_lab.EXIT_RULES.items()],
        "stops": [{"name": k, "label": strategy_lab.STOP_LABELS[k]} for k in strategy_lab.STOPS],
        "targets": [{"name": k, "label": v} for k, v in strategy_lab.TARGETS.items()],
    }


@app.post("/api/strategy-lab/start")
async def strategy_lab_start(body: StrategyLabStart):
    """Read the picked stocks' past days once (cached candles, read-only, no order) into a table of base trades.

    On the main desk in market hours it is refused unless the desk runs backtests at any hour (the bots' Groww
    limit); the backtest server runs it any time on its own login."""
    _refuse_when_paused()
    if server_role.is_desk() and market_is_open() and not desk_market_hours_free():
        raise HTTPException(409, "During market hours run the Strategy lab on the backtest server, so it never shares the bots' Groww limit.")
    try:
        first = dt.date.fromisoformat(body.date)
        last = dt.date.fromisoformat(body.end_date) if body.end_date else first
    except ValueError as exc:
        raise HTTPException(400, "Dates must be YYYY-MM-DD.") from exc
    if last >= dt.datetime.now(IST).date():
        raise HTTPException(400, "The lab reads finished days only: pick an end date before today.")
    bot_engine = _bots_by_number().get(body.bot)
    if bot_engine is None:
        raise HTTPException(400, "Bot must be 1-4.")
    cfg = bot_engine.load_config()

    def cfg_for(symbol: str):
        return _cfg_for(cfg, symbol)

    async def fetch(name, start, end):
        return await fetch_frame(_scan_broker(), name, start, end)

    try:
        job = strategy_lab.lab().start(
            body.symbols, first, last, body.bot, cfg_for, fetch, wait=groww_pressure.wait_for_bots, label=body.label, minutes=body.minutes or None
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {**strategy_lab.lab().view(), "job": job, "rules": _lab_rules()}


@app.post("/api/strategy-lab/stop")
async def strategy_lab_stop():
    """Stop after the stock being read; the table keeps the stocks read so far."""
    return strategy_lab.lab().stop()


@app.get("/api/strategy-lab/{job_id}")
async def strategy_lab_job(job_id: str):
    """One run: its settings, the bot's own switches as the starting choice, and that choice evaluated."""
    job, rows = _lab_job_or_404(job_id)
    choice = job.get("choice") or {}
    result = await asyncio.to_thread(strategy_lab.evaluate, rows, choice, job.get("settings"))
    return {"job": job, "result": result, "rules": _lab_rules()}


@app.post("/api/strategy-lab/{job_id}/evaluate")
async def strategy_lab_evaluate(job_id: str, body: StrategyLabChoice):
    """Apply a choice of checks / exits / stop to the run's base trades: P&L, per stock, per day, by reason, trades."""
    job, rows = _lab_job_or_404(job_id)
    return await asyncio.to_thread(strategy_lab.evaluate, rows, body.model_dump(), job.get("settings"))


@app.post("/api/strategy-lab/{job_id}/recommend")
async def strategy_lab_recommend(job_id: str, body: StrategyLabChoice):
    """What each rule adds on top of this choice, and the best combination a greedy search finds."""
    job, rows = _lab_job_or_404(job_id)
    return await asyncio.to_thread(strategy_lab.recommend, rows, body.model_dump(), job.get("settings"))


@app.delete("/api/strategy-lab/{job_id}")
async def strategy_lab_delete(job_id: str):
    if strategy_lab.lab().running and (strategy_lab.lab().current or {}).get("id") == job_id:
        raise HTTPException(409, "That run is still going. Stop it first.")
    if not strategy_lab.lab().delete(job_id):
        raise HTTPException(404, "No such Strategy lab run.")
    return strategy_lab.lab().view()


class MorningStudyStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=morning_study.MAX_STOCKS)
    days: int = Field(default=20, ge=morning_study.MIN_DAYS, le=morning_study.MAX_DAYS)
    at: Literal["10:00", "10:30", "11:00", "12:00"] = "10:30"
    minutes: Literal[1, 3, 5, 10, 15] = 5
    min_move: float = Field(default=morning_study.DEFAULT_MIN_MOVE, ge=0.1, le=5)
    delivery: bool = True


@app.get("/api/morning-study")
async def morning_study_get():
    """The Morning test (morning_study.py): progress and the last report. Read-only."""
    return morning_study.study().view()


@app.post("/api/morning-study/start")
async def morning_study_start(body: MorningStudyStart):
    """Start the Morning test: past days of each stock's Groww 1-minute candles (and NSE's delivery file), read-only.

    Same hours rule as the Sideways test: on the main desk in market hours only when the desk runs backtests at any
    hour; the backtest server runs it any time on its own login."""
    _refuse_when_paused()
    if server_role.is_desk() and market_is_open() and not desk_market_hours_free():
        raise HTTPException(409, "During market hours run the Morning test on the backtest server, so it never shares the bots' Groww limit.")
    cfg = engine.load_config()

    async def fetch(name, start, end):
        return await fetch_frame(_scan_broker(), name, start, end)

    try:
        return morning_study.study().start(
            body.symbols, fetch, body.days, body.at, body.minutes, int(cfg.sma_fast), int(cfg.sma_slow),
            min_move=body.min_move, with_delivery=body.delivery, wait=groww_pressure.wait_for_bots,
        )
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/morning-study/stop")
async def morning_study_stop():
    """Stop after the stock being read; the report covers the stocks read so far."""
    return morning_study.study().stop()


class SecondCandleStart(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=second_candle.MAX_STOCKS)
    days: int = Field(default=10, ge=second_candle.MIN_DAYS, le=second_candle.MAX_DAYS)
    minutes: Literal[1, 3, 5, 10, 15] = 5
    candle_no: int = Field(default=2, ge=1, le=second_candle.MAX_CANDLE_NO)
    #: Several candles in one run (each its own trade); empty = just candle_no.
    candle_nos: list[int] = Field(default_factory=list, max_length=second_candle.MAX_CANDLE_NO)
    top_n: int = Field(default=20, ge=0, le=250)
    target_points: float = Field(default=10.0, ge=0.05, le=500)
    square_off: str = "15:15"
    trails: list[float] = Field(default_factory=lambda: list(second_candle.DEFAULT_TRAILS), max_length=second_candle.MAX_TRAILS)
    min_body_pct: float = Field(default=0.0, ge=0, le=5)
    #: NTH = the Nth candle's colour, MAJORITY = most of candles 1..N, AGREE = the Nth candle only when most agree.
    side_rule: Literal["NTH", "MAJORITY", "AGREE"] = "NTH"
    majority_by: Literal["COUNT", "SIZE"] = "COUNT"


@app.get("/api/second-candle")
async def second_candle_get(rows: bool = True):
    """The Second candle test (second_candle.py): progress, the last report and its rows. Read-only."""
    return second_candle.test().view(with_rows=rows)


@app.post("/api/second-candle/start")
async def second_candle_start(body: SecondCandleStart):
    """Start the Second candle test on past days of the picked stocks (cached Groww 1-minute candles, read-only, no order).

    Same hours rule as the Sideways test: on the main desk in market hours only when the desk runs backtests at any
    hour; the backtest server runs it any time on its own login."""
    _refuse_when_paused()
    if server_role.is_desk() and market_is_open() and not desk_market_hours_free():
        raise HTTPException(409, "During market hours run the Second candle test on the backtest server, so it never shares the bots' Groww limit.")
    try:
        hh, mm = body.square_off.split(":")
        square_off = dt.time(int(hh), int(mm))
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Square-off must be HH:MM.") from exc
    if not dt.time(9, 20) <= square_off <= dt.time(15, 30):
        raise HTTPException(400, "Square-off must be between 09:20 and 15:30.")
    rule = second_candle.Rule(
        minutes=body.minutes, candle_no=body.candle_no,
        candle_nos=tuple(n for n in body.candle_nos if 1 <= n <= second_candle.MAX_CANDLE_NO), top_n=body.top_n,
        target_points=body.target_points,
        square_off=square_off, trails=tuple(body.trails), min_body_pct=body.min_body_pct,
        side_rule=body.side_rule, majority_by=body.majority_by,
    )

    async def fetch(name, start, end):
        return await fetch_frame(_scan_broker(), name, start, end)

    try:
        return second_candle.test().start(body.symbols, fetch, body.days, rule, wait=groww_pressure.wait_for_bots)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/second-candle/stop")
async def second_candle_stop():
    """Stop after the stock being read; the report covers the stocks read so far."""
    return second_candle.test().stop()


def _tape_for(symbol: str):
    for eng in [engine, *bot_engines.values(), research_engine]:
        tape = getattr(eng, "_tapes", {}).get(symbol)
        if tape is not None and not getattr(tape, "empty", True):
            return tape
    return None


async def news_candles(symbols: list[str], day: dt.date) -> dict[str, dict]:
    """1-minute candles of `day` for the desk's news check (app/services/news_validation.py), read-only.

    symbol -> {"rows": [[ts, o, h, l, c], ...], "prev_close": x} or {"error": text}. Today's armed stocks come
    from the bots' own 1-minute tapes (no Groww call); the rest are downloaded one at a time, after the bots
    (groww_pressure.wait_for_bots), on the scans' Groww session."""
    now = dt.datetime.now(IST)
    start = dt.datetime.combine(day - dt.timedelta(days=NEWS_CANDLE_DAYS), dt.time(9, 15), tzinfo=IST)
    end = min(dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST), now)
    out: dict[str, dict] = {}
    for symbol in dict.fromkeys(s.upper() for s in symbols if s):
        frame = _tape_for(symbol) if day == now.date() else None
        rows, prev = _day_rows(frame, day)
        if not rows or prev is None:
            try:
                await groww_pressure.wait_for_bots()
                frame = await fetch_frame(_scan_broker(), symbol, start, end)
                await asyncio.sleep(scan_worker.MIN_GAP_SEC)
            except Exception as exc:  # noqa: BLE001 - one stock's failure stays on its row
                out[symbol] = {"error": str(exc)[:200]}
                continue
            rows, prev = _day_rows(frame, day)
        out[symbol] = {"rows": rows, "prev_close": prev} if rows else {"error": "No candles for that day."}
    return out


def news_watch_list() -> dict:
    """Armed and held stocks of bots 1-5, for the desk's news alerts (read-only).

    symbol -> {"armed": [bot names], "held": [{bot, name, mode, side, qty}]}, held stocks first.
    """
    out: dict[str, dict] = {}
    for eng in [engine, *bot_engines.values()]:
        cfg = eng.load_config()
        name = getattr(cfg, "bot_name", None) or f"Bot {eng.bot_id}"
        mode = (cfg.trading_mode or "PAPER").upper()
        for sym in trade_names(cfg):
            out.setdefault(sym, {"armed": [], "held": []})["armed"].append(name)
        for sym, pos in list(eng.positions.items()):
            if pos is None:
                continue
            out.setdefault(sym, {"armed": [], "held": []})["held"].append(
                {"bot": eng.bot_id, "name": name, "mode": pos.mode or mode, "side": pos.direction, "qty": pos.qty}
            )
    return dict(sorted(out.items(), key=lambda kv: (not kv[1]["held"], kv[0])))


class WalletMoney(BaseModel):
    amount: float = Field(gt=0, le=paper_wallet.MAX_LOAD)


class WalletRepay(BaseModel):
    #: None repays as much of the loan as the free balance allows.
    amount: float | None = Field(default=None, gt=0)


class WalletMargin(BaseModel):
    margin_pct: float = Field(ge=1, le=100)


def _wallet_call(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/wallet")
async def wallet():
    """The practice wallet the PAPER bots trade from: loaded, loan, blocked margin, free balance."""
    return paper_wallet.summary()


@app.post("/api/wallet/add")
async def wallet_add(body: WalletMoney):
    return _wallet_call(paper_wallet.add, body.amount)


@app.post("/api/wallet/withdraw")
async def wallet_withdraw(body: WalletMoney):
    return _wallet_call(paper_wallet.withdraw, body.amount)


@app.post("/api/wallet/repay")
async def wallet_repay(body: WalletRepay):
    return _wallet_call(paper_wallet.repay, body.amount)


@app.post("/api/wallet/reset")
async def wallet_reset():
    return paper_wallet.reset()


@app.get("/api/wallet/statement")
async def wallet_statement(limit: int = 500):
    """Every wallet line, newest first: adds, withdrawals, loans, repayments, closes, margin changes."""
    return paper_wallet.statement(limit)


@app.get("/api/wallet/loans")
async def wallet_loans(limit: int = 500):
    """Every loan record, newest first, with what is repaid and what is still due."""
    return paper_wallet.loans(limit)


@app.put("/api/wallet/margin")
async def wallet_margin(body: WalletMargin):
    return _wallet_call(paper_wallet.set_margin, body.margin_pct)


@app.get("/api/bots")
async def list_bots():
    """Every SMA bot: name, mode, status, armed and held stocks, today's net."""
    return [_bot_summary(engine), *(_bot_summary(eng) for eng in bot_engines.values())]


class AllBotsGuard(BaseModel):
    target: float = Field(ge=0, le=all_bots_guard.MAX_AMOUNT)
    stop: float = Field(ge=0, le=all_bots_guard.MAX_AMOUNT)
    # "HH:MM" IST between 09:15 and 15:30; empty = off. Left out = unchanged.
    squareoff: str | None = None


async def _guard_view() -> dict:
    bots = _all_bots()
    cfg = engine.load_config()
    target, stop = all_bots_guard.limits(cfg)
    at = all_bots_guard.squareoff_at(cfg)
    closed = await asyncio.to_thread(all_bots_guard.closed_total, bots)
    amount = round(closed + all_bots_guard.open_total(bots), 2)
    halted = [eng.bot_id for eng in bots if all_bots_guard.is_guard_halt(eng)]
    return {
        "target": target,
        "stop": stop,
        "squareoff": at.strftime("%H:%M") if at else "",
        "total": amount,
        "hit": all_bots_guard.verdict(amount, target, stop, engine._now(), at),
        "halted_bots": halted,
        "reason": next((eng.halt_reason for eng in bots if all_bots_guard.is_guard_halt(eng)), ""),
    }


@app.get("/api/bots/guard")
async def all_bots_guard_get():
    """The all-bots day target / stop loss and today's combined P&L (before charges)."""
    return await _guard_view()


@app.put("/api/bots/guard")
async def all_bots_guard_put(body: AllBotsGuard):
    """Save the all-bots day target / stop loss (₹, 0 = off) on bot 1's settings row."""
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        row.all_bots_target = float(body.target)
        row.all_bots_stop = float(body.stop)
        if body.squareoff is not None:
            text = body.squareoff.strip()
            if text and all_bots_guard.parse_time(text) is None:
                raise HTTPException(422, "Square-off time must be HH:MM between 09:15 and 15:30, or empty for off.")
            row.all_bots_squareoff = text
        db.commit()
    engine.load_config()
    return await _guard_view()


class PnlAlertSettings(BaseModel):
    profit: float = Field(ge=0, le=pnl_alerts.MAX_AMOUNT)
    loss: float = Field(ge=0, le=pnl_alerts.MAX_AMOUNT)
    telegram: bool = True
    call: bool = True
    basis: Literal["GROSS", "NET"] = "GROSS"


async def _pnl_alert_view() -> dict:
    bots = _all_bots()
    conf = pnl_alerts.settings(engine.load_config())
    net = conf["basis"] == "NET"
    closed = await asyncio.to_thread(all_bots_guard.closed_total, bots, net)
    now = engine._now()
    return {
        **conf,
        "total": round(closed + all_bots_guard.open_total(bots, net), 2),
        "sent": pnl_alerts.alerts.sent_today(now.date()),
    }


@app.get("/api/bots/pnl-alerts")
async def pnl_alerts_get():
    """The all-bots P&L alert levels, today's total on their basis and the levels already alerted today."""
    return await _pnl_alert_view()


@app.put("/api/bots/pnl-alerts")
async def pnl_alerts_put(body: PnlAlertSettings):
    """Save the all-bots P&L alert levels (₹, 0 = off) on bot 1's settings row. Alerts only, no trading."""
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        row.pnl_alert_profit = float(body.profit)
        row.pnl_alert_loss = float(body.loss)
        row.pnl_alert_telegram = bool(body.telegram)
        row.pnl_alert_call = bool(body.call)
        row.pnl_alert_basis = body.basis
        db.commit()
    engine.load_config()
    return await _pnl_alert_view()


@app.post("/api/bots/pnl-alerts/test")
async def pnl_alerts_test():
    """Send a test message and make a test call (any time) through the alert channels."""
    from app.services.alert_notifier import alert_notifier

    conf = pnl_alerts.settings(engine.load_config())
    text = "PalTra P&L alert test: this is how a profit / loss level alert will reach you."
    out = {}
    if conf["telegram"]:
        res = await alert_notifier.send(text)
        out["telegram"] = "sent" if res.ok else (res.skipped_reason or res.error or "not sent")
    if conf["call"]:
        res = await alert_notifier.place_call(text, test=True)
        out["call"] = "ringing" if res.ok else (res.skipped_reason or res.error or "not made")
    if not out:
        raise HTTPException(409, "Tick Telegram or Phone call first.")
    return out


class NewsEntrySettings(BaseModel):
    bot: int = Field(ge=0, le=5)  # 0 = off
    impact: Literal["LOW", "MEDIUM", "HIGH"] = "HIGH"
    scope: Literal["FNO", "ARMED"] = "FNO"
    max_age_min: int = Field(default=15, ge=1, le=news_entry.MAX_AGE_MIN)
    max_day: int = Field(default=5, ge=1, le=news_entry.MAX_DAY)
    confirm_live: bool = False


def _news_entry_view() -> dict:
    conf = news_entry.settings(engine.load_config())
    bot = conf["bot"]
    mode = None
    if bot:
        mode = (_bot_engine_for(bot).load_config().trading_mode or "PAPER").upper()
    return {**conf, "mode": mode, "recent": news_entry.recent[:20]}


@app.get("/api/bots/news-entry")
async def news_entry_get():
    """News entries: which bot enters at once on fresh news, how strong, and the latest decisions."""
    return _news_entry_view()


@app.put("/api/bots/news-entry")
async def news_entry_put(body: NewsEntrySettings):
    """Save the news-entry settings on bot 1's row. A LIVE bot needs `confirm_live`. No order is placed here."""
    if body.bot:
        if body.bot in EXTRA_BOTS:
            ensure_bot_config(body.bot)
        live = (_bot_engine_for(body.bot).load_config().trading_mode or "PAPER").upper() == "LIVE"
        if live and not body.confirm_live:
            raise HTTPException(409, f"Bot {body.bot} is LIVE: news entries would place real Groww orders. Confirm first.")
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        row.news_entry_bot = int(body.bot)
        row.news_entry_impact = body.impact
        row.news_entry_scope = body.scope
        row.news_entry_max_age_min = int(body.max_age_min)
        row.news_entry_max_day = int(body.max_day)
        db.commit()
    engine.load_config()
    return _news_entry_view()


def news_entry_active() -> bool:
    """True while a bot takes news entries (the desk's news watcher then reads NSE every minute)."""
    return bool(news_entry.settings(engine.load_config())["bot"])


async def news_entry_consider(symbol: str, item: dict, now) -> str | None:
    """A fresh scored headline from the desk's news watcher: enter on it if the settings allow."""
    conf = news_entry.settings(engine.load_config())
    if not conf["bot"]:
        return None
    eng = _bot_engine_for(conf["bot"]) if conf["bot"] == 1 or conf["bot"] in bot_engines else None
    return await news_entry.consider(symbol, item, engine.load_config(), eng, now)


class FastBotSettings(BaseModel):
    on: bool | None = None
    rule: Literal["MOVE", "SPEED", "EITHER"] | None = None
    pct: float | None = Field(None, ge=0.05, le=fast_bot.MAX_PCT)
    minutes: int | None = Field(None, ge=1, le=fast_bot.MAX_MINUTES)
    speed: float | None = Field(None, ge=0.01, le=fast_bot.MAX_SPEED)
    speed_minutes: int | None = Field(None, ge=1, le=fast_bot.MAX_MINUTES)
    sources: list[Literal[1, 2, 3, 4]] | None = None
    max_day: int | None = Field(None, ge=1, le=100)
    min_profit_pct: float | None = Field(None, ge=0, le=fast_bot.MAX_PCT)
    bot5_trades: int | None = Field(None, ge=0, le=fast_bot.MAX_BOT5_TRADES)
    # Bot 5's own entry checks (VWAP, volume, density, RSI, ADX, EMA, …) are read before the hand-off.
    entry_checks: bool | None = None
    # Keep the source trade and let Bot 5 open its own order next to it (the stock is armed on both bots).
    keep_trade: bool | None = None
    # Practice bots watch, a LIVE Bot 5 trades: a PAPER source trade may hand off to a LIVE Bot 5.
    paper_to_live: bool | None = None
    flip: bool | None = None
    confirm_live: bool = False


def _fast_bot_view() -> dict:
    from dataclasses import asdict

    target = bot_engines.get(fast_bot.TARGET_BOT)
    cfg = target.load_config() if target is not None else None
    return {
        **asdict(fast_bot.load()),
        "target_bot": fast_bot.TARGET_BOT,
        "target_mode": (getattr(cfg, "trading_mode", None) or "PAPER").upper(),
        "target_status": getattr(target, "status", None),
        "target_qty": int(getattr(cfg, "qty", 0) or 0),
        **fast_bot.watcher.snapshot(),
    }


@app.get("/api/bots/fast-mover")
async def fast_bot_get():
    """Fast-mover hand-off to Bot 5: settings, Bot 5's mode and today's hand-offs (fast_bot.py)."""
    return _fast_bot_view()


@app.put("/api/bots/fast-mover")
async def fast_bot_put(body: FastBotSettings):
    """Save the fast-mover hand-off settings. Turning it on while Bot 5 is LIVE needs `confirm_live`.

    No order is placed here; hand-offs happen in the watcher while NSE is open."""
    if body.on or body.paper_to_live:
        ensure_bot_config(fast_bot.TARGET_BOT)
        target = bot_engines[fast_bot.TARGET_BOT].load_config()
        if (target.trading_mode or "PAPER").upper() == "LIVE" and not body.confirm_live:
            raise HTTPException(
                409,
                f"Bot {fast_bot.TARGET_BOT} is LIVE: fast movers would place real Groww orders at its size. Confirm first.",
            )
    fast_bot.save(body.model_dump(exclude={"confirm_live"}, exclude_none=True))
    return _fast_bot_view()


@app.post("/api/bots/kill-all")
async def kill_all_bots():
    """Panic on every SMA bot at once (main desk and bots 2-4). The research desk is practice only."""
    bots = (engine, *bot_engines.values())
    # Every bot squares off at the same time (each kill is its own chain of Groww calls), not one after another.
    await asyncio.gather(*(eng.kill("Manual PANIC SQUARE-OFF (all bots)") for eng in bots), return_exceptions=True)
    return [{"bot": eng.bot_id, "bot_status": eng.status} for eng in bots]


@app.get("/api/bots/{bot}/state")
async def bot_state(bot: int):
    return await _state(_bot(bot))


@app.get("/api/bots/{bot}/chart")
async def bot_chart(bot: int, limit: int = 240, symbol: str | None = None, interval: int | None = None):
    return await _chart(_bot(bot), limit, symbol, interval)


@app.get("/api/bots/{bot}/history")
async def bot_history(bot: int, symbol: str, start: str, end: str, interval: int = 1, run_id: int | None = None):
    return await _history(_bot(bot), symbol, start, end, interval, run_id)


@app.get("/api/bots/{bot}/config")
async def bot_get_config(bot: int):
    return await _get_config(_bot(bot))


@app.put("/api/bots/{bot}/config")
async def bot_put_config(bot: int, body: ConfigUpdate):
    return await _put_config(_bot(bot), body)


@app.post("/api/bots/{bot}/config/reset")
async def bot_reset_config(bot: int, body: ResetBody):
    return await _reset_config(_bot(bot), body)


@app.get("/api/bots/{bot}/config/stock/{symbol}")
async def bot_get_stock_config(bot: int, symbol: str):
    return await _get_stock_config(_bot(bot), symbol)


@app.put("/api/bots/{bot}/config/stock/{symbol}")
async def bot_put_stock_config(bot: int, symbol: str, body: StockConfigUpdate):
    return await _put_stock_config(_bot(bot), symbol, body)


@app.delete("/api/bots/{bot}/config/stock/{symbol}")
async def bot_reset_stock_config(bot: int, symbol: str):
    return await _reset_stock_config(_bot(bot), symbol)


@app.post("/api/bots/{bot}/trade-symbols")
async def bot_set_trade_symbol(bot: int, body: TradeSymbolUpdate):
    return await _set_trade_symbol(_bot(bot), body)


@app.post("/api/bots/{bot}/mode")
async def bot_set_mode(bot: int, body: ModeUpdate):
    """PAPER is the default. LIVE needs confirm_live, a Groww session and stocks no other LIVE bot owns."""
    return await _set_mode(_bot(bot), body)


@app.post("/api/bots/{bot}/bot/start")
async def bot_start(bot: int):
    return await _start_bot(_bot(bot))


@app.post("/api/bots/{bot}/bot/pause")
async def bot_pause(bot: int):
    return await _pause_bot(_bot(bot))


@app.post("/api/bots/{bot}/bot/force")
async def bot_force(bot: int, body: ForceOrder):
    return await _force_order(_bot(bot), body)


@app.post("/api/bots/{bot}/bot/review/{review_id}/{action}")
async def bot_answer_review(bot: int, review_id: int, action: str):
    return await _answer_review(_bot(bot), review_id, action)


@app.post("/api/bots/{bot}/bot/close")
async def bot_close(bot: int, body: CloseOrder):
    return await _close_position(_bot(bot), body)


@app.post("/api/bots/{bot}/bot/reset-trades")
async def bot_reset_trades(bot: int):
    return await _reset_trades(_bot(bot))


@app.post("/api/bots/{bot}/bot/kill")
async def bot_kill(bot: int):
    return await _kill_bot(_bot(bot))
