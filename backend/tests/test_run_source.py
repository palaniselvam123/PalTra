"""Where a backtest run came from (run_source.py; display only)."""
from __future__ import annotations

import run_source


def test_pick_runs_read_their_rule():
    d = run_source.describe({"scalp_pick": {"kind": "AUTOARM", "source": "ACTIVE"}})
    assert d == {"code": "AUTOARM_ACTIVE", "label": "Auto-arm · Most active", "via": None}
    assert run_source.describe({"scalp_pick": {"kind": "AUTOARM", "source": "TREND"}})["label"] == "Auto-arm · Trend quality"
    assert run_source.describe({"scalp_pick": {"kind": "TREND"}})["code"] == "TREND_PICKS"
    assert run_source.describe({"scalp_pick": {"kind": "ACTIVE"}})["code"] == "ACTIVE_PICKS"
    assert run_source.describe({"scalp_pick": {"kind": "SAVED"}})["code"] == "SAVED_SCAN"
    assert run_source.describe({"scalp_pick": {"pick_time": "09:45"}})["code"] == "SCALP_PICKS"
    # A pick run started by the queue says so; the rule still names what it is.
    q = run_source.describe({"origin": "queue_schedule", "scalp_pick": {"kind": "AUTOARM", "source": "BOTH"}})
    assert q["code"] == "AUTOARM_BOTH" and q["via"] == "Backtest queue (daily schedule)"


def test_plain_replays_read_their_origin():
    assert run_source.describe({"origin": "strategy_lab"})["label"] == "Strategy lab"
    assert run_source.describe({"origin": "live_compare", "as_armed": True})["via"] == "as armed live"
    assert run_source.describe({"as_armed": True})["code"] == "AS_ARMED"
    assert run_source.describe({"origin": "replay"})["label"] == "Replay bar"
    # Runs saved before the tag, and unknown origins, read as a plain replay.
    assert run_source.describe({})["label"] == "Replay"
    assert run_source.describe({"origin": "nonsense"})["label"] == "Replay"
    assert run_source.clean("Strategy_Lab") == "strategy_lab" and run_source.clean("x") is None


def test_replay_start_saves_the_origin(tmp_path, monkeypatch):
    """The start route keeps a known origin on the run's settings and drops an unknown one (no replay is played)."""
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/src.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    import main

    seen: list[dict] = []

    class Player:
        async def begin(self, broker, symbols, day, start, speed, end_day=None, settings=None, **kw):
            seen.append(settings or {})

        def info(self):
            return {"status": "LOADING"}

    monkeypatch.setattr(main, "_replay_for", lambda bot: Player())
    monkeypatch.setattr(main, "_refuse_desk_replay_in_market", lambda: None)
    monkeypatch.setattr(main.engine.broker, "token", "x", raising=False)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as client:
        body = {"date": "2026-11-20", "symbols": ["SBIN"], "speed": 60}
        assert client.post("/api/replay/start", json={**body, "origin": "strategy_lab"}).status_code == 200
        assert client.post("/api/replay/start", json={**body, "origin": "made_up"}).status_code == 200
    assert seen[0].get("origin") == "strategy_lab"
    assert "origin" not in seen[1]
    database.reset_engine()
