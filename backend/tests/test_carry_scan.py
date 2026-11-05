"""Carry-over scan (carry_scan.py): yesterday's SMA side and how today went. Read-only."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

import carry_scan as cs
from groww_client import IST

D1, D2, D3 = dt.date(2026, 10, 26), dt.date(2026, 10, 27), dt.date(2026, 10, 28)


def _tape(today_path, gap: float = 0.0, minutes_today: int = 375) -> pd.DataFrame:
    """Flat day, a steady climb yesterday (the SMAs point up at its close), then today's path from yesterday's close."""
    rows, price = [], 1000.0
    for day, n, step in ((D1, 375, None), (D2, 375, 0.05), (D3, minutes_today, "today")):
        t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        if step == "today":
            price += gap
        for i in range(n):
            o = price
            if step is None:
                price = 1000.0 + (0.02 if i % 2 else -0.02)
            elif step == "today":
                price = today_path(i, price)
            else:
                price += step
            rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, price) + 0.05, "low": min(o, price) - 0.05,
                         "close": price, "volume": 1000 * (i + 1)})
            t += dt.timedelta(minutes=1)
    return pd.DataFrame(rows)


NOW = dt.datetime.combine(D3, dt.time(15, 31), tzinfo=IST)


def test_a_trend_that_carries_on():
    row = cs.read(_tape(lambda i, p: p + 0.03, gap=2.0), 9, 21, 5, NOW, symbol="UP")
    assert row is not None and row.prev_side == cs.LONG and row.status == cs.CONTINUED
    assert row.prev_day == D2.isoformat() and row.day == D3.isoformat()
    assert row.prev_close == pytest.approx(1018.73, abs=0.01)
    assert row.gap_pct == pytest.approx(2.0 / 1018.73 * 100, abs=0.001) and row.gap_with is True
    assert row.dev_pct > 0 and row.dev_rs > 0 and row.dev_rs == pytest.approx(row.last - row.prev_sma_fast, abs=0.01)
    assert row.move_with_pct > 0 and row.best_pct >= row.move_with_pct and row.worst_pct <= row.best_pct
    assert row.reversed_at is None and row.prev_since is not None


def test_a_trend_that_reverses_and_one_that_comes_back():
    down = cs.read(_tape(lambda i, p: p - 0.05, gap=-1.0), 9, 21, 5, NOW, symbol="DOWN")
    assert down.status == cs.REVERSED and down.reversed_at is not None and down.side_now == cs.SHORT
    assert down.gap_with is False and down.move_with_pct < 0 and down.dev_pct < 0 and down.worst_pct < 0

    def dip_then_up(i, p):
        return p - 0.06 if i < 90 else p + 0.08

    back = cs.read(_tape(dip_then_up), 9, 21, 5, NOW, symbol="BACK")
    assert back.status == cs.BACK and back.reversed_at < back.back_at and back.side_now == cs.LONG


def test_mid_session_reads_only_closed_candles_and_needs_two_days():
    early = dt.datetime.combine(D3, dt.time(9, 46), tzinfo=IST)
    row = cs.read(_tape(lambda i, p: p + 0.03, minutes_today=31), 9, 21, 5, early, symbol="X")
    assert row.candles_today == 6 and row.last_at == "09:45"
    one_day = _tape(lambda i, p: p)
    assert cs.read(one_day[one_day["ts"] < int(dt.datetime.combine(D2, dt.time(0), tzinfo=IST).timestamp())], 9, 21, 5, NOW) is None
    assert cs.read(pd.DataFrame(), 9, 21, 5, NOW) is None


def test_sorted_continued_first_then_back_then_reversed():
    rows = [cs.read(_tape(fn), 9, 21, 5, NOW, symbol=s) for s, fn in (
        ("R", lambda i, p: p - 0.05), ("C", lambda i, p: p + 0.03), ("B", lambda i, p: p - 0.06 if i < 90 else p + 0.08))]
    assert [r.symbol for r in sorted(rows, key=cs.sort_key)] == ["C", "B", "R"]


@pytest.mark.asyncio
async def test_scanner_reads_each_stock_and_refuses_bad_input():
    sc = cs.CarryScanner()

    async def fetch(name, start, end):
        if name == "BAD":
            raise RuntimeError("no candles")
        return _tape(lambda i, p: p + 0.03)

    with pytest.raises(ValueError):
        sc.start([], fetch)
    with pytest.raises(ValueError):
        sc.start(["A"], fetch, minutes=7)
    out = sc.start(["a", "BAD", "b"], fetch, minutes=5, now=NOW)
    assert out["running"] and out["total"] == 3
    for _ in range(100):
        if not sc.status.running:
            break
        await asyncio.sleep(0.02)
    snap = sc.snapshot()
    assert [r["symbol"] for r in snap["rows"]] == ["A", "B"] and snap["failed"] == 1 and "could not be read" in snap["error"]


def test_api_starts_the_scan(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    async def fake_fetch(broker, name, start, end):
        return _tape(lambda i, p: p + 0.03)

    monkeypatch.setattr(main, "fetch_frame", fake_fetch)
    monkeypatch.setattr(main, "_scan_job", lambda *a, **k: None)
    monkeypatch.setattr(main, "carry_scanner", cs.CarryScanner())
    with TestClient(main.app) as client:
        assert client.post("/api/carry-scan/start", json={"symbols": ["TCS"], "minutes": 7}).status_code == 422
        assert client.post("/api/carry-scan/start", json={"symbols": []}).status_code == 400
        ok = client.post("/api/carry-scan/start", json={"symbols": ["TCS"], "minutes": 15})
        assert ok.status_code == 200 and ok.json()["minutes"] == 15
        for _ in range(100):
            view = client.get("/api/carry-scan").json()
            if not view["running"]:
                break
            import time

            time.sleep(0.05)
        assert view["rows"] and view["rows"][0]["symbol"] == "TCS"
    database.reset_engine()
