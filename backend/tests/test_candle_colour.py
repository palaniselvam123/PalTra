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


def test_smallest_body_per_candle_size():
    from candle_patterns import clean_body_by_tf, colour_min_body_pct, parse_body_by_tf

    assert parse_body_by_tf("5:0.05, 15:0.15,7:1,bad,30:x") == {5: 0.05, 15: 0.15}
    assert clean_body_by_tf("15:0.15,5:0.05") == "5:0.05,15:0.15"
    cfg = _cfg(entry_mode="COLOUR", colour_min_body=0.02, colour_body_by_tf="15:0.15")
    assert colour_min_body_pct(cfg, 15) == 0.15 and colour_min_body_pct(cfg, 5) == 0.02
    # 1-minute colour candles: a 0.02 % red body is held under 1:0.05, counts under 1:0.01.
    small = _frame([(1000.0, 1000.5), (1000.5, 1000.3)])
    held = colour_call(small, _cfg(entry_mode="COLOUR", colour_min_body=0.0, colour_body_by_tf="1:0.05"))
    assert held.side is None and "0.05%" in held.note
    assert colour_call(small, _cfg(entry_mode="COLOUR", colour_min_body=0.5, colour_body_by_tf="1:0.01")).side == "SHORT"


def test_smallest_body_in_atrs():
    # Candles about 1.0 tall (ATR ≈ 1.1): a 0.2 body is under 0.5× ATR, a 0.9 body is over it.
    base = [(1000.0 + (0.5 if k % 2 else 0), 1000.0 + (0.0 if k % 2 else 0.5)) for k in range(20)]
    small = pd.DataFrame(_rows(DAY, base + [(1000.5, 1000.3), (1000.3, 1000.3)]))
    big = pd.DataFrame(_rows(DAY, base + [(1000.5, 999.6), (999.6, 999.6)]))
    cfg = _cfg(entry_mode="COLOUR", colour_body_unit="ATR", colour_body_atr=0.5)
    held = colour_call(small, cfg)
    assert held.side is None and "ATR" in held.note
    assert colour_call(big, cfg).side == "SHORT"
    # 0 = any body counts.
    assert colour_call(small, _cfg(entry_mode="COLOUR", colour_body_unit="ATR", colour_body_atr=0)).side == "SHORT"


def test_api_saves_the_body_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        ok = client.put(
            "/api/config",
            json={"entry_mode": "COLOUR", "colour_body_unit": "ATR", "colour_body_atr": 0.3, "colour_body_by_tf": "15:0.15, 5:0.05"},
        )
        body = ok.json()
        assert ok.status_code == 200 and body["colour_body_unit"] == "ATR" and body["colour_body_atr"] == 0.3
        assert body["colour_body_by_tf"] == "5:0.05,15:0.15"
        assert client.put("/api/config", json={"colour_body_unit": "TICKS"}).status_code == 422
        assert client.get("/api/config").json()["trading_mode"] == "PAPER"


def test_each_candle_of_the_run_has_its_own_size():
    from candle_patterns import clean_body_seq, parse_body_seq

    assert parse_body_seq("0.1, 0.5") == [0.1, 0.5] and parse_body_seq("0.1,,0.5,") == [0.1, None, 0.5]
    assert clean_body_seq(" 0.10 ,, 0.5 ,,") == "0.1,,0.5" and clean_body_seq("x,") == ""
    # Candles about 1.0 tall (ATR ≈ 1.1). Two red in a row: 1st ≥ 0.1× ATR, 2nd ≥ 0.5× ATR.
    base = [(1000.0 + (0.5 if k % 2 else 0), 1000.0 + (0.0 if k % 2 else 0.5)) for k in range(20)]
    small_then_big = pd.DataFrame(_rows(DAY, base + [(1000.5, 1000.3), (1000.3, 999.4), (999.4, 999.4)]))
    big_then_small = pd.DataFrame(_rows(DAY, base + [(1000.5, 999.6), (999.6, 999.4), (999.4, 999.4)]))
    cfg = _cfg(entry_mode="COLOUR", colour_count=2, colour_body_unit="ATR", colour_body_atr=0, colour_body_seq="0.1,0.5")
    assert colour_call(small_then_big, cfg).side == "SHORT"
    held = colour_call(big_then_small, cfg)
    assert held.side is None and "candle 2 of 2" in held.note and "0.5× ATR" in held.note
    # In % too: 1st any body, 2nd ≥ 0.05 % of the price.
    pct = _cfg(entry_mode="COLOUR", colour_count=2, colour_min_body=0, colour_body_seq="0,0.05")
    assert colour_call(small_then_big, pct).side == "SHORT" and colour_call(big_then_small, pct).side is None
    # Off: both turn.
    off = _cfg(entry_mode="COLOUR", colour_count=2, colour_body_unit="ATR", colour_body_atr=0)
    assert colour_call(small_then_big, off).side == "SHORT" and colour_call(big_then_small, off).side == "SHORT"


def test_api_saves_the_body_per_candle(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        ok = client.put("/api/config", json={"entry_mode": "COLOUR", "colour_count": 2, "colour_body_seq": " 0.10, 0.5 "})
        assert ok.status_code == 200 and ok.json()["colour_body_seq"] == "0.1,0.5"
        assert client.get("/api/config").json()["trading_mode"] == "PAPER"
    database.reset_engine()


def _minutes(start: float, steps: list[float]) -> list[tuple[float, float]]:
    """1-minute candles, each opening at the last close and moving by its step."""
    out, price = [], start
    for step in steps:
        out.append((price, price + step))
        price += step
    return out


def test_inside_candles_agree_make_one_candle_enough():
    from candle_patterns import inside_minutes

    # 09:15-09:45 quiet, 09:45-10:00 a red 15-minute candle, 10:00-10:15 a green one, then the 10:15 minute forming.
    red = _minutes(1000.0, [-0.1] * 15)
    all_green = _minutes(998.5, [0.1] * 15)  # its three 5-minute candles are all green
    mixed = _minutes(998.5, [0.2] * 5 + [-0.1] * 5 + [0.2] * 5)  # green, red, green inside: still green overall

    def frame(last):
        return pd.DataFrame(_rows(DAY, _quiet(30) + red + last + [(last[-1][1], last[-1][1])]))

    base = dict(entry_mode="COLOUR", pattern_tf=15, colour_count=2)
    # Without the rule: one green after a red is not 2 in a row, so the trade is held.
    assert colour_call(frame(all_green), _cfg(**base)).side is None
    on = _cfg(**base, colour_inside_minutes=5)
    call = colour_call(frame(all_green), on)
    assert call.side == "LONG" and "every 5-minute candle inside green" in call.note
    # Mixed inside candles: the normal rule (2 in a row) still decides.
    assert colour_call(frame(mixed), on).side is None
    # Its own smallest body: 1.5 on ~1000 is 0.15 %; 0.1 % passes, 0.5 % holds.
    assert colour_call(frame(all_green), _cfg(**base, colour_inside_minutes=5, colour_inside_body=0.1)).side == "LONG"
    held = colour_call(frame(all_green), _cfg(**base, colour_inside_minutes=5, colour_inside_body=0.5))
    assert held.side is None and "under 0.5%" in held.note
    # Heikin Ashi: the inside candles are read in HA colours and set the colour, even while the 15-minute HA candle is
    # still red after the fall (the owner's ATHERENERG 11:00 candle): all three 5-minute HA candles green turn it.
    ha_cfg = _cfg(**base, colour_inside_minutes=5, ha_colours=True)
    from candle_patterns import inside_colour

    late_green = _minutes(998.5, [0.4] * 15)
    rows = pd.DataFrame(_rows(DAY, _quiet(30) + red + late_green))
    start = int(rows["ts"].iloc[45])
    got = inside_colour(rows, start, 15, 5, ha_cfg)
    call = colour_call(frame(late_green), ha_cfg)
    assert got == "G" and call.side == "LONG" and "inside green" in call.note
    # A size that does not fit inside the colour candle is off.
    assert inside_minutes(_cfg(pattern_tf=15, colour_inside_minutes=10), 15) == 0
    assert inside_minutes(_cfg(pattern_tf=15, colour_inside_minutes=15), 15) == 0
    assert inside_minutes(_cfg(pattern_tf=15, colour_inside_minutes=3), 15) == 3


def test_api_saves_the_inside_candle_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "colour_inside_minutes" in STOCK_FIELDS and "colour_inside_body" in STOCK_FIELDS
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["colour_inside_minutes"] == 0 and got["colour_inside_body"] == 0
        ok = client.put("/api/config", json={"colour_inside_minutes": 5, "colour_inside_body": 0.05})
        assert ok.status_code == 200 and ok.json()["colour_inside_minutes"] == 5 and ok.json()["colour_inside_body"] == 0.05
        assert client.put("/api/config", json={"colour_inside_minutes": 4}).status_code == 422
        assert client.put("/api/config", json={"colour_inside_body": 3}).status_code == 422
    database.reset_engine()


@pytest.mark.asyncio
async def test_recovery_hold_in_colour_mode(db):  # noqa: F811
    """Candle-colour mode with recovery hold: a turn against a losing trade holds it, judged on the colour candles,
    and it closes as a recovery target once the price comes back; a winner still turns as usual."""
    from sma_research.replayer import replay_symbol

    up = [(1000.0 + 0.1 * k, 1000.1 + 0.1 * k) for k in range(5)]  # 09:45-09:49 green: buy at 09:50 (~1000.5)
    down = [(1000.5 - 0.1 * k, 1000.4 - 0.1 * k) for k in range(5)]  # 09:50-09:54 red: the long is losing
    back = [(1000.0 + 0.1 * k, 1000.1 + 0.1 * k) for k in range(5)]  # 09:55-09:59 green: back to the entry
    day2 = _quiet(30) + up + down + back + _quiet(330, 1000.5)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    base = dict(entry_mode="COLOUR", pattern_tf=5, qty=1, use_stop=False)

    plain = await replay_symbol(frame, "COL", [DAY], _settings(db, **base))
    assert plain[0]["exit_reason"] == "COLOUR_TURN" and plain[0]["exit_time"].strftime("%H:%M") == "09:55"

    held = await replay_symbol(frame, "COL", [DAY], _settings(db, **base, recovery_hold=True))
    first = held[0]
    assert first["direction"] == "LONG" and first["entry_time"].strftime("%H:%M") == "09:50"
    # Not closed by the red 5-minute candle: held, then closed when the green one brought the price back.
    assert first["exit_reason"] == "RECOVERY_TARGET_HIT", [(t["exit_reason"], t["exit_time"]) for t in held]
    assert first["exit_time"].strftime("%H:%M") == "10:00"


@pytest.mark.asyncio
async def test_exits_read_the_colour_candles_in_colour_mode(db):  # noqa: F811
    """In candle-colour mode the bot's own candle is 1 minute; the exits read the colour candles instead, so three
    red 1-minute candles inside a green 5-minute candle are not "3 candles against", three red 5-minute ones are."""
    from sma_research.replayer import replay_symbol

    up = _minutes(1000.0, [0.02] * 25)  # 09:45-10:09: five green 5-minute candles in a row: buy at 10:10
    dip = _minutes(1000.5, [-0.1, -0.1, -0.1, 0.3, 0.3])  # 10:10-10:14: 3 red minutes, the 5-minute candle green
    base = dict(entry_mode="COLOUR", pattern_tf=5, colour_count=5, qty=1, use_stop=False,
                counter_exit_count=3, counter_need_fast=False, counter_min_body=0, counter_rule="COLOUR")

    held = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + up + dip + _quiet(315, 1000.8)))
    trades = await replay_symbol(held, "COL", [DAY], _settings(db, **base))
    assert trades and trades[0]["exit_reason"] != "COUNTER_CANDLES", [(t["exit_reason"], t["exit_time"]) for t in trades]

    reds = _minutes(1000.8, [-0.1] * 15)  # 10:15-10:29: three red 5-minute candles (5 in a row would turn it)
    turned = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + up + dip + reds + _quiet(300, 999.3)))
    trades = await replay_symbol(turned, "COL", [DAY], _settings(db, **base))
    first = trades[0]
    assert first["exit_reason"] == "COUNTER_CANDLES", [(t["exit_reason"], t["exit_time"]) for t in trades]
    assert first["exit_time"].strftime("%H:%M") == "10:30"
