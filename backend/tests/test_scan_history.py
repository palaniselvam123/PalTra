"""Scan history (scan_history.py) and the accuracy check (scan_accuracy.py).

Read-only. Fetches are stubbed with synthetic 1-minute candles; no network, no
Groww call, and no broker path is touched.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest
from fastapi.testclient import TestClient

import chop_scan
import cross_scan
import scan_accuracy
import scan_history

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 10, 8)


def _minutes(closes: list[float], day: dt.date = DAY, start: dt.time = dt.time(9, 15)) -> pd.DataFrame:
    t0 = dt.datetime.combine(day, start, tzinfo=IST)
    rows, prev = [], closes[0]
    for i, c in enumerate(closes):
        rows.append(
            {
                "ts": int((t0 + dt.timedelta(minutes=i)).timestamp()),
                "open": prev,
                "high": max(prev, c) + 0.05,
                "low": min(prev, c) - 0.05,
                "close": c,
                "volume": 1000 * (i + 1),
            }
        )
        prev = c
    return pd.DataFrame(rows)


def _at(hh: int, mm: int, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


@pytest.fixture(autouse=True)
def history_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "scan_history.db"))
    return tmp_path / "scan_history.db"


def _wait(scanner) -> None:
    async def go():
        for _ in range(500):
            if not scanner.status.running:
                return
            await asyncio.sleep(0.01)

    return go()


# ---- the store ----------------------------------------------------------------------------------


def test_a_saved_scan_comes_back_with_every_row_and_its_choices():
    snap = {
        "as_of": _at(10, 42).isoformat(), "finished_at": _at(10, 45).isoformat(), "minutes": 5, "total": 2,
        "failed": 0, "error": None, "sma_fast": 9, "sma_slow": 21, "quiet_min": 60,
        "rows": [{"symbol": "TCS", "score": "TRENDING", "crosses_by_minutes": {1: 2, 5: 0}}, {"symbol": "INFY", "score": "CHOPPY"}],
    }
    run_id = scan_history.save("chop", snap, {"source": "auto", "stocks": 2})
    assert run_id
    assert scan_history.days() == [{"day": "2026-10-08", "runs": 1}]
    (meta,) = scan_history.runs("2026-10-08")
    assert (meta["kind"], meta["label"], meta["row_count"], meta["minutes"]) == ("chop", "Trend quality", 2, 5)
    assert meta["meta"] == {"sma_fast": 9, "sma_slow": 21, "quiet_min": 60, "source": "auto", "stocks": 2}
    run = scan_history.get(run_id)
    assert [r["symbol"] for r in run["rows"]] == ["TCS", "INFY"]
    assert run["check"] is None and run["checked_at"] is None
    # Filter by scanner; an unknown kind is never saved.
    assert scan_history.runs("2026-10-08", "cross") == []
    assert scan_history.save("other", snap) is None
    assert scan_history.get(9999) is None


def test_old_days_are_dropped_on_the_next_save(monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_DAYS", "30")
    old = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    new = dt.date.today().isoformat()
    scan_history.save("cross", {"as_of": f"{old}T10:00:00+05:30", "rows": [{"symbol": "A"}]})
    scan_history.save("cross", {"as_of": f"{new}T10:00:00+05:30", "rows": [{"symbol": "B"}]})
    assert [d["day"] for d in scan_history.days()] == [new]


def test_a_finished_trend_quality_pass_is_saved_but_a_failed_one_is_not():
    async def go():
        tapes = {"TRND": _minutes([100 + i * 0.1 for i in range(300)])}

        async def fetch(symbol, start, end):
            if symbol not in tapes:
                raise RuntimeError("no Groww session")
            return tapes[symbol]

        saved = []
        s = chop_scan.ChopScanner()
        s.on_done = lambda snap, meta: saved.append(scan_history.save("chop", snap, meta))
        s.start(["TRND"], fetch, minutes=5, now=_at(14, 20), force=True, meta={"source": "manual"})
        await _wait(s)
        assert len(saved) == 1
        run = scan_history.get(saved[0])
        assert run["rows"][0]["symbol"] == "TRND" and run["meta"]["source"] == "manual"

        broken = chop_scan.ChopScanner()
        broken.on_done = lambda snap, meta: saved.append("again")
        broken.start(["X", "Y", "Z", "W", "V"], fetch, minutes=5, now=_at(14, 20), force=True)
        await _wait(broken)
        assert broken.status.error and len(saved) == 1

    asyncio.run(go())


def test_a_finished_cross_scan_is_handed_to_history():
    async def go():
        # Falls then turns up: the SMAs head for a bullish cross.
        tape = [110 - i * 0.05 for i in range(150)] + [102.5 + i * 0.04 for i in range(30)]

        async def fetch(symbol, start, end):
            return _minutes(tape)

        got = []
        s = cross_scan.CrossScanner()
        s.on_done = lambda snap, meta: got.append((snap, meta))
        s.start(["ABC"], fetch, minutes=1, now=_at(12, 16), force=True, meta={"stocks": 1})
        await _wait(s)
        assert len(got) == 1 and got[0][1] == {"stocks": 1} and got[0][0]["as_of"]

    asyncio.run(go())


# ---- the accuracy read --------------------------------------------------------------------------


def _day_tape(first: list[float]) -> pd.DataFrame:
    """09:15 to 15:29: `first` then flat at its last price."""
    full = first + [first[-1]] * (375 - len(first))
    return _minutes(full)


def test_a_bullish_call_on_a_rising_stock_counts_as_right_and_a_bearish_one_as_wrong():
    tape = _day_tape([100 + i * 0.05 for i in range(375)])
    as_of = _at(11, 0).isoformat()
    row = {"symbol": "UP", "side_by_minutes": {"5": "BULLISH"}, "quiet_runner": True, "trend": "STRONG_BULLISH", "score": "TRENDING"}
    out = scan_accuracy.evaluate("chop", row, tape, as_of, 5, 9, 21)
    assert out["side"] == "BULLISH"
    # Price at the scan: the 10:59 candle's close (105 minutes after 09:15).
    assert out["entry"] == pytest.approx(100 + 104 * 0.05, abs=0.01)
    assert out["after_15_pct"] > 0 and out["after_30_pct"] > out["after_15_pct"] and out["after_60_pct"] > out["after_30_pct"]
    assert out["close_pct"] > out["after_60_pct"] and out["best_pct"] >= out["close_pct"]
    assert out["next_cross_after_min"] is None and out["held_min"] == pytest.approx(270.0)
    short = scan_accuracy.evaluate("chop", {**row, "side_by_minutes": {"5": "BEARISH"}}, tape, as_of, 5, 9, 21)
    assert short["close_pct"] == pytest.approx(-out["close_pct"], abs=1e-3)
    assert short["worst_pct"] < 0


def test_a_late_scan_has_no_move_after_the_close():
    tape = _day_tape([100 + i * 0.01 for i in range(375)])
    out = scan_accuracy.evaluate("chop", {"symbol": "A", "last_cross_direction": "BULLISH"}, tape, _at(15, 0).isoformat(), 5, 9, 21)
    assert out["after_15_pct"] is not None and out["after_60_pct"] is None


def test_an_about_to_cross_row_is_marked_crossed_and_on_time():
    # Falls all morning, then turns up at 12:00: the 1-minute SMAs cross up a few minutes later.
    down = [110 - i * 0.05 for i in range(165)]
    up = [down[-1] + i * 0.06 for i in range(1, 211)]
    tape = _day_tape(down + up)
    row = {"symbol": "TURN", "state": cross_scan.APPROACHING, "side": "BULLISH", "minutes_to_cross": 6.0}
    out = scan_accuracy.evaluate("cross", row, tape, _at(12, 3).isoformat(), 1, 9, 21)
    assert out["crossed"] is True and out["next_cross_side"] == "BULLISH"
    assert out["next_cross_after_min"] <= 12 and out["on_time"] is True
    late = scan_accuracy.evaluate("cross", {**row, "minutes_to_cross": 0.5}, tape, _at(11, 40).isoformat(), 1, 9, 21)
    assert late["crossed"] is True and late["on_time"] is False


def test_summary_gives_plain_hit_rates_per_group():
    rows = [
        {"symbol": "A", "side": "BULLISH", "after_30_pct": 0.4, "close_pct": 1.0, "best_pct": 1.2, "worst_pct": -0.1, "held_min": 100, "quiet_runner": True, "trend": None},
        {"symbol": "B", "side": "BEARISH", "after_30_pct": -0.2, "close_pct": 0.5, "best_pct": 0.8, "worst_pct": -0.3, "held_min": 50, "quiet_runner": False, "trend": "STRONG_BEARISH"},
        {"symbol": "C", "side": None, "after_30_pct": 0.3, "close_pct": 0.3, "held_min": 10, "quiet_runner": False, "trend": None},
    ]
    groups = {g["key"]: g for g in scan_accuracy.summarize("chop", rows)}
    assert groups["all"]["count"] == 3 and groups["all"]["with_call"] == 2
    assert groups["all"]["right_after_30_pct"] == 50.0 and groups["all"]["right_close_pct"] == 100.0
    assert groups["all"]["avg_close_pct"] == pytest.approx(0.75)
    assert groups["quiet"]["count"] == 1 and groups["strong"]["count"] == 1
    assert "trending" not in groups  # no row with that score


def test_a_day_can_be_checked_only_once_it_has_closed():
    as_of = _at(11, 0).isoformat()
    assert not scan_accuracy.day_closed(as_of, _at(15, 0))
    assert scan_accuracy.day_closed(as_of, _at(15, 31))
    assert scan_accuracy.day_closed(as_of, _at(9, 30, DAY + dt.timedelta(days=1)))


def test_the_checker_downloads_each_stock_and_saves_the_result():
    async def go():
        run_id = scan_history.save(
            "chop",
            {"as_of": _at(11, 0).isoformat(), "minutes": 5, "sma_fast": 9, "sma_slow": 21,
             "rows": [{"symbol": "UP", "side_by_minutes": {5: "BULLISH"}}, {"symbol": "GONE"}]},
        )
        run = scan_history.get(run_id)
        asked = []

        async def fetch(symbol, start, end):
            asked.append((symbol, start, end))
            if symbol == "GONE":
                raise RuntimeError("Groww did not send candles")
            return _day_tape([100 + i * 0.05 for i in range(375)])

        c = scan_accuracy.AccuracyChecker()
        with pytest.raises(ValueError):
            c.start(run, fetch, save=scan_history.save_check, now=_at(14, 0))
        c.start(run, fetch, save=scan_history.save_check, now=_at(16, 0))
        with pytest.raises(RuntimeError):
            c.start(run, fetch, save=scan_history.save_check, now=_at(16, 0))
        await _wait(c)
        # The scan's day, with the warm-up days before it, to the close.
        assert asked[0][1] == dt.datetime(2026, 10, 4, 0, 0) and asked[0][2] == dt.datetime(2026, 10, 8, 15, 31)
        saved = scan_history.get(run_id)
        assert saved["checked_at"] and saved["check"]["checked"] == 1 and saved["check"]["failed"] == 1
        assert saved["check"]["rows"][0]["symbol"] == "UP" and saved["check"]["rows"][0]["close_pct"] > 0
        assert "GONE" not in str(saved["check"]["rows"]) and "could not be read" in saved["check"]["error"]
        assert scan_history.runs("2026-10-08")[0]["checked_at"] == saved["checked_at"]

    asyncio.run(go())


# ---- the terminal API ---------------------------------------------------------------------------


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


def test_api_lists_days_and_runs_and_opens_one(sma_api):
    assert sma_api.get("/api/scan-history").json()["runs"] == []
    first = scan_history.save("cross", {"as_of": _at(10, 0).isoformat(), "minutes": 5, "rows": [{"symbol": "A"}]})
    scan_history.save("chop", {"as_of": _at(11, 0).isoformat(), "minutes": 3, "rows": [{"symbol": "B"}]})
    scan_history.save("chop", {"as_of": _at(10, 0, DAY - dt.timedelta(days=1)).isoformat(), "rows": [{"symbol": "C"}]})
    body = sma_api.get("/api/scan-history").json()
    assert [d["day"] for d in body["days"]] == ["2026-10-08", "2026-10-07"]
    assert body["day"] == "2026-10-08" and [r["kind"] for r in body["runs"]] == ["cross", "chop"]
    assert [r["kind"] for r in sma_api.get("/api/scan-history?day=2026-10-08&kind=chop").json()["runs"]] == ["chop"]
    assert sma_api.get("/api/scan-history?day=yesterday").status_code == 422
    run = sma_api.get(f"/api/scan-history/{first}").json()
    assert run["rows"] == [{"symbol": "A"}] and run["can_check"] is True
    assert sma_api.get("/api/scan-history/999").status_code == 404
    assert sma_api.post("/api/scan-history/999/check").status_code in (404, 409)


def test_api_refuses_a_check_on_a_desk_with_scans_off(sma_api, monkeypatch):
    run_id = scan_history.save("cross", {"as_of": _at(10, 0).isoformat(), "rows": [{"symbol": "A"}]})
    monkeypatch.setenv("SCANS", "off")
    assert sma_api.post(f"/api/scan-history/{run_id}/check").status_code == 409
