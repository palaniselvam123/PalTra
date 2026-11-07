"""Strategy lab (strategy_lab.py): base trades with every rule's answer, the combiner, the cache and the API."""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import pandas as pd
import pytest

import strategy_lab as lab
from models import BotConfig
from tests.test_gap_mode import IST, _day

D0, D1, D2 = dt.date(2026, 11, 2), dt.date(2026, 11, 3), dt.date(2026, 11, 4)


def _trend(i, p):
    if i < 60:
        return 1000.0 + (0.02 if i % 2 else -0.02)
    if i < 90:
        return p + 0.12
    if i < 150:
        return p + (0.02 if i % 2 else -0.02)
    if i < 250:
        return p - 0.12
    return p + 0.08


def _tape() -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731
    return pd.DataFrame(_day(D0, flat) + _day(D1, _trend) + _day(D2, _trend))


def _cfg(**over) -> BotConfig:
    return BotConfig(**{"id": 1, "sma_fast": 9, "sma_slow": 21, "qty": 100, "candle_minutes": 5, **over})


def _hhmm(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def test_crosses_become_base_trades_with_every_rule_answered():
    trades, settings = lab.analyze_stock("TEST", _tape(), _cfg(), [D1, D2])
    assert trades and settings["candle_minutes"] == 5 and settings["qty"] == 100
    assert {t["day"] for t in trades} == {D1.isoformat(), D2.isoformat()}  # the warm-up day is never traded
    first = trades[0]
    assert first["side"] == "LONG" and _hhmm(first["entry_ts"]) == "10:05"  # the rise from 10:15 crosses at 10:00
    assert set(first["checks"]) == set(lab.ENTRY_CHECKS)
    assert set(first["exits"]) == set(lab.EXIT_RULES)
    assert set(first["stops"]) == {"ATR", "TSL", "SUPERTREND", "VWAP"} and set(first["targets"]) == set(lab.TARGETS)
    # the opposite cross one candle later is the cross exit, at the open of the candle after it
    assert _hhmm(first["exits"]["cross"][0]) == "10:10"
    assert _hhmm(first["square"][0]) == "15:15"
    # every candidate lies inside the trade's day and before the square-off
    for t in trades:
        for hit in list(t["exits"].values()) + list(t["stops"].values()) + list(t["targets"].values()):
            if hit:
                assert t["entry_ts"] <= hit[0] < t["square"][0]
    # the entry window: nothing before 09:20 or at / after the cut-off
    for t in trades:
        assert "09:20" <= _hhmm(t["entry_ts"]) < settings["entry_cutoff"]


def test_the_bots_own_switches_become_the_starting_choice():
    cfg = _cfg(use_vwap=True, use_rsi=True, use_gap_long=True, st_exit=True, bb_exit="BOTH", counter_exit_count=3, use_stop=True, stop_type="TSL", tsl_target_points=5.0, pivot_target="R1")
    choice = lab.choice_from_cfg(cfg)
    assert choice["entries"] == ["vwap", "rsi", "gap"]
    assert set(choice["exits"]) == {"cross", "bb_band", "bb_middle", "supertrend", "counter"}
    assert choice["stop"] == "TSL" and set(choice["targets"]) == {"tsl", "pivot_r1"}
    assert lab.choice_from_cfg(_cfg(cross_exit=False, use_stop=False)) == {"entries": [], "exits": [], "stop": "NONE", "targets": []}
    fields = lab.settings_for_choice(choice, {"counter_count": 3, "slope_bend_pct": 60.0})
    assert fields["use_vwap"] and fields["use_rsi"] and fields["use_gap_long"] and fields["use_gap_short"] and not fields["use_macd"]
    assert fields["bb_exit"] == "BOTH" and fields["st_exit"] and fields["counter_exit_count"] == 3 and fields["use_stop"] and fields["stop_type"] == "TSL"
    assert fields["pivot_target"] == "R1" and fields["slope_exit"] is False


def _synthetic_trades() -> list[dict]:
    """Two trades on one stock: A at 10:00, its cross exit at 11:00 opening B; B runs to a cross at 12:00."""
    t = lambda hh, mm: int(dt.datetime.combine(D1, dt.time(hh, mm), tzinfo=IST).timestamp())  # noqa: E731
    checks_ok = {name: True for name in lab.ENTRY_CHECKS}
    none = {name: None for name in lab.EXIT_RULES}
    a = {
        "symbol": "X", "day": D1.isoformat(), "side": "LONG", "cross_ts": t(9, 55), "entry_ts": t(10, 0), "entry_price": 100.0,
        "atr": 1.0, "qty": 10, "checks": {**checks_ok, "rsi": False},
        "exits": {**none, "cross": [t(11, 0), 104.0], "supertrend": [t(10, 30), 102.0]},
        "stops": {"ATR": [t(10, 10), 99.0], "TSL": None, "SUPERTREND": None, "VWAP": None},
        "targets": {"tsl": [t(10, 10), 101.5], "pivot_r1": None, "pivot_r2": None},
        "square": [t(15, 15), 110.0], "best": 10.0, "worst": -1.0,
    }
    b = {
        **a, "side": "SHORT", "cross_ts": t(10, 55), "entry_ts": t(11, 0), "entry_price": 104.0, "checks": checks_ok,
        "exits": {**none, "cross": [t(12, 0), 103.0]}, "stops": {"ATR": None, "TSL": None, "SUPERTREND": None, "VWAP": None},
        "targets": {"tsl": None, "pivot_r1": None, "pivot_r2": None}, "square": [t(15, 15), 90.0],
    }
    return [a, b]


def test_the_combiner_applies_checks_exits_stops_and_the_in_position_rule():
    trades = _synthetic_trades()
    base = lab.evaluate(trades, {"entries": [], "exits": ["cross"], "stop": "NONE"})
    assert base["totals"]["trades"] == 2 and [k["reason"] for k in base["trades"]] == ["MA_CROSS", "MA_CROSS"]
    assert base["trades"][0]["gross"] == 40.0 and base["trades"][1]["gross"] == 10.0
    assert base["trades"][0]["net"] < 40.0  # charges taken off
    # the RSI check refuses A; B is kept
    got = lab.evaluate(trades, {"entries": ["rsi"], "exits": ["cross"], "stop": "NONE"})
    assert got["totals"]["trades"] == 1 and got["skipped"]["refused"] == 1 and got["trades"][0]["side"] == "SHORT"
    # cross exit off: A runs to the square-off and B starts while A is held, so B is skipped
    held = lab.evaluate(trades, {"entries": [], "exits": [], "stop": "NONE"})
    assert held["totals"]["trades"] == 1 and held["trades"][0]["reason"] == "EOD_SQUARE_OFF" and held["skipped"]["in_position"] == 1
    assert held["trades"][0]["gross"] == 100.0
    # the earliest of the chosen exits wins; a stop beats a target at the same minute
    st = lab.evaluate(trades, {"entries": [], "exits": ["cross", "supertrend"], "stop": "NONE"})
    assert st["trades"][0]["reason"] == "SUPERTREND" and st["trades"][0]["gross"] == 20.0
    stop = lab.evaluate(trades, {"entries": [], "exits": ["cross"], "stop": "ATR", "targets": ["tsl"]})
    assert stop["trades"][0]["reason"] == "ATR_SL_HIT" and stop["trades"][0]["gross"] == -10.0
    target = lab.evaluate(trades, {"entries": [], "exits": ["cross"], "stop": "NONE", "targets": ["tsl"]})
    assert target["trades"][0]["reason"] == "TARGET_HIT"
    # the time window and a quantity override
    late = lab.evaluate(trades, {"entries": [], "exits": ["cross"], "stop": "NONE", "entry_from": "10:30"})
    assert late["totals"]["trades"] == 1 and late["skipped"]["outside_window"] == 1
    big = lab.evaluate(trades, {"entries": [], "exits": ["cross"], "stop": "NONE", "qty": 100})
    assert big["trades"][0]["gross"] == 400.0
    # per-stock daily cap from the bot's settings
    capped = lab.evaluate(trades, {"entries": [], "exits": ["cross"], "stop": "NONE"}, {"max_trades_per_stock": 1})
    assert capped["totals"]["trades"] == 1 and capped["skipped"]["stock_cap"] == 1
    # unknown names are dropped, not an error
    assert lab.clean_choice({"entries": ["nope", "rsi"], "exits": ["x"], "stop": "weird"}) == {
        "entries": ["rsi"], "exits": [], "stop": "NONE", "targets": [], "entry_from": "09:20", "entry_to": "15:30", "qty": 0,
    }
    # the settings the replay needs for this choice ride along
    assert stop["settings"]["use_stop"] and stop["settings"]["stop_type"] == "ATR" and stop["settings"]["cross_exit"]


def test_recommend_lists_every_rule_and_climbs_to_a_better_choice(monkeypatch):
    monkeypatch.setattr(lab, "SEARCH_MIN_TRADES", 1)
    trades = _synthetic_trades()
    rec = lab.recommend(trades, {"entries": [], "exits": ["cross"], "stop": "NONE"})
    names = {(r["kind"], r["name"]) for r in rec["rows"]}
    assert ("entry", "rsi") in names and ("exit", "supertrend") in names and ("stop", "ATR") in names and ("target", "tsl") in names
    row = next(r for r in rec["rows"] if r["kind"] == "entry" and r["name"] == "rsi")
    assert row["on"] is False and row["trades_changed"] == 1 and row["label"] == lab.ENTRY_CHECKS["rsi"]
    assert next(r for r in rec["rows"] if r["kind"] == "exit" and r["name"] == "macd")["label"] == lab.EXIT_RULES["macd"]
    assert rec["rows"][0]["delta_net"] >= rec["rows"][-1]["delta_net"]
    # turning the cross exit off makes 100 on A: the greedy search finds it
    assert rec["best"]["totals"]["net"] > rec["now"]["net"] and rec["best"]["steps"]


@pytest.mark.asyncio
async def test_the_candle_cache_downloads_each_day_once(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_DIR", str(tmp_path))
    calls: list[tuple] = []
    tape = _tape()

    async def fetch(symbol, start, end):
        calls.append((symbol, start.date(), end.date()))
        return tape

    today = D2 + dt.timedelta(days=1)
    got = await lab.load_tape("TEST", D0, D2, fetch, today=today)
    assert len(got) == 3 * 375 and len(calls) == 1
    again = await lab.load_tape("TEST", D0, D2, fetch, today=today)
    assert len(again) == 3 * 375 and len(calls) == 1  # all three days came from the cache
    # a wider range downloads only the days it lacks
    await lab.load_tape("TEST", D0 - dt.timedelta(days=2), D2, fetch, today=today)
    assert calls[-1][1] == D0 - dt.timedelta(days=2) and calls[-1][2] == D0 - dt.timedelta(days=1)
    # today is never cached
    await lab.load_tape("TEST", D0, today, fetch, today=today)
    assert calls[-1][1] == today and not lab._cache_file("TEST", today).exists()


@pytest.mark.asyncio
async def test_a_run_reads_each_stock_and_keeps_its_trades(tmp_path, monkeypatch):
    monkeypatch.setenv("STRATEGY_LAB_DIR", str(tmp_path))
    tape = _tape()

    async def fetch(symbol, start, end):
        if symbol == "BAD":
            raise RuntimeError("no candles")
        return tape

    room = lab.StrategyLab()
    job = room.start(["TEST", "BAD"], D1, D2, 1, lambda symbol: _cfg(), fetch)
    assert job["status"] == "RUNNING" and job["total"] == 2
    for _ in range(200):
        if not room.running:
            break
        await asyncio.sleep(0.05)
    assert job["status"] == "DONE" and job["done"] == 2 and job["errors"][0]["symbol"] == "BAD"
    rows = room.trades_of(job["id"])
    assert rows and rows[0]["symbol"] == "TEST" and job["trades"] == len(rows)
    assert job["choice"] == lab.choice_from_cfg(_cfg()) and job["settings"]["qty"] == 100
    # a fresh object reads the saved jobs and trades back from disk
    again = lab.StrategyLab()
    assert again.job(job["id"])["status"] == "DONE" and len(again.trades_of(job["id"])) == len(rows)
    assert again.delete(job["id"]) and again.job(job["id"]) is None
    with pytest.raises(ValueError):
        room.start([], D1, D2, 1, lambda s: _cfg(), fetch)
    with pytest.raises(ValueError):
        room.start(["TEST"], D2, D1, 1, lambda s: _cfg(), fetch)


def test_api_starts_evaluates_recommends_and_deletes(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("STRATEGY_LAB_DIR", str(tmp_path / "lab"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    monkeypatch.setattr(lab, "_LAB", None)
    monkeypatch.setattr(main, "market_is_open", lambda *a, **k: False)
    tape = _tape()

    async def fake_fetch(broker, name, start, end):
        return tape

    monkeypatch.setattr(main, "fetch_frame", fake_fetch)
    with TestClient(main.app) as client:
        view = client.get("/api/strategy-lab").json()
        assert view["running"] is False and [r["name"] for r in view["rules"]["stops"]] == list(lab.STOPS)
        assert client.post("/api/strategy-lab/start", json={"symbols": ["TCS"], "date": "2026/11/03"}).status_code == 400
        assert client.post("/api/strategy-lab/start", json={"symbols": ["TCS"], "date": "2999-01-01"}).status_code == 400
        assert client.post("/api/strategy-lab/start", json={"symbols": [], "date": D1.isoformat()}).status_code == 400
        ok = client.post("/api/strategy-lab/start", json={"symbols": ["TCS", "INFY"], "date": D1.isoformat(), "end_date": D2.isoformat(), "bot": 1})
        assert ok.status_code == 200
        job_id = ok.json()["job"]["id"]
        for _ in range(200):
            view = client.get("/api/strategy-lab").json()
            if not view["running"]:
                break
            time.sleep(0.05)
        assert view["jobs"][0]["status"] == "DONE" and view["jobs"][0]["trades"] > 0
        detail = client.get(f"/api/strategy-lab/{job_id}").json()
        assert detail["result"]["totals"]["trades"] > 0 and detail["result"]["choice"]["exits"] == ["cross"]
        ev = client.post(f"/api/strategy-lab/{job_id}/evaluate", json={"entries": ["rsi"], "exits": ["cross", "supertrend"], "stop": "TSL"})
        assert ev.status_code == 200 and ev.json()["choice"]["stop"] == "TSL" and "per_stock" in ev.json()
        rec = client.post(f"/api/strategy-lab/{job_id}/recommend", json={"entries": [], "exits": ["cross"], "stop": "ATR"})
        assert rec.status_code == 200 and rec.json()["rows"] and "best" in rec.json()
        assert client.get("/api/strategy-lab/nope").status_code == 404
        assert client.delete(f"/api/strategy-lab/{job_id}").status_code == 200
        assert client.get(f"/api/strategy-lab/{job_id}").status_code == 404
    database.reset_engine()


@pytest.mark.asyncio
async def test_the_lab_matches_the_replay_engine_on_the_base_strategy(tmp_path, monkeypatch):
    """The lab's base trades (SMA cross in and out, ATR stop) land on the same minutes and sides as the bot
    replaying the same day; the fills differ only by the price walk inside the entry minute."""
    from tests.test_replay import DAY, _frames, _run_day
    from replay import ReplayEngine, ReplayFeed

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/replay.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda msg: None)
    import database

    database.reset_engine()
    database.init_db()
    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.symbol = "TCS"
        row.trade_symbols = "TCS"
        row.qty = 10
        s.commit()
    clock = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST)
    engine = ReplayEngine(ReplayFeed(_frames(), clock), ["TCS"])
    cfg = engine.load_config()
    await engine.tick(clock)
    engine.hold_for_next_cross(["TCS"])
    engine.status = "RUNNING"
    await _run_day(engine, dt.time(15, 20))
    from models import TradeLog

    with database.session_factory()() as s:
        rows = s.query(TradeLog).order_by(TradeLog.entry_time).all()
        bot = [(r.direction, r.entry_time.strftime("%H:%M"), r.exit_time.strftime("%H:%M"), r.exit_reason, float(r.gross_pnl or 0)) for r in rows]
    database.reset_engine()
    assert bot, "the sine wave gives the bot trades"

    trades, settings = lab.analyze_stock("TCS", _frames()["TCS"], cfg, [DAY])
    got = lab.evaluate(trades, lab.choice_from_cfg(cfg), settings)
    mine = [(k["side"], _hhmm(k["entry_ts"]), _hhmm(k["exit_ts"]), k["reason"], k["gross"]) for k in got["trades"]]
    assert [m[:4] for m in mine] == [b[:4] for b in bot], f"lab {mine}\nbot {bot}"
    # the same minutes, so the P&L differs only by the walk inside the fill minutes (under a rupee a share here)
    for m, b in zip(mine, bot):
        assert abs(m[4] - b[4]) <= 10 * 1.0, (m, b)
