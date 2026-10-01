"""Tests for the SMA(9, 21) + 1.5× ATR terminal invariants."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from charges import calculate_charges
from indicators import closed_candle_cross, enrich, round_to_nse_tick

IST = ZoneInfo("Asia/Kolkata")


def test_groww_charge_breakdown_matches_schedule():
    # 1000 shares, buy 100 / sell 101.
    out = calculate_charges(100, 101, 1000)
    assert out["brokerage"] == pytest.approx(40.0)
    assert out["stt"] == pytest.approx(25.25)
    assert out["exchange_charge"] == pytest.approx(0.0000297 * 201_000)
    assert out["sebi_fee"] == pytest.approx(0.201)
    assert out["stamp_duty"] == pytest.approx(3.0)
    gst_base = out["brokerage"] + out["exchange_charge"] + out["sebi_fee"]
    assert out["gst"] == pytest.approx(0.18 * gst_base)
    assert out["total_charges"] == pytest.approx(
        out["brokerage"] + out["stt"] + out["exchange_charge"] + out["sebi_fee"] + out["stamp_duty"] + out["gst"]
    )
    assert out["gross_pnl"] == pytest.approx(1000.0)
    assert out["net_pnl"] == pytest.approx(1000.0 - out["total_charges"])


def test_brokerage_is_capped_at_20_per_leg():
    out = calculate_charges(10, 10, 10)
    # turnover 100, 0.05% = 0.05, under the cap
    assert out["brokerage_buy"] == pytest.approx(0.05)
    big = calculate_charges(500, 500, 1000)
    assert big["brokerage_buy"] == 20.0
    assert big["brokerage_sell"] == 20.0


def test_nse_tick_rounds_to_five_paise():
    assert round_to_nse_tick(100.02) == 100.0
    assert round_to_nse_tick(100.03) == 100.05
    assert round_to_nse_tick(100.13) == 100.15


def _ohlcv(closes: list[float]) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(closes):
        rows.append(
            {
                "ts": 1_700_000_000 + i * 60,
                "open": c,
                "high": c + 0.5,
                "low": c - 0.5,
                "close": c,
                "volume": 1000,
            }
        )
    return pd.DataFrame(rows)


def test_atr_wilder_is_nan_until_period():
    df = enrich(_ohlcv([100 + i * 0.1 for i in range(20)]), atr_period=14)
    assert pd.isna(df.iloc[12]["atr_14"])
    assert pd.notna(df.iloc[14]["atr_14"])


def test_cross_ignores_forming_bar():
    closes = [100.0] * 40
    # Lift the last CLOSED bar so a cross can exist, then smash the forming bar.
    closes[-2] = 140.0
    df = enrich(_ohlcv(closes))
    smashed = df.copy()
    smashed.loc[smashed.index[-1], "close"] = 1.0
    smashed = enrich(smashed)
    assert closed_candle_cross(df) == closed_candle_cross(smashed)
    # And the signal, whatever it is, agrees with iloc[-3] vs iloc[-2] only.
    prev, curr = smashed.iloc[-3], smashed.iloc[-2]
    if prev.sma_9 <= prev.sma_21 and curr.sma_9 > curr.sma_21:
        assert closed_candle_cross(smashed) == "BULLISH"
    elif prev.sma_9 >= prev.sma_21 and curr.sma_9 < curr.sma_21:
        assert closed_candle_cross(smashed) == "BEARISH"
    else:
        assert closed_candle_cross(smashed) is None


def test_forming_spike_does_not_invent_a_cross():
    """A spike only on iloc[-1] must not create a signal the closed bars lack."""
    closes = [100.0] * 40
    quiet = enrich(_ohlcv(closes))
    assert closed_candle_cross(quiet) is None
    spiked = quiet.copy()
    spiked.loc[spiked.index[-1], "close"] = 500.0
    spiked = enrich(spiked)
    assert closed_candle_cross(spiked) is None


class _FakeBroker:
    def __init__(self):
        self.events: list[str] = []
        self.sl_status = "TRIGGER_PENDING"
        self.mode = "PAPER"

    def set_mode(self, mode, token=None):
        self.mode = mode

    async def place_entry(self, symbol, side, qty, ltp):
        self.events.append(f"ENTRY {side}")
        from groww_client import OrderAck

        return OrderAck("E1", "FILLED", ltp)

    async def place_exit(self, symbol, side, qty, ltp):
        self.events.append(f"EXIT {side}")
        from groww_client import OrderAck

        return OrderAck("X1", "FILLED", ltp)

    async def place_sl(self, symbol, side, qty, trigger):
        self.events.append(f"SL {side} {trigger}")
        from groww_client import OrderAck

        self.sl_status = "TRIGGER_PENDING"
        return OrderAck("SL1", "TRIGGER_PENDING")

    async def cancel_order(self, order_id):
        self.events.append(f"CANCEL {order_id}")
        if self.sl_status != "FILLED":
            self.sl_status = "CANCELLED"

    async def get_order_status(self, order_id):
        return self.sl_status


def _bullish_frame() -> pd.DataFrame:
    # Flat, then a jump on the closed bar (index -2) so fast SMA crosses above slow.
    closes = [100.0] * 30 + [100, 100, 160, 100]
    # layout: ... [-4]=100, [-3]=100, [-2]=160, [-1]=100 forming
    return enrich(_ohlcv(closes))


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


async def _instant(_seconds=0):
    return None


@pytest.mark.asyncio
async def test_reverse_cancels_sl_before_new_entry(engine):
    from strategy_engine import OpenPosition

    engine.position = OpenPosition(
        direction="SHORT",
        qty=1000,
        entry_price=100,
        ma_cross_price=100,
        atr_at_entry=1.5,
        sl_trigger=102,
        sl_order_id="SL-OLD",
        entry_order_id="E0",
        entry_time=dt.datetime(2026, 9, 23, 10, 0, tzinfo=IST),
        trade_id=_seed_open_trade(),
        mode="PAPER",
    )
    cfg = engine.load_config()
    now = dt.datetime(2026, 9, 23, 10, 30, 1, tzinfo=IST)
    result = await engine.apply_signal("BULLISH", _bullish_frame(), cfg, now)
    assert "reversed" in result
    events = engine.broker.events
    assert events.index("CANCEL SL-OLD") < events.index("ENTRY BUY")
    assert engine.position is not None
    assert engine.position.direction == "LONG"
    assert engine.position.sl_trigger < engine.position.entry_price


@pytest.mark.asyncio
async def test_reverse_blocked_when_sl_cancel_not_confirmed(engine):
    from strategy_engine import OpenPosition

    engine.broker.sl_status = "TRIGGER_PENDING"

    async def never_cancel(order_id):
        engine.broker.events.append(f"CANCEL {order_id}")
        # leave status working

    engine.broker.cancel_order = never_cancel
    engine.position = OpenPosition(
        direction="LONG",
        qty=1000,
        entry_price=100,
        ma_cross_price=100,
        atr_at_entry=1.2,
        sl_trigger=98,
        sl_order_id="SL-STUCK",
        entry_order_id="E0",
        entry_time=dt.datetime(2026, 9, 23, 10, 0, tzinfo=IST),
        trade_id=_seed_open_trade(),
        mode="PAPER",
    )
    # Bearish frame isn't required beyond ATR being present; apply_signal reads iloc[-2].
    frame = _bullish_frame()
    cfg = engine.load_config()
    now = dt.datetime(2026, 9, 23, 10, 30, 1, tzinfo=IST)
    result = await engine.apply_signal("BEARISH", frame, cfg, now)
    assert "blocked" in result
    assert "ENTRY" not in " ".join(engine.broker.events)
    assert engine.position.direction == "LONG"


@pytest.mark.asyncio
async def test_pending_order_blocks_a_second_entry(engine):
    engine.inflight = "PENDING"
    cfg = engine.load_config()
    now = dt.datetime(2026, 9, 23, 10, 30, 1, tzinfo=IST)
    result = await engine.apply_signal("BULLISH", _bullish_frame(), cfg, now)
    assert "PENDING" in result
    assert engine.broker.events == []


@pytest.mark.asyncio
async def test_daily_loss_kill_flattens(engine):
    from strategy_engine import OpenPosition

    cfg = engine.load_config()
    with __import__("database").session_factory()() as db:
        row = db.get(__import__("models").BotConfig, 1)
        row.max_daily_loss = 500
        db.commit()
    engine.position = OpenPosition(
        direction="LONG",
        qty=1000,
        entry_price=100,
        ma_cross_price=100,
        atr_at_entry=1,
        sl_trigger=90,
        sl_order_id="SL1",
        entry_order_id="E1",
        entry_time=dt.datetime(2026, 9, 23, 10, 0, tzinfo=IST),
        trade_id=_seed_open_trade(),
        mode="PAPER",
    )
    engine.ltp = 90  # -10 * 1000 = -10000 gross, well past 500
    engine.realized_net = 0
    cfg = engine.load_config()
    assert engine._loss_breached(cfg)
    await engine.kill("max_daily_loss breached")
    assert engine.status == "HALTED"
    assert engine.position is None
    assert any(e.startswith("CANCEL") for e in engine.broker.events)
    assert any(e.startswith("EXIT") for e in engine.broker.events)


def _seed_open_trade() -> int:
    from models import TradeLog
    import database

    with database.session_factory()() as db:
        row = TradeLog(
            date="2026-09-23",
            symbol="KIRLOSFER",
            direction="LONG",
            qty=1000,
            entry_time=dt.datetime(2026, 9, 23, 10, 0),
            entry_price=100,
            ma_cross_price=100,
            atr_at_entry=1,
            sl_trigger_price=98,
            mode="PAPER",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


@pytest.fixture
def api(tmp_path, monkeypatch):
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
        yield client
    database.reset_engine()
    get_settings.cache_clear()


def test_boots_in_paper_and_live_needs_confirmation(api):
    state = api.get("/api/state")
    assert state.status_code == 200
    assert state.json()["mode"] == "PAPER"
    refused = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": False})
    assert refused.status_code == 400
    no_token = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert no_token.status_code == 503
    assert api.get("/api/config").json()["trading_mode"] == "PAPER"
