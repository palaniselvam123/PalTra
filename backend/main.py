"""SMA(9, 21) + 1.5× ATR intraday terminal API.

Run from `backend/`:

    uvicorn main:app --port 8001

WebSocket `/ws/stream` pushes a 1-second snapshot (LTP, SMAs, ATR, position,
stop, P&L, bot status). REST lives under `/api`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import csv
import io
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from candle_history import HistoryError, load_history
from replay import (
    SPEEDS,
    ReplaySession,
    close_orphan_replay_rows,
    delete_run,
    get_run,
    list_runs,
    parse_replay_range,
    parse_start,
    settings_snapshot,
)
import gap_mode
from groww_client import preferred_quote_token
from scalp_picks import MAX_UNIVERSE, PickRule
from database import init_db, session_factory
from models import BotConfig
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
    trade_names,
)

engine = StrategyEngine()
replay = ReplaySession()


_booted = False
_task: asyncio.Task | None = None


def boot_engine() -> asyncio.Task | None:
    """Start the 1-second loop once. Safe if both the standalone app and a mount call it."""
    global _booted, _task
    if _booted:
        return _task
    _booted = True
    init_db()
    close_orphan_replay_rows()
    cfg = engine.load_config()
    engine.restore_open_books()
    engine.restore_trades_today()
    engine.broker.set_mode(cfg.trading_mode)
    engine.broker.adopt_saved_session(force=True)
    _task = asyncio.create_task(engine.run())
    return _task


def stop_engine() -> None:
    engine.stop()
    if _task is not None and not _task.done():
        _task.cancel()


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
app.add_middleware(GZipMiddleware, minimum_size=2048)
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
    sma_slow: int | None = Field(default=None, ge=3, le=300)
    atr_period: int | None = Field(default=None, ge=2, le=100)
    atr_multiplier: float | None = Field(default=None, gt=0, le=10)
    use_adx_filter: bool | None = None
    use_stop: bool | None = None
    stop_type: Literal["ATR", "SMA_GAP", "TSL"] | None = None
    tsl_sl_points: float | None = Field(default=None, gt=0, le=100000)
    tsl_trail_points: float | None = Field(default=None, gt=0, le=100000)
    tsl_target_points: float | None = Field(default=None, ge=0, le=100000)
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
    gap_entry_long: float | None = Field(default=None, ge=-10, le=10)
    gap_exit_long: float | None = Field(default=None, ge=-10, le=10)
    gap_entry_short: float | None = Field(default=None, ge=-10, le=10)
    gap_exit_short: float | None = Field(default=None, ge=-10, le=10)
    gap_giveback_pct: float | None = Field(default=None, ge=0, le=100)
    gap_entry_delay_min: int | None = Field(default=None, ge=0, le=120)
    gap_entry_window_min: int | None = Field(default=None, ge=0, le=375)
    max_daily_loss: float | None = Field(default=None, gt=0)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=100)
    square_off_time: str | None = None
    entry_cutoff_time: str | None = None


class ModeUpdate(BaseModel):
    mode: str
    confirm_live: bool = False


def _config_dict(row: BotConfig) -> dict:
    return {
        "symbol": row.symbol,
        "trade_symbols": trade_names(row),
        "exchange": row.exchange,
        "qty": row.qty,
        "sma_fast": row.sma_fast,
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
        "max_daily_loss": row.max_daily_loss,
        "max_trades_per_day": row.max_trades_per_day,
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


@app.get("/api/state")
async def state():
    return engine.snapshot()


@app.get("/api/chart")
async def chart(limit: int = 240):
    # The 5/15/30/60-minute views build their bars from more 1-minute candles.
    # Off the event loop: building the chart must not hold up /api/state.
    return await asyncio.to_thread(engine.chart_payload, max(30, min(int(limit), 2500)))


@app.get("/api/history")
async def history(symbol: str, start: str, end: str, interval: int = 1, run_id: int | None = None):
    """Past candles from Groww for the chart's From/To view. Read-only.

    `run_id` marks one replay run's trades; without it, the practice/real
    trades plus the latest replay run in the range.
    """
    try:
        return await load_history(engine.broker, symbol, start, end, engine.load_config(), interval, run_id)
    except HistoryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/config")
async def get_config():
    return _config_dict(engine.load_config())


class TradeSymbolUpdate(BaseModel):
    symbol: str
    armed: bool


def _open_symbols() -> set[str]:
    return {symbol for symbol, pos in engine.positions.items() if pos is not None}


@app.post("/api/trade-symbols")
async def set_trade_symbol(body: TradeSymbolUpdate):
    """Arm or disarm a stock without changing the chart on screen."""
    symbol = (body.symbol or "").upper().strip()
    if not symbol or not symbol.isalnum():
        raise HTTPException(400, "Symbol must be an NSE trading symbol")
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        names = trade_names(row)
        if body.armed:
            if symbol not in names:
                if len(names) >= MAX_TRADE_SYMBOLS:
                    raise HTTPException(409, f"Trade is limited to {MAX_TRADE_SYMBOLS} stocks at once")
                names.append(symbol)
                engine.hold_for_next_cross([symbol])
        else:
            if symbol in _open_symbols():
                raise HTTPException(409, f"Close {symbol} before taking it off the trade buttons")
            names = [name for name in names if name != symbol]
        row.trade_symbols = ",".join(names)
        db.commit()
        db.refresh(row)
        payload = _config_dict(row)
        db.expunge(row)
    engine._cfg_cache = row
    return payload


@app.put("/api/config")
async def put_config(body: ConfigUpdate):
    # The chart symbol can change while another stock stays open. Quantity is
    # shared, so an open book still blocks a size change. Raising the trade
    # cap must not be blocked by that check.
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        own = stock_settings(row)
        if (
            body.qty is not None
            and int(body.qty) != int(row.qty)
            and any(
                pos.qty != body.qty
                for symbol, pos in engine.positions.items()
                if "qty" not in own.get(symbol, {})
            )
        ):
            raise HTTPException(409, "Close the open position before changing quantity")
        data = body.model_dump(exclude_none=True)
        if "symbol" in data:
            data["symbol"] = data["symbol"].upper().strip()
            if not data["symbol"].isalnum():
                raise HTTPException(400, "Symbol must be an NSE trading symbol")
        if "square_off_time" in data:
            _validate_hhmm(data["square_off_time"])
        if "entry_cutoff_time" in data:
            _validate_hhmm(data["entry_cutoff_time"], "entry_cutoff_time")
        if "sma_fast" in data and "sma_slow" in data and data["sma_fast"] >= data["sma_slow"]:
            raise HTTPException(400, "Fast SMA period must be shorter than the slow period")
        for key, value in data.items():
            setattr(row, key, value)
        if row.sma_fast >= row.sma_slow:
            raise HTTPException(400, "Fast SMA period must be shorter than the slow period")
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
    engine._cfg_cache = row
    if changed_cap:
        engine.release_trade_cap(cap)
        # A replay halted on the same cap resumes from the same Save.
        if replay.engine is not None:
            replay.engine.release_trade_cap(cap)
    return payload


def _check_settings(cfg: BotConfig, symbol: str = "") -> None:
    """The same consistency checks as Save, on one stock's settings."""
    who = f"{symbol}: " if symbol else ""
    if int(cfg.sma_fast) >= int(cfg.sma_slow):
        raise HTTPException(400, f"{who}Fast SMA period must be shorter than the slow period")
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


@app.get("/api/config/stock/{symbol}")
async def get_stock_config(symbol: str):
    """This stock's settings as the bot will use them, and which are its own."""
    name = _stock_name(symbol)
    row = engine.load_config()
    return {**_config_dict(_cfg_for(row, name)), "symbol": name, "own": stock_settings(row).get(name, {})}


@app.put("/api/config/stock/{symbol}")
async def put_stock_config(symbol: str, body: StockConfigUpdate):
    """Set this stock's own strategy settings.

    A value equal to the shared setting is not stored, so the stock keeps
    following the shared value when that changes later.
    """
    name = _stock_name(symbol)
    data = body.model_dump(exclude_none=True)
    data.pop("symbol", None)
    shared_only = sorted(k for k in data if k not in STOCK_FIELDS)
    if shared_only:
        raise HTTPException(
            400, f"{', '.join(shared_only)} apply to every stock. Change them with All stocks selected."
        )
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        everything = stock_settings(row)
        own = dict(everything.get(name, {}))
        for key, value in data.items():
            if value == getattr(row, key):
                own.pop(key, None)
            else:
                own[key] = value
        pos = engine.positions.get(name)
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
    engine._cfg_cache = row
    return payload


@app.delete("/api/config/stock/{symbol}")
async def reset_stock_config(symbol: str):
    """Drop this stock's own settings. It follows the shared ones again."""
    name = _stock_name(symbol)
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        everything = stock_settings(row)
        own = everything.pop(name, {})
        pos = engine.positions.get(name)
        if pos is not None and "qty" in own and int(pos.qty) != int(row.qty):
            raise HTTPException(409, f"Close the open {name} position before changing its quantity")
        row.stock_settings = json.dumps(everything)
        db.commit()
        db.refresh(row)
        payload = {**_config_dict(_cfg_for(row, name)), "symbol": name, "own": {}}
        db.expunge(row)
    engine._cfg_cache = row
    return payload


def _stock_name(symbol: str) -> str:
    name = (symbol or "").upper().strip()
    if not name or not name.isalnum():
        raise HTTPException(400, "Symbol must be an NSE trading symbol")
    return name


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
    from strategy_engine import _ist_now

    engine._roll_session(_ist_now())
    mode = body.mode.upper()
    if mode not in ("PAPER", "LIVE"):
        raise HTTPException(400, "mode must be PAPER or LIVE")
    if mode == "LIVE":
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
        engine.release_paper_halt()
    if engine.status == "HALTED":
        raise HTTPException(423, "Bot is halted for the day — restart tomorrow or reset after review")
    if _open_symbols():
        raise HTTPException(409, "Close the open position before switching execution mode")
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trading_mode = mode
        db.commit()
    engine.broker.set_mode(mode, _live_token())
    return {"trading_mode": mode}


@app.post("/api/bot/start")
async def start_bot():
    from strategy_engine import _ist_now

    engine._roll_session(_ist_now())
    engine.release_manual_panic()
    engine.release_trade_cap()
    if engine.status == "HALTED":
        raise HTTPException(423, engine.halt_reason or "Halted for the day")
    if engine.status == "DAY_COMPLETED":
        raise HTTPException(423, engine.halt_reason or "Session already squared off")
    # A cross already on the tape is not an order. The next cross is.
    engine.hold_for_next_cross(trade_names(engine.load_config()))
    engine.status = "RUNNING"
    engine.halt_reason = ""
    return {"bot_status": engine.status}


class ForceOrder(BaseModel):
    symbol: str = ""


@app.post("/api/bot/force")
async def force_order(body: ForceOrder):
    """Manual check order. Starts the bot. Does not wait for a cross."""
    try:
        result = await engine.force_order(body.symbol)
    except ForceRefused as exc:
        code = 423 if engine.status in ("HALTED", "DAY_COMPLETED") else 400
        raise HTTPException(code, str(exc)) from exc
    return {"bot_status": engine.status, "last_signal": result}


@app.post("/api/bot/pause")
async def pause_bot():
    if engine.status == "RUNNING":
        engine.status = "PAUSED"
    return {"bot_status": engine.status}


class CloseOrder(BaseModel):
    symbol: str = ""


@app.post("/api/bot/close")
async def close_position(body: CloseOrder):
    """Close one open stock. Does not halt the bot."""
    try:
        result = await engine.close_symbol(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": engine.status, "last_signal": result}


@app.post("/api/bot/kill")
async def kill_bot():
    """Panic: cancel SL, flatten MIS, lock the strategy."""
    await engine.kill("Manual PANIC SQUARE-OFF")
    return {"bot_status": engine.status, "halt_reason": engine.halt_reason}


class ReplayStart(BaseModel):
    date: str
    #: Last day of a multi-day run. Empty or equal to `date` replays one day.
    end_date: str | None = None
    start: str = "09:15"
    speed: int = 60
    #: Stocks to replay. Empty replays the armed stocks, as before. Lets the
    #: Scalp page test a shortlist without arming it.
    symbols: list[str] | None = None


class ScalpPickStart(BaseModel):
    date: str
    end_date: str | None = None
    #: The stocks to choose from each day (the Scalp page's streaming list).
    universe: list[str]
    pick_time: str = "09:45"
    top_n: int = Field(default=3, ge=1, le=10)
    min_atr_pct: float = Field(default=0.08, ge=0, le=5)
    min_value_cr: float = Field(default=5.0, ge=0, le=100000)
    require_bias: bool = True
    speed: int = 300


class ReplayControl(BaseModel):
    action: Literal["play", "pause", "stop", "speed"]
    speed: int | None = None


def _replay_engine():
    eng = replay.engine
    if eng is None:
        raise HTTPException(409, "No replay is playing. Start one first.")
    return eng


@app.get("/api/replay")
async def replay_info():
    return replay.info()


@app.post("/api/replay/scalp-picks")
async def replay_scalp_picks(body: ScalpPickStart):
    """Backtest the Scalp page's picks: each past day, score the universe at
    the pick time as the live page would, then let the SMA bot trade only the
    top picks for the rest of that day. Practice money; never sends an order.
    """
    cfg = engine.load_config()
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, "Switch to PAPER before starting a replay.")
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date)
        pick_time = parse_start(body.pick_time)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not (dt.time(9, 20) <= pick_time <= dt.time(14, 30)):
        raise HTTPException(400, "Pick a time between 09:20 and 14:30 IST.")
    universe: list[str] = []
    for raw in body.universe:
        name = (raw or "").strip().upper()
        if not name.isalnum():
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


@app.post("/api/replay/start")
async def replay_start(body: ReplayStart):
    """Practice on a past day's Groww candles. Never sends an order."""
    cfg = engine.load_config()
    if (cfg.trading_mode or "PAPER").upper() == "LIVE":
        raise HTTPException(409, "Switch to PAPER before starting a replay.")
    if body.speed not in SPEEDS:
        raise HTTPException(400, f"Speed must be one of {', '.join(str(s) for s in SPEEDS)}.")
    try:
        day, end_day = parse_replay_range(body.date, body.end_date)
        start = parse_start(body.start)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.symbols:
        symbols = []
        for raw in body.symbols:
            name = (raw or "").strip().upper()
            if not name.isalnum():
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
    await replay.begin(
        engine.broker,
        symbols,
        day,
        start,
        body.speed,
        end_day=end_day,
        settings={**settings_snapshot(cfg), "stock_settings": stock_settings(cfg)},
    )
    return replay.info()


@app.get("/api/replay/runs")
async def replay_runs():
    """Every replay run with its totals and the settings it used."""
    return list_runs()


@app.get("/api/replay/runs/{run_id}")
async def replay_run(run_id: int):
    """One run: day-wise P&L and the settings it used."""
    run = get_run(run_id)
    if run is None:
        raise HTTPException(404, "No such replay run")
    return run


@app.delete("/api/replay/runs/{run_id}")
async def replay_run_delete(run_id: int):
    if replay.run_id == run_id and replay.active:
        raise HTTPException(409, "Stop this replay before deleting it.")
    if not delete_run(run_id):
        raise HTTPException(404, "No such replay run")
    return {"deleted": run_id}


@app.post("/api/replay/control")
async def replay_control(body: ReplayControl):
    if body.action == "stop":
        await replay.stop()
        return replay.info()
    try:
        replay.control(body.action, body.speed)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return replay.info()


@app.get("/api/replay/state")
async def replay_state():
    eng = _replay_engine()
    return {**eng.snapshot(), "replay": replay.info()}


@app.get("/api/replay/chart")
async def replay_chart(limit: int = 240):
    # Off the event loop: building the chart must not hold up /api/state.
    return await asyncio.to_thread(_replay_engine().chart_payload, max(30, min(int(limit), 2500)))


@app.post("/api/replay/bot/start")
async def replay_bot_start():
    eng = _replay_engine()
    eng.release_manual_panic()
    eng.release_trade_cap()
    if eng.status in ("HALTED", "DAY_COMPLETED"):
        raise HTTPException(423, eng.halt_reason or "This replay day is finished")
    eng.hold_for_next_cross(eng.replay_symbols)
    eng.status = "RUNNING"
    eng.halt_reason = ""
    return {"bot_status": eng.status}


@app.post("/api/replay/bot/pause")
async def replay_bot_pause():
    eng = _replay_engine()
    if eng.status == "RUNNING":
        eng.status = "PAUSED"
    return {"bot_status": eng.status}


@app.post("/api/replay/bot/force")
async def replay_bot_force(body: ForceOrder):
    eng = _replay_engine()
    try:
        result = await eng.force_order(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/replay/bot/close")
async def replay_bot_close(body: CloseOrder):
    eng = _replay_engine()
    try:
        result = await eng.close_symbol(body.symbol)
    except ForceRefused as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"bot_status": eng.status, "last_signal": result}


@app.post("/api/replay/bot/kill")
async def replay_bot_kill():
    eng = _replay_engine()
    await eng.kill("Manual PANIC SQUARE-OFF (replay)")
    return {"bot_status": eng.status, "halt_reason": eng.halt_reason}


@app.get("/api/trades")
async def trades():
    return attach_market_prices(engine.trades(), engine._ltps)


@app.get("/api/trades/book")
async def trades_book(mode: str = "PAPER", limit: int = BOOK_LIMIT):
    """One book's newest trades, up to 20,000, for the blotter."""
    book = (mode or "PAPER").upper()
    if book not in BOOKS:
        raise HTTPException(status_code=422, detail=f"mode must be one of {', '.join(BOOKS)}")
    ltps = dict(engine._ltps)

    def build() -> str:
        data = engine.book(book, limit)
        rows, strategies = pack_strategies(attach_market_prices(engine.with_open_extremes(data["rows"]), ltps))
        return json.dumps({"mode": book, "total": data["total"], "rows": rows, "strategies": strategies})

    # Thousands of rows take a moment; build them off the event loop so the
    # bot keeps ticking, and skip FastAPI's per-value encoder (plain values only).
    return Response(await asyncio.to_thread(build), media_type="application/json")


@app.get("/api/trades/counts")
async def trades_counts():
    return engine.book_counts()


@app.get("/api/trades.csv")
async def trades_csv(mode: str = ""):
    book = (mode or "").upper()
    data = engine.book(book if book in BOOKS else None)
    rows = attach_market_prices(data["rows"], engine._ltps)
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
        writer.writerow(row)
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
