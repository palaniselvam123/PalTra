"""Auto-arm from the Scalp scans (auto_arm.py): live arming on the alert thresholds, and the auto-arm replay."""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

import auto_arm
import scalp_alerts
from tests.test_gap_mode import _day

IST = auto_arm.IST
DAY = dt.date(2026, 11, 2)
NOW = dt.datetime(2026, 11, 2, 11, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def _files(tmp_path, monkeypatch):
    monkeypatch.setenv("SCALP_ALERTS_FILE", str(tmp_path / "alerts.json"))
    for book in auto_arm._DONE.values():
        book.clear()
    auto_arm.recent.clear()


def _strong(symbol, trend="STRONG_BULLISH", passed=6):
    checks = {k: i < passed for i, k in enumerate(("stack", "vwap", "slope", "fan", "hold", "structure", "run"))}
    return {"symbol": symbol, "trend": trend, "trend_checks": checks, "ltp": 100.0, "day_move_pct": 1.0}


def _hooks(mode="PAPER", armed=()):
    calls: list[tuple[int, str, str]] = []

    async def bots():
        return {1: {"bot": 1, "name": "Bot 1", "mode": mode, "armed": list(armed)}}

    async def arm(bot, symbol, source):
        calls.append((bot, symbol, source))
        return None

    return bots, arm, calls


def test_settings_default_off_and_are_kept(tmp_path):
    s = scalp_alerts.load()
    assert (s.arm_strong_on, s.arm_pressure_on, s.arm_max_day) == (False, False, 10)
    s = scalp_alerts.save({"arm_strong_on": True, "arm_strong_bot": 3, "arm_max_day": 99, "arm_pressure_bot": 9})
    assert (s.arm_strong_on, s.arm_strong_bot, s.arm_max_day, s.arm_pressure_bot) == (True, 3, 50, 1)


@pytest.mark.asyncio
async def test_strong_trends_on_the_alert_threshold_are_armed_once():
    scalp_alerts.save({"arm_strong_on": True, "arm_strong_bot": 1, "strong_min_checks": 6, "strong_on": False})
    rows = [_strong("UP"), _strong("SEVEN", passed=7), _strong("WEAK", passed=5), _strong("DN", "STRONG_BEARISH"),
            {"symbol": "NONE", "trend": None, "trend_checks": {}}]
    bots, arm, calls = _hooks(armed=["DN"])
    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    # The alert's own switch is off: only its thresholds count. DN is already on the list; WEAK has 5 of 7.
    assert sorted(rec["armed"]) == ["SEVEN", "UP"] and rec["already"] == ["DN"]
    assert {c[2] for c in calls} == {"Auto-arm: Trend quality"}
    # The next scan does not arm them again.
    again = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    assert again is None and len(calls) == 2
    # Only the side the alert asks for.
    scalp_alerts.save({"strong_side": "DOWN"})
    auto_arm._DONE["strong"].clear()
    bots, arm, calls = _hooks()
    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    assert rec["armed"] == ["DN"]


@pytest.mark.asyncio
async def test_off_live_and_market_hours_are_refused_and_the_day_limit_holds():
    rows = [_strong(f"S{i}") for i in range(5)]
    bots, arm, calls = _hooks()
    assert await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False) is None
    scalp_alerts.save({"arm_strong_on": True, "arm_max_day": 2})
    # Outside market hours (a Sunday) nothing is armed.
    sunday = dt.datetime(2026, 11, 1, 11, 0, tzinfo=IST)
    assert await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=sunday) is None
    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    assert len(rec["armed"]) == 2 and len(rec["skipped"]) == 3 and "limit" in rec["skipped"][0]["why"]
    # A LIVE bot is never armed by itself.
    auto_arm._DONE["strong"].clear()
    bots, arm, calls = _hooks(mode="LIVE")
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, notify=notify, now=NOW, check_market=False)
    assert calls == [] and "LIVE" in rec["error"] and "PAPER" in sent[0]


@pytest.mark.asyncio
async def test_buyers_and_sellers_on_the_alert_threshold_are_armed():
    scalp_alerts.save({"arm_pressure_on": True, "arm_pressure_bot": 1, "pressure_pct": 75, "pressure_min_value_cr": 10})
    rows = [
        {"symbol": "BUYS", "buy_share": 0.8, "value_cr": 50},
        {"symbol": "SELLS", "buy_share": 0.2, "value_cr": 50},
        {"symbol": "EVEN", "buy_share": 0.6, "value_cr": 50},
        {"symbol": "THIN", "buy_share": 0.9, "value_cr": 2},
    ]
    bots, arm, calls = _hooks()
    rec = await auto_arm.after_scan("pressure", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    assert sorted(rec["armed"]) == ["BUYS", "SELLS"]
    assert {c[2] for c in calls} == {"Auto-arm: Most active"}


def test_saved_scans_give_the_first_minute_each_stock_reached_the_share():
    scans = [
        {"as_of": "2026-11-02T09:40:30+05:30", "rows": [{"symbol": "A", "buy_share": 0.6, "value_cr": 9}]},
        {"as_of": "2026-11-02T09:41:20+05:30", "rows": [{"symbol": "A", "buy_share": 0.8, "value_cr": 9},
                                                         {"symbol": "B", "buy_share": 0.1, "value_cr": 9}]},
        {"as_of": "2026-11-02T09:42:20+05:30", "rows": [{"symbol": "A", "buy_share": 0.9, "value_cr": 9}]},
        # An evening scan is no signal.
        {"as_of": "2026-11-02T19:00:00+05:30", "rows": [{"symbol": "C", "buy_share": 0.99, "value_cr": 9}]},
    ]
    assert auto_arm.active_hits(scans, 75, "BOTH", 0) == [("09:42", "A", "BUY", 80.0), ("09:42", "B", "SELL", 90.0)]
    assert auto_arm.active_hits(scans, 75, "SELL", 0) == [("09:42", "B", "SELL", 90.0)]
    assert auto_arm.active_hits(scans, 75, "BOTH", 10) == []


def _frame(step: float, wobble: float = 0.0) -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731
    return pd.DataFrame(_day(DAY - dt.timedelta(days=3), flat) + _day(DAY, lambda i, p: p + step + (wobble if i % 2 else -wobble)))


def test_the_replay_rule_arms_each_stock_at_its_minute():
    frames = {"UP": _frame(0.15), "FLAT": _frame(0.0, 0.5), "HIT": _frame(0.0, 0.5)}
    rule = auto_arm.AutoArmRule(source="BOTH", first=dt.time(10, 0), last=dt.time(12, 0), every_min=15, minutes=5,
                                min_checks=6, active_hits={DAY.isoformat(): [("09:50", "HIT", "BUY", 80.0),
                                                                             ("10:20", "HIT", "BUY", 85.0)]})
    got = auto_arm.picks_for_day(frames, DAY, rule)
    by = {p["symbol"]: p for p in got}
    # The 09:50 hit is before the first time; the one at 10:20 counts. UP is strong from the first read.
    assert set(by) == {"UP", "HIT"}
    assert by["UP"]["source"] == "Trend quality" and by["UP"]["side"] == "LONG"
    # The read is the Trend quality card's own (chop_scan.analyze): strong from the same minute, not before.
    import trend_picks

    def card(at):
        return trend_picks.read_at(frames["UP"], DAY, trend_picks.TrendPickRule(pick_time=at, minutes=5), "UP")["trend"]

    hh, mm = map(int, by["UP"]["at"].split(":"))
    armed_at = dt.time(hh, mm)
    before = (dt.datetime.combine(DAY, armed_at) - dt.timedelta(minutes=15)).time()
    assert card(armed_at) == "STRONG_BULLISH" and card(before) != "STRONG_BULLISH"
    assert auto_arm.strong_read(frames["UP"], DAY, armed_at, 5, 9, 21)["trend"] == card(armed_at)
    assert by["HIT"]["at"] == "10:20" and by["HIT"]["source"] == "Most active"
    assert [p["at"] for p in got] == sorted(p["at"] for p in got)  # oldest first
    assert len(auto_arm.picks_for_day(frames, DAY, auto_arm.AutoArmRule(**{**rule.__dict__, "max_day": 1}))) == 1
    windows = auto_arm.arm_windows(DAY, got)
    start = int(dt.datetime(2026, 11, 2, 10, 20, tzinfo=IST).timestamp())
    assert windows["HIT"][0][0] == start and windows["HIT"][0][1] > start
    from scalp_picks import picks_for_day

    assert picks_for_day(frames, DAY, rule) == got


@pytest.mark.asyncio
async def test_a_replay_trades_each_stock_only_from_the_minute_it_was_armed(monkeypatch, tmp_path):
    import json

    import replay as replay_mod
    from database import session_factory
    from models import ReplayRun, TradeLog
    from replay import ReplaySession

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    import database

    database.reset_engine()
    database.init_db()
    days = (dt.date(2026, 9, 25), dt.date(2026, 9, 28))
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        wave = lambda i, p: 1000.0 + 3.0 * ((i // 12) % 2)  # noqa: E731 - crosses all day
        return pd.DataFrame(_day(days[0], flat) + _day(days[1], wave))

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(12, 0))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)

    rule = auto_arm.AutoArmRule(source="ACTIVE", first=dt.time(9, 30), last=dt.time(11, 0), minutes=1,
                                active_hits={days[1].isoformat(): [("10:30", "AAA", "BUY", 80.0)]})
    session = ReplaySession()
    await session.begin(object(), ["AAA", "BBB"], days[1], dt.time(9, 30), 1_000_000, end_day=days[1],
                        settings={"qty": 10}, rule=rule)
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    assert [p["symbol"] for p in session.info()["picks"][days[1].isoformat()]] == ["AAA"]
    with session_factory()() as s:
        run = s.get(ReplayRun, session.run_id)
        assert json.loads(run.settings)["scalp_pick"]["kind"] == "AUTOARM"
        trades = s.query(TradeLog).filter(TradeLog.run_id == run.id).all()
    assert trades, "the wave crosses after 10:30, so the armed stock trades"
    assert all(t.symbol == "AAA" and t.entry_time.time() >= dt.time(10, 30) for t in trades)
    database.reset_engine()


def test_api_starts_and_refuses(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "scans.db"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        got = client.get("/api/scalp-alerts").json()
        assert got["arm_strong_on"] is False and "auto_arm" in got
        ok = client.put("/api/scalp-alerts", json={"arm_strong_on": True, "arm_strong_bot": 2, "arm_max_day": 5})
        assert ok.status_code == 200 and ok.json()["arm_strong_bot"] == 2
        assert client.put("/api/scalp-alerts", json={"arm_strong_bot": 7}).status_code == 422
        # No saved Most active scans: the Most active source has nothing to play.
        bad = client.post("/api/replay/auto-arm", json={"date": "2026-11-02", "source": "ACTIVE"})
        assert bad.status_code == 400 and "Most active" in bad.json()["detail"]
        # The Trend quality source needs stocks to read.
        bad = client.post("/api/replay/auto-arm", json={"date": "2026-11-02", "source": "TREND"})
        assert bad.status_code == 400
        bad = client.post("/api/replay/auto-arm", json={"date": "2026-11-02", "universe": ["SBIN"], "first": "15:20"})
        assert bad.status_code == 400
    database.reset_engine()


def test_the_reads_stop_once_the_days_picks_are_full_and_report_progress():
    frames = {"UP": _frame(0.15), "FLAT": _frame(0.0, 0.5)}
    seen: list[str] = []
    base = dict(source="TREND", first=dt.time(10, 0), last=dt.time(14, 0), every_min=15, minutes=5, min_checks=6)
    full = auto_arm.picks_for_day(frames, DAY, auto_arm.AutoArmRule(**base))
    rule = auto_arm.AutoArmRule(**base, max_day=1, on_step=seen.append)
    got = auto_arm.picks_for_day(frames, DAY, rule)
    # Same first pick; the reads stop at the step after it instead of running to 14:00.
    assert got == full[:1]
    after = (dt.datetime.combine(DAY, dt.time(*map(int, got[0]["at"].split(":")))) + dt.timedelta(minutes=15)).strftime("%H:%M")
    assert seen[0] == "10:00" and seen[-1] == after < "14:00"
    assert "on_step" not in rule.as_dict()


@pytest.mark.asyncio
async def test_the_price_range_limits_what_is_armed_live_and_in_the_replay():
    assert auto_arm.price_ok(None, 0, 0) and not auto_arm.price_ok(None, 100, 0)
    assert auto_arm.price_ok(150, 100, 200) and not auto_arm.price_ok(250, 100, 200) and auto_arm.price_ok(250, 100, 0)
    s = scalp_alerts.save({"arm_price_min": 500, "arm_price_max": 100})
    assert (s.arm_price_min, s.arm_price_max) == (100, 500)  # typed the wrong way round: swapped
    scalp_alerts.save({"arm_strong_on": True, "strong_min_checks": 6, "arm_price_min": 100, "arm_price_max": 500})
    rows = [dict(_strong("CHEAP"), ltp=50.0), dict(_strong("MID"), ltp=300.0), dict(_strong("DEAR"), ltp=900.0)]
    bots, arm, calls = _hooks()
    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    assert rec["armed"] == ["MID"]
    # Most active (saved scans): the row's last price.
    scans = [{"as_of": "2026-11-02T09:41:20+05:30", "rows": [
        {"symbol": "A", "buy_share": 0.9, "value_cr": 9, "ltp": 50.0},
        {"symbol": "B", "buy_share": 0.9, "value_cr": 9, "ltp": 300.0}]}]
    assert [h[1] for h in auto_arm.active_hits(scans, 75, "BOTH", 0, 100, 500)] == ["B"]
    # The replay's trend reads: the tape's price (about 1000 here) is outside ₹100-500, so nothing is armed.
    frames = {"UP": _frame(0.15)}
    base = dict(source="TREND", first=dt.time(10, 0), last=dt.time(12, 0), every_min=15, minutes=5, min_checks=6)
    assert auto_arm.picks_for_day(frames, DAY, auto_arm.AutoArmRule(**base))
    assert auto_arm.picks_for_day(frames, DAY, auto_arm.AutoArmRule(**base, price_min=100, price_max=500)) == []
    assert auto_arm.picks_for_day(frames, DAY, auto_arm.AutoArmRule(**base, price_min=500, price_max=5000))
