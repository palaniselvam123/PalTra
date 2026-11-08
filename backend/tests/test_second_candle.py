"""Second candle test (second_candle.py): the Nth candle's call, what the price did after it, the sweeps, the runner."""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import pandas as pd
import pytest

import second_candle as sc
from tests.test_gap_mode import IST, _day

D0, D1, D2 = dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)
FLAT = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731


def _rise(i, p):
    """Second 5-minute candle green, a ₹5 dip, then +₹20, then a slide into the close."""
    if i < 5:
        return 1000.0 + (0.5 if i % 2 else -0.5)
    if i < 20:
        return p + 0.3
    if i < 40:
        return p - 0.25
    if i < 120:
        return p + 0.25
    return p - 0.1


def _trap(i, p):
    """Second candle green, then straight down ₹12 before any rise: the stop goes first."""
    if i < 5:
        return 1000.0 + (0.5 if i % 2 else -0.5)
    if i < 10:
        return p + 0.4
    if i < 70:
        return p - 0.2
    return p + 0.3


def _fall(i, p):
    """Second candle red, then down ₹15: a short that reaches the target."""
    if i < 5:
        return 1000.0 + (0.5 if i % 2 else -0.5)
    if i < 10:
        return p - 0.4
    if i < 80:
        return p - 0.25
    return p + 0.05


def _weak(i, p):
    """Second candle green, then a slow slide of ₹10 and a flat afternoon: never the target, never a big win."""
    if i < 5:
        return 1000.0 + (0.5 if i % 2 else -0.5)
    if i < 10:
        return p + 0.4
    if i < 210:
        return p - 0.05
    return p + (0.02 if i % 2 else -0.02)


def _tape(day_path, volume_scale: int = 1) -> pd.DataFrame:
    rows = _day(D0, FLAT) + _day(D1, day_path)
    if volume_scale != 1:
        for r in rows:
            if dt.datetime.fromtimestamp(r["ts"], IST).date() == D1:
                r["volume"] *= volume_scale
    return pd.DataFrame(rows)


RULE = sc.Rule(minutes=5, candle_no=2, top_n=0, target_points=10.0)


def test_a_green_second_candle_buys_at_the_next_open_and_measures_the_path():
    row = sc.analyze_day("UP", _tape(_rise), D1, RULE)
    assert row["side"] == "LONG" and row["signal_at"] == "09:20" and row["entry_at"] == "09:25"
    assert row["hit"] is True and row["target_at"] is not None and row["minutes_to_target"] > 0
    assert row["best_pts"] > 15 and row["best_at"] > row["target_at"]
    assert 0 < row["against_before_target"] < 3 < row["worst_pts"]  # the ₹5 dip came after the target
    assert row["close_pts"] < row["best_pts"] and row["close_at"] == "15:15"
    assert row["rvol"] == 1.0  # the same volume as the day before
    # the ₹ levels: reached with the trade in order, never reached ₹20 with it, ₹10 never against it
    fav, adv = row["fav_min"], row["adv_min"]
    assert fav["2.0"] <= fav["5.0"] <= fav["10.0"] <= fav["15.0"] and fav["20.0"] is None
    assert adv["10.0"] is None and adv["5.0"] is not None
    # a ₹7.5 trail (0.75%) rides the +18 and keeps most of it; a ₹2.5 one is cut in the first wobble; plain floats
    assert row["trail"]["0.75"] > 10 > row["trail"]["0.25"] and max(row["trail"].values()) > row["close_pts"]
    assert all(type(v) is float for v in row["trail"].values())


def test_a_red_second_candle_sells_and_a_doji_is_skipped():
    short = sc.analyze_day("DOWN", _tape(_fall), D1, RULE)
    assert short["side"] == "SHORT" and short["hit"] and short["best_pts"] > 10 and short["against_before_target"] < 2

    def doji(i, p):
        return 1000.0 if i < 10 else p + 0.1

    assert sc.analyze_day("DOJI", _tape(doji), D1, RULE)["skipped"] == "doji"
    small = sc.analyze_day("SMALL", _tape(_rise), D1, sc.Rule(min_body_pct=1.0))
    assert small["skipped"] == "small body"
    assert sc.analyze_day("NONE", _tape(_rise), D2, RULE) is None  # no candles that day


def test_target_and_stop_are_judged_from_the_level_minutes():
    up = sc.analyze_day("UP", _tape(_rise), D1, RULE)
    trap = sc.analyze_day("TRAP", _tape(_trap), D1, RULE)
    assert sc.target_stop_outcome(up, 10, 5) == ("target", 10.0)
    assert sc.target_stop_outcome(trap, 10, 5) == ("stop", -5.0)
    assert sc.target_stop_outcome(up, 30, 50)[0] == "close"  # neither level reached: the square-off decides
    table = sc.target_stop_table([up, trap], 10, (5.0, 15.0))
    assert table[0]["hit_target"] == 1 and table[0]["hit_stop"] == 1 and table[0]["total"] == 5.0
    assert table[1]["stop"] == 15.0 and table[1]["hit_stop"] == 0 and table[1]["hit_target"] == 2  # the trap turns up before ₹15 against


def test_days_are_ranked_by_volume_vs_usual_and_the_top_n_summarized():
    rows = sc.analyze_stock("BUSY", _tape(_rise, 3), [D1], RULE) + sc.analyze_stock("QUIET", _tape(_weak), [D1], RULE)
    sc.rank_days(rows)
    by = {r["symbol"]: r for r in rows}
    assert by["BUSY"]["rank"] == 1 and by["QUIET"]["rank"] == 2 and by["BUSY"]["candidates"] == 2
    rep = sc.summarize(rows, RULE)
    assert rep["trades"] == 2 and rep["all"]["hits"] == 1 and rep["all"]["hit_pct"] == 50.0
    assert rep["by_rank"][0]["label"] == "Top 5" and rep["by_rank"][0]["trades"] == 2
    assert rep["trail"] and rep["best_trail"] and rep["target_stop"] and rep["best_stop"]
    assert any(h["target"] for h in rep["by_half_hour"]) and rep["answer"]
    one = sc.summarize(rows, sc.Rule(top_n=1))
    assert one["trades"] == 1 and one["stocks"] == 1
    stocks = sc.by_stock(rows, RULE)
    assert stocks[0]["symbol"] == "BUSY" and stocks[0]["hit_pct"] == 100.0 and stocks[1]["hit_pct"] == 0.0


def test_rule_clean_keeps_values_inside_their_bounds():
    r = sc.Rule(minutes=7, candle_no=0, top_n=-1, target_points=0, trails=(0.01, 0.5, 0.5, 3.0)).clean()
    assert (r.minutes, r.candle_no, r.top_n, r.target_points, r.trails) == (5, 1, 0, 0.05, (0.5, 3.0))


@pytest.mark.asyncio
async def test_the_runner_reads_each_stock_ranks_the_days_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_DIR", str(tmp_path / "lab"))
    monkeypatch.setenv("SECOND_CANDLE_FILE", str(tmp_path / "second.json"))
    monkeypatch.setattr(sc, "_last_trading_days", lambda today, count: [D1])
    tapes = {"UP": _tape(_rise, 3), "WEAK": _tape(_weak)}

    async def fetch(symbol, start, end):
        if symbol == "BAD":
            raise RuntimeError("no candles")
        return tapes[symbol]

    t = sc.SecondCandle()
    view = t.start(["UP", "WEAK", "BAD"], fetch, 1, RULE)
    assert view["status"] == "RUNNING" and view["total"] == 3
    for _ in range(200):
        if not t.running:
            break
        await asyncio.sleep(0.05)
    assert t.status == "DONE" and t.done == 3 and t.errors[0]["symbol"] == "BAD"
    assert t.report["trades"] == 2 and len(t.rows) == 2 and t.stocks[0]["symbol"] == "UP"
    again = sc.SecondCandle()
    assert again.status == "DONE" and again.report["trades"] == 2 and len(again.rows) == 2
    with pytest.raises(ValueError):
        t.start([], fetch, 1, RULE)


def test_api_starts_reports_and_stops(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("STRATEGY_LAB_DIR", str(tmp_path / "lab"))
    monkeypatch.setenv("SECOND_CANDLE_FILE", str(tmp_path / "second.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(sc, "_TEST", None)
    monkeypatch.setattr(sc, "_last_trading_days", lambda today, count: [D1])
    monkeypatch.setattr(main, "market_is_open", lambda *a, **k: False)
    tape = _tape(_rise)

    async def fake_fetch(broker, name, start, end):
        return tape

    monkeypatch.setattr(main, "fetch_frame", fake_fetch)
    with TestClient(main.app) as client:
        assert client.get("/api/second-candle").json()["status"] == "IDLE"
        assert client.post("/api/second-candle/start", json={"symbols": ["TCS"], "square_off": "16:00"}).status_code == 400
        assert client.post("/api/second-candle/start", json={"symbols": ["TCS"], "minutes": 7}).status_code == 422
        assert client.post("/api/second-candle/start", json={"symbols": []}).status_code == 400
        ok = client.post("/api/second-candle/start", json={"symbols": ["TCS", "INFY"], "days": 1, "target_points": 10, "trails": [0.5, 1.0]})
        assert ok.status_code == 200 and ok.json()["total"] == 2
        for _ in range(200):
            view = client.get("/api/second-candle").json()
            if view["status"] != "RUNNING":
                break
            time.sleep(0.05)
        assert view["status"] == "DONE" and view["report"]["trades"] == 2 and len(view["rows"]) == 2
        assert view["params"]["trails"] == [0.5, 1.0] and client.get("/api/second-candle?rows=false").json()["rows"] == []
        assert client.post("/api/second-candle/stop").status_code == 200
    database.reset_engine()


def test_stop_and_reverse_follows_the_other_side_after_the_stop():
    trap = sc.analyze_day("TRAP", _tape(_trap), D1, RULE)
    up = sc.analyze_day("UP", _tape(_rise), D1, RULE)
    leg = trap["rev"]["5.0"]
    # stopped ₹5 down on the slide: the reverse sell runs about ₹7 more, then the rise stops it out
    assert leg[0] > 0 and 5 < leg[1] < 10 and leg[2] >= 5
    assert sc.reverse_outcome(trap, 10, 5) == ("stop", -5.0)
    assert sc.reverse_outcome(trap, 5, 5) == ("target", 5.0)  # a ₹5 target on the reverse is reached first
    assert sc.reverse_outcome(up, 10, 5) is None  # the target came first: no reverse
    table = {t["stop"]: t for t in sc.stop_reverse_table([up, trap], 5, (5.0,))}
    assert table[5.0]["stopped"] == 1 and table[5.0]["rev_target"] == 1
    assert table[5.0]["total"] == table[5.0]["plain_total"] + 5.0


def test_several_candles_in_one_run_are_ranked_and_summarized_apart():
    rule = sc.Rule(minutes=5, candle_nos=(2, 1), top_n=0, target_points=10.0).clean()
    assert rule.candle_nos == (1, 2) and rule.candle_no == 1
    rows = sc.analyze_stock("UP", _tape(_rise), [D1], rule) + sc.analyze_stock("DOWN", _tape(_fall), [D1], rule)
    sc.rank_days(rows)
    assert {r["candle_no"] for r in rows} == {1, 2}
    for no in (1, 2):
        ranks = sorted(r["rank"] for r in rows if r.get("candle_no") == no and not r.get("skipped"))
        assert ranks == list(range(1, len(ranks) + 1))  # each candle ranked on its own
    rep = sc.summarize(rows, rule)
    assert [g["candle_no"] for g in rep["by_candle"]] == [1, 2] and rep["rule"]["candle_nos"] == [1, 2]
    assert rep["stop_reverse"] and "reverse_stops" in rep


def test_majority_picks_the_side_most_opening_candles_went():
    # The DRREDDY day: a big green first candle, then a small red pause, then up all day.
    def gap_up(i, p):
        if i < 5:
            return 1000.0 + i * 3.0  # candle 1 green, +12
        if i < 10:
            return 1012.0 - (i - 4) * 0.2  # candle 2 small red
        if i < 15:
            return p + 0.3  # candle 3 green
        return p + 0.1

    tape = _tape(gap_up)
    nth = sc.analyze_day("GAP", tape, D1, RULE)
    assert nth["side"] == "SHORT"  # the Nth candle alone sells into the rise
    tie = sc.analyze_day("GAP", tape, D1, sc.Rule(candle_no=2, side_rule="MAJORITY"))
    assert tie["skipped"] == "no majority" and tie["greens"] == 1 and tie["reds"] == 1
    by_size = sc.analyze_day("GAP", tape, D1, sc.Rule(candle_no=2, side_rule="MAJORITY", majority_by="SIZE"))
    assert by_size["side"] == "LONG" and by_size["net_body"] > 0 and by_size["best_pts"] > 10
    three = sc.analyze_day("GAP", tape, D1, sc.Rule(candle_no=3, side_rule="MAJORITY"))
    assert three["side"] == "LONG" and (three["greens"], three["reds"]) == (2, 1) and three["entry_at"] == "09:30"
    agree = sc.analyze_day("GAP", tape, D1, sc.Rule(candle_no=2, side_rule="AGREE", majority_by="SIZE"))
    assert agree["skipped"] == "against the majority"
    rep = sc.summarize([tie, three], sc.Rule(candle_nos=(2, 3), side_rule="MAJORITY", top_n=0))
    assert rep["skipped_by"] == {"no majority": 1} and any("skipped 1 no majority" in a for a in rep["answer"])
    assert sc.Rule(side_rule="X", majority_by="Y").clean().side_rule == "NTH"
