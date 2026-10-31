"""Reset a desk's strategy settings to the defaults (POST /api/config/reset and the bot / research forms)."""
from __future__ import annotations

import json


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/reset.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    return database, TestClient(app)


def test_defaults_cover_every_strategy_field():
    from main import COPY_SKIP, ConfigUpdate, strategy_defaults

    defaults = strategy_defaults()
    assert set(defaults) == set(ConfigUpdate.model_fields) - COPY_SKIP
    assert defaults["stop_type"] == "ATR" and defaults["use_gap_mode"] is False and defaults["sma_medium"] is None


def test_reset_puts_the_strategy_back_but_keeps_list_mode_and_name(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from models import BotConfig

    with client:
        before = client.get("/api/bots/3/config").json()
        client.put("/api/bots/3/config", json={"bot_name": "Trend", "qty": 7, "stop_type": "TSL", "tsl_sl_points": 4.5, "use_gap_mode": True})
        client.post("/api/bots/3/trade-symbols", json={"symbol": "SBIN", "armed": True})
        client.put("/api/bots/3/config/stock/SBIN", json={"qty": 3})
        res = client.post("/api/bots/3/config/reset", json={})
        assert res.status_code == 200, res.text
        out = res.json()
        assert {"qty", "stop_type", "tsl_sl_points", "use_gap_mode"} <= set(out["changed"])
        assert "stock_settings" not in out["changed"]
        got = client.get("/api/bots/3/config").json()
        assert got["qty"] == before["qty"] and got["stop_type"] == "ATR" and got["use_gap_mode"] is False
        assert got["bot_name"] == "Trend" and got["trading_mode"] == "PAPER" and got["trade_symbols"] == ["SBIN"]
        assert client.get("/api/bots/3/config/stock/SBIN").json()["qty"] == 3  # per-stock settings stay unless asked
        res = client.post("/api/bots/3/config/reset", json={"stock_settings": True})
        assert res.status_code == 200, res.text
        assert "stock_settings" in res.json()["changed"]
        with database.session_factory()() as s:
            assert json.loads(s.get(BotConfig, 4).stock_settings or "{}") == {}
        # The main desk and the research desk have the same route.
        client.put("/api/config", json={"atr_multiplier": 4.0})
        assert client.post("/api/config/reset", json={}).json()["config"]["atr_multiplier"] != 4.0
        assert client.post("/api/research/config/reset", json={}).status_code == 200
        assert client.get("/api/config/defaults").json()["stop_type"] == "ATR"
    database.reset_engine()


def test_reset_refuses_a_live_bot_without_confirm(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from models import BotConfig

    with client:
        client.get("/api/bots/2/config")
        with database.session_factory()() as s:
            s.get(BotConfig, 3).trading_mode = "LIVE"
            s.commit()
        assert client.post("/api/bots/2/config/reset", json={}).status_code == 409
        with database.session_factory()() as s:
            s.get(BotConfig, 3).trading_mode = "PAPER"
            s.commit()
        assert client.post("/api/bots/2/config/reset", json={"confirm_live": True}).status_code == 200
    database.reset_engine()
