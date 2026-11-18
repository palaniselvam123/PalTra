"""Executed volume exit (flow_exit.py): close once the other side did the set % of the shares traded at market."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import flow_exit
import pressure_exit
import tick_store
import trade_flow
from strategy_engine import IST
from tests.test_gap_mode import db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean():
    trade_flow.reset()
    pressure_exit.reset()
    tick_store.take()
    yield
    trade_flow.reset()
    pressure_exit.reset()
    tick_store.take()


def _ts(h, m, s=0):
    return dt.datetime(2026, 11, 16, h, m, s, tzinfo=IST).timestamp()


def test_settings_are_read_safely():
    cfg = type("C", (), {})()
    assert not flow_exit.on(cfg) and flow_exit.pct(cfg) == 60.0 and flow_exit.window(cfg) == 3
    odd = type("C", (), {"flow_exit_pct": 120, "flow_exit_window": 4})()
    assert flow_exit.pct(odd) == 95.0 and flow_exit.window(odd) == 4          # a custom window of 4 minutes
    assert flow_exit.window(type("C", (), {"flow_exit_window": 61})()) == 3    # past MAX_WINDOW: the default


def test_the_window_lies_wholly_after_the_entry():
    entry = _ts(10, 0, 5)                                    # inside the first 15 s: 10:00 counts
    assert flow_exit.first_minute(entry) == int(_ts(10, 0))
    assert flow_exit.first_minute(_ts(10, 0, 40)) == int(_ts(10, 1))
    assert flow_exit.span(_ts(10, 2, 30), entry, 3, 5) is None          # only 2 minutes since the entry
    assert flow_exit.span(_ts(10, 3, 10), entry, 3, 5) == (int(_ts(10, 0)), int(_ts(10, 3)))
    # Window 0: the exit candle (5 min from 09:15) that just closed, only when it opened after the entry.
    assert flow_exit.span(_ts(10, 4, 10), entry, 0, 5) is None          # 10:04 is not a 5-minute close
    assert flow_exit.span(_ts(10, 5, 10), entry, 0, 5) == (int(_ts(10, 0)), int(_ts(10, 5)))
    assert flow_exit.span(_ts(10, 5, 10), _ts(10, 1), 0, 5) is None     # that candle began before the entry


def test_judge_reads_the_other_side_share():
    points = [{"time": 0, "buy": 300, "sell": 700, "src": "T"}, {"time": 60, "buy": 500, "sell": 500, "src": "C"}]
    hit, note = flow_exit.judge(points, "LONG", 60, 2)
    assert hit and "sellers did 60% of the shares traded in 2 min (ticks + candles; exit at 60%)" in note
    hit, _ = flow_exit.judge(points, "SHORT", 60, 2)                    # buyers did 40%
    assert hit is False
    assert flow_exit.judge([], "LONG", 60, 3)[0] is None               # no volume: nothing to read


def test_waiting_orders_must_agree_when_asked():
    now = _ts(11, 0)
    assert flow_exit.book_note("ACME", "LONG", now)[0] is False         # no read
    pressure_exit.note_scan({"as_of": "", "rows": [{"symbol": "ACME", "buy_share": 0.35,
                                                    "read_at": dt.datetime.fromtimestamp(now - 120, IST).isoformat()}]})
    agree, note = flow_exit.book_note("ACME", "LONG", now)
    assert agree and "sellers hold 65% of the waiting orders" in note
    assert flow_exit.book_note("ACME", "SHORT", now)[0] is False        # buyers hold only 35%


@pytest.mark.asyncio
@pytest.mark.parametrize("on,want", [(False, "COLOUR_TURN"), (True, "FLOW_EXIT")])
async def test_a_green_run_sold_into_closes_on_the_executed_volume(db, on, want):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    # 09:45 green buys at 09:46; the candles stay green to 09:55, but from 09:46 the seconds tick down (sellers).
    greens = [(995.0 + 0.1 * i, 995.1 + 0.1 * i) for i in range(10)]
    start = int(dt.datetime.combine(DAY, dt.time(9, 46), tzinfo=IST).timestamp())
    for minute in range(6):
        for i, price in enumerate([996.0, 995.9, 995.8, 995.7, 995.6]):
            tick_store.add("FLW", start + minute * 60 + i * 10, price - 0.05 * minute)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + greens + [(996.0, 995.0)] + _quiet(334, 995.0)))
    trades = await replay_symbol(frame, "FLW", [DAY], _settings(
        db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, flow_exit=on, flow_exit_pct=60, flow_exit_window=3))
    assert trades[0]["direction"] == "LONG" and trades[0]["exit_reason"] == want
    if on:
        assert trades[0]["exit_time"].strftime("%H:%M") < "09:56"


# ---- entry check (flow_entry) -----------------------------------------------------------------------------------------

def test_entry_settings_and_window():
    cfg = type("C", (), {})()
    assert not flow_exit.entry_on(cfg) and flow_exit.entry_pct(cfg) == 55.0 and flow_exit.entry_window(cfg) == 3
    odd = type("C", (), {"flow_entry_pct": 10, "flow_entry_window": 7})()
    assert flow_exit.entry_pct(odd) == 50.0 and flow_exit.entry_window(odd) == 7
    assert flow_exit.entry_window(type("C", (), {"flow_entry_window": -1})()) == 3
    assert flow_exit.entry_span(_ts(10, 3, 20), 3, 5) == (int(_ts(10, 0)), int(_ts(10, 3)))
    assert flow_exit.entry_span(_ts(10, 5, 2), 0, 5) == (int(_ts(10, 0)), int(_ts(10, 5)))


def test_entry_refusal_reads_the_own_side():
    points = [{"time": 0, "buy": 300, "sell": 700, "src": "T"}, {"time": 60, "buy": 500, "sell": 500, "src": "T"}]
    assert flow_exit.entry_refusal(points, "SHORT", 55, 2) is None                       # sellers did 60%
    why = flow_exit.entry_refusal(points, "LONG", 55, 2)
    assert why == "executed volume: buyers did only 40% of the shares traded in 2 min (need 55%)"
    assert "too little volume" in flow_exit.entry_refusal([], "LONG", 55, 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("on", [False, True])
async def test_a_green_candle_sold_into_is_not_bought(db, on):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    # Greens from 09:45; every second the bots recorded from 09:42 to 09:56 ticks down (sellers hitting the bids).
    greens = [(995.0 + 0.1 * i, 995.1 + 0.1 * i) for i in range(10)]
    start = int(dt.datetime.combine(DAY, dt.time(9, 42), tzinfo=IST).timestamp())
    for minute in range(14):
        for i, price in enumerate([996.0, 995.9, 995.8, 995.7, 995.6]):
            tick_store.add("FLN", start + minute * 60 + i * 10, price - 0.05 * minute)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + greens + [(996.0, 995.0)] + _quiet(334, 995.0)))
    trades = await replay_symbol(frame, "FLN", [DAY], _settings(
        db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, flow_entry=on, flow_entry_pct=55, flow_entry_window=3))
    longs = [t for t in trades if t["direction"] == "LONG" and t["entry_time"].strftime("%H:%M") < "09:56"]
    assert bool(longs) is (not on)


# ---- confirm the other exits (flow_exit_mode = CONFIRM) ----------------------------------------------------------------

def test_mode_and_confirm_span():
    cfg = type("C", (), {"flow_exit": True})()
    assert flow_exit.mode(cfg) == "EXIT" and flow_exit.closes(cfg) and not flow_exit.confirms(cfg)
    cfg.flow_exit_mode = "confirm"
    assert flow_exit.confirms(cfg) and not flow_exit.closes(cfg)
    assert flow_exit.mode(type("C", (), {"flow_exit_mode": "odd"})()) == "EXIT"
    entry = _ts(9, 20, 30)                                    # first minute of the trade: 09:21
    # Window 0 on 15-minute candles: the last closed one (09:30-09:45), read at 09:52.
    assert flow_exit.confirm_span(_ts(9, 52, 10), entry, 0, 15) == (int(_ts(9, 30)), int(_ts(9, 45)))
    # At 09:31 the closed candle (09:15-09:30) is cut at the entry, so it reads 09:21-09:30.
    assert flow_exit.confirm_span(_ts(9, 31, 0), entry, 0, 15) == (int(_ts(9, 21)), int(_ts(9, 30)))
    # Minute windows are cut at the entry too; nothing closed since the entry = nothing to read.
    assert flow_exit.confirm_span(_ts(9, 23, 5), entry, 5, 15) == (int(_ts(9, 21)), int(_ts(9, 23)))
    assert flow_exit.confirm_span(_ts(9, 21, 40), entry, 5, 15) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,falling,held", [("CONFIRM", False, True), ("CONFIRM", True, False), (None, False, False)])
async def test_a_colour_turn_waits_for_the_executed_volume(db, mode, falling, held):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    # 09:45 greens buy at 09:46; red candles from 09:55. The seconds in the red minutes tick up (buyers still doing the
    # trading) or down (sellers).
    greens = [(995.0 + 0.1 * i, 995.1 + 0.1 * i) for i in range(10)]
    reds = [(996.0 - 0.2 * i, 995.8 - 0.2 * i) for i in range(3)]
    start = int(dt.datetime.combine(DAY, dt.time(9, 55), tzinfo=IST).timestamp())
    for minute in range(3):
        steps = [0.0, 0.05, 0.1, 0.15, 0.2]
        for i, step in enumerate(steps):
            base = 996.0 - 0.2 * minute
            tick_store.add("FLC", start + minute * 60 + i * 10, base - step if falling else base - 0.2 + step)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + greens + reds + _quiet(332, 995.4)))
    extra = {"flow_exit": True, "flow_exit_mode": mode, "flow_exit_pct": 60, "flow_exit_window": 1} if mode else {}
    trades = await replay_symbol(frame, "FLC", [DAY], _settings(
        db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, **extra))
    first = trades[0]
    assert first["direction"] == "LONG"
    if held:
        assert first["exit_reason"] != "COLOUR_TURN" and first["exit_time"].strftime("%H:%M") > "10:00"
    else:
        assert first["exit_reason"] == "COLOUR_TURN" and first["exit_time"].strftime("%H:%M") < "09:59"


def test_custom_windows_are_accepted_by_the_settings_api():
    from main import ConfigUpdate
    assert ConfigUpdate(flow_exit_window=7, flow_entry_window=45).flow_entry_window == 45
    with pytest.raises(Exception):
        ConfigUpdate(flow_exit_window=61)
    assert flow_exit.confirm_span(_ts(10, 20, 5), _ts(10, 0, 5), 7, 15) == (int(_ts(10, 13)), int(_ts(10, 20)))




# ---- executed volume starts the trade (flow_trigger) -------------------------------------------------------------------

def test_trigger_side_and_book_check():
    assert flow_exit.trigger_side(70, 60) == "LONG" and flow_exit.trigger_side(30, 60) == "SHORT"
    assert flow_exit.trigger_side(55, 60) is None
    assert flow_exit.split([], 3) is None
    assert flow_exit.split([{"time": 0, "buy": 300, "sell": 100, "src": "T"}], 1) == (75.0, "ticks")
    cfg = type("C", (), {"flow_trigger_book": 0})()
    assert flow_exit.trigger_book(cfg) == 0.0
    now = _ts(11, 0)
    assert "no fresh Most active read" in flow_exit.book_refusal("ACME", "SHORT", now, 55)
    pressure_exit.note_scan({"as_of": "", "rows": [{"symbol": "ACME", "buy_share": 0.3,
                                                    "read_at": dt.datetime.fromtimestamp(now - 60, IST).isoformat()}]})
    assert flow_exit.book_refusal("ACME", "SHORT", now, 55) is None                       # sellers hold 70%
    assert "buyers hold only 30%" in flow_exit.book_refusal("ACME", "LONG", now, 55)
    reads = flow_exit.entry_reads("ACME", None, now, 3)
    assert reads == {"wait_buy": 30.0, "read_at": int(now - 60)}


@pytest.mark.asyncio
@pytest.mark.parametrize("on", [False, True])
async def test_sellers_doing_the_trading_start_a_short(db, on):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    # Quiet candles all day; from 10:00 to 10:05 every recorded second ticks down: sellers do the trading.
    start = int(dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST).timestamp())
    for minute in range(5):
        for i, price in enumerate([996.0, 995.9, 995.8, 995.7, 995.6]):
            tick_store.add("FLT", start + minute * 60 + i * 10, price - 0.05 * minute)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(375)))
    trades = await replay_symbol(frame, "FLT", [DAY], _settings(
        db, qty=1, use_stop=False, flow_trigger=on, flow_trigger_pct=60, flow_trigger_window=3, flow_trigger_book=0))
    shorts = [t for t in trades if t["direction"] == "SHORT" and "10:0" <= t["entry_time"].strftime("%H:%M") <= "10:09"]
    assert bool(shorts) is on


def test_last_exit_candle_is_the_candle_the_chart_draws():
    # An order at 09:36 on 15-minute candles reads the 09:15-09:30 candle (the one the chart shows), not 09:21-09:36.
    assert flow_exit.entry_span(_ts(9, 36, 0), 0, 15) == (int(_ts(9, 15)), int(_ts(9, 30)))
    assert flow_exit.entry_span(_ts(12, 30, 5), 0, 15) == (int(_ts(12, 15)), int(_ts(12, 30)))
    assert flow_exit.entry_span(_ts(12, 30, 5), 10, 15) == (int(_ts(12, 20)), int(_ts(12, 30)))


# ---- executed volume only (entry_mode FLOW) -------------------------------------------------------------------------

def test_flow_only_mode_reads_no_waiting_orders():
    cfg = type("C", (), {"entry_mode": "FLOW", "flow_trigger": False, "flow_trigger_book": 70})()
    assert flow_exit.only_mode(cfg) and flow_exit.trigger_on(cfg) and flow_exit.trigger_book(cfg) == 0.0
    assert not flow_exit.only_mode(type("C", (), {"entry_mode": "COLOUR"})())
    from main import ConfigUpdate
    assert ConfigUpdate(entry_mode="FLOW").entry_mode == "FLOW"


@pytest.mark.asyncio
async def test_flow_only_mode_trades_executed_volume_not_crosses(db):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    # A clean rise from 11:00 makes the SMAs cross up (an SMA bot buys it), but no second was recorded there; the only
    # executed volume read is 10:00-10:05, where every second ticks down: sellers. No Most active read exists at all.
    start = int(dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST).timestamp())
    for minute in range(5):
        for i, price in enumerate([1000.0, 999.9, 999.8, 999.7, 999.6]):
            tick_store.add("FLO", start + minute * 60 + i * 10, price - 0.05 * minute)
    rise = [(1000.0 + 0.3 * i, 1000.3 + 0.3 * i) for i in range(30)]
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(105) + rise + _quiet(240, 1009.0)))
    base = dict(qty=1, use_stop=False, flow_trigger_pct=60, flow_trigger_window=3, flow_trigger_book=80)
    sma = await replay_symbol(frame, "FLO", [DAY], _settings(db, entry_mode="SMA", **base))
    only = await replay_symbol(frame, "FLO", [DAY], _settings(db, entry_mode="FLOW", **base))
    assert any(t["direction"] == "LONG" for t in sma)                                   # the cross trades in SMA mode
    assert only and only[0]["direction"] == "SHORT" and only[0]["entry_reason"].startswith("EXECUTED_VOLUME")
    assert not any(t["entry_reason"].startswith("SMA_CROSS") for t in only)              # crosses never enter


def test_chart_filters_carry_the_executed_volume_entry_for_the_why_line():
    from types import SimpleNamespace

    from strategy_engine import chart_filters

    cfg = SimpleNamespace(entry_mode="FLOW", flow_trigger=False, flow_trigger_pct=70, flow_trigger_window=0,
                          flow_trigger_book=55, flow_entry=True, flow_entry_pct=56, flow_entry_window=5,
                          candle_minutes=15, reentry_wait_candles=2, max_trades_per_stock=8)
    got = chart_filters(cfg)
    assert got["entry_mode"] == "FLOW" and got["flow_trigger"] is True       # the mode turns the trigger on
    assert (got["flow_trigger_pct"], got["flow_trigger_window"], got["flow_trigger_book"]) == (70, 0, 0.0)
    assert (got["flow_entry"], got["flow_entry_pct"], got["flow_entry_window"]) == (True, 56, 5)
    assert (got["candle_minutes"], got["reentry_wait_candles"], got["max_trades_per_stock"]) == (15, 2, 8)
