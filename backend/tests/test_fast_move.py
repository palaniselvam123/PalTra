"""Fast-move alerts (fast_move.py): alerts only, nothing trades."""
from __future__ import annotations

import pytest

import fast_move


@pytest.fixture(autouse=True)
def _file(tmp_path, monkeypatch):
    monkeypatch.setenv("FAST_MOVE_FILE", str(tmp_path / "fast_moves.json"))


def test_settings_clean_and_save():
    s = fast_move.load()
    assert s.on is False and s.pct == 0.5 and s.minutes == 3 and s.scan_on is False
    s = fast_move.save({"on": True, "pct": 99, "minutes": 0, "scan_speed": 0.2})
    assert s.on and s.pct == fast_move.MAX_PCT and s.minutes == 1 and s.scan_speed == 0.2
    assert fast_move.load().scan_speed == 0.2


def test_alerts_a_fast_move_once_per_cooldown():
    w = fast_move.Watcher()
    s = fast_move.Settings(on=True, pct=0.5, minutes=3, cooldown_min=15)
    t = 1_000_000.0
    assert w.note({"SBIN": 800.0, "TCS": 4000.0}, s, t) == []
    assert w.note({"SBIN": 802.0, "TCS": 4001.0}, s, t + 60) == []  # +0.25%: not yet
    hits = w.note({"SBIN": 804.5, "TCS": 4002.0}, s, t + 120)
    assert [h["symbol"] for h in hits] == ["SBIN"] and hits[0]["move_pct"] == pytest.approx(0.56, abs=0.01)
    assert w.note({"SBIN": 810.0, "TCS": 4002.0}, s, t + 150) == []  # cooling down
    # Old prices leave the window: a slow drift over 10 minutes is never "fast".
    slow = fast_move.Watcher()
    for i in range(11):
        assert slow.note({"INFY": 1500.0 * (1 + 0.001 * i)}, s, t + i * 60) == []
    msg = fast_move.text(hits, s)
    assert "SBIN" in msg and "nothing is traded" in msg


def test_falls_and_scan_speed():
    w = fast_move.Watcher()
    s = fast_move.Settings(on=True, pct=0.5, minutes=3, scan_on=True, scan_speed=0.1)
    w.note({"ITC": 400.0}, s, 0)
    down = w.note({"ITC": 397.5}, s, 90)
    assert down and down[0]["move_pct"] < 0 and "▼ ITC" in fast_move.text(down, s)
    rows = [
        {"symbol": "A", "speed_pct_per_min": 0.15, "move_pct": 1.5, "ltp": 100.0, "window_min": 10},
        {"symbol": "B", "speed_pct_per_min": -0.3, "move_pct": -3.0, "ltp": 50.0, "window_min": 10},
        {"symbol": "C", "speed_pct_per_min": 0.02, "move_pct": 0.2, "ltp": 10.0, "window_min": 10},
        {"symbol": "ITC", "speed_pct_per_min": 0.5, "move_pct": 5.0, "ltp": 397.5, "window_min": 10},  # cooling
    ]
    hits = w.scan_hits(rows, s, 100)
    assert [h["symbol"] for h in hits] == ["B", "A"]
    assert "Fast movers in the scan" in fast_move.scan_text(hits, s)
    assert w.scan_hits(rows, s, 200) == []  # each stock once per cooldown


def test_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/fast-moves").json()
        assert got["on"] is False and got["recent"] == []
        ok = client.put("/api/fast-moves", json={"on": True, "pct": 0.8, "scan_on": True})
        assert ok.status_code == 200 and ok.json()["pct"] == 0.8 and ok.json()["scan_on"] is True
        assert client.put("/api/fast-moves", json={"minutes": 0}).status_code == 422
    database.reset_engine()
