"""Hand-off chain (handoff_chain.py): stocks sent back to the bot they came from at the end of the day, and
backtests that play Bot 2 / Bot 3 on the stocks handed to them. PAPER only: no order path is used."""
from __future__ import annotations

import datetime as dt

import pytest

import handoff_chain
import recovery
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)
from tests.test_recovery import DAY, HOLD, _frame, _recovers, _seed_bots

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def _own_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HANDOFF_RETURN_FILE", str(tmp_path / "handoff_return.json"))


def _lists(database):
    import bots
    from models import BotConfig
    from strategy_engine import trade_names

    with database.session_factory()() as s:
        return {n: trade_names(s.get(BotConfig, bots.config_id_for(n))) for n in (1, 2, 3)}


def test_a_chain_of_hand_offs_goes_back_to_where_it_started(db):  # noqa: F811
    import bots

    bots.ensure_bot_config(3)
    _seed_bots(db)
    assert recovery.handoff(1, "SBIN", source_live=False).startswith("moved to Bot 2")
    assert handoff_chain.moved()["SBIN"]["origin"] == 1
    assert recovery.handoff(2, "SBIN", source_live=False, to_bot=3).startswith("moved to Bot 3")
    row = handoff_chain.moved()["SBIN"]
    assert row["origin"] == 1 and row["on"] == 3  # the origin is kept through the chain
    assert _lists(db) == {1: ["TCS"], 2: [], 3: ["SBIN"]}

    class Held:
        positions = {"SBIN": object()}

    lines = handoff_chain.send_back({3: Held()})
    assert "still held" in lines[0] and "SBIN" in handoff_chain.moved()  # a held stock waits
    lines = handoff_chain.send_back({})
    assert lines == ["SBIN: back on Bot 1 from Bot 3"]
    assert handoff_chain.moved() == {}
    assert _lists(db) == {1: ["TCS", "SBIN"], 2: [], 3: []}


def test_a_stock_handed_home_is_no_longer_noted_and_bot5_is_left_out(db):  # noqa: F811
    _seed_bots(db)
    recovery.handoff(1, "SBIN", source_live=False)
    recovery.handoff(2, "SBIN", source_live=False, to_bot=1, chop=False)
    assert handoff_chain.moved() == {}
    handoff_chain.note(1, 5, "TCS")  # the fast-mover hand-off gives its stocks back itself
    assert handoff_chain.moved() == {}


def test_send_back_is_due_after_the_close_or_before_the_open():
    s = handoff_chain.Settings(on=True, at="15:35")
    thu = lambda h, m: dt.datetime(2026, 11, 5, h, m, tzinfo=IST)  # noqa: E731
    assert not handoff_chain.due(thu(15, 0), s)
    assert not handoff_chain.due(thu(9, 30), s)
    assert handoff_chain.due(thu(15, 40), s) and handoff_chain.due(thu(8, 30), s)
    assert handoff_chain.due(dt.datetime(2026, 11, 7, 12, 0, tzinfo=IST), s)  # Saturday


def test_settings_check_the_time():
    assert handoff_chain.load().on is False  # off by default
    assert handoff_chain.save(True, "16:00").at == "16:00"
    with pytest.raises(ValueError):
        handoff_chain.save(True, "10:00")


def test_entry_reason_tag_round_trips():
    assert handoff_chain.tag("SMA_CROSS:PULLBACK", 2) == "SMA_CROSS:PULLBACK@B2"
    assert handoff_chain.played_as("GAP_MODE@B3") == 3
    assert handoff_chain.played_as("SMA_CROSS") is None and handoff_chain.played_as(None) is None


def test_overrides_give_bot2_the_chop_recipe_on_its_own_settings():
    snap = {"bots": {"2": {"fields": {"candle_minutes": 5, "flip_orders": False}, "stocks": {"SBIN": {"qty": 7}}}}}
    got = handoff_chain.overrides(snap, 2, "sbin", own_bot=1)
    assert got["candle_minutes"] == 5 and got["qty"] == 7
    assert got["flip_orders"] is True and got["use_gap_mode"] is True  # recovery.HANDOFF_SETTINGS on top
    assert handoff_chain.overrides(snap, 3, "SBIN", own_bot=1) == {}  # no Bot 3 in this snapshot


@pytest.mark.asyncio
async def test_a_backtest_plays_the_hand_off_to_bot_2_and_leaves_the_live_lists_alone(db):  # noqa: F811
    import bots
    from models import BotConfig
    from replay import SESSION_END, SESSION_OPEN, ReplayFeed, _to_next_grid
    from sma_research.replayer import ResearchEngine
    from tests.test_gap_mode import _settings

    _seed_bots(db)
    with db.session_factory()() as s:
        s.get(BotConfig, bots.config_id_for(1)).trade_symbols = "SIMR,TCS"
        s.commit()
    before = _lists(db)
    feed = ReplayFeed({"SIMR": _frame(_recovers)}, dt.datetime.combine(DAY, SESSION_OPEN, tzinfo=IST))
    engine = ResearchEngine(feed, "SIMR", _settings(db, **HOLD, recovery_max_candles=12))
    engine.chain = handoff_chain.replay_snapshot(1)
    engine.handoff = engine._hand_off
    engine.load_config()
    await engine.tick(feed.clock)
    engine.hold_for_next_cross(["SIMR"])
    engine.status = "RUNNING"
    end = dt.datetime.combine(DAY, SESSION_END, tzinfo=IST)
    while feed.clock < end and not engine.chain_log:
        feed.clock = min(end, feed.clock + dt.timedelta(seconds=_to_next_grid(feed.clock)))
        await engine.tick(feed.clock)
    assert engine.chain_log and engine.chain_log[0]["to_bot"] == 2
    assert engine._on_bot == {"SIMR": 2} and "SIMR" in engine.replay_symbols  # stays in the backtest
    assert engine._chain_fields["SIMR"]["flip_orders"] is True
    assert _lists(db) == before and handoff_chain.moved() == {}  # nothing moved live


def test_a_replay_engine_reads_the_played_bots_settings_for_that_stock(db):  # noqa: F811
    import bots
    from models import BotConfig
    from replay import SESSION_OPEN, ReplayEngine, ReplayFeed
    from strategy_engine import _cfg_for
    from tests.test_gap_mode import _day

    import pandas as pd

    bots.ensure_bot_config(3)
    with db.session_factory()() as s:
        s.get(BotConfig, bots.config_id_for(3)).candle_minutes = 5
        s.commit()
    snap = handoff_chain.replay_snapshot(1)
    frame = pd.DataFrame(_day(DAY, lambda i, p: 1000.0))
    feed = ReplayFeed({"SBIN": frame, "TCS": frame}, dt.datetime.combine(DAY, SESSION_OPEN, tzinfo=IST))
    engine = ReplayEngine(feed, ["SBIN", "TCS"], bot=1, chain=snap, on_bot={"SBIN": 3})
    cfg = engine.load_config()
    assert _cfg_for(cfg, "SBIN").candle_minutes == 5
    assert _cfg_for(cfg, "TCS").candle_minutes == cfg.candle_minutes  # the other stock keeps Bot 1's
    assert engine._chain_hand_off(3, "SBIN", 2).startswith("moved to Bot 2")
    assert _cfg_for(engine.load_config(), "SBIN").flip_orders is True


def test_run_totals_split_by_the_bot_a_trade_was_played_as():
    from models import TradeLog
    from replay import _handoff_part

    at = dt.datetime(2026, 11, 3, 10, 0)
    rows = [
        TradeLog(symbol="SBIN", entry_reason="SMA_CROSS", gross_pnl=50, net_pnl=40, exit_time=at),
        TradeLog(symbol="SBIN", entry_reason="GAP_MODE@B2", gross_pnl=-20, net_pnl=-30, exit_time=at),
        TradeLog(symbol="TCS", entry_reason="BREAKOUT_ARRIVAL:PULLBACK@B3", gross_pnl=80, net_pnl=70, exit_time=at),
        TradeLog(symbol="TCS", entry_reason="SMA_CROSS@B3", gross_pnl=10, net_pnl=5, exit_time=None),  # still open
    ]
    got = _handoff_part(rows)
    assert got == [
        {"bot": 2, "trades": 1, "stocks": 1, "gross": -20.0, "net": -30.0, "wins": 0},
        {"bot": 3, "trades": 1, "stocks": 1, "gross": 80.0, "net": 70.0, "wins": 1},
    ]
    assert _handoff_part(rows[:1]) is None


def test_api_reads_and_saves_the_send_back_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        assert client.get("/api/bots/handoff-return").json()["on"] is False
        got = client.put("/api/bots/handoff-return", json={"on": True, "at": "15:45"}).json()
        assert got["on"] is True and got["at"] == "15:45"
        assert client.put("/api/bots/handoff-return", json={"on": True, "at": "11:00"}).status_code == 422
        assert client.post("/api/bots/handoff-return/now").json()["lines"] == []
    database.reset_engine()
