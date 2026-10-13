"""The desk ↔ scanner server link. Read-only; see server_role.py.

* ``GET /api/role``: which server this is, for the page (no secrets).
* ``GET /api/link/armed`` (desk): each bot's Trade list, for the scanner's
  "Armed only" scans and armed chips. Needs ``Authorization: Bearer
  <SCANNER_LINK_TOKEN>``; exempt from the Google desk lock, which only takes a
  browser cookie. Off (404) until the secret is set.
* ``GET /api/link/desk-armed`` (scanner): asks the desk for that list.

No order, mode, setting or arm crosses the link.
"""
from __future__ import annotations

import hmac
import time

import httpx
from fastapi import APIRouter, HTTPException, Request

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
