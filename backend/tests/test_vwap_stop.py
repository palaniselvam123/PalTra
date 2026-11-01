"""VWAP + ATR buffer stop (vwap_stop.py, stop_type VWAP): stop = VWAP ∓ k × ATR, tighter only each candle."""
from __future__ import annotations

import pandas as pd
import pytest

import vwap_stop
from tests.test_counter_candles import _first
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)
from tests.test_supertrend import Cfg, _trades


def _closed(closes, vol=100.0, atr=2.0):
    rows = []
    for i, c in enumerate(closes):
        rows.append({"ts": 1_780_000_000 + i * 60, "open": c, "high": c + 1, "low": c - 1, "close": c, "minute_volume": vol, "atr_14": atr})
    return pd.DataFrame(rows)


def test_stop_on_buffer_and_level():
    assert vwap_stop.stop_on(Cfg(use_stop=True, stop_type="VWAP"))
    assert not vwap_stop.stop_on(Cfg(use_stop=False, stop_type="VWAP"))
    assert not vwap_stop.stop_on(Cfg(use_stop=True, stop_type="ATR"))
    assert vwap_stop.buffer(Cfg()) == 0.5 and vwap_stop.buffer(Cfg(vwap_stop_atr=99)) == 5.0 and vwap_stop.buffer(Cfg(vwap_stop_atr="x")) == 0.5
    closed = _closed([100.0] * 10)  # flat: VWAP = typical price = 100
    cfg = Cfg(vwap_stop_atr=0.5)
    assert vwap_stop.level(closed, "LONG", cfg) == pytest.approx(99.0)  # 100 − 0.5 × 2
    assert vwap_stop.level(closed, "SHORT", cfg) == pytest.approx(101.0)
    # Only when it guards the position against the price.
    assert vwap_stop.stop_level(closed, "LONG", cfg, price=103.0) == pytest.approx(99.0)
    assert vwap_stop.stop_level(closed, "LONG", cfg, price=98.0) is None
    assert vwap_stop.stop_level(closed, "SHORT", cfg, price=98.0) == pytest.approx(101.0)
    assert vwap_stop.stop_level(closed, "SHORT", cfg, price=105.0) is None
    assert vwap_stop.level(_closed([100.0] * 3).drop(columns=["atr_14"]), "LONG", cfg) is None
    assert vwap_stop.level(None, "LONG", cfg) is None


def _spike_day() -> pd.DataFrame:
    """Flat morning, a steady fall (bearish cross), then one sharp jump back up through VWAP at 12:15, then flat:
    the jump hits VWAP + ½ ATR on the price before the SMAs can cross back."""
    import datetime as dt

    from tests.test_gap_mode import _day

    def path(i, p):
        if i < 60:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        if i < 180:
            return p - 0.15
        if i == 180:
            return p + 14.0
        return p + (0.02 if i % 2 else -0.02)

    flat = _day(dt.date(2026, 9, 21), lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02))
    return pd.DataFrame(flat + _day(dt.date(2026, 9, 22), path))


async def _spike_trades(database, **over):
    import datetime as dt

    from sma_research.replayer import replay_symbol

    return await replay_symbol(_spike_day(), "SIMV", [dt.date(2026, 9, 22)], _settings(database, **over))


@pytest.mark.asyncio
async def test_vwap_stop_closes_the_short_when_the_price_jumps_back_through_vwap(db):  # noqa: F811
    import datetime as dt

    plain = _first(await _spike_trades(db), "SHORT")
    stopped = _first(await _spike_trades(db, use_stop=True, stop_type="VWAP", vwap_stop_atr=0.5), "SHORT")
    assert stopped["entry_time"] == plain["entry_time"]
    assert stopped["exit_reason"] == vwap_stop.HIT_REASON
    # The jump at 12:15 crossed VWAP + ½ ATR on the price, before the SMAs crossed back.
    assert dt.datetime(2026, 9, 22, 12, 15) <= stopped["exit_time"] <= plain["exit_time"]
    assert stopped["exit_price"] > stopped["entry_price"] - 20  # stopped near VWAP, not at the bottom


def test_api_saves_the_vwap_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "vwap_stop_atr" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["vwap_stop_atr"] == 0.5
        ok = client.put("/api/config", json={"stop_type": "VWAP", "vwap_stop_atr": 0.75})
        assert ok.status_code == 200 and ok.json()["stop_type"] == "VWAP" and ok.json()["vwap_stop_atr"] == 0.75
        assert client.put("/api/config", json={"vwap_stop_atr": 9}).status_code == 422
        assert client.get("/api/state").json()["stop_points"]["vwap_stop_atr"] == 0.75
    database.reset_engine()
