"""The desk ↔ scanner server link. Read-only; see server_role.py.

* ``GET /api/role``: which server this is, for the page (no secrets).
* ``GET /api/link/armed`` (desk): each bot's Trade list, for the scanner's
  "Armed only" scans and armed chips. Needs ``Authorization: Bearer
  <SCANNER_LINK_TOKEN>``; exempt from the Google desk lock, which only takes a
  browser cookie. Off (404) until the secret is set.
* ``GET /api/link/desk-armed`` (scanner): asks the desk for that list.
* Arming from the scanner (bots 1-4 and research): ``GET/PUT /api/link/stock/{bot}/{symbol}``
  (quantity and trailing stop) and ``POST /api/link/arm`` on the desk, reached through the scanner's
  ``/api/link/desk-stock/…`` and ``/api/link/desk-arm``. A change on a LIVE bot needs
  ``confirm_live`` (the scanner asks the person first, like the desk); without it the desk answers
  403. The terminal's own checks still apply (one LIVE bot per stock, Trade list size).

* The **backtest server** arms on the desk the same way (its Scalp page's Main desk / This server switch):
  its scanner-side routes below answer there too, but only towards the desk (``server=desk``), with the
  same ``SCANNER_LINK_TOKEN`` set on it. Its own (PAPER) bots are armed in-process, as on the desk.

* The scanner can arm on the **backtest server** instead (``?server=backtest`` on the scanner routes):
  the same calls go to ``BACKTEST_URL``, whose desk-side routes above answer with the same
  ``SCANNER_LINK_TOKEN`` (set on all three apps). Its bots are always PAPER. A call may start the
  stopped machine, so it waits longer; the arm waits for the backtest server's start-up copy of the
  desk's settings and is remembered there, so the next start-up copy keeps it
  (``backtest_server.note_scanner_arm``).

No order and no PAPER/LIVE switch crosses the link: arming only puts a stock on a Trade list.
"""
from __future__ import annotations

import hmac
import re
import time
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import server_role

router = APIRouter(tags=["link"])

ARMED_PATH = "/api/link/armed"
_CACHE_SEC = 20.0
_cache: dict[str, tuple[float, dict]] = {}
Server = Literal["desk", "backtest"]
# The backtest server may be stopped: Fly starts it on the first request (about 10-20 s), and an
# arm then waits for its start-up copy of the desk's settings.
_BACKTEST_TIMEOUT = 60.0


@router.get("/api/role")
async def get_role():
    return server_role.describe()


def _authorized(request: Request) -> bool:
    expected = server_role.link_token()
    header = request.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return bool(expected) and bool(given) and hmac.compare_digest(given.encode(), expected.encode())


@router.get(ARMED_PATH)
async def armed(request: Request):
    if server_role.is_scanner() or not server_role.link_token():
        raise HTTPException(404, "Not found")
    if not _authorized(request):
        raise HTTPException(401, "Bad link token")
    from app.sma_host import load_terminal

    mod = load_terminal()
    if mod is None or not hasattr(mod, "armed_lists"):
        raise HTTPException(503, "The SMA terminal is not loaded.")
    return mod.armed_lists()


def _server_name(server: Server) -> str:
    return "the backtest server" if server == "backtest" else "the desk"


def _base_url(server: Server) -> str:
    if server == "backtest":
        url = server_role.backtest_url()
        if not url:
            raise HTTPException(409, "BACKTEST_URL is not set on the scanner, so it cannot reach the backtest server.")
        return url
    return server_role.desk_url()


def _timeout(server: Server, desk: float) -> float:
    return _BACKTEST_TIMEOUT if server == "backtest" else desk


async def _fetch_desk_armed(server: Server = "desk") -> dict:
    url = f"{_base_url(server)}{ARMED_PATH}"
    headers = {"Authorization": f"Bearer {server_role.link_token()}"}
    async with httpx.AsyncClient(timeout=_timeout(server, 6)) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"{_server_name(server)} answered {resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("bots"), list):
        raise RuntimeError(f"{_server_name(server)} sent an unexpected answer")
    return body


def _link_caller(server: Server = "desk") -> bool:
    """The scanner reaches the desk and the backtest server; the backtest server reaches the desk only."""
    return server_role.is_scanner() or (server_role.is_backtest() and server == "desk")


@router.get("/api/link/desk-armed")
async def desk_armed(server: Server = "desk"):
    """The desk's (or the backtest server's) Trade lists, as the scanner sees them. Cached for a few seconds."""
    if not _link_caller(server):
        raise HTTPException(404, "Only the scanner and the backtest server ask the desk.")
    if not server_role.link_token():
        return {"bots": [], "research": None, "error": "SCANNER_LINK_TOKEN is not set, so armed stocks are unknown."}
    now = time.monotonic()
    hit = _cache.get(server)
    if hit is not None and now - hit[0] < _CACHE_SEC:
        return hit[1]
    try:
        body = await _fetch_desk_armed(server)
    except HTTPException as exc:
        return {"bots": [], "research": None, "error": str(exc.detail)}
    except Exception as exc:  # noqa: BLE001
        # The token is never in the message: only which server and the status.
        where = "the backtest server's" if server == "backtest" else "the desk's"
        return {"bots": [], "research": None, "error": f"Could not read {where} armed stocks: {exc or type(exc).__name__}"[:200]}
    out = {"bots": body.get("bots", []), "research": body.get("research"), "error": None}
    _cache[server] = (now, out)
    return out


# ---- arming a bot from the scanner ---------------------------------------------------------------
# The scanner's Arm prompt (quantity, stop, trail, target, then arm) reaches the desk's bots and the
# research desk over the same token. A change on a LIVE bot needs confirm_live, sent only after the
# person confirmed in the scanner page. No order is placed: arming only puts the stock on the bot's
# Trade list; the bot orders on its next SMA cross, as when armed on the desk.

ARM_PATH = "/api/link/arm"
# News watch lists (news_lists.py) live on the desk: the scanner's Scalp page reads and adds to them here.
NEWS_LISTS = "/api/link/news-lists"
NEWS_LISTS_ADD = "/api/link/news-lists/add"
STOCK_PREFIX = "/api/link/stock/"
LIVE_REFUSED = "That bot is LIVE: confirm the LIVE arm first (confirm_live)."


def token_path(path: str) -> bool:
    """Desk paths that check a link token themselves (exempt from the Google lock)."""
    return path in (ARMED_PATH, ARM_PATH, BACKTEST_SETTINGS, BACKTEST_GROWW, NEWS_LISTS, NEWS_LISTS_ADD) or path.startswith(
        STOCK_PREFIX
    )


# ---- Desk ↔ backtest server (backtest_server.py) ----
# Separate secret (BACKTEST_LINK_TOKEN): this link carries the desk's Groww session, so the scanner's
# token never reaches it. Desk role only; 404 elsewhere or while the secret is unset.

BACKTEST_SETTINGS = "/api/link/backtest/settings"
BACKTEST_GROWW = "/api/link/backtest/groww"


def _backtest_authorized(request: Request) -> None:
    expected = server_role.backtest_link_token()
    if not server_role.is_desk() or not expected:
        raise HTTPException(404, "Not found")
    header = request.headers.get("authorization", "")
    given = header[7:].strip() if header.lower().startswith("bearer ") else ""
    if not given or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(401, "Bad link token")


@router.get(BACKTEST_SETTINGS)
async def backtest_settings(request: Request):
    """Every bot's settings row (bots 1-4, research), for the backtest server's copy. Read-only."""
    _backtest_authorized(request)
    from app.sma_host import load_terminal

    mod = load_terminal()
    if mod is None or not hasattr(mod, "export_bot_settings"):
        raise HTTPException(503, "The SMA terminal is not loaded.")
    import asyncio

    return await asyncio.to_thread(mod.export_bot_settings)


@router.get(BACKTEST_GROWW)
async def backtest_groww(request: Request):
    """The desk's current Groww access token, for past candles on the backtest server (which
    never orders and keeps it in memory only). Empty when the desk has no session today."""
    _backtest_authorized(request)
    from groww_client import desk_session_token

    import asyncio

    token = await asyncio.to_thread(desk_session_token)
    return {"token": token or ""}


def _target(raw: str) -> int | str:
    if raw == "research":
        return "research"
    if raw in ("1", "2", "3", "4", "5"):
        return int(raw)
    raise HTTPException(400, "Target is a bot 1-4 or research.")


def _stock_path(target: int | str, symbol: str) -> str:
    if target == "research":
        return f"/api/research/config/stock/{symbol}"
    return f"/api/config/stock/{symbol}" if target == 1 else f"/api/bots/{target}/config/stock/{symbol}"


def _arm_path(target: int | str) -> str:
    if target == "research":
        return "/api/research/trade-symbols"
    return "/api/trade-symbols" if target == 1 else f"/api/bots/{target}/trade-symbols"


def _desk_guard(request: Request, raw_target: str, confirm_live: bool = False) -> int | str:
    """Desk side: the link token, and a confirmed change for a LIVE bot (reads need none)."""
    if server_role.is_scanner() or not server_role.link_token():
        raise HTTPException(404, "Not found")
    if not _authorized(request):
        raise HTTPException(401, "Bad link token")
    target = _target(raw_target)
    if target != "research":
        from app.sma_host import load_terminal

        mod = load_terminal()
        if mod is None or not hasattr(mod, "armed_lists"):
            raise HTTPException(503, "The SMA terminal is not loaded.")
        modes = {b.get("bot"): b.get("mode") for b in mod.armed_lists().get("bots", [])}
        if modes.get(target) is None:
            raise HTTPException(404, "No such bot.")
        if modes.get(target) != "PAPER" and not confirm_live:
            raise HTTPException(403, LIVE_REFUSED)
    return target


async def _terminal(method: str, path: str, body: dict | None = None):
    from fastapi.responses import JSONResponse

    from app.api.routes_mcp import terminal_client

    async with terminal_client() as client:
        res = await client.request(method, path, json=body)
    try:
        content = res.json()
    except ValueError:
        content = {"detail": res.text[:300]}
    return JSONResponse(content, status_code=res.status_code)


_SYMBOL = r"^[A-Z0-9&_-]{1,30}$"


class StockChange(BaseModel):
    """Only what the Arm prompt changes: quantity and the trailing stop numbers (₹ or %)."""

    qty: int | None = Field(default=None, ge=1, le=1_000_000)
    stop_type: Literal["TSL"] | None = None
    use_stop: Literal[True] | None = None
    tsl_sl_points: float | None = Field(default=None, gt=0)
    tsl_trail_points: float | None = Field(default=None, gt=0)
    tsl_target_points: float | None = Field(default=None, ge=0)
    # The same trailing stop in % of the entry price (tsl.py, tsl_mode PERCENT); the terminal's own bounds.
    tsl_mode: Literal["POINTS", "PERCENT"] | None = None
    tsl_sl_pct: float | None = Field(default=None, gt=0, le=50)
    tsl_trail_pct: float | None = Field(default=None, gt=0, le=50)
    tsl_target_pct: float | None = Field(default=None, ge=0, le=100)


class ArmRequest(BaseModel):
    target: str
    symbol: str = Field(pattern=_SYMBOL)
    source: str | None = Field(default=None, max_length=60)
    # Required when the bot is LIVE: the person confirmed the LIVE arm in the scanner page.
    confirm_live: bool = False


@router.get(STOCK_PREFIX + "{target}/{symbol}")
async def desk_stock(target: str, symbol: str, request: Request):
    t = _desk_guard(request, target, confirm_live=True)  # reading needs no confirmation
    return await _terminal("GET", _stock_path(t, _clean_symbol(symbol)))


@router.put(STOCK_PREFIX + "{target}/{symbol}")
async def desk_stock_save(target: str, symbol: str, body: StockChange, request: Request, confirm_live: bool = False):
    t = _desk_guard(request, target, confirm_live)
    await _first_sync()
    return await _terminal("PUT", _stock_path(t, _clean_symbol(symbol)), body.model_dump(exclude_none=True))


@router.post(ARM_PATH)
async def desk_arm(body: ArmRequest, request: Request):
    t = _desk_guard(request, body.target, body.confirm_live)
    await _first_sync()
    res = await _terminal("POST", _arm_path(t), {"symbol": body.symbol, "armed": True, "source": body.source})
    if server_role.is_backtest() and res.status_code == 200:
        _remember_scanner_arm(t, body.symbol)
    return res


async def _first_sync() -> None:
    """On the backtest server: a scanner arm that woke the machine waits for its start-up copy."""
    if server_role.is_backtest():
        import backtest_server

        await backtest_server.wait_first_sync()


def _remember_scanner_arm(target: int | str, symbol: str) -> None:
    import backtest_server
    from app.sma_host import load_terminal

    mod = load_terminal()
    if mod is None or not hasattr(mod, "link_config_id"):
        return
    try:
        backtest_server.note_scanner_arm(mod.link_config_id(target), symbol)
    except (OSError, KeyError, ValueError):
        pass  # the stock is armed; only keeping it through the next start-up copy is lost


def _clean_symbol(symbol: str) -> str:
    name = symbol.strip().upper()
    if not re.fullmatch(_SYMBOL, name):
        raise HTTPException(400, "Bad stock symbol.")
    return name


# ---- scanner side: the same three calls, sent to the desk with the token ----------------------


def _scanner_only(server: Server = "desk") -> None:
    if not _link_caller(server):
        raise HTTPException(404, "Only the scanner and the backtest server arm through the desk link.")
    if not server_role.link_token():
        raise HTTPException(409, "SCANNER_LINK_TOKEN is not set on this server, so it cannot reach the desk.")


async def _to_desk(method: str, path: str, body: dict | None = None, server: Server = "desk"):
    from fastapi.responses import JSONResponse

    base = _base_url(server)
    headers = {"Authorization": f"Bearer {server_role.link_token()}"}
    try:
        async with httpx.AsyncClient(timeout=_timeout(server, 15)) as client:
            res = await client.request(method, f"{base}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach {_server_name(server)}: {type(exc).__name__}") from None
    try:
        content = res.json()
    except ValueError:
        content = {"detail": f"{_server_name(server).capitalize()} answered {res.status_code}."}
    return JSONResponse(content, status_code=res.status_code)


@router.get("/api/link/desk-stock/{target}/{symbol}")
async def scanner_stock(target: str, symbol: str, server: Server = "desk"):
    _scanner_only(server)
    return await _to_desk("GET", f"{STOCK_PREFIX}{_target(target)}/{_clean_symbol(symbol)}", server=server)


@router.put("/api/link/desk-stock/{target}/{symbol}")
async def scanner_stock_save(target: str, symbol: str, body: StockChange, confirm_live: bool = False, server: Server = "desk"):
    _scanner_only(server)
    query = "?confirm_live=true" if confirm_live else ""
    return await _to_desk(
        "PUT",
        f"{STOCK_PREFIX}{_target(target)}/{_clean_symbol(symbol)}{query}",
        body.model_dump(exclude_none=True),
        server=server,
    )


@router.post("/api/link/desk-arm")
async def scanner_arm(body: ArmRequest, server: Server = "desk"):
    _scanner_only(server)
    _target(body.target)
    out = await _to_desk("POST", ARM_PATH, body.model_dump(), server=server)
    _cache.pop(server, None)  # the armed chips update on the next read
    return out


# ---- news watch lists over the link (the Scalp page's "Watch news" on the scanner) ----------------


class NewsListAdd(BaseModel):
    symbols: list[str] = Field(min_length=1, max_length=200)
    list_id: int | None = None
    new_name: str | None = Field(None, max_length=40)


def _news_guard(request: Request) -> None:
    if not server_role.is_desk() or not server_role.link_token():
        raise HTTPException(404, "Not found")
    if not _authorized(request):
        raise HTTPException(401, "Bad link token")


async def add_to_news_list(body: NewsListAdd) -> dict:
    """Add stocks to a watch list, or to a new one named `new_name`. Information only: nothing is armed."""
    from app.services import news_lists
    from app.services.news_impact import news_watcher

    try:
        if body.list_id:
            row = await news_lists.update(body.list_id, add=body.symbols)
        else:
            row = await news_lists.create(body.new_name or "Watch list", body.symbols)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    news_watcher.kick()
    return row


@router.get(NEWS_LISTS)
async def desk_news_lists(request: Request):
    from app.services import news_lists

    _news_guard(request)
    return {"lists": await news_lists.all_lists()}


@router.post(NEWS_LISTS_ADD)
async def desk_news_lists_add(body: NewsListAdd, request: Request):
    _news_guard(request)
    return await add_to_news_list(body)


@router.get("/api/link/desk-news-lists")
async def scanner_news_lists():
    _scanner_only()
    return await _to_desk("GET", NEWS_LISTS)


@router.post("/api/link/desk-news-lists/add")
async def scanner_news_lists_add(body: NewsListAdd):
    _scanner_only()
    return await _to_desk("POST", NEWS_LISTS_ADD, body.model_dump())
