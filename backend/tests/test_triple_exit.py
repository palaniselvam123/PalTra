"""Three-line mode: close on the fast / medium SMA cross against the trade (triple_exit.py)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import triple_exit
from groww_client import IST
from strategy_engine import candle_rows


def _closes(values):
    return pd.DataFrame([{"ts": i * 60, "open": v, "high": v, "low": v, "close": v} for i, v in enumerate(values)])


def _cfg(**over):
    return NS(**{**dict(sma_fast=3, sma_medium=5, sma_slow=8, triple_exit=True), **over})


def test_on_only_in_three_line_mode_with_the_switch():
    assert triple_exit.on(_cfg())
    assert not triple_exit.on(_cfg(triple_exit=False))
    assert not triple_exit.on(_cfg(sma_medium=None))
    assert not triple_exit.on(NS(sma_fast=9, sma_slow=21))


def test_a_cross_under_the_medium_line_closes_a_buy_not_a_sell():
    rising = [100 + i for i in range(10)]
    fall = rising + [105, 100]  # the fast line drops under the medium one on the last candle
    hit, note = triple_exit.crossed_against(_closes(fall), "LONG", _cfg())
    assert hit and "SMA 3 crossed under SMA 5" in note
    assert triple_exit.crossed_against(_closes(fall), "SHORT", _cfg())[0] is False
    # Still rising: no cross, the buy stays.
    assert triple_exit.crossed_against(_closes(rising), "LONG", _cfg())[0] is False
    # The mirror for a sell.
    falling = [200 - i for i in range(10)]
    up = falling + [195, 200]
    assert triple_exit.crossed_against(_closes(up), "SHORT", _cfg())[0] is True
    # Too few candles for the medium line: nothing.
    assert triple_exit.crossed_against(_closes([1, 2, 3]), "LONG", _cfg())[0] is False


def test_the_chart_rows_carry_the_medium_line():
    from indicators import enrich

    frame = _closes([100 + i * 0.1 for i in range(40)])
    frame["volume"] = range(1, 41)
    rows = candle_rows(enrich(frame, 9, 30, 14, 21))
    assert rows[-1]["smaMid"] is not None and rows[0]["smaMid"] is None
    plain = candle_rows(enrich(frame, 9, 21, 14))
    assert plain[-1]["smaMid"] is None


@pytest.mark.asyncio
async def test_engine_closes_a_three_line_buy_on_the_fast_medium_cross(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from indicators import enrich
    from models import BotConfig, TradeLog
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker, _instant
    from tests.test_sma_atr_terminal import _tape as bot_tape

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    now = dt.datetime(2026, 9, 29, 11, 0, tzinfo=IST)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: now)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "ACME"
        row.sma_fast, row.sma_medium, row.sma_slow = 3, 5, 8
        row.triple_exit = True
        db.commit()
    cfg = eng.load_config()
    cfg.symbol = "ACME"
    up = [100.0 + i * 0.2 for i in range(40)]
    frame = enrich(bot_tape(up), cfg.sma_fast, cfg.sma_slow, cfg.atr_period, cfg.sma_medium)
    eng._frames["ACME"] = frame
    eng._ltps["ACME"] = float(frame["close"].iloc[-1])
    eng.status = "RUNNING"
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert eng.positions["ACME"].direction == "LONG", text
    # Later candles: the price drops, the 3-candle line falls under the 5-candle one.
    tape = bot_tape(up + [up[-1] + 0.2, up[-1] - 3.0, up[-1] - 3.0])
    shift = int((now + dt.timedelta(minutes=5)).timestamp()) - int(tape["ts"].iloc[-1])
    tape["ts"] = tape["ts"] + shift
    eng._frames["ACME"] = tape
    eng._focus = "ACME"
    eng.ltp = up[-1] - 3.0
    await eng._watch_triple_exit(cfg)
    assert "ACME" not in eng.positions
    with database.session_factory()() as db:
        last = db.query(TradeLog).order_by(TradeLog.id.desc()).first()
        assert last.exit_reason == triple_exit.REASON
    database.reset_engine()


def test_api_saves_the_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "triple_exit" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["triple_exit"] is False
        body = client.put("/api/config", json={"sma_fast": 9, "sma_medium": 21, "sma_slow": 50, "triple_exit": True}).json()
        assert body["triple_exit"] is True and body["sma_medium"] == 21 and body["sma_slow"] == 50
    database.reset_engine()
