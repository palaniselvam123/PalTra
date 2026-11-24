"""All-bots day target / stop loss (all_bots_guard.py). PAPER only: fake engines and the API."""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)

import all_bots_guard as guard


class FakeEngine:
    def __init__(self, bot_id: int, closed: float, opened: float = 0.0):
        self.bot_id = bot_id
        self.closed = closed
        self.positions = {"SBIN": object()} if opened else {}
        self.opened = opened
        self.status = "RUNNING"
        self.halt_reason = ""
        self.killed: list[str] = []

    def load_config(self):
        return type("C", (), {"trading_mode": "PAPER"})()

    def _kpis(self, mode):  # noqa: ARG002
        return {"actual_gross": self.closed}

    def _open_pnl(self, name):  # noqa: ARG002
        return {"gross": self.opened, "net": self.opened}

    async def kill(self, reason: str) -> None:
        self.killed.append(reason)
        self.status, self.halt_reason, self.positions = "HALTED", reason, {}


def test_limits_and_verdict():
    cfg = type("C", (), {"all_bots_target": 5000, "all_bots_stop": "2000"})()
    assert guard.limits(cfg) == (5000.0, 2000.0)
    assert guard.limits(type("C", (), {"all_bots_target": -1, "all_bots_stop": None})()) == (0.0, 0.0)
    assert guard.verdict(5000, 5000, 2000) == "target"
    assert guard.verdict(-2000, 5000, 2000) == "stop"
    assert guard.verdict(4999, 5000, 2000) is None and guard.verdict(-1999, 5000, 2000) is None
    assert guard.verdict(1e6, 0, 0) is None  # both off


def test_total_adds_closed_and_open_of_every_bot():
    bots = [FakeEngine(1, 1200), FakeEngine(2, -300, opened=450.5), FakeEngine(3, 0), FakeEngine(4, 100)]
    assert guard.total(bots) == 1450.5


@pytest.mark.asyncio
async def test_hitting_the_target_panics_every_bot_once():
    bots = [FakeEngine(1, 3000), FakeEngine(2, 1500, opened=600), FakeEngine(3, 0), FakeEngine(4, -50)]
    cfg = type("C", (), {"all_bots_target": 5000, "all_bots_stop": 2000})()
    task = asyncio.create_task(guard.run(lambda: bots, lambda: cfg, lambda: True, poll=0.01))
    await asyncio.sleep(0.1)
    task.cancel()
    assert all(len(b.killed) == 1 for b in bots)  # every bot, flat or not, and only once
    assert bots[0].halt_reason.startswith(guard.HALT_PREFIX) and "target" in bots[0].halt_reason
    assert all(guard.is_guard_halt(b) for b in bots)


@pytest.mark.asyncio
async def test_stop_loss_and_closed_market_and_off():
    cfg = type("C", (), {"all_bots_target": 0, "all_bots_stop": 1000})()
    losing = [FakeEngine(1, -700), FakeEngine(2, -400)]
    closed_market = [FakeEngine(1, -5000)]
    off = [FakeEngine(1, 99999)]
    tasks = [
        asyncio.create_task(guard.run(lambda: losing, lambda: cfg, lambda: True, poll=0.01)),
        asyncio.create_task(guard.run(lambda: closed_market, lambda: cfg, lambda: False, poll=0.01)),
        asyncio.create_task(
            guard.run(lambda: off, lambda: type("C", (), {"all_bots_target": 0, "all_bots_stop": 0})(), lambda: True, poll=0.01)
        ),
    ]
    await asyncio.sleep(0.1)
    for t in tasks:
        t.cancel()
    assert all(b.killed and "stop loss" in b.halt_reason for b in losing)
    assert not closed_market[0].killed  # only while NSE is open
    assert not off[0].killed  # 0 / 0 = off


def test_api_saves_limits_and_start_is_refused_until_they_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app, engine
    from models import TradeLog

    with TestClient(app) as client:
        got = client.get("/api/bots/guard").json()
        assert got["target"] == 0 and got["stop"] == 0 and got["hit"] is None
        assert client.put("/api/bots/guard", json={"target": -1, "stop": 0}).status_code == 422
        today = engine._now().date()
        # The engine is shared with earlier test files: read today's book, whatever day one left it on.
        monkeypatch.setattr(engine, "_session_date", today.isoformat())
        with database.session_factory()() as s:
            s.add(TradeLog(date=today.isoformat(), symbol="SBIN", direction="LONG", qty=10, mode="PAPER", bot=1,
                           entry_time=dt.datetime.combine(today, dt.time(10)), entry_price=100.0, ma_cross_price=100.0,
                           atr_at_entry=1.0, sl_trigger_price=98.0, exit_time=dt.datetime.combine(today, dt.time(11)),
                           exit_price=110.0, gross_pnl=100.0, net_pnl=80.0, exit_reason="MA_CROSS"))
            s.commit()
        saved = client.put("/api/bots/guard", json={"target": 50, "stop": 500}).json()
        assert saved["target"] == 50 and saved["total"] == 100.0 and saved["hit"] == "target"
        # As the watcher would: the target is hit, the bot is halted by the guard.
        engine.status, engine.halt_reason = "HALTED", f"{guard.HALT_PREFIX} target hit: test"
        assert client.post("/api/bot/start").status_code == 423  # still hit: stays stopped today
        client.put("/api/bots/guard", json={"target": 1000, "stop": 500})
        assert client.post("/api/bot/start").status_code == 200  # limits raised: may start again
        engine.status = "STOPPED"
    database.reset_engine()


def test_squareoff_time_rules():
    assert guard.parse_time("15:10") == dt.time(15, 10)
    assert guard.parse_time("") is None and guard.parse_time("25:00") is None and guard.parse_time("9:30") is None
    assert guard.parse_time("08:00") is None and guard.parse_time("15:45") is None  # outside the session
    at = dt.time(15, 10)
    day = dt.date(2026, 10, 20)
    assert guard.verdict(0, 0, 0, dt.datetime.combine(day, dt.time(15, 9)), at) is None
    assert guard.verdict(0, 0, 0, dt.datetime.combine(day, dt.time(15, 10)), at) == "time"
    assert guard.verdict(9999, 5000, 0, dt.datetime.combine(day, dt.time(10)), None) == "target"


@pytest.mark.asyncio
async def test_squareoff_time_panics_every_bot_whatever_the_pnl():
    bots = [FakeEngine(1, 50, opened=20), FakeEngine(2, -10)]
    cfg = type("C", (), {"all_bots_target": 0, "all_bots_stop": 0, "all_bots_squareoff": "15:10"})()
    late = lambda: dt.datetime(2026, 10, 20, 15, 10, 30)  # noqa: E731
    early = lambda: dt.datetime(2026, 10, 20, 15, 9, 50)  # noqa: E731
    before = [FakeEngine(1, 50, opened=20)]
    tasks = [
        asyncio.create_task(guard.run(lambda: bots, lambda: cfg, lambda: True, poll=0.01, now_fn=late)),
        asyncio.create_task(guard.run(lambda: before, lambda: cfg, lambda: True, poll=0.01, now_fn=early)),
    ]
    await asyncio.sleep(0.1)
    for t in tasks:
        t.cancel()
    assert all(len(b.killed) == 1 and "square-off time 15:10" in b.halt_reason for b in bots)
    assert not before[0].killed


def test_api_saves_and_checks_the_squareoff_time(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/bots/guard").json()["squareoff"] == ""
        assert client.put("/api/bots/guard", json={"target": 0, "stop": 0, "squareoff": "16:00"}).status_code == 422
        saved = client.put("/api/bots/guard", json={"target": 0, "stop": 0, "squareoff": "15:05"}).json()
        assert saved["squareoff"] == "15:05"
        kept = client.put("/api/bots/guard", json={"target": 100, "stop": 0}).json()  # left out = unchanged
        assert kept["squareoff"] == "15:05" and kept["target"] == 100
        off = client.put("/api/bots/guard", json={"target": 0, "stop": 0, "squareoff": ""}).json()
        assert off["squareoff"] == ""
    database.reset_engine()


async def _replay_day(database, **row1):
    """Play a test day the way a backtest does, with the all-bots settings on bot 1's row."""
    import math

    import pandas as pd

    from models import BotConfig
    from replay import IST, SESSION_END, SESSION_OPEN, ReplayFeed, _to_next_grid
    from sma_research.replayer import ResearchEngine, _trade_rows
    from tests.test_gap_mode import _day, _settings

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        for key, value in row1.items():
            setattr(row, key, value)
        s.commit()
    day = dt.date(2026, 9, 22)
    flat = lambda i, p: 1000.0  # noqa: E731
    wave = lambda i, p: 1000.0 + 3 * math.sin(i * 2 * math.pi / 40)  # noqa: E731
    frame = pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(day, wave))
    feed = ReplayFeed({"SIMG": frame}, dt.datetime.combine(day, SESSION_OPEN, tzinfo=IST))
    eng = ResearchEngine(feed, "SIMG", _settings(database))
    eng.day_guard = True  # the research baseline turns it off; a desk backtest has it on
    first = eng._min_trade_id
    eng.load_config()
    await eng.tick(feed.clock)
    eng.hold_for_next_cross(["SIMG"])
    eng.status = "RUNNING"
    end = dt.datetime.combine(day, SESSION_END, tzinfo=IST)
    while feed.clock < end:
        feed.clock = min(end, feed.clock + dt.timedelta(seconds=_to_next_grid(feed.clock)))
        await eng.tick(feed.clock)
    return eng, _trade_rows(first)


@pytest.mark.asyncio
async def test_a_backtest_squares_off_at_the_all_bots_time(db):  # noqa: F811
    _, plain = await _replay_day(db)
    assert any(t["entry_time"] >= dt.datetime(2026, 9, 22, 12) for t in plain)  # trades all day without it
    eng, rows = await _replay_day(db, all_bots_squareoff="11:00")
    assert eng.status == "HALTED" and "square-off time 11:00" in eng.halt_reason
    assert rows and all(t["entry_time"] < dt.datetime(2026, 9, 22, 11, 1) for t in rows)
    assert all(t["exit_time"] is not None and t["exit_time"] <= dt.datetime(2026, 9, 22, 11, 1) for t in rows)


@pytest.mark.asyncio
async def test_a_backtest_stops_at_the_all_bots_target(db):  # noqa: F811
    _, plain = await _replay_day(db)
    eng, rows = await _replay_day(db, all_bots_target=1.0)  # the first ₹1 of profit trips it
    assert any(t["gross"] > 1 for t in plain)  # the wave day has winning trades
    assert eng.status == "HALTED" and "target" in eng.halt_reason
    assert len(rows) < len(plain)


@pytest.mark.asyncio
async def test_a_backtest_day_ends_once_the_bot_is_halted_and_flat(monkeypatch, tmp_path):
    """Owner's request: after the day target / stop loss / square-off time, the replay no longer plays empty minutes
    to 15:30; that day finishes and the next one starts."""
    import pandas as pd

    import database
    import replay as replay_mod
    from models import BotConfig
    from replay import ReplaySession
    from tests.test_gap_mode import _day

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    database.reset_engine()
    database.init_db()
    with database.session_factory()() as s:
        row = s.get(BotConfig, 1) or BotConfig(id=1)
        row.all_bots_squareoff = "10:30"
        s.add(row)
        s.commit()
    days = (dt.date(2026, 9, 24), dt.date(2026, 9, 25), dt.date(2026, 9, 28))
    wave = lambda i, p: 1000.0 + 3.0 * ((i // 12) % 2)  # noqa: E731

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.DataFrame([bar for d in days for bar in _day(d, wave)])

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)
    ends: list[dt.datetime] = []
    finish = ReplaySession._finish_day

    async def note_end(self):
        ends.append(self.engine.feed.clock)
        await finish(self)

    monkeypatch.setattr(ReplaySession, "_finish_day", note_end)
    session = ReplaySession()
    await session.begin(object(), ["AAA"], days[1], dt.time(9, 15), 1_000_000, end_day=days[2], settings={"qty": 10})
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    assert len(ends) == 2 and all(e.time() <= dt.time(10, 31) for e in ends), ends
    assert "rest of the replayed day skipped" in session.engine.halt_reason
    database.reset_engine()


def test_profit_safety_net_floor_moves_up_with_the_best():
    off = type("C", (), {"all_bots_trail": False})()
    assert guard.trail_rule(off) is None
    cfg = type("C", (), {"all_bots_trail": True, "all_bots_trail_start": 5000, "all_bots_trail_keep": 70,
                         "all_bots_trail_start2": 10000, "all_bots_trail_keep2": 75})()
    rule = guard.trail_rule(cfg)
    assert guard.trail_floor(4999, rule) is None
    assert guard.trail_floor(5000, rule) == 3500
    assert guard.trail_floor(8000, rule) == 5600
    assert guard.trail_floor(20000, rule) == 15000
    assert guard.verdict(3600, 0, 4000, floor=3500) is None
    assert guard.verdict(3500, 0, 4000, floor=3500) == "trail"
    assert guard.verdict(-4000, 0, 4000, floor=None) == "stop"


@pytest.mark.asyncio
async def test_profit_safety_net_panics_once_the_pnl_falls_back(monkeypatch):
    monkeypatch.setattr(guard, "_PEAK", {"day": None, "peak": None})
    monkeypatch.setattr(guard, "seed_peak", lambda day: None)
    bot = FakeEngine(1, 0.0, opened=6000)
    cfg = type("C", (), {"all_bots_target": 0, "all_bots_stop": 4000, "all_bots_trail": True,
                         "all_bots_trail_start": 5000, "all_bots_trail_keep": 70,
                         "all_bots_trail_start2": 10000, "all_bots_trail_keep2": 75})()
    task = asyncio.create_task(guard.run(lambda: [bot], lambda: cfg, lambda: True, poll=0.01))
    await asyncio.sleep(0.05)
    assert not bot.killed, "best ₹6,000, net ₹4,200: still running"
    bot.opened = 4300
    await asyncio.sleep(0.05)
    assert not bot.killed
    bot.opened = 4200
    await asyncio.sleep(0.05)
    task.cancel()
    assert len(bot.killed) == 1 and "safety net" in bot.halt_reason and "4,200" in bot.halt_reason


@pytest.mark.asyncio
async def test_a_backtest_stops_at_the_profit_safety_net(db):  # noqa: F811
    _, plain = await _replay_day(db)
    eng, rows = await _replay_day(db, all_bots_trail=True, all_bots_trail_start=1.0, all_bots_trail_keep=99.0,
                                  all_bots_trail_start2=0.0)
    assert eng.status == "HALTED" and "safety net" in eng.halt_reason
    assert len(rows) < len(plain)


def test_levels_in_percent_of_capital():
    cfg = type("C", (), {"all_bots_unit": "PCT", "all_bots_target": 9999, "all_bots_stop": 9999,
                         "all_bots_target_pct": 2.0, "all_bots_stop_pct": 0.8, "all_bots_trail": True,
                         "all_bots_trail_start_pct": 1.0, "all_bots_trail_keep": 70,
                         "all_bots_trail_start2_pct": 2.0, "all_bots_trail_keep2": 75})()
    assert guard.limits(cfg, 500_000) == (10_000, 4_000)
    assert guard.trail_rule(cfg, 500_000) == (5_000, 70, 10_000, 75)
    assert guard.limits(cfg, 0) == (0, 0) and guard.trail_rule(cfg, 0) is None  # no money loaded: off
    cfg.all_bots_unit = "RS"
    assert guard.limits(cfg, 500_000) == (9999, 9999)


def test_api_saves_percent_levels(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/pct.db")
    monkeypatch.setenv("OPEN_PNL_FILE", str(tmp_path / "pnl.db"))
    import database

    database.reset_engine()
    database.init_db()
    import paper_wallet
    from fastapi.testclient import TestClient
    from main import app

    paper_wallet.add(500_000)
    with TestClient(app) as client:
        got = client.put("/api/bots/guard", json={"target": 0, "stop": 0, "unit": "PCT", "target_pct": 2,
                                                  "stop_pct": 0.8}).json()
        assert got["unit"] == "PCT" and got["capital"] == 500_000
        assert (got["effective"]["target"], got["effective"]["stop"]) == (10_000, 4_000)
        assert got["effective"]["stop"] == 4_000 and got["stop_pct"] == 0.8
    database.reset_engine()
