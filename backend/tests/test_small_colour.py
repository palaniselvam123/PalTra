"""Colours on smaller candles (small_colour.py): the last N candles of smaller sizes, as an entry check and as a
confirm for the colour-turn exit. Local fills only (no Groww order path)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import small_colour as sc

T0 = 1_758_600_000  # 09:30 IST


def _tape(moves: list[float], start: float = 100.0) -> pd.DataFrame:
    """1-minute candles; each minute opens at the last close and moves by `moves[i]` (green > 0, red < 0)."""
    rows, price = [], start
    for i, mv in enumerate(moves):
        o, c = price, price + mv
        rows.append({"ts": T0 + i * 60, "open": o, "high": max(o, c) + 0.1, "low": min(o, c) - 0.1, "close": c, "volume": 1000 * (i + 1)})
        price = c
    return pd.DataFrame(rows)


def _cfg(**over):
    base = dict(sc_minutes="5", sc_count=2, sc_join="ALL", sc_need=1, sc_entry=True, sc_confirm=True, sma_fast=9, sma_slow=21, ha_colours=False)
    return NS(**{**base, **over})


def test_sizes_counts_and_joins():
    assert sc.sizes(_cfg(sc_minutes="10, 3,7,3")) == [3, 10] and sc.clean_sizes([5, 1, 99]) == "1,5"
    assert sc.watched(_cfg(sc_minutes="3,5,10"), 5) == [3]
    assert sc.count(_cfg(sc_count=9)) == 5 and sc.join(_cfg(sc_join="x")) == "ALL"
    runs = {3: "G", 5: "R"}
    assert sc.agree(runs, "G", _cfg(sc_join="ALL"))[0] is False
    assert sc.agree(runs, "G", _cfg(sc_join="ANY"))[0] is True
    assert sc.agree({1: "G", 3: "G", 5: None}, "G", _cfg(sc_join="AT_LEAST", sc_need=2))[0] is True


def test_run_colour_reads_the_last_n_candles():
    frame = pd.DataFrame({"open": [1, 2, 3], "close": [2, 3, 4]})
    assert sc.run_colour(frame, 2) == "G"
    frame = pd.DataFrame({"open": [1, 3, 2], "close": [2, 2, 1]})
    assert sc.run_colour(frame, 2) == "R" and sc.run_colour(frame, 3) is None and sc.run_colour(frame, 4) is None


def test_entry_check_and_turn_confirm_on_five_minute_candles():
    # 20 green minutes then 11 red ones: the last two closed 5-minute candles are red (the 31st minute is forming).
    tape = _tape([0.2] * 20 + [-0.2] * 11)
    assert sc.entry_reason(tape, "SHORT", _cfg(), 15) is None
    refused = sc.entry_reason(tape, "LONG", _cfg(), 15)
    assert refused and refused.startswith("smaller candles:")
    assert sc.entry_reason(tape, "LONG", _cfg(sc_entry=False), 15) is None
    assert sc.entry_reason(tape, "LONG", _cfg(), 5) is None  # 5 is not smaller than a 5-minute bot: nothing read
    assert sc.confirms_turn(tape, "LONG", _cfg(), 15)[0] == "yes"
    assert sc.confirms_turn(tape, "SHORT", _cfg(), 15)[0] == "back"  # red is the short trade's own colour: drop the wait
    mixed = _tape([0.2] * 20 + [-0.2] * 5 + [0.2] * 6)  # last two 5-minute candles: red, then green
    assert sc.confirms_turn(mixed, "LONG", _cfg(), 15)[0] == "wait"


@pytest.mark.asyncio
async def test_engine_refuses_an_entry_the_smaller_candles_do_not_back(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/s.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from indicators import enrich
    from models import BotConfig
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import IST, _FakeBroker, _instant
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
        row.candle_minutes = 15
        row.sc_minutes = "5"
        row.sc_count = 2
        row.sc_entry = True
        db.commit()
    cfg = eng.load_config()
    cfg.symbol = "ACME"
    frame = enrich(bot_tape([100.0 + i * 0.2 for i in range(40)]), cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
    eng._frames["ACME"] = frame
    eng._ltps["ACME"] = float(frame["close"].iloc[-1])
    eng.status = "RUNNING"
    eng._tapes["ACME"] = _tape([0.2] * 20 + [-0.2] * 11)  # the smaller candles are red
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert "ACME" not in eng.positions and "smaller candles" in text
    eng._tapes["ACME"] = _tape([-0.2] * 20 + [0.2] * 11)  # now green
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert eng.positions["ACME"].direction == "LONG", text
    database.reset_engine()
