"""Queue a plain replay from the Backtests tab (POST /api/backtest-queue/replay). Practice money; nothing is ordered."""
from __future__ import annotations

import asyncio


def test_queue_replay_route_and_start(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/q.db")
    monkeypatch.setenv("BACKTEST_QUEUE_FILE", str(tmp_path / "queue.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    body = {"date": "2026-10-14", "end_date": "2026-10-16", "symbols": ["SBIN"], "bot": 2, "note": "again from run #7"}
    res = client.post("/api/backtest-queue/replay", json=body)
    assert res.status_code == 200, res.text
    item = res.json()["items"][0]
    assert item["kind"] == "REPLAY" and item["body"]["symbols"] == ["SBIN"] and "run #7" in item["label"]
    assert client.post("/api/backtest-queue/replay", json={**body, "symbols": []}).status_code == 400
    assert client.post("/api/backtest-queue/replay", json={**body, "bot": 5}).status_code == 422

    seen = {}

    async def fake_start(start):
        seen["body"] = start

    monkeypatch.setattr(main, "replay_start", fake_start)
    asyncio.run(main._queue_start(item["body"], "REPLAY"))
    got = seen["body"]
    assert got.bot == 2 and got.symbols == ["SBIN"] and got.end_date == "2026-10-16" and got.speed == max(main.SPEEDS)
