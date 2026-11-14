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
    # Tiny dips never give a stop smaller than one candle's normal swing (1 ATR), nor under the smallest room.
    tiny = smart_stop.room(_tape(0.6), "LONG", 1000.0, _cfg())
    assert tiny["pct"] >= 0.3 and (tiny["atr_floor"] or tiny["clamped"] == "min")
    assert tiny["pct"] >= tiny["atr_pct"] - 1e-9
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


def _swing_tape(days: int = 4) -> pd.DataFrame:
    """The owner's case: a 15-minute strategy on a ~₹200 stock. Inside each minute the price barely wiggles (₹0.10), but
    the 15-minute swings pull back about ₹1.2 before the trend goes on."""
    rows = []
    for d in range(days):
        start = dt.datetime(2026, 10, 5 + d, 9, 15, tzinfo=IST)
        p = 200.0
        for minute in range(375):
            leg = (minute // 15) % 3  # two 15-minute candles up, one back
            step = 0.08 if leg < 2 else -0.08
            o, c = p, p + step
            rows.append((start + dt.timedelta(minutes=minute), o, max(o, c) + 0.05, min(o, c) - 0.05, c))
            p = c
    return pd.DataFrame(
        {"ts": [int(r[0].timestamp()) for r in rows], "open": [r[1] for r in rows], "high": [r[2] for r in rows],
         "low": [r[3] for r in rows], "close": [r[4] for r in rows], "volume": 100}
    )


def test_a_15_minute_strategy_gets_the_room_of_15_minute_swings():
    tape = _swing_tape()
    price = float(tape["close"].iloc[-1])
    one = smart_stop.room(tape, "LONG", price, _cfg(), minutes=1)
    fifteen = smart_stop.room(tape, "LONG", price, _cfg(), minutes=15)
    assert fifteen["minutes"] == 15
    # 1-minute wiggles would have stopped it out; the 15-minute room covers a pulled-back candle.
    assert fifteen["rs"] > one["rs"]
    assert fifteen["rs"] >= 1.0  # the ₹1.2 pullbacks, not the ₹0.1 wiggles
    assert fifteen["atr_pct"] is not None and fifteen["pct"] >= fifteen["atr_pct"] - 1e-9


@pytest.mark.asyncio
async def test_colour_mode_entry_reads_the_colour_candles(engine):  # noqa: F811
    cfg = engine.load_config()
    cfg.use_stop, cfg.stop_type = True, "SMART"
    cfg.entry_mode, cfg.pattern_tf = "COLOUR", 15
    symbol = (cfg.symbol or "").upper()
    tape = _swing_tape()
    engine._tapes[symbol] = tape
    engine._focus = symbol
    price = float(tape["close"].iloc[-1])
    engine._ltps[symbol] = engine.ltp = price
    when = dt.datetime(2026, 10, 9, 10, 0, tzinfo=IST)
    await engine._open("SHORT", price, 0.05, cfg, when)
    pos = engine.positions[symbol]
    assert pos.smart_info["minutes"] == 15
    assert pos.smart_room >= 1.0


def _calm_then_wild() -> pd.DataFrame:
    """15-minute bot: three calm sessions (₹0.4 pullbacks), then today's candles swing ₹2 before going on."""
    rows = []
    for d in range(4):
        start = dt.datetime(2026, 10, 5 + d, 9, 15, tzinfo=IST)
        p = 200.0
        step = 0.03 if d < 3 else 0.15
        for minute in range(375):
            leg = (minute // 15) % 3
            move = step if leg < 2 else -step
            o, c = p, p + move
            rows.append((start + dt.timedelta(minutes=minute), o, max(o, c) + 0.01, min(o, c) - 0.01, c))
            p = c
    return pd.DataFrame(
        {"ts": [int(r[0].timestamp()) for r in rows], "open": [r[1] for r in rows], "high": [r[2] for r in rows],
         "low": [r[3] for r in rows], "close": [r[4] for r in rows], "volume": 100}
    )


@pytest.mark.asyncio
async def test_live_resize_follows_each_stocks_candles_during_the_trade(engine):  # noqa: F811
    from strategy_engine import OpenPosition

    import database
    from models import TradeLog

    engine.status = "RUNNING"
    cfg = engine.load_config()
    cfg.use_stop, cfg.stop_type, cfg.entry_mode, cfg.pattern_tf = True, "SMART", "COLOUR", 15
    symbol = (cfg.symbol or "").upper()
    full = _calm_then_wild()
    entry_at = dt.datetime(2026, 10, 8, 9, 30, tzinfo=IST)
    engine._tapes[symbol] = full[full["ts"] < int(entry_at.timestamp())]
    engine._frames[symbol] = engine._tapes[symbol]
    engine._focus = symbol
    price = float(engine._tapes[symbol]["close"].iloc[-1])
    engine._ltps[symbol] = engine.ltp = price
    entry_room = smart_stop.room(engine._tapes[symbol], "LONG", price, cfg, minutes=15)["rs"]
    engine.position = OpenPosition(
        direction="LONG", qty=1, entry_price=price, ma_cross_price=price, atr_at_entry=0.1,
        sl_trigger=round(price - entry_room, 2), sl_order_id="PAPERSL1", entry_order_id="E", entry_time=entry_at,
        trade_id=_seed_open_trade(), mode="PAPER", smart=True, smart_room=entry_room, high=price, low=price,
    )
    pos = engine.position
    stop_before = pos.sl_trigger
    # Two hours of the trade later, today's candles swing much wider than the calm days before.
    now = dt.datetime(2026, 10, 8, 11, 30, tzinfo=IST)
    engine._now = lambda: now
    engine._tapes[symbol] = full[full["ts"] < int(now.timestamp())]
    engine._frames[symbol] = engine._tapes[symbol]
    engine._ltps[symbol] = engine.ltp = float(engine._tapes[symbol]["close"].iloc[-1])
    await engine._trail_smart(cfg)
    assert pos.smart_room > entry_room  # re-sized to the wilder swings of this trade's own candles
    assert pos.smart_resized_at == "11:30" and pos.smart_info["minutes"] == 15
    assert pos.sl_trigger >= stop_before  # a wider room never moves the stop back
    with database.session_factory()() as db:
        assert "smart room →" in (db.get(TradeLog, pos.trade_id).stop_log or "")
    # Same closed candle: not worked out again.
    room = pos.smart_room
    await engine._trail_smart(cfg)
    assert pos.smart_room == room
    # Switched off: the entry's room stands.
    cfg.smart_live = False
    pos.smart_bar = None
    pos.smart_room = entry_room
    await engine._trail_smart(cfg)
    assert pos.smart_room == entry_room
