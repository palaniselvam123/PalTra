"""Watch smaller candles while in a trade (small_candles.py): exit on a 1/3/5-minute turn before the bot's own candle can."""
from __future__ import annotations

import pandas as pd
import pytest

import exit_candle
import small_candles


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update({"sma_fast": 2, "sma_slow": 3, "slope_lookback": 1, "slope_turn_pct": 0.02}, **kw)


def _tape(closes, start=0):
    rows = [{"ts": start + i * 60, "open": c - 0.1 if i % 2 else c + 0.1, "high": c + 0.3, "low": c - 0.3, "close": c, "volume": 1} for i, c in enumerate(closes)]
    return pd.DataFrame(rows)


def test_sizes_are_cleaned_and_only_smaller_than_the_bot_candle_count():
    assert small_candles.sizes(Cfg(small_exit_minutes="5, 1,3,7,abc,1")) == [1, 3, 5]
    assert small_candles.clean_sizes([3, 5, 99]) == "3,5"
    assert small_candles.clean_sizes("") == "" and small_candles.sizes(Cfg(small_exit_minutes=None)) == []
    cfg = Cfg(small_exit_minutes="1,5,10")
    assert small_candles.watched(cfg, 15) == [1, 5, 10]
    assert small_candles.watched(cfg, 5) == [1]
    assert small_candles.on(cfg, 1) is False


def test_a_cross_against_the_trade_on_the_small_candle_says_exit():
    # A long; the 1-minute closes fall, so the fast SMA crosses under the slow one.
    frame = exit_candle.closed_frame(_tape([100, 101, 102, 103, 104, 103, 101, 97]), 1, 2, 3)
    cfg = Cfg(small_exit_slope=False)
    note = small_candles.verdict(frame, "LONG", cfg, first_after_entry=False)
    assert note and "crossed below" in note
    # The same candles do not bother a short.
    assert small_candles.verdict(frame, "SHORT", cfg, first_after_entry=False) is None


def test_first_candle_after_the_entry_counts_smas_already_against_as_a_cross():
    frame = exit_candle.closed_frame(_tape([100, 99, 98, 97, 96, 95, 94, 93]), 1, 2, 3)
    cfg = Cfg(small_exit_slope=False)
    assert small_candles.verdict(frame, "LONG", cfg, first_after_entry=False) is None  # no fresh cross
    assert "already against" in (small_candles.verdict(frame, "LONG", cfg, first_after_entry=True) or "")


def test_slope_and_counter_candles_say_exit_on_their_own():
    frame = exit_candle.closed_frame(_tape([100, 100, 100, 100, 99, 98, 97, 96]), 1, 2, 3)
    only_slope = Cfg(small_exit_cross=False, small_exit_counter=0)
    assert "slope" in (small_candles.verdict(frame, "LONG", only_slope, False) or "")
    only_counter = Cfg(small_exit_cross=False, small_exit_slope=False, small_exit_counter=3)
    # closes 99 → 98 → 97 → 96 but the drawn opens alternate, so make every body red by hand
    red = frame.copy()
    red["open"] = red["close"] + 0.2
    assert "3 candles in a row against" in (small_candles.verdict(red, "LONG", only_counter, False) or "")
    assert small_candles.verdict(red, "SHORT", only_counter, False) is None


def test_decide_needs_enough_sizes_and_the_loss_gate():
    cfg = Cfg(small_exit_agree=2, small_exit_loss_pct=0.5)
    state = {1: "cross", 5: None}
    assert small_candles.decide(state, cfg, "LONG", 100.0, 99.0) == (False, "")
    state = {1: "cross", 5: "slope"}
    assert small_candles.decide(state, cfg, "LONG", 100.0, 99.8) == (False, "")  # only 0.2% under water
    hit, note = small_candles.decide(state, cfg, "LONG", 100.0, 99.0)
    assert hit and "1-min: cross" in note and "5-min: slope" in note and "1.00% under water" in note
    # A short under water is the price above the entry.
    assert small_candles.decide(state, cfg, "SHORT", 100.0, 101.0)[0] is True
    assert small_candles.decide(state, cfg, "SHORT", 100.0, 99.0)[0] is False
    # No gate, any one size: the default.
    assert small_candles.decide({3: "turn"}, Cfg(), "LONG", 100.0, 105.0)[0] is True


def test_api_saves_the_small_candle_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "small_exit_minutes" in STOCK_FIELDS and "small_exit_loss_pct" in STOCK_FIELDS
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["small_exit_minutes"] == "" and got["small_exit_cross"] is True and got["small_exit_agree"] == 1
        ok = client.put("/api/config", json={"small_exit_minutes": "5,1,7", "small_exit_loss_pct": 0.3, "small_exit_counter": 3})
        assert ok.status_code == 200
        assert ok.json()["small_exit_minutes"] == "1,5" and ok.json()["small_exit_loss_pct"] == 0.3 and ok.json()["small_exit_counter"] == 3
        own = client.put("/api/config/stock/SBIN", json={"small_exit_minutes": "3"})
        assert own.status_code == 200 and own.json()["small_exit_minutes"] == "3"
        assert client.put("/api/config", json={"small_exit_agree": 9}).status_code == 422
    database.reset_engine()


@pytest.mark.asyncio
async def test_engine_exits_a_15_minute_long_on_the_1_minute_turn(tmp_path, monkeypatch):
    """A 15-minute bot holds a long; the 1-minute tape turns down: the trade closes with SMALL_CANDLE_EXIT."""
    import datetime as dt
    from zoneinfo import ZoneInfo

    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import OpenPosition, StrategyEngine, _cfg_for

    eng = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    cfg = eng.load_config()
    with database.session_factory()() as db:
        from models import BotConfig

        row = db.get(BotConfig, cfg.id)
        row.candle_minutes, row.small_exit_minutes, row.small_exit_slope, row.sma_fast, row.sma_slow = 15, "1", False, 2, 3
        row.trade_symbols = "ACME"
        db.commit()
    cfg = eng.load_config()
    ist = ZoneInfo("Asia/Kolkata")
    entry = dt.datetime(2026, 10, 29, 10, 0, tzinfo=ist)
    start = int(entry.timestamp()) - 5 * 60
    eng._tapes["ACME"] = _tape([100, 101, 102, 103, 104, 104, 103, 101, 99, 98, 97], start=start)
    eng.positions["ACME"] = OpenPosition(
        direction="LONG", qty=1, entry_price=104.0, ma_cross_price=104.0, atr_at_entry=1.0, sl_trigger=90.0,
        sl_order_id="", entry_order_id="E1", entry_time=entry, trade_id=1, mode="PAPER", stop_active=False,
    )
    eng.status = "RUNNING"
    eng._focus = "ACME"
    eng.ltp = 97.0
    eng._ltps["ACME"] = 97.0
    closed: list[tuple] = []

    async def fake_exit(cfg_, price, reason):
        closed.append((cfg_.symbol, price, reason))
        eng.position = None

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    await eng._watch_small_candles(_cfg_for(cfg, "ACME"))
    assert closed == [("ACME", 97.0, small_candles.REASON)]
    database.reset_engine()
