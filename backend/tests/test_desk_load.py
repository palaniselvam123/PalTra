"""A closed market and a slow Groww balance must not stall the desk."""
from __future__ import annotations

import asyncio
import time

import pytest

from app.services.market_data import broker_poll_due


def test_depth_quotes_wait_until_the_open():
    assert broker_poll_due(True) is True
    assert broker_poll_due(False) is False


@pytest.mark.asyncio
async def test_cached_balance_does_not_call_groww(monkeypatch):
    from app.services import groww_funds as funds

    funds.clear_funds_cache()
    funds._cache = {"clear_cash": 10.0}
    funds._cache_at = time.monotonic()
    called = {"n": 0}

    class Client:
        async def is_token_valid(self):
            called["n"] += 1
            return True

        async def get_available_margin(self):
            called["n"] += 1
            return {"clear_cash": 1.0}

    monkeypatch.setattr(funds, "groww_client", lambda: Client())
    result = await funds.groww_desk_funds()
    assert result["funds"]["clear_cash"] == 10.0
    assert called["n"] == 0
    funds.clear_funds_cache()


@pytest.mark.asyncio
async def test_a_hung_balance_read_gives_up(monkeypatch):
    from app.services import groww_funds as funds

    funds.clear_funds_cache()
    funds._fetch_task = None
    monkeypatch.setattr(funds, "_WAIT_SEC", 0.05)

    class Client:
        async def is_token_valid(self):
            return True

        async def get_available_margin(self):
            await asyncio.sleep(30)
            return {"clear_cash": 1.0}

    monkeypatch.setattr(funds, "groww_client", lambda: Client())
    started = time.monotonic()
    result = await funds.groww_desk_funds()
    assert time.monotonic() - started < 1
    assert result["funds"] is None
    assert "did not answer" in result["error"]
    task = funds._fetch_task
    if task is not None and not task.done():
        task.cancel()
    funds.clear_funds_cache()
    funds._fetch_task = None
