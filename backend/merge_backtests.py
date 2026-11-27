"""Copy another server's backtests into this desk's database (owner's request: moving from the old Fly account,
the backtest server's runs should show on the new desk too).

Read-only on the source files; writes only the target's backtest tables. No order path, nothing armed, no setting
changed. Run it on the desk's machine (`fly ssh console`), from /app:

    python merge_backtests.py --sma /data/bt_sma.db [--pnl /data/bt_pnl.db] [--trend /data/bt_trend.db]   # check
    python merge_backtests.py --sma /data/bt_sma.db [--pnl ...] [--trend ...] --write                     # copy

What is copied, for every finished / stopped / interrupted run of the source:
- `replay_run` (a new id here; its settings get `copied_from: "backtest"`, so the run detail says so and the practice
  wallet never counts it: those runs never used this desk's wallet),
- its `trade_log` rows (mode REPLAY, new ids, `run_id` moved to the new run),
- its `review_log` and `handoff_log` rows (run and trade ids moved),
- with `--pnl`, its replay P&L log (`replay_pnl` in open_pnl.db; the P&L dashboard and the run's day high / low),
- with `--trend`, its trend readings (`trend_reads.db`).

A run already here (same start time, days and settings) is skipped, so running it twice copies nothing twice. Before
writing, each target file is backed up next to itself (`*.before-merge`).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys

COPIED = "backtest"
DONE = ("FINISHED", "STOPPED", "INTERRUPTED", "DONE")


def _cols(con: sqlite3.Connection, table: str) -> list[str]:
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    except sqlite3.Error:
        return []


def _has(con: sqlite3.Connection, table: str) -> bool:
    return bool(_cols(con, table))


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    cur = con.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _insert(con: sqlite3.Connection, table: str, row: dict, cols: list[str]) -> int:
    keep = [c for c in cols if c in row]
    cur = con.execute(
        f"INSERT INTO {table} ({', '.join(keep)}) VALUES ({', '.join('?' for _ in keep)})",
        [row[c] for c in keep],
    )
    return int(cur.lastrowid)


def _key(run: dict) -> tuple:
    try:
        settings = json.loads(run.get("settings") or "{}")
    except ValueError:
        settings = {}
    if isinstance(settings, dict):
        settings.pop("copied_from", None)
    return (
        str(run.get("created_at")), run.get("start_date"), run.get("end_date"), run.get("start_time"),
        run.get("symbols"), json.dumps(settings, sort_keys=True, default=str),
    )


def _backup(path: str) -> None:
    if os.path.isfile(path):
        shutil.copy2(path, path + ".before-merge")


def merge_sma(src: sqlite3.Connection, dst: sqlite3.Connection) -> tuple[dict[int, int], dict]:
    """Copy the runs and their trades / reviews / hand-offs. Returns old run id → new run id and counts."""
    counts = {"runs": 0, "skipped": 0, "trades": 0, "reviews": 0, "handoffs": 0}
    run_cols = [c for c in _cols(dst, "replay_run") if c != "id"]
    trade_cols = [c for c in _cols(dst, "trade_log") if c != "id"]
    have = {_key(r) for r in _rows(dst, "SELECT * FROM replay_run")}
    runs: dict[int, int] = {}
    for run in _rows(src, "SELECT * FROM replay_run ORDER BY id"):
        if str(run.get("status") or "").upper() not in DONE:
            continue
        if _key(run) in have:
            counts["skipped"] += 1
            continue
        try:
            settings = json.loads(run.get("settings") or "{}")
        except ValueError:
            settings = {}
        if not isinstance(settings, dict):
            settings = {}
        settings["copied_from"] = COPIED
        row = dict(run, settings=json.dumps(settings))
        runs[int(run["id"])] = _insert(dst, "replay_run", row, run_cols)
        counts["runs"] += 1
    trades: dict[int, int] = {}
    for old, new in runs.items():
        for t in _rows(src, "SELECT * FROM trade_log WHERE run_id = ? ORDER BY id", (old,)):
            trades[int(t["id"])] = _insert(dst, "trade_log", dict(t, run_id=new), trade_cols)
            counts["trades"] += 1
    for table, key in (("review_log", "reviews"), ("handoff_log", "handoffs")):
        if not (_has(src, table) and _has(dst, table)):
            continue
        cols = [c for c in _cols(dst, table) if c != "id"]
        for old, new in runs.items():
            for r in _rows(src, f"SELECT * FROM {table} WHERE run_id = ? ORDER BY id", (old,)):
                tid = r.get("trade_id")
                row = dict(r, run_id=new, trade_id=trades.get(int(tid), tid) if tid is not None else None)
                _insert(dst, table, row, cols)
                counts[key] += 1
    return runs, counts


def merge_keyed(src: sqlite3.Connection, dst: sqlite3.Connection, table: str, runs: dict[int, int]) -> int:
    """Rows keyed by run_id (replay_pnl, trend_reads): copied with the new run id, duplicates ignored."""
    if not (_has(src, table) and _has(dst, table)):
        return 0
    cols = [c for c in _cols(dst, table) if c in _cols(src, table)]
    n = 0
    for old, new in runs.items():
        for r in _rows(src, f"SELECT * FROM {table} WHERE run_id = ?", (old,)):
            row = dict(r, run_id=new)
            dst.execute(
                f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                [row[c] for c in cols],
            )
            n += 1
    return n


def _target(name: str) -> str:
    return os.path.join("/data", name)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Copy another server's backtests into this desk.")
    p.add_argument("--sma", required=True, help="the source sma_terminal.db (e.g. /data/bt_sma.db)")
    p.add_argument("--pnl", help="the source open_pnl.db")
    p.add_argument("--trend", help="the source trend_reads.db")
    p.add_argument("--into", default=_target("sma_terminal.db"), help="this desk's sma_terminal.db")
    p.add_argument("--into-pnl", default=_target("open_pnl.db"))
    p.add_argument("--into-trend", default=_target("trend_reads.db"))
    p.add_argument("--write", action="store_true", help="copy (without it, only count what would be copied)")
    a = p.parse_args(argv)
    for path in (a.sma, a.into):
        if not os.path.isfile(path):
            print(f"Not found: {path}")
            return 1
    if os.path.abspath(a.sma) == os.path.abspath(a.into):
        print("The source and the target are the same file.")
        return 1
    if a.write:
        _backup(a.into)
    src = sqlite3.connect(f"file:{a.sma}?mode=ro", uri=True)
    dst = sqlite3.connect(a.into, timeout=30)
    try:
        runs, counts = merge_sma(src, dst)
        if a.write:
            dst.commit()
        else:
            dst.rollback()
    finally:
        src.close()
        dst.close()
    print(f"Runs to copy: {counts['runs']} (already here: {counts['skipped']}); trades {counts['trades']}, "
          f"reviews {counts['reviews']}, hand-offs {counts['handoffs']}")
    for flag, target, table, label in ((a.pnl, a.into_pnl, "replay_pnl", "P&L log points"),
                                       (a.trend, a.into_trend, "trend_reads", "trend readings")):
        if not flag:
            continue
        if not os.path.isfile(flag):
            print(f"Not found: {flag} ({label} skipped)")
            continue
        if a.write:
            _backup(target)
        s = sqlite3.connect(f"file:{flag}?mode=ro", uri=True)
        d = sqlite3.connect(target, timeout=30)
        try:
            if not _has(d, table):
                d.executescript(";".join(r[0] for r in s.execute(
                    "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL AND tbl_name = ?", (table,))))
            n = merge_keyed(s, d, table, runs if a.write else {k: k for k in runs})
            if a.write:
                d.commit()
            else:
                d.rollback()
        finally:
            s.close()
            d.close()
        print(f"{label}: {n}")
    print("Copied." if a.write else "Nothing written: add --write to copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
