"""Export / import settings (GET /api/settings/export, POST /api/settings/import). PAPER only; no order."""
from __future__ import annotations

import json


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/transfer.db")
    monkeypatch.setenv("FAST_BOT_FILE", str(tmp_path / "fast_bot.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    return database, TestClient(app)


def test_export_then_import_round_trips_without_mode_or_name(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from models import BotConfig

    with client:
        client.put("/api/bots/3/config", json={"bot_name": "Trend", "qty": 7, "stop_type": "TSL", "tsl_sl_points": 4.5})
        client.post("/api/bots/3/trade-symbols", json={"symbol": "SBIN", "armed": True})
        client.put("/api/bots/3/config/stock/SBIN", json={"qty": 3, "review_check_minutes": [1, 5]})
        client.put("/api/bots/fast-mover", json={"on": True, "pct": 0.7, "sources": [3]})
        res = client.get("/api/settings/export", params={"desks": "1,3"})
        assert res.status_code == 200
        file = res.json()
        assert file["kind"] == "paltra-settings" and set(file["desks"]) == {"1", "3"}
        three = file["desks"]["3"]
        assert three["settings"]["qty"] == 7 and three["stock_settings"]["SBIN"]["qty"] == 3
        assert three["trade_symbols"] == ["SBIN"] and "trading_mode" not in three["settings"]
        assert file["fast_bot"]["pct"] == 0.7

        # Bot 3's file onto bot 2: strategy, per-stock settings and (added) Trade list; never the mode or name.
        file["desks"]["2"] = {**three, "mode": "LIVE", "settings": {**three["settings"], "trading_mode": "LIVE", "bot_name": "X"}}
        client.put("/api/bots/fast-mover", json={"on": False, "pct": 2.0})
        res = client.post(
            "/api/settings/import",
            json={"file": file, "desks": ["2"], "stock_settings": True, "trade_lists": True, "fast_bot": True},
        )
        assert res.status_code == 200, res.text
        out = res.json()["desks"][0]
        assert {"qty", "stop_type", "tsl_sl_points", "stock_settings"} <= set(out["changed"]) and out["added"] == ["SBIN"]
        got = client.get("/api/bots/2/config").json()
        assert got["qty"] == 7 and got["stop_type"] == "TSL" and got["trading_mode"] == "PAPER"
        assert got["bot_name"] != "X" and got["trade_symbols"] == ["SBIN"]
        with database.session_factory()() as s:
            assert json.loads(s.get(BotConfig, 3).stock_settings or "{}") == three["stock_settings"]
        fast = client.get("/api/bots/fast-mover").json()
        assert fast["on"] is True and fast["pct"] == 0.7
    database.reset_engine()


def test_a_bad_value_or_live_bot_writes_nothing(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from bots import config_id_for
    from models import BotConfig

    with client:
        file = client.get("/api/settings/export", params={"desks": "1,2"}).json()
        before = client.get("/api/config").json()["qty"]
        file["desks"]["1"]["settings"]["qty"] = before + 5
        file["desks"]["2"]["settings"]["qty"] = 0  # below the minimum
        res = client.post("/api/settings/import", json={"file": file, "desks": ["1", "2"]})
        assert res.status_code == 400 and "Bot 2" in res.json()["detail"]
        assert client.get("/api/config").json()["qty"] == before  # bot 1 untouched: checked before writing

        file["desks"]["2"]["settings"]["qty"] = 4
        file["desks"]["2"]["stock_settings"] = {"TCS": {"nonsense_field": 1}}
        res = client.post("/api/settings/import", json={"file": file, "desks": ["2"]})
        assert res.status_code == 400 and "unknown settings" in res.json()["detail"]

        with database.session_factory()() as s:
            s.get(BotConfig, config_id_for(2)).trading_mode = "LIVE"
            s.commit()
        file["desks"]["2"]["stock_settings"] = {}
        res = client.post("/api/settings/import", json={"file": file, "desks": ["2"], "trade_lists": True})
        assert res.status_code == 409
        file["desks"]["2"]["trade_symbols"] = ["INFY"]
        res = client.post("/api/settings/import", json={"file": file, "desks": ["2"], "trade_lists": True, "confirm_live": True})
        assert res.status_code == 200
        out = res.json()["desks"][0]
        assert out["added"] == [] and "LIVE" in out["notes"][0]  # a LIVE bot's Trade list is never changed here
        with database.session_factory()() as s:
            row = s.get(BotConfig, config_id_for(2))
            assert row.trading_mode == "LIVE" and row.qty == 4
            row.trading_mode = "PAPER"
            s.commit()
        assert client.post("/api/settings/import", json={"file": {"kind": "x"}, "desks": ["1"]}).status_code == 400
    database.reset_engine()
