"""SMA-gap moving stop and target (PAPER only)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from gap_trail import gap_levels, tighten, uses_gap_stop

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)
T0 = int(dt.datetime(2026, 9, 29, 9, 15, tzinfo=IST).timestamp())


def test_formula_matches_the_worked_example():
    sl, tp = gap_levels("LONG", 1000.0, 0.40, 1.0, 2.0, 0.2)
    assert sl == pytest.approx(996.0) and tp == pytest.approx(1008.0)
    sl, tp = gap_levels("LONG", 1004.0, 0.30, 1.0, 2.0, 0.2)
    assert sl == pytest.approx(1000.988) and tp == pytest.approx(1010.024)
    # SHORT is the mirror image, and the sign of the gap does not matter.
    sl, tp = gap_levels("SHORT", 1000.0, -0.40, 1.0, 2.0, 0.2)
    assert sl == pytest.approx(1004.0) and tp == pytest.approx(992.0)


def test_a_tiny_gap_right_after_a_cross_uses_the_floor():
    sl, tp = gap_levels("LONG", 1000.0, 0.01, 1.0, 2.0, 0.2)
    assert sl == pytest.approx(998.0) and tp == pytest.approx(1004.0)


def test_stop_only_tightens():
    assert tighten("LONG", 996.0, 1000.0) == 1000.0
    assert tighten("LONG", 1000.0, 990.0) == 1000.0
    assert tighten("SHORT", 1004.0, 1001.0) == 1001.0
    assert tighten("SHORT", 1001.0, 1010.0) == 1001.0


def test_live_and_stop_off_never_use_the_gap_stop():
    gap = SimpleNamespace(stop_type="SMA_GAP", use_stop=True)
    assert uses_gap_stop(gap, live=False) is True
    assert uses_gap_stop(gap, live=True) is False
    assert uses_gap_stop(SimpleNamespace(stop_type="SMA_GAP", use_stop=False), live=False) is False
    assert uses_gap_stop(SimpleNamespace(stop_type="ATR", use_stop=True), live=False) is False


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [T0 + 60 * i for i in range(len(closes))],
            "open": closes,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        }
    )


def _gap(frame: pd.DataFrame) -> float:
    from indicators import enrich, sma_gap_pct

    closed = enrich(frame).iloc[-2]
    return sma_gap_pct(closed["sma_9"], closed["sma_21"])


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    engine = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    yield engine
    database.reset_engine()


def _cfg(eng, stop_type: str = "SMA_GAP"):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "RELIANCE"
        row.trade_symbols = "RELIANCE"
        row.qty = 10
        row.use_stop = True
        row.stop_type = stop_type
        row.gap_sl_mult = 1.0
        row.gap_tp_mult = 2.0
        row.gap_min_pct = 0.2
        db.commit()
    return eng.load_config()


# A steady climb: SMA 9 sits well above SMA 21, so the gap is above the floor.
RISING = [1000.0 + 2.0 * i for i in range(40)]


async def _open_long(eng, cfg, closes=RISING):
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    eng._frames["RELIANCE"] = _frame(closes)
    eng._ltps["RELIANCE"] = closes[-1]
    eng.ltp = closes[-1]
    await eng._open("LONG", closes[-2], 2.0, cfg, NOW)
    return eng.positions["RELIANCE"]


@pytest.mark.asyncio
async def test_entry_sets_stop_and_target_from_the_gap(eng):
    cfg = _cfg(eng)
    pos = await _open_long(eng, cfg)
    gap = _gap(eng._frames["RELIANCE"])
    assert gap > 0.2
    sl, tp = gap_levels("LONG", pos.entry_price, gap, 1.0, 2.0, 0.2)
    assert pos.trailing is True
    assert pos.sl_trigger == pytest.approx(sl, abs=0.05)
    assert pos.target == pytest.approx(tp, abs=0.05)
    snap = eng.snapshot()
    assert snap["position"]["trailing"] is True
    assert snap["position"]["target"] == pos.target
    assert eng.chart_payload()["target"] == pos.target


@pytest.mark.asyncio
async def test_atr_stop_type_is_unchanged(eng):
    cfg = _cfg(eng, stop_type="ATR")
    pos = await _open_long(eng, cfg)
    assert pos.trailing is False and pos.target is None
    assert pos.sl_trigger == pytest.approx(pos.entry_price - 3.0)


@pytest.mark.asyncio
async def test_stop_trails_up_never_down_and_target_follows(eng):
    cfg = _cfg(eng)
    pos = await _open_long(eng, cfg)
    first_sl, first_tp = pos.sl_trigger, pos.target

    # Next closed candle higher: the stop and target both move up.
    eng._frames["RELIANCE"] = _frame(RISING + [RISING[-1] + 2.0, RISING[-1] + 3.0])
    eng._trail_gap_levels(cfg)
    assert pos.sl_trigger > first_sl
    assert pos.target > first_tp
    raised, raised_tp = pos.sl_trigger, pos.target

    # A dip on the next closed candle: the target moves down, the stop stays.
    eng._frames["RELIANCE"] = _frame(RISING + [RISING[-1] + 2.0, RISING[-1] - 6.0, RISING[-1] - 6.0])
    eng._trail_gap_levels(cfg)
    assert pos.sl_trigger == raised
    assert pos.target < raised_tp

    # The same candle again changes nothing.
    before = (pos.sl_trigger, pos.target)
    eng._trail_gap_levels(cfg)
    assert (pos.sl_trigger, pos.target) == before

    # The trailed stop is saved for a restart.
    from database import session_factory
    from models import TradeLog

    with session_factory()() as db:
        assert db.get(TradeLog, pos.trade_id).sl_trigger_price == raised


@pytest.mark.asyncio
async def test_target_hit_closes_with_target_reason(eng):
    cfg = _cfg(eng)
    pos = await _open_long(eng, cfg)
    eng._focus = "RELIANCE"
    eng.ltp = pos.target + 0.5
    await eng._watch_stop(cfg)
    assert "RELIANCE" not in eng.positions
    assert eng.trades()[0]["exit_reason"] == "TARGET_HIT"
    assert eng.last_signal == "target hit — flat"


@pytest.mark.asyncio
async def test_moving_stop_hit_closes_with_gap_reason(eng):
    cfg = _cfg(eng)
    pos = await _open_long(eng, cfg)
    eng._focus = "RELIANCE"
    eng.ltp = pos.sl_trigger - 0.5
    await eng._watch_stop(cfg)
    assert "RELIANCE" not in eng.positions
    assert eng.trades()[0]["exit_reason"] == "GAP_SL_HIT"


@pytest.mark.asyncio
async def test_restart_keeps_trailing_from_the_saved_stop(eng):
    cfg = _cfg(eng)
    pos = await _open_long(eng, cfg)
    saved = pos.sl_trigger
    eng.positions.clear()
    eng.restore_open_books()
    again = eng.positions["RELIANCE"]
    assert again.trailing is True
    assert again.sl_trigger == saved
    assert again.target is None  # comes back on the next closed candle


def test_config_api_accepts_the_gap_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/config").json()["stop_type"] == "ATR"
        res = client.put(
            "/api/config",
            json={"stop_type": "SMA_GAP", "gap_sl_mult": 1.5, "gap_tp_mult": 3, "gap_min_pct": 0.25},
        )
        assert res.status_code == 200
        body = res.json()
        assert (body["stop_type"], body["gap_sl_mult"], body["gap_tp_mult"], body["gap_min_pct"]) == (
            "SMA_GAP",
            1.5,
            3.0,
            0.25,
        )
        assert client.put("/api/config", json={"stop_type": "BOTH"}).status_code == 422
        assert client.put("/api/config", json={"gap_sl_mult": 0}).status_code == 422
    database.reset_engine()
    get_settings.cache_clear()
