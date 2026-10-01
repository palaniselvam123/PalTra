"""Order and stop prices use each stock's own tick size."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

import tick_sizes

IST = ZoneInfo("Asia/Kolkata")

CSV = (
    "exchange,exchange_token,trading_symbol,groww_symbol,name,instrument_type,segment,series,isin,"
    "underlying_symbol,underlying_exchange_token,expiry_date,strike_price,lot_size,tick_size,"
    "freeze_quantity,is_reserved,buy_allowed,sell_allowed,internal_trading_symbol,is_intraday\n"
    "NSE,2885,RELIANCE,NSE-RELIANCE,Reliance,EQ,CASH,EQ,INE002A01018,,,,,1,0.1,,0,1,1,RELIANCE,1\n"
    "NSE,1,CHEAP,NSE-CHEAP,Cheap,EQ,CASH,EQ,X,,,,,1,0.01,,0,1,1,CHEAP,1\n"
    "NSE,2,BADTICK,NSE-BADTICK,Bad,EQ,CASH,EQ,Y,,,,,1,abc,,0,1,1,BADTICK,1\n"
    "NSE,3,NIFTY24OCTFUT,NSE-X,Fut,FUT,FNO,,,,,,,1,0.05,,0,1,1,X,1\n"
    "BSE,4,RELIANCE,BSE-RELIANCE,Reliance,EQ,CASH,A,INE,,,,,1,0.05,,0,1,1,RELIANCE,1\n"
)


@pytest.fixture(autouse=True)
def _clean_table():
    tick_sizes.set_ticks({})
    yield
    tick_sizes.set_ticks({})


def test_csv_gives_nse_cash_ticks_only():
    table = tick_sizes.parse_instrument_csv(CSV)
    assert table == {"RELIANCE": 0.1, "CHEAP": 0.01}


def test_known_stock_rounds_to_its_own_tick():
    tick_sizes.set_ticks(tick_sizes.parse_instrument_csv(CSV))
    # 0.05 rounding would give 1234.55, which Groww rejects for a 0.10 tick.
    assert tick_sizes.round_price("RELIANCE", 1234.56) == pytest.approx(1234.6)
    assert tick_sizes.round_price("reliance", 1234.54) == pytest.approx(1234.5)
    assert tick_sizes.round_price("CHEAP", 101.23) == pytest.approx(101.23)


def test_unknown_stock_uses_a_coarse_safe_band():
    # Never finer than 0.05, so a cheap name stays on the old step.
    assert tick_sizes.tick_for("UNKNOWN", 120.0) == 0.05
    assert tick_sizes.tick_for("UNKNOWN", 800.0) == 0.05
    assert tick_sizes.tick_for("UNKNOWN", 1500.0) == 0.10
    assert tick_sizes.tick_for("UNKNOWN", 7000.0) == 0.50
    assert tick_sizes.tick_for("UNKNOWN", 15000.0) == 1.00
    assert tick_sizes.tick_for("UNKNOWN", 30000.0) == 5.00
    assert tick_sizes.round_price("UNKNOWN", 1234.56) == pytest.approx(1234.6)


def _on_tick(price: float, tick: float) -> bool:
    steps = price / tick
    return abs(steps - round(steps)) < 1e-6


@pytest.mark.asyncio
async def test_paper_entry_and_stop_land_on_the_stock_tick(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    tick_sizes.set_ticks({"RELIANCE": 0.1})
    eng = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    cfg = eng.load_config()
    cfg.symbol = "RELIANCE"
    cfg.qty = 1
    cfg.atr_multiplier = 1.5
    eng._focus = "RELIANCE"
    await eng._open("LONG", 1234.56, 1.23, cfg, dt.datetime(2026, 9, 29, 10, 0, tzinfo=IST))
    pos = eng.positions["RELIANCE"]
    assert _on_tick(pos.entry_price, 0.1)
    assert _on_tick(pos.sl_trigger, 0.1)
    assert pos.sl_trigger == pytest.approx(1232.8)  # 1234.6 - 1.845 = 1232.755 -> 1232.8
    database.reset_engine()


@pytest.mark.asyncio
async def test_live_limit_and_stop_prices_use_the_stock_tick(monkeypatch):
    """Checks the prices sent to the SDK. A stub stands in for Groww."""
    from groww_client import GrowwClient

    sent: list[dict] = []

    class StubSdk:
        def place_order(self, **kwargs):
            sent.append(kwargs)
            return {"groww_order_id": f"G{len(sent)}", "order_status": "OPEN"}

    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    tick_sizes.set_ticks({"RELIANCE": 0.1})
    client = GrowwClient(mode="LIVE", token="stub")
    client._sdk = StubSdk()
    await client.place_entry("RELIANCE", "BUY", 1, 1234.56)
    await client.place_sl("RELIANCE", "SELL", 1, 1232.77)
    assert _on_tick(sent[0]["price"], 0.1)
    assert _on_tick(sent[1]["price"], 0.1)
    assert _on_tick(sent[1]["trigger_price"], 0.1)
    assert sent[1]["trigger_price"] == pytest.approx(1232.8)
