"""Force order follows the candle colour (colour_force.py). PAPER only: a fake broker fills locally."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import colour_force
from strategy_engine import ForceRefused
from tests.test_candle_patterns import DAY, _cfg, _rows
from tests.test_sma_atr_terminal import IST, engine  # noqa: F401  (engine is a fixture)

# 40 rising minutes (SMA 9 well above SMA 21), then 09:55-09:59 a red 5-minute candle, then two forming minutes.
_UP = [(1000.0 + i, 1000.0 + i + 1) for i in range(40)]
_RED = [(1040.0, 1039.6), (1039.6, 1039.2), (1039.2, 1038.8), (1038.8, 1038.4), (1038.4, 1038.0)]
_FORMING = [(1038.0, 1038.5), (1038.5, 1039.0)]


def _frame(candles):
    return pd.DataFrame(_rows(DAY, candles))


def test_side_reads_the_last_closed_colour_candle_not_the_forming_one():
    cfg = _cfg(entry_mode="COLOUR", pattern_tf=5, colour_count=1, colour_min_body=0.0, colour_force=True)
    assert colour_force.on(cfg)
    side, note = colour_force.side(_frame(_UP + _RED + _FORMING), cfg)
    assert side == "BEARISH" and "red" in note
    # A green 5-minute candle just closed (09:50-09:54 still rising before the red one): buy.
    side, _ = colour_force.side(_frame(_UP[:40] + _FORMING), cfg)
    assert side == "BULLISH"


def test_no_colour_means_no_side_and_off_outside_colour_mode():
    cfg = _cfg(entry_mode="COLOUR", pattern_tf=5, colour_count=1, colour_min_body=5.0, colour_force=True)
    side, note = colour_force.side(_frame(_UP + _RED + _FORMING), cfg)
    assert side is None and note
    assert not colour_force.on(_cfg(entry_mode="SMA", colour_force=True))
    assert not colour_force.on(_cfg(entry_mode="COLOUR", colour_force=False))


def _setup(engine, monkeypatch, **over):  # noqa: F811
    import database
    from models import BotConfig

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: dt.datetime.combine(DAY, dt.time(10, 1), tzinfo=IST))
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "COLR"
        for key, value in {"entry_mode": "COLOUR", "pattern_tf": 5, "colour_count": 1, "colour_min_body": 0.0, **over}.items():
            setattr(row, key, value)
        db.commit()
    engine._frames["COLR"] = _frame(_UP + _RED + _FORMING)
    engine._ltps["COLR"] = 1039.0
    engine.data_source = "GROWW"


@pytest.mark.asyncio
async def test_force_sells_on_a_red_colour_candle_while_the_smas_point_up(engine, monkeypatch):  # noqa: F811
    _setup(engine, monkeypatch, colour_force=True)
    await engine.force_order("COLR")
    assert engine.positions["COLR"].direction == "SHORT"


@pytest.mark.asyncio
async def test_force_keeps_the_sma_side_when_the_switch_is_off(engine, monkeypatch):  # noqa: F811
    _setup(engine, monkeypatch, colour_force=False)
    await engine.force_order("COLR")
    assert engine.positions["COLR"].direction == "LONG"


@pytest.mark.asyncio
async def test_force_refuses_when_the_colour_candle_has_no_colour(engine, monkeypatch):  # noqa: F811
    _setup(engine, monkeypatch, colour_force=True, colour_min_body=5.0)
    with pytest.raises(ForceRefused, match="no colour to follow"):
        await engine.force_order("COLR")
    assert "COLR" not in engine.positions
