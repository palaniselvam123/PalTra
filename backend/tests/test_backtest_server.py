"""Backtest server (PALTRA_ROLE=backtest): never an order, no live bots; settings and the Groww session
from the desk over its own link; stops itself when idle. No real Groww call: the desk is faked."""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import backtest_server
import server_role
from app.api import routes_link

TOKEN = "b" * 32


@pytest.fixture
def backtest(monkeypatch):
    monkeypatch.setenv("PALTRA_ROLE", "backtest")


@pytest.fixture(autouse=True)
def fresh_state(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_SETTINGS_FILE", str(tmp_path / "backtest_server.json"))
    backtest_server.state.reset()
    yield
    backtest_server.state.reset()


def _no_sdk(*_a, **_k):
    raise AssertionError("the backtest server reached the Groww SDK")


def test_role_never_orders_and_never_scans(monkeypatch, backtest):
    monkeypatch.setenv("SCANS", "on")
    assert server_role.role() == "backtest" and server_role.is_backtest() and not server_role.is_desk()
    assert server_role.no_orders() and not server_role.scans_enabled()
    with pytest.raises(server_role.ScannerNoOrders, match="backtest server"):
        server_role.refuse_orders()
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    assert TOKEN not in str(server_role.describe())
    monkeypatch.setenv("PALTRA_ROLE", "desk")
    assert server_role.is_desk() and not server_role.no_orders()
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", "short")
    assert server_role.backtest_link_token() == ""


def test_sma_client_never_orders_on_the_backtest_server(monkeypatch, backtest):
    from groww_client import GrowwClient

    client = GrowwClient(mode="LIVE", token="test-token")
    monkeypatch.setattr(client, "_require_sdk", _no_sdk)
    for call in (
        client.place_entry("RELIANCE", "BUY", 1, 1200.0),
        client.place_exit("RELIANCE", "SELL", 1, 1200.0),
        client.place_sl("RELIANCE", "SELL", 1, 1190.0),
    ):
        with pytest.raises(server_role.ScannerNoOrders):
            asyncio.run(call)


def test_desk_broker_never_orders_on_the_backtest_server(monkeypatch, backtest):
    from app.brokers.base import BrokerOrderError
    from app.brokers.groww_client import GrowwClient

    client = GrowwClient()
    monkeypatch.setattr(client, "_require_session", _no_sdk)
    with pytest.raises(BrokerOrderError, match="backtest server"):
        asyncio.run(client.cancel_order("GW-1"))


def test_quotes_use_the_desk_session_from_the_link(monkeypatch, backtest):
    from groww_client import desk_session_token

    backtest_server.state.token = "desk-session"
    assert desk_session_token() == "desk-session"
    monkeypatch.setenv("PALTRA_ROLE", "desk")
    assert backtest_server.remote_token() == ""


# ---- idle stop ----


class _Replay:
    def __init__(self, status: str):
        self.status = status


def test_idle_time_and_busy_backtests(monkeypatch):
    assert backtest_server.idle_minutes() == 30
    assert backtest_server.set_idle_minutes(5) == 10 and backtest_server.idle_minutes() == 10
    assert backtest_server.set_idle_minutes(999) == 240
    backtest_server.set_idle_minutes(10)
    now = time.monotonic()
    backtest_server.state.last_request = backtest_server.state.last_busy = now - 9 * 60
    idle = {1: _Replay("FINISHED"), 2: _Replay("PAUSED")}
    assert 50 < backtest_server.idle_seconds_left(idle, now) <= 60
    assert backtest_server.idle_seconds_left(idle, now + 120) == 0
    playing = {1: _Replay("PLAYING")}
    assert backtest_server.busy(playing) and backtest_server.idle_seconds_left(playing, now + 120) == 600
    backtest_server.note_request("/api/health")
    assert backtest_server.state.last_request < now
    backtest_server.note_request("/sma/api/state")
    assert backtest_server.state.last_request >= now


def test_run_stops_the_server_once_idle(monkeypatch, backtest):
    stopped = []
    monkeypatch.setattr(backtest_server, "CHECK_EVERY_SEC", 0)
    monkeypatch.setattr(backtest_server, "idle_minutes", lambda: 0)
    monkeypatch.setattr(backtest_server, "stop_now", lambda reason: stopped.append(reason))
    monkeypatch.delenv("BACKTEST_LINK_TOKEN", raising=False)
    asyncio.run(asyncio.wait_for(backtest_server.run(lambda rows: [], lambda: {}), 5))
    assert stopped and "idle" in stopped[0]
    assert "BACKTEST_LINK_TOKEN" in (backtest_server.state.sync_error or "")


# ---- the link, backtest side ----


def _desk(settings=None, token="desk-session", status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if status != 200:
            return httpx.Response(status)
        if request.url.path == backtest_server.GROWW_PATH:
            return httpx.Response(200, json={"token": token})
        return httpx.Response(200, json={"rows": settings if settings is not None else {"1": {"qty": 5}}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_sync_and_token_from_the_desk(monkeypatch, backtest):
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    applied = []

    async def go():
        async with _desk() as client:
            assert await backtest_server.refresh_token(client)
            return await backtest_server.sync(lambda rows: applied.append(rows) or ["1"], client)

    assert asyncio.run(go()) == {"synced": ["1"]}
    assert applied == [{"1": {"qty": 5}}] and backtest_server.state.token == "desk-session"
    status = backtest_server.status({})
    assert status["groww"] and status["synced"] == ["1"] and "desk-session" not in str(status)


def test_link_failures_keep_the_last_copy_and_say_why(monkeypatch, backtest):
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    backtest_server.state.token = "older"

    async def go():
        async with _desk(status=503) as client:
            assert not await backtest_server.refresh_token(client)
            with pytest.raises(RuntimeError):
                await backtest_server.sync(lambda rows: ["1"], client)

    asyncio.run(go())
    assert backtest_server.state.token == "older"
    assert "503" in backtest_server.state.sync_error and TOKEN not in backtest_server.state.sync_error

    async def no_session():
        async with _desk(token="") as client:
            return await backtest_server.refresh_token(client)

    assert asyncio.run(no_session()) is False
    assert "log in to Groww on the desk" in backtest_server.state.token_error


# ---- the link, desk side ----


class _Terminal:
    @staticmethod
    def export_bot_settings():
        return {"rows": {"1": {"id": 1, "qty": 7}}}


@pytest.fixture
def link(monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setattr("app.sma_host.load_terminal", lambda: _Terminal)
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-session")
    app = FastAPI()
    app.include_router(routes_link.router)
    return TestClient(app)


def test_desk_link_needs_its_own_token(link, monkeypatch):
    good = {"Authorization": f"Bearer {TOKEN}"}
    for path in (routes_link.BACKTEST_SETTINGS, routes_link.BACKTEST_GROWW):
        monkeypatch.delenv("BACKTEST_LINK_TOKEN", raising=False)
        assert link.get(path, headers=good).status_code == 404
        monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
        assert link.get(path).status_code == 401
        assert link.get(path, headers={"Authorization": "Bearer " + "x" * 32}).status_code == 401
        # The scanner's token is not this link's token.
        monkeypatch.setenv("SCANNER_LINK_TOKEN", "s" * 32)
        assert link.get(path, headers={"Authorization": "Bearer " + "s" * 32}).status_code == 401
    assert link.get(routes_link.BACKTEST_SETTINGS, headers=good).json() == {"rows": {"1": {"id": 1, "qty": 7}}}
    assert link.get(routes_link.BACKTEST_GROWW, headers=good).json() == {"token": "desk-session"}
    for other in ("scanner", "backtest"):
        monkeypatch.setenv("PALTRA_ROLE", other)
        assert link.get(routes_link.BACKTEST_GROWW, headers=good).status_code == 404


def test_desk_link_paths_pass_the_google_lock():
    assert routes_link.token_path(routes_link.BACKTEST_SETTINGS)
    assert routes_link.token_path(routes_link.BACKTEST_GROWW)
    assert not routes_link.token_path("/api/link/backtest/other")


# ---- the terminal on the backtest server ----


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


def test_settings_round_trip_forces_paper(sma_api, monkeypatch):
    import main
    from database import session_factory
    from models import BotConfig

    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    assert sma_api.put("/api/config", json={"qty": 9}).status_code == 200
    exported = main.export_bot_settings()["rows"]
    assert exported["1"]["qty"] == 9
    with pytest.raises(RuntimeError):
        main.import_bot_settings(exported)  # never on the desk

    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    rows = {k: dict(v) for k, v in exported.items()}
    rows["1"].update(qty=11, trading_mode="LIVE", trade_symbols="RELIANCE,TCS", not_a_column=1)
    rows["99"] = {"qty": 1}
    stamp = next((k for k, v in rows["1"].items() if isinstance(v, str) and "T" in v and v[:4].isdigit()), None)
    done = main.import_bot_settings(rows)
    assert "1" in done and "99" not in done
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        assert row.qty == 11 and row.trading_mode == "PAPER" and row.trade_symbols == "RELIANCE,TCS"
        if stamp:
            assert isinstance(getattr(row, stamp), dt.datetime)


def test_backtest_server_routes(sma_api, monkeypatch):
    import main

    assert sma_api.get("/api/backtest/status").status_code == 404  # desk
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    status = sma_api.get("/api/backtest/status").json()
    assert status["role"] == "backtest" and status["idle_min"] == 30
    assert sma_api.put("/api/backtest/idle", json={"minutes": 60}).json()["idle_min"] == 60
    assert sma_api.put("/api/backtest/idle", json={"minutes": 5}).status_code == 422
    # The bots trade on the desk only; LIVE is refused like on the scanner.
    assert sma_api.post("/api/bot/start").status_code == 409
    refused = sma_api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert refused.status_code == 403 and "backtest server" in refused.json()["detail"]

    stopped = []
    monkeypatch.setattr(backtest_server, "stop_now", lambda reason: stopped.append(reason))
    monkeypatch.setattr(main, "replays", {1: _Replay("PLAYING")})
    assert sma_api.post("/api/backtest/stop").status_code == 409
    assert sma_api.post("/api/backtest/sync").status_code == 409
    assert sma_api.post("/api/backtest/stop?force=true").json() == {"stopping": True}
    monkeypatch.setattr(main, "replays", {1: _Replay("FINISHED")})
    assert sma_api.post("/api/backtest/stop").json() == {"stopping": True}
    time.sleep(1.3)
    assert len(stopped) == 2

    async def no_desk(apply, client=None):
        raise RuntimeError("the desk answered 503")

    monkeypatch.setattr(backtest_server, "sync", no_desk)
    failed = sma_api.post("/api/backtest/sync")
    assert failed.status_code == 502 and "503" in failed.json()["detail"]


# ---- arming on the backtest server from the scanner ------------------------------------------


def test_scanner_arm_list_survives_the_idle_setting():
    backtest_server.set_idle_minutes(45)
    backtest_server.note_scanner_arm(4, "infy")
    backtest_server.note_scanner_arm(4, "INFY")
    backtest_server.note_scanner_arm(1, "TCS")
    assert backtest_server.scanner_armed() == {4: ["INFY"], 1: ["TCS"]}
    assert backtest_server.idle_minutes() == 45
    backtest_server.set_idle_minutes(60)
    assert backtest_server.scanner_armed() == {4: ["INFY"], 1: ["TCS"]}
    backtest_server.clear_scanner_armed()
    assert backtest_server.scanner_armed() == {} and backtest_server.idle_minutes() == 60


def test_boot_copy_keeps_scanner_arms_and_waits(monkeypatch, backtest):
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    backtest_server.note_scanner_arm(4, "INFY")
    seen = []

    def apply(rows, keep=None):
        seen.append(keep)
        return ["4"]

    async def go():
        async def fake_get(path, client=None):
            if path == backtest_server.GROWW_PATH:
                return {"token": ""}
            return {"rows": {"4": {"qty": 1}}}

        monkeypatch.setattr(backtest_server, "_desk_get", fake_get)
        monkeypatch.setattr(backtest_server, "CHECK_EVERY_SEC", 60)
        task = asyncio.create_task(backtest_server.run(apply, lambda: {}))
        await asyncio.wait_for(backtest_server.wait_first_sync(), 5)
        assert backtest_server.state.first_sync_done
        task.cancel()

    asyncio.run(go())
    assert seen == [{4: ["INFY"]}]


def test_import_keeps_scanner_arms_still_armed_here(sma_api, monkeypatch):
    import json

    import main
    from database import session_factory
    from models import BotConfig

    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    rid = main.link_config_id(3)
    main.ensure_bot_config(3)
    with session_factory()() as db:
        row = db.get(BotConfig, rid)
        row.trade_symbols = "INFY,SBIN"
        row.trade_sources = json.dumps({"INFY": "Trend quality", "SBIN": "Manual"})
        row.stock_settings = json.dumps({"INFY": {"qty": 7, "stop_type": "TSL"}})
        db.commit()
    desk_rows = {str(rid): {"qty": 2, "trade_symbols": "RELIANCE", "trade_sources": "{}", "stock_settings": json.dumps({"RELIANCE": {"qty": 3}})}}
    # TCS was armed from the scanner but has since been unarmed here: it is dropped.
    main.import_bot_settings(desk_rows, keep={rid: ["INFY", "TCS"]})
    with session_factory()() as db:
        row = db.get(BotConfig, rid)
        assert row.trade_symbols == "RELIANCE,INFY" and row.trading_mode == "PAPER"
        assert json.loads(row.trade_sources) == {"INFY": "Trend quality"}
        assert json.loads(row.stock_settings) == {"RELIANCE": {"qty": 3}, "INFY": {"qty": 7, "stop_type": "TSL"}}
    assert backtest_server.scanner_armed() == {rid: ["INFY"]}
    # SBIN (armed here by hand, not from the scanner) follows the desk; a full copy keeps nothing.
    main.import_bot_settings(desk_rows)
    with session_factory()() as db:
        assert db.get(BotConfig, rid).trade_symbols == "RELIANCE"


def test_manual_sync_clears_scanner_arms(sma_api, monkeypatch):
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    backtest_server.note_scanner_arm(1, "INFY")

    async def ok(apply, client=None, keep=None):
        assert keep is None
        return {"synced": ["1"]}

    async def no_token(client=None):
        return False

    monkeypatch.setattr(backtest_server, "sync", ok)
    monkeypatch.setattr(backtest_server, "refresh_token", no_token)
    assert sma_api.get("/api/backtest/status").json()["scanner_armed"] == 1
    assert sma_api.post("/api/backtest/sync").status_code == 200
    assert backtest_server.scanner_armed() == {}


LINK = "s" * 32


def test_scanner_arms_on_the_backtest_server(monkeypatch):
    monkeypatch.setenv("PALTRA_ROLE", "scanner")
    monkeypatch.setenv("SCANNER_LINK_TOKEN", LINK)
    monkeypatch.setattr(routes_link, "_cache", {})
    app = FastAPI()
    app.include_router(routes_link.router)
    http = TestClient(app)
    monkeypatch.delenv("BACKTEST_URL", raising=False)
    refused = http.post("/api/link/desk-arm?server=backtest", json={"target": "3", "symbol": "INFY"})
    assert refused.status_code == 409 and "BACKTEST_URL" in refused.json()["detail"]
    assert "BACKTEST_URL" in http.get("/api/link/desk-armed?server=backtest").json()["error"]

    monkeypatch.setenv("BACKTEST_URL", "https://bt.example")
    sent = []

    class _Client:
        def __init__(self, timeout=None):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def request(self, method, url, headers=None, json=None):
            sent.append((method, url, json, self.timeout, headers["Authorization"]))
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(routes_link.httpx, "AsyncClient", _Client)
    assert http.post("/api/link/desk-arm?server=backtest", json={"target": "3", "symbol": "INFY"}).status_code == 200
    http.get("/api/link/desk-stock/3/infy?server=backtest")
    http.post("/api/link/desk-arm", json={"target": "3", "symbol": "INFY"})
    assert sent[0][:2] == ("POST", "https://bt.example/api/link/arm") and sent[0][3] == 60.0
    assert sent[0][4] == f"Bearer {LINK}"
    assert sent[1][:2] == ("GET", "https://bt.example/api/link/stock/3/INFY")
    assert sent[2][1].startswith(server_role.desk_url()) and sent[2][3] == 15
    assert http.post("/api/link/desk-arm?server=elsewhere", json={"target": "3", "symbol": "INFY"}).status_code == 422


def test_backtest_server_takes_a_scanner_arm_and_remembers_it(monkeypatch, backtest):
    from fastapi import FastAPI as _FastAPI

    monkeypatch.setenv("SCANNER_LINK_TOKEN", LINK)
    term = _FastAPI()
    calls = []

    @term.post("/api/bots/{bot}/trade-symbols")
    def arm_bot(bot: int, body: dict):
        calls.append((bot, body["symbol"]))
        return {"trade_symbols": [body["symbol"]]}

    class _Mod:
        app = term

        @staticmethod
        def armed_lists():
            return {"bots": [{"bot": 3, "name": "Bot 3", "mode": "PAPER", "armed": []}]}

        @staticmethod
        def link_config_id(target):
            return 4

    monkeypatch.setattr("app.sma_host.load_terminal", lambda: _Mod)
    app = FastAPI()
    app.include_router(routes_link.router)
    http = TestClient(app)
    backtest_server.state.first_sync_done = True
    res = http.post("/api/link/arm", headers={"Authorization": f"Bearer {LINK}"}, json={"target": "3", "symbol": "INFY"})
    assert res.status_code == 200 and calls == [(3, "INFY")]
    assert backtest_server.scanner_armed() == {4: ["INFY"]}
    # The backtest server's own link token does not open the scanner's routes.
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    assert http.post("/api/link/arm", headers={"Authorization": f"Bearer {TOKEN}"}, json={"target": "3", "symbol": "TCS"}).status_code == 401
