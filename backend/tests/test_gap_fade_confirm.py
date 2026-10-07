"""Gap mode fade exit: ride out a pullback, exit on the reversal. PAPER replay only."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from gap_mode import fade_confirmed, judge_exit
from tests.test_gap_mode import _day, _settings, db  # noqa: F401


def _cfg(**over):
    base = dict(gap_exit_long=0.02, gap_exit_short=-0.02, gap_giveback_pct=0.0, gap_fade_min_candles=0,
                gap_fade_confirm_sma=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_off_by_default_confirms_every_fade():
    assert fade_confirmed(_cfg(), "LONG", 100.0, 101.0, {}) == (True, "")


def test_close_must_break_the_slow_sma_against_the_trade():
    cfg = _cfg(gap_fade_confirm_sma=True)
    ok, why = fade_confirmed(cfg, "SHORT", 99.0, 100.0, {})
    assert not ok and "still below the slow SMA" in why
    assert fade_confirmed(cfg, "SHORT", 100.5, 100.0, {})[0]
    assert not fade_confirmed(cfg, "LONG", 100.5, 100.0, {})[0]
    assert fade_confirmed(cfg, "LONG", 99.5, 100.0, {})[0]
    assert not fade_confirmed(cfg, "LONG", 99.5, None, {})[0]


def test_narrowing_run_is_counted_and_resets_when_the_gap_widens():
    cfg, state = _cfg(gap_fade_min_candles=3), {}
    for gap, prev in [(0.10, None), (0.12, 0.10), (0.11, 0.12), (0.09, 0.11)]:
        judge_exit(cfg, "LONG", gap, prev, state)
    assert state["narrow_run"] == 2
    assert not fade_confirmed(cfg, "LONG", 0, 0, state)[0]
    judge_exit(cfg, "LONG", 0.08, 0.09, state)
    assert state["narrow_run"] == 3 and fade_confirmed(cfg, "LONG", 0, 0, state)[0]
    judge_exit(cfg, "LONG", 0.10, 0.08, state)
    assert state["narrow_run"] == 0


def _pullback_then_reversal(i: int, p: float) -> float:
    if i < 60:
        return 1000.0 + (0.02 if i % 2 else -0.02)  # 09:15-10:14 flat
    if i < 85:
        return p - 0.15  # fall: the short
    if i < 91:
        return p + 0.06  # pullback: the gap narrows, candles stay below SMA 21
    if i < 113:
        return p - 0.15  # the fall resumes
    if i < 125:
        return p + 0.40  # sharp reversal from 11:08
    return p + (0.02 if i % 2 else -0.02)


@pytest.mark.asyncio
async def test_rides_out_the_pullback_and_exits_on_the_reversal(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731
    frame = pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(dt.date(2026, 9, 22), _pullback_then_reversal))
    base = dict(use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.01, gap_entry_short=-0.05,
                gap_exit_short=-0.01, gap_giveback_pct=30)

    async def first_short(**over):
        trades = await replay_symbol(frame, "SIMG", [dt.date(2026, 9, 22)], _settings(db, **{**base, **over}))
        return next(t for t in trades if t["direction"] == "SHORT")

    plain = await first_short()
    held = await first_short(gap_fade_confirm_sma=True)
    pullback_ends, reversal = dt.datetime(2026, 9, 22, 10, 50), dt.datetime(2026, 9, 22, 11, 8)
    assert plain["exit_reason"] == "GAP_FADE" and plain["exit_time"] < pullback_ends  # out on the pullback
    assert held["exit_reason"] == "GAP_FADE" and held["exit_time"] >= reversal  # held to the reversal
    assert held["entry_time"] == plain["entry_time"]
    assert held["exit_price"] < plain["exit_price"]  # the short kept the second leg down
