"""Compare backtests trade by trade (backtest_detail.py): wins and losers, the same trade in two runs, takeaways."""
from __future__ import annotations

import datetime as dt

import pytest

import backtest_detail as bd

DAY = "2026-11-16"


def tr(run, symbol, side, entry, exit_, pnl, exit_reason="COLOUR_TURN", entry_reason="CANDLE_COLOUR", buy=None):
    h, m = map(int, entry.split(":"))
    eh, em = map(int, exit_.split(":"))
    base = dt.datetime(2026, 11, 16, tzinfo=bd.IST)
    a = (base + dt.timedelta(hours=h, minutes=m)).timestamp()
    b = (base + dt.timedelta(hours=eh, minutes=em)).timestamp()
    return {"id": 0, "run_id": run, "date": DAY, "symbol": symbol, "side": side, "qty": 10, "entry_at": entry + ":00",
            "exit_at": exit_ + ":00", "entry_ts": a, "exit_ts": b, "entry_price": 100.0, "exit_price": 101.0,
            "gross": float(pnl), "net": float(pnl) - 5, "entry_reason": entry_reason, "exit_reason": exit_reason,
            "hold_min": (b - a) / 60, "best_pts": None, "worst_pts": None, "trend": None, "buy_pct": buy,
            "rvol": None, "value_cr": None}


def test_stats_count_wins_losers_stocks_and_time_in_the_trade():
    rows = [tr(1, "TCS", "LONG", "09:45", "10:15", 300), tr(1, "TCS", "SHORT", "10:30", "11:30", -100),
            tr(1, "INFY", "LONG", "09:50", "10:00", -50), tr(1, "SBIN", "SHORT", "11:00", "11:20", 0)]
    s = bd.stats(rows)
    assert (s["trades"], s["wins"], s["losses"], s["flat"], s["win_rate"]) == (4, 1, 2, 1, 25.0)
    assert (s["stocks"], s["stocks_won"], s["stocks_lost"]) == (3, 1, 1)
    assert s["avg_hold"] == 30.0 and s["avg_hold_win"] == 30.0 and s["avg_hold_loss"] == 35.0
    assert (s["longs"], s["long_pnl"], s["shorts"], s["short_pnl"]) == (2, 250.0, 2, -100.0)
    assert s["best_trade"]["symbol"] == "TCS" and s["worst_trade"]["pnl"] == -100.0
    assert s["by_exit"][0] == {"name": "COLOUR_TURN", "trades": 4, "wins": 1, "losses": 2, "pnl": 150.0, "avg_hold": 30.0}
    assert bd.stats([])["win_rate"] is None


def test_the_same_trade_is_matched_within_the_tolerance_and_the_rest_told_apart():
    a = [tr(1, "TCS", "LONG", "09:45", "10:15", 300),               # same trade, same exit
         tr(1, "INFY", "LONG", "09:50", "10:30", -50),              # same trade, other exit
         tr(1, "SBIN", "LONG", "11:00", "11:20", 80),               # the other run sold here
         tr(1, "ITC", "SHORT", "12:00", "12:30", 40)]               # only run 1
    b = [tr(2, "TCS", "LONG", "09:47", "10:16", 310),
         tr(2, "INFY", "LONG", "09:50", "09:58", -10, exit_reason="TSL_HIT"),
         tr(2, "SBIN", "SHORT", "11:01", "11:25", -70),
         tr(2, "WIPRO", "LONG", "13:00", "13:40", 500),             # only run 2
         tr(2, "TCS", "LONG", "10:20", "10:40", 20)]                # too far from 09:45: only run 2
    o = bd.overlap(a, b)
    assert (o["same"], o["opposite"], o["only_a"], o["only_b"], o["union"]) == (2, 1, 1, 2, 6)
    assert o["rate"] == pytest.approx(33.3)
    assert (o["same_exit"], o["diff_exit_reason"], o["diff_exit_time"]) == (1, 1, 1)
    assert (o["same_pnl_a"], o["same_pnl_b"], o["only_a_pnl"], o["only_b_pnl"]) == (250.0, 300.0, 40.0, 520.0)
    assert o["stocks_only_a"] == ["ITC"] and o["stocks_only_b"] == ["WIPRO"] and o["stocks_both"] == 3
    assert o["apart"][0]["a"]["symbol"] == "INFY" and o["apart"][0]["diff"] == 40.0
    assert o["opposite_rows"][0]["b"]["side"] == "SHORT"
    assert [r["symbol"] for r in o["only_b_rows"]] == ["WIPRO", "TCS"]   # biggest first


def test_side_share_and_takeaways_read_the_numbers():
    assert bd.side_pct({"side": "SHORT", "buy_pct": 29.0}) == 71.0
    assert bd.side_pct({"side": "LONG", "buy_pct": None}) is None
    a = [tr(1, "TCS", "LONG", "09:45", "10:15", 300), tr(1, "INFY", "LONG", "09:50", "10:30", -50)]
    b = [tr(2, "TCS", "LONG", "09:45", "10:15", 300), tr(2, "WIPRO", "LONG", "13:00", "13:40", 900)]
    per_run = {1: bd.stats(a), 2: bd.stats(b)}
    lines = bd.takeaways([1, 2], 1, per_run, {2: bd.overlap(a, b)}, {1: a, 2: b}, "gross")
    text = " | ".join(f"{t['title']}: {t['text']}" for t in lines)
    assert "Run #2 made ₹1,200 from 2 trades: 2 winners, 0 losers" in text
    assert "33.3% the same trades" in text and "which trades were taken" in text
    assert "Run #1 only: INFY" in text and "Run #2 only: WIPRO" in text
    assert all(t["tone"] in ("good", "bad", "info") for t in lines)


def test_detail_and_stock_day_read_the_trade_book(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/d.db")
    monkeypatch.setenv("OPEN_PNL_FILE", f"{tmp_path}/pnl.db")
    monkeypatch.setenv("SCAN_HISTORY_FILE", f"{tmp_path}/scans.db")
    import database
    from models import TradeLog

    database.reset_engine()
    database.init_db()
    at = dt.datetime(2026, 11, 16, 9, 45)
    with database.session_factory()() as db:
        for run, pnl, mins, reason in ((7, 250.0, 30, "COLOUR_TURN"), (8, -90.0, 12, "TSL_HIT"), (8, 0.0, 5, "REPLAY_STOPPED")):
            db.add(TradeLog(date=DAY, symbol="TCS", direction="LONG", qty=10, entry_time=at, entry_price=100.0,
                            ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=99.0,
                            exit_time=at + dt.timedelta(minutes=mins), exit_price=100.0 + pnl / 10, exit_reason=reason,
                            gross_pnl=pnl, brokerage_and_taxes=5.0, net_pnl=pnl - 5, mode="REPLAY", run_id=run,
                            entry_reason="CANDLE_COLOUR"))
        db.commit()
    out = bd.detail([7, 8], base=7)
    assert out["base"] == 7 and out["trades_total"] == 2          # the REPLAY_STOPPED row is left out
    assert out["stats"][7]["wins"] == 1 and out["stats"][8]["losses"] == 1
    assert out["overlap"][8]["same"] == 1 and out["overlap"][8]["diff_exit_reason"] == 1
    assert out["trades"][0]["entry_at"] == "09:45:00" and out["trades"][0]["hold_min"] in (30.0, 12.0)
    day = bd.stock_day([7, 8], "tcs", DAY)
    assert [len(r["trades"]) for r in day["runs"]] == [1, 1] and day["symbol"] == "TCS"
    with pytest.raises(ValueError):
        bd.detail([7])
    database.reset_engine()


def test_reads_are_fetched_once_per_stock_day_and_the_latest_before_each_entry_is_taken(monkeypatch):
    import pressure_exit

    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    day1 = dt.datetime(2026, 11, 2, 0, 0, tzinfo=ist).timestamp()
    day2 = dt.datetime(2026, 11, 3, 0, 0, tzinfo=ist).timestamp()
    calls: list[tuple[str, float]] = []

    def series(symbol, start, end):
        calls.append((symbol, start))
        base = start + 10 * 3600  # 10:00
        return [{"time": base, "buy_pct": 60.0, "rvol": 1.1, "value_cr": 5.0},
                {"time": base + 1800, "buy_pct": 70.0, "rvol": 1.5, "value_cr": 9.0}]

    monkeypatch.setattr(pressure_exit, "series", series)
    # Two runs, each going through both days in its own order (the order that used to unpack every day per run).
    trades = [
        {"symbol": "TCS", "entry_ts": day1 + 10 * 3600 + 600},   # 10:10: the 10:00 read
        {"symbol": "TCS", "entry_ts": day2 + 10 * 3600 + 2400},  # 10:40: the 10:30 read
        {"symbol": "TCS", "entry_ts": day1 + 9.5 * 3600},        # 09:30: no read yet
        {"symbol": "TCS", "entry_ts": day2 + 10 * 3600 + 1800},  # 10:30 exactly: that read
    ]
    bd.add_reads(trades)
    assert sorted(calls) == [("TCS", day1), ("TCS", day2)]  # once per stock and day
    assert trades[0]["buy_pct"] == 60.0 and trades[0]["read_at"] == "10:00"
    assert trades[1]["buy_pct"] == 70.0 and trades[1]["rvol"] == 1.5 and trades[1]["read_at"] == "10:30"
    assert trades[2].get("buy_pct") is None
    assert trades[3]["buy_pct"] == 70.0
