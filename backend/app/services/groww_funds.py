"""Groww cash and MIS margin for the manual desk.

The practice wallet stays in place until this read succeeds. A failed call
never substitutes the ₹1,00,000 practice balance and labels it as Groww.

The HTTP handler must not sit on a Groww call. A cached read is returned
immediately, and a cold read gives up after a few seconds so the rest of
the desk can answer.
"""
from __future__ import annotations

import asyncio
import time

from app.brokers.base import BrokerAuthError, BrokerOrderError

_CACHE_TTL_SEC = 30.0
_WAIT_SEC = 4.0
_cache_at = 0.0
_cache: dict | None = None
_fetch_task: asyncio.Task | None = None


def clear_funds_cache() -> None:
    global _cache_at, _cache
    _cache_at = 0.0
    _cache = None


def _remember(funds: dict) -> dict:
    global _cache_at, _cache
    _cache = funds
    _cache_at = time.monotonic()
    return {"connected": True, "funds": funds, "error": None}


async def _read_funds(client) -> dict:
    if not await client.is_token_valid():
        return {
            "connected": False,
            "funds": None,
            "error": "Groww session expired. Log in again from Settings.",
        }
    try:
        funds = await asyncio.wait_for(client.get_available_margin(), timeout=_WAIT_SEC)
    except asyncio.TimeoutError:
        return {
            "connected": True,
            "funds": None,
            "error": "Groww balance did not answer. This is not a zero balance.",
        }
    except (BrokerAuthError, BrokerOrderError) as exc:
        return {"connected": True, "funds": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"connected": True, "funds": None, "error": f"Could not read the Groww balance: {exc}"}
    return _remember(funds)


def _kick(client) -> None:
    global _fetch_task
    if _fetch_task is not None and not _fetch_task.done():
        return
    _fetch_task = asyncio.create_task(_read_funds(client))


async def groww_desk_funds() -> dict:
    """``connected`` is true when a Groww session exists.

    ``funds`` is the parsed margin payload, or None when it could not be read.
    ``error`` is a short reason for the desk to show.
    """
    client = groww_client()
    if client is None:
        return {"connected": False, "funds": None, "error": None}

    now = time.monotonic()
    fresh = _cache is not None and now - _cache_at < _CACHE_TTL_SEC
    if _cache is not None and fresh:
        return {"connected": True, "funds": _cache, "error": None}
    if _cache is not None:
        _kick(client)
        return {"connected": True, "funds": _cache, "error": None}

    # One shared read. Timing out this request must not cancel that read,
    # or the next poll starts another Groww call and the box fills up.
    global _fetch_task
    if _fetch_task is None or _fetch_task.done():
        _fetch_task = asyncio.create_task(_read_funds(client))
    try:
        return await asyncio.wait_for(asyncio.shield(_fetch_task), timeout=_WAIT_SEC)
    except asyncio.TimeoutError:
        if _cache is not None:
            return {"connected": True, "funds": _cache, "error": None}
        return {
            "connected": True,
            "funds": None,
            "error": "Groww balance did not answer. This is not a zero balance.",
        }


def groww_client():
    from app.api.routes_auth import _active_clients

    return _active_clients.get("groww")
