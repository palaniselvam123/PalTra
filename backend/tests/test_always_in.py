"""Always in a trade (always_in.py): a flat armed stock is entered on the SMAs' side, again after every exit.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import always_in
from tests.test_counter_candles import DAY, _frame
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_frame(), "SIME", [DAY], _settings(database, **over))


def test_side_reads_the_last_closed_candle():
    frame = pd.DataFrame({"sma_9": [10.0, 11.0, 9.0], "sma_21": [10.5, 10.5, 10.5]})
    assert always_in.side(frame) == "LONG"  # row -2: 11 > 10.5 (the forming row is never read)
    frame.loc[1, "sma_9"] = 10.0
    assert always_in.side(frame) == "SHORT"
    assert always_in.side(frame.iloc[:1]) is None
    assert always_in.enabled(type("C", (), {"always_in": True})()) and not always_in.enabled(object())


@pytest.mark.asyncio
async def test_always_in_enters_at_once_and_again_after_every_exit(db):  # noqa: F811
    plain = await _trades(db)
    always = await _trades(db, always_in=True, use_stop=True, stop_type="TSL", tsl_mode="POINTS",
                           tsl_sl_points=0.3, tsl_trail_points=0.1, tsl_target_points=0.0)
    assert always, "always in must trade"
    first = min(t["entry_time"] for t in always)
    # In before any fresh cross: the plain bot's first trade comes later (or never).
    assert not plain or first <= min(t["entry_time"] for t in plain)
    assert always[0]["entry_reason"] == "ALWAYS_IN"
    rows = sorted(always, key=lambda t: t["entry_time"])
    cutoff = dt.time(15, 0)
    for before, after in zip(rows, rows[1:]):
        if before["exit_time"] is None or before["exit_time"].time() >= cutoff:
            continue
        # Back in straight after the exit (the next minute at most in this replayer).
        assert after["entry_time"] - before["exit_time"] <= dt.timedelta(minutes=2), (before, after)
    assert len(always) > len(plain)


def test_reverse_helpers():
    on = type("C", (), {"always_in": True, "always_in_reverse": True})()
    assert always_in.reverse_on(on)
    assert not always_in.reverse_on(type("C", (), {"always_in": False, "always_in_reverse": True})())
    assert always_in.lost(-0.5) and not always_in.lost(0) and not always_in.lost(None) and not always_in.lost(3)
    assert always_in.opposite("LONG") == "SHORT" and always_in.opposite("SHORT") == "LONG"


@pytest.mark.asyncio
async def test_reverse_after_a_loss_goes_the_other_way(db):  # noqa: F811
    stop = dict(use_stop=True, stop_type="TSL", tsl_mode="POINTS", tsl_sl_points=0.3, tsl_trail_points=0.1, tsl_target_points=0.0)
    rows = sorted(await _trades(db, always_in=True, always_in_reverse=True, **stop), key=lambda t: t["entry_time"])
    reversed_ = [t for t in rows if t["entry_reason"] == "ALWAYS_IN_REVERSE"]
    assert reversed_, "a losing exit must be followed by a reverse"
    cutoff = dt.time(15, 0)
    for before, after in zip(rows, rows[1:]):
        if before["exit_time"] is None or before["exit_time"].time() >= cutoff:
            continue
        if after["entry_reason"] != "ALWAYS_IN_REVERSE":
            continue
        # A reverse only follows a losing trade, and goes the other way.
        gross = (before["exit_price"] - before["entry_price"]) * (1 if before["direction"] == "LONG" else -1)
        assert gross < 0, (before, after)
        assert after["direction"] != before["direction"], (before, after)
