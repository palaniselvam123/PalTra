"""Most active stocks: ranked from Groww quotes, market data only."""
from __future__ import annotations

import datetime as dt

import pytest

from app.core.market_clock import IST
from app.services.active_stocks import ActiveScanner, row_from_quote, session_fraction

NOON = dt.datetime(2026, 10, 6, 12, 22, 30, tzinfo=IST)  # 187.5 of 375 minutes gone


def _quote(ltp, volume, avg, prev_close, buy=None, sell=None, high=None, low=None, open_=None):
    q = {
        "last_price": ltp,
        "volume": volume,
        "average_price": avg,
        "ohlc": {"open": open_ or prev_close, "high": high or ltp, "low": low or ltp, "close": prev_close},
    }
    if buy is not None:
        q["total_buy_quantity"] = buy
        q["total_sell_quantity"] = sell
    return q


def test_session_fraction():
    assert session_fraction(NOON) == pytest.approx(0.5)
    assert session_fraction(NOON.replace(hour=9, minute=16)) == pytest.approx(0.05)  # floored
    assert session_fraction(NOON.replace(hour=16, minute=0)) == 1.0


def test_a_row_reads_value_volume_vs_usual_pressure_and_bias():
    row = row_from_quote(
        "TCS", _quote(102.0, 2_000_000, 101.0, 100.0, buy=300_000, sell=100_000, high=103, low=99, open_=100),
        avg_daily_volume=1_000_000, fraction=0.5,
    )
    assert row.value_cr == pytest.approx(2_000_000 * 101.0 / 1e7)
    # 2M so far against 0.5M usual by midday: 4× the usual crowd.
    assert row.rvol == pytest.approx(4.0)
    assert row.change_pct == pytest.approx(2.0)
    assert row.vwap_dist_pct == pytest.approx((102 - 101) / 101 * 100)
    assert row.buy_share == pytest.approx(0.75)
    assert row.range_pct == pytest.approx(4.0)
    assert row.bias == "LONG"


def test_missing_fields_stay_unknown_and_no_volume_is_no_row():
    row = row_from_quote("X", {"last_price": 50, "volume": 1000}, None, 0.5)
    assert row.rvol is None and row.buy_share is None and row.avg_price is None and row.bias == "NONE"
    assert row_from_quote("X", {"last_price": 50, "volume": 0}, None, 0.5) is None
    assert row_from_quote("X", {"volume": 10}, None, 0.5) is None


class _FakeGroww:
    def __init__(self, quotes, fail=()):
        self.quotes = quotes
        self.fail = set(fail)
        self.daily_calls = 0

    async def get_quote_payload(self, symbol):
        if symbol in self.fail:
            raise RuntimeError("Groww quote fetch failed")
        return self.quotes[symbol]

    async def get_daily_volumes(self, symbol, days=20, end=None):
        self.daily_calls += 1
        assert end is not None and end.date() < NOON.date()  # today's partial day is left out
        return [1_000_000] * days

    async def place_order(self, *_a, **_k):  # pragma: no cover - must never be called
        raise AssertionError("the scan must not place orders")


@pytest.mark.asyncio
async def test_scan_ranks_by_money_traded_and_counts_failures(monkeypatch):
    monkeypatch.setattr("app.services.active_stocks.CALL_GAP_SEC", 0)
    quotes = {
        "BIG": _quote(1000.0, 3_000_000, 1000.0, 990.0, buy=1, sell=3),
        "SMALL": _quote(99.5, 500_000, 100.0, 101.0),
        "HOT": _quote(50.0, 4_000_000, 49.0, 48.0),
    }
    client = _FakeGroww(quotes, fail={"BROKEN"})
    scanner = ActiveScanner()
    await scanner.scan(client, symbols=["BIG", "SMALL", "HOT", "BROKEN"], now=NOON)
    snap = scanner.snapshot()
    assert [r["symbol"] for r in snap["rows"]] == ["BIG", "HOT", "SMALL"]
    assert snap["universe"] == 4 and snap["scanned"] == 3 and snap["failed"] == 1
    assert snap["error"] is None
    by_rvol = scanner.snapshot(sort="rvol")["rows"]
    assert by_rvol[0]["symbol"] == "HOT"  # 4M vs 0.5M usual by midday
    assert [r["symbol"] for r in scanner.snapshot(bias="SHORT")["rows"]] == ["SMALL"]
    # Averages are fetched once a day, not on every scan.
    calls = client.daily_calls
    await scanner.scan(client, symbols=["BIG", "SMALL", "HOT", "BROKEN"], now=NOON)
    assert client.daily_calls == calls


def test_fno_list_is_stocks_with_futures(monkeypatch):
    from app.services import instruments

    csv_text = (
        "exchange,exchange_token,trading_symbol,groww_symbol,name,instrument_type,segment,series,isin,"
        "underlying_symbol,underlying_exchange_token,expiry_date,strike_price,lot_size,tick_size,"
        "freeze_quantity,is_reserved,buy_allowed,sell_allowed,internal_trading_symbol,is_intraday\n"
        "NSE,1,RELIANCE,NSE-RELIANCE,Reliance,EQ,CASH,EQ,I1,,,,,1,0.1,,0,1,1,RELIANCE,1\n"
        "NSE,2,TINYCO,NSE-TINYCO,Tiny,EQ,CASH,EQ,I2,,,,,1,0.05,,0,1,1,TINYCO,1\n"
        "NSE,3,RELIANCE26OCTFUT,NSE-RF,Rel fut,FUT,FNO,,,RELIANCE,1,2026-10-27,,250,0.1,,0,1,1,X,1\n"
        "NSE,4,NIFTY26OCTFUT,NSE-NF,Nifty fut,FUT,FNO,,,NIFTY,9,2026-10-27,,75,0.1,,0,1,1,Y,1\n"
        "NSE,5,RELIANCE26OCT1500CE,NSE-RC,Rel call,CE,FNO,,,RELIANCE,1,2026-10-27,1500,250,0.05,,0,1,1,Z,1\n"
    )

    class _Resp:
        def read(self):
            return csv_text.encode()

    monkeypatch.setattr(instruments.urllib.request, "urlopen", lambda *_a, **_k: _Resp())
    master = instruments.InstrumentMaster()
    # NIFTY is an index future with no cash stock; TINYCO has no futures.
    assert master.fno_stocks() == ["RELIANCE"]


def test_the_api_needs_a_groww_session_to_scan(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_scalp
    from app.main import app

    monkeypatch.setattr(routes_scalp, "_groww_session", lambda: None)
    client = TestClient(app)
    body = client.get("/api/scalp/active").json()
    assert body["connected"] is False and "rows" in body
    assert client.get("/api/scalp/active", params={"sort": "nonsense"}).status_code == 400
    assert client.post("/api/scalp/active/scan").status_code == 428
