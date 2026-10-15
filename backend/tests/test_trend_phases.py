"""Trend phases: today's candles split into choppy / strong bull / strong bear. Read-only, no order path."""
from __future__ import annotations

import numpy as np
import pandas as pd

import trend_phases as tp
from indicators import enrich

DAY = 1_792_035_900  # 15 Oct 2026 09:15 IST


def _frame(closes: list[float]) -> pd.DataFrame:
    c = np.array(closes, dtype="float64")
    o = np.r_[c[0], c[:-1]]
    f = pd.DataFrame({"ts": DAY + 300 * np.arange(len(c)), "open": o, "high": np.maximum(o, c) + 0.3, "low": np.minimum(o, c) - 0.3, "close": c})
    return enrich(f)


def _shape() -> list[float]:
    chop = [100 + (0.8 if i % 2 else -0.8) for i in range(30)]
    up = [100 + 0.7 * (i + 1) for i in range(15)]
    down = [up[-1] - 0.7 * (i + 1) for i in range(12)]
    tail = [down[-1] + (0.5 if i % 2 else -0.5) for i in range(10)]
    return chop + up + down + tail


def test_choppy_then_up_then_down_is_found_in_order():
    found = tp.phases(_frame(_shape()), DAY)
    sides = [p["side"] for p in found]
    assert sides == [tp.CHOP, tp.BULL, tp.BEAR, tp.CHOP]
    bull = found[1]
    assert DAY + 300 * 29 <= bull["start_ts"] <= DAY + 300 * 30  # backdated to the turn, not the late detection
    assert bull["move_pct"] > 5
    assert found[2]["move_pct"] < -4


def test_a_short_burst_stays_choppy():
    closes = [100 + (0.8 if i % 2 else -0.8) for i in range(30)] + [100 + i for i in range(1, 4)] + [103 + (0.8 if i % 2 else -0.8) for i in range(20)]
    assert {p["side"] for p in tp.phases(_frame(closes), DAY)} == {tp.CHOP}


def test_only_todays_candles_are_labelled():
    found = tp.phases(_frame(_shape()), DAY + 300 * 40)
    assert found[0]["start_ts"] == DAY + 300 * 40


def test_summary_reports_the_phase_now():
    s = tp.summary(tp.phases(_frame(_shape()), DAY))
    assert s["phase"] == tp.CHOP and s["strong_phases"] == 2 and len(s["phases"]) == 4
    assert tp.summary([])["phase"] is None
