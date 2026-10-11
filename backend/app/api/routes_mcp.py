"""MCP server for outside AI tools (n8n, Gemini, Claude, ...): POST /mcp.

Streamable HTTP, stateless, JSON responses only. Off unless the MCP_TOKEN
secret is set; every call then needs that token, as `Authorization: Bearer
<token>` or, for clients that cannot send headers, as the last path part
(`/mcp/<token>`).

Reads cover the SMA bots (status, settings, trades, scans). Writes are the two
changes the chat assistant can propose (Trade list, stop settings), and only
on a bot in PAPER: an outside agent has no Apply button, so a LIVE bot is
refused. There is no tool for orders, closing, the PAPER/LIVE switch,
start/stop or panic. Every call goes through the terminal's own routes, so
their checks apply.
"""
from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import ValidationError

from app.services.chat_actions import STOP_FIELDS, StopProposal, TradeListProposal

router = APIRouter(tags=["mcp"])
log = logging.getLogger("mcp")


class _HideToken(logging.Filter):
    """Uvicorn's access log prints the path; never let a /mcp/<token> reach the Fly log."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple):
            record.args = tuple(
                a.split("/mcp/", 1)[0] + "/mcp/<token>" if isinstance(a, str) and "/mcp/" in a else a for a in args
            )
        return True


logging.getLogger("uvicorn.access").addFilter(_HideToken())

SERVER_INFO = {"name": "paltra", "version": "1.0.0"}
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
MIN_TOKEN_LEN = 24
MAX_TRADES = 200

STATUS_KEYS = (
    "bot", "bot_name", "bot_status", "halt_reason", "mode", "data_source", "connected", "last_error",
    "last_signal", "trade_symbols", "books", "stop_enabled", "stop_type", "realized_net_pnl",
    "open_net_total", "open_gross_total", "trades_today", "max_trades", "max_daily_loss", "kpis",
)


def _token() -> str:
    return os.environ.get("MCP_TOKEN", "").strip()


def _authorized(request: Request, path_token: str | None) -> bool:
    expected = _token()
    header = request.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else (path_token or "")
    return bool(given) and hmac.compare_digest(given.encode(), expected.encode())


def terminal_client() -> httpx.AsyncClient:
    """The SMA terminal's own API: in this process on Fly, else the local terminal on :8001."""
    from app.sma_host import load_terminal

    mod = load_terminal()
    if mod is not None and hasattr(mod, "app"):
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=mod.app), base_url="http://sma", timeout=30)
    return httpx.AsyncClient(base_url=os.environ.get("SMA_INTERNAL_URL", "http://127.0.0.1:8001"), timeout=30)


class ToolError(Exception):
    pass


async def _call(method: str, path: str, **kwargs) -> Any:
    async with terminal_client() as client:
        res = await client.request(method, path, **kwargs)
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail", res.text)
        except Exception:  # noqa: BLE001
            detail = res.text
        raise ToolError(f"Terminal refused ({res.status_code}): {str(detail)[:300]}")
    return res.json()


def _bot_path(bot: int, path: str) -> str:
    return path if bot == 1 else path.replace("/api/", f"/api/bots/{bot}/", 1)


def _bot_arg(args: dict) -> int:
    bot = args.get("bot", 1)
    if bot not in (1, 2, 3, 4):
        raise ToolError("bot must be 1, 2, 3 or 4")
    return int(bot)


async def _require_paper(bot: int) -> dict:
    bots = await _call("GET", "/api/bots")
    row = next((b for b in bots if b.get("bot") == bot), None)
    if row is None:
        raise ToolError(f"Bot {bot} not found")
    if (row.get("mode") or "").upper() != "PAPER":
        raise ToolError(
            f"Bot {bot} is {row.get('mode')}. Outside tools may only change PAPER bots; change a LIVE bot in the app."
        )
    return row


# ---- tools -----------------------------------------------------------------

async def list_bots(args: dict) -> Any:
    return await _call("GET", "/api/bots")


async def get_bot_status(args: dict) -> Any:
    bot = _bot_arg(args)
    state = await _call("GET", _bot_path(bot, "/api/state"))
    return {k: state.get(k) for k in STATUS_KEYS if k in state}


async def get_settings(args: dict) -> Any:
    bot = _bot_arg(args)
    symbol = (args.get("symbol") or "").strip().upper()
    if symbol:
        return await _call("GET", _bot_path(bot, f"/api/config/stock/{symbol}"))
    return await _call("GET", _bot_path(bot, "/api/config"))


async def get_trades(args: dict) -> Any:
    bot = _bot_arg(args)
    mode = (args.get("mode") or "PAPER").upper()
    if mode not in ("PAPER", "LIVE"):
        raise ToolError("mode must be PAPER or LIVE")
    limit = max(1, min(int(args.get("limit") or 50), MAX_TRADES))
    book = await _call("GET", "/api/trades/book", params={"mode": mode, "limit": limit, "bot": bot})
    return {"mode": mode, "bot": bot, "total": book.get("total"), "rows": book.get("rows", [])}


async def get_scans(args: dict) -> Any:
    return {"cross_scan": await _call("GET", "/api/cross-scan"), "trend_quality": await _call("GET", "/api/chop-scan")}


def _run_bot(run: dict) -> int:
    try:
        return int((run.get("settings") or {}).get("bot") or 1)
    except (TypeError, ValueError):
        return 1


async def list_backtests(args: dict) -> Any:
    bot = args.get("bot")
    if bot is not None and bot not in (1, 2, 3, 4):
        raise ToolError("bot must be 1, 2, 3 or 4")
    limit = max(1, min(int(args.get("limit") or 20), 50))
    runs = await _call("GET", "/api/replay/runs", params={"limit": 500 if bot is not None else limit})
    out = []
    for run in runs:
        if bot is not None and _run_bot(run) != bot:
            continue
        out.append({**{k: v for k, v in run.items() if k != "settings"}, "bot": _run_bot(run)})
        if len(out) >= limit:
            break
    return out


async def get_backtest(args: dict) -> Any:
    try:
        run_id = int(args.get("run_id"))
    except (TypeError, ValueError) as exc:
        raise ToolError("run_id must be a number from list_backtests") from exc
    run = await _call("GET", f"/api/replay/runs/{run_id}")
    return {**run, "bot": _run_bot(run)}


BACKTEST_SPEED = 300
REPLAY_BUSY = ("LOADING", "PLAYING", "PAUSED")
# Same window as the deploy gate: a backtest shares the event loop with the trading bots.
MARKET_BLOCK = (dt.time(9, 0), dt.time(15, 45))


def _now() -> dt.datetime:
    from app.core.market_clock import ist_now

    return ist_now()


async def start_backtest(args: dict) -> Any:
    bot = _bot_arg(args)
    now = _now()
    if now.weekday() < 5 and MARKET_BLOCK[0] <= now.time() <= MARKET_BLOCK[1]:
        raise ToolError(
            "Backtests cannot be started from outside tools during market hours (09:00-15:45 IST, Mon-Fri). "
            "Try after 15:45, or start it in the app."
        )
    date = str(args.get("date") or "").strip()
    if not date:
        raise ToolError("date is required (YYYY-MM-DD)")
    symbols = args.get("symbols") or None
    if symbols is not None and not isinstance(symbols, list):
        raise ToolError("symbols must be a list of NSE symbols")
    current = await _call("GET", "/api/replay", params={"bot": bot})
    if current.get("status") in REPLAY_BUSY:
        raise ToolError(f"Bot {bot} already has a backtest {current['status'].lower()}. Wait for it to finish.")
    body = {"bot": bot, "date": date, "end_date": args.get("end_date") or None, "start": "09:15",
            "speed": BACKTEST_SPEED, "symbols": symbols}
    info = await _call("POST", "/api/replay/start", json=body)
    log.info("mcp.start_backtest bot=%s date=%s end=%s symbols=%s", bot, date, body["end_date"], len(symbols or []))
    return {
        "bot": bot,
        "status": info.get("status"),
        "date": info.get("date"),
        "end_date": info.get("end_date"),
        "note": "Started. It runs at full speed in the background; check progress with list_backtests / get_backtest.",
    }


async def set_trade_list(args: dict) -> Any:
    try:
        p = TradeListProposal.model_validate(args)
    except ValidationError as exc:
        raise ToolError(f"Invalid request: {exc.errors()[0]['msg']}") from exc
    if not p.arm and not p.unarm:
        raise ToolError("Name at least one stock to arm or unarm")
    if set(p.arm) & set(p.unarm):
        raise ToolError("A stock cannot be armed and unarmed at once")
    await _require_paper(p.bot)
    done, failed = [], []
    for symbol, armed in [*((s, True) for s in p.arm), *((s, False) for s in p.unarm)]:
        try:
            await _call("POST", _bot_path(p.bot, "/api/trade-symbols"), json={"symbol": symbol, "armed": armed, "source": "mcp"})
            done.append(f"{'armed' if armed else 'unarmed'} {symbol}")
        except ToolError as exc:
            failed.append(f"{symbol}: {exc}")
    log.info("mcp.set_trade_list bot=%s done=%s failed=%s", p.bot, done, len(failed))
    return {"bot": p.bot, "done": done, "failed": failed}


async def set_stop(args: dict) -> Any:
    try:
        p = StopProposal.model_validate(args)
    except ValidationError as exc:
        raise ToolError(f"Invalid request: {exc.errors()[0]['msg']}") from exc
    changes = {k: getattr(p, k) for k in STOP_FIELDS if getattr(p, k) is not None}
    if not changes:
        raise ToolError("No stop settings given")
    await _require_paper(p.bot)
    path = f"/api/config/stock/{p.symbol}" if p.symbol else "/api/config"
    saved = await _call("PUT", _bot_path(p.bot, path), json=changes)
    log.info("mcp.set_stop bot=%s symbol=%s fields=%s", p.bot, p.symbol, sorted(changes))
    return {"bot": p.bot, "symbol": p.symbol, "saved": {k: saved.get(k) for k in changes}}


_BOT = {"type": "integer", "enum": [1, 2, 3, 4], "description": "SMA bot: 1 = main desk, 2-4 = bots 2-4. Default 1."}
_STOP_PROPS = {
    "use_stop": {"type": "boolean"},
    "stop_type": {"type": "string", "enum": ["ATR", "SMA_GAP", "TSL"]},
    "atr_multiplier": {"type": "number"},
    "tsl_mode": {"type": "string", "enum": ["POINTS", "PERCENT"]},
    "tsl_sl_points": {"type": "number"},
    "tsl_trail_points": {"type": "number"},
    "tsl_target_points": {"type": "number"},
    "tsl_sl_pct": {"type": "number"},
    "tsl_trail_pct": {"type": "number"},
    "tsl_target_pct": {"type": "number"},
    "gap_sl_mult": {"type": "number"},
    "gap_tp_mult": {"type": "number"},
    "gap_min_pct": {"type": "number"},
}
_READ = {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS: dict[str, dict] = {
    "list_bots": {
        "fn": list_bots,
        "description": "All four SMA bots: name, PAPER/LIVE mode, status, armed and held stocks, trades and P&L today.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": _READ,
    },
    "get_bot_status": {
        "fn": get_bot_status,
        "description": "One SMA bot's status: mode, running/halted, open positions per stock (books), P&L, today's trade count and caps.",
        "inputSchema": {"type": "object", "properties": {"bot": _BOT}},
        "annotations": _READ,
    },
    "get_settings": {
        "fn": get_settings,
        "description": "One SMA bot's strategy and stop settings, or one stock's settings on it (its own overrides in `own`).",
        "inputSchema": {"type": "object", "properties": {"bot": _BOT, "symbol": {"type": "string"}}},
        "annotations": _READ,
    },
    "get_trades": {
        "fn": get_trades,
        "description": "Newest trades of one SMA bot's PAPER or LIVE book (net_pnl is after charges).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "bot": _BOT,
                "mode": {"type": "string", "enum": ["PAPER", "LIVE"]},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_TRADES},
            },
        },
        "annotations": _READ,
    },
    "get_scans": {
        "fn": get_scans,
        "description": "Last Cross scan (stocks about to cross / just crossed) and Trend quality scan results.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": _READ,
    },
    "list_backtests": {
        "fn": list_backtests,
        "description": (
            "Backtest (replay) runs, newest first, optionally for one bot: dates, stocks, status, days done, "
            "and totals (trades, wins, net P&L, charges). Use get_backtest for one run's detail."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"bot": _BOT, "limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
        "annotations": _READ,
    },
    "get_backtest": {
        "fn": get_backtest,
        "description": "One backtest run: day-by-day P&L, per-stock results and the exact settings it used.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer", "description": "The id from list_backtests."}},
            "required": ["run_id"],
        },
        "annotations": _READ,
    },
    "start_backtest": {
        "fn": start_backtest,
        "description": (
            "Start a practice backtest (replay of past days' Groww candles) with one PAPER bot's current settings. "
            "date and optional end_date are YYYY-MM-DD (a range up to about 30 trading days); symbols defaults to "
            "the bot's armed stocks. Never sends an order. Refused on a LIVE bot, while that bot already has a "
            "backtest running, and during market hours (09:00-15:45 IST, Mon-Fri)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bot": _BOT,
                "date": {"type": "string", "description": "First day, YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Last day, YYYY-MM-DD. Omit for one day."},
                "symbols": {"type": "array", "items": {"type": "string"}, "description": "Stocks; default the armed ones."},
            },
            "required": ["date"],
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
    "set_trade_list": {
        "fn": set_trade_list,
        "description": (
            "Arm or unarm stocks on a PAPER SMA bot's Trade list (refused on a LIVE bot). Arming lets the bot "
            "trade the stock on its next signal; it does not place an order."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "bot": _BOT,
                "arm": {"type": "array", "items": {"type": "string"}},
                "unarm": {"type": "array", "items": {"type": "string"}},
            },
        },
        "annotations": _WRITE,
    },
    "set_stop": {
        "fn": set_stop,
        "description": (
            "Set stop-loss settings on a PAPER SMA bot (refused on a LIVE bot), for all stocks or one `symbol`. "
            "Send only the fields that change. TSL with tsl_mode POINTS uses rupees, PERCENT uses % of entry; "
            "a fixed stop + target is TSL with tsl_trail_points equal to tsl_target_points."
        ),
        "inputSchema": {"type": "object", "properties": {"bot": _BOT, "symbol": {"type": "string"}, **_STOP_PROPS}},
        "annotations": _WRITE,
    },
}


# ---- JSON-RPC ----------------------------------------------------------------

def _result(msg_id: Any, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


async def handle(msg: Any) -> dict | None:
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request")
    method, msg_id, params = msg["method"], msg.get("id"), msg.get("params") or {}
    if "id" not in msg:
        return None  # notification, e.g. notifications/initialized
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _result(
            msg_id,
            {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": (
                    "PalTra SMA trading bots. Read status, settings, trades, scans and backtests. Changes are limited to "
                    "arming stocks and stop settings on PAPER bots, and starting practice backtests outside market "
                    "hours; orders and LIVE bots are not reachable."
                ),
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(
            msg_id,
            {
                "tools": [
                    {"name": name, "description": t["description"], "inputSchema": t["inputSchema"], "annotations": t["annotations"]}
                    for name, t in TOOLS.items()
                ]
            },
        )
    if method == "tools/call":
        tool = TOOLS.get(params.get("name"))
        if tool is None:
            return _error(msg_id, -32602, f"Unknown tool: {params.get('name')}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(msg_id, -32602, "arguments must be an object")
        try:
            data = await tool["fn"](args)
            return _result(msg_id, {"content": [{"type": "text", "text": json.dumps(data, default=str)}], "isError": False})
        except (ToolError, ValueError) as exc:
            return _result(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except httpx.HTTPError as exc:
            return _result(msg_id, {"content": [{"type": "text", "text": f"Terminal not reachable: {exc}"}], "isError": True})
    return _error(msg_id, -32601, f"Method not found: {method}")


async def _serve(request: Request, path_token: str | None) -> Response:
    if len(_token()) < MIN_TOKEN_LEN:
        return JSONResponse({"detail": "MCP is off. Set the MCP_TOKEN secret (24+ characters) to turn it on."}, status_code=404)
    if not _authorized(request, path_token):
        return JSONResponse({"detail": "Invalid or missing token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    if isinstance(body, list):
        replies = [r for r in [await handle(m) for m in body] if r is not None]
        return JSONResponse(replies) if replies else Response(status_code=202)
    reply = await handle(body)
    return JSONResponse(reply) if reply is not None else Response(status_code=202)


@router.post("/mcp")
async def mcp(request: Request):
    return await _serve(request, None)


@router.post("/mcp/{token}")
async def mcp_with_token(token: str, request: Request):
    return await _serve(request, token)


@router.get("/mcp")
@router.get("/mcp/{token}")
async def mcp_get():
    # Stateless server: no server-initiated stream.
    return Response(status_code=405, headers={"Allow": "POST"})
