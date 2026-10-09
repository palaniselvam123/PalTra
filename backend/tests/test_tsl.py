"""Groww-style trailing stop (stop_type TSL): ₹ stop, ₹ trail step, optional ₹ target."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from tsl import tsl_entry_levels, tsl_stop, uses_tsl

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 9, 29, 10, 30, tzinfo=IST)
T0 = int(dt.datetime(2026, 9, 29, 9, 15, tzinfo=IST).timestamp())


def test_stop_moves_one_step_per_full_step_gained():
    # Buy 1,106.50, stop ₹20 below, trail every ₹10.
    assert tsl_stop("LONG", 1106.5, 20, 10, 1106.5) == pytest.approx(1086.5)
    assert tsl_stop("LONG", 1106.5, 20, 10, 1116.4) == pytest.approx(1086.5)  # 9.90 gained: no step
    assert tsl_stop("LONG", 1106.5, 20, 10, 1116.5) == pytest.approx(1096.5)  # one step
    assert tsl_stop("LONG", 1106.5, 20, 10, 1131.0) == pytest.approx(1106.5)  # two steps
    assert tsl_stop("LONG", 1106.5, 20, 10, 1090.0) == pytest.approx(1086.5)  # a loss never moves it
    # SHORT is the mirror image.
    assert tsl_stop("SHORT", 1106.5, 20, 10, 1106.5) == pytest.approx(1126.5)
    assert tsl_stop("SHORT", 1106.5, 20, 10, 1085.0) == pytest.approx(1106.5)


def test_entry_levels_and_optional_target():
    assert tsl_entry_levels("LONG", 1000.0, 20, 0) == (980.0, None)
    assert tsl_entry_levels("LONG", 1000.0, 20, 50) == (980.0, 1050.0)
    assert tsl_entry_levels("SHORT", 1000.0, 20, 50) == (1020.0, 950.0)


def test_only_tsl_with_a_stop_uses_it():
    assert uses_tsl(SimpleNamespace(stop_type="TSL", use_stop=True)) is True
    assert uses_tsl(SimpleNamespace(stop_type="TSL", use_stop=False)) is False
    assert uses_tsl(SimpleNamespace(stop_type="ATR", use_stop=True)) is False


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


def _cfg(eng, target: float = 0.0):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "RELIANCE"
        row.trade_symbols = "RELIANCE"
        row.qty = 10
        row.use_stop = True
        row.stop_type = "TSL"
        row.tsl_sl_points = 20.0
        row.tsl_trail_points = 10.0
        row.tsl_target_points = target
        db.commit()
    return eng.load_config()


async def _open(eng, cfg, direction="LONG", price=1000.0):
    closes = [price] * 30
    eng.status = "RUNNING"
    eng._focus = "RELIANCE"
    eng._frames["RELIANCE"] = _frame(closes)
    eng._ltps["RELIANCE"] = price
    eng.ltp = price
    await eng._open(direction, price, 2.0, cfg, NOW)
    return eng.positions["RELIANCE"]


async def _tick(eng, cfg, ltp: float) -> None:
    eng._focus = "RELIANCE"
    eng._ltps["RELIANCE"] = ltp
    eng.ltp = ltp
    await eng._trail_tsl(cfg)
    await eng._watch_stop(cfg)


def _saved_stop(trade_id: int) -> float:
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        return db.get(TradeLog, trade_id).sl_trigger_price


@pytest.mark.asyncio
async def test_practice_long_trails_up_never_down_then_stops_out(eng):
    cfg = _cfg(eng)
    pos = await _open(eng, cfg)
    assert pos.sl_trigger == pytest.approx(980.0) and pos.target is None and pos.tsl_step == 10.0
    await _tick(eng, cfg, 1009.0)
    assert pos.sl_trigger == pytest.approx(980.0)
    await _tick(eng, cfg, 1025.0)  # two full steps
    assert pos.sl_trigger == pytest.approx(1000.0)
    assert _saved_stop(pos.trade_id) == pytest.approx(1000.0)
    await _tick(eng, cfg, 1012.0)  # pulls back: the stop stays
    assert pos.sl_trigger == pytest.approx(1000.0) and eng.positions.get("RELIANCE") is pos
    await _tick(eng, cfg, 999.5)  # through the trailed stop
    assert "RELIANCE" not in eng.positions
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        row = db.get(TradeLog, pos.trade_id)
    assert row.exit_reason == "TSL_HIT" and row.exit_price == pytest.approx(999.5)


@pytest.mark.asyncio
async def test_practice_short_trails_down_and_hits_the_target(eng):
    cfg = _cfg(eng, target=50.0)
    pos = await _open(eng, cfg, direction="SHORT")
    assert pos.sl_trigger == pytest.approx(1020.0) and pos.target == pytest.approx(950.0)
    await _tick(eng, cfg, 979.0)
    assert pos.sl_trigger == pytest.approx(1000.0)
    await _tick(eng, cfg, 949.0)
    import database
    from models import TradeLog

    with database.session_factory()() as db:
        row = db.get(TradeLog, pos.trade_id)
    assert row.exit_reason == "TARGET_HIT"
    assert eng.chart_payload()["tsl_step"] is None  # flat again


class _LiveStopBook:
    """Stub broker: records stop modifies. Never talks to Groww."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.moves: list[tuple] = []

    async def modify_sl(self, order_id, symbol, side, qty, trigger):
        if self.fail:
            raise RuntimeError("Groww refused the modify")
        self.moves.append((order_id, symbol, side, qty, trigger))


@pytest.mark.asyncio
async def test_live_trailing_modifies_the_exchange_stop(eng, monkeypatch):
    cfg = _cfg(eng)
    pos = await _open(eng, cfg)
    pos.mode, pos.sl_order_id = "LIVE", "SL123"
    book = _LiveStopBook()
    eng.broker = book
    await eng._trail_tsl(cfg)  # at entry: nothing to move
    eng.ltp = 1031.0
    await eng._trail_tsl(cfg)
    assert book.moves == [("SL123", "RELIANCE", "SELL", 10, 1010.0)]
    assert pos.sl_trigger == pytest.approx(1010.0)
    # A second move inside 2 s waits; the stop is not loosened meanwhile.
    eng.ltp = 1045.0
    await eng._trail_tsl(cfg)
    assert len(book.moves) == 1 and pos.sl_trigger == pytest.approx(1010.0)
    monkeypatch.setattr(pos, "tsl_modified_at", 0.0)
    await eng._trail_tsl(cfg)
    assert book.moves[-1][-1] == pytest.approx(1020.0)


@pytest.mark.asyncio
async def test_a_refused_live_modify_keeps_the_old_stop(eng):
    cfg = _cfg(eng)
    pos = await _open(eng, cfg)
    pos.mode, pos.sl_order_id = "LIVE", "SL123"
    eng.broker = _LiveStopBook(fail=True)
    eng.ltp = 1031.0
    await eng._trail_tsl(cfg)
    assert pos.sl_trigger == pytest.approx(980.0)
    assert _saved_stop(pos.trade_id) == pytest.approx(980.0)
    assert "still live" in eng.last_error


@pytest.mark.asyncio
async def test_live_without_a_known_stop_id_does_not_trail(eng):
    cfg = _cfg(eng)
    pos = await _open(eng, cfg)
    pos.mode, pos.sl_order_id = "LIVE", ""  # restored after a restart
    book = _LiveStopBook()
    eng.broker = book
    eng.ltp = 1031.0
    await eng._trail_tsl(cfg)
    assert book.moves == [] and pos.sl_trigger == pytest.approx(980.0)


def test_config_api_accepts_tsl_and_rejects_a_zero_step(eng):
    from fastapi.testclient import TestClient

    import main

    client = TestClient(main.app)
    ok = client.put(
        "/api/config",
        json={"stop_type": "TSL", "tsl_sl_points": 25, "tsl_trail_points": 5, "tsl_target_points": 60},
    )
    assert ok.status_code == 200, ok.text
    cfg = client.get("/api/config").json()
    assert (cfg["stop_type"], cfg["tsl_sl_points"], cfg["tsl_trail_points"], cfg["tsl_target_points"]) == (
        "TSL",
        25.0,
        5.0,
        60.0,
    )
    assert client.put("/api/config", json={"tsl_trail_points": 0}).status_code == 422


@pytest.mark.asyncio
async def test_live_target_cancels_the_exchange_stop_before_the_exit(eng, monkeypatch):
    cfg = _cfg(eng, target=50.0)
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
    eng.ltp = 1051.0
    eng._ltps["RELIANCE"] = 1051.0
    await eng._watch_stop(cfg)
    assert calls == ["cancel SL123", "close TARGET_HIT 1051.0"]


@pytest.mark.asyncio
async def test_live_target_with_a_stop_that_already_filled_books_the_stop(eng, monkeypatch):
    from strategy_engine import SlCancelFailed

    cfg = _cfg(eng, target=50.0)
    pos = await _open(eng, cfg)
    pos.mode, pos.sl_order_id = "LIVE", "SL123"
    calls: list[str] = []

    async def cancel(p):  # noqa: ARG001
        raise SlCancelFailed("SL SL123 already filled")

    async def close(p, price, reason, now, c):  # noqa: ARG001
        calls.append(f"close {reason} {price}")

    monkeypatch.setattr(eng, "_cancel_sl_verified", cancel)
    monkeypatch.setattr(eng, "_close_position", close)
    eng.ltp = 1051.0
    await eng._watch_stop(cfg)
    assert calls == ["close TSL_HIT 980.0"]


@pytest.mark.asyncio
async def test_groww_client_modifies_the_stop_in_place(monkeypatch):
    """LIVE path with a fake SDK: an SL modify, never a new order or a cancel."""
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    from groww_client import GrowwClient

    sent: list[dict] = []

    class FakeSdk:
        def modify_order(self, **kwargs):
            sent.append(kwargs)
            return {"groww_order_id": kwargs["groww_order_id"], "order_status": "OPEN"}

        def place_order(self, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("modify must not place a new order")

        def cancel_order(self, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("modify must not cancel the stop")

    client = GrowwClient(mode="PAPER")
    client.mode = "LIVE"
    monkeypatch.setattr(client, "_require_sdk", lambda: FakeSdk())
    await client.modify_sl("GSL1", "RELIANCE", "SELL", 10, 1010.0)
    assert len(sent) == 1
    call = sent[0]
    assert (call["order_type"], call["segment"], call["groww_order_id"], call["quantity"]) == ("SL", "CASH", "GSL1", 10)
    assert call["trigger_price"] == pytest.approx(1010.0)
    assert call["price"] < call["trigger_price"]  # SELL stop limit sits below the trigger


# --- Percent mode -------------------------------------------------------------


def test_tsl_mode_defaults_to_points_and_reads_overrides():
    from types import SimpleNamespace

    import tsl as _tsl
    assert _tsl.tsl_mode(SimpleNamespace()) == _tsl.POINTS
    assert _tsl.tsl_mode(SimpleNamespace(tsl_mode="percent")) == _tsl.PERCENT
    assert _tsl.tsl_mode(SimpleNamespace(tsl_mode="junk")) == _tsl.POINTS


def test_tsl_settings_converts_percent_to_rupees_using_entry_price():
    from types import SimpleNamespace

    import tsl as _tsl
    cfg = SimpleNamespace(
        stop_type="TSL",
        use_stop=True,
        tsl_mode="PERCENT",
        tsl_sl_pct=1.0,
        tsl_trail_pct=0.5,
        tsl_target_pct=2.0,
    )
    # ₹1,000 entry: 1% = ₹10, 0.5% = ₹5, 2% = ₹20.
    assert _tsl.tsl_settings(cfg, entry_price=1_000.0) == (10.0, 5.0, 20.0)
    # ₹100 entry: 1% = ₹1, 0.5% = ₹0.5, 2% = ₹2.
    assert _tsl.tsl_settings(cfg, entry_price=100.0) == (1.0, 0.5, 2.0)
    # No target when target_pct is 0.
    cfg.tsl_target_pct = 0.0
    assert _tsl.tsl_settings(cfg, entry_price=500.0) == (5.0, 2.5, 0.0)


def test_tsl_settings_points_mode_is_unchanged_whatever_the_entry_price():
    from types import SimpleNamespace

    import tsl as _tsl
    cfg = SimpleNamespace(tsl_mode="POINTS", tsl_sl_points=20.0, tsl_trail_points=10.0, tsl_target_points=50.0)
    assert _tsl.tsl_settings(cfg, entry_price=1_000.0) == (20.0, 10.0, 50.0)
    assert _tsl.tsl_settings(cfg, entry_price=None) == (20.0, 10.0, 50.0)


def test_tsl_settings_percent_without_entry_falls_back_to_points_for_display():
    from types import SimpleNamespace

    import tsl as _tsl
    cfg = SimpleNamespace(
        tsl_mode="PERCENT",
        tsl_sl_points=20.0,
        tsl_trail_points=10.0,
        tsl_target_points=0.0,
        tsl_sl_pct=1.0,
        tsl_trail_pct=0.5,
        tsl_target_pct=0.0,
    )
    # No entry price (display path, e.g. _stop_points): returns the stored points, never nonsense.
    assert _tsl.tsl_settings(cfg, entry_price=None) == (20.0, 10.0, 0.0)
