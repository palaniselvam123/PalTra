"""Compare backtest runs (backtest_compare.py, GET /api/replay/compare): fair ground, ranking, verdict, stocks, days,
settings against the base. Read-only."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import backtest_compare as bc

D = ["2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]


def run(rid, days=D, symbols=("TCS", "INFY"), settings=None, done=None, status="FINISHED"):
    days = list(days)
    return {
        "id": rid, "status": status, "start_date": days[0] if days else "", "end_date": days[-1] if days else "",
        "created_at": None, "days_total": len(days), "days_done": len(days) if done is None else done, "days": days,
        "symbols": list(symbols), "settings": settings or {},
    }


def tr(rid, date, symbol, gross, reason="MA_CROSS", charges=10.0):
    return {"run_id": rid, "date": date, "symbol": symbol, "gross": float(gross), "net": float(gross) - charges,
            "charges": charges, "exit_reason": reason, "entry_time": None}


def daily(rid, per_day, days=D, symbols=("TCS", "INFY"), trades_each=1):
    """`trades_each` trades per stock per day, each `per_day[i] / (stocks × trades_each)`."""
    out = []
    for d, total in zip(days, per_day):
        share = total / (len(symbols) * trades_each)
        for s in symbols:
            out += [tr(rid, d, s, share) for _ in range(trades_each)]
    return out


def by_run(result, rid):
    return next(r for r in result["overall"] if r["run_id"] == rid)


def test_the_steady_winner_ranks_first_and_beats_the_base_day_by_day():
    trades = (
        daily(1, [100, -50, 80, -40, 60], trades_each=2)      # base: +150, 3 green days
        + daily(2, [200, 50, 120, 30, 90], trades_each=2)     # best: +490, won every day
        + daily(3, [-100, -80, 20, -60, -10], trades_each=2)  # loser: -230
    )
    out = bc.analyze([run(1), run(2), run(3)], trades, base=1)
    assert [r["run_id"] for r in out["overall"]] == [2, 1, 3]
    assert [r["rank"] for r in out["overall"]] == [1, 2, 3]
    best = by_run(out, 2)
    assert best["metrics"]["pnl"] == 490 and best["metrics"]["green_days"] == 5 and best["days_won"] == 5
    assert best["vs_base"]["pnl_diff"] == 340 and best["vs_base"]["head_to_head"] == {"won": 5, "lost": 0, "tied": 0, "days": 5}
    v = out["verdict"]
    assert (v["best"], v["runner_up"], v["confidence"]) == (2, 1, "clear")
    assert v["most_pnl"] == 2 and v["steadiest"] == 2 and v["lead"] == 340
    assert "all_lost" not in v["warnings"]
    # The running totals for the chart, one point per common day.
    assert out["curve"]["days"] == D and out["curve"]["runs"][2][-1] == 490


def test_only_the_common_ground_counts_and_a_quiet_day_is_zero():
    a = run(1, days=D[:4])            # Mon-Thu
    b = run(2, days=D[2:])            # Wed-Fri
    trades = [tr(1, D[0], "TCS", 1000), tr(1, D[2], "TCS", 50), tr(2, D[2], "TCS", 80), tr(2, D[4], "TCS", 500)]
    out = bc.analyze([a, b], trades)
    g = out["ground"]
    assert g["days"] == D[2:4] and g["days_filtered"]
    assert {"code": "different_days", "days": 2} in g["notes"]
    # Run 1's ₹1,000 Monday and run 2's ₹500 Friday are outside the common days: 50 vs 80 on Wed, 0 vs 0 on Thu.
    assert by_run(out, 1)["metrics"]["pnl"] == 50 and by_run(out, 2)["metrics"]["pnl"] == 80
    assert by_run(out, 1)["metrics"]["days"] == 2 and by_run(out, 1)["metrics"]["green_days"] == 1
    # Everything each run finished is still shown on its own.
    assert by_run(out, 1)["metrics_all"]["pnl"] == 1050 and by_run(out, 2)["metrics_all"]["pnl"] == 580
    assert [r["date"] for r in out["by_day"]["rows"]] == D[2:4]


def test_unfinished_days_and_trades_closed_by_stop_are_left_out():
    cut = run(1, done=2, status="INTERRUPTED")
    trades = [
        tr(1, D[0], "TCS", 100), tr(1, D[1], "TCS", 100),
        tr(1, D[2], "TCS", 999),                       # the day it was cut on: not finished
        tr(1, D[1], "INFY", 0, reason=bc.STOPPED_REASON),
        tr(2, D[0], "TCS", 10), tr(2, D[1], "TCS", 10), tr(2, D[2], "TCS", 10),
    ]
    out = bc.analyze([cut, run(2)], trades)
    assert out["ground"]["days"] == D[:2]
    assert by_run(out, 1)["metrics"]["pnl"] == 200 and by_run(out, 1)["metrics"]["trades"] == 2
    assert by_run(out, 1)["metrics_all"]["pnl"] == 200


def test_stocks_say_which_strategy_suits_each_one():
    trades = (
        [tr(1, d, "TCS", 100) for d in D] + [tr(1, d, "INFY", -60) for d in D] + [tr(1, d, "SBIN", 20) for d in D]
        + [tr(2, d, "TCS", 40) for d in D] + [tr(2, d, "INFY", 30) for d in D] + [tr(2, d, "SBIN", 25) for d in D]
    )
    syms = ("TCS", "INFY", "SBIN")
    out = bc.analyze([run(1, symbols=syms), run(2, symbols=syms)], trades)
    rows = {r["symbol"]: r for r in out["by_stock"]["rows"]}
    assert rows["TCS"]["verdict"] == "ALL_WIN" and rows["TCS"]["best"] == 1
    assert rows["INFY"]["verdict"] == "DEPENDS" and rows["INFY"]["best"] == 2 and rows["INFY"]["spread"] == 450
    assert rows["SBIN"]["best"] == 2
    # Run 2 is the better strategy (more money, no losing stock-day), but TCS does better on run 1's settings.
    assert out["verdict"]["best"] == 2
    summary = out["by_stock"]
    # Each stock on its own best run: TCS 500 (run 1) + INFY 150 + SBIN 125 (run 2) = 775 vs run 2's 475.
    assert summary["each_best_total"] == 775 and summary["best_run_total"] == 475 and summary["uplift"] == 300
    assert summary["suggestions"] == [{"symbol": "TCS", "run_id": 1, "pnl": 500.0, "best_run_pnl": 200.0, "gain": 300.0, "trades": 5}]
    assert out["verdict"]["good_on_all"] == ["SBIN", "TCS"]


def test_a_run_that_never_traded_is_not_the_safest():
    trades = daily(1, [100, -20, 50, -10, 40], trades_each=3)
    out = bc.analyze([run(1), run(2)], trades)
    idle = by_run(out, 2)
    assert idle["metrics"]["trades"] == 0 and idle["metrics"]["max_drawdown"] == 0
    assert idle["places"]["max_drawdown"] == 2 and idle["places"]["avg_trade"] == 2
    assert out["verdict"]["best"] == 1 and out["verdict"]["smallest_drop"] == 1


def test_profit_leaning_on_one_stock_or_day_is_flagged():
    trades = [tr(1, D[0], "TCS", 5000)] + [tr(1, d, "INFY", 10) for d in D] + [tr(1, D[1], "SBIN", 10)]
    trades += [tr(2, d, "INFY", 5) for d in D]
    out = bc.analyze([run(1, symbols=("TCS", "INFY", "SBIN")), run(2, symbols=("TCS", "INFY", "SBIN"))], trades)
    v = out["verdict"]
    assert v["best"] == 1
    assert v["lean"]["stock"]["symbol"] == "TCS" and v["lean"]["day"]["date"] == D[0]
    assert {"leans_on_stock", "leans_on_day"} <= set(v["warnings"])


def test_everything_lost_and_too_few_days():
    trades = [tr(1, D[0], "TCS", -50), tr(2, D[0], "TCS", -20)]
    out = bc.analyze([run(1, days=D[:1]), run(2, days=D[:1])], trades)
    v = out["verdict"]
    assert v["best"] == 2 and v["confidence"] == "too_few"
    assert {"all_lost", "few_days", "few_trades"} <= set(v["warnings"])


def test_net_basis_takes_the_charges_off():
    trades = [tr(1, D[0], "TCS", 100), tr(2, D[0], "TCS", 100), tr(2, D[0], "TCS", 0)]
    gross = bc.analyze([run(1, days=D[:1]), run(2, days=D[:1])], trades)
    net = bc.analyze([run(1, days=D[:1]), run(2, days=D[:1])], trades, basis="net")
    assert by_run(gross, 2)["metrics"]["pnl"] == 100 and by_run(net, 2)["metrics"]["pnl"] == 80
    assert net["basis"] == "net"


def test_settings_that_differ_against_the_base_and_their_effect():
    base = run(1, settings={"stop_type": "ATR", "atr_multiplier": 1.5, "use_vwap": False, "bot": 1})
    smart = run(2, settings={"stop_type": "SMART", "atr_multiplier": 1.5, "use_vwap": False, "bot": 1, "max_trades_per_stock": 0})
    smart_vwap = run(3, settings={"stop_type": "smart", "atr_multiplier": 1.50, "use_vwap": True, "bot": 1,
                                  "stock_settings": {"TCS": {"qty": 5}}})
    trades = daily(1, [10] * 5) + daily(2, [30] * 5) + daily(3, [50] * 5)
    out = bc.analyze([base, smart, smart_vwap], trades, base=1)
    diff = out["differences"]
    keys = {f["key"] for f in diff["fields"]}
    # "smart" and "SMART" are the same choice, 1.5 and 1.50 the same number, a missing max_trades_per_stock its default 0.
    assert keys == {"stop_type", "use_vwap"}
    vs = {v["run_id"]: v for v in diff["vs_base"]}
    assert vs[2]["changed"] == ["stop_type"] and vs[2]["pnl_diff"] == 100
    assert vs[3]["changed"] == ["stop_type", "use_vwap"] and vs[3]["changed_stocks"] == ["TCS"]
    assert diff["stock_fields"] == [{"symbol": "TCS", "key": "qty", "values": {1: None, 2: None, 3: 5}}]
    effect = {e["key"]: e for e in diff["effects"]}
    assert effect["use_vwap"]["groups"][0] == {"value": True, "runs": [3], "avg_pnl": 250.0}
    assert effect["stop_type"]["groups"][0]["runs"] == [2, 3]


def test_a_setting_only_counts_where_it_is_in_use():
    atr = run(1, settings={"stop_type": "ATR", "tsl_sl_pct": 1.0, "rsi_long_min": 40, "use_rsi": False})
    smart = run(2, settings={"stop_type": "SMART", "tsl_sl_pct": 0.8, "rsi_long_min": 55, "use_rsi": False})
    tsl = run(3, settings={"stop_type": "TSL", "tsl_sl_pct": 0.6, "rsi_long_min": 45, "use_rsi": False})
    trades = daily(1, [10] * 5) + daily(2, [20] * 5) + daily(3, [30] * 5)
    out = bc.analyze([atr, smart, tsl], trades, base=1)
    fields = {f["key"]: f for f in out["differences"]["fields"]}
    # RSI numbers differ but no run has the RSI check on: not a difference. The trailing distance only does
    # something in run 3, so the others read "not used".
    assert "rsi_long_min" not in fields
    assert fields["tsl_sl_pct"]["unused"] == [1, 2]
    vs = {v["run_id"]: v["changed"] for v in out["differences"]["vs_base"]}
    assert vs[2] == ["stop_type"]  # 0.8 vs 1.0 trailing distance: neither run trails
    assert vs[3] == ["stop_type", "tsl_sl_pct"]
    effect = {e["key"]: e for e in out["differences"]["effects"]}
    assert "tsl_sl_pct" not in effect  # one run uses it: nothing to group
    assert bc.in_use("tsl_sl_pct", {"stop_type": "TSL", "use_stop": False}) is False
    assert bc.in_use("slope_lookback", {}) is True  # shared by several rules: always shown


def test_bad_requests():
    with pytest.raises(ValueError, match="2 to 8"):
        bc.analyze([run(1)], [])
    with pytest.raises(ValueError, match="2 to 8"):
        bc.compare([5])


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/cmp.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    yield database
    database.reset_engine()


def _store(database, days, per_day, settings):
    from models import ReplayRun, TradeLog

    with database.session_factory()() as s:
        r = ReplayRun(created_at=dt.datetime(2026, 10, 10, 16, 0), start_date=days[0], end_date=days[-1], start_time="09:15",
                      symbols="TCS", settings=json.dumps(settings), status="FINISHED", days_total=len(days),
                      days_done=len(days), days=",".join(days))
        s.add(r)
        s.commit()
        for d, pnl in zip(days, per_day):
            t = dt.datetime.fromisoformat(f"{d}T10:00:00")
            s.add(TradeLog(date=d, symbol="TCS", direction="LONG", qty=1, entry_time=t, entry_price=100.0, ma_cross_price=100.0,
                           atr_at_entry=1.0, sl_trigger_price=99.0, exit_time=t + dt.timedelta(minutes=30),
                           exit_price=100.0 + pnl, exit_reason="MA_CROSS", gross_pnl=pnl, brokerage_and_taxes=1.0,
                           net_pnl=pnl - 1.0, mode="REPLAY", bot=1, run_id=r.id))
        s.commit()
        return r.id


def test_compare_reads_the_saved_runs_and_the_api_serves_it(db):
    a = _store(db, D, [10, 20, -5, 15, 5], {"stop_type": "ATR", "bot": 1})
    b = _store(db, D, [30, 25, 10, -5, 20], {"stop_type": "SMART", "bot": 1})
    out = bc.compare([a, b, a])
    assert [r["id"] for r in out["runs"]] == [a, b] and out["base"] == a
    assert out["verdict"]["best"] == b and by_run(out, b)["metrics"]["pnl"] == 80
    from fastapi.testclient import TestClient
    from main import app

    client = TestClient(app)
    got = client.get(f"/api/replay/compare?runs={a},{b}&base={b}&basis=net")
    assert got.status_code == 200
    body = got.json()
    assert body["base"] == b and body["basis"] == "net" and body["verdict"]["best"] == b
    assert client.get(f"/api/replay/compare?runs={a}").status_code == 400
    assert client.get(f"/api/replay/compare?runs={a},999").status_code == 400
    assert client.get("/api/replay/compare?runs=x,y").status_code == 400
