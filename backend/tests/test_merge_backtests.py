"""merge_backtests.py: copying another server's backtests into this desk's database."""
from __future__ import annotations

import datetime as dt
import json
import sqlite3

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import merge_backtests
import run_source
from models import Base, HandoffLog, ReplayRun, TradeLog


def _db(path, runs):
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        for run_id, status, symbol in runs:
            s.add(ReplayRun(id=run_id, created_at=dt.datetime(2026, 11, 20, 16, run_id), start_date="2026-11-18",
                            end_date="2026-11-19", symbols=symbol, settings=json.dumps({"bot": 1}), status=status,
                            days_total=2, days_done=2, days="2026-11-18,2026-11-19"))
            for n in range(2):
                s.add(TradeLog(id=run_id * 10 + n, date="2026-11-18", symbol=symbol, direction="LONG", qty=10,
                               entry_time=dt.datetime(2026, 11, 18, 10, n), entry_price=100.0, ma_cross_price=100.0,
                               atr_at_entry=1.0, sl_trigger_price=98.0, exit_time=dt.datetime(2026, 11, 18, 11, n),
                               exit_price=101.0, exit_reason="MA_CROSS", gross_pnl=10.0, net_pnl=8.0, mode="REPLAY",
                               bot=1, run_id=run_id))
            s.add(HandoffLog(at=dt.datetime(2026, 11, 18, 10, 30), day="2026-11-18", mode="REPLAY", run_id=run_id,
                             from_bot=1, to_bot=2, symbol=symbol, kind="RECOVERY", trade_id=run_id * 10))
        s.commit()
    eng.dispose()


def _pnl(path, run_ids):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE replay_pnl (run_id INTEGER NOT NULL, bot INTEGER NOT NULL, day TEXT NOT NULL, "
                "minute TEXT NOT NULL, gross REAL, net REAL, held INTEGER, stocks TEXT, saved_at TEXT NOT NULL, "
                "closed_gross REAL, closed_net REAL, PRIMARY KEY (run_id, minute))")
    for r in run_ids:
        for m in ("2026-11-18 10:00", "2026-11-18 10:01"):
            con.execute("INSERT INTO replay_pnl VALUES (?,1,'2026-11-18',?,1,1,0,'',?,0,0)", (r, m, "x"))
    con.commit()
    con.close()


def test_copies_finished_runs_with_new_ids_and_skips_them_the_second_time(tmp_path):
    src, dst = tmp_path / "bt.db", tmp_path / "desk.db"
    src_pnl, dst_pnl = tmp_path / "bt_pnl.db", tmp_path / "desk_pnl.db"
    _db(src, [(1, "FINISHED", "SBIN"), (2, "RUNNING", "TCS")])
    _db(dst, [(1, "FINISHED", "INFY")])  # the desk's own run 1 keeps its id
    _pnl(src_pnl, [1, 2])
    args = ["--sma", str(src), "--into", str(dst), "--pnl", str(src_pnl), "--into-pnl", str(dst_pnl)]

    assert merge_backtests.main(args) == 0  # dry run writes nothing
    con = sqlite3.connect(dst)
    assert con.execute("SELECT count(*) FROM replay_run").fetchone()[0] == 1
    con.close()

    assert merge_backtests.main(args + ["--write"]) == 0
    con = sqlite3.connect(dst)
    runs = con.execute("SELECT id, symbols, settings FROM replay_run ORDER BY id").fetchall()
    assert [r[1] for r in runs] == ["INFY", "SBIN"]  # the running source run is left out
    new_id = runs[1][0]
    assert new_id != 1 and json.loads(runs[1][2])["copied_from"] == "backtest"
    trades = con.execute("SELECT id, symbol FROM trade_log WHERE run_id = ?", (new_id,)).fetchall()
    assert len(trades) == 2 and all(t[1] == "SBIN" for t in trades)
    own = con.execute("SELECT count(*) FROM trade_log WHERE run_id = 1").fetchone()[0]
    assert own == 2  # the desk's own run untouched
    hand = con.execute("SELECT trade_id FROM handoff_log WHERE run_id = ?", (new_id,)).fetchone()[0]
    assert hand in {t[0] for t in trades}  # the hand-off points at the copied trade
    con.close()
    pnl = sqlite3.connect(dst_pnl)
    assert pnl.execute("SELECT count(*) FROM replay_pnl WHERE run_id = ?", (new_id,)).fetchone()[0] == 2
    pnl.close()
    assert (tmp_path / "desk.db.before-merge").is_file()

    assert merge_backtests.main(args + ["--write"]) == 0  # again: nothing copied twice
    con = sqlite3.connect(dst)
    assert con.execute("SELECT count(*) FROM replay_run").fetchone()[0] == 2
    assert con.execute("SELECT count(*) FROM trade_log").fetchone()[0] == 4
    con.close()


def test_a_copied_run_says_so():
    assert run_source.describe({"copied_from": "backtest"})["via"] == "copied from the backtest server"
    assert run_source.describe({})["via"] is None
