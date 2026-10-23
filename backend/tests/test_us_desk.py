"""US practice desk: New York clock, candles, signals, the practice engine on a fake Alpaca feed, the Groww check.

Practice only: the desk fills locally and has no order path; the fake feed stands in for Alpaca's data API.
"""
from __future__ import annotations

import datetime as dt

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import routes_us
from app.models.database import UsCredential, UsDeskConfig, UsPaperTrade
from app.services import alpaca, us_desk as ud, us_market
from app.services.us_market import ET

DAY = dt.date(2026, 10, 22)  # a Thursday, New York on daylight time


def et(hh: int, mm: int, ss: int = 0) -> dt.datetime:
    return dt.datetime(DAY.year, DAY.month, DAY.day, hh, mm, ss, tzinfo=ET)


def test_clock_hours_holidays_and_india_time():
    assert us_market.is_open(et(9, 30)) and us_market.is_open(et(15, 59))
    assert not us_market.is_open(et(9, 29)) and not us_market.is_open(et(16, 0))
    assert not us_market.is_open(dt.datetime(2026, 11, 26, 11, 0, tzinfo=ET))  # Thanksgiving
    assert not us_market.is_open(dt.datetime(2026, 11, 27, 13, 30, tzinfo=ET))  # early close
    assert not us_market.is_open(dt.datetime(2026, 10, 24, 11, 0, tzinfo=ET))  # Saturday
    s = us_market.status(et(8, 0))
    assert not s["open"] and s["next_open_et"] == "2026-10-22 09:30" and s["next_open_ist"] == "2026-10-22 19:00"
    winter = us_market.status(dt.datetime(2026, 11, 10, 8, 0, tzinfo=ET))
    assert winter["next_open_ist"] == "2026-11-10 20:00"  # after the US clocks go back
    assert us_market.next_open(dt.datetime(2026, 10, 23, 17, 0, tzinfo=ET)).date() == dt.date(2026, 10, 26)


def _bars(closes: list[float], start: dt.datetime) -> list[dict]:
    t0 = int(start.timestamp())
    return [
        {"ts": t0 + i * 60, "open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 100.0}
        for i, c in enumerate(closes)
    ]


def test_candles_start_at_the_open_and_drop_the_forming_one():
    frame = ud.to_frame(_bars([100 + i for i in range(12)], et(9, 30)))
    five = ud.resample(frame, 5)
    assert [dt.datetime.fromtimestamp(t, ET).strftime("%H:%M") for t in five["ts"]] == ["09:30", "09:35", "09:40"]
    assert list(five["open"]) == [100, 105, 110] and list(five["close"]) == [104, 109, 111]
    closed = ud.closed_candles(frame, 5, et(9, 41, 30).timestamp())
    assert len(closed) == 2  # 09:40 is still forming


def test_cross_and_charges():
    up = ud.enrich(ud.to_frame(_bars([10] * 5 + [9, 8, 7, 12], et(10, 0))), 2, 4, 3)
    assert ud.cross(up) == "BUY"
    down = ud.enrich(ud.to_frame(_bars([10] * 5 + [11, 12, 13, 8], et(10, 0))), 2, 4, 3)
    assert ud.cross(down) == "SELL"
    assert ud.cross(ud.enrich(ud.to_frame(_bars([10] * 9, et(10, 0))), 2, 4, 3)) is None
    assert ud.charges(100, 50.0) == pytest.approx(0.0000278 * 5000 + 0.0166)
    assert ud.charges(100_000, 1.0) == pytest.approx(0.0000278 * 100_000 + 8.30)
    assert ud.parse_symbols("aapl, msft;BRK.B  aapl x$") == ["AAPL", "MSFT", "BRK.B"]


def test_sim_feed_moves_and_keeps_minutes():
    sim = ud.SimFeed(seed=1)
    t = et(10, 0).timestamp()
    sim.tick(["AAPL"], t)
    first = sim.price("AAPL")
    sim.tick(["AAPL"], t + 185)
    frame = sim.frame("AAPL")
    assert first and sim.price("AAPL") and len(frame) >= 600
    assert frame["ts"].is_monotonic_increasing and frame["ts"].diff().dropna().eq(60).all()


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        for model in (UsDeskConfig, UsPaperTrade, UsCredential):
            await conn.run_sync(model.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ud, "async_session", maker)
    monkeypatch.setattr(routes_us, "async_session", maker)
    yield maker
    await engine.dispose()


class FakeAlpaca:
    """Alpaca's data API on a fixed tape: bars up to `now`, the latest price = the price at `now`."""

    tape: list[dict] = []
    now: float = 0.0

    def __init__(self, keys):
        self.keys = keys

    async def aclose(self):
        pass

    async def bars(self, symbols, start, end=None):
        lo = start.timestamp()
        return {s: [b for b in self.tape if lo <= b["ts"] and b["ts"] + 60 <= self.now] for s in symbols}

    async def latest(self, symbols):
        past = [b for b in self.tape if b["ts"] <= self.now]
        return {s: {"price": past[-1]["close"], "ts": past[-1]["ts"]} for s in symbols} if past else {}


async def _desk(db, monkeypatch, tape, **cfg):
    FakeAlpaca.tape = tape
    monkeypatch.setattr(alpaca, "AlpacaData", FakeAlpaca)
    desk = ud.UsDesk()

    async def keys():
        return alpaca.Keys("key-id-123", "secret-1234", "env")

    monkeypatch.setattr(desk, "keys", keys)
    async with db() as session:
        session.add(UsDeskConfig(id=1, running=True, symbols="AAPL", candle_minutes=1, sma_fast=3, sma_slow=6,
                                 atr_period=3, **cfg))
        await session.commit()
    return desk


async def _at(desk, when: dt.datetime):
    FakeAlpaca.now = when.timestamp()
    await desk.step(when.timestamp())


async def _trades(db):
    async with db() as session:
        from sqlalchemy import select

        return list((await session.scalars(select(UsPaperTrade).order_by(UsPaperTrade.id))).all())


@pytest.mark.asyncio
async def test_alpaca_feed_buys_the_cross_then_stops_and_squares_off(db, monkeypatch):
    # 09:30-09:59 falling, then rising: the 3/6 SMAs cross up on a closed 1-minute candle around 10:02.
    closes = [100 - i * 0.2 for i in range(30)] + [94.2 + i * 0.4 for i in range(30)]
    desk = await _desk(db, monkeypatch, _bars(closes, et(9, 30)), dollars_per_trade=1000)
    await _at(desk, et(9, 50))  # first look: nothing traded
    assert await _trades(db) == []
    for m in range(51, 60):
        await _at(desk, et(9, m, 5))
    for m in range(0, 10):
        await _at(desk, et(10, m, 5))
    rows = await _trades(db)
    assert len(rows) == 1 and rows[0].direction == "LONG" and rows[0].feed == "alpaca"
    assert rows[0].qty == int(1000 // rows[0].entry_price) and rows[0].stop_price < rows[0].entry_price
    assert rows[0].entry_reason == "SMA_CROSS" and rows[0].trade_day == "2026-10-22"
    # Square-off at 15:50 New York (the tape keeps its last price).
    await _at(desk, et(15, 50, 2))
    rows = await _trades(db)
    assert rows[0].status == "CLOSED" and rows[0].exit_reason == "SQUARE_OFF" and rows[0].pnl > 0
    assert rows[0].charges > 0


@pytest.mark.asyncio
async def test_stop_loss_and_no_entry_before_the_start(db, monkeypatch):
    closes = [100 - i * 0.2 for i in range(30)] + [94.2 + i * 0.4 for i in range(12)] + [80.0] * 20
    desk = await _desk(db, monkeypatch, _bars(closes, et(9, 30)))
    await _at(desk, et(9, 50))
    for m in range(51, 60):
        await _at(desk, et(9, m, 5))
    for m in range(0, 13):
        await _at(desk, et(10, m, 5))
    rows = await _trades(db)
    assert rows and rows[0].exit_reason == "STOP_LOSS" and rows[0].pnl < 0

    # With a 10:30 entry start the same 10:05 cross is refused.
    async with db() as session:
        cfg = await session.get(UsDeskConfig, 1)
        cfg.entry_start = "10:30"
        await session.commit()
    block = await desk.entry_block(await desk.config(), "LONG", et(10, 5), "2026-10-22")
    assert "before the entry start" in block


@pytest.mark.asyncio
async def test_stopped_desk_and_closed_market_do_not_trade(db, monkeypatch):
    closes = [100 - i * 0.2 for i in range(30)] + [94.2 + i * 0.4 for i in range(30)]
    desk = await _desk(db, monkeypatch, _bars(closes, et(9, 30)))
    async with db() as session:
        (await session.get(UsDeskConfig, 1)).running = False
        await session.commit()
    for m in range(50, 60):
        await _at(desk, et(9, m, 5))
    for m in range(0, 10):
        await _at(desk, et(10, m, 5))
    assert await _trades(db) == []
    weekend = dt.datetime(2026, 10, 24, 10, 0, tzinfo=ET)
    async with db() as session:
        (await session.get(UsDeskConfig, 1)).running = True
        await session.commit()
    await _at(desk, weekend)
    assert await _trades(db) == []


@pytest.mark.asyncio
async def test_simulated_feed_without_keys_and_manual_close(db, monkeypatch):
    desk = ud.UsDesk()

    async def no_keys():
        return None

    monkeypatch.setattr(desk, "keys", no_keys)
    async with db() as session:
        session.add(UsDeskConfig(id=1, running=True, symbols="AAPL", candle_minutes=1))
        await session.commit()
    await desk.step(et(10, 0).timestamp())
    snap = await desk.snapshot()
    assert desk.feed == "simulated" and snap["prices"]["AAPL"] > 0 and snap["feed"] == "simulated"
    row = await desk.open("AAPL", "SHORT", desk.prices["AAPL"], 1.0, await desk.config(), "2026-10-22", "TEST")
    closed = await desk.close_trade(row.id)
    assert closed.status == "CLOSED" and closed.exit_reason == "MANUAL_EXIT"


class FakeSdk:
    def __init__(self, ltp=None):
        self.ltp = ltp

    def get_quote(self, **kw):
        raise RuntimeError("Invalid exchange US")

    def get_ltp(self, **kw):
        if self.ltp is None:
            raise RuntimeError("Instrument not found")
        return {"US_AAPL": self.ltp}

    def place_order(self, **kw):  # pragma: no cover - must never be called
        raise AssertionError("the Groww US check must never order")


def test_groww_probe_reads_prices_only():
    no = ud.probe_groww(FakeSdk(), "AAPL")
    assert not no["ok"] and [t["call"] for t in no["tries"]] == ["quote", "ltp"]
    yes = ud.probe_groww(FakeSdk(ltp=231.5), "AAPL")
    assert yes["ok"] and yes["tries"][1]["price"] == 231.5


@pytest.mark.asyncio
async def test_groww_check_alerts_once(db, monkeypatch):
    from app.services.alert_notifier import alert_notifier

    sent = []

    async def send(msg):
        sent.append(msg)

    monkeypatch.setattr(alert_notifier, "send", send)
    monkeypatch.setattr(ud, "_groww_sdk", lambda: FakeSdk(ltp=231.5))
    first = await ud.run_groww_check("aapl", alert=True)
    again = await ud.run_groww_check("AAPL", alert=True)
    assert first["ok"] and again["ok"] and len(sent) == 1 and "AAPL" in sent[0]
    monkeypatch.setattr(ud, "_groww_sdk", lambda: None)
    none = await ud.run_groww_check("AAPL")
    assert not none["ok"] and "log in to Groww" in none["note"]


@pytest.mark.asyncio
async def test_api_config_and_keys_never_leak(db, monkeypatch):
    app = FastAPI()
    app.include_router(routes_us.router)
    monkeypatch.delenv("ALPACA_API_KEY_ID", raising=False)

    class FakeCheck(FakeAlpaca):
        async def account(self):
            if self.keys.key_id == "bad-key-id":
                raise alpaca.AlpacaError("Alpaca refused the key (401)")
            return {"status": "ACTIVE", "currency": "USD", "account": "paper"}

    monkeypatch.setattr(alpaca, "AlpacaData", FakeCheck)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        ok = await client.put("/api/us/config", json={"symbols": "aapl, nvda", "candle_minutes": 15})
        assert ok.status_code == 200 and ok.json()["symbols"] == ["AAPL", "NVDA"]
        assert (await client.put("/api/us/config", json={"candle_minutes": 7})).status_code == 422
        assert (await client.put("/api/us/config", json={"sma_fast": 30, "sma_slow": 21})).status_code == 422
        assert (await client.put("/api/us/config", json={"entry_cutoff": "16:30"})).status_code == 422
        assert (await client.get("/api/us/keys")).json() == {"saved": False, "env": False, "source": None}
        bad = await client.put("/api/us/keys", json={"key_id": "bad-key-id", "secret": "secret-123456"})
        assert bad.status_code == 400
        good = await client.put("/api/us/keys", json={"key_id": "PKTESTKEY123", "secret": "s3cret-value-xyz"})
        assert good.status_code == 200 and "s3cret" not in good.text and "PKTESTKEY123" not in good.text
        status = await client.get("/api/us/keys")
        assert status.json()["source"] == "saved" and "PKTEST" not in status.text
        async with db() as session:
            from sqlalchemy import select

            row = await session.scalar(select(UsCredential))
            assert row.key_id_encrypted != "PKTESTKEY123"
        assert (await client.delete("/api/us/keys")).json() == {"saved": False}
        assert (await client.post("/api/us/run", json={"running": True})).json() == {"running": True}


def test_routes_mounted_and_no_order_path():
    import inspect

    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/us/state", "/api/us/config", "/api/us/groww-check", "/api/us/keys"} <= paths
    source = inspect.getsource(ud) + inspect.getsource(alpaca) + inspect.getsource(routes_us)
    for word in ("place_order", "/v2/orders", "place_entry", "modify_order"):
        assert word not in source
