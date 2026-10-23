"""Options page: chain normalising, summary numbers, simulated chain and the practice book."""
from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.market_clock import IST
from app.models.database import OptionPaperTrade
from app.services import options
from app.services.options import OptionsError, Ticket

GROWW_CHAIN = {
    "underlying_ltp": 25012.5,
    "strikes": {
        "24900": {
            "CE": {"trading_symbol": "NIFTY25O2824900CE", "ltp": 160.0, "open_interest": 1000, "volume": 50,
                   "greeks": {"delta": 0.62, "gamma": 0.001, "theta": -9.1, "vega": 12.0, "rho": 3.0, "iv": 12.5}},
            "PE": {"trading_symbol": "NIFTY25O2824900PE", "ltp": 40.0, "open_interest": 5000, "volume": 80,
                   "greeks": {"delta": -0.38, "iv": 0.13}},
        },
        "25000": {
            "CE": {"trading_symbol": "NIFTY25O2825000CE", "ltp": 95.0, "open_interest": 3000, "volume": 90,
                   "greeks": {"iv": 12.0}},
            "PE": {"trading_symbol": "NIFTY25O2825000PE", "ltp": 82.0, "open_interest": 2000, "volume": 70,
                   "greeks": {"iv": 12.4}},
        },
        "25100": {
            "ce": {"trading_symbol": "NIFTY25O2825100CE", "ltp": 50.0, "open_interest": 6000, "volume": 40, "greeks": {}},
            "pe": {"trading_symbol": "NIFTY25O2825100PE", "ltp": 135.0, "open_interest": 500, "volume": 20, "greeks": {}},
        },
        "bad": {"CE": {}},
    },
}


def test_normalize_reads_groww_shape_and_sorts():
    spot, rows = options.normalize_chain(GROWW_CHAIN)
    assert spot == 25012.5
    assert [r["strike"] for r in rows] == [24900, 25000, 25100]
    assert rows[0]["ce"]["oi"] == 1000 and rows[0]["ce"]["delta"] == 0.62
    # lower-case leg keys too, and an IV sent as a fraction is shown in %
    assert rows[2]["ce"]["ltp"] == 50.0
    assert rows[0]["pe"]["iv"] == pytest.approx(13.0)


def test_summary_pcr_max_pain_and_walls():
    spot, rows = options.normalize_chain(GROWW_CHAIN)
    s = options.summarize(spot, rows)
    assert s["atm"] == 25000
    assert s["pcr"] == pytest.approx(7500 / 10000)
    assert s["resistance"] == 25100  # most call OI
    assert s["support"] == 24900  # most put OI
    assert s["straddle"] == 177.0
    assert s["max_pain"] == 25000


def test_black_scholes_put_call_parity():
    years = 7 / 365
    ce = options.black_scholes(25000, 25100, years, 0.13, "CE")
    pe = options.black_scholes(25000, 25100, years, 0.13, "PE")
    import math

    assert ce["price"] - pe["price"] == pytest.approx(25000 - 25100 * math.exp(-options.RATE * years), abs=1e-6)
    assert 0 < ce["delta"] < 1 and -1 < pe["delta"] < 0


def test_sim_chain_has_both_legs_around_spot():
    now = dt.datetime(2026, 10, 20, 11, 0, tzinfo=IST)
    raw = options.sim_chain("NIFTY", "2026-10-27", now)
    spot, rows = options.normalize_chain(raw)
    assert len(rows) == 2 * options.SIM_STRIKES_EACH_SIDE + 1
    assert rows[0]["strike"] < spot < rows[-1]["strike"]
    assert all(r["ce"]["ltp"] >= 0 and r["pe"]["ltp"] >= 0 for r in rows)


def test_sim_expiries_are_tuesdays_and_thursday_for_sensex():
    today = dt.date(2026, 10, 22)  # Thursday
    nifty = list(options.sim_expiries("NIFTY", today))
    assert nifty[0] == "2026-10-27" and all(dt.date.fromisoformat(d).weekday() == 1 for d in nifty)
    assert list(options.sim_expiries("SENSEX", today))[0] == "2026-10-22"


def test_charges_estimate():
    # 2 orders, ₹10,000 bought and ₹12,000 sold
    c = options.charges(10_000, 12_000, 2)
    brokerage, exchange, sebi = 40, 22_000 * options.EXCHANGE_TXN, 22_000 * options.SEBI
    want = brokerage + exchange + sebi + (brokerage + exchange + sebi) * 0.18 + 12_000 * 0.001 + 10_000 * 0.00003
    assert c == pytest.approx(round(want, 2))


@pytest_asyncio.fixture
async def session(monkeypatch):
    options.reset_for_tests()
    monkeypatch.setattr(options, "_groww_sdk", lambda: None)  # simulated feed, no Groww
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(OptionPaperTrade.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_practice_buy_then_close_on_simulated_feed(session, monkeypatch):
    expiry = list(options.sim_expiries("NIFTY"))[1]
    got = await options.chain("NIFTY", expiry)
    strike = got["summary"]["atm"]
    row = await options.place(session, Ticket("NIFTY", expiry, strike, "CE", "BUY", 2))
    assert row.feed == "simulated" and row.lot_size == 75 and row.quantity == 150
    assert row.status == "OPEN" and row.entry_price > 0

    # the price moves: the book marks it, and the close books P&L before charges
    options._chains.clear()
    leg = options._leg_of(got, strike, "CE")
    monkeypatch.setattr(options, "_mark", _fixed_mark(leg["ltp"] + 10))
    book = await options.book(session)
    assert book["positions"][0]["pnl"] == pytest.approx(10 * 150)
    closed = await options.close(session, row.id)
    assert closed.status == "CLOSED" and closed.pnl == pytest.approx(10 * 150)
    assert closed.charges and closed.charges > 40


@pytest.mark.asyncio
async def test_short_pnl_sign(session, monkeypatch):
    expiry = list(options.sim_expiries("BANKNIFTY"))[1]
    got = await options.chain("BANKNIFTY", expiry)
    strike = got["summary"]["atm"]
    row = await options.place(session, Ticket("BANKNIFTY", expiry, strike, "PE", "SELL", 1))
    monkeypatch.setattr(options, "_mark", _fixed_mark(row.entry_price - 5))
    closed = await options.close(session, row.id)
    assert closed.pnl == pytest.approx(5 * row.quantity)


@pytest.mark.asyncio
async def test_refusals(session):
    expiry = list(options.sim_expiries("NIFTY"))[1]
    with pytest.raises(OptionsError):
        await options.place(session, Ticket("NIFTY", expiry, 25000, "XX", "BUY", 1))
    with pytest.raises(OptionsError):
        await options.place(session, Ticket("NIFTY", expiry, 25000, "CE", "BUY", 0))
    with pytest.raises(OptionsError):
        await options.place(session, Ticket("NIFTY", "2020-01-02", 25000, "CE", "BUY", 1))
    with pytest.raises(OptionsError):  # a strike the chain does not have
        await options.place(session, Ticket("NIFTY", expiry, 1.5, "CE", "BUY", 1))
    with pytest.raises(OptionsError):
        await options.close(session, 999)


@pytest.mark.asyncio
async def test_groww_prices_fill_only_while_nse_open(session, monkeypatch):
    expiry = "2026-10-27"
    monkeypatch.setattr(options, "_groww_sdk", lambda: _FakeSdk())
    monkeypatch.setattr(options, "lot_size", _async(75))
    sunday = dt.datetime(2026, 10, 25, 11, 0, tzinfo=IST)
    with pytest.raises(OptionsError, match="NSE is closed"):
        await options.place(session, Ticket("NIFTY", expiry, 25000, "CE", "BUY", 1), now=sunday)
    monday = dt.datetime(2026, 10, 26, 11, 0, tzinfo=IST)
    row = await options.place(session, Ticket("NIFTY", expiry, 25000, "CE", "BUY", 1), now=monday)
    assert row.feed == "groww" and row.entry_price == 95.0 and row.trading_symbol == "NIFTY25O2825000CE"


@pytest.mark.asyncio
async def test_expired_position_settles_at_intrinsic(session):
    expiry = list(options.sim_expiries("NIFTY"))[0]
    await options.chain("NIFTY", expiry)
    spot = options._last_spot[("simulated", "NIFTY")]
    row = OptionPaperTrade(
        underlying="NIFTY", expiry=expiry, strike=round(spot) - 100, option_type="CE", trading_symbol="X",
        side="BUY", lots=1, lot_size=75, quantity=75, entry_price=50.0, feed="simulated", status="OPEN",
    )
    session.add(row)
    await session.commit()
    after = dt.datetime.combine(dt.date.fromisoformat(expiry), dt.time(16, 0), tzinfo=IST)
    assert await options.settle_expired(session, now=after) == 1
    await session.refresh(row)
    assert row.status == "CLOSED" and row.exit_reason == "EXPIRED"
    assert row.exit_price == pytest.approx(options.intrinsic(spot, row.strike, "CE"), abs=0.01)


@pytest.mark.asyncio
async def test_groww_refusal_is_explained(monkeypatch):
    options.reset_for_tests()

    class Refuses:
        def get_option_chain(self, *a, **k):
            raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(options, "_groww_sdk", lambda: Refuses())
    out = await options.chain("NIFTY", "2026-10-27")
    assert out["rows"] == [] and "paid API plan" in out["error"]


def test_route_is_mounted():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/options/chain", "/api/options/order", "/api/options/book"} <= paths


class _FakeSdk:
    def get_option_chain(self, exchange, underlying, expiry, timeout=None):
        assert exchange == "NSE" and underlying == "NIFTY"
        return GROWW_CHAIN


def _fixed_mark(price):
    async def mark(rows):
        return {r.id: price for r in rows}

    return mark


def _async(value):
    async def fn(*a, **k):
        return value

    return fn


def test_instrument_list_keeps_option_expiries_and_lots(monkeypatch):
    from app.services import instruments

    csv_text = (
        "exchange,trading_symbol,name,instrument_type,segment,series,isin,underlying_symbol,expiry_date,strike_price,lot_size,is_intraday\n"
        "NSE,RELIANCE,Reliance,EQ,CASH,EQ,INE1,,,,1,1\n"
        "NSE,RELIANCE25OCTFUT,,FUT,FNO,,,RELIANCE,2026-10-27,,500,0\n"
        "NSE,NIFTY26O2725000CE,,CE,FNO,,,NIFTY,2026-10-27,25000,75,0\n"
        "NSE,NIFTY26O2725000PE,,PE,FNO,,,NIFTY,2026-10-27,25000,75,0\n"
        "NSE,NIFTY26N0325000CE,,CE,FNO,,,NIFTY,2026-11-03,25000,65,0\n"
        "NSE,RELIANCE26O271400CE,,CE,FNO,,,RELIANCE,2026-10-27,1400,500,0\n"
    )

    class Res:
        def read(self):
            return csv_text.encode()

    monkeypatch.setattr(instruments.urllib.request, "urlopen", lambda *a, **k: Res())
    master = instruments.InstrumentMaster()
    assert master.option_expiries("nifty") == {"2026-10-27": 75, "2026-11-03": 65}
    assert master.option_underlyings() == ["NIFTY", "RELIANCE"]
    assert master.fno_stocks() == ["RELIANCE"]
    assert master.get("RELIANCE") is not None
