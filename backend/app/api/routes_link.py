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
_cache: tuple[float, dict] | None = None


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


async def _fetch_desk_armed() -> dict:
    url = f"{server_role.desk_url()}{ARMED_PATH}"
    headers = {"Authorization": f"Bearer {server_role.link_token()}"}
    async with httpx.AsyncClient(timeout=6) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"the desk answered {resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict) or not isinstance(body.get("bots"), list):
        raise RuntimeError("the desk sent an unexpected answer")
    return body


@router.get("/api/link/desk-armed")
async def desk_armed():
    """The desk's Trade lists, as the scanner sees them. Cached for a few seconds."""
    global _cache
    if not server_role.is_scanner():
        raise HTTPException(404, "Only the scanner server asks the desk.")
    if not server_role.link_token():
        return {"bots": [], "research": None, "error": "SCANNER_LINK_TOKEN is not set, so armed stocks are unknown."}
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < _CACHE_SEC:
        return _cache[1]
    try:
        body = await _fetch_desk_armed()
    except Exception as exc:  # noqa: BLE001
        # The token is never in the message: only the desk URL and the status.
        return {"bots": [], "research": None, "error": f"Could not read the desk's armed stocks: {exc}"[:200]}
    out = {"bots": body.get("bots", []), "research": body.get("research"), "error": None}
    _cache = (now, out)
    return out


# ---- arming a bot from the scanner ---------------------------------------------------------------
# The scanner's Arm prompt (quantity, stop, trail, target, then arm) reaches the desk's bots and the
# research desk over the same token. A change on a LIVE bot needs confirm_live, sent only after the
# person confirmed in the scanner page. No order is placed: arming only puts the stock on the bot's
# Trade list; the bot orders on its next SMA cross, as when armed on the desk.

ARM_PATH = "/api/link/arm"
STOCK_PREFIX = "/api/link/stock/"
LIVE_REFUSED = "That bot is LIVE: confirm the LIVE arm first (confirm_live)."


def token_path(path: str) -> bool:
    """Desk paths that check SCANNER_LINK_TOKEN themselves (exempt from the Google lock)."""
    return path in (ARMED_PATH, ARM_PATH) or path.startswith(STOCK_PREFIX)


def _target(raw: str) -> int | str:
    if raw == "research":
        return "research"
    if raw in ("1", "2", "3", "4"):
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
    """Only what the Arm prompt changes: quantity and the trailing stop numbers."""

    qty: int | None = Field(default=None, ge=1, le=1_000_000)
    stop_type: Literal["TSL"] | None = None
    use_stop: Literal[True] | None = None
    tsl_sl_points: float | None = Field(default=None, gt=0)
    tsl_trail_points: float | None = Field(default=None, gt=0)
    tsl_target_points: float | None = Field(default=None, ge=0)


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
    return await _terminal("PUT", _stock_path(t, _clean_symbol(symbol)), body.model_dump(exclude_none=True))


@router.post(ARM_PATH)
async def desk_arm(body: ArmRequest, request: Request):
    t = _desk_guard(request, body.target, body.confirm_live)
    return await _terminal("POST", _arm_path(t), {"symbol": body.symbol, "armed": True, "source": body.source})


def _clean_symbol(symbol: str) -> str:
    name = symbol.strip().upper()
    if not re.fullmatch(_SYMBOL, name):
        raise HTTPException(400, "Bad stock symbol.")
    return name


# ---- scanner side: the same three calls, sent to the desk with the token ----------------------


def _scanner_only() -> None:
    if not server_role.is_scanner():
        raise HTTPException(404, "Only the scanner server arms through the desk link.")
    if not server_role.link_token():
        raise HTTPException(409, "SCANNER_LINK_TOKEN is not set, so the scanner cannot reach the desk.")


async def _to_desk(method: str, path: str, body: dict | None = None):
    from fastapi.responses import JSONResponse

    headers = {"Authorization": f"Bearer {server_role.link_token()}"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.request(method, f"{server_role.desk_url()}{path}", headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Could not reach the desk: {type(exc).__name__}") from None
    try:
        content = res.json()
    except ValueError:
        content = {"detail": f"The desk answered {res.status_code}."}
    return JSONResponse(content, status_code=res.status_code)


@router.get("/api/link/desk-stock/{target}/{symbol}")
async def scanner_stock(target: str, symbol: str):
    _scanner_only()
    return await _to_desk("GET", f"{STOCK_PREFIX}{_target(target)}/{_clean_symbol(symbol)}")


@router.put("/api/link/desk-stock/{target}/{symbol}")
async def scanner_stock_save(target: str, symbol: str, body: StockChange, confirm_live: bool = False):
    _scanner_only()
    query = "?confirm_live=true" if confirm_live else ""
    return await _to_desk(
        "PUT", f"{STOCK_PREFIX}{_target(target)}/{_clean_symbol(symbol)}{query}", body.model_dump(exclude_none=True)
    )


@router.post("/api/link/desk-arm")
async def scanner_arm(body: ArmRequest):
    _scanner_only()
    _target(body.target)
    out = await _to_desk("POST", ARM_PATH, body.model_dump())
    global _cache
    _cache = None  # the armed chips update on the next read
    return out
