"""ATR target (atr_target.py): target = entry ± atr_target_mult × ATR, next to an ATR / Supertrend / VWAP stop."""
from __future__ import annotations

import pytest

import atr_target
from tests.test_counter_candles import _first
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)
from tests.test_supertrend import Cfg
from tests.test_vwap_stop import _spike_trades


def test_level_and_when_it_applies():
    cfg = Cfg(use_stop=True, stop_type="ATR", atr_target_mult=3.0)
    assert atr_target.on(cfg)
    assert atr_target.level(cfg, "LONG", 100.0, 2.0) == pytest.approx(106.0)
    assert atr_target.level(cfg, "SHORT", 100.0, 2.0) == pytest.approx(94.0)
    # No ATR formed yet: no target (the trade is never given a made-up one).
    assert atr_target.level(cfg, "LONG", 100.0, 0.0) is None
    assert atr_target.level(cfg, "LONG", 100.0, None) is None
    assert atr_target.level(cfg, "LONG", 100.0, float("nan")) is None
    # Off by default, off with the stop off, and the trailing / SMA-gap stops keep their own targets.
    assert not atr_target.on(Cfg(use_stop=True, stop_type="ATR"))
    assert not atr_target.on(Cfg(use_stop=True, stop_type="ATR", atr_target_mult=0))
    assert not atr_target.on(Cfg(use_stop=False, stop_type="ATR", atr_target_mult=3.0))
    assert not atr_target.on(Cfg(use_stop=True, stop_type="TSL", atr_target_mult=3.0))
    assert not atr_target.on(Cfg(use_stop=True, stop_type="SMA_GAP", atr_target_mult=3.0))
    assert atr_target.on(Cfg(use_stop=True, stop_type="SUPERTREND", atr_target_mult=3.0))
    assert atr_target.on(Cfg(use_stop=True, stop_type="VWAP", atr_target_mult=3.0))
    assert atr_target.mult(Cfg(atr_target_mult="x")) == 0.0 and atr_target.mult(Cfg(atr_target_mult=999)) == atr_target.MAX_MULT


@pytest.mark.asyncio
async def test_the_short_is_closed_at_its_atr_target(db):  # noqa: F811
    plain = _first(await _spike_trades(db, use_stop=True, stop_type="ATR"), "SHORT")
    booked = _first(await _spike_trades(db, use_stop=True, stop_type="ATR", atr_target_mult=3.0), "SHORT")
    assert booked["entry_time"] == plain["entry_time"] and booked["entry_price"] == plain["entry_price"]
    assert plain["exit_reason"] != "TARGET_HIT"
    assert booked["exit_reason"] == "TARGET_HIT"
    assert booked["exit_time"] < plain["exit_time"]
    # Booked in profit, and twice the multiple books twice the distance (same entry, same ATR).
    gain = booked["entry_price"] - booked["exit_price"]
    assert gain > 0
    far = _first(await _spike_trades(db, use_stop=True, stop_type="ATR", atr_target_mult=6.0), "SHORT")
    assert far["exit_reason"] == "TARGET_HIT" and far["exit_time"] > booked["exit_time"]
    assert far["entry_price"] - far["exit_price"] == pytest.approx(2 * gain, rel=0.2)
    # The trailing stop keeps its own target: the ATR target is not applied on top.
    tsl = _first(await _spike_trades(db, use_stop=True, stop_type="TSL", tsl_sl_points=500, tsl_trail_points=500, tsl_target_points=0, atr_target_mult=3.0), "SHORT")
    assert tsl["exit_reason"] != "TARGET_HIT"

def test_api_saves_the_atr_target(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import SNAPSHOT_FIELDS, STOCK_FIELDS

    assert "atr_target_mult" in STOCK_FIELDS and "atr_target_mult" in SNAPSHOT_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["atr_target_mult"] == 0.0
        ok = client.put("/api/config", json={"atr_target_mult": 3})
        assert ok.status_code == 200 and ok.json()["atr_target_mult"] == 3.0
        assert client.put("/api/config", json={"atr_target_mult": 99}).status_code == 422
        assert client.put("/api/config", json={"atr_target_mult": -1}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"atr_target_mult": 2})
        assert own.status_code == 200
