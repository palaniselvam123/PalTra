"""Saved-scan backtest (saved_picks.py): pick each day's first usable saved Most active scan at or after a time, rank by
one or more columns, take the top N, and replay them from the minute after the scan read them at the run's quantity."""
from __future__ import annotations

import asyncio
import datetime as dt
import json

import pandas as pd
import pytest

import saved_picks
from tests.test_gap_mode import _day

IST = saved_picks.IST


def _row(sym, rvol, share, value=10.0, read="09:16:30", day="2026-11-02", **extra):
    return {"symbol": sym, "rvol": rvol, "buy_share": share, "value_cr": value, "ltp": 100.0,
            "change_pct": extra.pop("move", 1.0), "read_at": f"{day}T{read}+05:30", **extra}


def _scan(at, rows, day="2026-11-02"):
    return {"as_of": f"{day}T{at}+05:30", "rows": rows}


def test_ranks_by_one_column_and_trades_after_the_last_read():
    scan = _scan("09:15:05", [_row("A", 2.0, 0.6), _row("B", 5.0, 0.4, read="09:17:40"), _row("C", 3.0, 0.7)])
    rule = saved_picks.SavedScanRule(top_n=2, rank_by=["rvol"])
    picks, note = saved_picks.choose_for_day([scan], rule)
    assert [p["symbol"] for p in picks] == ["B", "C"]
    assert {p["at"] for p in picks} == {"09:18"}, "the minute after B's read at 09:17:40"
    assert [p["side"] for p in picks] == ["SELL", "BUY"]
    assert "#1 by Vol vs usual" in picks[0]["why"] and "traded from 09:18" in note


def test_several_columns_average_the_places():
    rows = [_row("A", 5.0, 0.55), _row("B", 1.0, 0.90), _row("C", 4.0, 0.80)]
    rule = saved_picks.SavedScanRule(top_n=1, rank_by=["rvol", "buyers"])
    # A: places 1 + 3, B: 3 + 1, C: 2 + 2 → ties of 2.0, broken by money traded (all equal) then the name.
    picks, _ = saved_picks.choose_for_day([_scan("09:15:00", rows)], rule)
    assert picks[0]["symbol"] == "A"
    rows[2]["value_cr"] = 50.0
    picks, _ = saved_picks.choose_for_day([_scan("09:15:00", rows)], rule)
    assert picks[0]["symbol"] == "C"


def test_a_thin_first_scan_is_skipped_and_named():
    thin = _scan("09:15:10", [_row("A", None, 0.6), _row("B", 2.0, 0.6)])
    good = _scan("09:29:00", [_row("A", 3.0, 0.6, read="09:30:10"), _row("B", 2.0, 0.6, read="09:30:20")])
    early = _scan("09:10:00", [_row("Z", 9.0, 0.6)])  # before the session: never read
    picks, note = saved_picks.choose_for_day([good, thin, early], saved_picks.SavedScanRule(top_n=2))
    assert [p["symbol"] for p in picks] == ["A", "B"] and picks[0]["at"] == "09:31"
    assert "09:15 not available (09:15:10 (1 of 2 stocks usable))" in picks[0]["why"]
    assert "09:15 not available" in note


def test_filters_and_one_sided_books():
    rows = [_row("BUY", 3.0, 0.7), _row("SELL", 4.0, 0.3), _row("ONE", 9.0, 1.0), _row("SMALL", 8.0, 0.8, value=1.0)]
    rule = saved_picks.SavedScanRule(top_n=1, rank_by=["rvol"], side="BUYERS", min_value_cr=5.0)
    picks, _ = saved_picks.choose_for_day([_scan("09:20:00", rows)], rule)
    assert [p["symbol"] for p in picks] == ["BUY"], "ONE's book is one-sided, SMALL trades too little money"
    rule = saved_picks.SavedScanRule(top_n=1, rank_by=["sellers"])
    assert saved_picks.choose_for_day([_scan("09:20:00", rows)], rule)[0][0]["symbol"] == "SELL"


def test_no_scan_at_or_after_the_time():
    picks, note = saved_picks.choose_for_day([_scan("09:20:00", [_row("A", 2, 0.6)])],
                                             saved_picks.SavedScanRule(pick_time=dt.time(10, 0), top_n=1))
    assert picks == [] and "10:00" in note


def test_rule_round_trips_for_resume():
    from scalp_picks import picks_for_day, rule_from_dict

    rule = saved_picks.SavedScanRule(top_n=3, rank_by=["value", "rvol", "nope"], qty=100,
                                     chosen={"2026-11-02": [{"symbol": "A", "at": "09:16"}]})
    back = rule_from_dict(json.loads(json.dumps(rule.as_dict())))
    assert isinstance(back, saved_picks.SavedScanRule)
    assert (back.top_n, back.rank_by, back.qty) == (3, ["value", "rvol"], 100)
    assert picks_for_day({"A": None}, dt.date(2026, 11, 2), back) == [{"symbol": "A", "at": "09:16"}]


@pytest.mark.asyncio
async def test_a_replay_trades_the_picks_from_their_minute_at_the_runs_qty(monkeypatch, tmp_path):
    import replay as replay_mod
    from database import session_factory
    from models import ReplayRun, TradeLog
    from replay import ReplaySession

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    import database

    database.reset_engine()
    database.init_db()
    days = (dt.date(2026, 9, 25), dt.date(2026, 9, 28))
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    async def fake_fetch(broker, symbol, start, end):  # noqa: ARG001
        wave = lambda i, p: 1000.0 + 3.0 * ((i // 12) % 2)  # noqa: E731 - crosses all day
        return pd.DataFrame(_day(days[0], flat) + _day(days[1], wave))

    monkeypatch.setattr(replay_mod, "fetch_frame", fake_fetch)
    monkeypatch.setattr(replay_mod, "SESSION_END", dt.time(12, 0))
    monkeypatch.setattr(replay_mod, "STEP_SECONDS", 60)
    monkeypatch.setattr(replay_mod, "CPU_SHARE", 1000.0)
    monkeypatch.setattr(replay_mod, "LOOP_SECONDS", 0.01)

    rule = saved_picks.SavedScanRule(top_n=1, qty=7,
                                     chosen={days[1].isoformat(): [{"symbol": "AAA", "at": "10:30", "side": "BUY"}]})
    session = ReplaySession()
    await session.begin(object(), ["AAA"], days[1], dt.time(9, 15), 1_000_000, end_day=days[1],
                        settings={"qty": 10}, rule=rule)
    for _ in range(600):
        await asyncio.sleep(0.05)
        if session.status in ("FINISHED", "ERROR"):
            break
    assert session.status == "FINISHED", session.error
    with session_factory()() as s:
        run = s.get(ReplayRun, session.run_id)
        assert json.loads(run.settings)["scalp_pick"]["kind"] == "SAVED"
        trades = s.query(TradeLog).filter(TradeLog.run_id == run.id).all()
    assert trades, "the wave crosses after 10:30"
    assert all(t.symbol == "AAA" and t.entry_time.time() >= dt.time(10, 30) and t.qty == 7 for t in trades)
    database.reset_engine()


def test_api_refuses_without_saved_scans(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "scans.db"))
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        bad = client.post("/api/replay/saved-scans", json={"date": "2026-11-02"})
        assert bad.status_code == 400 and "saved Most active" in bad.json()["detail"]
        bad = client.post("/api/replay/saved-scans", json={"date": "2026-11-02", "rank_by": ["oops"]})
        assert bad.status_code == 400 and "Rank by" in bad.json()["detail"]
        assert client.post("/api/replay/saved-scans", json={"date": "2026-11-02", "at": "15:20"}).status_code == 400
    database.reset_engine()
