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
