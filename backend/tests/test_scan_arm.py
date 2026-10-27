"""Scan & arm (scan_arm.py): a Trend quality pass, then arm the matches on a bot. Arming only, no order."""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import pytest

import scan_arm

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
DAY = dt.date(2026, 10, 27)  # a Tuesday


def _at(hh: int, mm: int, ss: int = 0) -> dt.datetime:
    return dt.datetime.combine(DAY, dt.time(hh, mm, ss), tzinfo=IST)


def _row(symbol, side, candles, gap, sizes=(1, 5, 15), strong=False, peak=None):
    trend = ("STRONG_BULLISH" if side == "BULLISH" else "STRONG_BEARISH") if strong else None
    return {
        "symbol": symbol,
        "ltp": 100.0,
        "side_by_minutes": {str(m): side for m in sizes},
        "trend_by_minutes": {str(m): trend for m in sizes},
        "run_candles_by_minutes": {str(m): candles for m in sizes},
        "run_since_ts_by_minutes": {str(m): 1000 for m in sizes},
        "gap_pct_by_minutes": {str(m): gap if side == "BULLISH" else -gap for m in sizes},
        "gap_peak_pct_by_minutes": {str(m): peak if peak is not None else gap for m in sizes},
    }


@pytest.fixture(autouse=True)
def _file(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_ARM_FILE", str(tmp_path / "scan_arm.json"))


def test_clean_keeps_sane_values():
    p = scan_arm.clean({"bot": 9, "sizes": [1, 5, 15, 7], "times": ["10:00", "9:20", "16:00", "10:00", "x"], "on": True,
                        "top_n": 99, "direction": "UP", "kind": "BAD", "lists": ["nifty50", "zzz"]})
    assert p.bot == 3 and p.sizes == [1, 5, 15] and p.times == ["09:20", "10:00"] and p.on
    assert p.top_n == scan_arm.MAX_TOP and p.direction == "UP" and p.kind == "STRONG" and p.lists == ["nifty50"]
    assert scan_arm.clean({"on": True}).on is False  # no times, nothing to schedule
    assert "Strong bullish on 1m/5m/15m" in scan_arm.describe(p)


def test_picks_need_every_size_the_same_way_and_take_the_top_ranks():
    rows = [
        _row("FRESH", "BULLISH", 3, 0.6, strong=True),
        _row("OLDER", "BEARISH", 40, 0.5, strong=True),
        _row("WEAK", "BULLISH", 2, 0.3),  # SMA side only, not a strong trend
        _row("FADED", "BULLISH", 2, 0.2, strong=True, peak=0.9),
    ]
    mixed = _row("MIXED", "BULLISH", 3, 0.6, strong=True)
    mixed["side_by_minutes"]["15"] = "BEARISH"
    mixed["trend_by_minutes"]["15"] = "STRONG_BEARISH"
    rows.append(mixed)
    p = scan_arm.clean({"sizes": [1, 5, 15], "kind": "STRONG", "top_n": 5, "skip_fading": True})
    assert [r["symbol"] for r in scan_arm.picks(rows, p)] == ["FRESH", "OLDER"]
    p = scan_arm.clean({"sizes": [1, 5, 15], "kind": "STRONG", "direction": "DOWN"})
    assert [r["symbol"] for r in scan_arm.picks(rows, p)] == ["OLDER"]
    p = scan_arm.clean({"sizes": [1, 5, 15], "kind": "SIDE", "top_n": 2, "max_age_min": 60})
    assert len(scan_arm.picks(rows, p)) == 2 and "OLDER" not in [r["symbol"] for r in scan_arm.picks(rows, p)]


def test_due_time_once_a_day_within_the_grace_window():
    p = scan_arm.clean({"times": ["10:00", "10:15"], "on": True})
    assert scan_arm.due_time(p, _at(9, 59)) is None
    assert scan_arm.due_time(p, _at(10, 0, 30)) == "10:00"
    p.fired["10:00"] = DAY.isoformat()
    assert scan_arm.due_time(p, _at(10, 5)) is None
    assert scan_arm.due_time(p, _at(10, 16)) == "10:15"
    assert scan_arm.due_time(p, _at(10, 31)) is None  # missed by more than the grace (a restart)
    p.on = False
    assert scan_arm.due_time(p, _at(10, 16)) is None
    p.on = True
    assert scan_arm.due_time(p, dt.datetime(2026, 10, 25, 10, 16, tzinfo=IST)) is None  # Sunday


def test_save_get_delete_and_times_reset_fired():
    p = scan_arm.save({"name": "Bot 3 trend", "bot": 3, "sizes": [1, 5, 15], "kind": "SIDE", "times": ["10:00"], "on": True})
    assert p.id and scan_arm.get(p.id).name == "Bot 3 trend"
    scan_arm._update(p.id, fired={"10:00": DAY.isoformat()})
    again = scan_arm.save({"id": p.id, "times": ["10:00", "10:15"], "fired": {}, "last": {"x": 1}})
    assert again.fired == {"10:00": DAY.isoformat()} and again.last is None and again.times == ["10:00", "10:15"]
    assert scan_arm.save({"id": p.id, "times": ["11:00"]}).fired == {}
    assert scan_arm.delete(p.id) and scan_arm.get(p.id) is None and not scan_arm.delete(p.id)


def _hooks(rows, book, armed_log, refuse=()):
    async def symbols(lists):
        return ["FRESH", "OLDER", "HELD", "BAD"]

    async def scan(names, minutes, progress):
        progress("Scanning")
        return {"rows": rows, "as_of": "x"}

    async def bots():
        return book

    async def arm(bot, symbol, source, confirm_live):
        if symbol in refuse:
            return "Trade is limited"
        armed_log.append((bot, symbol, source, confirm_live))
        return None

    return {"symbols": symbols, "scan": scan, "bots": bots, "arm": arm}


def test_run_arms_new_matches_and_reports_the_rest():
    rows = [_row(s, "BULLISH", 3 + i, 0.6 - i * 0.1, strong=True) for i, s in enumerate(["FRESH", "HELD", "BAD", "OLDER"])]
    book = {3: {"bot": 3, "name": "Trend bot", "mode": "PAPER", "armed": ["HELD"]}}
    log: list = []
    p = scan_arm.save({"name": "Morning", "bot": 3, "sizes": [1, 5, 15], "top_n": 3})
    rec = asyncio.run(scan_arm.Runner().run(p, trigger="schedule", at="10:00", **_hooks(rows, book, log, refuse=("BAD",))))
    assert [m["symbol"] for m in rec["matched"]] == ["FRESH", "HELD", "BAD"]
    assert rec["armed"] == ["FRESH"] and rec["already"] == ["HELD"] and rec["skipped"][0]["symbol"] == "BAD"
    assert log == [(3, "FRESH", "Scan & arm: Morning", False)]
    assert scan_arm.get(p.id).last["armed"] == ["FRESH"]
    msg = scan_arm.text(rec)
    assert "Trend bot" in msg and "FRESH ▲" in msg and "No order was placed" in msg


def test_a_live_bot_needs_the_persons_confirmation():
    book = {2: {"bot": 2, "name": "Bot 2", "mode": "LIVE", "armed": []}}
    log: list = []
    p = scan_arm.clean({"bot": 2, "sizes": [5]})
    with pytest.raises(scan_arm.LiveRefused):
        asyncio.run(scan_arm.Runner().run(p, **_hooks([_row("FRESH", "BULLISH", 3, 0.5, sizes=(5,), strong=True)], book, log)))
    rec = asyncio.run(
        scan_arm.Runner().run(p, confirm_live=True, **_hooks([_row("FRESH", "BULLISH", 3, 0.5, sizes=(5,), strong=True)], book, log))
    )
    assert rec["armed"] == ["FRESH"] and log[0][3] is True


def test_scheduled_live_run_is_refused_and_reported():
    book = {2: {"bot": 2, "name": "Bot 2", "mode": "LIVE", "armed": []}}
    sent: list = []

    async def notify(message):
        sent.append(message)

    async def go():
        runner = scan_arm.Runner()
        p = scan_arm.save({"bot": 2, "sizes": [5], "times": ["10:00"], "on": True})
        runner.start(p, trigger="schedule", at="10:00", notify=notify, **_hooks([], book, []))
        while runner._tasks:
            await asyncio.sleep(0.01)
        return runner

    runner = asyncio.run(go())
    assert "LIVE" in runner.recent[0]["error"] and sent and "Not done" in sent[0]


def test_tick_fires_each_time_once():
    started: list = []
    p = scan_arm.save({"bot": 3, "sizes": [5], "times": ["10:00"], "on": True})
    runner = scan_arm.Runner()
    assert asyncio.run(runner.tick(lambda pr, t: started.append((pr.id, t)), now=_at(10, 1))) == [p.id]
    assert asyncio.run(runner.tick(lambda pr, t: started.append((pr.id, t)), now=_at(10, 2))) == []
    assert started == [(p.id, "10:00")]


# ---- terminal routes --------------------------------------------------------------------------


def test_routes_save_run_and_arm_a_paper_bot(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("PALTRA_ROLE", raising=False)
    monkeypatch.delenv("SCANS", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    rows = [_row("SBIN", "BULLISH", 4, 0.4, strong=True), _row("TCS", "BEARISH", 6, 0.3, strong=True)]

    async def fake_symbols(armed_only, lists=None):
        return ["SBIN", "TCS", "INFY"]

    async def fake_scan(names, minutes, progress):
        return {"rows": rows, "as_of": "now"}

    monkeypatch.setattr(main, "_auto_symbols", fake_symbols)
    monkeypatch.setattr(main, "_scan_arm_scan", fake_scan)
    monkeypatch.setattr(main, "_scans_paused", False)
    with TestClient(main.app) as client:
        assert client.put("/api/scan-arm/presets", json={"sizes": []}).status_code == 400
        assert client.put("/api/scan-arm/presets", json={"times": ["16:30"]}).status_code == 400
        saved = client.put("/api/scan-arm/presets", json={"name": "1/5/15 on Bot 3", "bot": 3, "sizes": [1, 5, 15],
                                                            "kind": "STRONG", "times": ["10:00", "10:15"], "on": True})
        assert saved.status_code == 200 and saved.json()["times"] == ["10:00", "10:15"]
        pid = saved.json()["id"]
        assert client.post("/api/scan-arm/run", json={}).status_code == 400
        run = client.post("/api/scan-arm/run", json={"preset_id": pid})
        assert run.status_code == 200 and run.json()["started"]
        for _ in range(100):
            state = client.get("/api/scan-arm").json()
            if state["running"] is None and state["recent"]:
                break
            time.sleep(0.05)
        rec = state["recent"][0]
        assert rec["error"] is None and sorted(rec["armed"]) == ["SBIN", "TCS"]
        armed = client.get("/api/bots/3/config").json()["trade_symbols"]
        assert "SBIN" in armed and "TCS" in armed
        assert "SBIN" not in client.get("/api/config").json()["trade_symbols"]
        assert client.delete(f"/api/scan-arm/presets/{pid}").status_code == 200
    database.reset_engine()
