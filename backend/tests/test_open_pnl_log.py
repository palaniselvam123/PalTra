"""Open P&L log (open_pnl_log.py): each desk's "Open" figure, kept minute by minute.

Read-only. Engines are stubbed with positions and last prices; no network, no
Groww call, no broker path.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import open_pnl_log

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 10, 14)


def _at(hh: int, mm: int, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm, 20), tzinfo=IST)


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPEN_PNL_FILE", str(tmp_path / "open_pnl.db"))


class FakeEngine:
    def __init__(self, books: dict, mode: str = "PAPER") -> None:
        self.positions = {s: SimpleNamespace(direction=d, qty=q, entry_price=e) for s, (d, q, e, _l, _g) in books.items()}
        self._ltps = {s: v[3] for s, v in books.items()}
        self._pnl = {s: v[4] for s, v in books.items()}
        self._cfg_cache = SimpleNamespace(trading_mode=mode)

    def _open_pnl(self, symbol):
        g = self._pnl.get(symbol)
        return None if g is None else {"gross": g, "net": g - 20.0}


def test_sample_adds_every_held_stock_like_the_stocks_bar():
    eng = FakeEngine({"TCS": ("LONG", 10, 4000.0, 4050.0, 500.0), "INFY": ("SHORT", 20, 1500.0, 1510.0, -200.0)})
    s = open_pnl_log.sample(eng)
    assert (s["gross"], s["net"], s["held"]) == (300.0, 260.0, 2)
    assert {x["symbol"]: (x["side"], x["qty"], x["ltp"], x["gross"]) for x in s["stocks"]} == {
        "TCS": ("LONG", 10, 4050.0, 500.0),
        "INFY": ("SHORT", 20, 1510.0, -200.0),
    }
    assert open_pnl_log.sample(FakeEngine({})) == {"gross": 0.0, "net": 0.0, "held": 0, "stocks": []}


def test_points_are_kept_per_desk_and_minute_and_read_back_with_high_and_low():
    rec = open_pnl_log.OpenPnlRecorder()
    live = FakeEngine({"TCS": ("LONG", 10, 4000.0, 4050.0, 500.0)}, mode="LIVE")
    desks = lambda: [("1", live), ("research", FakeEngine({}))]  # noqa: E731

    async def go():
        assert await rec.record_once(desks, now=_at(10, 0)) == 2
        live._pnl["TCS"] = -150.0
        assert await rec.record_once(desks, now=_at(10, 1)) == 2
        live._pnl["TCS"] = 800.0
        await rec.record_once(desks, now=_at(10, 2))
        # The same minute again replaces its point; a closed market records nothing.
        await rec.record_once(desks, now=_at(10, 2))
        assert await rec.record_once(desks, now=_at(16, 0)) == 0

    asyncio.run(go())
    series = open_pnl_log.points("2026-10-14", "1")
    assert [p["t"] for p in series] == ["2026-10-14T10:00+05:30", "2026-10-14T10:01+05:30", "2026-10-14T10:02+05:30"]
    assert [p["gross"] for p in series] == [500.0, -150.0, 800.0] and series[0]["mode"] == "LIVE"
    assert series[0]["stocks"][0]["symbol"] == "TCS"
    assert open_pnl_log.points("2026-10-14", "research")[0]["mode"] == "RESEARCH"
    summary = open_pnl_log.summary(series)
    assert (summary["high"], summary["high_at"], summary["low"], summary["low_at"]) == (800.0, series[2]["t"], -150.0, series[1]["t"])
    assert open_pnl_log.days("1") == [{"day": "2026-10-14", "points": 3, "held_points": 3}]


def test_old_days_are_dropped(monkeypatch):
    monkeypatch.setenv("OPEN_PNL_DAYS", "30")
    sample = {"gross": 1.0, "net": 1.0, "held": 0, "stocks": []}
    open_pnl_log.save(_at(10, 0, DAY - dt.timedelta(days=40)), [("1", "PAPER", sample)])
    open_pnl_log.save(_at(10, 0), [("1", "PAPER", sample)])
    assert [d["day"] for d in open_pnl_log.days()] == ["2026-10-14"]


@pytest.fixture
def sma_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from main import app

    with TestClient(app) as client:
        yield client
    database.reset_engine()
    get_settings.cache_clear()


def test_api_returns_a_desks_day(sma_api):
    assert sma_api.get("/api/open-pnl").json()["points"] == []
    sample = {"gross": 120.5, "net": 100.0, "held": 1, "stocks": [{"symbol": "SBIN"}]}
    open_pnl_log.save(_at(11, 30), [("2", "PAPER", sample)])
    body = sma_api.get("/api/open-pnl?desk=2").json()
    assert body["day"] == "2026-10-14" and body["points"][0]["gross"] == 120.5
    assert body["summary"]["last"] == 120.5
    assert sma_api.get("/api/open-pnl?desk=2&day=2026-10-13").json()["points"] == []
    assert sma_api.get("/api/open-pnl?desk=9").status_code == 422
