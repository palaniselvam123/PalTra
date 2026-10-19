"""Copy one bot's strategy settings onto another (POST /api/bots/copy-settings). PAPER only."""
from __future__ import annotations

import json


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/copy.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    return database, TestClient(app)


def _guard_target_of_bot_2(database):
    from models import BotConfig

    with database.session_factory()() as s:
        return s.get(BotConfig, 3).all_bots_target


def test_copy_replaces_the_strategy_but_keeps_list_mode_and_name(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from models import BotConfig

    with client:
        client.put("/api/bots/3/config", json={"bot_name": "Trend", "qty": 7, "stop_type": "TSL", "tsl_sl_points": 4.5})
        client.post("/api/bots/3/trade-symbols", json={"symbol": "SBIN", "armed": True})
        client.put("/api/bots/3/config/stock/SBIN", json={"qty": 3})
        client.put("/api/bots/guard", json={"target": 500, "stop": 0})  # on bot 1's row
        client.post("/api/bots/2/trade-symbols", json={"symbol": "TCS", "armed": True})
        res = client.post("/api/bots/copy-settings", json={"from_desk": "1", "to_desk": "2"})
        assert res.status_code == 200
        assert not _guard_target_of_bot_2(database)
        res = client.post("/api/bots/copy-settings", json={"from_desk": "3", "to_desk": "2"})
        assert res.status_code == 200, res.text
        out = res.json()
        assert {"qty", "stop_type", "tsl_sl_points"} <= set(out["changed"])
        got = client.get("/api/bots/2/config").json()
        assert got["qty"] == 7 and got["stop_type"] == "TSL" and got["tsl_sl_points"] == 4.5
        assert got["bot_name"] != "Trend" and got["trading_mode"] == "PAPER"
        assert got["trade_symbols"] == ["TCS"]
        with database.session_factory()() as s:
            two = s.get(BotConfig, 3)
            assert json.loads(two.stock_settings or "{}") == {}  # per-stock settings only when asked
            assert not two.all_bots_target  # never copied
        res = client.post("/api/bots/copy-settings", json={"from_desk": "3", "to_desk": "research", "stock_settings": True})
        assert res.status_code == 200, res.text
        assert client.get("/api/research/config/stock/SBIN").json()["qty"] == 3
        assert client.post("/api/bots/copy-settings", json={"from_desk": "2", "to_desk": "2"}).status_code == 400
        assert client.post("/api/bots/copy-settings", json={"from_desk": "9", "to_desk": "2"}).status_code == 422
    database.reset_engine()


def test_a_live_destination_needs_a_confirm(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from models import BotConfig

    with client:
        client.get("/api/bots/4/config")
        with database.session_factory()() as s:
            s.get(BotConfig, 5).trading_mode = "LIVE"  # set in the DB only; no order is placed here
            s.commit()
        client.put("/api/config", json={"qty": 11})
        res = client.post("/api/bots/copy-settings", json={"from_desk": "1", "to_desk": "4"})
        assert res.status_code == 409 and "LIVE" in res.text
        with database.session_factory()() as s:
            assert s.get(BotConfig, 5).qty != 11
            s.get(BotConfig, 5).trading_mode = "PAPER"
            s.commit()
    database.reset_engine()
