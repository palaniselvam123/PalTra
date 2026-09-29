"""Tests for the SMA(9, 21) + 1.5× ATR terminal invariants."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from charges import calculate_charges
from indicators import closed_candle_cross, enrich, round_to_nse_tick

IST = ZoneInfo("Asia/Kolkata")


def test_open_trade_is_marked_from_the_live_price():
    from strategy_engine import attach_market_prices, mark_to_market

    long_pts, long_pnl = mark_to_market("LONG", 1175.95, 1180.20, 1)
    assert long_pts == pytest.approx(4.25)
    assert long_pnl == pytest.approx(4.25)
    short_pts, short_pnl = mark_to_market("SHORT", 120.50, 119.10, 1)
    assert short_pts == pytest.approx(1.40)
    assert short_pnl == pytest.approx(1.40)

    rows = attach_market_prices(
        [
            {
                "symbol": "RELIANCE",
                "direction": "LONG",
                "qty": 1,
                "entry_price": 1175.95,
                "exit_price": None,
                "points": None,
                "gross_pnl": None,
            },
            {
                "symbol": "SHIPROCKET",
                "direction": "SHORT",
                "qty": 1,
                "entry_price": 120.50,
                "exit_price": 119.60,
                "points": 0.90,
                "gross_pnl": 0.90,
            },
        ],
        {"RELIANCE": 1180.20, "SHIPROCKET": 50.0},
    )
    assert rows[0]["market_price"] == pytest.approx(1180.20)
    assert rows[0]["mark_pnl"] == pytest.approx(4.25)
    assert rows[0]["gross_pnl"] is None
    # A closed row is marked at the exit fill, not the latest quote.
    assert rows[1]["market_price"] == pytest.approx(119.60)
    assert rows[1]["mark_pnl"] == pytest.approx(0.90)
    assert rows[1]["points"] == pytest.approx(0.90)


def test_whatsapp_text_names_the_fill_and_the_close():
    from strategy_engine import close_alert, fill_alert

    opened = fill_alert(
        mode="LIVE",
        direction="LONG",
        symbol="ANTELOPUS",
        qty=1,
        fill=1175.95,
        stop=1171.25,
        when=dt.datetime(2026, 9, 29, 14, 58, 26),
    )
    assert "LIVE LONG ANTELOPUS" in opened
    assert "1,175.95" in opened
    assert "1,171.25" in opened
    closed = close_alert(
        direction="LONG",
        symbol="ANTELOPUS",
        exit_price=1165.0,
        reason="EOD_SQUARE_OFF",
        gross=-10.95,
        net=-12.74,
        when=dt.datetime(2026, 9, 29, 15, 15, 4),
    )
    assert "closed LONG ANTELOPUS" in closed
    assert "square-off" in closed
    assert "-10.95" in closed


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
async def test_a_fill_is_queued_for_whatsapp(engine, monkeypatch):
    notes: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", notes.append)
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    cfg.qty = 1
    result = await engine.apply_signal(
        "BULLISH", _bullish_frame(), cfg, dt.datetime(2026, 9, 29, 14, 0, tzinfo=IST)
    )
    assert "opened" in result
    assert any("LONG SHIPROCKET" in note for note in notes)


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
async def test_executed_groww_stop_clears_the_long(engine):
    from strategy_engine import OpenPosition

    engine.status = "RUNNING"
    engine.broker.sl_status = "EXECUTED"
    engine.position = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=1154.85,
        ma_cross_price=1155,
        atr_at_entry=4,
        sl_trigger=1150.5,
        sl_order_id="SL-LIVE",
        entry_order_id="E-LIVE",
        entry_time=dt.datetime(2026, 9, 29, 10, 0, tzinfo=IST),
        trade_id=_seed_open_trade(),
        mode="LIVE",
    )
    cfg = engine.load_config()
    cfg.trading_mode = "LIVE"
    await engine._watch_stop(cfg)
    assert engine.position is None
    assert engine.last_signal == "ATR stop hit — flat"


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
    client = GrowwClient(mode="LIVE", token="test-token")
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
    client = GrowwClient(mode="LIVE", token="test-token")
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


def test_paper_after_the_close_walks_from_the_last_nse_price(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: False)
    client = GrowwClient(mode="PAPER", token="test-token")
    ts = int(dt.datetime(2026, 9, 29, 15, 29, tzinfo=IST).timestamp())
    frame = pd.DataFrame(
        [{"ts": ts, "open": 1160.0, "high": 1166.0, "low": 1158.0, "close": 1162.5, "volume": 10}]
    )
    calls = {"n": 0}

    async def fake(symbol):
        calls["n"] += 1
        assert symbol == "ANTELOPUS"
        return 1162.5, frame

    client._refresh_live = fake  # type: ignore[method-assign]
    ltp, got, source = asyncio.run(client.refresh("ANTELOPUS"))
    assert source == "SIMULATOR"
    assert calls["n"] == 1
    assert float(got.iloc[0]["close"]) == 1162.5
    assert abs(ltp - 1162.5) < 20
    _again, _, source_again = asyncio.run(client.refresh("ANTELOPUS"))
    assert calls["n"] == 1
    assert source_again == "SIMULATOR"

    async def other(symbol):
        px = 120.5 if symbol == "SHIPROCKET" else 1189.6
        return px, pd.DataFrame(
            [{"ts": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1}]
        )

    client._refresh_live = other  # type: ignore[method-assign]
    ship, ship_frame, _ = asyncio.run(client.refresh("SHIPROCKET"))
    ante_again, ante_frame, _ = asyncio.run(client.refresh("ANTELOPUS"))
    assert float(ship_frame.iloc[0]["close"]) == 120.5
    assert abs(ship - 120.5) < 20
    assert float(ante_frame.iloc[0]["close"]) == 1162.5
    assert abs(ante_again - 1162.5) < 30


def test_totals_stay_on_the_selected_book(engine):
    from database import session_factory
    from models import TradeLog

    day = engine._session_date
    when = dt.datetime(2026, 9, 29, 14, 0)
    with session_factory()() as db:
        db.add(
            TradeLog(
                date=day,
                symbol="ANTELOPUS",
                direction="LONG",
                qty=1,
                entry_time=when,
                entry_price=100,
                ma_cross_price=100,
                atr_at_entry=1,
                sl_trigger_price=98,
                exit_time=when,
                exit_price=110,
                exit_reason="MA_CROSS",
                gross_pnl=10,
                brokerage_and_taxes=1,
                net_pnl=9,
                mode="PAPER",
            )
        )
        db.add(
            TradeLog(
                date=day,
                symbol="RELIANCE",
                direction="LONG",
                qty=1,
                entry_time=when,
                entry_price=100,
                ma_cross_price=100,
                atr_at_entry=1,
                sl_trigger_price=98,
                exit_time=when,
                exit_price=90,
                exit_reason="EOD_SQUARE_OFF",
                gross_pnl=-10,
                brokerage_and_taxes=1,
                net_pnl=-11,
                mode="LIVE",
            )
        )
        db.commit()
    paper = engine._kpis("PAPER")
    live = engine._kpis("LIVE")
    assert paper["trades"] == 1
    assert paper["net"] == 9
    assert live["trades"] == 1
    assert live["net"] == -11


def test_empty_env_token_uses_the_saved_desk_session(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-token")
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: False)
    client = GrowwClient(mode="LIVE", token="unused")
    client.token = ""
    client.set_mode("LIVE", "")
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


def test_day_change_uses_the_previous_close(monkeypatch):
    from strategy_engine import StrategyEngine

    monkeypatch.setattr(
        "strategy_engine._ist_now",
        lambda: dt.datetime(2026, 9, 28, 22, 0, tzinfo=IST),
    )
    engine = StrategyEngine()
    yesterday = int(dt.datetime(2026, 9, 27, 15, 30, tzinfo=IST).timestamp())
    today = int(dt.datetime(2026, 9, 28, 15, 29, tzinfo=IST).timestamp())
    engine.candles = pd.DataFrame(
        [
            {"ts": yesterday, "open": 126.0, "high": 127.0, "low": 125.0, "close": 126.19, "volume": 1},
            {"ts": today, "open": 122.0, "high": 122.8, "low": 119.3, "close": 120.9, "volume": 1},
        ]
    )
    engine.ltp = 120.9
    day_open, change = engine._day_open_and_change()
    assert day_open == 122.0
    assert change == pytest.approx((120.9 - 126.19) / 126.19 * 100)


def test_day_change_stays_fast_on_a_long_tape(monkeypatch):
    """The websocket computes this on the only thread that can serve the desk."""
    import time

    from strategy_engine import StrategyEngine

    monkeypatch.setattr(
        "strategy_engine._ist_now",
        lambda: dt.datetime(2026, 9, 28, 12, 0, tzinfo=IST),
    )
    start = int(dt.datetime(2026, 9, 20, 9, 15, tzinfo=IST).timestamp())
    n = 80_000
    ts = [start + i * 60 for i in range(n)]
    midnight = int(dt.datetime(2026, 9, 28, tzinfo=IST).timestamp())
    engine = StrategyEngine()
    engine.candles = pd.DataFrame(
        {
            "ts": ts,
            "open": [100.0] * n,
            "high": [101.0] * n,
            "low": [99.0] * n,
            "close": [100.0] * n,
            "volume": [1] * n,
        }
    )
    prev_i = max(i for i, t in enumerate(ts) if t < midnight)
    engine.candles.loc[prev_i, "close"] = 110.0
    engine.candles.loc[prev_i + 1, "open"] = 108.0
    engine.ltp = 121.0
    t0 = time.perf_counter()
    day_open, change = engine._day_open_and_change()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.25
    assert day_open == 108.0
    assert change == pytest.approx((121.0 - 110.0) / 110.0 * 100)


def test_stub_bar_does_not_report_a_flat_day(monkeypatch):
    from strategy_engine import StrategyEngine

    monkeypatch.setattr(
        "strategy_engine._ist_now",
        lambda: dt.datetime(2026, 9, 28, 22, 0, tzinfo=IST),
    )
    engine = StrategyEngine()
    today = int(dt.datetime(2026, 9, 28, 22, 0, tzinfo=IST).timestamp())
    engine.candles = pd.DataFrame(
        [{"ts": today, "open": 120.9, "high": 120.9, "low": 120.9, "close": 120.9, "volume": 0}]
    )
    engine.ltp = 120.9
    _open, change = engine._day_open_and_change()
    assert change is None


def test_a_losing_book_has_no_largest_win():
    from app.services.reports import _largest_win

    assert _largest_win([{"net_pnl": -0.33}]) is None
    assert _largest_win([{"net_pnl": -0.33}, {"net_pnl": 1.5}]) == 1.5


@pytest.mark.asyncio
async def test_start_ignores_a_cross_already_on_the_tape(engine, monkeypatch):
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    engine.data_source = "GROWW"
    crossed = [100.0] * 40
    crossed[-2] = 160.0
    frame = enrich(_ohlcv(crossed))
    assert closed_candle_cross(frame) == "BULLISH"
    engine._frames["SHIPROCKET"] = frame
    engine.hold_for_next_cross(["SHIPROCKET"])
    engine.status = "RUNNING"
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    now = dt.datetime(2026, 9, 29, 14, 0, 5, tzinfo=IST)
    await engine.on_minute(now, cfg, frame)
    assert "SHIPROCKET" not in engine.positions
    assert engine.broker.events == []

    later = [100.0] * 41
    later[-2] = 180.0
    fresh = enrich(_ohlcv(later))
    assert closed_candle_cross(fresh) == "BULLISH"
    assert int(fresh.iloc[-2]["ts"]) > int(frame.iloc[-2]["ts"])
    await engine.on_minute(now + dt.timedelta(minutes=1), cfg, fresh)
    assert engine.positions["SHIPROCKET"].direction == "LONG"


@pytest.mark.asyncio
async def test_force_order_uses_the_live_side_and_starts_the_bot(engine, monkeypatch):
    session = {"open": False}
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: session["open"])
    closes = [100.0] * 40
    closes[-2] = 40.0
    closes[-1] = 200.0
    raw = _ohlcv(closes)
    frame = enrich(raw)
    assert closed_candle_cross(frame) == "BEARISH"
    engine._frames["SHIPROCKET"] = raw
    engine._ltps["SHIPROCKET"] = 200.0
    engine.status = "STOPPED"
    engine.data_source = "GROWW"
    result = await engine.force_order("SHIPROCKET")
    assert engine.status == "RUNNING"
    assert "opened LONG" in result
    assert engine.positions["SHIPROCKET"].direction == "LONG"
    session["open"] = True
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    await engine.on_minute(dt.datetime(2026, 9, 29, 14, 0, tzinfo=IST), cfg, frame)
    assert engine.positions["SHIPROCKET"].direction == "LONG"
    assert engine.broker.events.count("ENTRY BUY") == 1


def test_chart_can_move_while_another_stock_stays_armed(api):
    from strategy_engine import OpenPosition

    from main import engine

    relied = api.put("/api/config", json={"symbol": "RELIANCE"})
    assert relied.status_code == 200
    armed = api.post("/api/trade-symbols", json={"symbol": "RELIANCE", "armed": True})
    assert armed.status_code == 200
    assert "RELIANCE" in armed.json()["trade_symbols"]
    engine._focus = "RELIANCE"
    engine.positions["RELIANCE"] = OpenPosition(
        direction="SHORT",
        qty=1,
        entry_price=1189.95,
        ma_cross_price=1189.95,
        atr_at_entry=0.84,
        sl_trigger=1191.2,
        sl_order_id="SL-R",
        entry_order_id="E-R",
        entry_time=dt.datetime(2026, 9, 29, 13, 40, tzinfo=IST),
        trade_id=1,
        mode="PAPER",
    )
    viewed = api.put("/api/config", json={"symbol": "KIRLOSFER"})
    assert viewed.status_code == 200
    assert viewed.json()["symbol"] == "KIRLOSFER"
    assert "RELIANCE" in viewed.json()["trade_symbols"]
    assert engine.positions["RELIANCE"].direction == "SHORT"
    blocked = api.post("/api/trade-symbols", json={"symbol": "RELIANCE", "armed": False})
    assert blocked.status_code == 409
    second = api.post("/api/trade-symbols", json={"symbol": "ANTELOPUS", "armed": True})
    assert second.status_code == 200
    assert "ANTELOPUS" in second.json()["trade_symbols"]


def test_ltp_payload_accepts_a_single_unnamed_price():
    from groww_client import _parse_ltp

    assert _parse_ltp({"payload": {"NSE_RELIANCE": "1,189.30"}}, "RELIANCE") == 1189.30
    assert _parse_ltp({"payload": {"instrument": {"last_price": 1188.7}}}, "RELIANCE") == 1188.7


def test_boots_in_paper_and_live_needs_confirmation(api):
    state = api.get("/api/state")
    assert state.status_code == 200
    assert state.json()["mode"] == "PAPER"
    refused = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": False})
    assert refused.status_code == 400
    no_token = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert no_token.status_code == 503
    assert api.get("/api/config").json()["trading_mode"] == "PAPER"
