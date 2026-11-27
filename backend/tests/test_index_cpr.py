"""Nifty direction check (index_check.py), CPR / previous-day levels (pivots.py, level_check.py) and the scan columns."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import chop_scan
import index_check
import level_check
import pivots
from groww_client import IST

DAY = dt.date(2026, 9, 29)


def _tape(day_steps: list[float], start: float = 100.0, minutes: int = 200) -> pd.DataFrame:
    """1-minute rows over len(day_steps) sessions; each session climbs `step` a minute from where the last ended."""
    rows, price = [], start
    first = dt.datetime.combine(DAY - dt.timedelta(days=len(day_steps) - 1), dt.time(9, 15), tzinfo=IST)
    for d, step in enumerate(day_steps):
        for i in range(minutes):
            ts = int((first + dt.timedelta(days=d, minutes=i)).timestamp())
            nxt = price + step
            rows.append({"ts": ts, "open": price, "high": max(price, nxt) + 0.01, "low": min(price, nxt) - 0.01, "close": nxt, "volume": 1000 * (i + 1)})
            price = nxt
    return pd.DataFrame(rows)


def test_nifty_rules():
    up, down = _tape([0.0, 0.05]), _tape([0.0, -0.05])
    cfg = NS(use_index=True, index_rule="BOTH", sma_fast=9, sma_slow=21)
    assert index_check.entry_reason(up, "LONG", cfg, 5) is None
    assert "against a sell" in index_check.entry_reason(up, "SHORT", cfg, 5)
    assert index_check.entry_reason(down, "SHORT", cfg, 5) is None
    assert "against a buy" in index_check.entry_reason(down, "LONG", cfg, 5)
    assert index_check.day_move(up) > 0 > index_check.day_move(down)
    # Down on the day but turned up: the averages already point up while the day is still red.
    turned = _tape([0.0, -0.05], minutes=120)
    more = _tape([0.05], start=float(turned["close"].iloc[-1]), minutes=60)
    more["ts"] = more["ts"] + 120 * 60
    mixed = pd.concat([turned, more], ignore_index=True)
    assert index_check.entry_reason(mixed, "LONG", NS(index_rule="SMA", sma_fast=9, sma_slow=21), 5) is None
    assert "on the day" in index_check.entry_reason(mixed, "LONG", NS(index_rule="DAY", sma_fast=9, sma_slow=21), 5)
    assert index_check.entry_reason(mixed, "LONG", cfg, 5) is not None


def test_nifty_not_loaded_refuses_and_defaults():
    cfg = NS(use_index=True, index_rule="odd", sma_fast=9, sma_slow=21)
    assert index_check.rule(cfg) == "SMA" and not index_check.on(NS())
    assert "not formed" in index_check.entry_reason(None, "LONG", cfg, 5)
    assert "not loaded" in index_check.entry_reason(None, "LONG", NS(index_rule="DAY"), 5)
    assert index_check.wanted({"use_index": True}) and index_check.wanted({"stock_settings": {"SBIN": {"use_index": True}}})
    assert not index_check.wanted({"stock_settings": {"SBIN": {"use_vwap": True}}}) and not index_check.wanted(None)


def test_cpr_and_previous_day_levels():
    frame = _tape([0.05, 0.0])
    lv = pivots.levels(frame, DAY)
    prev = frame[frame["ts"] < int(dt.datetime.combine(DAY, dt.time(0, 0), tzinfo=IST).timestamp())]
    high, low, close = prev["high"].max(), prev["low"].min(), prev["close"].iloc[-1]
    p, bc = (high + low + close) / 3, (high + low) / 2
    assert lv["pdh"] == round(high, 2) and lv["pdl"] == round(low, 2)
    assert lv["cpr_low"] == round(min(bc, 2 * p - bc), 2) and lv["cpr_high"] == round(max(bc, 2 * p - bc), 2)
    assert lv["cpr_width_pct"] == pytest.approx(abs(2 * p - 2 * bc) / p * 100, abs=1e-3)
    assert lv["p"] == round(p, 2)  # the classic levels are unchanged


def test_level_check_reads_cpr_and_yesterdays_high():
    # Yesterday rose 100 -> 110; today sits flat at 110, just under yesterday's high and above the CPR.
    frame = _tape([0.05, 0.0])
    assert level_check.entry_reason(frame, "LONG", NS(level_line="CPR")) is None
    assert "CPR bottom" in level_check.entry_reason(frame, "SHORT", NS(level_line="CPR"))
    assert "yesterday's high" in level_check.entry_reason(frame, "LONG", NS(level_line="PREV_DAY"))
    broke = _tape([0.05, 0.05])
    assert level_check.entry_reason(broke, "LONG", NS(level_line="PREV_DAY")) is None
    assert "yesterday's low" in level_check.entry_reason(broke, "SHORT", NS(level_line="PREV_DAY"))


def test_scan_rows_carry_cpr_and_the_move_against_nifty():
    frame = _tape([0.03, 0.03, 0.03], minutes=375)
    now = dt.datetime.fromtimestamp(int(frame["ts"].iloc[-1]) + 60, IST)
    row = chop_scan.analyze(frame, 9, 21, 5, now, symbol="ACME")
    assert row.cpr_width_pct is not None and row.cpr_side == "ABOVE" and row.prev_side == "ABOVE"
    assert row.prev_high < row.ltp and row.rs_pct is None
    chop_scan.with_index([row], 0.5)
    assert row.index_move_pct == 0.5 and row.rs_pct == pytest.approx(row.day_move_pct - 0.5, abs=1e-3)
    chop_scan.with_index([row], None)  # Nifty unreadable: nothing changes
    assert row.index_move_pct == 0.5


@pytest.mark.asyncio
async def test_engine_refuses_a_buy_while_nifty_falls(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/i.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from indicators import enrich
    from models import BotConfig
    from strategy_engine import STOCK_FIELDS, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker, _instant
    from tests.test_sma_atr_terminal import _tape as bot_tape

    assert {"use_index", "index_rule"} <= set(STOCK_FIELDS)
    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    now = dt.datetime.combine(DAY, dt.time(11, 0), tzinfo=IST)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: now)
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "ACME"
        row.use_index = True
        row.index_rule = "BOTH"
        db.commit()
    cfg = eng.load_config()
    cfg.symbol = "ACME"
    frame = enrich(bot_tape([100.0 + i * 0.2 for i in range(40)]), cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
    eng._frames["ACME"] = frame
    eng._tapes["ACME"] = frame
    eng._ltps["ACME"] = float(frame["close"].iloc[-1])
    eng.status = "RUNNING"
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)  # Nifty never read
    assert "ACME" not in eng.positions and "Nifty check" in text
    eng._index_tape = _tape([0.0, -0.05])
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert "ACME" not in eng.positions and "against a buy" in text
    eng._index_tape = _tape([0.0, 0.05])
    text = await eng.apply_signal("BULLISH", frame, cfg, now, alert=False)
    assert eng.positions["ACME"].direction == "LONG", text
    database.reset_engine()


def test_api_saves_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_index"] is False and first["index_rule"] == "SMA"
        body = client.put("/api/config", json={"use_index": True, "index_rule": "DAY", "use_level": True, "level_line": "CPR"}).json()
        assert body["use_index"] is True and body["index_rule"] == "DAY" and body["level_line"] == "CPR"
        assert client.put("/api/config", json={"level_line": "PREV_DAY"}).json()["level_line"] == "PREV_DAY"
        assert client.put("/api/config", json={"index_rule": "VWAP"}).status_code == 422
    database.reset_engine()


@pytest.mark.asyncio
async def test_a_pass_reads_nifty_only_while_the_check_is_on_and_never_watches_it(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database
    from models import BotConfig
    from strategy_engine import StrategyEngine

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine()  # the built-in simulator: no Groww session
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "SBIN"
        row.symbol = "SBIN"
        db.commit()
    now = dt.datetime.combine(DAY, dt.time(11, 0), tzinfo=IST)
    await eng.tick(now)
    assert eng._index_tape is None  # the check is off: Nifty is never asked for
    with database.session_factory()() as db:
        db.get(BotConfig, 1).use_index = True
        db.commit()
    await eng.tick(now)
    assert eng._index_tape is not None and len(eng._index_tape) > 0
    assert index_check.INDEX not in eng._tapes and index_check.INDEX not in eng._frames
    assert index_check.INDEX not in eng.positions
    database.reset_engine()
