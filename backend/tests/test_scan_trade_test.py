"""Trade a saved Most active scan (scan_trade_test.py): entry after the scan, normal vs Heikin Ashi vs hold."""
import asyncio
import datetime as dt

import pandas as pd

import scan_trade_test as stt

IST = stt.IST
DAY = dt.date(2026, 11, 10)


def tape(prices):
    """1-minute candles from 09:15; each minute opens at the last close and closes at the listed price."""
    start = int(dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST).timestamp())
    rows, prev = [], prices[0]
    for n, close in enumerate(prices):
        rows.append({"ts": start + n * 60, "open": prev, "high": max(prev, close), "low": min(prev, close), "close": close, "volume": n})
        prev = close
    return pd.DataFrame(rows)


def test_picks_by_buyers_and_sellers_share():
    rows = [
        {"symbol": "A", "ltp": 100, "buy_share": 0.80, "value_cr": 10},
        {"symbol": "B", "ltp": 100, "buy_share": 0.20, "value_cr": 30},
        {"symbol": "C", "ltp": 100, "buy_share": 0.60, "value_cr": 50},
        {"symbol": "D", "ltp": 100, "buy_share": None, "value_cr": 90},
    ]
    got = stt.picks(rows, "PRESSURE", 75, 10)
    assert [(p["symbol"], p["side"]) for p in got] == [("B", "SHORT"), ("A", "LONG")]  # busiest first
    assert stt.picks(rows, "PRESSURE", 75, 1)[0]["symbol"] == "B"
    assert [p["symbol"] for p in stt.picks([{"symbol": "E", "ltp": 5, "bias": "LONG"}, {"symbol": "F", "ltp": 5, "bias": "NONE"}], "BIAS", 75, 5)] == ["E"]


def test_long_rides_green_candles_and_leaves_on_the_first_red_one():
    # 09:15-10:15 flat at 100; 10:15-10:45 up 1 a minute; then down 1 a minute.
    prices = [100.0] * 60 + [100.0 + n for n in range(1, 31)] + [130.0 - n * 0.2 for n in range(1, 300)]
    at = dt.datetime.combine(DAY, dt.time(10, 14, 30), tzinfo=IST)
    got = stt.simulate(tape(prices), at, "LONG", 5, dt.time(15, 15))
    assert (got["entry_at"], got["entry"]) == ("10:15", 100.0)
    normal = got["normal"]
    # Green 5-minute candles 10:15-10:45, the 10:45 candle is red: out at the 10:50 open.
    assert (normal["reason"], normal["exit_at"], normal["exit"]) == ("candle turned", "10:50", 129.0)
    assert normal["points"] == 29.0 and normal["candles"] == 7
    assert got["hold"]["reason"] == "square-off" and got["hold"]["exit_at"] == "15:15" and got["hold"]["points"] < 0
    assert got["ha"]["reason"] == "candle turned" and got["ha"]["exit_at"] >= normal["exit_at"]  # HA turns no sooner here


def test_short_side_and_no_entry_after_the_square_off():
    prices = [100.0] * 60 + [100.0 - n * 0.5 for n in range(1, 300)]
    at = dt.datetime.combine(DAY, dt.time(10, 14, 30), tzinfo=IST)
    got = stt.simulate(tape(prices), at, "SHORT", 5, dt.time(15, 15))
    assert got["normal"]["reason"] == "square-off" and got["normal"]["points"] > 0   # red all day: held to the end
    assert got["normal"]["points"] == got["hold"]["points"]
    late = dt.datetime.combine(DAY, dt.time(15, 20), tzinfo=IST)
    assert stt.simulate(tape(prices), late, "SHORT", 5, dt.time(15, 15)) is None


def test_run_adds_rupees_and_an_answer():
    prices = [100.0] * 60 + [100.0 + n for n in range(1, 31)] + [130.0 - n * 0.2 for n in range(1, 300)]
    scan = {"id": 7, "as_of": "2026-11-10T10:14:30+05:30", "rows": [
        {"symbol": "UP", "ltp": 100, "buy_share": 0.9, "value_cr": 5},
        {"symbol": "GONE", "ltp": 100, "buy_share": 0.9, "value_cr": 4},
    ]}

    async def load(symbol, day):
        return tape(prices) if symbol == "UP" else None

    out = asyncio.run(stt.run(scan, load, pick="PRESSURE", pct=75, top_n=10, minutes=5, qty=10))
    assert (out["day"], out["time"], out["picked"]) == ("2026-11-10", "10:14", 2)
    assert [r["symbol"] for r in out["rows"]] == ["UP"] and out["errors"][0]["symbol"] == "GONE"
    assert out["rows"][0]["normal"]["rupees"] == 290.0
    normal = next(w for w in out["ways"] if w["way"] == "normal")
    assert (normal["trades"], normal["winners"], normal["rupees"]) == (1, 1, 290.0)
    assert out["best"] in ("normal", "ha") and any("Normal candles: made ₹290" in line for line in out["answer"])
