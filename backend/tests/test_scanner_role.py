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
    monkeypatch.setattr(routes_link, "_cache", {})
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

    async def fake_fetch(server="desk"):
        return _Terminal.armed_lists()

    monkeypatch.setattr(routes_link, "_fetch_desk_armed", fake_fetch)
    body = link.get("/api/link/desk-armed").json()
    assert body["error"] is None
    assert body["research"] == ["TCS"]


def test_scanner_link_failure_is_reported_without_the_token(link, monkeypatch, scanner):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)

    async def down(server="desk"):
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


# ---- arming from the scanner (PAPER and LIVE; LIVE only with confirm_live) -------------------


def _arm_terminal(modes: dict[int, str]):
    """A stand-in SMA terminal: bot modes and the calls the link makes into it."""
    from fastapi import FastAPI as _FastAPI

    calls: list = []
    term = _FastAPI()

    @term.get("/api/config/stock/{symbol}")
    def stock1(symbol: str):
        calls.append(("GET", 1, symbol))
        return {"symbol": symbol, "qty": 5}

    @term.put("/api/bots/{bot}/config/stock/{symbol}")
    def stock_bot(bot: int, symbol: str, body: dict):
        calls.append(("PUT", bot, symbol, body))
        return {"symbol": symbol, **body}

    @term.post("/api/trade-symbols")
    def arm1(body: dict):
        calls.append(("ARM", 1, body))
        return {"trade_symbols": [body["symbol"]]}

    @term.post("/api/bots/{bot}/trade-symbols")
    def arm_bot(bot: int, body: dict):
        calls.append(("ARM", bot, body))
        return {"trade_symbols": [body["symbol"]]}

    class _Mod:
        app = term

        @staticmethod
        def armed_lists():
            return {"bots": [{"bot": n, "name": f"Bot {n}", "mode": m, "armed": []} for n, m in modes.items()]}

    return _Mod, calls


@pytest.fixture
def arm_link(monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)
    mod, calls = _arm_terminal({1: "PAPER", 2: "LIVE"})
    monkeypatch.setattr("app.sma_host.load_terminal", lambda: mod)
    app = FastAPI()
    app.include_router(routes_link.router)
    return TestClient(app), calls


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_desk_arms_a_paper_bot_over_the_link(arm_link):
    http, calls = arm_link
    assert http.get("/api/link/stock/1/infy", headers=AUTH).json()["qty"] == 5
    res = http.post("/api/link/arm", headers=AUTH, json={"target": "1", "symbol": "INFY", "source": "Trend quality"})
    assert res.status_code == 200
    assert ("ARM", 1, {"symbol": "INFY", "armed": True, "source": "Trend quality"}) in calls


def test_desk_refuses_a_live_arm_without_confirmation(arm_link):
    http, calls = arm_link
    refused = http.post("/api/link/arm", headers=AUTH, json={"target": "2", "symbol": "TCS"})
    assert refused.status_code == 403 and "confirm" in refused.json()["detail"]
    assert http.put("/api/link/stock/2/TCS", headers=AUTH, json={"qty": 3}).status_code == 403
    assert not [c for c in calls if c[0] in ("ARM", "PUT")]
    # Reading a LIVE bot's stock settings needs no confirmation.
    assert http.get("/api/link/stock/1/TCS", headers=AUTH).status_code == 200


def test_desk_arms_a_live_bot_when_confirmed(arm_link):
    http, calls = arm_link
    saved = http.put("/api/link/stock/2/TCS?confirm_live=true", headers=AUTH, json={"qty": 3, "stop_type": "TSL", "use_stop": True, "tsl_sl_points": 2, "tsl_trail_points": 1, "tsl_target_points": 0})
    assert saved.status_code == 200
    armed = http.post("/api/link/arm", headers=AUTH, json={"target": "2", "symbol": "TCS", "confirm_live": True})
    assert armed.status_code == 200
    assert ("ARM", 2, {"symbol": "TCS", "armed": True, "source": None}) in calls


def test_arm_link_needs_the_token_and_sane_input(arm_link):
    http, calls = arm_link
    assert http.post("/api/link/arm", json={"target": "1", "symbol": "INFY"}).status_code == 401
    assert http.post("/api/link/arm", headers=AUTH, json={"target": "9", "symbol": "INFY"}).status_code == 400
    assert http.post("/api/link/arm", headers=AUTH, json={"target": "1", "symbol": "bad sym!"}).status_code == 422
    # Only quantity and trailing-stop numbers reach the bot: a mode or anything else is dropped.
    res = http.put("/api/link/stock/2/INFY?confirm_live=true", headers=AUTH, json={"qty": 4, "trading_mode": "PAPER"})
    assert res.status_code == 200
    assert ("PUT", 2, "INFY", {"qty": 4}) in calls
    # A trailing stop in % of the entry price goes through too; out-of-range numbers are refused.
    pct = {"stop_type": "TSL", "use_stop": True, "tsl_mode": "PERCENT", "tsl_sl_pct": 0.8, "tsl_trail_pct": 0.4, "tsl_target_pct": 2}
    assert http.put("/api/link/stock/2/INFY?confirm_live=true", headers=AUTH, json=pct).status_code == 200
    assert ("PUT", 2, "INFY", pct) in calls
    assert http.put("/api/link/stock/2/INFY?confirm_live=true", headers=AUTH, json={"tsl_sl_pct": 90}).status_code == 422


def test_arm_link_paths_pass_the_google_lock(monkeypatch):
    from app.core.desk_lock import desk_lock_middleware  # noqa: F401
    from app.api.routes_link import token_path

    assert token_path("/api/link/arm") and token_path("/api/link/stock/1/INFY") and token_path("/api/link/armed")
    assert not token_path("/api/link/desk-arm") and not token_path("/api/role")


def test_scanner_sends_the_live_confirmation_to_the_desk(monkeypatch, scanner):
    monkeypatch.setenv("SCANNER_LINK_TOKEN", TOKEN)
    sent = []

    async def fake_to_desk(method, path, body=None, server="desk"):
        assert server == "desk"
        sent.append((method, path, body))
        return {"ok": True}

    monkeypatch.setattr(routes_link, "_to_desk", fake_to_desk)
    app = FastAPI()
    app.include_router(routes_link.router)
    http = TestClient(app)
    http.post("/api/link/desk-arm", json={"target": "2", "symbol": "TCS", "confirm_live": True})
    http.put("/api/link/desk-stock/2/tcs?confirm_live=true", json={"qty": 2})
    http.get("/api/link/desk-stock/research/INFY")
    assert sent[0] == ("POST", "/api/link/arm", {"target": "2", "symbol": "TCS", "source": None, "confirm_live": True})
    assert sent[1] == ("PUT", "/api/link/stock/2/TCS?confirm_live=true", {"qty": 2})
    assert sent[2] == ("GET", "/api/link/stock/research/INFY", None)


def test_desk_does_not_forward_arms(link):
    assert link.post("/api/link/desk-arm", json={"target": "1", "symbol": "INFY"}).status_code == 404
