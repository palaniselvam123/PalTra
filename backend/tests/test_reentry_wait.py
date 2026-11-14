"""Wait N candles after an exit before the next entry (reentry_wait.py, reentry_wait_candles; 0 = off)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import reentry_wait
from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
from tests.test_gap_mode import _settings, db  # noqa: F401
from tests.test_stock_trade_cap import _add, eng  # noqa: F401

IST = reentry_wait.IST


def at(hh: int, mm: int, ss: int = 0) -> dt.datetime:
    return dt.datetime(2026, 10, 19, hh, mm, ss, tzinfo=IST)


def test_the_wait_counts_whole_candles_after_the_exit():
    # A colour turn on a 15-minute candle's close (processed a little after 11:00): 11:00-11:15 and 11:15-11:30 wait.
    assert reentry_wait.ready_at(at(11, 0, 40), 15, 2) == at(11, 30)
    # A stop hit inside a candle: that candle does not count; the next two do.
    assert reentry_wait.ready_at(at(11, 7), 15, 2) == at(11, 45)
    assert reentry_wait.ready_at(at(11, 7), 15, 1) == at(11, 30)
    # 1-minute candles: an exit at 10:05:20 (into the 10:05 candle) waits 10:05 and 10:06 with N = 2.
    assert reentry_wait.ready_at(at(10, 5, 20), 1, 2) == at(10, 7)
    # 5-minute candles from 09:15: an exit at 09:52 (mid-candle) counts from 09:55; one at 09:50:45 was its close.
    assert reentry_wait.ready_at(at(9, 52), 5, 1) == at(10, 0)
    assert reentry_wait.ready_at(at(9, 50, 45), 5, 1) == at(9, 55)


def test_refusal_text_and_off():
    assert reentry_wait.refusal("MANKIND", at(11, 0, 40), at(11, 29), 15, 2) == (
        "MANKIND waits 2 candle(s) of 15 min after its exit at 11:00 (next entry from 11:30)"
    )
    assert reentry_wait.refusal("MANKIND", at(11, 0, 40), at(11, 30), 15, 2) is None
    assert reentry_wait.refusal("MANKIND", at(11, 0, 40), at(11, 10), 15, 0) is None
    assert reentry_wait.refusal("MANKIND", None, at(11, 10), 15, 2) is None
    # A new day starts fresh.
    assert reentry_wait.refusal("MANKIND", at(15, 10), at(11, 10) + dt.timedelta(days=1), 15, 2) is None
    assert reentry_wait.candles(SimpleNamespace(reentry_wait_candles=99)) == 10
    assert reentry_wait.candles(SimpleNamespace(reentry_wait_candles="x")) == 0


def test_the_engine_reads_each_stocks_last_exit_and_a_restart_keeps_it(eng):  # noqa: F811
    cfg = SimpleNamespace(symbol="TCS", reentry_wait_candles=3, entry_mode="SMA", candle_minutes=1, stock_settings=None)
    assert eng._reentry_wait_hit(cfg) is None  # no exit yet
    now = eng._now()
    eng._last_exit["TCS"] = now
    assert eng._reentry_wait_hit(cfg) is not None
    assert eng._reentry_wait_hit(SimpleNamespace(**{**vars(cfg), "symbol": "INFY"})) is None  # each stock on its own
    assert eng._reentry_wait_hit(SimpleNamespace(**{**vars(cfg), "reentry_wait_candles": 0})) is None
    # A restart: today's last exit per stock comes back from the trade book.
    eng._last_exit = {}
    _add("TCS", eng._session_date, 2)
    eng.restore_trades_today()
    assert eng._last_exit["TCS"].replace(tzinfo=None) == dt.datetime(2026, 10, 7, 10, 1, 30)


def test_the_setting_is_per_stock_and_in_the_snapshot():
    from models import BotConfig
    from strategy_engine import SNAPSHOT_FIELDS, STOCK_FIELDS, _cfg_for

    assert "reentry_wait_candles" in SNAPSHOT_FIELDS and "reentry_wait_candles" in STOCK_FIELDS
    row = BotConfig(symbol="TCS", reentry_wait_candles=1, stock_settings='{"INFY": {"reentry_wait_candles": 3}}')
    assert _cfg_for(row, "INFY").reentry_wait_candles == 3
    assert _cfg_for(row, "TCS").reentry_wait_candles == 1


def _got(trades):
    return [(t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"]) for t in trades]


async def _run(db, **over):  # noqa: F811
    from sma_research.replayer import replay_symbol

    # From 09:45 the 1-minute colour candles alternate green / red for 20 minutes: a sideways stock.
    flips = []
    price = 1000.0
    for i in range(20):
        nxt = price + (0.3 if i % 2 == 0 else -0.3)
        flips.append((price, nxt))
        price = nxt
    day2 = _quiet(30) + flips + _quiet(325, price)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, day2))
    return await replay_symbol(frame, "COL", [DAY], _settings(db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, **over))


@pytest.mark.asyncio
async def test_a_sideways_colour_stock_waits_after_each_exit(db):  # noqa: F811
    plain = _got(await _run(db))
    # Without the wait: each colour turn closes and reverses at once, so trades follow each other with no gap.
    assert len(plain) >= 15
    assert any(b[1] == a[2] for a, b in zip(plain, plain[1:]))

    waited = _got(await _run(db, reentry_wait_candles=2))
    assert plain[0][:2] == waited[0][:2]  # the first entry is not touched
    assert 2 <= len(waited) < len(plain) // 2
    for before, after in zip(waited, waited[1:]):
        # Never a reverse on the exit's candle: the next entry comes at least 2 one-minute candles later.
        assert after[1] - before[2] >= dt.timedelta(minutes=2), (before, after)
