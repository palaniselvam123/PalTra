"""Smart stop (smart_stop.py): each stock's own room from the dips it recovered from; trails the best price,
breakeven after one room, closer as the profit grows. PAPER, fake broker."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import smart_stop
from tests.test_sma_atr_terminal import IST, _seed_open_trade, engine  # noqa: F401  (engine is a fixture)
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)


def _cfg(**over):
    base = dict(use_stop=True, stop_type="SMART")
    base.update(over)
    return type("C", (), base)()


def _tape(dip_rs: float, price: float = 1000.0, days: int = 3, waves: int = 12) -> pd.DataFrame:
    """1-minute candles of an up-trend: each wave climbs 5 rupees a minute for 4 minutes, dips `dip_rs`, comes back."""
    rows = []
    for d in range(days):
        start = dt.datetime(2026, 11, 10 + d, 9, 15, tzinfo=IST)
        p = price
        minute = 0
        for _ in range(waves):
            for _ in range(4):
                rows.append((start + dt.timedelta(minutes=minute), p, p + 5, p - 0.5, p + 5))
                p += 5
                minute += 1
            rows.append((start + dt.timedelta(minutes=minute), p, p, p - dip_rs, p - dip_rs + 1))
            p = p - dip_rs + 1
            minute += 1
            rows.append((start + dt.timedelta(minutes=minute), p, p + dip_rs, p - 0.2, p + dip_rs))
            p += dip_rs
            minute += 1
    return pd.DataFrame(
        {"ts": [int(r[0].timestamp()) for r in rows], "open": [r[1] for r in rows], "high": [r[2] for r in rows],
         "low": [r[3] for r in rows], "close": [r[4] for r in rows], "volume": 100}
    )


def test_room_is_each_stocks_own_dips_kept_inside_the_bounds():
    calm = smart_stop.room(_tape(5), "LONG", 1000.0, _cfg())
    wild = smart_stop.room(_tape(14), "LONG", 1000.0, _cfg())
    assert calm["source"] == wild["source"] == "dips" and calm["samples"] >= 30
    # A stock that dips ₹14 before going on gets more room than one that dips ₹5.
    assert wild["rs"] > calm["rs"] > 0
    assert 0.3 <= calm["pct"] <= 2.0 and 1.0 <= wild["pct"] <= 2.0
    # Never looser than the largest room, never tighter than the smallest.
    huge = smart_stop.room(_tape(60), "LONG", 1000.0, _cfg())
    assert huge["pct"] == 2.0 and huge["clamped"] == "max"
    tiny = smart_stop.room(_tape(0.6), "LONG", 1000.0, _cfg())
    assert tiny["pct"] == 0.3 and tiny["clamped"] == "min"
    # Only candles before the entry are read.
    tape = _tape(14)
    early = int(tape["ts"].iloc[2])
    assert smart_stop.room(tape, "LONG", 1000.0, _cfg(), before_ts=early, fallback=12.0)["source"] == "atr"
    assert smart_stop.room(None, "LONG", 1000.0, _cfg())["source"] == "floor"


def test_short_reads_bounces_not_dips():
    up = _tape(14)
    # In an up-trend there are no new lows to bounce from, so a short has too few samples and uses the ATR distance.
    assert smart_stop.room(up, "SHORT", 1000.0, _cfg(), fallback=10.0)["source"] == "atr"


def test_trail_breakeven_and_follow_closer():
    be = 1000.6
    # Not yet a room in profit: the room behind the best price.
    assert smart_stop.want("LONG", 1000, 1010, 20, be, True) == pytest.approx(990)
    # One room in profit: at least breakeven after charges.
    assert smart_stop.want("LONG", 1000, 1020, 20, be, True) == pytest.approx(1000.6)
    # Five rooms in profit: follows at half a room (1100 - 10), more profit kept.
    assert smart_stop.want("LONG", 1000, 1100, 20, be, True) == pytest.approx(1090)
    assert smart_stop.want("LONG", 1000, 1100, 20, be, False) == pytest.approx(1080)
    # A short mirrors it.
    assert smart_stop.want("SHORT", 1000, 980, 20, 999.4, True) == pytest.approx(999.4)
    assert smart_stop.worth_moving(990, 991, 20) is False and smart_stop.worth_moving(990, 992, 20) is True


@pytest.mark.asyncio
async def test_engine_trails_each_stock_with_its_own_room(engine):  # noqa: F811
    from strategy_engine import OpenPosition

    engine.status = "RUNNING"
    cfg = engine.load_config()
    cfg.use_stop, cfg.stop_type = True, "SMART"
    symbol = (cfg.symbol or "").upper()
    engine._tapes[symbol] = _tape(14)
    engine.position = OpenPosition(
        direction="LONG", qty=1, entry_price=1000.0, ma_cross_price=1000, atr_at_entry=2, sl_trigger=985.0,
        sl_order_id="PAPERSL1", entry_order_id="E", entry_time=dt.datetime(2026, 11, 13, 10, 0, tzinfo=IST),
        trade_id=_seed_open_trade(), mode="PAPER", smart=True, high=1000.0, low=1000.0,
    )
    pos = engine.position
    engine.ltp = 1000.0
    await engine._trail_smart(cfg)  # restored-style: the room is worked out from the stock's own candles
    assert pos.smart_room is not None and pos.smart_info["source"] == "dips"
    room = pos.smart_room
    pos.high, engine.ltp = 1000.0 + room * 0.5, 1000.0 + room * 0.4  # half a room up: stop moves up, still a loss
    await engine._trail_smart(cfg)
    assert 985.0 < pos.sl_trigger < 1000.0
    pos.high, engine.ltp = 1000.0 + room * 1.2, 1000.0 + room * 1.1  # past one room: no loss left
    await engine._trail_smart(cfg)
    assert pos.sl_trigger > 1000.0
    before = pos.sl_trigger
    pos.high, engine.ltp = 1000.0 + room * 0.9, 1000.0 + room * 0.9  # a lower best never loosens it
    await engine._trail_smart(cfg)
    assert pos.sl_trigger == before


def test_stop_reason_for_a_smart_stop():
    from strategy_engine import OpenPosition, _STOP_FILLS, _stop_reason

    pos = OpenPosition(
        direction="LONG", qty=1, entry_price=100, ma_cross_price=100, atr_at_entry=1, sl_trigger=99, sl_order_id="",
        entry_order_id="", entry_time=dt.datetime(2026, 11, 13, 10, tzinfo=IST), trade_id=1, mode="PAPER", smart=True,
    )
    assert _stop_reason(pos) == "SMART_SL_HIT" and "SMART_SL_HIT" in _STOP_FILLS


@pytest.mark.asyncio
async def test_entry_puts_the_stop_one_room_away(engine):  # noqa: F811
    cfg = engine.load_config()
    cfg.use_stop, cfg.stop_type = True, "SMART"
    symbol = (cfg.symbol or "").upper()
    tape = _tape(14)
    engine._tapes[symbol] = tape
    engine._focus = symbol
    price = float(tape["close"].iloc[-1])
    engine._ltps[symbol] = engine.ltp = price
    when = dt.datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    await engine._open("LONG", price, 2.0, cfg, when)
    pos = engine.positions[symbol]
    expected = smart_stop.room(tape, "LONG", pos.entry_price, cfg, before_ts=int(when.timestamp()))
    assert pos.smart and pos.smart_room == pytest.approx(expected["rs"])
    assert pos.sl_trigger == pytest.approx(pos.entry_price - expected["rs"], abs=0.05)
    assert pos.plan_r is None  # the staged stop plan never runs next to it
