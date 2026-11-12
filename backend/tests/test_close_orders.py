"""After-close orders scan (close_orders.py): orders waiting, the last hour, delivery, OI and short selling read into
real demand vs short sellers buying back. No network."""
from __future__ import annotations

import asyncio
import datetime as dt

from app.core.market_clock import IST
from app.services import close_orders as co

DAY = dt.date(2026, 11, 12)


def candles(path, vol_per_min=100, end_rush=1.0):
    """1-minute candles from a list of closes (one a minute from 09:15), Groww-style running volume."""
    out, cum, prev = [], 0, path[0]
    open_ts = int(dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST).timestamp())
    for i, c in enumerate(path):
        ts = open_ts + i * 60
        late = dt.datetime.fromtimestamp(ts, IST).time() >= co.RUSH_FROM
        cum += vol_per_min * (end_rush if late else 1)
        out.append((ts, prev, max(prev, c) + 0.05, min(prev, c) - 0.05, c, cum))
        prev = c
    return out


def late_jump():
    """Down 1% until 14:30 (315 minutes), then up 2% in the last hour."""
    return [100 - i * (1 / 315) for i in range(315)] + [99 + i * (2 / 60) for i in range(60)]


def test_short_sellers_buying_back():
    rows = candles(late_jump(), end_rush=3)
    quote = {"last_price": 101.0, "volume": 40000, "ohlc": {"close": 100.0}, "total_buy_quantity": 1000, "total_sell_quantity": 1000}
    r = co.CloseOrderScanner.row("ABC", quote, rows, DAY, {"pct": 20.0, "avg_pct": 45.0}, {"ABC": -6.0}, {"ABC": 2000.0}, {"ABC"})
    assert r["verdict"] == "SHORT_COVER" and r["cover_points"] >= 6
    assert r["move_to_1430_pct"] < 0 and r["last_hour_pct"] > 1.5 and r["end_rush"] > 2
    assert any("open interest fell" in w for w in r["cover_why"]) and r["short_pct"] == 5.0
    assert not r["unusual_buy"]


def test_real_demand_after_the_close():
    rows = candles([100 + i * 0.01 for i in range(375)])
    quote = {"last_price": 103.7, "volume": 37500, "ohlc": {"close": 100.0}, "total_buy_quantity": 9000, "total_sell_quantity": 1500,
             "depth": {"buy": [{"price": 103.7, "quantity": 5000, "orders": 40}], "sell": [{"price": 103.8, "quantity": 100, "orders": 2}]}}
    r = co.CloseOrderScanner.row("XYZ", quote, rows, DAY, {"pct": 70.0, "avg_pct": 40.0}, {}, None, set())
    assert r["verdict"] == "DEMAND" and r["unusual_buy"] and r["buy_sell_ratio"] == 6.0
    assert r["buy_orders_top5"] == 40 and r["sell_orders_top5"] == 2 and r["buy_vs_volume_pct"] == 24.0
    assert "short_qty" not in r  # the short-selling file is not out yet


def test_quiet_and_missing_numbers_are_named():
    r = co.read({"symbol": "Q", "buy_qty": 500, "sell_qty": 600, "volume": 10000, "fno": True})
    assert r["verdict"] == "QUIET" and any("delivery" in m for m in r["missing"]) and any("open interest" in m for m in r["missing"])
    none_sold = co.read({"symbol": "N", "buy_qty": 500, "sell_qty": 0, "volume": 10000})
    assert none_sold["buy_sell_ratio"] == 999.0 and none_sold["unusual_buy"]


def test_per_minute_volume_and_short_file():
    assert co.minute_volumes([(0, 0, 0, 0, 1, 10), (60, 0, 0, 0, 1, 25), (120, 0, 0, 0, 1, 30)]) == [10, 15, 5]
    assert co.minute_volumes([(0, 0, 0, 0, 1, 10), (60, 0, 0, 0, 1, 5)]) == [10, 5]
    day, rows = co.parse_shorts('"Security Name","Symbol Name","Trade Date","Quantity"\n"KOTAK","KOTAKBANK","11-Nov-2026","243194"\n')
    assert day == dt.date(2026, 11, 11) and rows == {"KOTAKBANK": 243194.0}


def test_scan_reads_each_stock_and_notes_what_is_missing(monkeypatch):
    class Client:
        async def get_quote_payload(self, sym):
            return {"last_price": 101.0, "volume": 40000, "ohlc": {"close": 100.0}, "total_buy_quantity": 9000, "total_sell_quantity": 1000}

        async def get_candles_window(self, sym, interval, start, end):
            return candles(late_jump())

    async def none(*_a, **_k):
        return None

    async def oi():
        return {}

    import groww_pressure

    from app.services import delivery

    monkeypatch.setattr(groww_pressure, "wait_for_bots", none)
    monkeypatch.setattr(co, "oi_changes", oi)
    monkeypatch.setattr(co, "shorts_for", none)
    monkeypatch.setattr(co, "CALL_GAP_SEC", 0)
    monkeypatch.setattr(delivery, "report", lambda names, days: {"day": None, "rows": {}})
    monkeypatch.setattr(co, "ist_now", lambda: dt.datetime(2026, 11, 12, 16, 30, tzinfo=IST))
    saved = []
    s = co.CloseOrderScanner()
    s.on_done = saved.append
    asyncio.run(s.scan(Client(), ["abc", "ABC", "def"]))
    v = s.view()
    assert [r["symbol"] for r in v["rows"]] == ["ABC", "DEF"] and v["day"] == "2026-11-12" and saved
    assert any("Delivery" in n for n in v["notes"]) and any("short-selling" in n for n in v["notes"])


def test_routes(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_scalp
    from app.main import app

    monkeypatch.setattr(routes_scalp, "_groww_session", lambda: None)
    with TestClient(app) as client:
        assert client.get("/api/scalp/close-orders").json()["rows"] == []
        assert client.post("/api/scalp/close-orders/scan", json={"symbols": ["ABC"]}).status_code in (409, 428)
        assert client.post("/api/scalp/close-orders/scan", json={"symbols": []}).status_code == 422
        assert client.get("/api/scalp/close-orders/history").status_code == 200
