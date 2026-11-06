"""Trend breakout handoff (breakout.py): the chop bot hands a stock back to Bot 3.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd
import pytest

import breakout
import recovery
from tests.test_gap_mode import _day, _settings, db  # noqa: F401  (db is a fixture)
from tests.test_recovery import _seed_bots

DAY = dt.date(2026, 9, 22)
JUMP = 120  # 11:15
LONG_JUMP = 130  # 11:25: the sine wave has a buy open when the green breakout candle closes


def _frame(spike_volume: bool, jump: int = JUMP) -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def chop(i, p):  # a slow wave the SMAs cross every few minutes, then one tall candle up
        if i < jump:
            return 1000.0 + math.sin(i * 2 * math.pi / 30)
        if i == jump:
            return p + 6.0
        return p + 0.05

    rows = _day(dt.date(2026, 9, 21), flat) + _day(DAY, chop)
    if spike_volume:
        for row in rows[375 + jump:]:
            row["volume"] += 30_000  # that minute traded 31x the usual 1,000 shares
    return pd.DataFrame(rows)


async def _run(database, frame, until=None, **over):
    """Replay the day on bot 1 with the breakout handoff wired as a live bot has it."""
    from replay import IST, SESSION_END, SESSION_OPEN, ReplayFeed, _to_next_grid
    from sma_research.replayer import ResearchEngine, _trade_rows

    feed = ReplayFeed({"SIMB": frame}, dt.datetime.combine(DAY, SESSION_OPEN, tzinfo=IST))
    engine = ResearchEngine(feed, "SIMB", _settings(database, **over))
    first_id = engine._min_trade_id
    moved: list[str] = []
    engine.handoff = engine._hand_off
    engine.on_handoff = moved.append
    engine.load_config()
    await engine.tick(feed.clock)
    engine.hold_for_next_cross(["SIMB"])
    engine.status = "RUNNING"
    end = until or dt.datetime.combine(DAY, SESSION_END, tzinfo=IST)
    while feed.clock < end and not moved:
        feed.clock = min(end, feed.clock + dt.timedelta(seconds=_to_next_grid(feed.clock)))
        await engine.tick(feed.clock)
    return moved, _trade_rows(first_id), engine


def _seed(database):
    import bots
    from models import BotConfig

    _seed_bots(database)
    bots.ensure_bot_config(3)
    with database.session_factory()() as s:
        s.get(BotConfig, bots.config_id_for(1)).trade_symbols = "SIMB,TCS"
        three = s.get(BotConfig, bots.config_id_for(3))
        three.trading_mode, three.trade_symbols = "PAPER", "INFY"
        s.commit()


def test_check_rules():
    cfg = type("C", (), {})()
    bar = {"high": 104.0, "low": 100.0}
    # 4.0 >= 1.8 x 2.0, and the gap is 0.25 % of 100: a breakout.
    assert breakout.check(bar, 2.0, 100.25, 100.0, None, None, cfg)[0]
    assert not breakout.check(bar, 2.0, 100.1, 100.0, None, None, cfg)[0]  # gap too narrow, no volume
    assert breakout.check(bar, 2.0, 100.1, 100.0, 1600, 1000, cfg)[0]  # volume 1.6x instead
    assert not breakout.check(bar, 2.0, 100.1, 100.0, 1400, 1000, cfg)[0]  # 1.4x is not a surge
    assert not breakout.check(bar, 2.5, 100.5, 100.0, 5000, 1000, cfg)[0]  # 4.0 < 1.8 x 2.5: not tall
    assert not breakout.check({"high": None, "low": 100.0}, 2.0, 101, 100, None, None, cfg)[0]
    tight = type("C", (), {"breakout_atr_mult": 3.0})()
    assert not breakout.check(bar, 2.0, 100.25, 100.0, None, None, tight)[0]
    assert breakout.atr_mult(type("C", (), {"breakout_atr_mult": 99})()) == 10.0
    assert "breakout_handoff" not in recovery.HANDOFF_SETTINGS  # a hand-off does not tick it; Bot 2's own setting rules


@pytest.mark.asyncio
async def test_a_breakout_cuts_the_chop_trade_and_hands_the_stock_to_bot_3(db):  # noqa: F811
    import bots
    from models import BotConfig
    from strategy_engine import trade_names

    _seed(db)
    moved, trades, engine = await _run(db, _frame(spike_volume=True), breakout_handoff=True, breakout_cut=True)
    assert moved == ["SIMB"]
    last = trades[-1]
    assert last["exit_reason"] == breakout.BREAKOUT_EXIT
    assert last["exit_time"] >= dt.datetime(2026, 9, 22, 11, 16)  # once the tall candle had closed
    assert "SIMB" not in engine.positions
    with db.session_factory()() as s:
        assert trade_names(s.get(BotConfig, bots.config_id_for(1))) == ["TCS"]
        assert trade_names(s.get(BotConfig, bots.config_id_for(3))) == ["INFY", "SIMB"]


@pytest.mark.asyncio
async def test_no_handoff_without_the_volume_or_the_gap_or_when_off(db):  # noqa: F811
    _seed(db)
    # The tall candle alone: no volume surge, and one candle does not open a 0.20 % SMA gap.
    moved, trades, _ = await _run(db, _frame(spike_volume=False), breakout_handoff=True)
    assert moved == [] and all(t["exit_reason"] != breakout.BREAKOUT_EXIT for t in trades)
    moved, trades, _ = await _run(db, _frame(spike_volume=True))
    assert moved == [] and all(t["exit_reason"] != breakout.BREAKOUT_EXIT for t in trades)


def test_a_practice_bot_never_hands_a_stock_to_a_live_bot_3(db):  # noqa: F811
    import bots
    from models import BotConfig
    from strategy_engine import trade_names

    _seed(db)
    with db.session_factory()() as s:
        s.get(BotConfig, bots.config_id_for(3)).trading_mode = "LIVE"
        s.commit()
    text = recovery.handoff(2, "SIMB", source_live=False, to_bot=breakout.TREND_BOT)
    assert text.startswith("no handoff: Bot 3 is LIVE"), text
    with db.session_factory()() as s:
        assert "SIMB" not in trade_names(s.get(BotConfig, bots.config_id_for(3)))


def test_api_saves_the_breakout_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    keys = {"breakout_handoff", "breakout_atr_mult", "breakout_gap_pct", "breakout_volume_mult", "breakout_rule", "breakout_cut"}
    assert keys <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["breakout_handoff"] is False and got["breakout_atr_mult"] == 1.8
        assert got["breakout_gap_pct"] == 0.2 and got["breakout_volume_mult"] == 1.5
        ok = client.put("/api/bots/2/config", json={"breakout_handoff": True, "breakout_gap_pct": 0.3})
        assert ok.status_code == 200 and ok.json()["breakout_handoff"] is True and ok.json()["breakout_gap_pct"] == 0.3
        assert client.put("/api/bots/2/config", json={"breakout_volume_mult": 0.5}).status_code == 422
        assert got["breakout_rule"] == "CANDLE" and got["breakout_cut"] is False
        ok = client.put("/api/config", json={"breakout_rule": "STRONG", "breakout_cut": True})
        assert ok.status_code == 200 and ok.json()["breakout_rule"] == "STRONG" and ok.json()["breakout_cut"] is True
        assert client.put("/api/bots/2/config", json={"breakout_rule": "BIG"}).status_code == 422
        own = client.put("/api/bots/2/config/stock/SBIN", json={"breakout_handoff": False})
        assert own.status_code == 200 and own.json()["breakout_handoff"] is False
    database.reset_engine()


def _pulls_back_frame() -> pd.DataFrame:
    """The breakout candle, then a minute that gives back most of it, then the run goes on."""
    frame = _frame(spike_volume=True)
    day = frame.iloc[375:].reset_index(drop=True)
    price = float(day.loc[JUMP, "close"])
    for k in range(JUMP + 1, 375):
        o = price
        price = price - 4.0 if k == JUMP + 1 else price + 0.05
        day.loc[k, ["open", "high", "low", "close"]] = [o, max(o, price) + 0.05, min(o, price) - 0.05, price]
    return pd.concat([frame.iloc[:375], day], ignore_index=True)


async def _loop(database, frame, until: dt.time, **trend_over):
    """Bot 2 (breakout on) plays the day; at its handoff a Bot 3 engine picks the stock up."""
    from replay import IST, SESSION_OPEN, ReplayFeed, _to_next_grid
    from sma_research.replayer import ResearchEngine, _trade_rows

    feed = ReplayFeed({"SIMB": frame}, dt.datetime.combine(DAY, SESSION_OPEN, tzinfo=IST))
    chop = ResearchEngine(feed, "SIMB", _settings(database, breakout_handoff=True, breakout_cut=True))
    first_id = chop._min_trade_id
    queued: list = []
    results: list[str] = []
    chop.handoff = chop._hand_off

    def arrive(to_bot, symbol, arrival):
        queued.append((to_bot, symbol, arrival))
        return "queued"

    chop.breakout_arrival = arrive
    chop.load_config()
    await chop.tick(feed.clock)
    chop.hold_for_next_cross(["SIMB"])
    chop.status = "RUNNING"
    trend = None
    end = dt.datetime.combine(DAY, until, tzinfo=IST)
    while feed.clock < end:
        feed.clock = min(end, feed.clock + dt.timedelta(seconds=_to_next_grid(feed.clock)))
        await chop.tick(feed.clock)
        if queued and trend is None:
            # The stock was not on Bot 3 before the handoff; now it is, as bots.register wires it.
            trend = ResearchEngine(feed, "SIMB", _settings(database, **trend_over))
            trend.load_config()
            await trend.tick(feed.clock)
            trend.hold_for_next_cross(["SIMB"])
            trend.status = "RUNNING"
            original = trend._enter_on_arrival

            async def noted(*args, **kwargs):
                results.append(await original(*args, **kwargs))
                return results[-1]

            trend._enter_on_arrival = noted
            trend.queue_breakout_entry(queued[0][1], queued[0][2])
        if trend is not None:
            await trend.tick(feed.clock)
    after = dt.datetime(2026, 9, 22, 11, 16)
    if trend is not None:
        trend.arrival_results = results
    return queued, trend, [t for t in _trade_rows(first_id) if t["entry_time"] >= after]


@pytest.mark.asyncio
async def test_bot_3_buys_the_breakout_on_its_pullback_not_at_the_top(db):  # noqa: F811
    """Bot 3 enters on the breakout's side at once, every check applied, waiting at the
    pullback level instead of buying the tall candle's top."""
    _seed(db)
    frame = _pulls_back_frame()
    top = float(frame.iloc[375 + JUMP]["high"])
    queued, trend, rows = await _loop(db, frame, dt.time(12, 0))
    assert len(queued) == 1
    to_bot, symbol, arrival = queued[0]
    assert (to_bot, symbol, arrival.direction) == (3, "SIMB", "LONG")
    assert arrival.limit < top - 2  # the 9 SMA or the candle's half-way, well under its top
    assert rows and rows[0]["direction"] == "LONG", rows
    assert rows[0]["entry_price"] <= arrival.limit + 0.5 and rows[0]["entry_price"] < top - 1.5


@pytest.mark.asyncio
async def test_no_pullback_within_two_candles_means_no_bot_3_order(db):  # noqa: F811
    _seed(db)
    queued, trend, rows = await _loop(db, _frame(spike_volume=True), dt.time(11, 40))  # it only climbs after
    assert len(queued) == 1 and trend is not None
    assert rows == []  # never bought the run's top; the wait ended after two candles
    assert trend.arrival_results[0].startswith("breakout arrival — pullback BUY at"), trend.arrival_results


@pytest.mark.asyncio
async def test_bot_3_checks_still_refuse_the_arrival(db):  # noqa: F811
    _seed(db)
    queued, trend, rows = await _loop(db, _pulls_back_frame(), dt.time(12, 0), use_rsi=True, rsi_long_min=95.0, rsi_long_max=100.0)
    assert len(queued) == 1 and rows == []
    assert "BULLISH ignored" in trend.arrival_results[0] and "RSI" in trend.arrival_results[0], trend.arrival_results


@pytest.mark.asyncio
async def test_a_trade_already_on_the_breakout_side_is_kept(db, monkeypatch):  # noqa: F811
    """A buy open when a green breakout candle closes rides it: not cut, and the stock stays on this bot."""
    import bots
    from models import BotConfig
    from replay import IST
    from strategy_engine import trade_names

    _seed(db)
    hits = []
    real_read = breakout.read

    def read(frame, cfg):
        out = real_read(frame, cfg)
        if out[0]:
            hits.append(breakout.side(frame.iloc[-2]))
        return out

    monkeypatch.setattr(breakout, "read", read)
    until = dt.datetime.combine(DAY, dt.time(9, 15), tzinfo=IST) + dt.timedelta(minutes=LONG_JUMP + 3)
    moved, trades, engine = await _run(db, _frame(spike_volume=True, jump=LONG_JUMP), until=until, breakout_handoff=True)
    pos = engine.positions.get("SIMB")
    assert moved == [] and pos is not None and pos.direction == "LONG"
    assert hits and hits[0] == "LONG"  # the green breakout was seen…
    assert all(t["exit_reason"] != breakout.BREAKOUT_EXIT for t in trades)  # …and the buy riding it was kept
    with db.session_factory()() as s:
        assert "SIMB" in trade_names(s.get(BotConfig, bots.config_id_for(1)))
        assert "SIMB" not in trade_names(s.get(BotConfig, bots.config_id_for(3)))


def _enriched_day(first_tall: bool):
    """Yesterday's last 30 quiet 15-minute candles, then today's (forming bar last), ATR 2 and a 0.25% SMA gap."""
    import datetime as _dt

    ist = _dt.timezone(_dt.timedelta(hours=5, minutes=30))
    rows = []
    start = _dt.datetime(2026, 10, 16, 9, 15, tzinfo=ist)
    for i in range(25):
        t = start + _dt.timedelta(minutes=15 * i)
        rows.append({"ts": int(t.timestamp()), "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2, "minute_volume": 1000})
    today = _dt.datetime(2026, 10, 19, 9, 15, tzinfo=ist)
    for i in range(3):
        t = today + _dt.timedelta(minutes=15 * i)
        tall = (i == 0) == first_tall and i < 2
        rows.append(
            {"ts": int(t.timestamp()), "open": 100.0, "high": 106.0 if tall else 100.5, "low": 99.5, "close": 105.0 if tall else 100.2,
             "minute_volume": 9000 if tall else 1000}
        )
    frame = pd.DataFrame(rows)
    frame["atr_14"], frame["sma_9"], frame["sma_21"] = 2.0, 100.25, 100.0
    return frame


def test_the_days_first_candle_is_never_a_breakout():
    cfg = type("C", (), {})()
    opening = _enriched_day(first_tall=True).iloc[:-1]  # the 09:15 candle just closed, 09:30 forming
    assert breakout.opening_candle(opening.iloc[:-1])
    hit, note = breakout.read(opening, cfg)
    assert not hit and "first candle" in note
    later = _enriched_day(first_tall=False)  # the same tall candle at 09:30 is read (09:45 forming)
    assert not breakout.opening_candle(later.iloc[:-1])
    assert breakout.read(later, cfg)[0]


@pytest.mark.asyncio
async def test_by_default_the_trade_runs_to_its_own_exit_then_the_stock_moves(db):  # noqa: F811
    """Owner's choice: a breakout never cuts the open trade; it closes by its own exits and the stock moves after."""
    import bots
    from models import BotConfig
    from strategy_engine import trade_names

    _seed(db)
    moved, trades, engine = await _run(db, _frame(spike_volume=True), breakout_handoff=True)
    assert moved == ["SIMB"]
    assert trades and all(t["exit_reason"] != breakout.BREAKOUT_EXIT for t in trades)  # nothing was cut
    assert all(t["exit_time"] is not None for t in trades) and "SIMB" not in engine.positions
    assert "SIMB" not in engine._breakout_pending
    with db.session_factory()() as s:
        assert "SIMB" in trade_names(s.get(BotConfig, bots.config_id_for(3)))


def test_a_handed_off_stock_goes_back_where_it_came_from(db, tmp_path, monkeypatch):  # noqa: F811
    import handoff_chain
    from strategy_engine import StrategyEngine

    monkeypatch.setenv("HANDOFF_RETURN_FILE", str(tmp_path / "hr.json"))
    eng = StrategyEngine()
    eng.bot_id = 2
    assert eng._breakout_target("SIMB") == breakout.TREND_BOT  # armed on Bot 2 by hand: Bot 3, as before
    handoff_chain.note(1, 2, "SIMB")  # Bot 1's recovery hand-off brought it to Bot 2
    assert eng._handoff_origin("SIMB") == 1 and eng._breakout_target("SIMB") == 1
    eng.bot_id = 1
    assert eng._handoff_origin("SIMB") is None  # the record says it is on Bot 2, not here


def test_a_backtest_sends_a_stock_played_as_bot_2_back_to_the_replayed_bot(db):  # noqa: F811
    import handoff_chain
    from replay import IST, SESSION_OPEN, ReplayEngine, ReplayFeed
    from tests.test_gap_mode import _day

    frame = pd.DataFrame(_day(DAY, lambda i, p: 1000.0))
    feed = ReplayFeed({"SIMB": frame}, dt.datetime.combine(DAY, SESSION_OPEN, tzinfo=IST))
    engine = ReplayEngine(feed, ["SIMB"], bot=1, chain=handoff_chain.replay_snapshot(1), on_bot={"SIMB": 2})
    assert engine._acting_bot("SIMB") == 2 and engine._handoff_origin("SIMB") == 1
    assert engine._breakout_target("SIMB") == 1  # back to Bot 1, not on to Bot 3
    plain = ReplayEngine(feed, ["SIMB"], bot=1)
    assert plain._acting_bot("SIMB") == 1 and plain._breakout_target("SIMB") == breakout.TREND_BOT


def _trend_frame(step: float) -> pd.DataFrame:
    """Two sessions of 5-minute candles: flat, then a steady climb (step > 0) or a zig-zag (step = 0)."""
    from indicators import enrich

    rows, price = [], 1000.0
    start = int(dt.datetime(2026, 9, 21, 9, 15, tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30))).timestamp())
    for d in range(2):
        for k in range(75):
            ts = start + d * 86_400 + k * 300
            if d == 0 or step == 0:
                nxt = 1000.0 + (1.5 if k % 2 else -1.5)
            else:
                nxt = price + step + (0.3 if k % 3 == 0 else 0.0)
            o = price
            rows.append({"ts": ts, "open": o, "high": max(o, nxt) + 0.2, "low": min(o, nxt) - 0.2, "close": nxt,
                         "volume": 1000 * (d * 75 + k + 1)})
            price = nxt
    return enrich(pd.DataFrame(rows), 9, 21)


def test_the_strong_trend_rule_needs_a_real_trend_not_one_candle():
    climb = _trend_frame(1.0).iloc[: 75 + 40]
    hit, note, side = breakout.strong(climb)
    assert hit and side == "LONG" and "strong trend up" in note, note
    hit, note, side = breakout.strong(_trend_frame(0.0).iloc[: 75 + 40])
    assert not hit and side is None
    cfg = type("C", (), {"breakout_rule": "STRONG"})()
    assert breakout.judge(climb, cfg)[0] and breakout.rule(cfg) == "STRONG"
    assert breakout.rule(type("C", (), {"breakout_rule": "nonsense"})()) == "CANDLE"
    assert breakout.cut(type("C", (), {})()) is False  # off by default


def test_the_strong_trend_checks_and_count_are_adjustable():
    climb = _trend_frame(1.0).iloc[: 75 + 40]
    C = lambda **kw: type("C", (), {"breakout_rule": "STRONG", **kw})()  # noqa: E731
    hit, note, _ = breakout.strong(climb, C())
    assert hit and "needs 6" in note, note
    # Every check on and all 7 needed: the steady climb still passes the ones it passes, or says how many.
    hit7, note7, _ = breakout.strong(climb, C(breakout_trend_need=7))
    assert ("needs 7" in note7) and (hit7 or "of 7 checks" in note7), note7
    # Only two checks counted, one of them needed: the zig-zag can still fail the must-pass ones.
    loose = C(breakout_trend_checks="stack:use,vwap:off,slope:off,fan:off,hold:off,structure:off,run:off", breakout_trend_need=1)
    assert breakout.trend_need(loose) == 1
    assert breakout.trend_rules(loose)["vwap"] == "off" and breakout.trend_rules(loose)["stack"] == "use"
    assert breakout.trend_need(C(breakout_trend_checks="stack:must,vwap:off,slope:off,fan:off,hold:off,structure:off,run:off", breakout_trend_need=6)) == 1
    zig = _trend_frame(0.0).iloc[: 75 + 40]
    hit, note, _ = breakout.strong(zig, C(breakout_trend_checks="run:must"))
    assert not hit and ("must-pass" in note or "needs" in note), note
    with pytest.raises(ValueError):
        breakout.parse_trend_rules("stack:maybe")
    with pytest.raises(ValueError):
        breakout.parse_trend_rules(",".join(f"{k}:off" for k in breakout.TREND_CHECKS))
    assert breakout.parse_trend_rules("") == breakout.DEFAULT_TREND_RULES


def test_api_saves_the_strong_trend_choices(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"breakout_trend_checks", "breakout_trend_need"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["breakout_trend_need"] == 6 and "stack:must" in got["breakout_trend_checks"]
        ok = client.put("/api/config", json={"breakout_trend_checks": "run:off,fan:must", "breakout_trend_need": 4})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["breakout_trend_need"] == 4 and "run:off" in body["breakout_trend_checks"] and "fan:must" in body["breakout_trend_checks"]
        assert client.put("/api/config", json={"breakout_trend_checks": "nonsense:must"}).status_code == 422
        assert client.put("/api/config", json={"breakout_trend_need": 9}).status_code == 422
    database.reset_engine()

