"""Replay a past day on Groww candles: practice only, separate REPLAY book."""
from __future__ import annotations

import asyncio
import datetime as dt
import math

import pandas as pd
import pytest

from groww_client import IST
from replay import (
    ReplayBroker,
    ReplayEngine,
    ReplayFeed,
    ReplaySession,
    _path_price,
    parse_replay_day,
    parse_start,
)

DAY = dt.date(2026, 9, 29)  # a Tuesday


def _day_frame(day: dt.date, base: float = 1000.0, wave: float = 12.0) -> pd.DataFrame:
    """375 one-minute bars with a slow sine wave, so SMA 9 and 21 cross."""
    rows = []
    t0 = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for i in range(375):
        mid = base + wave * math.sin(i / 25.0)
        o = mid - 0.4
        c = mid + 0.4 if math.cos(i / 25.0) >= 0 else mid - 0.8
        rows.append(
            {
                "ts": int((t0 + dt.timedelta(minutes=i)).timestamp()),
                "open": o,
                "high": max(o, c) + 0.5,
                "low": min(o, c) - 0.5,
                "close": c,
                "volume": 1000 * (i + 1),  # Groww: running session total
            }
        )
    return pd.DataFrame(rows)


def _frames(symbols=("TCS",)) -> dict[str, pd.DataFrame]:
    out = {}
    for s in symbols:
        out[s] = pd.concat([_day_frame(DAY - dt.timedelta(days=1)), _day_frame(DAY)]).reset_index(drop=True)
    return out


def test_price_walks_open_low_high_close_inside_a_green_minute():
    assert _path_price(100, 104, 98, 103, 0.0)[0] == pytest.approx(100)
    assert _path_price(100, 104, 98, 103, 1 / 3)[0] == pytest.approx(98)
    assert _path_price(100, 104, 98, 103, 2 / 3)[0] == pytest.approx(104)
    price, hi, lo = _path_price(100, 104, 98, 103, 1.0)
    assert (price, hi, lo) == (pytest.approx(103), 104, 98)
    # A red minute goes to the high first.
    assert _path_price(100, 102, 95, 96, 1 / 3)[0] == pytest.approx(102)


def test_feed_hides_the_future_and_forms_the_current_bar():
    clock = dt.datetime.combine(DAY, dt.time(10, 0, 30), tzinfo=IST)
    feed = ReplayFeed(_frames(), clock)
    ltp, frame = feed.quote("TCS")
    minute = int(dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST).timestamp())
    assert int(frame["ts"].iloc[-1]) == minute  # the forming bar
    assert int(frame["ts"].iloc[-2]) == minute - 60  # last closed bar
    assert (frame["ts"] <= minute).all()  # nothing from the future
    assert frame["low"].iloc[-1] <= ltp <= frame["high"].iloc[-1]


def test_dates_and_times_are_checked():
    late = dt.datetime(2026, 10, 1, 20, 0, tzinfo=IST)
    assert parse_replay_day("2026-09-29", late) == DAY
    assert parse_replay_day("2026-10-01", late) == dt.date(2026, 10, 1)  # today, after 15:30
    with pytest.raises(ValueError, match="weekend"):
        parse_replay_day("2026-09-27", late)
    with pytest.raises(ValueError, match="already closed"):
        parse_replay_day("2026-10-01", dt.datetime(2026, 10, 1, 11, 0, tzinfo=IST))
    with pytest.raises(ValueError, match="already closed"):
        parse_replay_day("2026-10-02", late)
    assert parse_start("10:30") == dt.time(10, 30)
    with pytest.raises(ValueError):
        parse_start("08:00")


def test_replay_broker_cannot_reach_groww():
    broker = ReplayBroker(ReplayFeed(_frames(), dt.datetime.combine(DAY, dt.time(10), tzinfo=IST)))
    broker.set_mode("LIVE")
    assert broker.mode == "PAPER"
    assert not hasattr(broker, "_require_sdk")
    assert not hasattr(broker, "_live_limit")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/replay.db")
    sent: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda msg: sent.append(msg))
    import database

    database.reset_engine()
    database.init_db()
    from models import BotConfig

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.symbol = "TCS"
        row.trade_symbols = "TCS"
        row.qty = 10
        s.commit()
    yield sent
    database.reset_engine()


async def _run_day(engine: ReplayEngine, until: dt.time) -> None:
    end = dt.datetime.combine(DAY, until, tzinfo=IST)
    while engine.feed.clock < end:
        engine.feed.clock += dt.timedelta(seconds=30)
        await engine.tick(engine.feed.clock)


@pytest.mark.asyncio
async def test_bot_trades_the_replayed_day_into_the_replay_book(db):
    sent = db
    clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"])
    engine.load_config()
    await engine.tick(clock)
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    await _run_day(engine, dt.time(15, 20))

    from database import session_factory
    from models import TradeLog

    with session_factory()() as s:
        rows = s.query(TradeLog).all()
    assert rows, "the sine wave should give the bot at least one cross"
    assert {r.mode for r in rows} == {"REPLAY"}
    assert {r.date for r in rows} == {DAY.isoformat()}
    # 15:15 square-off happened on the replayed clock.
    assert all(r.exit_time is not None for r in rows)
    assert engine.status == "DAY_COMPLETED"
    # No Telegram/WhatsApp from a replay.
    assert sent == []
    snap = engine.snapshot()
    assert snap["mode"] == "REPLAY" and snap["data_source"] == "REPLAY"


@pytest.mark.asyncio
async def test_live_engine_ignores_replay_rows_after_a_restart(db):
    from database import session_factory
    from models import TradeLog
    from replay import close_orphan_replay_rows
    from strategy_engine import StrategyEngine

    with session_factory()() as s:
        s.add(
            TradeLog(
                date=DAY.isoformat(), symbol="TCS", direction="LONG", qty=10,
                entry_time=dt.datetime(2026, 9, 29, 10, 0), entry_price=1000.0, ma_cross_price=1000.0,
                atr_at_entry=1.0, sl_trigger_price=998.0, mode="REPLAY",
            )
        )
        s.commit()
    live = StrategyEngine(broker=ReplayBroker(ReplayFeed(_frames(), dt.datetime.now(IST))))
    live.restore_open_books()
    assert live.positions == {}
    assert close_orphan_replay_rows() == 1
    with session_factory()() as s:
        row = s.query(TradeLog).one()
    assert row.exit_reason == "REPLAY_STOPPED" and row.net_pnl == 0.0


@pytest.mark.asyncio
async def test_session_loads_plays_pauses_and_stops(db, monkeypatch):
    frames = _frames()

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return frames[symbol].copy()

    monkeypatch.setattr("replay.fetch_frame", fake_fetch)
    session = ReplaySession()
    await session.begin(object(), ["TCS"], DAY, dt.time(9, 30), 300)
    for _ in range(50):
        await asyncio.sleep(0.05)
        if session.status == "PLAYING" and session.engine.feed.clock > dt.datetime.combine(DAY, dt.time(9, 31), tzinfo=IST):
            break
    assert session.status == "PLAYING"
    info = session.info()
    assert info["date"] == DAY.isoformat() and info["symbols"] == ["TCS"]
    session.control("pause")
    paused_at = session.engine.feed.clock
    await asyncio.sleep(0.5)
    assert session.engine.feed.clock == paused_at
    with pytest.raises(ValueError):
        session.control("speed", 7)
    await session.stop()
    assert session.status == "IDLE" and session.engine is None


@pytest.mark.asyncio
async def test_a_holiday_reports_no_candles(db, monkeypatch):
    async def empty(broker, symbol, start, end):  # noqa: ARG001
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])

    monkeypatch.setattr("replay.fetch_frame", empty)
    session = ReplaySession()
    await session.begin(object(), ["TCS"], DAY, dt.time(9, 15), 60)
    for _ in range(40):
        await asyncio.sleep(0.05)
        if session.status == "ERROR":
            break
    assert session.status == "ERROR"
    assert "no 1-minute candles" in session.error


def test_api_refuses_replay_in_live_and_without_login(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app, engine

    monkeypatch.setattr(engine.broker, "token", "")
    monkeypatch.setattr(engine.broker, "adopt_saved_session", lambda **_: None)
    with TestClient(app) as client:
        assert client.get("/api/replay").json()["status"] == "IDLE"
        res = client.post("/api/replay/start", json={"date": "2026-09-29", "start": "09:15", "speed": 60})
        assert res.status_code == 400 and "Groww" in res.json()["detail"]
        assert client.post("/api/replay/start", json={"date": "2026-09-29", "speed": 7}).status_code == 400
        assert client.get("/api/replay/state").status_code == 409
        from models import BotConfig

        with database.session_factory()() as s:
            s.get(BotConfig, 1).trading_mode = "LIVE"
            s.commit()
        res = client.post("/api/replay/start", json={"date": "2026-09-29"})
        assert res.status_code == 409 and "PAPER" in res.json()["detail"]
    database.reset_engine()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_a_replay_has_no_daily_trade_cap(db):
    from models import BotConfig
    import database

    with database.session_factory()() as s:
        s.get(BotConfig, 1).max_trades_per_day = 2
        s.commit()
    clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"])
    engine.load_config()
    await engine.tick(clock)
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    await _run_day(engine, dt.time(15, 0))
    assert engine.trades_today > 2
    assert not (engine.halt_reason or "").startswith("max_trades_per_day")
    assert engine.snapshot()["max_trades"] is None


def test_a_replay_halted_on_an_old_cap_starts_again(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/cap.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        clock = dt.datetime.combine(DAY, dt.time(11, 0), tzinfo=IST)
        eng = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"])
        eng.trades_today = 40
        eng.status = "HALTED"
        eng.halt_reason = "max_trades_per_day (40) reached"
        monkeypatch.setattr(main.replay, "engine", eng)
        monkeypatch.setattr(main.replay, "status", "PAUSED")

        # The setting itself still takes 1-100 (it guards PAPER and LIVE).
        assert client.put("/api/config", json={"max_trades_per_day": 400}).status_code == 422
        # A replay has no cap, so one halted on the old cap simply starts again.
        assert client.post("/api/replay/bot/start").json()["bot_status"] == "RUNNING"
        monkeypatch.setattr(main.replay, "engine", None)
        monkeypatch.setattr(main.replay, "status", "IDLE")
    database.reset_engine()
    get_settings.cache_clear()


# --- multi-day runs -------------------------------------------------------


def test_replay_range_rules():
    from replay import parse_replay_range

    late = dt.datetime(2026, 10, 1, 20, 0, tzinfo=IST)
    assert parse_replay_range("2026-09-29", None, late) == (DAY, DAY)
    assert parse_replay_range("2026-09-01", "2026-09-30", late) == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    # A weekend start moves to Monday; a weekend end moves back to Friday.
    assert parse_replay_range("2026-09-26", "2026-10-04", dt.datetime(2026, 10, 6, 20, tzinfo=IST)) == (
        dt.date(2026, 9, 28),
        dt.date(2026, 10, 2),
    )
    # Six weeks fits (30 trading days); more does not.
    assert parse_replay_range("2026-08-18", "2026-09-30", late)[0] == dt.date(2026, 8, 18)
    with pytest.raises(ValueError, match="45 days or less"):
        parse_replay_range("2026-08-01", "2026-09-30", late)
    with pytest.raises(ValueError, match="on or after"):
        parse_replay_range("2026-09-29", "2026-09-28", late)
    with pytest.raises(ValueError, match="already closed"):
        parse_replay_range("2026-09-29", "2026-10-02", late)


def _wave_day(day: dt.date, base: float = 1000.0) -> pd.DataFrame:
    """A quick wave (a cross every ~20 minutes) with a running volume total."""
    rows = []
    t0 = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    for i in range(375):
        mid = base + 6 * math.sin(i / 6.0)
        o, c = mid - 0.3, mid + (0.5 if math.cos(i / 6.0) >= 0 else -0.5)
        rows.append(
            {
                "ts": int((t0 + dt.timedelta(minutes=i)).timestamp()),
                "open": o,
                "high": max(o, c) + 0.4,
                "low": min(o, c) - 0.4,
                "close": c,
                "volume": 1000 * (i + 1),
            }
        )
    return pd.DataFrame(rows)


@pytest.mark.asyncio
async def test_a_multi_day_run_plays_each_day_and_records_the_run(db, monkeypatch):
    import replay as replay_mod
    from database import session_factory
    from models import ReplayRun, TradeLog
    from replay import get_run, list_runs

    mon, tue, wed = dt.date(2026, 9, 28), dt.date(2026, 9, 29), dt.date(2026, 9, 30)
    warm = dt.date(2026, 9, 25)  # Friday before

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.concat([_wave_day(d) for d in (warm, mon, tue, wed)]).reset_index(drop=True)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    # Keep the test quick: each replayed day ends at 10:30, minute steps.
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(10, 30))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)

    session = ReplaySession()
    await session.begin(
        object(), ["TCS"], mon, dt.time(9, 15), 1_000_000, end_day=wed, settings={"qty": 10, "stop_type": "ATR"}
    )
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    info = session.info()
    assert info["days"] == [mon.isoformat(), tue.isoformat(), wed.isoformat()]
    assert info["days_total"] == 3 and info["day_index"] == 2

    with session_factory()() as s:
        run = s.get(ReplayRun, info["run_id"])
        assert (run.status, run.days_total, run.days_done) == ("FINISHED", 3, 3)
        trades = s.query(TradeLog).filter(TradeLog.run_id == run.id).all()
    assert trades and {t.mode for t in trades} == {"REPLAY"}
    assert {t.date for t in trades} <= {mon.isoformat(), tue.isoformat(), wed.isoformat()}
    assert len({t.date for t in trades}) >= 2  # trades on more than one replayed day
    assert all(t.exit_time is not None for t in trades)  # each day squared off

    detail = get_run(info["run_id"])
    assert detail["settings"] == {"qty": 10, "stop_type": "ATR"}
    assert [d["date"] for d in detail["days"]] == sorted(d["date"] for d in detail["days"])
    assert detail["days"][-1]["cumulative"] == pytest.approx(detail["totals"]["net"], abs=0.05)
    assert detail["totals"]["trades"] == len(trades)
    assert list_runs()[0]["id"] == info["run_id"]
    # Going back to today, or starting the next run, keeps this one FINISHED.
    await session.stop()
    await session.stop()
    with session_factory()() as s:
        assert s.get(ReplayRun, info["run_id"]).status == "FINISHED"


def test_day_rows_split_profit_loss_and_drawdown():
    from types import SimpleNamespace as NS
    from replay import _day_rows, _totals

    rows = [
        NS(date="2026-09-28", exit_price=1.0, gross_pnl=15.0, brokerage_and_taxes=2.0, net_pnl=13.0),
        NS(date="2026-09-28", exit_price=1.0, gross_pnl=-20.0, brokerage_and_taxes=2.0, net_pnl=-22.0),
        NS(date="2026-09-29", exit_price=1.0, gross_pnl=-30.0, brokerage_and_taxes=1.0, net_pnl=-31.0),
        NS(date="2026-09-30", exit_price=1.0, gross_pnl=50.0, brokerage_and_taxes=1.0, net_pnl=49.0),
        NS(date="2026-09-30", exit_price=None, gross_pnl=None, brokerage_and_taxes=None, net_pnl=None),
    ]
    days = _day_rows(rows)
    assert [(d["date"], d["trades"], d["profit"], d["loss"], d["gross"], d["net"]) for d in days] == [
        ("2026-09-28", 2, 15.0, -20.0, -5.0, -9.0),
        ("2026-09-29", 1, 0.0, -30.0, -30.0, -31.0),
        ("2026-09-30", 1, 50.0, 0.0, 50.0, 49.0),
    ]
    assert [d["cumulative"] for d in days] == [-9.0, -40.0, 9.0]
    total = _totals(days)
    assert (total["trades"], total["wins"], total["losses"]) == (4, 2, 2)
    assert (total["profit"], total["loss"], total["gross"], total["net"]) == (65.0, -50.0, 15.0, 9.0)
    assert total["max_drawdown"] == -40.0
    assert (total["green_days"], total["red_days"]) == (1, 2)


def test_runs_api_lists_details_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/runs.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main
    from models import ReplayRun, TradeLog

    with database.session_factory()() as s:
        run = ReplayRun(
            created_at=dt.datetime(2026, 10, 1, 20), start_date="2026-09-28", end_date="2026-09-29",
            symbols="TCS", settings='{"stop_type": "SMA_GAP"}', status="FINISHED", days_total=2, days_done=2,
        )
        s.add(run)
        s.commit()
        rid = run.id
        s.add(
            TradeLog(
                date="2026-09-28", symbol="TCS", direction="LONG", qty=10, entry_time=dt.datetime(2026, 9, 28, 10),
                entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=99.0,
                exit_time=dt.datetime(2026, 9, 28, 11), exit_price=101.0, gross_pnl=10.0,
                brokerage_and_taxes=1.0, net_pnl=9.0, mode="REPLAY", run_id=rid,
            )
        )
        s.commit()
    with TestClient(main.app) as client:
        runs = client.get("/api/replay/runs").json()
        assert runs[0]["id"] == rid and runs[0]["totals"]["net"] == 9.0
        assert runs[0]["settings"]["stop_type"] == "SMA_GAP"
        detail = client.get(f"/api/replay/runs/{rid}").json()
        assert detail["days"][0]["date"] == "2026-09-28"
        assert client.get("/api/replay/runs/9999").status_code == 404
        assert client.delete(f"/api/replay/runs/{rid}").status_code == 200
        assert client.get("/api/replay/runs").json() == []
    with database.session_factory()() as s:
        assert s.query(TradeLog).count() == 0
    database.reset_engine()
    get_settings.cache_clear()


def test_chart_markers_come_from_this_book_only(db):
    """Replaying the same day again must not stack its entries on the chart."""
    from database import session_factory
    from models import TradeLog
    from strategy_engine import StrategyEngine

    def trade(price: float, mode: str, run_id: int | None) -> TradeLog:
        return TradeLog(
            date=DAY.isoformat(), symbol="TCS", direction="LONG", qty=10,
            entry_time=dt.datetime(2026, 9, 29, 10, 0), entry_price=price, ma_cross_price=price,
            atr_at_entry=1.0, sl_trigger_price=price - 2, exit_time=dt.datetime(2026, 9, 29, 10, 30),
            exit_price=price + 1, exit_reason="MA_CROSS", mode=mode, run_id=run_id,
        )

    with session_factory()() as s:
        s.add_all([trade(1000.0, "REPLAY", 1), trade(1001.0, "REPLAY", 2), trade(1002.0, "PAPER", None)])
        s.commit()
    clock = dt.datetime.combine(DAY, dt.time(11, 0), tzinfo=IST)
    feed = ReplayFeed(_frames(), clock)

    run2 = ReplayEngine(feed, ["TCS"], run_id=2)
    run2.load_config()
    assert [m["price"] for m in run2.chart_payload()["markers"] if m["kind"] == "ENTRY"] == [1001.0]

    paper = StrategyEngine(broker=ReplayBroker(feed))
    paper.load_config()
    assert [m["price"] for m in paper.chart_payload()["markers"] if m["kind"] == "ENTRY"] == [1002.0]

    # The trade list says which run a replay trade belongs to, so the page can match it.
    assert {t["entry_price"]: t["run_id"] for t in paper.trades()} == {1000.0: 1, 1001.0: 2, 1002.0: None}


@pytest.mark.asyncio
async def test_the_same_day_gives_the_same_trades_at_any_speed(db, monkeypatch):
    """Ticks sit on a fixed grid, so speed and server load cannot change the result."""
    import replay as replay_mod
    from database import session_factory
    from models import BotConfig, TradeLog

    day, warm = dt.date(2026, 9, 29), dt.date(2026, 9, 28)

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.concat([_wave_day(warm), _wave_day(day)]).reset_index(drop=True)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(10, 30))
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)
    with session_factory()() as s:
        row = s.get(BotConfig, 1)
        # A trailing stop reads every tick, so it shows any change in where ticks land.
        row.stop_type, row.tsl_sl_points, row.tsl_trail_points = "TSL", 3.0, 1.0
        s.commit()

    async def play(speed: int) -> list[tuple]:
        session = ReplaySession()
        await session.begin(object(), ["TCS"], day, dt.time(9, 15), speed, settings={})
        for _ in range(2000):
            await asyncio.sleep(0.01)
            if session.status in ("FINISHED", "ERROR"):
                break
        assert session.status == "FINISHED", session.error
        with session_factory()() as s:
            rows = s.query(TradeLog).filter(TradeLog.run_id == session.run_id).order_by(TradeLog.id).all()
            out = [
                (t.direction, t.entry_time, t.entry_price, t.exit_time, t.exit_price, t.exit_reason, t.net_pnl)
                for t in rows
            ]
        await session.stop()
        return out

    slow = await play(1_237)
    fast = await play(9_871)
    assert slow, "the wave should trade"
    assert slow == fast
    # Every fill happened on the 10-second grid.
    assert all(t[1].second % 10 == 0 and t[1].microsecond == 0 for t in slow)


@pytest.mark.asyncio
async def test_each_closed_trade_records_its_highest_and_lowest_price(db, monkeypatch):
    """max_high / max_low span every price the trade saw, entry and exit included."""
    import replay as replay_mod
    from database import session_factory
    from models import TradeLog

    day, warm = dt.date(2026, 9, 29), dt.date(2026, 9, 28)

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.concat([_wave_day(warm), _wave_day(day)]).reset_index(drop=True)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(10, 30))
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)
    session = ReplaySession()
    await session.begin(object(), ["TCS"], day, dt.time(9, 15), 5_000, settings={})
    for _ in range(2000):
        await asyncio.sleep(0.01)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    bars = _wave_day(day)
    with session_factory()() as s:
        rows = s.query(TradeLog).filter(TradeLog.run_id == session.run_id, TradeLog.exit_price.isnot(None)).all()
        assert rows, "the wave should trade"
        for t in rows:
            assert t.max_high >= max(t.entry_price, t.exit_price)
            assert t.max_low <= min(t.entry_price, t.exit_price)
            # Never outside what the market printed while the trade was open.
            start, end = t.entry_time.replace(tzinfo=IST), t.exit_time.replace(tzinfo=IST)
            window = bars[(bars["ts"] >= int(start.timestamp()) - 60) & (bars["ts"] <= int(end.timestamp()))]
            assert t.max_high <= float(window["high"].max()) + 1e-6
            assert t.max_low >= float(window["low"].min()) - 1e-6
        # Ticks between entry and exit widen the range past the two fills.
        assert any(t.max_high > max(t.entry_price, t.exit_price) or t.max_low < min(t.entry_price, t.exit_price) for t in rows)
    await session.stop()


def test_each_trade_keeps_the_strategy_it_was_entered_with(db):
    """The blotter's Strategy column reads the settings saved on the trade at entry."""
    from database import session_factory
    from models import BotConfig, TradeLog
    from strategy_engine import StrategyEngine

    with session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.stop_type, row.tsl_sl_points, row.tsl_trail_points, row.use_vwap = "TSL", 7.0, 2.0, True
        s.commit()
    clock = dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST)
    engine = StrategyEngine(broker=ReplayBroker(ReplayFeed(_frames(), clock)))
    cfg = engine.load_config()
    trade_id = engine._insert_open_trade(
        cfg=cfg, direction="LONG", fill=100.0, cross_price=100.0, atr=1.0, sl=93.0, now=clock, qty=5
    )
    # A later settings change does not rewrite what the open trade used.
    with session_factory()() as s:
        s.get(BotConfig, 1).stop_type = "ATR"
        s.commit()
    saved = next(t for t in engine.trades() if t["id"] == trade_id)["strategy"]
    assert saved["stop_type"] == "TSL"
    assert (saved["tsl_sl_points"], saved["tsl_trail_points"], saved["use_vwap"]) == (7.0, 2.0, True)
    assert saved["qty"] == 5  # the trade's own size, not the configured default

    # Trades booked before this was recorded have no strategy, and say so.
    with session_factory()() as s:
        s.get(TradeLog, trade_id).strategy = None
        s.commit()
    engine._trades_cache = None  # trades() caches for a few seconds
    assert next(t for t in engine.trades() if t["id"] == trade_id)["strategy"] is None


def test_an_open_trade_shows_its_high_and_low_so_far(db):
    from strategy_engine import OpenPosition, StrategyEngine

    clock = dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST)
    engine = StrategyEngine(broker=ReplayBroker(ReplayFeed(_frames(), clock)))
    cfg = engine.load_config()
    trade_id = engine._insert_open_trade(
        cfg=cfg, direction="SHORT", fill=100.0, cross_price=100.0, atr=1.0, sl=103.0, now=clock, qty=5
    )
    pos = OpenPosition(
        direction="SHORT", qty=5, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0,
        sl_trigger=103.0, sl_order_id="", entry_order_id="", entry_time=clock, trade_id=trade_id,
        mode="PAPER", high=100.0, low=100.0,
    )
    for price in (101.5, 98.25, 0.0, 99.0):  # a zero quote is ignored
        pos.note_price(price)
    engine.positions["TCS"] = pos
    row = next(t for t in engine.trades() if t["id"] == trade_id)
    assert (row["max_high"], row["max_low"]) == (101.5, 98.25)
    # Nothing is written to the trade until it closes.
    engine.positions.clear()
    engine._trades_cache = None
    row = next(t for t in engine.trades() if t["id"] == trade_id)
    assert (row["max_high"], row["max_low"]) == (None, None)


def test_a_run_is_split_by_stock_with_each_stocks_days():
    import json as _json

    from models import TradeLog
    from replay import _day_rows, _stock_rows, _totals

    def trade(i, symbol, date, gross, charges=10.0, strategy=None):
        return TradeLog(
            id=i, symbol=symbol, date=date, direction="LONG", qty=10, entry_price=100.0,
            ma_cross_price=100.0, exit_price=101.0, gross_pnl=gross, brokerage_and_taxes=charges,
            net_pnl=gross - charges, strategy=_json.dumps(strategy) if strategy else None,
        )

    trades = [
        trade(1, "AAA", "2026-10-05", 50.0, strategy={"qty": 10, "use_rsi": True}),
        trade(2, "AAA", "2026-10-06", -20.0),
        trade(3, "BBB", "2026-10-05", 200.0, strategy={"qty": 5}),
    ]
    stocks = _stock_rows(trades, ["AAA", "BBB", "CCC"])
    assert [s["symbol"] for s in stocks] == ["BBB", "AAA", "CCC"]  # best net first, no-trade stocks last
    by = {s["symbol"]: s for s in stocks}
    assert by["AAA"]["totals"] == _totals(_day_rows(trades[:2]))
    assert [d["date"] for d in by["AAA"]["days"]] == ["2026-10-05", "2026-10-06"]
    assert by["AAA"]["strategy"] == {"qty": 10, "use_rsi": True}
    assert by["BBB"]["totals"]["net"] == 190.0
    assert by["CCC"]["totals"]["trades"] == 0 and by["CCC"]["days"] == [] and by["CCC"]["strategy"] is None
    # The stocks add up to the run.
    whole = _totals(_day_rows(trades))
    for key in ("trades", "wins", "losses", "profit", "loss", "charges", "net"):
        assert sum(s["totals"][key] for s in stocks) == pytest.approx(whole[key])


@pytest.mark.asyncio
async def test_reusing_the_chart_frame_between_closes_changes_nothing(db, monkeypatch):
    """The view cache only skips recomputing indicators between candle closes.

    A day replayed with it and without it must give the same trades and the
    same on-screen readings at every 10-second step.
    """
    from database import session_factory
    from models import TradeLog
    from strategy_engine import enrich

    async def play(recompute: bool):
        clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
        engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"], run_id=7 if recompute else 8)
        if recompute:
            monkeypatch.setattr(
                engine,
                "_view_frame",
                lambda view, frame, cfg: (enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period), True),
            )
        engine.load_config()
        await engine.tick(clock)
        engine.hold_for_next_cross(["TCS"])
        engine.status = "RUNNING"
        readings = []
        end = dt.datetime.combine(DAY, dt.time(11, 30), tzinfo=IST)
        while engine.feed.clock < end:
            engine.feed.clock += dt.timedelta(seconds=10)
            await engine.tick(engine.feed.clock)
            row = engine.candles.iloc[-1]
            readings.append(
                (engine.sma9, engine.sma21, engine.atr14, engine.adx14, engine.vwap, engine.rsi14, engine.ltp,
                 float(row["close"]), float(row["high"]), float(row["low"]))
            )
        with session_factory()() as s:
            trades = [
                (t.direction, t.entry_time, t.entry_price, t.exit_time, t.exit_price, t.exit_reason)
                for t in s.query(TradeLog).filter(TradeLog.run_id == engine.run_id).order_by(TradeLog.id)
            ]
        return trades, readings

    fresh_trades, fresh_readings = await play(recompute=True)
    cached_trades, cached_readings = await play(recompute=False)
    assert fresh_trades, "the sine-wave day should trade"
    assert cached_trades == fresh_trades
    assert cached_readings == fresh_readings


def test_api_replays_a_given_list_of_stocks_instead_of_the_armed_ones(tmp_path, monkeypatch):
    """The Scalp page's "Backtest these" replays a shortlist without arming it."""
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main
    from models import BotConfig

    with database.session_factory()() as s:
        s.get(BotConfig, 1).trade_symbols = "TCS"
        s.commit()
    seen = {}

    async def fake_begin(broker, symbols, day, start, speed, end_day=None, settings=None, **_):
        seen.update(symbols=symbols, day=day, end_day=end_day, speed=speed)

    monkeypatch.setattr(main.engine.broker, "token", "test-token")
    monkeypatch.setattr(main.replay, "begin", fake_begin)
    with TestClient(main.app) as client:
        body = {"date": "2026-09-28", "end_date": "2026-10-02", "speed": 300, "symbols": ["infy", "SBIN", "INFY"]}
        assert client.post("/api/replay/start", json=body).status_code == 200
        assert seen["symbols"] == ["INFY", "SBIN"]
        assert (seen["day"], seen["end_day"]) == (dt.date(2026, 9, 28), dt.date(2026, 10, 2))
        # Without a list, the armed stocks as before.
        assert client.post("/api/replay/start", json={"date": "2026-09-29"}).status_code == 200
        assert seen["symbols"] == ["TCS"]
        bad = client.post("/api/replay/start", json={"date": "2026-09-29", "symbols": ["TCS; DROP"]})
        assert bad.status_code == 400
        # Arming is untouched: the shortlist is not added to the Trade list.
        assert client.get("/api/config").json()["trade_symbols"] == ["TCS"]
    database.reset_engine()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_a_replay_plays_another_bots_settings_into_the_replay_book(db):
    """Bot 3's replay uses bot 3's settings row (id 4), tags its trades bot 3, and never writes that row."""
    import database
    from bots import ensure_bot_config
    from models import BotConfig, TradeLog

    ensure_bot_config(3)
    with database.session_factory()() as s:
        row = s.get(BotConfig, 4)
        row.qty, row.trade_symbols, row.symbol = 7, "TCS", "TCS"
        s.commit()
        before = {c.name: getattr(row, c.name) for c in BotConfig.__table__.columns}
    clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"], bot=3)
    cfg = engine.load_config()
    assert cfg.qty == 7 and cfg.trading_mode == "REPLAY"
    await engine.tick(clock)
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    await _run_day(engine, dt.time(15, 20))

    with database.session_factory()() as s:
        rows = s.query(TradeLog).all()
        after = {c.name: getattr(s.get(BotConfig, 4), c.name) for c in BotConfig.__table__.columns}
    assert rows and {r.mode for r in rows} == {"REPLAY"} and {r.bot for r in rows} == {3}
    assert {r.qty for r in rows} == {7}
    assert after == before  # the bot's own settings row is untouched


def test_api_replays_any_bot_and_refuses_only_that_bot_in_live(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main
    from models import BotConfig

    seen = {}

    async def fake_begin(broker, symbols, day, start, speed, end_day=None, settings=None, bot=1, bot_name=None, **_):
        seen.update(symbols=symbols, bot=bot, bot_name=bot_name, settings=settings)

    monkeypatch.setattr(main.engine.broker, "token", "test-token")
    for session in main.replays.values():  # each bot has its own replay player
        monkeypatch.setattr(session, "begin", fake_begin)
    with TestClient(main.app) as client:
        client.put("/api/bots/3/config", json={"bot_name": "Scalper"})
        client.post("/api/bots/3/trade-symbols", json={"symbol": "INFY", "armed": True})
        with database.session_factory()() as s:
            s.get(BotConfig, 1).trade_symbols = "TCS"
            s.get(BotConfig, 1).trading_mode = "LIVE"  # bot 1 on real money does not block bot 3's replay
            s.commit()
        res = client.post("/api/replay/start", json={"date": "2026-09-29", "bot": 3})
        assert res.status_code == 200, res.text
        assert seen["symbols"] == ["INFY"] and seen["bot"] == 3 and seen["bot_name"] == "Scalper"
        assert seen["settings"]["bot"] == 3 and seen["settings"]["bot_name"] == "Scalper"
        # Bot 1's own replay is still refused while bot 1 is LIVE.
        res = client.post("/api/replay/start", json={"date": "2026-09-29"})
        assert res.status_code == 409 and "PAPER" in res.json()["detail"]
        with database.session_factory()() as s:
            s.get(BotConfig, 4).trading_mode = "LIVE"
            s.commit()
        res = client.post("/api/replay/start", json={"date": "2026-09-29", "bot": 3})
        assert res.status_code == 409 and "Scalper" in res.json()["detail"]
        assert client.post("/api/replay/start", json={"date": "2026-09-29", "bot": 9}).status_code == 422
    database.reset_engine()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_a_replay_takes_margin_from_the_wallet_and_returns_its_pnl(db, monkeypatch):
    import paper_wallet
    import replay as replay_mod
    from database import session_factory
    from models import TradeLog

    mon, warm = dt.date(2026, 9, 28), dt.date(2026, 9, 25)

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        return pd.concat([_wave_day(d) for d in (warm, mon)]).reset_index(drop=True)

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(10, 30))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)
    paper_wallet.add(100)  # less than one entry's margin (10 x ~Rs 1,000 x 20%): the replay borrows

    session = ReplaySession()
    await session.begin(object(), ["TCS"], mon, dt.time(9, 15), 1_000_000, settings={"qty": 10, "stop_type": "ATR"})
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    with session_factory()() as s:
        trades = s.query(TradeLog).filter(TradeLog.run_id == session.info()["run_id"]).all()
    assert trades and all(t.exit_time is not None for t in trades)
    w = paper_wallet.summary()
    assert w["blocked"] == 0  # every margin came back at the square-off
    assert w["realized"] == pytest.approx(sum(t.gross_pnl for t in trades), abs=0.01)
    assert w["loan"] > 0 and "replay entry" in paper_wallet.loans()[-1]["note"]
    await session.stop()
