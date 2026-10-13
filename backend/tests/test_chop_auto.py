"""Trend quality auto re-scan on the server: interval, market hours, saved settings, the API."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pytest
from fastapi.testclient import TestClient

import chop_auto
from groww_client import IST


def _at(h: int, m: int, day: int = 13) -> dt.datetime:
    return dt.datetime(2026, 10, day, h, m, tzinfo=IST)  # 13 Oct 2026 is a Tuesday


@pytest.fixture
def auto(tmp_path, monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.delenv("SCANS", raising=False)
    return chop_auto.ChopAuto(path=tmp_path / "chop_auto.json")


def _recorder():
    calls: list[chop_auto.AutoSettings] = []

    async def start(s):
        calls.append(s)

    return start, calls


def test_off_by_default_on_the_desk_and_on_by_default_on_the_scanner(tmp_path, monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    assert chop_auto.ChopAuto(path=tmp_path / "a.json").settings.on is False
    monkeypatch.setenv("PALTRA_ROLE", "scanner")
    assert chop_auto.ChopAuto(path=tmp_path / "b.json").settings.on is True


def test_runs_when_switched_on_then_waits_the_chosen_interval(auto):
    start, calls = _recorder()
    auto.update(on=True, every_min=15)
    assert asyncio.run(auto.tick(start, _at(10, 0))) is True
    assert asyncio.run(auto.tick(start, _at(10, 14))) is False
    assert asyncio.run(auto.tick(start, _at(10, 15))) is True
    assert len(calls) == 2
    assert auto.snapshot(_at(10, 16))["next_at"].startswith("2026-10-13T10:30")


def test_a_manual_scan_pushes_the_next_automatic_one(auto):
    start, calls = _recorder()
    auto.update(on=True, every_min=10)
    asyncio.run(auto.tick(start, _at(11, 0)))
    auto.note_run(_at(11, 5))  # Scan now pressed
    assert asyncio.run(auto.tick(start, _at(11, 10))) is False
    assert asyncio.run(auto.tick(start, _at(11, 15))) is True
    assert len(calls) == 2


def test_never_outside_market_hours_or_when_off(auto):
    start, calls = _recorder()
    auto.update(on=True)
    for when in (_at(9, 0), _at(15, 45), _at(11, 0, day=17)):  # before the open, after the close, Saturday
        assert asyncio.run(auto.tick(start, when)) is False
    auto.update(on=False)
    assert asyncio.run(auto.tick(start, _at(11, 0))) is False
    assert calls == []


def test_next_scan_after_hours_is_the_next_open(auto):
    auto.update(on=True, every_min=15)
    assert auto.snapshot(_at(18, 30))["next_at"].startswith("2026-10-14T09:15")  # Tue evening -> Wed
    assert auto.snapshot(_at(8, 0))["next_at"].startswith("2026-10-13T09:15")
    assert auto.snapshot(_at(16, 0, day=16))["next_at"].startswith("2026-10-19T09:15")  # Friday -> Monday


def test_never_on_a_desk_with_scans_off(auto, monkeypatch):
    start, calls = _recorder()
    auto.update(on=True)
    monkeypatch.setenv("SCANS", "off")
    assert asyncio.run(auto.tick(start, _at(11, 0))) is False
    assert calls == []


def test_a_failed_start_is_reported_and_retried_next_interval(auto):
    async def boom(_s):
        raise RuntimeError("No stocks are armed on any bot")

    auto.update(on=True, every_min=5)
    assert asyncio.run(auto.tick(boom, _at(12, 0))) is False
    assert "armed" in auto.snapshot()["error"]
    start, calls = _recorder()
    assert asyncio.run(auto.tick(start, _at(12, 3))) is False
    assert asyncio.run(auto.tick(start, _at(12, 5))) is True
    assert auto.snapshot()["error"] is None


def test_settings_are_saved_and_bad_values_fall_back(tmp_path, monkeypatch):
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    path = tmp_path / "chop_auto.json"
    a = chop_auto.ChopAuto(path=path)
    a.update(on=True, every_min=45, minutes=15, armed_only=False, quiet_min=90, price_min=100, price_max=2000)
    again = chop_auto.ChopAuto(path=path)
    assert again.settings == a.settings
    path.write_text(json.dumps({"on": True, "every_min": 7, "minutes": 4, "quiet_min": 9999}))
    odd = chop_auto.ChopAuto(path=path)
    assert (odd.settings.every_min, odd.settings.minutes, odd.settings.quiet_min) == (30, 5, 375)
    path.write_text("not json")
    assert chop_auto.ChopAuto(path=path).settings.every_min == chop_auto.DEFAULT_EVERY


# ---- the terminal API ------------------------------------------------------------------------


@pytest.fixture
def sma_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.delenv("SCANS", raising=False)
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


def test_api_sets_the_interval_and_reports_it(sma_api, tmp_path):
    res = sma_api.put("/api/chop-scan/auto", json={"on": True, "every_min": 10, "minutes": 3, "armed_only": True})
    assert res.status_code == 200
    body = res.json()
    assert (body["on"], body["every_min"], body["minutes"]) == (True, 10, 3)
    assert sma_api.get("/api/chop-scan").json()["auto"]["every_min"] == 10
    # Saved next to the terminal's database (the Fly volume in production).
    assert json.loads((tmp_path / "chop_auto.json").read_text())["every_min"] == 10
    assert sma_api.put("/api/chop-scan/auto", json={"every_min": 7}).status_code == 422
    sma_api.put("/api/chop-scan/auto", json={"on": False})


def test_api_refuses_auto_on_a_desk_with_scans_off(sma_api, monkeypatch):
    monkeypatch.setenv("SCANS", "off")
    assert sma_api.put("/api/chop-scan/auto", json={"on": True}).status_code == 409


def test_auto_scans_the_armed_stocks_with_the_saved_choices(sma_api, monkeypatch):
    import main

    monkeypatch.setattr(main, "armed_lists", lambda: {"bots": [{"armed": ["tcs", "INFY"]}], "research": ["INFY", "WIPRO"]})
    seen = {}

    def fake_start(symbols, **kw):
        seen.update(symbols=symbols, **kw)
        return {}

    monkeypatch.setattr(main, "_start_chop", fake_start)
    s = chop_auto.AutoSettings(on=True, minutes=15, quiet_min=45, alert_on_quiet=True, price_max=3000)
    asyncio.run(main._chop_auto_start(s))
    assert seen["symbols"] == ["INFY", "TCS", "WIPRO"]
    assert (seen["minutes"], seen["quiet_min"], seen["price_max"], seen["force"]) == (15, 45, 3000, True)


def test_auto_with_nothing_armed_says_so(sma_api, monkeypatch):
    import main

    monkeypatch.setattr(main, "armed_lists", lambda: {"bots": [{"armed": []}], "research": []})
    with pytest.raises(RuntimeError, match="armed"):
        asyncio.run(main._chop_auto_start(chop_auto.AutoSettings(on=True)))
