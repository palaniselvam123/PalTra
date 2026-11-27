"""Candle patterns as an extra entry check and an exit (pattern_check.py)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import pattern_check
from groww_client import IST

DAY = dt.date(2026, 9, 29)


def _tape(candles: list[tuple[float, float, float, float]], start: dt.time = dt.time(10, 0), size: int = 5) -> pd.DataFrame:
    """1-minute rows that group into the given `size`-minute candles (open, high, low, close) from `start`, plus the
    forming minute last (never read)."""
    rows = []
    t0 = dt.datetime.combine(DAY, start, tzinfo=IST)
    for n, (o, h, low, c) in enumerate(candles):
        for k in range(size):
            ts = int((t0 + dt.timedelta(minutes=n * size + k)).timestamp())
            a = o + (c - o) * k / size
            b = o + (c - o) * (k + 1) / size
            hi, lo = max(a, b), min(a, b)
            if k == 1:
                hi = h
            if k == 2:
                lo = low
            rows.append({"ts": ts, "open": a, "high": hi, "low": lo, "close": b, "volume": 1000 * (len(rows) + 1)})
    last = rows[-1]
    rows.append({**last, "ts": last["ts"] + 60, "open": last["close"], "high": last["close"], "low": last["close"]})
    return pd.DataFrame(rows)


DOWN = [(103.0, 103.1, 101.9, 102.0), (102.0, 102.1, 100.9, 101.0), (101.0, 101.1, 99.9, 100.0)]
ENGULF_UP = DOWN + [(100.0, 100.1, 98.9, 99.2), (99.0, 100.6, 98.9, 100.5)]  # small red, then a green that covers it
UP = [(97.0, 98.1, 96.9, 98.0), (98.0, 99.1, 97.9, 99.0), (99.0, 100.1, 98.9, 100.0)]
ENGULF_DOWN = UP + [(100.0, 100.9, 99.9, 100.8), (101.0, 101.1, 99.4, 99.5)]  # small green, then a red that covers it
HAMMER = DOWN + [(99.0, 99.25, 97.5, 99.2)]  # long lower wick after a fall


def _cfg(**over):
    base = dict(pc_entry=True, pc_exit=True, pc_minutes=5, pc_set="STRONG", pc_within=1)
    return NS(**{**base, **over})


def test_entry_needs_a_pattern_of_the_trades_side():
    tape = _tape(ENGULF_UP)
    assert pattern_check.entry_reason(tape, "LONG", _cfg(), 5) is None
    why = pattern_check.entry_reason(tape, "SHORT", _cfg(), 5)
    assert why and "no bearish pattern" in why and "Bullish engulfing" in why
    assert "Bullish engulfing at 10:20" in pattern_check.entry_note(tape, "LONG", _cfg(), 5)
    assert pattern_check.entry_reason(_tape(ENGULF_DOWN), "SHORT", _cfg(), 5) is None


def test_within_reaches_back_a_few_candles():
    tape = _tape(ENGULF_UP + [(100.5, 100.8, 100.2, 100.6)])  # a plain candle after the pattern
    assert pattern_check.entry_reason(tape, "LONG", _cfg(), 5) is not None
    assert pattern_check.entry_reason(tape, "LONG", _cfg(pc_within=2), 5) is None


def test_the_soft_patterns_count_only_with_all():
    tape = _tape(HAMMER)
    assert pattern_check.entry_reason(tape, "LONG", _cfg(), 5) is not None
    assert pattern_check.entry_reason(tape, "LONG", _cfg(pc_set="ALL"), 5) is None


def test_size_zero_reads_the_bots_own_candle_and_too_few_candles_wait():
    tape = _tape(ENGULF_UP)
    assert pattern_check.minutes(_cfg(pc_minutes=0), 5) == 5
    assert pattern_check.entry_reason(tape, "LONG", _cfg(pc_minutes=0), 5) is None
    assert "waiting for 3" in pattern_check.entry_reason(_tape(DOWN[:2]), "LONG", _cfg(), 5)
    assert pattern_check.minutes(_cfg(pc_minutes=7), 15) == 15  # an unknown size reads as the bot's own


def test_exit_reads_only_a_candle_closed_after_the_entry():
    tape = _tape(ENGULF_DOWN)
    last_open = int(tape["ts"].iloc[-2]) - 4 * 60  # the 10:20 candle
    start, note = pattern_check.exit_read(tape, "LONG", _cfg(), 5, last_open + 60)
    assert start == last_open and note and "Bearish engulfing" in note and "against the buy" in note
    # Bought after that candle closed: the pattern came before the trade, so it never closes it.
    assert pattern_check.exit_read(tape, "LONG", _cfg(), 5, last_open + 5 * 60)[1] is None
    # A pattern on the trade's own side never closes it.
    assert pattern_check.exit_read(tape, "SHORT", _cfg(), 5, last_open)[1] is None


def test_defaults_and_bounds():
    empty = NS()
    assert not pattern_check.entry_on(empty) and not pattern_check.exit_on(empty)
    assert pattern_check.pattern_set(NS(pc_set="odd")) == "STRONG"
    assert pattern_check.within(NS(pc_within=99)) == pattern_check.WITHIN_MAX


@pytest.mark.asyncio
async def test_engine_refuses_then_takes_and_closes_on_a_pattern(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from indicators import enrich
    from models import BotConfig
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker, _instant
    from tests.test_sma_atr_terminal import _tape as bot_tape

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    now = dt.datetime.combine(DAY, dt.time(10, 26), tzinfo=IST)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: now)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "ACME"
        row.candle_minutes = 5
        row.pc_entry = True
        row.pc_exit = True
        row.pc_minutes = 5
        db.commit()
    cfg = eng.load_config()
    cfg.symbol = "ACME"
    frame = enrich(bot_tape([100.0 + i * 0.2 for i in range(40)]), cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
    eng._frames["ACME"] = frame
    eng._ltps["ACME"] = float(frame["close"].iloc[-1])
    eng.status = "RUNNING"
    eng._tapes["ACME"] = _tape(ENGULF_DOWN)  # a bearish pattern: no buy
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert "ACME" not in eng.positions and "candle pattern" in text
    eng._tapes["ACME"] = _tape(ENGULF_UP)  # a bullish pattern: the buy goes
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert eng.positions["ACME"].direction == "LONG", text
    # The 10:25 candle then closes as a bearish engulfing: the buy closes on it.
    eng._tapes["ACME"] = _tape(ENGULF_UP + [(100.6, 100.7, 98.8, 98.9)])
    eng._focus = "ACME"
    eng.ltp = 99.0  # the price now (the exit fills there)
    await eng._watch_pattern_exit(cfg)
    assert "ACME" not in eng.positions
    with database.session_factory()() as db:
        from models import TradeLog

        last = db.query(TradeLog).order_by(TradeLog.id.desc()).first()
        assert last.exit_reason == pattern_check.REASON
    database.reset_engine()


def test_api_saves_the_pattern_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"pc_entry", "pc_exit", "pc_minutes", "pc_set", "pc_within"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["pc_entry"] is False and first["pc_exit"] is False
        assert first["pc_minutes"] == 0 and first["pc_set"] == "STRONG" and first["pc_within"] == 1
        ok = client.put("/api/config", json={"pc_entry": True, "pc_exit": True, "pc_minutes": 15, "pc_set": "ALL", "pc_within": 2})
        body = ok.json()
        assert ok.status_code == 200 and body["pc_entry"] and body["pc_exit"]
        assert body["pc_minutes"] == 15 and body["pc_set"] == "ALL" and body["pc_within"] == 2
        assert client.put("/api/config", json={"pc_minutes": 7}).status_code == 422
        assert client.put("/api/config", json={"pc_within": 9}).status_code == 422
    database.reset_engine()
