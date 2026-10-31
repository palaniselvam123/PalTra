"""Pull the backtest server's scan history into the desk's (scan_sync.py)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.testclient import TestClient

import scan_history
import scan_sync
from app.api import routes_link

TOKEN = "x" * 32
IST = ZoneInfo("Asia/Kolkata")


def _snap(as_of: dt.datetime, rows):
    return {"as_of": as_of.isoformat(), "finished_at": as_of.isoformat(), "minutes": 5, "total": len(rows), "failed": 0, "rows": rows}


def test_export_answers_only_on_the_backtest_server_with_the_token(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "scans.db"))
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    day = dt.datetime(2026, 10, 29, 10, 30, tzinfo=IST)
    first = scan_history.save("chop", _snap(day, [{"symbol": "ACME"}]), {"source": "manual"})
    scan_history.save_check(first, {"groups": [1]})
    app = FastAPI()
    app.include_router(routes_link.router)
    client = TestClient(app)
    good = {"Authorization": f"Bearer {TOKEN}"}
    assert client.get(routes_link.BACKTEST_EXPORT).status_code == 401
    runs = client.get(routes_link.BACKTEST_EXPORT, headers=good).json()["runs"]
    assert [r["kind"] for r in runs] == ["chop"] and runs[0]["id"] == first
    detail = client.get(f"{routes_link.BACKTEST_EXPORT}/{first}", headers=good).json()
    assert detail["rows"] == [{"symbol": "ACME"}] and detail["check"] == {"groups": [1]} and detail["meta"]["source"] == "manual"
    assert routes_link.token_path(routes_link.BACKTEST_EXPORT) and routes_link.token_path(f"{routes_link.BACKTEST_EXPORT}/7")
    # The desk never answers the export route.
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    assert client.get(routes_link.BACKTEST_EXPORT, headers=good).status_code == 404


def test_pull_copies_what_the_desk_lacks_with_checks(monkeypatch, tmp_path):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "desk.db"))
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    d1 = dt.datetime(2026, 10, 29, 10, 30, tzinfo=IST)
    d2 = dt.datetime(2026, 10, 29, 11, 0, tzinfo=IST)
    # The desk already has the 10:30 scan.
    have = scan_history.save("chop", _snap(d1, [{"symbol": "OLD"}]), {"source": "auto"})
    remote = {
        f"{scan_sync.EXPORT_PATH}?days=14": {"runs": [
            {"id": 1, "kind": "chop", "as_of": d1.isoformat(), "day": "2026-10-29"},
            {"id": 2, "kind": "chop", "as_of": d2.isoformat(), "day": "2026-10-29"},
            {"id": 3, "kind": "cross", "as_of": d2.isoformat(), "day": "2026-10-29"},
        ]},
        f"{scan_sync.EXPORT_PATH}/2": {**_snap(d2, [{"symbol": "NEW"}]), "meta": {"source": "manual", "sma_fast": 9}, "check": {"groups": [2]}},
        f"{scan_sync.EXPORT_PATH}/3": {**_snap(d2, [{"symbol": "X"}]), "meta": {}, "check": None},
    }
    asked = []

    def fetch(path):
        asked.append(path)
        return remote[path]

    out = scan_sync.pull(fetch, 14)
    assert out == {"copied": 2, "checks": 1, "already": 1, "errors": []}
    assert f"{scan_sync.EXPORT_PATH}/1" not in asked  # the one the desk had was not downloaded
    runs = scan_history.runs("2026-10-29")
    assert [(r["kind"], r["id"] == have) for r in runs] == [("chop", True), ("chop", False), ("cross", False)]
    new = next(r for r in runs if r["kind"] == "chop" and r["id"] != have)
    got = scan_history.get(new["id"])
    assert got["rows"] == [{"symbol": "NEW"}] and got["check"] == {"groups": [2]}
    assert got["meta"]["server"] == "backtest" and got["meta"]["source"] == "manual" and got["meta"]["sma_fast"] == 9
    # A second pull copies nothing more.
    assert scan_sync.pull(fetch, 14)["copied"] == 0


def test_pull_route_refuses_without_the_backtest_address(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "scans.db"))
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.delenv("BACKTEST_URL", raising=False)
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from main import app

    with TestClient(app) as client:
        assert client.post("/api/scan-history/pull").status_code == 409
        monkeypatch.setenv("BACKTEST_URL", "http://backtest.test")
        monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
        monkeypatch.setattr(scan_sync, "http_fetch", lambda path: {"runs": []})
        assert client.post("/api/scan-history/pull?days=3").json() == {"copied": 0, "checks": 0, "already": 0, "errors": []}
    database.reset_engine()
