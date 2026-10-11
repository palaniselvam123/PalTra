"""MCP server: token-gated, read tools, writes only on PAPER bots, no order path."""
from __future__ import annotations

import json
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_mcp

TOKEN = "t" * 32


def _fake_terminal(modes: dict[int, str]) -> tuple[FastAPI, list]:
    app = FastAPI()
    writes: list = []

    @app.get("/api/bots")
    def bots():
        return [{"bot": n, "mode": m, "armed": []} for n, m in modes.items()]

    @app.get("/api/bots/{bot}/state")
    def state(bot: int):
        return {"bot": bot, "mode": modes[bot], "bot_status": "RUNNING", "secret_internal": 1}

    @app.post("/api/bots/{bot}/trade-symbols")
    def arm(bot: int, body: dict):
        writes.append(("arm", bot, body))
        return {"trade_symbols": [body["symbol"]]}

    @app.get("/api/replay/runs")
    def runs(limit: int = 50):
        writes.append(("runs_limit", limit))
        return [
            {"id": 9, "settings": {"bot": 2, "qty": 1}, "totals": {"net": 120.0}, "status": "FINISHED"},
            {"id": 8, "settings": {}, "totals": {"net": -40.0}, "status": "FINISHED"},
            {"id": 7, "settings": {"bot": 2}, "totals": {"net": 5.0}, "status": "STOPPED"},
        ][:limit]

    @app.get("/api/replay/runs/{run_id}")
    def run(run_id: int):
        return {"id": run_id, "settings": {"bot": 2}, "days": [{"date": "2026-10-08", "net": 120.0}], "stocks": []}

    replay_status = {"status": "IDLE"}

    @app.get("/api/replay")
    def replay(bot: int = 1):
        return replay_status

    @app.post("/api/replay/start")
    def replay_start(body: dict):
        if modes[body["bot"]] == "LIVE":
            from fastapi import HTTPException

            raise HTTPException(409, "Switch Bot to PAPER before replaying its settings.")
        writes.append(("replay", body))
        return {"status": "LOADING", "date": body["date"], "end_date": body["end_date"] or body["date"]}

    app.state.replay_status = replay_status

    @app.put("/api/config/stock/{symbol}")
    def stock(symbol: str, body: dict):
        writes.append(("stock", 1, symbol, body))
        return body

    return app, writes


@pytest.fixture
def mcp(monkeypatch):
    def make(modes=None, token=TOKEN):
        monkeypatch.setenv("MCP_TOKEN", token)
        term, writes = _fake_terminal(modes or {1: "PAPER", 2: "PAPER", 3: "LIVE", 4: "PAPER"})
        monkeypatch.setattr(
            routes_mcp,
            "terminal_client",
            lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=term), base_url="http://sma"),
        )
        app = FastAPI()
        app.include_router(routes_mcp.router)
        return TestClient(app), writes

    return make


def rpc(client, method, params=None, auth=True, path="/mcp", msg_id=1):
    headers = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
    return client.post(path, json={"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}, headers=headers)


def call(client, name, args):
    res = rpc(client, "tools/call", {"name": name, "arguments": args}).json()["result"]
    return res["isError"], res["content"][0]["text"]


def test_off_without_a_token_and_refuses_a_wrong_one(mcp):
    client, _ = mcp(token="")
    assert rpc(client, "ping").status_code == 404
    client, _ = mcp()
    assert rpc(client, "ping", auth=False).status_code == 401
    assert client.post("/mcp", json={}, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert rpc(client, "ping", auth=False, path=f"/mcp/{TOKEN}").status_code == 200


def test_initialize_and_tool_list_have_no_order_path(mcp):
    client, _ = mcp()
    init = rpc(client, "initialize", {"protocolVersion": "2025-03-26"}).json()["result"]
    assert init["protocolVersion"] == "2025-03-26" and "tools" in init["capabilities"]
    note = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"Authorization": f"Bearer {TOKEN}"})
    assert note.status_code == 202
    names = {t["name"] for t in rpc(client, "tools/list").json()["result"]["tools"]}
    assert names == {
        "list_bots", "get_bot_status", "get_settings", "get_trades", "get_scans",
        "list_backtests", "get_backtest", "start_backtest", "set_trade_list", "set_stop",
    }
    blob = json.dumps(rpc(client, "tools/list").json()).lower()
    for forbidden in ("confirm_live", "place_order", "kill", "panic", "trading_mode", "force_order", "close_position"):
        assert forbidden not in blob


def test_status_is_trimmed(mcp):
    client, _ = mcp()
    err, text = call(client, "get_bot_status", {"bot": 2})
    assert not err and json.loads(text) == {"bot": 2, "mode": "PAPER", "bot_status": "RUNNING"}


def test_writes_reach_paper_bots_only(mcp):
    client, writes = mcp()
    err, text = call(client, "set_trade_list", {"bot": 2, "arm": ["sbin"]})
    assert not err and json.loads(text)["done"] == ["armed SBIN"]
    assert writes == [("arm", 2, {"symbol": "SBIN", "armed": True, "source": "mcp"})]

    err, text = call(client, "set_trade_list", {"bot": 3, "arm": ["TCS"]})
    assert err and "LIVE" in text
    err, text = call(client, "set_stop", {"bot": 3, "use_stop": False})
    assert err and "LIVE" in text
    assert len(writes) == 1


def test_stop_change_is_validated_and_sent_per_stock(mcp):
    client, writes = mcp()
    err, text = call(client, "set_stop", {"bot": 1, "symbol": "reliance", "stop_type": "TSL", "tsl_sl_points": 11, "tsl_target_points": 18, "tsl_trail_points": 18})
    assert not err
    assert writes == [("stock", 1, "RELIANCE", {"stop_type": "TSL", "tsl_sl_points": 11.0, "tsl_trail_points": 18.0, "tsl_target_points": 18.0})]
    err, text = call(client, "set_stop", {"bot": 1, "tsl_sl_pct": 99})
    assert err and "Invalid" in text
    err, _ = call(client, "set_stop", {"bot": 1, "trading_mode": "LIVE"})
    assert err


def test_unknown_tool_and_method(mcp):
    client, _ = mcp()
    assert rpc(client, "tools/call", {"name": "place_order", "arguments": {}}).json()["error"]["code"] == -32602
    assert rpc(client, "resources/list").json()["error"]["code"] == -32601


def test_token_in_the_path_is_hidden_from_the_access_log():
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("1.2.3.4", "POST", f"/mcp/{TOKEN}", "1.1", 200), None)
    routes_mcp._HideToken().filter(record)
    assert TOKEN not in record.getMessage() and "/mcp/<token>" in record.getMessage()


def test_backtests_for_one_bot(mcp):
    client, writes = mcp()
    err, text = call(client, "list_backtests", {"bot": 2})
    runs = json.loads(text)
    assert not err and [r["id"] for r in runs] == [9, 7] and all(r["bot"] == 2 for r in runs)
    assert "settings" not in runs[0]
    assert ("runs_limit", 500) in writes
    err, text = call(client, "list_backtests", {})
    assert [r["bot"] for r in json.loads(text)] == [2, 1, 2]
    err, text = call(client, "get_backtest", {"run_id": 9})
    detail = json.loads(text)
    assert not err and detail["bot"] == 2 and detail["days"][0]["net"] == 120.0
    err, text = call(client, "get_backtest", {"run_id": "x"})
    assert err


def _at(monkeypatch, y, mo, d, h, mi):
    import datetime as dt

    monkeypatch.setattr(routes_mcp, "_now", lambda: dt.datetime(y, mo, d, h, mi, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))))


def test_start_backtest_outside_market_hours(mcp, monkeypatch):
    client, writes = mcp()
    _at(monkeypatch, 2026, 10, 12, 16, 0)  # Monday 16:00 IST
    err, text = call(client, "start_backtest", {"bot": 2, "date": "2026-10-01", "end_date": "2026-10-09", "symbols": ["SBIN"]})
    assert not err and json.loads(text)["status"] == "LOADING"
    assert writes == [("replay", {"bot": 2, "date": "2026-10-01", "end_date": "2026-10-09", "start": "09:15", "speed": 300, "symbols": ["SBIN"]})]


def test_start_backtest_is_blocked_in_market_hours_but_not_at_weekends(mcp, monkeypatch):
    client, writes = mcp()
    _at(monkeypatch, 2026, 10, 12, 9, 0)  # Monday 09:00
    err, text = call(client, "start_backtest", {"bot": 2, "date": "2026-10-01"})
    assert err and "market hours" in text
    _at(monkeypatch, 2026, 10, 12, 15, 45)
    assert call(client, "start_backtest", {"bot": 2, "date": "2026-10-01"})[0]
    assert writes == []
    _at(monkeypatch, 2026, 10, 11, 11, 0)  # Sunday
    assert not call(client, "start_backtest", {"bot": 2, "date": "2026-10-01"})[0]


def test_start_backtest_refuses_live_bot_and_a_running_backtest(mcp, monkeypatch):
    client, writes = mcp()
    _at(monkeypatch, 2026, 10, 12, 18, 0)
    err, text = call(client, "start_backtest", {"bot": 3, "date": "2026-10-01"})
    assert err and "PAPER" in text
    err, text = call(client, "start_backtest", {"bot": 2})
    assert err and "date is required" in text
    assert writes == []

    term, busy_writes = _fake_terminal({1: "PAPER", 2: "PAPER", 3: "PAPER", 4: "PAPER"})
    term.state.replay_status["status"] = "PLAYING"
    monkeypatch.setattr(
        routes_mcp,
        "terminal_client",
        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=term), base_url="http://sma"),
    )
    err, text = call(client, "start_backtest", {"bot": 2, "date": "2026-10-01"})
    assert err and "already has a backtest playing" in text
    assert busy_writes == []
