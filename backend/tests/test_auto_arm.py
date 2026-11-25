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
    for book in (*auto_arm._DONE.values(), *auto_arm._DONE_LIVE.values(), *auto_arm._SEEN.values()):
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
    auto_arm._SEEN["strong"].clear()
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
    # Volume vs usual floor: B read 3x usual, A unknown.
    scans[1]["rows"][1]["rvol"] = 3.0
    assert auto_arm.active_hits(scans, 75, "BOTH", 0, min_rvol=2) == [("09:42", "B", "SELL", 90.0)]


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
    # Each pick is reported as it is found (the replay bar names them while the reads run).
    found: list[tuple] = []
    auto_arm.picks_for_day(frames, DAY, auto_arm.AutoArmRule(**base, max_day=1, on_found=lambda *a: found.append(a)))
    assert found == [(got[0]["at"], got[0]["symbol"], got[0]["side"])]
    assert "on_found" not in rule.as_dict()


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


@pytest.mark.asyncio
async def test_live_bot_is_armed_only_with_arm_live_and_its_own_daily_limit():
    rows = [_strong(f"L{i}") for i in range(5)]
    scalp_alerts.save({"arm_strong_on": True, "arm_strong_bot": 2, "arm_max_day": 10})
    assert scalp_alerts.load().arm_live is False
    calls: list = []

    async def bots():
        return {2: {"mode": "LIVE", "armed": [], "name": "Bot 2"}}

    async def arm(bot, symbol, source, live=False):
        calls.append((bot, symbol, live))
        return None

    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False)
    assert calls == [] and "LIVE" in rec["error"]
    auto_arm._DONE["strong"].clear()
    scalp_alerts.save({"arm_live": True, "arm_live_max_day": 2})
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, notify=notify, now=NOW, check_market=False)
    assert rec["live"] is True and len(rec["armed"]) == 2 and len(rec["skipped"]) == 3
    assert all(c[2] is True for c in calls) and "(LIVE)" in rec["skipped"][0]["why"]
    assert "LIVE" in sent[0]
    assert scalp_alerts.save({"arm_live_max_day": 99}).arm_live_max_day == 10


def test_turning_live_arm_on_needs_confirm(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    res = client.put("/api/scalp-alerts", json={"arm_live": True})
    assert res.status_code == 409 and scalp_alerts.load().arm_live is False
    res = client.put("/api/scalp-alerts", json={"arm_live": True, "confirm_live": True})
    assert res.status_code == 200 and res.json()["arm_live"] is True
    # Already on: saving again (or another field) needs no confirm; turning it off never does.
    assert client.put("/api/scalp-alerts", json={"arm_live": True, "arm_live_max_day": 4}).status_code == 200
    assert client.put("/api/scalp-alerts", json={"arm_live": False}).status_code == 200
    assert scalp_alerts.load().arm_live is False


@pytest.mark.asyncio
async def test_paper_arms_earlier_in_the_day_do_not_use_up_the_live_limit():
    # Owner's case: the bot armed stocks in PAPER in the morning, then went LIVE; the shared count left the LIVE
    # bot with no room, so nothing was armed on it.
    scalp_alerts.save({"arm_strong_on": True, "arm_strong_bot": 2, "arm_max_day": 3,
                       "arm_live": True, "arm_live_max_day": 2})
    mode = {"now": "PAPER"}
    armed: list[str] = []
    calls: list = []

    async def bots():
        return {2: {"mode": mode["now"], "armed": list(armed), "name": "Bot 2"}}

    async def arm(bot, symbol, source, live=False):
        calls.append((symbol, live))
        armed.append(symbol)
        return None

    rec = await auto_arm.after_scan("strong", {"rows": [_strong(f"P{i}") for i in range(3)]}, bots=bots, arm=arm,
                                    now=NOW, check_market=False)
    assert rec["live"] is False and len(rec["armed"]) == 3
    # The bot goes LIVE (and the morning's stocks were taken off): new picks still arm on it, up to the LIVE limit.
    mode["now"] = "LIVE"
    armed.clear()
    later = NOW + dt.timedelta(minutes=30)
    rows = [_strong(f"N{i}") for i in range(3)]
    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=later, check_market=False)
    assert rec["live"] is True and rec["armed"] == ["N0", "N1"]
    assert "(LIVE)" in rec["skipped"][0]["why"]
    assert [c for c in calls if c[1]] == [("N0", True), ("N1", True)]
    # A stock armed in PAPER this morning can be armed on the LIVE bot too.
    auto_arm._DONE_LIVE["strong"].clear()
    rec = await auto_arm.after_scan("strong", {"rows": [_strong("P0")]}, bots=bots, arm=arm, now=later,
                                    check_market=False)
    assert rec["armed"] == ["P0"] and calls[-1] == ("P0", True)


def test_backtest_server_arms_the_desk_only_when_asked(monkeypatch):
    # Owner's case: auto-arm on the backtest server filled its own practice bots, the main desk got nothing.
    import main
    import server_role

    monkeypatch.setattr(server_role, "is_scanner", lambda: False)
    monkeypatch.setattr(server_role, "is_backtest", lambda: True)
    monkeypatch.setattr(server_role, "link_token", lambda: "t" * 32)
    assert scalp_alerts.load().arm_on == "here" and main._auto_arm_remote() is False
    scalp_alerts.save({"arm_on": "desk"})
    assert main._auto_arm_remote() is True and scalp_alerts.view()["desk_link"] is True
    # Without the link token the desk cannot be reached: its own bots.
    monkeypatch.setattr(server_role, "link_token", lambda: "")
    assert main._auto_arm_remote() is False
    # A bad value falls back to this server's own bots.
    assert scalp_alerts.save({"arm_on": "elsewhere"}).arm_on == "here"


def test_saved_scans_arm_at_each_stocks_own_read_and_skip_one_sided_books():
    scans = [
        # A pass that started 09:31 and read LATE at 09:44 arms it at 09:45, not 09:32.
        {"as_of": "2026-11-16T09:31:00+05:30", "rows": [
            {"symbol": "EARLY", "buy_share": 0.2, "value_cr": 9, "read_at": "2026-11-16T09:31:20+05:30"},
            {"symbol": "LATE", "buy_share": 0.8, "value_cr": 9, "read_at": "2026-11-16T09:44:10+05:30"},
            # 0 buy orders waiting: a bad quote, never "sellers 100%".
            {"symbol": "KAYNES", "buy_share": 0.0, "value_cr": 500, "read_at": "2026-11-16T09:31:40+05:30"},
            {"symbol": "ONESIDE", "buy_share": 1.0, "value_cr": 500, "read_at": "2026-11-16T09:31:50+05:30"},
        ]},
        {"as_of": "2026-11-16T09:45:00+05:30", "rows": [
            {"symbol": "KAYNES", "buy_share": 0.44, "value_cr": 500, "read_at": "2026-11-16T09:46:00+05:30"},
        ]},
    ]
    assert auto_arm.active_hits(scans, 75, "BOTH", 0) == [("09:32", "EARLY", "SELL", 80.0), ("09:45", "LATE", "BUY", 80.0)]


def _tape(days: list[dt.date], seed: int = 7, loud_from: int | None = None) -> pd.DataFrame:
    """1-minute candles (running volume total) for `days`; sessions from index `loud_from` on trade 4× the volume."""
    import numpy as np

    rng = np.random.default_rng(seed)
    rows, px = [], 1000.0
    for i, d in enumerate(days):
        total, drift = 0, rng.normal(0, 0.0005)
        mult = 4 if loud_from is not None and i >= loud_from else 1
        for m in range(375):
            t = dt.datetime.combine(d, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=m)
            o = px
            px *= 1 + drift + rng.normal(0, 0.0012)
            total += int(rng.integers(500, 5000)) * mult
            rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, px) * 1.0005, "low": min(o, px) * 0.9995,
                         "close": px, "volume": total})
    return pd.DataFrame(rows)


def _weekdays(start: dt.date, n: int) -> list[dt.date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


@pytest.mark.parametrize("ha", [False, True])
def test_backtest_read_matches_the_trend_quality_scan(ha):
    """The backtest's read of a minute equals the live scan's row at that minute (what History saved), though a
    multi-day run's tape carries every earlier day: the live scan downloads only WARMUP_DAYS calendar days."""
    import chop_scan

    days = _weekdays(dt.date(2026, 10, 19), 12)
    # Quiet early sessions, loud recent ones: the morning volume check flips if the backtest averages more sessions.
    tape = _tape(days, loud_from=8)
    keys = ("trend", "trend_score", "trend_checks", "trend_side", "trend_mode", "rvol", "trend_days")
    for day in days[9:]:
        for at in (dt.time(9, 50), dt.time(11, 0), dt.time(14, 10)):
            for minutes in (5, 15):
                now = dt.datetime.combine(day, at, tzinfo=IST)
                start = (now - dt.timedelta(days=chop_scan.WARMUP_DAYS)).replace(hour=0, minute=0)
                live = tape[(tape["ts"] >= int(start.timestamp())) & (tape["ts"] < int(now.timestamp()))]
                row = chop_scan.analyze(live.reset_index(drop=True), 9, 21, minutes, now, ha=ha).as_dict()
                read = auto_arm.strong_read(tape, day, at, minutes, 9, 21, ha=ha)
                assert {k: read.get(k) for k in keys if k in read} == {k: row.get(k) for k in keys if k in read}, (day, at, minutes)


def test_resumed_run_keeps_its_candles_and_sma_periods():
    import scalp_picks

    saved = auto_arm.AutoArmRule(candles="HA", min_rvol=2.0, sma_fast=5, sma_slow=13).as_dict()
    rule = scalp_picks.rule_from_dict(saved, 9, 21)
    assert (rule.candles, rule.min_rvol, rule.sma_fast, rule.sma_slow) == ("HA", 2.0, 5, 13)


def test_the_why_names_the_candle_and_rules_read():
    rule = auto_arm.AutoArmRule(minutes=15)
    read = {"trend_mode": "MORNING", "trend_checks": {"stack": True, "vwap": True, "slope": True, "fan": True,
                                                      "hold": True, "run": False, "volume": False}}
    assert auto_arm.strong_why(read, "DOWN", rule) == (
        "strong down, 5 of 7 checks on 15-min candles (morning rules, before 10:00); missed: run, volume")
    ha = auto_arm.AutoArmRule(minutes=5, candles="HA")
    assert auto_arm.strong_why({"trend_checks": {"a": True}}, "UP", ha) == "strong up, 1 of 1 checks on 5-min Heikin Ashi candles"


@pytest.mark.asyncio
async def test_force_order_when_armed_runs_right_after_each_new_arm():
    """"Force order when armed": the bot enters at once on each stock it was just armed with, per source."""
    rows = [_strong("UP"), _strong("DN", "STRONG_BEARISH")]
    scalp_alerts.save({"arm_strong_on": True, "arm_strong_bot": 1, "strong_min_checks": 6})
    forced: list = []

    async def force(bot, symbol, live):
        forced.append((bot, symbol, live))
        return (symbol == "UP"), ("BUY 1 UP" if symbol == "UP" else "the bot is stopped")

    bots, arm, calls = _hooks(armed=["DN"])
    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False, force=force)
    assert forced == [] and rec["forced"] == []  # switch off: arming only
    auto_arm._DONE["strong"].clear()
    auto_arm._SEEN["strong"].clear()
    scalp_alerts.save({"arm_strong_force": True})
    bots, arm, calls = _hooks()
    sent: list[str] = []

    async def notify(text):
        sent.append(text)

    rec = await auto_arm.after_scan("strong", {"rows": rows}, bots=bots, arm=arm, now=NOW, check_market=False,
                                    force=force, notify=notify)
    assert forced == [(1, "UP", False), (1, "DN", False)]
    assert rec["forced"] == [{"symbol": "UP", "placed": True, "note": "BUY 1 UP"},
                             {"symbol": "DN", "placed": False, "note": "the bot is stopped"}]
    assert "Force order: UP" in sent[0] and "No force order: DN" in sent[0]
    # The other source's switch is its own.
    assert auto_arm.forced("pressure", scalp_alerts.load()) is False


def test_force_with_a_live_bot_allowed_needs_confirm(monkeypatch):
    import main
    from fastapi.testclient import TestClient

    client = TestClient(main.app)
    assert client.put("/api/scalp-alerts", json={"arm_strong_force": True}).status_code == 200  # PAPER only: no ask
    client.put("/api/scalp-alerts", json={"arm_strong_force": False})
    assert client.put("/api/scalp-alerts", json={"arm_live": True, "confirm_live": True}).status_code == 200
    res = client.put("/api/scalp-alerts", json={"arm_pressure_force": True})
    assert res.status_code == 409 and scalp_alerts.load().arm_pressure_force is False
    res = client.put("/api/scalp-alerts", json={"arm_pressure_force": True, "confirm_live": True})
    assert res.status_code == 200 and scalp_alerts.load().arm_pressure_force is True


def test_replay_forces_only_the_picked_sources_switched_on():
    rule = auto_arm.AutoArmRule(force_strong=True)
    picked = [{"symbol": "a", "source": "Trend quality"}, {"symbol": "B", "source": "Most active"}]
    assert auto_arm.force_symbols(rule, picked) == {"A"}
    assert auto_arm.force_symbols(auto_arm.AutoArmRule(), picked) == set()
    import scalp_picks

    back = scalp_picks.rule_from_dict(auto_arm.AutoArmRule(force_pressure=True).as_dict(), 9, 21)
    assert back.force_pressure is True and back.force_strong is False
