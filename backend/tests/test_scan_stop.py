"""Each Scalp scan has its own Stop (main.scan_stop, the desk's /api/scalp/active/stop). Read-only scans."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

import turn_scan
from tests.test_turn_scan import _at, _frame


@pytest.mark.asyncio
async def test_a_running_scan_stops_and_keeps_its_last_rows():
    gate = asyncio.Event()

    async def slow(symbol, start, end):
        await gate.wait()
        return _frame()

    async def quick(symbol, start, end):
        return _frame()

    scanner = turn_scan.TurnScanner()
    scanner.start(["A"], quick, minutes=5, count=3, now=_at(14, 40))
    await scanner._task
    assert [r["symbol"] for r in scanner.snapshot()["rows"]] == ["A"]
    scanner.start(["B", "C"], slow, minutes=5, count=3, now=_at(14, 45), force=True)
    assert scanner.snapshot()["running"]
    scanner.stop("Stopped by you.")
    await asyncio.sleep(0)
    snap = scanner.snapshot()
    assert not snap["running"] and snap["error"] == "Stopped by you."
    assert [r["symbol"] for r in snap["rows"]] == ["A"]  # the last finished rows stay


def test_api_stop_answers_for_each_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        for kind in ("cross", "chop", "morning", "high52", "carry", "turn"):
            got = client.post(f"/api/scan-stop/{kind}")
            assert got.status_code == 200 and got.json()["stopped"] is False, kind
        assert client.post("/api/scan-stop/nonsense").status_code == 404
    database.reset_engine()


@pytest.mark.asyncio
async def test_most_active_stop_keeps_the_last_scan():
    from app.services.active_stocks import ActiveScanner

    scanner = ActiveScanner()
    assert scanner.stop_pass() is False
    gate = asyncio.Event()

    class Client:
        async def get_quote_payload(self, symbol):
            await gate.wait()
            return {}

    scanner.rows = ["kept"]  # type: ignore[list-item]
    scanner._average_volumes = lambda *a, **k: asyncio.sleep(0)  # type: ignore[method-assign]
    task = asyncio.create_task(scanner.scan(Client(), symbols=["A", "B"], now=dt.datetime(2026, 11, 6, 11, 0)))
    await asyncio.sleep(0)
    assert scanner.stop_pass() is True
    gate.set()
    await task
    assert scanner.rows == ["kept"] and "Stopped by you" in (scanner.error or "")
