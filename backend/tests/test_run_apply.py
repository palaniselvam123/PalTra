"""Use a backtest run's strategy on a bot (POST /api/replay/runs/{id}/apply). PAPER only; no order is placed."""
from __future__ import annotations

import datetime as dt
import json


def _client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/apply.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    return database, TestClient(app)


def _run(database, settings: dict) -> int:
    from models import ReplayRun

    with database.session_factory()() as s:
        run = ReplayRun(
            created_at=dt.datetime(2026, 11, 17, 16, 0), start_date="2026-11-17", end_date="2026-11-17",
            start_time="09:15", symbols="SBIN", settings=json.dumps(settings), status="FINISHED",
            days_total=1, days_done=1, days="2026-11-17",
        )
        s.add(run)
        s.commit()
        return int(run.id)


def test_apply_copies_the_runs_strategy_and_keeps_list_mode_and_name(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    with client:
        client.put("/api/bots/2/config", json={"bot_name": "Chop", "rsi_long_min": 44})
        client.post("/api/bots/2/trade-symbols", json={"symbol": "TCS", "armed": True})
        client.put("/api/bots/2/config/stock/TCS", json={"qty": 9})
        run_id = _run(database, {
            "bot": 1, "qty": 7, "stop_type": "TSL", "tsl_sl_points": 4.5, "entry_mode": "COLOUR",
            "trading_mode": "LIVE", "bot_name": "Other", "trade_symbols": "INFY",
            "stock_settings": {"SBIN": {"qty": 3, "no_such_field": 1}},
        })
        res = client.post(f"/api/replay/runs/{run_id}/apply", json={"to_desk": "2"})
        assert res.status_code == 200, res.text
        out = res.json()
        assert {"qty", "stop_type", "tsl_sl_points", "entry_mode", "stock_settings"} <= set(out["changed"])
        assert any("no_such_field" in n for n in out["notes"])
        got = client.get("/api/bots/2/config").json()
        assert got["qty"] == 7 and got["stop_type"] == "TSL" and got["entry_mode"] == "COLOUR"
        assert got["rsi_long_min"] == 44  # not in the run, no reset: the bot keeps it
        assert got["bot_name"] == "Chop" and got["trading_mode"] == "PAPER" and got["trade_symbols"] == ["TCS"]
        assert client.get("/api/bots/2/config/stock/SBIN").json()["qty"] == 3
        assert client.get("/api/bots/2/config/stock/TCS").json()["qty"] == 7  # the run's per-stock settings replaced it
        # Reset: fields the run did not save go back to their defaults; per-stock settings off = cleared.
        defaults = client.get("/api/config/defaults").json()
        res = client.post(f"/api/replay/runs/{run_id}/apply", json={"to_desk": "2", "reset": True, "stock_settings": False})
        assert res.status_code == 200, res.text
        got = client.get("/api/bots/2/config").json()
        assert got["rsi_long_min"] == defaults["rsi_long_min"] and got["qty"] == 7
        assert client.get("/api/bots/2/config/stock/SBIN").json()["qty"] == 7
        assert client.post("/api/replay/runs/99999/apply", json={"to_desk": "2"}).status_code == 404
    database.reset_engine()


def test_a_live_bot_needs_a_confirm(tmp_path, monkeypatch):
    database, client = _client(tmp_path, monkeypatch)
    from models import BotConfig

    with client:
        client.get("/api/bots/3/config")
        with database.session_factory()() as s:
            s.get(BotConfig, 4).trading_mode = "LIVE"  # set in the DB only; no order is placed here
            s.commit()
        run_id = _run(database, {"bot": 1, "qty": 13})
        res = client.post(f"/api/replay/runs/{run_id}/apply", json={"to_desk": "3"})
        assert res.status_code == 409 and "LIVE" in res.text
        with database.session_factory()() as s:
            assert s.get(BotConfig, 4).qty != 13
            s.get(BotConfig, 4).trading_mode = "PAPER"
            s.commit()
    database.reset_engine()


def test_a_real_snapshot_applies_whole(tmp_path, monkeypatch):
    """Every field a run saves (settings_snapshot) goes through the update model, reset or not."""
    database, client = _client(tmp_path, monkeypatch)
    from main import engine
    from strategy_engine import settings_snapshot, stock_settings

    with client:
        client.put("/api/config", json={"qty": 21, "candle_minutes": 5, "review_check_minutes": [1, 5]})
        cfg = engine.load_config()
        run_id = _run(database, {**settings_snapshot(cfg), "stock_settings": stock_settings(cfg), "bot": 1})
        for reset in (False, True):
            res = client.post(f"/api/replay/runs/{run_id}/apply", json={"to_desk": "4", "reset": reset})
            assert res.status_code == 200, res.text
        got = client.get("/api/bots/4/config").json()
        assert got["qty"] == 21 and got["candle_minutes"] == 5
    database.reset_engine()
