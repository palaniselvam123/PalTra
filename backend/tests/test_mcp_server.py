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

    @app.get("/api/replay/runs/{run_id}/trades")
    def run_trades(run_id: int, symbol: str | None = None):
        rows = [{"symbol": "SBIN", "best_points": 6.0, "worst_points": 2.0}, {"symbol": "TCS", "best_points": 1.0, "worst_points": 4.0}]
        return [r for r in rows if not symbol or r["symbol"] == symbol]

    @app.put("/api/config/stock/{symbol}")
    def stock(symbol: str, body: dict):
        writes.append(("stock", 1, symbol, body))
        return body

    def scan(name: str) -> dict:
        return {"running": False, "rows": [{"symbol": f"S{i}", "kind": name} for i in range(5)]}

    @app.get("/api/cross-scan")
    def cross():
        return scan("cross")

    @app.get("/api/chop-scan")
    def chop():
        return scan("chop")

    @app.get("/api/morning-scan")
    def morning():
        return scan("morning")

    @app.post("/api/{kind}-scan/start")
    def scan_start(kind: str, body: dict):
        writes.append(("scan", kind, body))
        return {"running": True, "rows": [{"symbol": "OLD"}]}

    @app.get("/api/scan-history")
    def history(day: str | None = None, kind: str | None = None):
        return {"days": [{"day": "2026-10-21", "runs": 2}], "day": day or "2026-10-21", "runs": [{"id": 4, "kind": kind}], "checking": {}}

    @app.get("/api/scan-history/{run_id}")
    def history_run(run_id: int):
        return {"id": run_id, "rows": [{"symbol": "A"}, {"symbol": "B"}, {"symbol": "C"}], "check": {"summary": {}}}

    @app.post("/api/scan-history/{run_id}/check")
    def history_check(run_id: int):
        writes.append(("check", run_id))
        return {"running": True}

    @app.get("/api/scan-arm")
    def scan_arm_state():
        return {"presets": [{"id": "ab12", "bot": 3}, {"id": "cd34", "bot": 2}], "running": None, "recent": []}

    @app.put("/api/scan-arm/presets")
    def scan_arm_save(body: dict):
        writes.append(("scan_arm_save", body))
        return {**body, "id": body.get("id") or "new1"}

    @app.post("/api/scan-arm/run")
    def scan_arm_run(body: dict):
        writes.append(("scan_arm_run", body))
        return {"started": True}

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
        "list_backtests", "get_backtest", "get_backtest_trades", "compare_backtests", "start_backtest",
        "set_trade_list", "set_stop", "start_scan", "get_scalp_monitor", "get_most_active",
        "list_scan_history", "get_scan_history", "check_scan_accuracy", "get_stock_news",
        "get_scan_arm", "save_scan_arm", "run_scan_arm",
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


def test_backtest_trades_and_compare(mcp):
    client, _ = mcp()
    err, text = call(client, "get_backtest_trades", {"run_id": 9, "symbol": "sbin"})
    assert not err and json.loads(text)["trades"] == [{"symbol": "SBIN", "best_points": 6.0, "worst_points": 2.0}]
    err, text = call(client, "compare_backtests", {"run_ids": [9, 7]})
    runs = json.loads(text)["runs"]
    assert not err and [r["run_id"] for r in runs] == [9, 7] and all(r["bot"] == 2 for r in runs)
    assert call(client, "compare_backtests", {"run_ids": [9]})[0]


def test_settings_are_locked_while_that_bots_backtest_runs(mcp, monkeypatch):
    mcp()
    term, writes = _fake_terminal({1: "PAPER", 2: "PAPER", 3: "PAPER", 4: "PAPER"})
    term.state.replay_status["status"] = "PLAYING"
    monkeypatch.setattr(
        routes_mcp,
        "terminal_client",
        lambda: httpx.AsyncClient(transport=httpx.ASGITransport(app=term), base_url="http://sma"),
    )
    app = FastAPI()
    app.include_router(routes_mcp.router)
    client = TestClient(app)
    err, text = call(client, "set_stop", {"bot": 1, "symbol": "SBIN", "tsl_sl_points": 5})
    assert err and "backtest playing" in text
    err, text = call(client, "set_trade_list", {"bot": 2, "arm": ["SBIN"]})
    assert err and "backtest playing" in text
    assert writes == []


def test_scans_are_read_per_kind_and_trimmed(mcp):
    client, _ = mcp()
    err, text = call(client, "get_scans", {"kind": "morning", "limit": 2})
    out = json.loads(text)
    assert not err and list(out) == ["morning_0930"]
    assert out["morning_0930"]["rows_total"] == 5 and len(out["morning_0930"]["rows"]) == 2
    err, text = call(client, "get_scans", {})
    assert set(json.loads(text)) == {"cross_scan", "trend_quality", "morning_0930"}
    assert call(client, "get_scans", {"kind": "nope"})[0]


def test_start_scan_on_armed_or_fno_stocks(mcp, monkeypatch):
    client, writes = mcp()

    async def desk(name, **kwargs):
        assert name == "universe"
        return {"symbols": ["SBIN", "TCS"], "error": None}

    monkeypatch.setattr(routes_mcp, "_desk_call", desk)
    err, text = call(client, "start_scan", {"kind": "cross", "minutes": 3})
    assert not err and json.loads(text)["stocks"] == 2
    assert writes[-1] == ("scan", "cross", {"symbols": ["SBIN", "TCS"], "force": False, "minutes": 3})
    err, text = call(client, "start_scan", {"kind": "chop", "symbols": ["infy"], "quiet_min": 45})
    assert not err and writes[-1] == ("scan", "chop", {"symbols": ["INFY"], "force": False, "minutes": 5, "quiet_min": 45})
    err, text = call(client, "start_scan", {"kind": "morning", "stocks": "armed"})
    assert err and "armed" in text  # the fake bots have nothing armed
    assert call(client, "start_scan", {"kind": "orders"})[0]


def test_monitor_and_most_active_come_from_the_desk(mcp, monkeypatch):
    client, _ = mcp()
    seen = []

    async def desk(name, **kwargs):
        seen.append((name, kwargs))
        return {"rows": [{"symbol": "SBIN"}], "alerts": {"enabled": True}}

    monkeypatch.setattr(routes_mcp, "_desk_call", desk)
    err, text = call(client, "get_scalp_monitor", {"only_ready": True, "limit": 10})
    assert not err and "alerts" not in json.loads(text)
    assert seen[-1][0] == "monitor" and seen[-1][1]["only_ready"] is True and seen[-1][1]["top"] == 10
    err, _ = call(client, "get_most_active", {"sort": "rvol", "bias": "buy"})
    assert not err and seen[-1] == ("active", {"sort": "rvol", "top": 40, "bias": "BUY", "min_value_cr": 0.0})
    assert call(client, "get_most_active", {"sort": "bad"})[0]


def test_scan_history_and_accuracy_check(mcp):
    client, writes = mcp()
    err, text = call(client, "list_scan_history", {"kind": "chop"})
    assert not err and json.loads(text)["runs"] == [{"id": 4, "kind": "chop"}] and "checking" not in json.loads(text)
    err, text = call(client, "get_scan_history", {"run_id": 4, "limit": 1})
    out = json.loads(text)
    assert not err and out["rows_total"] == 3 and len(out["rows"]) == 1
    err, _ = call(client, "check_scan_accuracy", {"run_id": 4})
    assert not err and writes[-1] == ("check", 4)
    assert call(client, "get_scan_history", {})[0]


def test_scan_arm_tools_reach_paper_bots_only(mcp):
    client, writes = mcp()
    err, text = call(client, "save_scan_arm", {"bot": 2, "sizes": [1, 5, 15], "kind": "SIDE", "times": ["10:00", "10:15"], "on": True})
    assert not err and json.loads(text)["id"] == "new1"
    assert writes[-1] == ("scan_arm_save", {"bot": 2, "sizes": [1, 5, 15], "kind": "SIDE", "times": ["10:00", "10:15"], "on": True})
    err, text = call(client, "save_scan_arm", {"bot": 3, "sizes": [5]})
    assert err and "LIVE" in text
    err, text = call(client, "save_scan_arm", {"preset_id": "ab12", "top_n": 3})  # preset ab12 arms bot 3 (LIVE)
    assert err and "LIVE" in text
    err, text = call(client, "run_scan_arm", {"preset_id": "cd34"})
    assert not err and writes[-1] == ("scan_arm_run", {"preset_id": "cd34", "source": "mcp"})
    err, text = call(client, "run_scan_arm", {"bot": 2})
    assert err and "sizes" in text
