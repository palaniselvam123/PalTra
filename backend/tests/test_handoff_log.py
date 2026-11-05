"""Hand-off trail and trend at entry (handoff_log.py): bookkeeping only, PAPER replay engine."""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

import handoff_log
from tests.test_breakout import DAY, _frame, _run, _seed
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)


@pytest.mark.asyncio
async def test_a_breakout_hand_off_is_kept_with_the_trend_and_trades_carry_their_trend(db):  # noqa: F811
    from models import TradeLog

    _seed(db)
    moved, _trades, _engine = await _run(db, _frame(spike_volume=True), breakout_handoff=True)
    assert moved == ["SIMB"]
    rows = handoff_log.rows(day=DAY.isoformat())
    assert len(rows) == 1
    row = rows[0]
    assert (row["from_bot"], row["to_bot"], row["symbol"], row["kind"]) == (1, 3, "SIMB", "BREAKOUT")
    assert row["moved"] and row["result"].startswith("moved")
    assert row["at"].startswith("2026-09-22T11:")
    assert row["regime"] and row["regime"]["phase"] in ("BULL", "BEAR", "CHOP")
    assert handoff_log.rows(day=DAY.isoformat(), bot=3)[0]["id"] == row["id"]
    assert handoff_log.rows(day=DAY.isoformat(), bot=2) == []
    with db.session_factory()() as s:
        saved = [t.entry_regime for t in s.query(TradeLog).filter(TradeLog.symbol == "SIMB").all()]
    assert saved and all(saved)
    first = json.loads(saved[0])
    assert first["score"] in ("TRENDING", "MIXED", "CHOPPY") and first["minutes"] == 1


def test_run_rows_are_dropped_with_the_run(db):  # noqa: F811
    at = dt.datetime(2026, 9, 22, 11, 0)
    for day_shift, run in ((0, 7), (1, 7), (0, None)):
        handoff_log.record(
            at=at + dt.timedelta(days=day_shift), mode="REPLAY" if run else "PAPER", run_id=run, from_bot=1, to_bot=2,
            symbol="sbin", kind="RECOVERY", why="target", result="moved to Bot 2 (flipped, gap mode)",
        )
    assert len(handoff_log.rows(run_id=7)) == 2
    assert len(handoff_log.rows(day="2026-09-22")) == 1  # the market-day view leaves replays out
    handoff_log.delete_run(7, ["2026-09-23"])
    assert [r["day"] for r in handoff_log.rows(run_id=7)] == ["2026-09-22"]
    handoff_log.delete_run(7)
    assert handoff_log.rows(run_id=7) == []


def test_trend_groups_and_text():
    up = json.dumps({"phase": "BULL", "score": "TRENDING", "crosses": 1, "phase_since": "12:10", "phase_move_pct": 0.8})
    chop = json.dumps({"phase": "CHOP", "score": "CHOPPY", "crosses": 5})
    t = lambda d, reg, g: SimpleNamespace(direction=d, entry_regime=reg, exit_price=1.0, gross_pnl=g, net_pnl=g - 1)  # noqa: E731
    groups = {g["group"]: g for g in handoff_log.by_regime([t("LONG", up, 100), t("SHORT", up, -40), t("LONG", chop, -10), t("LONG", None, 5)])}
    assert groups["UP_WITH"]["gross"] == 100 and groups["UP_WITH"]["win_pct"] == 100.0
    assert groups["UP_AGAINST"]["trades"] == 1 and groups["CHOPPY"]["gross"] == -10
    assert groups["UNKNOWN"]["trades"] == 1
    text = handoff_log.trend_text(json.loads(up), "LONG")
    assert text.startswith("Up since 12:10 (+0.80%), with it") and "1 cross" in text
    assert handoff_log.trend_text(None, "LONG") == ""
