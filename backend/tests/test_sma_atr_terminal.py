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
    assert "Order placed" in opened
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


def _gap_frame(gaps: list[float], atr: float = 1.0) -> pd.DataFrame:
    """Closed SMA gaps, oldest first, plus a forming bar the strategy must ignore."""
    rows = []
    for i, gap in enumerate([*gaps, gaps[-1]]):
        rows.append(
            {
                "ts": 1_700_000_000 + i * 60,
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.0,
                "volume": 1000,
                "sma_9": 100.0 + gap,
                "sma_21": 100.0,
                "atr_14": atr,
            }
        )
    return pd.DataFrame(rows)


def _level_frame(level: float, pcts: list[float], atr: float = 1.0) -> pd.DataFrame:
    """Percent gaps of SMA 21, so a cheap stock and a dear stock can share one path."""
    rows = []
    for i, pct in enumerate([*pcts, pcts[-1]]):
        slow = level
        rows.append(
            {
                "ts": 1_700_000_000 + i * 60,
                "open": level,
                "high": level + 0.5,
                "low": level - 0.5,
                "close": level,
                "volume": 1000,
                "sma_9": slow * (1 + pct / 100.0),
                "sma_21": slow,
                "atr_14": atr,
            }
        )
    return pd.DataFrame(rows)


def test_a_closing_sma_gap_is_a_few_minutes_from_a_cross():
    from strategy_engine import minutes_until_cross, minutes_until_stop

    side, minutes, gap = minutes_until_cross(_gap_frame([-1.5, -1.0, -0.5]))
    assert side == "BULLISH"
    assert minutes == pytest.approx(1.0)
    assert gap == pytest.approx(-0.5)
    apart, _, _ = minutes_until_cross(_gap_frame([-1.0, -2.0, -3.0]))
    assert apart is None
    cheap, cheap_min, _ = minutes_until_cross(_level_frame(120.0, [-1.5, -1.0, -0.5]))
    dear, dear_min, dear_gap = minutes_until_cross(_level_frame(1160.0, [-1.5, -1.0, -0.5]))
    assert cheap == dear == "BULLISH"
    assert cheap_min == pytest.approx(dear_min) == pytest.approx(1.0)
    assert dear_gap == pytest.approx(-0.5)
    assert minutes_until_stop("SHORT", 100.2, 101.0, 0.4) == pytest.approx(2.0)
    assert minutes_until_stop("LONG", 100.2, 101.0, 0.4) is None


@pytest.mark.asyncio
async def test_heads_up_fires_once_before_an_order_and_before_a_close(engine, monkeypatch):
    from strategy_engine import OpenPosition

    notes: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", notes.append)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    cfg.square_off_time = "15:15"
    now = dt.datetime(2026, 9, 29, 14, 0, tzinfo=IST)
    frame = _gap_frame([-1.5, -1.0, -0.5])
    await engine.on_minute(now, cfg, frame)
    await engine.on_minute(now + dt.timedelta(minutes=1), cfg, frame)
    assert sum("may be ordered" in note for note in notes) == 1
    assert "SHIPROCKET" not in engine.positions

    notes.clear()
    engine._warned.clear()
    engine.positions["SHIPROCKET"] = OpenPosition(
        direction="SHORT",
        qty=1,
        entry_price=100.0,
        ma_cross_price=100.0,
        atr_at_entry=1.0,
        sl_trigger=103.0,
        sl_order_id="",
        entry_order_id="E",
        entry_time=now,
        trade_id=1,
        mode="PAPER",
    )
    engine.ltp = 100.0
    engine._ltps["SHIPROCKET"] = 100.0
    await engine.on_minute(now, cfg, frame)
    assert any("may close" in note for note in notes)
    assert not any("may be ordered" in note for note in notes)
    assert engine.positions["SHIPROCKET"].direction == "SHORT"

    notes.clear()
    engine._warned.discard(("SHIPROCKET", "stop"))
    engine.ltp = 102.2
    engine._ltps["SHIPROCKET"] = 102.2
    tight = _gap_frame([-3.0, -4.0, -5.0], atr=0.4)
    await engine.on_minute(now + dt.timedelta(minutes=2), cfg, tight)
    assert any("may hit its stop" in note for note in notes)

    notes.clear()
    engine._warned.discard(("SHIPROCKET", "squareoff"))
    engine.ltp = 100.0
    engine._ltps["SHIPROCKET"] = 100.0
    await engine.on_minute(dt.datetime(2026, 9, 29, 15, 13, tzinfo=IST), cfg, tight)
    assert any("square-off in about" in note for note in notes)


@pytest.mark.asyncio
async def test_a_halt_and_the_square_off_tell_telegram(engine, monkeypatch):
    notes: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", notes.append)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    await engine.kill("Manual PANIC SQUARE-OFF")
    assert any(note.startswith("PalTra bot HALTED") and "Manual PANIC SQUARE-OFF" in note for note in notes)

    notes.clear()
    engine.status = "RUNNING"
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.square_off_time = "15:15"
    await engine.on_minute(dt.datetime(2026, 9, 30, 15, 16, tzinfo=IST), cfg, _gap_frame([1.0, 1.0, 1.0]))
    assert engine.status == "DAY_COMPLETED"
    assert any("PalTra bot DAY_COMPLETED" in note for note in notes)


@pytest.mark.asyncio
async def test_a_running_bot_is_announced_when_the_process_stops(engine, monkeypatch):
    sent: list[str] = []

    class Notifier:
        async def send(self, message: str):
            sent.append(message)

    monkeypatch.setattr("app.services.alert_notifier.alert_notifier", Notifier())
    engine.status = "STOPPED"
    await engine.announce_shutdown()
    assert sent == []
    engine.status = "RUNNING"
    await engine.announce_shutdown()
    assert sent and "process is stopping" in sent[0] and "RUNNING" in sent[0]


@pytest.mark.asyncio
async def test_a_bearish_heads_up_does_not_order_before_the_cross(engine, monkeypatch):
    from strategy_engine import OpenPosition

    notes: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", notes.append)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    now = dt.datetime(2026, 9, 29, 14, 0, tzinfo=IST)
    bearish = _gap_frame([1.5, 1.0, 0.5])
    engine.ltp = 100.0
    engine._ltps["SHIPROCKET"] = 100.0

    await engine.on_minute(now, cfg, bearish)
    await engine.on_minute(now + dt.timedelta(minutes=1), cfg, bearish)
    assert "SHIPROCKET" not in engine.positions
    assert engine.broker.events == []
    assert any("no order yet" in note for note in notes)
    assert not any("placed now" in note or "sold now" in note for note in notes)

    trade_id = _seed_open_trade()
    engine.positions["SHIPROCKET"] = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=100.0,
        ma_cross_price=100.0,
        atr_at_entry=1.0,
        sl_trigger=98.0,
        sl_order_id="",
        entry_order_id="E",
        entry_time=now,
        trade_id=trade_id,
        mode="PAPER",
    )
    notes.clear()
    engine._warned.clear()
    await engine.on_minute(now, cfg, bearish)
    assert engine.positions["SHIPROCKET"].direction == "LONG"
    assert engine.broker.events == []
    assert any("no close yet" in note for note in notes)


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
    assert client.token == "desk-token"
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


def test_a_fresh_price_does_not_download_candles_again():
    from groww_client import GrowwClient

    calls = {"candles": 0}

    class Sdk:
        def get_ltp(self, **_kwargs):
            return {"NSE_SUNTV": 610.0}

        def get_historical_candle_data(self, **_kwargs):
            calls["candles"] += 1
            start = 1_700_000_000
            candles = [
                [start + i * 60, 600 + i * 0.01, 601, 599, 600.5, 100]
                for i in range(40)
            ]
            return {"candles": candles}

    client = GrowwClient(mode="LIVE", token="test-token")
    client._sdk = Sdk()
    client._load_quote("SUNTV")
    client._load_quote("SUNTV")
    assert calls["candles"] == 1
    assert float(client._quotes["SUNTV"][0]) == 610.0


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
        trading_mode = "PAPER"
        symbol = "RELIANCE"

    asyncio.run(engine.on_minute(now, Config(), frame))  # type: ignore[arg-type]
    assert called["entry"] is False
    # 19:00 is past square-off, so the practice day is complete; no entry either way.
    assert engine.status == "DAY_COMPLETED"


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
    # Force orders are refused outside the session, so this runs inside it.
    session = {"open": True}
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: session["open"])
    monkeypatch.setattr("strategy_engine._ist_now", lambda: dt.datetime(2026, 9, 29, 13, 59, tzinfo=IST))
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
    from models import BotConfig
    import database

    with database.session_factory()() as db:
        db.get(BotConfig, 1).trade_symbols = "SHIPROCKET"  # Force needs an armed stock.
        db.commit()
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


def test_boots_in_paper_and_live_needs_confirmation(api, monkeypatch):
    state = api.get("/api/state")
    assert state.status_code == 200
    assert state.json()["mode"] == "PAPER"
    refused = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": False})
    assert refused.status_code == 400
    monkeypatch.setattr("main.preferred_quote_token", lambda: "")
    no_token = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert no_token.status_code == 503
    assert api.get("/api/config").json()["trading_mode"] == "PAPER"


def test_a_practice_halt_does_not_block_live_confirm(api, monkeypatch):
    from main import engine

    monkeypatch.setattr("main.preferred_quote_token", lambda: "desk-token")
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-token")
    engine.positions.clear()
    engine.status = "HALTED"
    engine.halt_reason = "max_trades_per_day (15) reached"
    engine.trades_today = 15
    switched = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert switched.status_code == 200
    assert switched.json()["trading_mode"] == "LIVE"
    assert engine.status == "STOPPED"
    assert engine.trades_today == 0
    assert engine.halt_reason == ""


def test_a_live_halt_still_blocks_another_confirm(api, monkeypatch):
    from database import session_factory
    from models import BotConfig
    from main import engine

    monkeypatch.setattr("main.preferred_quote_token", lambda: "desk-token")
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-token")
    engine.positions.clear()
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trading_mode = "LIVE"
        db.commit()
    engine.status = "HALTED"
    engine.halt_reason = "max_daily_loss breached"
    blocked = api.post("/api/mode", json={"mode": "LIVE", "confirm_live": True})
    assert blocked.status_code == 423
    assert engine.status == "HALTED"


def test_start_resumes_after_a_manual_panic(api):
    from main import engine

    engine.positions.clear()
    engine.status = "HALTED"
    engine.halt_reason = "Manual PANIC SQUARE-OFF"
    resumed = api.post("/api/bot/start")
    assert resumed.status_code == 200
    assert resumed.json()["bot_status"] == "RUNNING"
    assert engine.halt_reason == ""


def test_start_stays_locked_after_a_loss_halt(api):
    from main import engine

    engine.positions.clear()
    engine.status = "HALTED"
    engine.halt_reason = "max_daily_loss ₹5000 breached"
    blocked = api.post("/api/bot/start")
    assert blocked.status_code == 423
    assert blocked.json()["detail"] == "max_daily_loss ₹5000 breached"
    assert engine.status == "HALTED"


def test_desk_session_is_used_ahead_of_the_fly_secret(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-token")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "stale-env")
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: True)
    client = GrowwClient(mode="LIVE", token="stale-env")
    assert client.token == "desk-token"
    frame = pd.DataFrame(
        [{"ts": 1, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1}]
    )

    async def fake(symbol):
        assert client.token == "desk-token"
        return 10.0, frame

    client._refresh_live = fake  # type: ignore[method-assign]
    ltp, _, source = asyncio.run(client.refresh("RELIANCE"))
    assert ltp == 10.0
    assert source == "GROWW"


def test_auth_failure_retries_with_the_desk_session(monkeypatch):
    import asyncio

    from groww_client import GrowwClient

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "desk-token")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "stale-env")
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: True)
    client = GrowwClient(mode="LIVE", token="bad-explicit")
    client.token = "bad-explicit"
    client._using_saved = False
    frame = pd.DataFrame(
        [{"ts": 1, "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "volume": 1}]
    )
    seen: list[str] = []

    async def fake(_symbol):
        seen.append(client.token)
        if client.token != "desk-token":
            raise RuntimeError("Authentication failed. Your API token has either expired or is invalid.")
        return 10.0, frame

    client._refresh_live = fake  # type: ignore[method-assign]
    ltp, _, source = asyncio.run(client.refresh("RELIANCE"))
    assert seen == ["bad-explicit", "desk-token"]
    assert ltp == 10.0
    assert source == "GROWW"
    assert client.token == "desk-token"


def test_a_refused_groww_body_is_not_an_order():
    from groww_client import GrowwOrderRejected, _order_ack_from_response

    try:
        _order_ack_from_response({"status": "FAILURE", "error": {"message": "Intraday orders are not available"}})
        raise AssertionError("expected a refusal")
    except GrowwOrderRejected as exc:
        assert "Intraday" in str(exc)
    try:
        _order_ack_from_response({"payload": {"remark": "stopped"}})
        raise AssertionError("missing id must not be invented")
    except GrowwOrderRejected as exc:
        assert "stopped" in str(exc)
    ack = _order_ack_from_response(
        {"payload": {"groww_order_id": "GM123", "order_status": "EXECUTED", "average_fill_price": 600.5}}
    )
    assert ack.order_id == "GM123"
    assert ack.status == "EXECUTED"
    assert ack.fill_price == 600.5


@pytest.mark.asyncio
async def test_a_pending_live_order_is_cancelled_and_not_booked(engine):
    from groww_client import OrderAck

    async def pending_entry(symbol, side, qty, ltp):
        engine.broker.events.append(f"ENTRY {side}")
        return OrderAck("GM-PEND", "PENDING", None)

    engine.broker.place_entry = pending_entry  # type: ignore[method-assign]
    engine.broker.sl_status = "PENDING"
    cfg = engine.load_config()
    cfg.trading_mode = "LIVE"
    cfg.symbol = "SUNTV"
    cfg.qty = 1
    cfg.use_stop = False
    result = await engine.apply_signal(
        "BULLISH", _bullish_frame(), cfg, dt.datetime(2026, 9, 30, 11, 0, tzinfo=IST)
    )
    assert "cancelled" in result
    assert "CANCEL GM-PEND" in engine.broker.events
    assert engine.position is None
    assert engine.trades() == []


@pytest.mark.asyncio
async def test_a_live_exit_is_not_sent_when_groww_is_flat(engine):
    from strategy_engine import OpenPosition

    async def net_quantity(symbol):
        return 0

    engine.broker.net_quantity = net_quantity  # type: ignore[method-assign]
    trade_id = _seed_open_trade()
    engine.position = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=600,
        ma_cross_price=600,
        atr_at_entry=4,
        sl_trigger=590,
        sl_order_id="",
        entry_order_id="GM1",
        entry_time=dt.datetime(2026, 9, 30, 10, 0, tzinfo=IST),
        trade_id=trade_id,
        mode="LIVE",
    )
    cfg = engine.load_config()
    cfg.trading_mode = "LIVE"
    cfg.symbol = "SUNTV"
    cfg.use_stop = False
    await engine._close_position(
        engine.position, 590, "MA_CROSS", dt.datetime(2026, 9, 30, 14, 0, tzinfo=IST), cfg
    )
    assert not any(event.startswith("EXIT") for event in engine.broker.events)
    closed = engine.trades()[0]
    assert closed["exit_reason"] == "NOT_ON_GROWW"
    assert closed["gross_pnl"] == 0


@pytest.mark.asyncio
async def test_an_open_row_groww_does_not_hold_is_removed_without_an_order(engine):
    from strategy_engine import OpenPosition

    async def net_quantity(symbol):
        return 0

    engine.broker.net_quantity = net_quantity  # type: ignore[method-assign]
    trade_id = _seed_open_trade()
    engine.positions["SUNTV"] = OpenPosition(
        direction="SHORT",
        qty=1,
        entry_price=610,
        ma_cross_price=610,
        atr_at_entry=3,
        sl_trigger=620,
        sl_order_id="",
        entry_order_id="GM2",
        entry_time=dt.datetime(2026, 9, 30, 10, 0, tzinfo=IST),
        trade_id=trade_id,
        mode="LIVE",
    )
    cfg = engine.load_config()
    cfg.trading_mode = "LIVE"
    await engine._drop_positions_groww_does_not_hold(cfg)
    assert "SUNTV" not in engine.positions
    assert engine.broker.events == []
    assert engine.trades()[0]["exit_reason"] == "NOT_ON_GROWW"


@pytest.mark.asyncio
async def test_close_symbol_flattens_one_book_and_leaves_the_bot_running(engine):
    from strategy_engine import OpenPosition

    engine.status = "RUNNING"
    trade_id = _seed_open_trade()
    engine.positions["KIRLOSFER"] = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=100,
        ma_cross_price=100,
        atr_at_entry=1,
        sl_trigger=98,
        sl_order_id="",
        entry_order_id="E1",
        entry_time=dt.datetime(2026, 9, 30, 10, 0, tzinfo=IST),
        trade_id=trade_id,
        mode="PAPER",
    )
    engine._ltps["KIRLOSFER"] = 110
    engine._focus = "SUNTV"
    engine.positions["SUNTV"] = OpenPosition(
        direction="SHORT",
        qty=1,
        entry_price=600,
        ma_cross_price=600,
        atr_at_entry=2,
        sl_trigger=610,
        sl_order_id="",
        entry_order_id="E2",
        entry_time=dt.datetime(2026, 9, 30, 10, 5, tzinfo=IST),
        trade_id=trade_id + 1,
        mode="PAPER",
    )
    result = await engine.close_symbol("KIRLOSFER")
    assert result == "KIRLOSFER closed"
    assert "KIRLOSFER" not in engine.positions
    assert engine.positions["SUNTV"].direction == "SHORT"
    assert engine._focus == "SUNTV"
    assert engine.status == "RUNNING"
    assert "EXIT SELL" in engine.broker.events
    assert engine.trades()[0]["exit_reason"] == "MANUAL_CLOSE"


def test_close_without_a_position_is_refused(api):
    response = api.post("/api/bot/close", json={"symbol": "SUNTV"})
    assert response.status_code == 400
    assert "no open position" in response.json()["detail"]


def _tape(closes: list[float], volumes: list[int] | None = None, body: float = 0.4) -> pd.DataFrame:
    """Build a frame whose `volume` column is a session running total.

    `volumes` are the shares traded in each minute. The column stores the
    cumulative sum, which is what Groww puts on a 1-minute candle.
    """
    rows = []
    running = 0
    for i, close in enumerate(closes):
        running += 1000 if volumes is None else volumes[i]
        rows.append(
            {
                "ts": 1_758_600_000 + i * 60,
                "open": close - body,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": running,
            }
        )
    return pd.DataFrame(rows)


def test_unchecked_entry_filters_are_ignored():
    from indicators import entry_filter_reason

    quiet = _tape([100.0] * 5, [1, 1, 1, 1, 1], body=0.0)
    assert entry_filter_reason(quiet, "LONG") is None


def test_each_checked_filter_can_block_and_can_pass():
    from indicators import entry_filter_reason

    below = _tape([200.0] * 24 + [100.0, 100.0])
    assert "below" in (entry_filter_reason(below, "LONG", use_vwap=True) or "")
    assert entry_filter_reason(below, "SHORT", use_vwap=True) is None
    assert entry_filter_reason(below, "LONG", use_vwap=False) is None

    thin = _tape([100.0] * 30, [1000] * 28 + [10, 10])
    assert "volume" in (entry_filter_reason(thin, "LONG", use_volume=True, volume_min_ratio=1) or "")
    thick = _tape([100.0] * 30, [1000] * 30)
    assert entry_filter_reason(thick, "LONG", use_volume=True, volume_min_ratio=1) is None

    doji = _tape([100.0] * 8, body=0.0)
    assert "density" in (entry_filter_reason(doji, "LONG", use_density=True, density_min_pct=50) or "")
    solid = _tape([100.0] * 8, body=0.8)
    assert entry_filter_reason(solid, "LONG", use_density=True, density_min_pct=50) is None

    stretched = _tape([100.0 + i for i in range(40)])
    assert "RSI" in (entry_filter_reason(stretched, "LONG", use_rsi=True, rsi_long_max=70) or "")
    calm = _tape([100.0 + (0.2 if i % 2 else -0.2) for i in range(40)])
    assert entry_filter_reason(calm, "LONG", use_rsi=True, rsi_long_min=30, rsi_long_max=70) is None


@pytest.mark.asyncio
async def test_a_checked_vwap_blocks_the_new_order_and_still_closes_the_old_one(engine):
    from strategy_engine import OpenPosition

    cfg = engine.load_config()
    cfg.use_adx_filter = False
    cfg.use_vwap = True
    frame = enrich(_tape([200.0] * 24 + [100.0, 100.0]))
    now = dt.datetime(2026, 9, 30, 11, 0, tzinfo=IST)
    result = await engine.apply_signal("BULLISH", frame, cfg, now)
    assert "VWAP" in result
    assert engine.broker.events == []

    cfg.use_vwap = False
    opened = await engine.apply_signal("BULLISH", frame, cfg, now)
    assert "opened LONG" in opened

    cfg.use_vwap = True
    engine.broker.events.clear()
    engine.position = OpenPosition(
        direction="SHORT",
        qty=1,
        entry_price=100,
        ma_cross_price=100,
        atr_at_entry=1,
        sl_trigger=102,
        sl_order_id="",
        entry_order_id="E",
        entry_time=now,
        trade_id=_seed_open_trade(),
        mode="PAPER",
    )
    reversed_ = await engine.apply_signal("BULLISH", frame, cfg, now)
    assert "closed on BULLISH" in reversed_
    assert "VWAP" in reversed_
    assert engine.position is None
    assert engine.broker.events.count("ENTRY BUY") == 0


@pytest.mark.asyncio
async def test_force_order_skips_checked_filters_but_logs_them(engine, monkeypatch):
    from models import BotConfig
    import database

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: dt.datetime(2026, 9, 29, 13, 59, tzinfo=IST))
    logged = []
    monkeypatch.setattr(engine, "_log_decision", lambda *a, **k: logged.append(k.get("note", "")))

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.use_vwap = True
        row.use_rsi = True
        row.symbol = "SUNTV"
        row.trade_symbols = "SUNTV"
        row.trading_mode = "PAPER"
        db.commit()
    frame = enrich(_tape([100.0 + i for i in range(30)]))
    engine._frames["SUNTV"] = frame
    engine._ltps["SUNTV"] = 50.0  # far below VWAP: a cross would be refused
    engine.status = "STOPPED"
    result = await engine.force_order("SUNTV")
    assert result == "opened LONG"
    assert engine.positions["SUNTV"].direction == "LONG"
    assert engine.status == "RUNNING"
    assert any("would have refused" in note and "VWAP" in note for note in logged)


@pytest.mark.asyncio
async def test_force_order_still_refuses_an_unarmed_stock_and_a_closed_market(engine, monkeypatch):
    from models import BotConfig
    import database
    from strategy_engine import ForceRefused

    monkeypatch.setattr("strategy_engine._ist_now", lambda: dt.datetime(2026, 9, 29, 13, 59, tzinfo=IST))
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.symbol = "SUNTV"
        row.trade_symbols = "TCS"
        db.commit()
    engine._frames["SUNTV"] = enrich(_tape([100.0 + i for i in range(30)]))
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    with pytest.raises(ForceRefused, match="not on the Trade list"):
        await engine.force_order("SUNTV")
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: False)
    with pytest.raises(ForceRefused, match="Market is closed"):
        await engine.force_order("TCS")
    assert engine.broker.events == []


def _at_close(frame: pd.DataFrame, when: dt.datetime) -> pd.DataFrame:
    """Put the last closed bar on `when`, floored to the minute."""
    when = when.astimezone(IST).replace(second=0, microsecond=0)
    out = frame.copy()
    delta = int(when.timestamp()) - int(out.iloc[-2]["ts"])
    out["ts"] = out["ts"] + delta
    return out


def _arm(symbols: list[str], chart: str | None = None) -> None:
    from models import BotConfig
    import database

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = ",".join(symbols)
        row.symbol = chart or symbols[0]
        row.use_adx_filter = False
        row.qty = 1
        row.trading_mode = "PAPER"
        db.commit()


@pytest.mark.asyncio
async def test_every_armed_symbol_can_open_on_the_same_cross(engine, monkeypatch):
    """A cross on ADANIENSOL must not be dropped because ANTELOPUS ordered first."""
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    names = ["ANTELOPUS", "ADANIENSOL", "KOTAKBANK", "AZAD"]
    _arm(names, "ADANIENSOL")
    now = dt.datetime(2026, 10, 1, 12, 21, 5, tzinfo=IST)
    raw = _at_close(_ohlcv([100.0] * 30 + [100, 100, 160, 100]), now - dt.timedelta(minutes=1))
    frames = {name: raw.copy() for name in names}

    async def refresh(symbol):
        frame = frames[symbol]
        return float(frame.iloc[-1]["close"]), frame.copy(), "GROWW"

    engine.broker.refresh = refresh
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    await engine.tick(now)
    assert set(engine.positions) == set(names)
    for name in names:
        assert engine.positions[name].direction == "LONG"


@pytest.mark.asyncio
async def test_a_late_candle_is_judged_when_it_arrives(engine, monkeypatch):
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    _arm(["ADANIENSOL"])
    now = dt.datetime(2026, 10, 1, 12, 21, 5, tzinfo=IST)
    stale = _at_close(_ohlcv([100.0] * 30 + [100, 100, 160, 100]), now - dt.timedelta(minutes=3))
    frames = {"ADANIENSOL": stale}

    async def refresh(symbol):
        frame = frames[symbol]
        return float(frame.iloc[-1]["close"]), frame.copy(), "GROWW"

    engine.broker.refresh = refresh
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    await engine.tick(now)
    assert "ADANIENSOL" not in engine.positions
    assert "closed candle" in engine._signals["ADANIENSOL"]

    frames["ADANIENSOL"] = _at_close(
        _ohlcv([100.0] * 30 + [100, 100, 160, 100]), now - dt.timedelta(minutes=1)
    )
    await engine.tick(now)
    assert engine.positions["ADANIENSOL"].direction == "LONG"


@pytest.mark.asyncio
async def test_a_cross_one_bar_late_still_orders(engine, monkeypatch):
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    from strategy_engine import _signal_on_unjudged_bars

    # The cross completed on the previous closed bar. The newest bar did not cross again.
    frame = _gap_frame([0, 0, 1, 1])
    signal, signal_frame = _signal_on_unjudged_bars(frame, judged_ts=int(frame.iloc[-4]["ts"]))
    assert signal == "BULLISH"
    assert signal_frame is not None
    assert int(signal_frame.iloc[-2]["ts"]) == int(frame.iloc[-3]["ts"])

    missed = _gap_frame([0, 1, 1, 1, 1])
    none_signal, _none_frame = _signal_on_unjudged_bars(missed, judged_ts=0)
    assert none_signal is None

    _arm(["AZAD"])
    cfg = engine.load_config()
    cfg.symbol = "AZAD"
    cfg.use_adx_filter = False
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    engine._judged_bar["AZAD"] = int(frame.iloc[-4]["ts"])
    now = dt.datetime(2026, 10, 1, 12, 21, 5, tzinfo=IST)
    result = await engine.on_minute(now, cfg, frame)
    assert "opened LONG" in result
    assert engine.positions["AZAD"].direction == "LONG"

    engine.positions.clear()
    engine.broker.events.clear()
    engine._judged_bar["AZAD"] = 0
    quiet = await engine.on_minute(now, cfg, missed)
    assert "waiting for a new cross" in quiet
    assert "no order" in quiet
    assert "RSI, Bollinger, SMA gap and candle direction are off" in quiet
    assert "SMA 9 is above SMA 21" in quiet
    assert "AZAD" not in engine.positions


@pytest.mark.asyncio
async def test_an_in_flight_order_does_not_drop_the_other_cross(engine, monkeypatch):
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    _arm(["KOTAKBANK"])
    now = dt.datetime(2026, 10, 1, 12, 21, 5, tzinfo=IST)
    raw = _at_close(_ohlcv([100.0] * 30 + [100, 100, 160, 100]), now - dt.timedelta(minutes=1))

    async def refresh(symbol):
        return float(raw.iloc[-1]["close"]), raw.copy(), "GROWW"

    engine.broker.refresh = refresh
    engine.status = "RUNNING"
    engine.data_source = "GROWW"
    engine.inflight = "PENDING"
    await engine.tick(now)
    assert "KOTAKBANK" not in engine.positions
    assert engine._judged_bar.get("KOTAKBANK") != int(raw.iloc[-2]["ts"])
    engine.inflight = None
    await engine.tick(now)
    assert engine.positions["KOTAKBANK"].direction == "LONG"


def test_twenty_four_stocks_can_be_armed_and_the_twenty_fifth_cannot(api):
    from strategy_engine import MAX_TRADE_SYMBOLS

    assert MAX_TRADE_SYMBOLS == 24
    current = api.get("/api/config").json()["trade_symbols"]
    for name in current:
        cleared = api.post("/api/trade-symbols", json={"symbol": name, "armed": False})
        assert cleared.status_code == 200, cleared.text
    for i in range(24):
        res = api.post("/api/trade-symbols", json={"symbol": f"S{i}", "armed": True})
        assert res.status_code == 200, res.text
    blocked = api.post("/api/trade-symbols", json={"symbol": "S24", "armed": True})
    assert blocked.status_code == 409
    assert "24" in blocked.json()["detail"]
    assert len(api.get("/api/config").json()["trade_symbols"]) == 24


def test_the_old_daily_trade_cap_is_raised_once(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/cap.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    from config import get_settings

    get_settings.cache_clear()
    import database
    from models import BotConfig

    database.reset_engine()
    database.init_db()
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.max_trades_per_day = 15
        row.max_trades_bumped = 0
        db.commit()
    database._ensure_bot_config_columns(database.get_engine())
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        assert row.max_trades_per_day == 40
        assert row.max_trades_bumped == 1
        row.max_trades_per_day = 15
        db.commit()
    database._ensure_bot_config_columns(database.get_engine())
    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        assert row.max_trades_per_day == 15
    database.reset_engine()
    get_settings.cache_clear()


def _open_book(symbol: str = "ADANIENSOL") -> None:
    from strategy_engine import OpenPosition
    from main import engine

    engine.positions[symbol] = OpenPosition(
        direction="LONG",
        qty=1,
        entry_price=1340.9,
        ma_cross_price=1340.9,
        atr_at_entry=8.0,
        sl_trigger=1328.0,
        sl_order_id="SL-LIVE",
        entry_order_id="E-LIVE",
        entry_time=dt.datetime(2026, 10, 1, 12, 30, tzinfo=IST),
        trade_id=1,
        mode="LIVE",
    )


def test_raising_the_trade_cap_unlocks_start_and_keeps_the_open_order(api):
    from database import session_factory
    from models import BotConfig
    from main import engine

    engine.positions.clear()
    _open_book()
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.max_trades_per_day = 15
        row.max_trades_bumped = 1
        row.qty = 1
        db.commit()
    engine.load_config()
    engine.status = "HALTED"
    engine.halt_reason = "max_trades_per_day (15) reached"
    engine.trades_today = 15

    blocked = api.post("/api/bot/start")
    assert blocked.status_code == 423
    assert blocked.json()["detail"] == "max_trades_per_day (15) reached"
    assert engine.positions["ADANIENSOL"].direction == "LONG"
    assert engine.status == "HALTED"

    saved = api.put("/api/config", json={"max_trades_per_day": 40, "qty": 1})
    assert saved.status_code == 200
    assert saved.json()["max_trades_per_day"] == 40
    assert engine.status == "STOPPED"
    assert engine.halt_reason == ""
    assert engine.positions["ADANIENSOL"].entry_order_id == "E-LIVE"
    assert engine.trades_today == 15

    started = api.post("/api/bot/start")
    assert started.status_code == 200
    assert started.json()["bot_status"] == "RUNNING"
    assert engine.positions["ADANIENSOL"].direction == "LONG"
    assert engine.trades_today == 15


def test_a_loss_halt_stays_locked_when_the_trade_cap_is_raised(api):
    from main import engine

    engine.positions.clear()
    engine.status = "HALTED"
    engine.halt_reason = "max_daily_loss ₹5000 breached"
    engine.trades_today = 3
    saved = api.put("/api/config", json={"max_trades_per_day": 40})
    assert saved.status_code == 200
    assert engine.status == "HALTED"
    blocked = api.post("/api/bot/start")
    assert blocked.status_code == 423
    assert "max_daily_loss" in blocked.json()["detail"]


def test_restart_remembers_how_many_live_trades_were_taken(api):
    from database import session_factory
    from models import BotConfig, TradeLog
    from main import engine

    engine._session_date = "2026-10-01"
    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trading_mode = "LIVE"
        db.commit()
        for i in range(15):
            db.add(
                TradeLog(
                    date="2026-10-01",
                    symbol="KOTAKBANK",
                    direction="LONG",
                    qty=1,
                    entry_time=dt.datetime(2026, 10, 1, 10, i % 60),
                    entry_price=1900 + i,
                    ma_cross_price=1900 + i,
                    atr_at_entry=5,
                    sl_trigger_price=1890,
                    mode="LIVE",
                )
            )
        db.add(
            TradeLog(
                date="2026-10-01",
                symbol="KIRLOSFER",
                direction="LONG",
                qty=1,
                entry_time=dt.datetime(2026, 10, 1, 10, 0),
                entry_price=100,
                ma_cross_price=100,
                atr_at_entry=1,
                sl_trigger_price=98,
                mode="PAPER",
            )
        )
        db.commit()
    assert engine.restore_trades_today() == 15
