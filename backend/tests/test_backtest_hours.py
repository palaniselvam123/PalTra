"""Backtest server market-hours-only switch, its scans copied to the desk, and desk replays after hours only."""
from __future__ import annotations

import datetime as dt
import time

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import backtest_server
from app.api import routes_link
from groww_client import IST

TOKEN = "b" * 32
THU = dt.datetime(2026, 10, 29, 11, 0, tzinfo=IST)  # a Thursday


@pytest.fixture(autouse=True)
def fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("BACKTEST_SETTINGS_FILE", str(tmp_path / "backtest_server.json"))
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "scan_history.db"))
    backtest_server.state.reset()
    yield
    backtest_server.state.reset()


def test_market_hours_window_and_stop_time():
    assert backtest_server.market_hours_only() is False and backtest_server.stop_at() == "15:45"
    backtest_server.set_market_hours(True, "16:30")
    assert backtest_server.market_hours_only() and backtest_server.stop_at() == "16:30"
    assert backtest_server.in_hours(THU) and not backtest_server.after_hours(THU)
    assert backtest_server.after_hours(THU.replace(hour=16, minute=30))
    assert backtest_server.after_hours(THU.replace(hour=8, minute=59))
    assert backtest_server.after_hours(THU + dt.timedelta(days=2))  # Saturday
    with pytest.raises(ValueError):
        backtest_server.set_market_hours(True, "08:00")
    backtest_server.set_market_hours(False)
    assert not backtest_server.after_hours(THU.replace(hour=20))


def test_after_hours_an_open_page_does_not_keep_it_up_but_a_backtest_does():
    backtest_server.set_market_hours(True)
    evening = THU.replace(hour=18)
    start = backtest_server.state.started
    backtest_server.note_request("/api/state")  # someone has the page open
    now = start + backtest_server.AFTER_HOURS_MIN * 60 + 1
    assert backtest_server.idle_seconds_left({}, now=now, clock=evening) == 0
    # In market hours the same page keeps it up for the idle time.
    assert backtest_server.idle_seconds_left({}, now=now, clock=THU) > 0
    playing = {1: type("R", (), {"status": "PLAYING"})()}
    assert backtest_server.idle_seconds_left(playing, now=now, clock=evening) > 0
    assert backtest_server.status({})["market_hours_only"] is True


def test_scans_are_pushed_to_the_desk_only_from_the_backtest_server(monkeypatch):
    sent = []
    monkeypatch.setattr(backtest_server, "_desk_post", lambda path, body: sent.append((path, body)))
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    backtest_server.push_scan("chop", {"as_of": "x", "rows": []}, {"minutes": 5})
    assert sent == []  # the desk itself never pushes
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    backtest_server.push_scan("chop", {"as_of": "x", "rows": []}, {"minutes": 5})
    backtest_server.push_check("chop", "x", {"groups": []})
    assert sent[0] == (backtest_server.SCANS_PATH, {"kind": "chop", "snapshot": {"as_of": "x", "rows": []}, "meta": {"minutes": 5, "server": "backtest"}})
    assert sent[1][1] == {"check": {"kind": "chop", "as_of": "x", "result": {"groups": []}}}

    def boom(*_a):
        raise RuntimeError("desk down")

    monkeypatch.setattr(backtest_server, "_desk_post", boom)
    backtest_server.push_scan("chop", {"as_of": "x"})  # never raises


def test_desk_keeps_the_copied_scans_once_with_their_checks(monkeypatch):
    import scan_history

    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("BACKTEST_LINK_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(routes_link.router)
    client = TestClient(app)
    good = {"Authorization": f"Bearer {TOKEN}"}
    as_of = THU.isoformat()
    body = {"kind": "cross", "snapshot": {"as_of": as_of, "rows": [{"symbol": "ACME"}]}, "meta": {"server": "backtest"}}
    assert client.post(routes_link.BACKTEST_SCANS, json=body).status_code == 401
    first = client.post(routes_link.BACKTEST_SCANS, json=body, headers=good).json()["saved"]
    assert first and client.post(routes_link.BACKTEST_SCANS, json=body, headers=good).json()["saved"] is None
    active = {"kind": "active", "snapshot": {"as_of": as_of, "rows": [{"symbol": "BIG"}]}}
    assert client.post(routes_link.BACKTEST_SCANS, json=active, headers=good).json()["saved"]
    check = {"check": {"kind": "cross", "as_of": as_of, "result": {"groups": [1]}}}
    assert client.post(routes_link.BACKTEST_SCANS, json=check, headers=good).json() == {"saved": first}
    run = scan_history.get(first)
    assert run["rows"] == [{"symbol": "ACME"}] and run["check"] == {"groups": [1]}
    assert [r["kind"] for r in scan_history.runs(THU.date().isoformat())] == ["cross"]
    assert routes_link.token_path(routes_link.BACKTEST_SCANS)


def test_desk_replays_wait_for_the_market_to_close(monkeypatch):
    import main

    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.setenv("DESK_REPLAY_HOURS", "after")
    with pytest.raises(HTTPException) as err:
        main._refuse_desk_replay_in_market(THU)
    assert err.value.status_code == 409 and "after 15:45" in err.value.detail
    main._refuse_desk_replay_in_market(THU.replace(hour=15, minute=45))
    main._refuse_desk_replay_in_market(THU + dt.timedelta(days=2))  # Saturday
    monkeypatch.setenv("PALTRA_ROLE", "backtest")
    main._refuse_desk_replay_in_market(THU)  # the backtest server replays any time
