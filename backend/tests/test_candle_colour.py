"""Ride the candle colour (entry_mode = "COLOUR"): a green candle buys, a red one sells, the
trade is held while the candles keep its colour and the first candle of the other colour
closes it and opens the other way. Replays on local fills only (no Groww order path).
"""
from __future__ import annotations

import pandas as pd
import pytest

from candle_patterns import colour_call
from tests.test_candle_patterns import DAY, DAY0, _cfg, _quiet, _rows
from tests.test_gap_mode import _settings, db  # noqa: F401


def test_colour_of_the_closed_candle():
    green = pd.DataFrame(_rows(DAY, _quiet(30) + [(1000.0, 1000.5), (1000.5, 1000.5)]))
    red = pd.DataFrame(_rows(DAY, _quiet(30) + [(1000.5, 1000.0), (1000.0, 1000.0)]))
    doji = pd.DataFrame(_rows(DAY, _quiet(32)))
    assert colour_call(green, _cfg(entry_mode="COLOUR")).side == "LONG"
    assert colour_call(red, _cfg(entry_mode="COLOUR")).side == "SHORT"
    held = colour_call(doji, _cfg(entry_mode="COLOUR"))
    assert held.side is None and "doji" in held.note
    # 09:15 + 32 candles: the 09:45 5-minute candle is still forming, so nothing is judged yet.
    five = pd.DataFrame(_rows(DAY, _quiet(31) + [(1000.0, 1000.5), (1000.5, 1000.5)]))
    assert colour_call(five, _cfg(entry_mode="COLOUR", pattern_tf=5)).side is None


@pytest.mark.asyncio
async def test_holds_while_the_colour_holds_and_reverses_on_the_other(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    greens = [(1000.0, 1000.3), (1000.3, 1000.6), (1000.6, 1000.9)]  # 09:45-09:47
    reds = [(1000.9, 1000.6), (1000.6, 1000.3)]  # 09:48-09:49
    back = [(1000.3, 1000.6), (1000.6, 1000.9)]  # 09:50-09:51
    day2 = _quiet(30) + greens + reds + back + _quiet(338, 1000.9)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    trades = await replay_symbol(
        frame, "COL", [DAY], _settings(db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False)
    )
    got = [
        (t["direction"], t["entry_time"].strftime("%H:%M"), t["exit_time"].strftime("%H:%M"), t["exit_reason"])
        for t in trades
    ]
    assert got[:2] == [("LONG", "09:46", "09:49", "COLOUR_TURN"), ("SHORT", "09:49", "09:51", "COLOUR_TURN")]
    # The last long is held through the dojis until the square-off.
    assert len(got) == 3 and got[2][:2] == ("LONG", "09:51") and got[2][3] != "COLOUR_TURN"
    assert all(t["entry_reason"] == "CANDLE_COLOUR" for t in trades)


@pytest.mark.asyncio
async def test_five_minute_colour_candles(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    up = [(1000.0 + 0.1 * k, 1000.1 + 0.1 * k) for k in range(5)]  # 09:45-09:49 green
    down = [(1000.5 - 0.1 * k, 1000.4 - 0.1 * k) for k in range(5)]  # 09:50-09:54 red
    day2 = _quiet(30) + up + down + _quiet(335, 1000.0)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    trades = await replay_symbol(
        frame, "COL", [DAY], _settings(db, entry_mode="COLOUR", pattern_tf=5, qty=1, use_stop=False)
    )
    first = trades[0]
    assert first["direction"] == "LONG" and first["entry_time"].strftime("%H:%M") == "09:50"
    assert first["exit_time"].strftime("%H:%M") == "09:55" and first["exit_reason"] == "COLOUR_TURN"
    assert trades[1]["direction"] == "SHORT" and trades[1]["entry_time"].strftime("%H:%M") == "09:55"


def test_api_saves_colour_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        ok = client.put("/api/config", json={"entry_mode": "COLOUR", "pattern_tf": 15})
        assert ok.status_code == 200 and ok.json()["entry_mode"] == "COLOUR" and ok.json()["pattern_tf"] == 15
        assert client.get("/api/config").json()["trading_mode"] == "PAPER"
    database.reset_engine()


def _frame(candles):
    # The candles closed, then one forming candle.
    return pd.DataFrame(_rows(DAY, _quiet(30) + candles + [(candles[-1][1], candles[-1][1])]))


def test_candles_in_a_row():
    one_red = _frame([(1000.0, 1000.5), (1000.5, 1000.9), (1000.9, 1000.6)])
    two_red = _frame([(1000.0, 1000.5), (1000.9, 1000.6), (1000.6, 1000.3)])
    held = colour_call(one_red, _cfg(entry_mode="COLOUR", colour_count=2))
    assert held.side is None and "1 of 2 red" in held.note
    assert colour_call(one_red, _cfg(entry_mode="COLOUR", colour_count=1)).side == "SHORT"
    turned = colour_call(two_red, _cfg(entry_mode="COLOUR", colour_count=2))
    assert turned.side == "SHORT" and "2 red" in turned.note
    # A doji in the run breaks it.
    broken = _frame([(1000.9, 1000.6), (1000.6, 1000.6), (1000.6, 1000.3)])
    assert colour_call(broken, _cfg(entry_mode="COLOUR", colour_count=2)).side is None
    # Too few candles yet: nothing.
    short = pd.DataFrame(_rows(DAY, [(1000.0, 1000.5), (1000.5, 1000.6)]))
    assert colour_call(short, _cfg(entry_mode="COLOUR", colour_count=3)).side is None


def test_smallest_body():
    small = _frame([(1000.0, 1000.5), (1000.5, 1000.3)])  # red body 0.02 %
    cfg = _cfg(entry_mode="COLOUR", colour_min_body=0.05)
    call = colour_call(small, cfg)
    assert call.side is None and "under 0.05%" in call.note
    big = _frame([(1000.0, 1000.5), (1000.5, 999.5)])  # red body 0.1 %
    assert colour_call(big, cfg).side == "SHORT"


@pytest.mark.asyncio
async def test_two_in_a_row_rides_through_a_lone_candle(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    greens = [(1000.0, 1000.3), (1000.3, 1000.6)]  # 09:45-09:46: two green, long at 09:47
    blip = [(1000.6, 1000.5), (1000.5, 1000.8)]  # 09:47 red, 09:48 green: held
    reds = [(1000.8, 1000.5), (1000.5, 1000.2)]  # 09:49-09:50: two red, short at 09:51
    day2 = _quiet(30) + greens + blip + reds + _quiet(339, 1000.2)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    trades = await replay_symbol(
        frame, "COL", [DAY], _settings(db, entry_mode="COLOUR", pattern_tf=1, colour_count=2, qty=1, use_stop=False)
    )
    got = [(t["direction"], t["entry_time"].strftime("%H:%M"), t["exit_time"].strftime("%H:%M"), t["exit_reason"]) for t in trades]
    assert got[0] == ("LONG", "09:47", "09:51", "COLOUR_TURN")
    assert got[1][:2] == ("SHORT", "09:51")


def test_api_saves_colour_count_and_body(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        ok = client.put("/api/config", json={"entry_mode": "COLOUR", "pattern_tf": 60, "colour_count": 3, "colour_min_body": 0.1})
        body = ok.json()
        assert ok.status_code == 200 and body["pattern_tf"] == 60 and body["colour_count"] == 3 and body["colour_min_body"] == 0.1
        assert client.put("/api/config", json={"colour_count": 6}).status_code == 422
        assert client.get("/api/config").json()["trading_mode"] == "PAPER"
