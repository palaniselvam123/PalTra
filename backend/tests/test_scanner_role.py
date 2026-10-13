"""Scanner server (PALTRA_ROLE=scanner): never an order; SCANS=off on the desk; the read-only link."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import server_role
from app.api import routes_link

TOKEN = "k" * 32


@pytest.fixture
def scanner(monkeypatch):
    monkeypatch.setenv("PALTRA_ROLE", "scanner")


def _no_sdk(*_a, **_k):
    raise AssertionError("the scanner reached the Groww SDK")


def test_defaults_are_the_desk_with_scans_on(monkeypatch):
    for name in ("PALTRA_ROLE", "SCANS", "SCANNER_LINK_TOKEN", "DESK_URL"):
        monkeypatch.delenv(name, raising=False)
    assert server_role.role() == "desk"
    assert server_role.scans_enabled() is True
    assert server_role.desk_url() == "https://paltra.fly.dev"
    assert server_role.link_token() == ""
    monkeypatch.setenv("SCANNER_LINK_TOKEN", "short")
    assert server_role.link_token() == ""
    server_role.refuse_orders()  # the desk is never refused here


def test_scanner_always_scans_even_with_scans_off(monkeypatch, scanner):
    monkeypatch.setenv("SCANS", "off")
    assert server_role.scans_enabled() is True


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no"])
def test_scans_off_on_the_desk(monkeypatch, value):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("SCANS", value)
    assert server_role.scans_enabled() is False


def test_sma_client_never_orders_on_the_scanner(monkeypatch, scanner):
    from groww_client import GrowwClient

    client = GrowwClient(mode="LIVE", token="test-token")
    monkeypatch.setattr(client, "_require_sdk", _no_sdk)
    with pytest.raises(server_role.ScannerNoOrders):
        asyncio.run(client.place_entry("RELIANCE", "BUY", 1, 1200.0))
    with pytest.raises(server_role.ScannerNoOrders):
        asyncio.run(client.place_exit("RELIANCE", "SELL", 1, 1200.0))
    with pytest.raises(server_role.ScannerNoOrders):
        asyncio.run(client.place_sl("RELIANCE", "SELL", 1, 1190.0))
    with pytest.raises(server_role.ScannerNoOrders):
        asyncio.run(client.modify_sl("GW-1", "RELIANCE", "SELL", 1, 1195.0))
    with pytest.raises(server_role.ScannerNoOrders):
        asyncio.run(client.cancel_order("GW-1"))


def test_sma_client_paper_still_fills_on_the_scanner(scanner):
    from groww_client import GrowwClient

    client = GrowwClient(mode="PAPER")
    ack = asyncio.run(client.place_entry("RELIANCE", "BUY", 1, 1200.0))
    assert ack.order_id.startswith("PAPER")


def test_desk_broker_never_orders_on_the_scanner(monkeypatch, scanner):
    from app.brokers.base import BrokerOrderError, OrderRequest
    from app.brokers.groww_client import GrowwClient

    client = GrowwClient()
    monkeypatch.setattr(client, "_require_session", _no_sdk)
    order = OrderRequest.__new__(OrderRequest)
    with pytest.raises(BrokerOrderError, match="scanner server"):
        asyncio.run(client.place_order(order))
    with pytest.raises(BrokerOrderError, match="scanner server"):
        asyncio.run(client.cancel_order("GW-1"))
    with pytest.raises(BrokerOrderError, match="scanner server"):
        asyncio.run(client.square_off_all())


@pytest.fixture
def sma_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from main import app

    with TestClient(app) as client:
        yield client
    database.reset_engine()
    get_settings.cache_clear()


def test_scanner_refuses_live_on_every_bot(sma_api, monkeypatch, scanner):
    monkeypatch.setattr("main.preferred_quote_token", lambda: "desk-token")
    refused = sma_api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert refused.status_code == 403
    assert "never sends orders" in refused.json()["detail"]
    assert sma_api.post("/api/bots/2/mode", json={"mode": "LIVE", "confirm_live": True}).status_code == 403
    assert sma_api.get("/api/config").json()["trading_mode"] == "PAPER"
    assert sma_api.post("/api/mode", json={"mode": "PAPER"}).status_code == 200


def test_desk_with_scans_off_keeps_terminal_scans_off(sma_api, monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("SCANS", "off")
    assert sma_api.get("/api/scans/pause").json()["scans_off"] is True
    resumed = sma_api.put("/api/scans/pause", json={"paused": False})
    assert resumed.status_code == 409
    started = sma_api.post("/api/cross-scan/start", json={"symbols": ["RELIANCE"], "minutes": 5})
    assert started.status_code == 409
    assert "scanner server" in started.json()["detail"]


def test_manual_desk_refuses_groww_execution_on_the_scanner(monkeypatch, scanner):
    from app import state
    from app.api import routes_manual

    app = FastAPI()
    app.include_router(routes_manual.router)
    res = TestClient(app).post("/api/manual/execution", json={"mode": "groww", "confirm_live": True})
    assert res.status_code == 403
    assert state.manual_live is False


def test_desk_scalp_pause_cannot_resume_with_scans_off(monkeypatch):
    from app.api import routes_scalp
    from app.services.active_stocks import active_scanner

    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("SCANS", "off")
    monkeypatch.setattr(active_scanner, "paused", False)
    app = FastAPI()
    app.include_router(routes_scalp.router)
    http = TestClient(app)
    assert http.get("/api/scalp/pause").json() == {"paused": True, "scans_off": True}
    assert http.post("/api/scalp/pause", json={"paused": False}).status_code == 409
    assert http.post("/api/scalp/pause", json={"paused": True}).status_code == 200


# ---- the read-only link ------------------------------------------------------------------------


class _Terminal:
    @staticmethod
    def armed_lists():
        return {"bots": [{"bot": 1, "name": "Bot 1", "mode": "LIVE", "armed": ["RELIANCE"]}], "research": ["TCS"]}


@pytest.fixture
def link(monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setattr("app.sma_host.load_terminal", lambda: _Terminal)
    monkeypatch.setattr(routes_link, "_cache", None)
    app = FastAPI()
    app.include_router(routes_link.router)
    return TestClient(app)


def test_role_endpoint_has_no_secrets(link, monkeypatch):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)
    body = link.get("/api/role").json()
    assert body["role"] == "desk"
    assert TOKEN not in str(body)


def test_armed_link_is_off_without_a_token(link, monkeypatch):
    monkeypatch.delenv("SCANNER_LINK_TOKEN", raising=False)
    assert link.get("/api/link/armed", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_armed_link_needs_the_token(link, monkeypatch):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)
    assert link.get("/api/link/armed").status_code == 401
    assert link.get("/api/link/armed", headers={"Authorization": "Bearer " + "x" * 32}).status_code == 401
    ok = link.get("/api/link/armed", headers={"Authorization": f"Bearer {TOKEN}"})
    assert ok.status_code == 200
    assert ok.json()["bots"][0]["armed"] == ["RELIANCE"]


def test_scanner_does_not_serve_the_armed_link(link, monkeypatch, scanner):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)
    assert link.get("/api/link/armed", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 404


def test_scanner_reads_the_desk_armed_lists(link, monkeypatch, scanner):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)

    async def fake_fetch():
        return _Terminal.armed_lists()

    monkeypatch.setattr(routes_link, "_fetch_desk_armed", fake_fetch)
    body = link.get("/api/link/desk-armed").json()
    assert body["error"] is None
    assert body["research"] == ["TCS"]


def test_scanner_link_failure_is_reported_without_the_token(link, monkeypatch, scanner):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)

    async def down():
        raise RuntimeError("the desk answered 503")

    monkeypatch.setattr(routes_link, "_fetch_desk_armed", down)
    body = link.get("/api/link/desk-armed").json()
    assert body["bots"] == [] and "503" in body["error"]
    assert TOKEN not in body["error"]


def test_desk_does_not_ask_itself(link):
    assert link.get("/api/link/desk-armed").status_code == 404


def test_armed_link_passes_the_google_lock_but_role_does_not(monkeypatch):
    from app.core.desk_lock import install_desk_lock

    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "paltra-client")
    monkeypatch.setenv("GOOGLE_ALLOWED_EMAILS", "owner@gmail.com")
    monkeypatch.setenv("ENCRYPTION_KEY", "test-signing-key")
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)
    monkeypatch.setattr("app.sma_host.load_terminal", lambda: _Terminal)
    app = FastAPI()
    app.include_router(routes_link.router)
    install_desk_lock(app)
    http = TestClient(app)
    assert http.get("/api/role").status_code == 401
    assert http.get("/api/link/armed").status_code == 401  # its own token check, not the cookie
    assert http.get("/api/link/armed", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200
    assert http.get("/api/link/desk-armed").status_code == 401
