"""Each stock can carry its own strategy settings. PAPER only; no order reaches Groww."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from groww_client import IST
from indicators import enrich
from models import BotConfig
from strategy_engine import STOCK_FIELDS, _cfg_for, settings_for, stock_settings
from tests.test_sma_atr_terminal import _FakeBroker, _instant, _seed_open_trade, _tape, api  # noqa: F401


def _row(**over) -> BotConfig:
    base = {col.name: col.default.arg if col.default is not None else None for col in BotConfig.__table__.columns}
    base.update(over)
    return BotConfig(**base)


def test_a_stock_reads_its_own_settings_over_the_shared_ones():
    row = _row(qty=1000, atr_multiplier=1.5, stock_settings=json.dumps({"tcs": {"qty": 25, "atr_multiplier": 2.5}}))
    tcs = _cfg_for(row, "TCS")
    assert (tcs.qty, tcs.atr_multiplier, tcs.symbol) == (25, 2.5, "TCS")
    infy = _cfg_for(row, "INFY")
    assert (infy.qty, infy.atr_multiplier) == (1000, 1.5)
    assert row.qty == 1000  # the shared row is not changed


def test_account_wide_fields_cannot_be_set_per_stock():
    assert "max_daily_loss" not in STOCK_FIELDS and "square_off_time" not in STOCK_FIELDS
    row = _row(
        max_daily_loss=5000,
        stock_settings=json.dumps({"TCS": {"max_daily_loss": 1, "trading_mode": "LIVE", "square_off_time": "09:30"}}),
    )
    tcs = _cfg_for(row, "TCS")
    assert tcs.max_daily_loss == 5000 and tcs.trading_mode == "PAPER" and tcs.square_off_time == "15:15"
    assert stock_settings(row) == {}


def test_bad_json_is_ignored():
    assert stock_settings(_row(stock_settings="not json")) == {}
    assert _cfg_for(_row(stock_settings="[1, 2]"), "TCS").qty == 1000


def test_a_replay_snapshot_applies_its_own_stock_settings():
    from types import SimpleNamespace

    snap = SimpleNamespace(qty=10, sma_fast=9, stock_settings={"TCS": {"sma_fast": 5}})
    assert settings_for(snap, "TCS").sma_fast == 5
    assert settings_for(snap, "INFY") is snap


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database

    database.reset_engine()
    database.init_db()
    from strategy_engine import StrategyEngine

    eng = StrategyEngine(broker=_FakeBroker())
    eng._sleep = _instant
    return eng


@pytest.mark.asyncio
async def test_each_stock_is_judged_and_sized_with_its_own_settings(engine):
    """Same cross, two stocks: VWAP is on for one only, and the sizes differ."""
    cfg = engine.load_config()
    cfg.use_adx_filter = False
    cfg.use_vwap = False
    cfg.qty = 1
    cfg.stock_settings = json.dumps({"BLOCKED": {"use_vwap": True}, "SIZED": {"qty": 7}})
    frame = enrich(_tape([200.0] * 24 + [100.0, 100.0]))
    now = dt.datetime(2026, 9, 30, 11, 0, tzinfo=IST)

    blocked = await engine.apply_signal("BULLISH", frame, _cfg_for(cfg, "BLOCKED"), now)
    assert "VWAP" in blocked and "BLOCKED" not in engine.positions

    opened = await engine.apply_signal("BULLISH", frame, _cfg_for(cfg, "SIZED"), now)
    assert "opened LONG" in opened
    assert engine.positions["SIZED"].qty == 7


def test_the_api_saves_resets_and_refuses_shared_fields(api):  # noqa: F811
    api.put("/api/config", json={"qty": 100, "atr_multiplier": 1.5})
    saved = api.put("/api/config/stock/tcs", json={"qty": 20, "atr_multiplier": 1.5, "use_rsi": True})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    # A value equal to the shared one is not stored; the stock keeps following it.
    assert body["own"] == {"qty": 20, "use_rsi": True}
    assert body["qty"] == 20 and body["symbol"] == "TCS"
    config = api.get("/api/config").json()
    assert config["qty"] == 100 and config["stock_settings"] == {"TCS": {"qty": 20, "use_rsi": True}}
    assert api.get("/api/config/stock/TCS").json()["use_rsi"] is True
    assert api.get("/api/state").json()["stock_settings"]["TCS"]["qty"] == 20

    refused = api.put("/api/config/stock/TCS", json={"max_daily_loss": 10})
    assert refused.status_code == 400 and "every stock" in refused.json()["detail"]
    bad = api.put("/api/config/stock/TCS", json={"sma_fast": 30})
    assert bad.status_code == 400 and "TCS" in bad.json()["detail"]
    # The shared Save checks every stock's own settings too.
    api.put("/api/config/stock/TCS", json={"sma_slow": 12})
    clash = api.put("/api/config", json={"sma_fast": 15, "sma_slow": 40})
    assert clash.status_code == 400 and "TCS" in clash.json()["detail"]

    reset = api.delete("/api/config/stock/TCS")
    assert reset.status_code == 200 and reset.json()["own"] == {} and reset.json()["qty"] == 100
    assert api.get("/api/config").json()["stock_settings"] == {}
