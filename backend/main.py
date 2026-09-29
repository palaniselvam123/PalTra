"""SMA(9, 21) + 1.5× ATR intraday terminal API.

Run from `backend/`:

    uvicorn main:app --port 8001

WebSocket `/ws/stream` pushes a 1-second snapshot (LTP, SMAs, ATR, position,
stop, P&L, bot status). REST lives under `/api`.
"""
from __future__ import annotations

import asyncio
import csv
import io
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import get_settings
from database import init_db, session_factory
from models import BotConfig
from strategy_engine import MAX_TRADE_SYMBOLS, StrategyEngine, trade_names

engine = StrategyEngine()


_booted = False
_task: asyncio.Task | None = None


def boot_engine() -> asyncio.Task | None:
    """Start the 1-second loop once. Safe if both the standalone app and a mount call it."""
    global _booted, _task
    if _booted:
        return _task
    _booted = True
    init_db()
    cfg = engine.load_config()
    engine.restore_open_books()
    engine.broker.set_mode(cfg.trading_mode, get_settings().groww_access_token)
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
    adx_threshold: float | None = Field(default=None, ge=0, le=100)
    max_daily_loss: float | None = Field(default=None, gt=0)
    max_trades_per_day: int | None = Field(default=None, ge=1, le=100)
    square_off_time: str | None = None


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
        "adx_threshold": row.adx_threshold,
        "max_daily_loss": row.max_daily_loss,
        "max_trades_per_day": row.max_trades_per_day,
        "square_off_time": row.square_off_time,
        "trading_mode": row.trading_mode,
    }


@app.get("/api/health")
async def health():
    return {"ok": True, "mode": engine.load_config().trading_mode, "bot": engine.status}


@app.get("/api/state")
async def state():
    return engine.snapshot()


@app.get("/api/chart")
async def chart():
    return engine.chart_payload()


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
        else:
            if symbol in _open_symbols():
                raise HTTPException(409, f"Close {symbol} before taking it off the trade buttons")
            names = [name for name in names if name != symbol]
        row.trade_symbols = ",".join(names)
        db.commit()
        db.refresh(row)
        return _config_dict(row)


@app.put("/api/config")
async def put_config(body: ConfigUpdate):
    # The chart symbol can change while another stock stays open. Quantity is
    # shared, so an open book still blocks a size change.
    if body.qty is not None and any(pos.qty != body.qty for pos in engine.positions.values()):
        raise HTTPException(409, "Close the open position before changing quantity")
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise HTTPException(500, "BotConfig missing")
        data = body.model_dump(exclude_none=True)
        if "symbol" in data:
            data["symbol"] = data["symbol"].upper().strip()
            if not data["symbol"].isalnum():
                raise HTTPException(400, "Symbol must be an NSE trading symbol")
        if "square_off_time" in data:
            _validate_hhmm(data["square_off_time"])
        if "sma_fast" in data and "sma_slow" in data and data["sma_fast"] >= data["sma_slow"]:
            raise HTTPException(400, "Fast SMA period must be shorter than the slow period")
        for key, value in data.items():
            setattr(row, key, value)
        if row.sma_fast >= row.sma_slow:
            raise HTTPException(400, "Fast SMA period must be shorter than the slow period")
        db.commit()
        db.refresh(row)
        return _config_dict(row)


def _validate_hhmm(value: str) -> None:
    try:
        parts = (value or "").split(":")
        if len(parts) != 2:
            raise ValueError
        hh, mm = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        raise HTTPException(400, "square_off_time must be HH:MM") from None
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise HTTPException(400, "square_off_time must be HH:MM")


@app.post("/api/mode")
async def set_mode(body: ModeUpdate):
    """PAPER is the default. LIVE requires confirm_live and a Groww token."""
    mode = body.mode.upper()
    if mode not in ("PAPER", "LIVE"):
        raise HTTPException(400, "mode must be PAPER or LIVE")
    if engine.status == "HALTED":
        raise HTTPException(423, "Bot is halted for the day — restart tomorrow or reset after review")
    if mode == "LIVE":
        if not body.confirm_live:
            raise HTTPException(
                400,
                "LIVE REAL MONEY requires explicit confirmation (confirm_live=true).",
            )
        token = get_settings().groww_access_token
        if not token:
            raise HTTPException(
                503,
                "GROWW_ACCESS_TOKEN is not set. Refusing to enable LIVE.",
            )
    if _open_symbols():
        raise HTTPException(409, "Close the open position before switching execution mode")
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trading_mode = mode
        db.commit()
    engine.broker.set_mode(mode, get_settings().groww_access_token)
    return {"trading_mode": mode}


@app.post("/api/bot/start")
async def start_bot():
    if engine.status == "HALTED":
        raise HTTPException(423, engine.halt_reason or "Halted for the day")
    if engine.status == "DAY_COMPLETED":
        raise HTTPException(423, engine.halt_reason or "Session already squared off")
    engine.status = "RUNNING"
    engine.halt_reason = ""
    return {"bot_status": engine.status}


@app.post("/api/bot/pause")
async def pause_bot():
    if engine.status == "RUNNING":
        engine.status = "PAUSED"
    return {"bot_status": engine.status}


@app.post("/api/bot/kill")
async def kill_bot():
    """Panic: cancel SL, flatten MIS, lock the strategy."""
    await engine.kill("Manual PANIC SQUARE-OFF")
    return {"bot_status": engine.status, "halt_reason": engine.halt_reason}


@app.get("/api/trades")
async def trades():
    return engine.trades()


@app.get("/api/trades.csv")
async def trades_csv():
    rows = engine.trades()
    buffer = io.StringIO()
    fields = [
        "id",
        "date",
        "symbol",
        "direction",
        "qty",
        "entry_time",
        "entry_price",
        "ma_cross_price",
        "atr_at_entry",
        "sl_trigger_price",
        "exit_time",
        "exit_price",
        "exit_reason",
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
