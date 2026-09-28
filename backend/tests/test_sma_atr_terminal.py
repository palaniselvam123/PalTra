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


def test_closed_market_quote_is_the_last_nse_price(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: False)
    client = GrowwClient(mode="PAPER", token="test-token")
    frame = pd.DataFrame(
        [{"ts": 1, "open": 1135.0, "high": 1285.0, "low": 1094.0, "close": 1261.7, "volume": 10}]
    )

    async def fake(symbol):
        assert symbol == "ANTELOPUS"
        return 1261.7, frame

    client._refresh_live = fake  # type: ignore[method-assign]
    ltp, got, source = asyncio.run(client.refresh("antelopus"))
    assert ltp == 1261.7
    assert source == "LAST CLOSE"
    assert float(got.iloc[-1]["close"]) == 1261.7

    async def should_not_run(_symbol):
        raise AssertionError("cached quote was fetched again")

    client._refresh_live = should_not_run  # type: ignore[method-assign]
    again, _, source_again = asyncio.run(client.refresh("ANTELOPUS"))
    assert again == 1261.7
    assert source_again == "LAST CLOSE"


def test_failed_quote_keeps_the_last_real_price(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: False)
    client = GrowwClient(mode="PAPER", token="test-token")
    frame = pd.DataFrame(
        [{"ts": 1, "open": 1260.0, "high": 1262.0, "low": 1259.0, "close": 1261.7, "volume": 1}]
    )
    client._quotes["ANTELOPUS"] = (1261.7, frame, 0.0)

    async def fail(_symbol):
        raise TimeoutError("timed out")

    client._refresh_live = fail  # type: ignore[method-assign]
    ltp, _, source = asyncio.run(client.refresh("ANTELOPUS"))
    assert ltp == 1261.7
    assert source == "LAST CLOSE"
    assert client.last_error == ""
    assert client.data_source != "SIMULATOR"


def test_simulator_only_when_there_is_no_groww_token(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    client = GrowwClient(mode="PAPER", token="unused")
    client.token = ""
    _ltp, _frame, source = asyncio.run(client.refresh("ANTELOPUS"))
    assert source == "SIMULATOR"


def test_empty_env_token_uses_the_saved_desk_session(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-token")
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: False)
    client = GrowwClient(mode="PAPER", token="unused")
    client.token = ""
    client.set_mode("PAPER", "")
    assert client.token == ""
    frame = pd.DataFrame(
        [{"ts": 1, "open": 1160.0, "high": 1166.0, "low": 1158.0, "close": 1159.45, "volume": 1}]
    )

    async def fake(symbol):
        assert symbol == "ANTELOPUS"
        assert client.token == "desk-token"
        return 1159.45, frame

    client._refresh_live = fake  # type: ignore[method-assign]
    ltp, _, source = asyncio.run(client.refresh("ANTELOPUS"))
    assert ltp == 1159.45
    assert source == "LAST CLOSE"
    assert client.data_source != "SIMULATOR"


def test_desk_session_token_reads_an_unexpired_row(tmp_path, monkeypatch):
    import sqlite3

    from cryptography.fernet import Fernet

    import groww_client

    key = Fernet.generate_key().decode()
    cipher = Fernet(key.encode()).encrypt(b"quote-only-token").decode()
    db = tmp_path / "trading.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "create table broker_credentials "
            "(broker text, access_token_encrypted text, token_expires_at text)"
        )
        con.execute(
            "insert into broker_credentials values (?, ?, ?)",
            ("groww", cipher, "2099-01-01 23:59:00"),
        )
    monkeypatch.setenv("DESK_TRADING_DB", str(db))
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    groww_client._desk_token_cache = None
    assert groww_client.desk_session_token() == "quote-only-token"
    with sqlite3.connect(db) as con:
        con.execute("update broker_credentials set token_expires_at = ?", ("2000-01-01 00:00:00",))
    groww_client._desk_token_cache = None
    assert groww_client.desk_session_token() == ""


def test_empty_mode_token_does_not_clear_a_loaded_session():
    from groww_client import GrowwClient

    client = GrowwClient(mode="PAPER", token="kept")
    client.set_mode("PAPER", "")
    assert client.token == "kept"


def test_quote_client_skips_the_changelog_download():
    from growwapi import GrowwAPI

    from groww_client import GrowwClient

    def boom(_self):
        raise AssertionError("changelog")

    GrowwAPI._get_changelog = boom
    client = GrowwClient(mode="PAPER", token="test-token")
    sdk = client._require_sdk()
    assert sdk.token == "test-token"


def test_ltp_is_kept_when_candle_history_fails():
    from groww_client import GrowwClient

    client = GrowwClient(mode="PAPER", token="test-token")

    class Sdk:
        def get_ltp(self, **_kwargs):
            assert _kwargs.get("timeout") == 6
            return {"NSE_ANTELOPUS": 1159.45}

        def get_historical_candle_data(self, **_kwargs):
            raise TimeoutError("candles")

    client._sdk = Sdk()
    ltp, frame = client._load_quote("ANTELOPUS")
    assert ltp == 1159.45
    assert len(frame) == 1
    assert float(frame.iloc[-1]["close"]) == 1159.45


def test_session_open_fills_the_day_change_when_candles_are_missing():
    from groww_client import GrowwClient

    client = GrowwClient(mode="PAPER", token="test-token")

    class Sdk:
        def get_ltp(self, **_kwargs):
            return {"NSE_SHIPROCKET": 120.9}

        def get_historical_candle_data(self, **_kwargs):
            raise TimeoutError("candles")

        def get_ohlc(self, **_kwargs):
            return {"NSE_SHIPROCKET": {"open": 126.19, "high": 127.0, "low": 119.5, "close": 120.9}}

    client._sdk = Sdk()
    ltp, frame = client._load_quote("SHIPROCKET")
    assert ltp == 120.9
    assert float(frame.iloc[-1]["open"]) == 126.19
    assert float(frame.iloc[-1]["close"]) == 120.9


def test_real_tape_does_not_open_a_position_after_the_close(monkeypatch):
    import asyncio

    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: False)
    engine = StrategyEngine(broker=GrowwClient(mode="PAPER", token="unused"))
    engine.status = "RUNNING"
    engine.data_source = "LAST CLOSE"
    called = {"entry": False}

    async def refuse(*_args, **_kwargs):
        called["entry"] = True
        raise AssertionError("entry after the close")

    engine.broker.place_entry = refuse  # type: ignore[method-assign]
    frame = enrich(_ohlcv([100.0] * 40))
    now = dt.datetime(2026, 9, 28, 19, 0, tzinfo=IST)

    class Config:
        square_off_time = "15:15"

    asyncio.run(engine.on_minute(now, Config(), frame))  # type: ignore[arg-type]
    assert called["entry"] is False
    assert engine.last_signal == "market closed — showing the last NSE price"


def test_boots_in_paper_and_live_needs_confirmation(api):
    state = api.get("/api/state")
    assert state.status_code == 200
    assert state.json()["mode"] == "PAPER"
    refused = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": False})
    assert refused.status_code == 400
    no_token = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert no_token.status_code == 503
    assert api.get("/api/config").json()["trading_mode"] == "PAPER"
