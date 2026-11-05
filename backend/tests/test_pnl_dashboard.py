"""P&L dashboard (pnl_dashboard.py): read-only, from the minute-by-minute P&L log."""
from __future__ import annotations

import datetime as dt

import pytest

import pnl_dashboard as pd
from pnl_dashboard import GRID


def _day(key, points, stocks=None):
    sp = pd.span(points)
    curve = pd.day_curve(points)
    st = {k: pd.day_curve(v) for k, v in (stocks or {}).items()}
    return pd.build_day(key, key, curve, sp[0], sp[1], st)


def test_the_grid_is_every_session_minute():
    assert GRID[0] == "09:15" and GRID[-1] == "15:30" and len(GRID) == 376


def test_a_curve_carries_the_last_point_and_starts_at_zero():
    c = pd.day_curve([("2026-10-20T10:00+05:30", 100), ("2026-10-20T11:00+05:30", -50)])
    assert c[GRID.index("09:59")] == 0 and c[GRID.index("10:30")] == 100
    assert c[GRID.index("11:00")] == -50 and c[-1] == -50


def test_high_low_close_and_the_best_time_to_close_everything():
    # Both days peak at 11:00 and give it back by the close.
    a = _day("2026-10-20", [("10:00", 200), ("11:00", 500), ("13:00", 100), ("15:20", -100)])
    b = _day("2026-10-21", [("10:00", 100), ("11:00", 300), ("14:00", 0), ("15:20", 50)])
    out = pd.analyze([a, b], step=30)
    first = out["days"][0]
    assert (first["high"], first["high_at"], first["low"], first["close"]) == (500, "11:00", -100, -100)
    assert first["giveback"] == 600
    best = out["best_exit"]
    assert best["by_avg"] == "11:15"  # the 30-minute grid point straight after the 11:00 peak
    assert best["avg_at_best"] == 400 and best["avg_close"] == -25 and best["gain_vs_close"] == 425
    row = next(r for r in out["by_time"] if r["t"] == "11:15")
    assert row["beat_close_pct"] == 100 and row["sum"] == 800 and row["pos_pct"] == 100
    assert out["grid"][-1] == "15:30" and len(out["days"][0]["curve"]) == len(out["grid"])
    assert out["totals"]["days"] == 2 and out["totals"]["win_days"] == 1 and out["totals"]["loss_days"] == 1
    assert sum(h["days"] for h in out["high_when"]) == 2
    assert next(h for h in out["high_when"] if h["bucket"] == "10:45")["days"] == 2


def test_stocks_are_ranked_and_a_stock_filter_reads_only_them():
    a = _day("d1", [("10:00", 0), ("15:00", 300)], {"SBIN": [("10:00", 400), ("15:00", 200)], "TCS": [("12:00", 100)]})
    out = pd.analyze([a], step=15)
    assert [s["symbol"] for s in out["stocks"]] == ["SBIN", "TCS"]
    sbin = out["stocks"][0]
    assert sbin["avg_high"] == 400 and sbin["sum_close"] == 200 and sbin["best_exit"] is not None
    only = pd.analyze([a], step=15, stocks=["tcs"])
    assert only["days"][0]["close"] == 100 and only["totals"]["days"] == 1
    assert pd.analyze([a], stocks=["INFY"])["totals"] is None


def test_nothing_logged_gives_an_empty_dashboard():
    out = pd.analyze([], step=7)
    assert out["step"] == 5 and out["days"] == [] and out["best_exit"] is None and out["totals"] is None


def test_api_reads_replay_runs_and_market_days(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("OPEN_PNL_FILE", str(tmp_path / "open_pnl.db"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database
    import open_pnl_log

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    sample = lambda g: {"gross": g, "net": g - 10, "held": 1, "stocks": [{"symbol": "SBIN", "qty": 50, "gross": g, "net": g - 10}],
                        "closed_gross": 0.0, "closed_net": 0.0}
    open_pnl_log.save_replay(7, 1, [(dt.datetime(2026, 10, 20, 10, 0, tzinfo=ist), sample(100)),
                                    (dt.datetime(2026, 10, 20, 11, 0, tzinfo=ist), sample(400)),
                                    (dt.datetime(2026, 10, 20, 15, 0, tzinfo=ist), sample(-50))])
    open_pnl_log.save(dt.datetime(2026, 10, 21, 10, 0, tzinfo=ist), [("1", "PAPER", sample(80))])
    open_pnl_log.save(dt.datetime(2026, 10, 21, 10, 0, tzinfo=ist), [("2", "PAPER", sample(20))])
    with TestClient(main.app) as client:
        opts = client.get("/api/pnl-dashboard/options").json()
        assert opts["runs"][0]["run_id"] == 7 and opts["market_days"] == ["2026-10-21"]
        rep = client.get("/api/pnl-dashboard?source=replay&runs=7&step=30").json()
        assert rep["days"][0]["high"] == 400 and rep["days"][0]["close"] == -50
        assert rep["best_exit"]["by_avg"] == "11:15" and rep["all_stocks"] == ["SBIN"]
        assert client.get("/api/pnl-dashboard?source=replay").json()["runs"] == [7]  # the latest run by default
        net = client.get("/api/pnl-dashboard?source=replay&runs=7&basis=net").json()
        assert net["days"][0]["high"] == 390
        mkt = client.get("/api/pnl-dashboard?source=market&desks=1,2").json()
        assert mkt["days"][0]["close"] == 100 and mkt["stocks"][0]["symbol"] == "SBIN"
        assert client.get("/api/pnl-dashboard?source=market&desks=1&start=2026-10-22").json()["totals"] is None
        # Qty: every trade restated as N shares (the sample holds 50 SBIN)
        q = client.get("/api/pnl-dashboard?source=replay&runs=7&step=30&qty=100&basis=net").json()
        assert q["qty"] == 100 and q["basis"] == "gross"
        assert q["days"][0]["high"] == 800 and q["days"][0]["close"] == -100
    database.reset_engine()


def test_per_qty_restates_a_trade_as_n_shares():
    assert pd.per_qty(120.0, 40, 100) == 300.0
    assert pd.per_qty(-30.0, -10, 50) == -150.0  # a short's qty sign is ignored
    assert pd.per_qty(120.0, 40, 0) == 120.0 and pd.per_qty(120.0, None, 100) == 120.0
    pts = [{"t": "x", "stocks": [{"symbol": "A", "qty": 10, "gross": 5.0, "net": 4.0}]}]
    out = pd.scale_points(pts, 20)
    assert out[0]["stocks"][0]["gross"] == 10.0 and out[0]["stocks"][0]["net"] == 8.0
    assert pts[0]["stocks"][0]["gross"] == 5.0  # the input is untouched


def test_a_day_without_any_pnl_is_left_out():
    flat = _day("d0", [("10:00", 0), ("15:00", 0)])
    real = _day("d1", [("10:00", 50)])
    out = pd.analyze([flat, real])
    assert [d["key"] for d in out["days"]] == ["d1"] and out["totals"]["days"] == 1
