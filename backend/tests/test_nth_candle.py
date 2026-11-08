"""Second candle entries (entry_mode = "NTH_CANDLE", nth_candle.py): the day's Nth candle colour, once a
day, buy on green / sell on red, then the stop / target / exits run the trade. Replays on local fills only
(no Groww order path)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import candles
import nth_candle
from candle_patterns import per_candle
from tests.test_candle_patterns import DAY, DAY0, IST, _quiet, _rows
from tests.test_gap_mode import _settings, db  # noqa: F401


def _cfg(**over):
    base = dict(entry_mode="NTH_CANDLE", nth_candle_no=2, nth_candle_minutes=5, nth_min_body_pct=0.0, nth_min_rvol=0.0)
    return SimpleNamespace(**{**base, **over})


def _five(day: dt.date, candles_: list[tuple[float, float]], volume: int = 10_000, day0: list[tuple[float, float]] | None = None):
    """A bot frame of 5-minute candles from 09:15 (the last one forming), volume = the running session total."""
    rows = []
    for d, cs in ((DAY0, day0), (day, candles_)):
        if cs is None:
            continue
        t = dt.datetime.combine(d, dt.time(9, 15), tzinfo=IST)
        for i, (o, c) in enumerate(cs):
            rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": volume * (i + 1)})
            t += dt.timedelta(minutes=5)
    return pd.DataFrame(rows)


def test_the_call_reads_the_nth_candle_once():
    cfg = _cfg()
    green = _five(DAY, [(1000.0, 1000.2), (1000.2, 1001.0), (1001.0, 1001.1)])
    red = _five(DAY, [(1000.0, 1000.2), (1000.2, 999.0), (999.0, 999.1)])
    doji = _five(DAY, [(1000.0, 1000.2), (1000.2, 1000.2), (1000.2, 999.1)])
    long = nth_candle.call(green, cfg)
    assert long.side == "LONG" and long.day == DAY and "green 2nd 5-minute candle" in long.note and long.rvol is None
    assert nth_candle.call(red, cfg).side == "SHORT"
    assert nth_candle.call(doji, cfg).side is None and "doji" in nth_candle.call(doji, cfg).note
    # Before the 2nd candle has closed, after it, and once it was read: nothing.
    waiting = nth_candle.call(_five(DAY, [(1000.0, 1000.2), (1000.2, 1001.0)]), cfg)
    assert waiting.side is None and "1 of 2 closed" in waiting.note
    later = nth_candle.call(_five(DAY, [(1000.0, 1000.2), (1000.2, 1001.0), (1001.0, 1002.0), (1002.0, 1002.1)]), cfg)
    assert later.side is None and "closed earlier" in later.note
    assert nth_candle.call(green, cfg, done_for=DAY).side is None
    # The 3rd candle, 15-minute size: the frame's candles are whatever size the bot trades.
    assert nth_candle.call(_five(DAY, [(1000.0, 1000.2), (1000.2, 1001.0), (1001.0, 1000.5), (1000.5, 1000.6)]), _cfg(nth_candle_no=3)).side == "SHORT"


def test_body_floor_and_volume_vs_usual():
    small = _five(DAY, [(1000.0, 1000.2), (1000.2, 1000.3), (1000.3, 1000.4)])  # body 0.01 %
    assert nth_candle.call(small, _cfg(nth_min_body_pct=0.05)).side is None
    assert nth_candle.call(small, _cfg()).side == "LONG"
    # Volume vs usual: the same running volume as the day before = 1×; three times it = 3×.
    quiet0 = [(1000.0, 1000.0)] * 3
    same = _five(DAY, [(1000.0, 1000.2), (1000.2, 1001.0), (1001.0, 1001.1)], day0=quiet0)
    busy = same.copy()
    busy.loc[busy["ts"] >= int(dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST).timestamp()), "volume"] *= 3
    assert nth_candle.call(same, _cfg(nth_min_rvol=2)).side is None and "1× usual" in nth_candle.call(same, _cfg(nth_min_rvol=2)).note
    hit = nth_candle.call(busy, _cfg(nth_min_rvol=2))
    assert hit.side == "LONG" and hit.rvol == 3.0
    # No earlier session: unknown refuses only while the check is on.
    alone = _five(DAY, [(1000.0, 1000.2), (1000.2, 1001.0), (1001.0, 1001.1)])
    assert nth_candle.call(alone, _cfg(nth_min_rvol=2)).side is None and nth_candle.call(alone, _cfg()).side == "LONG"


def test_the_bot_candle_follows_the_mode_and_it_is_a_per_candle_mode():
    assert candles.candle_minutes(SimpleNamespace(entry_mode="NTH_CANDLE", nth_candle_minutes=15, candle_minutes=1)) == 15
    assert candles.candle_minutes(SimpleNamespace(entry_mode="NTH_CANDLE", nth_candle_minutes=None, candle_minutes=1)) == 5
    assert candles.candle_minutes(SimpleNamespace(entry_mode="SMA", nth_candle_minutes=15, candle_minutes=3)) == 3
    assert candles.candle_minutes(SimpleNamespace(entry_mode="SMA", candle_minutes=3), {"entry_mode": "NTH_CANDLE", "nth_candle_minutes": 10}) == 10
    assert per_candle(_cfg()) and not per_candle(SimpleNamespace(entry_mode="SMA"))
    assert nth_candle.candle_no(_cfg(nth_candle_no=9)) == 6 and nth_candle.minutes(_cfg(nth_candle_minutes=7)) == 5


def _day(second: list[tuple[float, float]], rest: float, first: list[tuple[float, float]] | None = None) -> list[tuple[float, float]]:
    """A 1-minute day: 09:15-09:19 quiet, 09:20-09:24 `second`, then flat at `rest` till 15:30."""
    return (first or _quiet(5)) + second + _quiet(375 - 10, rest)


@pytest.mark.asyncio
async def test_replay_buys_the_green_second_candle_and_holds_to_the_square_off(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    rising = [(1000.0 + 0.2 * k, 1000.2 + 0.2 * k) for k in range(5)]  # 09:20-09:24: the 2nd 5-minute candle is green
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _day(rising, 1001.0)))
    trades = await replay_symbol(
        frame, "NTH", [DAY], _settings(db, entry_mode="NTH_CANDLE", nth_candle_no=2, nth_candle_minutes=5, qty=1, use_stop=False)
    )
    assert len(trades) == 1
    t = trades[0]
    assert t["direction"] == "LONG" and t["entry_time"].strftime("%H:%M") == "09:25" and t["entry_reason"] == "NTH_CANDLE"
    assert t["exit_reason"] == "EOD_SQUARE_OFF"


@pytest.mark.asyncio
async def test_replay_sells_the_red_second_candle_and_a_doji_or_small_body_skips_the_day(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    falling = [(1000.0 - 0.2 * k, 999.8 - 0.2 * k) for k in range(5)]
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _day(falling, 999.0)))
    settings = _settings(db, entry_mode="NTH_CANDLE", nth_candle_no=2, nth_candle_minutes=5, qty=1, use_stop=False)
    trades = await replay_symbol(frame, "NTH", [DAY], settings)
    assert [(t["direction"], t["entry_time"].strftime("%H:%M")) for t in trades] == [("SHORT", "09:25")]
    # A doji 2nd candle: no order all day, whatever the later candles do.
    doji = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _day(_quiet(5), 1000.0) ))
    assert await replay_symbol(doji, "NTH", [DAY], settings) == []
    # A body under the floor: the same.
    tiny = [(1000.0, 1000.02)] + _quiet(4, 1000.02)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _day(tiny, 1000.02)))
    assert await replay_symbol(frame, "NTH", [DAY], {**settings, "nth_min_body_pct": 0.05}) == []
    assert len(await replay_symbol(frame, "NTH", [DAY], settings)) == 1


@pytest.mark.asyncio
async def test_replay_reads_the_third_fifteen_minute_candle_and_the_volume_gate(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    # 09:15-09:44 flat, 09:45-09:59 rising: the 3rd 15-minute candle is green, order at 10:00.
    day = _quiet(30) + [(1000.0 + 0.1 * k, 1000.1 + 0.1 * k) for k in range(15)] + _quiet(330, 1001.5)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day))
    settings = _settings(db, entry_mode="NTH_CANDLE", nth_candle_no=3, nth_candle_minutes=15, qty=1, use_stop=False)
    trades = await replay_symbol(frame, "NTH", [DAY], settings)
    assert [(t["direction"], t["entry_time"].strftime("%H:%M")) for t in trades] == [("LONG", "10:00")]
    # Volume vs usual: the test tape trades the same every day (1×), so a 1.5× floor refuses; triple it and it orders.
    assert await replay_symbol(frame, "NTH", [DAY], {**settings, "nth_min_rvol": 1.5}) == []
    busy = frame.copy()
    busy.loc[busy["ts"] >= int(dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST).timestamp()), "volume"] *= 3
    assert len(await replay_symbol(busy, "NTH", [DAY], {**settings, "nth_min_rvol": 1.5})) == 1


def test_api_saves_the_mode_and_checks_its_bounds(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        ok = client.put("/api/config", json={"entry_mode": "NTH_CANDLE", "nth_candle_no": 3, "nth_candle_minutes": 15, "nth_min_rvol": 2})
        body = ok.json()
        assert ok.status_code == 200 and body["entry_mode"] == "NTH_CANDLE" and body["nth_candle_no"] == 3
        assert body["nth_candle_minutes"] == 15 and body["nth_min_rvol"] == 2
        assert client.put("/api/config", json={"nth_candle_minutes": 7}).status_code == 422
        both = client.put("/api/config", json={"nth_side_rule": "MAJORITY", "nth_majority_by": "SIZE", "nth_skip_checks": True}).json()
        assert (both["nth_side_rule"], both["nth_majority_by"], both["nth_skip_checks"]) == ("MAJORITY", "SIZE", True)
        assert client.put("/api/config", json={"nth_side_rule": "SOMETIMES"}).status_code == 422
        assert client.put("/api/config", json={"nth_candle_no": 0}).status_code == 422
        assert client.get("/api/config").json()["trading_mode"] == "PAPER"
        # The per-stock form takes the same fields.
        own = client.put("/api/config/stock/TCS", json={"nth_candle_no": 1, "nth_candle_minutes": 5})
        assert own.status_code == 200
    database.reset_engine()


def test_majority_of_the_first_candles_picks_the_side():
    # Three 5-minute candles: green, green, then a small red 3rd.
    run = _five(DAY, [(1000.0, 1001.0), (1001.0, 1002.0), (1002.0, 1001.8), (1001.8, 1001.9)])
    nth = nth_candle.call(run, _cfg(nth_candle_no=3))
    assert nth.side == "SHORT"  # the 3rd candle alone sells
    most = nth_candle.call(run, _cfg(nth_candle_no=3, nth_side_rule="MAJORITY"))
    assert most.side == "LONG" and "2 green, 1 red" in most.note
    agree = nth_candle.call(run, _cfg(nth_candle_no=3, nth_side_rule="AGREE"))
    assert agree.side is None and "against the majority" in agree.note
    # Counted by size: one big green beats two small reds.
    sized = _five(DAY, [(1000.0, 1003.0), (1003.0, 1002.5), (1002.5, 1002.0), (1002.0, 1002.1)])
    assert nth_candle.call(sized, _cfg(nth_candle_no=3, nth_side_rule="MAJORITY")).side == "SHORT"
    assert nth_candle.call(sized, _cfg(nth_candle_no=3, nth_side_rule="MAJORITY", nth_majority_by="SIZE")).side == "LONG"
    # A tie (2 candles, one each way) by count: no order.
    tie = _five(DAY, [(1000.0, 1001.0), (1001.0, 1000.5), (1000.5, 1000.6)])
    call = nth_candle.call(tie, _cfg(nth_candle_no=2, nth_side_rule="MAJORITY"))
    assert call.side is None and "no majority" in call.note
    assert nth_candle.side_rule(_cfg(nth_side_rule="X")) == "NTH" and not nth_candle.skip_checks(_cfg())


@pytest.mark.asyncio
async def test_replay_enters_at_once_without_the_entry_checks(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    # 09:15-09:29: up, up, a small dip; most of the first three 5-minute candles went up → buy at 09:30.
    day = [(1000.0 + 0.2 * k, 1000.2 + 0.2 * k) for k in range(10)] + [(1002.0, 1001.9)] * 5 + _quiet(360, 1003.0)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day))
    settings = _settings(
        db, entry_mode="NTH_CANDLE", nth_candle_no=3, nth_candle_minutes=5, nth_side_rule="MAJORITY", qty=1, use_stop=False,
        use_rsi=True, rsi_long_min=99.0, rsi_long_max=100.0,
    )
    # RSI 99+ refuses the buy through the normal entry checks …
    assert await replay_symbol(frame, "NTH", [DAY], settings) == []
    # … and "enter at once" skips them.
    trades = await replay_symbol(frame, "NTH", [DAY], {**settings, "nth_skip_checks": True})
    assert [(t["direction"], t["entry_time"].strftime("%H:%M"), t["entry_reason"]) for t in trades] == [("LONG", "09:30", "NTH_CANDLE")]
