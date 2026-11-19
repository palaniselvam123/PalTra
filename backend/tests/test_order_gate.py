"""Simple order-flow check (order_gate.py): executed / waiting tick boxes for the entry and the exit."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import order_gate
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


def _cfg(**kw):
    return type("C", (), kw)()


def _wait_read(symbol: str, buy_share: float, at: float) -> None:
    pressure_exit.note_scan({"as_of": "", "rows": [{"symbol": symbol, "buy_share": buy_share,
                                                    "read_at": dt.datetime.fromtimestamp(at, IST).isoformat()}]})


def test_settings_are_read_safely():
    cfg = _cfg()
    assert not order_gate.entry_on(cfg) and not order_gate.exit_on(cfg)
    assert order_gate.entry_exec_pct(cfg) == 60 and order_gate.exit_wait_pct(cfg) == 45
    odd = _cfg(og_entry_exec_pct=150, og_exit_exec_pct=0, og_entry_wait_pct="x")
    assert order_gate.entry_exec_pct(odd) == 99 and order_gate.exit_exec_pct(odd) == 1
    assert order_gate.entry_wait_pct(odd) == 60
    assert order_gate.entry_on(_cfg(og_entry_wait=True)) and order_gate.exit_on(_cfg(og_exit_exec=True))


def test_executed_reads_the_own_side():
    points = [{"time": 0, "buy": 700, "sell": 300, "src": "T"}, {"time": 60, "buy": 0, "sell": 0, "src": "C"}]
    assert order_gate.executed(points, "LONG", 2) == 70.0
    assert order_gate.executed(points, "SHORT", 2) == 30.0
    assert order_gate.executed([], "LONG", 5) is None


def _reading(exec_pct=None, wait_pct=None, side="LONG"):
    return order_gate.Reading(side=side, start=int(_ts(10, 0)), end=int(_ts(10, 5)), exec_pct=exec_pct,
                              wait_pct=wait_pct, wait_at=_ts(10, 4) if wait_pct is not None else None)


@pytest.mark.parametrize("ticks,exec_pct,wait_pct,ok", [
    ({"og_entry_exec": True}, 70, None, True),                       # only executed ticked: waiting not read
    ({"og_entry_exec": True}, 50, 90, False),
    ({"og_entry_wait": True}, 10, 75, True),                         # only waiting ticked
    ({"og_entry_wait": True}, 90, None, False),                      # no fresh read refuses
    ({"og_entry_exec": True, "og_entry_wait": True}, 70, 75, True),  # owner's example: 70 % and 75 %
    ({"og_entry_exec": True, "og_entry_wait": True}, 70, 50, False),
    ({"og_entry_exec": True, "og_entry_wait": True}, None, 80, False),
])
def test_entry_needs_every_ticked_number(ticks, exec_pct, wait_pct, ok):
    cfg = _cfg(og_entry_exec_pct=70, og_entry_wait_pct=70, **ticks)
    allowed, note = order_gate.entry_verdict(cfg, _reading(exec_pct, wait_pct))
    assert allowed is ok
    assert (order_gate.entry_refusal(cfg, _reading(exec_pct, wait_pct)) is None) is ok
    assert ("executed" in note) is bool(ticks.get("og_entry_exec"))
    assert ("waiting" in note) is bool(ticks.get("og_entry_wait"))


@pytest.mark.parametrize("ticks,exec_pct,wait_pct,close", [
    ({"og_exit_exec": True, "og_exit_wait": True}, 60, 80, False),   # owner's example: continue
    ({"og_exit_exec": True, "og_exit_wait": True}, 40, 20, True),    # owner's example: quit
    ({"og_exit_exec": True, "og_exit_wait": True}, 40, 80, False),   # both ticked: both must be weak
    ({"og_exit_exec": True}, 40, 80, True),                          # only executed ticked
    ({"og_exit_wait": True}, 60, 20, True),                          # only waiting ticked
    ({"og_exit_wait": True}, 10, None, False),                       # no read never closes
    ({"og_exit_exec": True}, None, 10, False),
])
def test_exit_closes_when_every_ticked_number_is_weak(ticks, exec_pct, wait_pct, close):
    cfg = _cfg(og_exit_exec_pct=45, og_exit_wait_pct=45, **ticks)
    assert order_gate.exit_verdict(cfg, _reading(exec_pct, wait_pct))[0] is close


def test_waiting_is_the_trade_side_from_a_fresh_read():
    now = _ts(11, 0)
    assert order_gate.waiting("ACME", "LONG", now) is None
    _wait_read("ACME", 0.25, now - 120)
    assert order_gate.waiting("ACME", "SHORT", now)[0] == 75.0
    assert order_gate.waiting("ACME", "LONG", now)[0] == 25.0
    assert order_gate.waiting("ACME", "LONG", now + pressure_exit.MAX_AGE_SEC + 1) is None   # too old


@pytest.mark.asyncio
@pytest.mark.parametrize("on", [False, True])
async def test_a_green_candle_sold_into_is_not_bought(db, on):  # noqa: F811
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    greens = [(995.0 + 0.1 * i, 995.1 + 0.1 * i) for i in range(10)]
    start = int(dt.datetime.combine(DAY, dt.time(9, 42), tzinfo=IST).timestamp())
    for minute in range(14):
        for i, price in enumerate([996.0, 995.9, 995.8, 995.7, 995.6]):
            tick_store.add("OGN", start + minute * 60 + i * 10, price - 0.05 * minute)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + greens + [(996.0, 995.0)] + _quiet(334, 995.0)))
    trades = await replay_symbol(frame, "OGN", [DAY], _settings(
        db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, og_entry_exec=on, og_entry_exec_pct=60))
    longs = [t for t in trades if t["direction"] == "LONG" and t["entry_time"].strftime("%H:%M") < "09:56"]
    assert bool(longs) is (not on)


@pytest.mark.asyncio
@pytest.mark.parametrize("ticks,shares,closes_at", [
    ({"og_exit_exec": True, "og_exit_wait": True}, [(0.60, 0.80), (0.40, 0.20)], 2),
    ({"og_exit_exec": True, "og_exit_wait": True}, [(0.40, 0.80), (0.60, 0.20)], None),
    ({"og_exit_exec": True}, [(0.60, 0.20), (0.40, 0.80)], 2),
    ({"og_exit_wait": True}, [(0.40, 0.80), (0.40, None)], None),
])
async def test_the_exit_judges_each_closed_candle(monkeypatch, tmp_path, ticks, shares, closes_at):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/g.db")
    import database
    from strategy_engine import OpenPosition, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    entry = dt.datetime(2026, 11, 16, 10, 0, 5, tzinfo=IST)
    clock = {"now": entry}
    monkeypatch.setattr(eng, "_now", lambda: clock["now"])
    eng._focus = "ACME"
    eng.position = OpenPosition(
        direction="LONG", qty=10, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=0.0,
        sl_order_id="", entry_order_id="E", entry_time=entry, trade_id=0, mode="PAPER", stop_active=False,
    )
    eng.status, eng.ltp = "RUNNING", 99.0
    current = {}

    def fake_minutes(symbol, tape, start, end):
        buy = current["exec"]
        return [{"time": t, "buy": 100 * buy, "sell": 100 * (1 - buy), "src": "T"} for t in range(start, end, 60)]

    monkeypatch.setattr(trade_flow, "minutes", fake_minutes)
    exits = []

    async def fake_exit(cfg, price, reason):
        exits.append(reason)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    monkeypatch.setattr(eng, "_exit_minutes", lambda symbol, c: 5)
    cfg = _cfg(symbol="ACME", og_exit_exec_pct=45, og_exit_wait_pct=45, **ticks)
    closed = None
    for i, (exec_share, wait_share) in enumerate(shares, start=1):
        current["exec"] = exec_share
        clock["now"] = dt.datetime(2026, 11, 16, 10, 5, 20, tzinfo=IST) + dt.timedelta(minutes=5 * (i - 1))
        now = clock["now"].timestamp()
        pressure_exit.reset()
        if wait_share is not None:
            _wait_read("ACME", wait_share, now - 60)
        eng._tapes["ACME"] = pd.DataFrame({"ts": [int(now) - 80]})
        await eng._watch_order_gate(cfg)
        await eng._watch_order_gate(cfg)               # the same candle is judged once
        if exits and closed is None:
            closed = i
    assert closed == closes_at
    assert set(exits) <= {order_gate.EXIT_REASON}
    notes = eng._gate_notes("ACME", [{"time": 0}])
    assert len(notes) == len(shares)              # one note per candle judged
    assert all(n["kind"] == "exit" for n in notes)
    assert notes[0]["text"].startswith("held LONG")
    database.reset_engine()


def test_the_first_candle_judged_opens_after_the_entry(monkeypatch):
    import flow_exit

    start, _ = order_gate.candle_span(_ts(10, 5, 20), 5)
    assert start >= flow_exit.first_minute(_ts(10, 0, 5))          # 10:00-10:05 counts for a 10:00:05 entry
    start, _ = order_gate.candle_span(_ts(10, 5, 20), 5)
    assert start < flow_exit.first_minute(_ts(10, 2))              # an entry at 10:02 waits for 10:05-10:10


def test_settings_api_and_snapshot():
    from main import ConfigUpdate
    from strategy_engine import SNAPSHOT_FIELDS, STOCK_FIELDS, chart_filters

    up = ConfigUpdate(og_entry_exec=True, og_entry_exec_pct=70, og_exit_wait=True, og_exit_wait_pct=20)
    assert up.og_entry_exec_pct == 70 and up.og_exit_wait_pct == 20
    with pytest.raises(Exception):
        ConfigUpdate(og_exit_exec_pct=120)
    for name in ("og_entry_exec", "og_entry_wait_pct", "og_exit_exec", "og_exit_wait_pct"):
        assert name in SNAPSHOT_FIELDS and name in STOCK_FIELDS
    f = chart_filters(_cfg(og_entry_exec=True, og_entry_exec_pct=70))
    assert f["og_entry_exec"] is True and f["og_entry_exec_pct"] == 70 and f["og_exit_wait"] is False
