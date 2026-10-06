"""Bollinger exit (bb_exit): take profit at the far band, or leave when a close
falls back across the middle band. Off by default."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from indicators import bollinger_exit

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)
T0 = int(dt.datetime(2026, 9, 29, 9, 15, tzinfo=IST).timestamp())
# 9:15 .. 10:29 closed before the 10:30 entry: a choppy base around 1000.
BASE = [1000 + (0.4 if i % 2 else -0.4) for i in range(75)]


def test_band_exit_takes_the_profit_at_the_far_band():
    closes = pd.Series(BASE + [1012.0])
    assert bollinger_exit(closes, "LONG", "BAND", 20, 2.0, False)[0] == "BB_TARGET"
    assert bollinger_exit(closes, "SHORT", "BAND", 20, 2.0, False)[0] is None
    low = pd.Series(BASE + [988.0])
    assert bollinger_exit(low, "SHORT", "BOTH", 20, 2.0, False)[0] == "BB_TARGET"
    assert bollinger_exit(closes, "LONG", "OFF", 20, 2.0, False)[0] is None
    assert bollinger_exit(closes, "LONG", "MIDDLE", 20, 2.0, False)[0] is None


def test_middle_exit_waits_for_a_close_on_the_trade_side_first():
    below = pd.Series(BASE + [999.5])
    reason, _note, armed = bollinger_exit(below, "LONG", "MIDDLE", 20, 2.0, False)
    assert reason is None and armed is False  # never above the middle yet: no exit
    above = pd.Series(BASE + [1000.8])
    assert bollinger_exit(above, "LONG", "MIDDLE", 20, 2.0, False) == (None, "", True)
    reason, note, _ = bollinger_exit(pd.Series(BASE + [1000.8, 999.5]), "LONG", "MIDDLE", 20, 2.0, True)
    assert reason == "BB_MIDDLE" and "middle band" in note
    # A SHORT is the mirror image.
    assert bollinger_exit(pd.Series(BASE + [999.2, 1000.6]), "SHORT", "MIDDLE", 20, 2.0, True)[0] == "BB_MIDDLE"


def _frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ts": [T0 + 60 * i for i in range(len(closes))],
            "open": closes,
            "high": [c + 0.1 for c in closes],
            "low": [c - 0.1 for c in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        }
    )


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


def _cfg(eng, mode: str):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "RELIANCE"
        row.trade_symbols = "RELIANCE"
        row.qty = 10
        row.use_stop = True
        row.stop_type = "ATR"
        row.atr_multiplier = 5.0
        row.bb_exit = mode
        db.commit()
    return eng.load_config()


async def _open(eng, cfg, direction="LONG", price=1000.0):
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    eng._frames["RELIANCE"] = _frame(BASE + [price])
    eng._ltps["RELIANCE"] = price
    eng.ltp = price
    await eng._open(direction, price, 2.0, cfg, NOW)
    return eng.positions["RELIANCE"]


async def _candle(eng, cfg, closed: list[float], ltp: float) -> None:
    """Closed candles after the entry, then a forming bar at ltp."""
    eng._focus = "RELIANCE"
    eng._frames["RELIANCE"] = _frame(BASE + closed + [ltp])
    eng._ltps["RELIANCE"] = ltp
    eng.ltp = ltp
    await eng._watch_stop(cfg)
    await eng._watch_bollinger(cfg)


def _row(trade_id: int):
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        return db.get(TradeLog, trade_id)


@pytest.mark.asyncio
async def test_off_by_default_never_exits(eng):
    cfg = _cfg(eng, "OFF")
    pos = await _open(eng, cfg)
    await _candle(eng, cfg, [1012.0], 1012.0)
    assert eng.positions.get("RELIANCE") is pos


@pytest.mark.asyncio
async def test_practice_long_takes_the_band_target(eng):
    cfg = _cfg(eng, "BAND")
    pos = await _open(eng, cfg)
    await _candle(eng, cfg, [1000.5], 1000.5)  # inside the bands: holds
    assert eng.positions.get("RELIANCE") is pos
    await _candle(eng, cfg, [1000.5, 1012.0], 1011.5)
    assert "RELIANCE" not in eng.positions
    row = _row(pos.trade_id)
    assert row.exit_reason == "BB_TARGET" and row.exit_price == pytest.approx(1011.5)
    assert eng.last_signal == "Bollinger band target — flat"


@pytest.mark.asyncio
async def test_the_cross_candle_before_the_entry_is_not_read(eng):
    cfg = _cfg(eng, "BAND")
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    # The 10:29 candle closed far above the band, then the entry at 10:30.
    eng._frames["RELIANCE"] = _frame(BASE[:-1] + [1012.0, 1012.0])
    eng._ltps["RELIANCE"] = eng.ltp = 1012.0
    await eng._open("LONG", 1012.0, 2.0, cfg, NOW)
    await eng._watch_bollinger(cfg)
    assert "RELIANCE" in eng.positions


@pytest.mark.asyncio
async def test_practice_middle_exit_after_the_move_fades(eng):
    cfg = _cfg(eng, "MIDDLE")
    pos = await _open(eng, cfg)
    await _candle(eng, cfg, [999.7], 999.7)  # below the middle, never above yet: holds
    assert eng.positions.get("RELIANCE") is pos and pos.bb_armed is False
    await _candle(eng, cfg, [999.7, 1000.9], 1000.9)
    assert pos.bb_armed is True
    await _candle(eng, cfg, [999.7, 1000.9, 999.4], 999.4)
    assert "RELIANCE" not in eng.positions
    assert _row(pos.trade_id).exit_reason == "BB_MIDDLE"


@pytest.mark.asyncio
async def test_live_cancels_the_exchange_stop_before_the_bollinger_exit(eng, monkeypatch):
    cfg = _cfg(eng, "BAND")
    pos = await _open(eng, cfg)
    pos.mode, pos.sl_order_id = "LIVE", "SL123"
    calls: list[str] = []

    async def cancel(p):
        calls.append(f"cancel {p.sl_order_id}")
        p.sl_order_id = ""

    async def close(p, price, reason, now, c):  # noqa: ARG001
        calls.append(f"close {reason} {price}")

    monkeypatch.setattr(eng, "_cancel_sl_verified", cancel)
    monkeypatch.setattr(eng, "_close_position", close)
    eng._frames["RELIANCE"] = _frame(BASE + [1012.0, 1011.0])
    eng.ltp = eng._ltps["RELIANCE"] = 1011.0
    await eng._watch_bollinger(cfg)
    assert calls == ["cancel SL123", "close BB_TARGET 1011.0"]


@pytest.mark.asyncio
async def test_live_position_restored_without_its_stop_id_is_left_to_the_stop(eng, monkeypatch):
    cfg = _cfg(eng, "BAND")
    pos = await _open(eng, cfg)
    pos.mode, pos.sl_order_id = "LIVE", ""

    async def close(*_a, **_k):
        raise AssertionError("no exit may be sent")

    monkeypatch.setattr(eng, "_close_position", close)
    eng._frames["RELIANCE"] = _frame(BASE + [1012.0, 1011.0])
    eng.ltp = eng._ltps["RELIANCE"] = 1011.0
    await eng._watch_bollinger(cfg)
    assert eng.positions.get("RELIANCE") is pos


def test_api_saves_the_exit_mode(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "bb_exit" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["bb_exit"] == "OFF"
        ok = client.put("/api/config", json={"bb_exit": "BOTH"})
        assert ok.status_code == 200 and ok.json()["bb_exit"] == "BOTH"
        assert client.put("/api/config", json={"bb_exit": "SOMETIMES"}).status_code == 422
    database.reset_engine()
