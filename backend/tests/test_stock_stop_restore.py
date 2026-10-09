"""A stock's own stop (set when it is armed) is the one its trade keeps.

The shared settings say ₹50; the stock was armed with ₹10, trail ₹5, target
₹30. The trade opens with ₹10, a restart restores ₹10 (not the shared ₹50),
and the state sends that stock's numbers for the top bar. PAPER only.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/stop.db")
    import database

    database.reset_engine()
    database.init_db()
    from models import BotConfig

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.symbol = "TCS"
        row.use_stop, row.stop_type = True, "TSL"
        row.tsl_sl_points, row.tsl_trail_points, row.tsl_target_points = 50.0, 10.0, 0.0
        row.stock_settings = json.dumps({"TCS": {"tsl_sl_points": 10.0, "tsl_trail_points": 5.0, "tsl_target_points": 30.0}})
        s.commit()
    yield database
    database.reset_engine()


def _open_trade(eng, cfg):
    now = dt.datetime(2026, 10, 9, 10, 0, tzinfo=IST)
    return eng._insert_open_trade(cfg=cfg, direction="LONG", fill=2000.0, cross_price=2000.0, atr=2.0, sl=1990.0, now=now, qty=5)


def test_a_restart_restores_the_stocks_own_stop_not_the_shared_one(db):
    from strategy_engine import StrategyEngine, _cfg_for

    eng = StrategyEngine()
    own = _cfg_for(eng.load_config(), "TCS")
    assert (own.tsl_sl_points, own.tsl_trail_points, own.tsl_target_points) == (10.0, 5.0, 30.0)
    _open_trade(eng, own)

    again = StrategyEngine()
    again.restore_open_books()
    pos = again.positions["TCS"]
    assert (pos.tsl_points, pos.tsl_step) == (10.0, 5.0)
    assert pos.target == pytest.approx(2030.0)
    assert pos.sl_trigger == pytest.approx(1990.0)


def test_a_restart_keeps_the_stop_the_trade_opened_with_after_settings_change(db):
    from models import BotConfig
    from strategy_engine import StrategyEngine, _cfg_for

    eng = StrategyEngine()
    _open_trade(eng, _cfg_for(eng.load_config(), "TCS"))
    # The stock's settings change while the trade is open: the trade keeps its own.
    with db.session_factory()() as s:
        s.get(BotConfig, 1).stock_settings = "{}"
        s.commit()
    again = StrategyEngine()
    again.restore_open_books()
    assert (again.positions["TCS"].tsl_points, again.positions["TCS"].tsl_step) == (10.0, 5.0)


def test_the_state_sends_the_chart_stocks_own_stop_numbers(db):
    from strategy_engine import StrategyEngine

    snap = StrategyEngine().snapshot()
    assert snap["stop_type"] == "TSL"
    assert snap["stop_points"]["tsl_sl_points"] == 10.0
    assert snap["stop_points"]["tsl_trail_points"] == 5.0
    assert snap["stop_points"]["tsl_target_points"] == 30.0
