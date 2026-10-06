"""Connect Live Data must reach a LIVE feed that is already running.

The feed kept the Groww client it was started with, so after a token expired
a fresh login never cleared "Authentication failed". No order path here.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.market_data import DataSource, LiveGrowwFeed, MarketDataManager


def _live_manager(client) -> MarketDataManager:
    mgr = MarketDataManager()
    mgr._live = LiveGrowwFeed(client, ["TCS"])
    mgr._live.last_error = "Authentication failed. Your API token has either expired or is invalid."
    mgr.source = DataSource.LIVE
    return mgr


def test_a_live_feed_takes_the_new_client_and_clears_the_old_error():
    old, new = object(), object()
    mgr = _live_manager(old)
    assert mgr.adopt_client(new) is True
    assert mgr._live.client is new
    assert mgr.health().error is None


def test_the_simulated_feed_is_left_alone():
    mgr = MarketDataManager()
    assert mgr.source is DataSource.SIMULATED
    assert mgr.adopt_client(object()) is False
    assert mgr._live is None


@pytest.mark.asyncio
async def test_connect_live_data_hands_the_new_session_to_the_running_feed(monkeypatch):
    from app.api import routes_auth
    from app.services import market_data as md

    old = object()
    mgr = _live_manager(old)
    monkeypatch.setattr(md, "market_data", mgr)

    async def fake_login(self, api_key, api_secret, totp_secret):
        self._access_token = "NEW"
        return "NEW"

    monkeypatch.setattr(routes_auth.GrowwClient, "login", fake_login)
    monkeypatch.setattr(
        routes_auth, "get_vault", lambda: SimpleNamespace(decrypt=lambda v: v, encrypt=lambda v: v)
    )
    monkeypatch.setattr("groww_client.note_fresh_desk_token", lambda: None, raising=False)
    monkeypatch.setitem(routes_auth._active_clients, "groww", old)

    row = SimpleNamespace(
        api_key_encrypted="k", api_secret_encrypted="", totp_secret_encrypted="t",
        access_token_encrypted=None, token_expires_at=None,
    )

    class FakeSession:
        async def scalar(self, _query):
            return row

        async def commit(self):
            return None

    out = await routes_auth.login("groww", session=FakeSession())
    assert out["ok"] is True
    fresh = routes_auth._active_clients["groww"]
    assert fresh is not old
    assert mgr._live.client is fresh
    assert mgr.health().error is None
