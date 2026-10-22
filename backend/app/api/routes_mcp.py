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


SCAN_PATHS = {"cross": "/api/cross-scan", "chop": "/api/chop-scan", "morning": "/api/morning-scan"}
SCAN_NAMES = {"cross": "cross_scan", "chop": "trend_quality", "morning": "morning_0930"}
MAX_SCAN_ROWS = 500
MAX_SCAN_SYMBOLS = 250


def _trim(snapshot: dict, limit: int) -> dict:
    rows = snapshot.get("rows") or []
    return {**snapshot, "rows": rows[:limit], "rows_total": len(rows)}


def _limit(args: dict, default: int) -> int:
    return max(1, min(int(args.get("limit") or default), MAX_SCAN_ROWS))


async def get_scans(args: dict) -> Any:
    kind = (args.get("kind") or "all").lower()
    if kind != "all" and kind not in SCAN_PATHS:
        raise ToolError("kind must be cross, chop, morning or all")
    limit = _limit(args, 100)
    kinds = list(SCAN_PATHS) if kind == "all" else [kind]
    return {SCAN_NAMES[k]: _trim(await _call("GET", SCAN_PATHS[k]), limit) for k in kinds}


async def _scan_universe(stocks: str) -> list[str]:
    """The stocks a scan reads: the F&O list (as the Scalp page) or every bot's armed ones."""
    if stocks == "armed":
        bots = await _call("GET", "/api/bots")
        return sorted({str(s).upper() for b in bots for s in (b.get("armed") or [])})
    out = await _desk_call("universe", lists=stocks)
    if not out.get("symbols"):
        raise ToolError(out.get("error") or "The F&O stock list is not loaded yet; try again in a minute.")
    return list(out["symbols"])


async def start_scan(args: dict) -> Any:
    kind = (args.get("kind") or "").lower()
    if kind not in SCAN_PATHS:
        raise ToolError("kind must be cross, chop or morning")
    symbols = [str(s).strip().upper() for s in (args.get("symbols") or []) if str(s).strip()]
    if not symbols:
        stocks = (args.get("stocks") or "fno").lower()
        if stocks not in ("fno", "armed", "nifty50", "nifty100", "niftybank"):
            raise ToolError("stocks must be fno, armed, nifty50, nifty100 or niftybank")
        symbols = await _scan_universe(stocks)
    if not symbols:
        raise ToolError("No stocks to scan (no bot has armed stocks).")
    symbols = symbols[:MAX_SCAN_SYMBOLS]
    body: dict = {"symbols": symbols, "force": bool(args.get("force"))}
    if kind in ("cross", "chop"):
        body["minutes"] = int(args.get("minutes") or 5)
    if kind == "chop":
        for key in ("quiet_min", "price_min", "price_max"):
            if args.get(key) is not None:
                body[key] = args[key]
    out = await _call("POST", f"{SCAN_PATHS[kind]}/start", json=body)
    log.info("mcp.start_scan kind=%s stocks=%d", kind, len(symbols))
    return {"kind": kind, "stocks": len(symbols), "started": _trim(out, 0) if isinstance(out, dict) else out}


async def _desk_call(name: str, **kwargs) -> Any:
    """The desk's own Scalp routes (this process), called directly: the Google desk lock only
    guards browser requests, and the MCP token was already checked."""
    from app.api import routes_scalp as scalp

    if name == "monitor":
        return await scalp.scalp_monitor(**kwargs)
    if name == "active":
        return await scalp.most_active(**kwargs)
    if name == "universe":
        return await scalp.fno_universe(lists=kwargs.get("lists", "fno"))
    raise ToolError(f"unknown desk call {name}")


async def get_scalp_monitor(args: dict) -> Any:
    from app.services.scalp_monitor import DEFAULT_MAX_SPREAD_PCT, DEFAULT_MIN_ATR_PCT, DEFAULT_MIN_VALUE_CR

    out = await _desk_call(
        "monitor",
        min_atr_pct=float(args.get("min_atr_pct", DEFAULT_MIN_ATR_PCT)),
        max_spread_pct=float(args.get("max_spread_pct", DEFAULT_MAX_SPREAD_PCT)),
        min_value_cr=float(args.get("min_value_cr", DEFAULT_MIN_VALUE_CR)),
        only_ready=bool(args.get("only_ready", False)),
        top=max(1, min(int(args.get("limit") or 50), MAX_SCAN_ROWS)),
    )
    out.pop("alerts", None)
    return out


async def get_most_active(args: dict) -> Any:
    sort = (args.get("sort") or "value").lower()
    if sort not in ("value", "rvol", "change", "pressure"):
        raise ToolError("sort must be value, rvol, change or pressure")
    bias = (args.get("bias") or "").upper() or None
    return await _desk_call(
        "active", sort=sort, top=max(1, min(int(args.get("limit") or 40), 250)), bias=bias,
        min_value_cr=float(args.get("min_value_cr") or 0.0),
    )


async def get_stock_news(args: dict) -> Any:
    from app.services.stock_news import stock_news

    symbol = str(args.get("symbol") or "").strip().upper()
    if not symbol or not symbol.replace("&", "").replace("-", "").replace("_", "").isalnum():
        raise ToolError("symbol (an NSE trading symbol, e.g. SBIN) is required")
    out = await stock_news(symbol)
    limit = max(1, min(int(args.get("limit") or 20), 40))
    return {**out, "items": out["items"][:limit]}


async def list_scan_history(args: dict) -> Any:
    params = {}
    if args.get("day"):
        params["day"] = str(args["day"])
    kind = (args.get("kind") or "").lower()
    if kind:
        if kind not in ("cross", "chop"):
            raise ToolError("kind must be cross or chop")
        params["kind"] = kind
    out = await _call("GET", "/api/scan-history", params=params)
    out.pop("checking", None)
    return out


async def get_scan_history(args: dict) -> Any:
    run_id = args.get("run_id")
    if not isinstance(run_id, int):
        raise ToolError("run_id (an integer from list_scan_history) is required")
    return _trim(await _call("GET", f"/api/scan-history/{run_id}"), _limit(args, 100))


async def check_scan_accuracy(args: dict) -> Any:
    run_id = args.get("run_id")
    if not isinstance(run_id, int):
        raise ToolError("run_id (an integer from list_scan_history) is required")
    out = await _call("POST", f"/api/scan-history/{run_id}/check")
    return {"run_id": run_id, "started": out}


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


async def _require_no_backtest(bot: int) -> None:
    """A multi-day backtest reads the bot's settings again each day: a change mid-run spoils it."""
    current = await _call("GET", "/api/replay", params={"bot": bot})
    if current.get("status") in REPLAY_BUSY:
        raise ToolError(
            f"Bot {bot} has a backtest {current['status'].lower()}. Wait for it to finish before changing "
            "settings, or the later days would use the new ones."
        )


async def get_backtest_trades(args: dict) -> Any:
    try:
        run_id = int(args.get("run_id"))
    except (TypeError, ValueError) as exc:
        raise ToolError("run_id must be a number from list_backtests") from exc
    params = {"symbol": str(args["symbol"]).strip().upper()} if args.get("symbol") else None
    rows = await _call("GET", f"/api/replay/runs/{run_id}/trades", params=params)
    return {"run_id": run_id, "trades": rows[:MAX_TRADES], "truncated": len(rows) > MAX_TRADES}


def _stop_fields(settings: dict) -> dict:
    keys = ("use_stop", "stop_type", "atr_multiplier", *(k for k in STOP_FIELDS if k.startswith(("tsl_", "gap_"))))
    return {k: settings.get(k) for k in keys if k in settings}


async def compare_backtests(args: dict) -> Any:
    ids = args.get("run_ids") or []
    if not isinstance(ids, list) or not 2 <= len(ids) <= 6:
        raise ToolError("run_ids must list 2 to 6 runs from list_backtests")
    out = []
    for raw in ids:
        try:
            run = await _call("GET", f"/api/replay/runs/{int(raw)}")
        except (TypeError, ValueError) as exc:
            raise ToolError(f"{raw!r} is not a run id") from exc
        settings = run.get("settings") or {}
        stock_settings = settings.get("stock_settings") or {}
        if isinstance(stock_settings, str):
            try:
                stock_settings = json.loads(stock_settings or "{}")
            except ValueError:
                stock_settings = {}
        out.append(
            {
                "run_id": run.get("id"),
                "bot": _run_bot(run),
                "dates": [run.get("start_date"), run.get("end_date")],
                "status": run.get("status"),
                "symbols": run.get("symbols"),
                "totals": run.get("totals"),
                "stop_settings": _stop_fields(settings),
                "stock_stop_settings": {
                    sym: _stop_fields(own) for sym, own in stock_settings.items()
                    if isinstance(own, dict) and _stop_fields(own)
                },
                "per_stock": [{"symbol": s.get("symbol"), "totals": s.get("totals")} for s in run.get("stocks") or []],
            }
        )
    return {"runs": out, "note": "Compare only runs over the same dates and stocks; totals.net is after charges."}


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
    await _require_no_backtest(p.bot)
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
    await _require_no_backtest(p.bot)
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
_SCAN = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
_KIND = {"type": "string", "enum": ["cross", "chop", "morning"]}
_LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_SCAN_ROWS, "description": "Rows to return (default 100)."}
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
        "description": (
            "Scalp page scans, last result of each: cross = Cross scan (stocks whose SMA fast/slow are about to "
            "cross or just crossed, soonest first, with volume and speed); chop = Trend quality (crosses today, "
            "TRENDING/MIXED/CHOPPY, strong trend, trend phases, quiet runners); morning = 09:30 morning checklist "
            "(ORB, RVOL, VWAP, gap, candles; runners). `rows_total` is the full count. Use start_scan for a fresh one."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"kind": {"type": "string", "enum": ["cross", "chop", "morning", "all"]}, "limit": _LIMIT},
        },
        "annotations": _READ,
    },
    "start_scan": {
        "fn": start_scan,
        "description": (
            "Run a Scalp page scan now (the page's Scan now button). Read-only: it downloads Groww candles and "
            "never orders or changes a bot. kind cross / chop / morning; stocks = fno (the F&O list, as the page) or "
            "armed (every bot's armed stocks), or pass `symbols`. minutes = candle size for cross / chop (1-15, "
            "default 5); nifty50 / nifty100 / niftybank scan that index's stocks. Returns at once; poll get_scans until `running` is false. Refused while scans are paused "
            "(or moved to the scanner server), and a rescan of the same candle waits a minute unless force."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": _KIND,
                "stocks": {"type": "string", "enum": ["fno", "armed", "nifty50", "nifty100", "niftybank"]},
                "symbols": {"type": "array", "items": {"type": "string"}, "maxItems": MAX_SCAN_SYMBOLS},
                "minutes": {"type": "integer", "enum": [1, 2, 3, 5, 10, 15]},
                "quiet_min": {"type": "integer", "minimum": 1, "maximum": 375, "description": "Trend quality: quiet-runner minutes."},
                "price_min": {"type": "number", "minimum": 0},
                "price_max": {"type": "number", "minimum": 0},
                "force": {"type": "boolean"},
            },
            "required": ["kind"],
        },
        "annotations": _SCAN,
    },
    "get_scalp_monitor": {
        "fn": get_scalp_monitor,
        "description": (
            "Scalp page Monitor: F&O stocks scored for scalping now (ATR %, spread %, ₹ traded, score, ready flag), "
            "best first. Filters as on the page: min_atr_pct (default 0.08), max_spread_pct (0.05), min_value_cr "
            "(5, ₹ crore traded today), only_ready."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "min_atr_pct": {"type": "number", "minimum": 0, "maximum": 5},
                "max_spread_pct": {"type": "number", "minimum": 0, "maximum": 5},
                "min_value_cr": {"type": "number", "minimum": 0},
                "only_ready": {"type": "boolean"},
                "limit": _LIMIT,
            },
        },
        "annotations": _READ,
    },
    "get_most_active": {
        "fn": get_most_active,
        "description": (
            "Scalp page Most active: F&O stocks ranked by ₹ traded (value), volume vs usual (rvol), % change or "
            "buy/sell pressure, from the last scan; bias BUY or SELL keeps one side."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "sort": {"type": "string", "enum": ["value", "rvol", "change", "pressure"]},
                "bias": {"type": "string", "enum": ["BUY", "SELL"]},
                "min_value_cr": {"type": "number", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 250},
            },
        },
        "annotations": _READ,
    },
    "get_stock_news": {
        "fn": get_stock_news,
        "description": (
            "News for one NSE stock, newest first: NSE corporate announcements (results, board meetings, orders, "
            "dividends; source NSE, with the filing's link) and Google News headlines of the last 7 days. "
            "`errors` names a source that could not be read."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 40}},
            "required": ["symbol"],
        },
        "annotations": _READ,
    },
    "list_scan_history": {
        "fn": list_scan_history,
        "description": (
            "Scalp page History: the days with saved Cross scan / Trend quality runs and one day's runs (latest "
            "day by default) with their time, choices and accuracy summary when checked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "day": {"type": "string", "description": "YYYY-MM-DD"},
                "kind": {"type": "string", "enum": ["cross", "chop"]},
            },
        },
        "annotations": _READ,
    },
    "get_scan_history": {
        "fn": get_scan_history,
        "description": "One saved scan with its rows as the page showed them, and its accuracy check (hit rates, each stock's move after the scan) when one was made.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}, "limit": _LIMIT},
            "required": ["run_id"],
        },
        "annotations": _READ,
    },
    "check_scan_accuracy": {
        "fn": check_scan_accuracy,
        "description": (
            "Start the accuracy check of a saved scan once its day has closed: downloads that day's candles again "
            "and measures each stock's move 15/30/60 min later and at the close, and the next SMA cross. Read-only. "
            "Refused on the desk during market hours. Poll get_scan_history for the result."
        ),
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "integer"}}, "required": ["run_id"]},
        "annotations": _SCAN,
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
    "get_backtest_trades": {
        "fn": get_backtest_trades,
        "description": (
            "One backtest run's trades (optionally one symbol): side, entry/exit, exit_reason, P&L, and "
            "best_points / worst_points = the most the price moved for / against the trade from entry "
            "(rupees per share). Use these to choose a stop (beyond most winners' worst_points) and a target "
            "(within reach of most winners' best_points)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"run_id": {"type": "integer"}, "symbol": {"type": "string"}},
            "required": ["run_id"],
        },
        "annotations": _READ,
    },
    "compare_backtests": {
        "fn": compare_backtests,
        "description": "Side by side: 2-6 runs' totals, stop/target settings used, and per-stock totals.",
        "inputSchema": {
            "type": "object",
            "properties": {"run_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 6}},
            "required": ["run_ids"],
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


TUNING_GUIDE = (
    "PalTra SMA trading bots. Read status, settings, trades, Scalp page scans (get_scans, get_scalp_monitor, "
    "get_most_active, scan history and accuracy) and backtests. Changes are limited to arming stocks and stop "
    "settings on PAPER bots, starting practice backtests outside market hours (09:00-15:45 IST, Mon-Fri), and "
    "running read-only scans (start_scan, check_scan_accuracy); orders and LIVE bots are not reachable.\n"
    "Tuning stop-loss and target with backtests (the person arms the stocks in the app):\n"
    "1. list_bots / get_settings for the bot: note the armed stocks and current stop settings.\n"
    "2. start_backtest on that bot and a date range; poll list_backtests until its status is FINISHED.\n"
    "3. get_backtest_trades for the run: per stock, look at winners' worst_points and best_points. A stop just "
    "beyond most winners' worst_points and a target most winners reached (e.g. their median best_points) is a "
    "reasonable candidate; leave room for charges.\n"
    "4. set_stop per stock (fixed stop + target = stop_type TSL, tsl_mode POINTS, tsl_sl_points = stop, "
    "tsl_target_points = target, tsl_trail_points = target).\n"
    "5. start_backtest again over the SAME dates and stocks, wait for FINISHED, then compare_backtests.\n"
    "6. Keep the better settings; if the new ones were worse, set_stop back to the old values. Report the "
    "before/after numbers. Settings cannot change while that bot's backtest is running. Results on past days "
    "do not guarantee future results; tuning on the same days you test on overfits, so check a second date range."
)


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
                "instructions": TUNING_GUIDE,
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
