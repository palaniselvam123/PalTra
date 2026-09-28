"""Groww cash and MIS margin for the manual desk.

The practice wallet stays in place until this read succeeds. A failed call
never substitutes the ₹1,00,000 practice balance and labels it as Groww.
"""
from __future__ import annotations

import time

from app.brokers.base import BrokerAuthError, BrokerOrderError

_CACHE_TTL_SEC = 8.0
_cache_at = 0.0
_cache: dict | None = None


def clear_funds_cache() -> None:
    global _cache_at, _cache
    _cache_at = 0.0
    _cache = None


async def groww_desk_funds() -> dict:
    """``connected`` is true when a Groww session exists.

    ``funds`` is the parsed margin payload, or None when it could not be read.
    ``error`` is a short reason for the desk to show.
    """
    global _cache_at, _cache
    from app.api.routes_auth import _active_clients

    client = _active_clients.get("groww")
    if client is None:
        return {"connected": False, "funds": None, "error": None}

    now = time.monotonic()
    if _cache is not None and now - _cache_at < _CACHE_TTL_SEC:
        return {"connected": True, "funds": _cache, "error": None}

    try:
        if not await client.is_token_valid():
            return {
                "connected": False,
                "funds": None,
                "error": "Groww session expired. Log in again from Settings.",
            }
        funds = await client.get_available_margin()
    except (BrokerAuthError, BrokerOrderError) as exc:
        return {"connected": True, "funds": None, "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"connected": True, "funds": None, "error": f"Could not read the Groww balance: {exc}"}

    _cache = funds
    _cache_at = now
    return {"connected": True, "funds": funds, "error": None}


def groww_client():
    from app.api.routes_auth import _active_clients

    return _active_clients.get("groww")
