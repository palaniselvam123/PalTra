"""Scanner process (scan_worker.py): scans run outside the bots' process and pace Groww.

Read-only: no Groww network call here (fake SDK / fake jobs), no order path.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import os

import pytest

import scan_worker
from candle_history import HistoryError
from tests import scan_worker_fakes as fakes

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
START, END = dt.datetime(2026, 10, 8, 0, 0), dt.datetime(2026, 10, 12, 10, 0)


class _Broker:
    def __init__(self, token="tok", spare=None):
        self.token = token
        self._spare = spare

    def adopt_saved_session(self):
        pass

    def _recover_from_auth_failure(self):
        if self._spare:
            self.token, self._spare = self._spare, None
            return True
        return False


@pytest.fixture
def pool():
    yield
    scan_worker.shutdown()


def test_a_job_runs_in_another_process_with_the_desk_token(pool):
    out = asyncio.run(scan_worker.run(_Broker(), fakes.pid_job, "TCS", START, END, 9, 21, 5))
    assert out["pid"] != os.getpid()  # not the bots' process
    assert out["symbol"] == "TCS" and out["args"] == [9, 21, 5] and out["token_seen"]


def test_a_rejected_session_switches_to_the_saved_token_and_retries(pool):
    assert asyncio.run(scan_worker.run(_Broker("old", spare="new"), fakes.auth_job, "TCS", START, END)) == "new"
    with pytest.raises(HistoryError, match="Groww did not send candles"):
        asyncio.run(scan_worker.run(_Broker("old"), fakes.auth_job, "TCS", START, END))


def test_a_failed_stock_comes_back_as_a_history_error(pool):
    with pytest.raises(HistoryError, match="Rate limit"):
        asyncio.run(scan_worker.run(_Broker(), fakes.fail_job, "TCS", START, END))


def test_no_groww_session_says_so_without_starting_the_worker():
    with pytest.raises(HistoryError, match="Log in to Groww"):
        asyncio.run(scan_worker.run(_Broker(token=""), fakes.pid_job, "TCS", START, END))
    assert scan_worker._pool is None


class _FlakySdk:
    def __init__(self, errors):
        self.errors = list(errors)
        self.calls = 0

    def get_historical_candle_data(self, **kwargs):
        self.calls += 1
        if self.errors:
            raise RuntimeError(self.errors.pop(0))
        return {"candles": []}


def test_rate_limit_answers_are_retried_and_other_errors_are_not(monkeypatch):
    monkeypatch.setattr(scan_worker, "RATE_RETRY_SEC", (0, 0, 0))
    monkeypatch.setattr(scan_worker, "MIN_GAP_SEC", 0)
    sdk = _FlakySdk(["Rate limit has breached for your request", "429 Too Many Requests"])
    assert scan_worker._Paced(sdk).get_historical_candle_data() == {"candles": []} and sdk.calls == 3
    sdk = _FlakySdk(["Rate limit has breached"] * 4)
    with pytest.raises(RuntimeError, match="Rate limit"):
        scan_worker._Paced(sdk).get_historical_candle_data()
    assert sdk.calls == 4  # the first try and three retries
    sdk = _FlakySdk(["symbol not found"])
    with pytest.raises(RuntimeError, match="not found"):
        scan_worker._Paced(sdk).get_historical_candle_data()
    assert sdk.calls == 1


def test_requests_are_spaced_out(monkeypatch):
    monkeypatch.setattr(scan_worker, "MIN_GAP_SEC", 0.05)
    paced = scan_worker._Paced(_FlakySdk([]))
    t0 = dt.datetime.now()
    for _ in range(4):
        paced.get_historical_candle_data()
    assert (dt.datetime.now() - t0).total_seconds() >= 0.15


def test_a_session_error_in_the_worker_is_reported_as_auth(monkeypatch):
    monkeypatch.setattr(scan_worker, "_sdk", lambda token: _FlakySdk(["Authentication failed: token has either expired"]))
    with pytest.raises(scan_worker.AuthFailed):
        scan_worker._frame("tok", "TCS", START, END)


def test_scanners_use_the_job_instead_of_fetching(monkeypatch):
    import chop_scan
    import cross_scan

    async def fetch(*_a):
        raise AssertionError("fetch must not be called when a job is given")

    async def job(symbol, start, end):
        return None  # e.g. too little tape

    async def go():
        for scanner in (cross_scan.CrossScanner(), chop_scan.ChopScanner()):
            scanner.start(["TCS", "INFY"], fetch, job=job, now=dt.datetime(2026, 10, 12, 10, 0, tzinfo=IST))
            await scanner._task
            snap = scanner.snapshot()
            assert snap["done"] == 2 and snap["failed"] == 0 and snap["error"] is None

    asyncio.run(go())


def test_scan_worker_off_keeps_scans_in_process(monkeypatch):
    monkeypatch.setenv("SCAN_WORKER", "off")
    assert not scan_worker.enabled()
    monkeypatch.setenv("SCAN_WORKER", "on")
    assert scan_worker.enabled()


def test_pause_refuses_new_scans_and_stops_a_running_one(tmp_path, monkeypatch):
    """Scalp page "Pause all scans" (terminal side). Read-only: no Groww call, no order."""
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/pause.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        assert client.get("/api/scans/pause").json() == {"paused": False, "scans_off": False}
        assert client.put("/api/scans/pause", json={"paused": True}).json() == {"paused": True}
        for path in ("/api/cross-scan/start", "/api/chop-scan/start"):
            refused = client.post(path, json={"symbols": ["TCS"], "minutes": 5})
            assert refused.status_code == 409 and "paused" in refused.json()["detail"]
        assert client.get("/api/cross-scan").json()["paused"] is True
        assert client.get("/api/chop-scan").json()["paused"] is True
        assert client.put("/api/scans/pause", json={"paused": False}).json() == {"paused": False}
    database.reset_engine()


def test_stop_cancels_a_running_pass_and_keeps_the_last_rows():
    import cross_scan

    async def slow(symbol, start, end):
        await asyncio.sleep(30)

    async def go():
        scanner = cross_scan.CrossScanner()
        scanner.status.rows = [{"symbol": "OLD"}]
        scanner.start(["TCS"], None, job=slow, now=dt.datetime(2026, 10, 12, 10, 0, tzinfo=IST))
        await asyncio.sleep(0.05)
        assert scanner.snapshot()["running"] is True
        scanner.stop("Stopped: scans are paused.")
        snap = scanner.snapshot()
        assert snap["running"] is False and snap["error"] == "Stopped: scans are paused." and snap["rows"] == [{"symbol": "OLD"}]
        with pytest.raises(asyncio.CancelledError):
            await scanner._task
        assert scanner.snapshot()["rows"] == [{"symbol": "OLD"}]  # a cancelled pass writes nothing

    asyncio.run(go())


def test_each_worker_spaces_its_calls_by_the_number_of_workers(monkeypatch):
    """Three workers together keep the pace of one: each waits 3 × MIN_GAP_SEC between its own calls."""
    monkeypatch.setattr(scan_worker, "MIN_GAP_SEC", 0.05)
    monkeypatch.setattr(scan_worker, "_workers", [3])
    paced = scan_worker._Paced(_FlakySdk([]))
    t0 = dt.datetime.now()
    for _ in range(3):
        paced.get_historical_candle_data()
    assert (dt.datetime.now() - t0).total_seconds() >= 0.29


def test_later_scans_today_download_only_today(monkeypatch):
    import pandas as pd

    from groww_client import IST as ist

    end = dt.datetime(2026, 10, 12, 11, 0)
    cut = int(dt.datetime(2026, 10, 12, tzinfo=ist).timestamp())
    asked = []

    def fake_frame(token, symbol, start, stop):
        asked.append((start, stop))
        first = cut - 3 * 3600 if start < dt.datetime(2026, 10, 12) else cut + 3 * 3600
        return pd.DataFrame({"ts": [first, cut + 4 * 3600], "close": [1.0, 2.0]})

    monkeypatch.setattr(scan_worker, "_frame", fake_frame)
    frame, keep = scan_worker._frame_with_past("tok", "TCS", START, end, None)
    assert asked[-1] == (START, end) and len(frame) == 2 and list(keep["ts"]) == [cut - 3 * 3600]
    frame, keep = scan_worker._frame_with_past("tok", "TCS", START, end, frame.iloc[:1])
    assert asked[-1] == (dt.datetime(2026, 10, 12), end) and keep is None
    assert list(frame["ts"]) == [cut - 3 * 3600, cut + 3 * 3600, cut + 4 * 3600]


def test_run_cached_hands_the_earlier_days_to_the_next_scan(pool):
    scan_worker._PAST.clear()
    scan_worker._PAST_DAY[0] = None
    first = asyncio.run(scan_worker.run_cached(_Broker(), fakes.past_job, "TCS", START, END, 9))
    second = asyncio.run(scan_worker.run_cached(_Broker(), fakes.past_job, "TCS", START, END, 9))
    other = asyncio.run(scan_worker.run_cached(_Broker(), fakes.past_job, "INFY", START, END, 9))
    tomorrow = asyncio.run(scan_worker.run_cached(_Broker(), fakes.past_job, "TCS", START, END + dt.timedelta(days=1), 9))
    assert (first, second, other, tomorrow) == ("full", "cached", "full", "full")
