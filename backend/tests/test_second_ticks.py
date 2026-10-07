"""Second-by-second prices: batched Groww LTP, the tick record, and the live gap-fade exit.

A stub stands in for the Groww SDK (quotes only; it has no order methods). PAPER only.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time

import pandas as pd
import pytest

from tests.test_gap_fade_confirm import _pullback_then_reversal
from tests.test_gap_mode import _day, _settings, db  # noqa: F401


class StubSdk:
    def __init__(self):
        self.batch_calls = 0
        self.single_calls = 0
        self.prices = {"TCS": 101.5, "INFY": 202.25}

    def get_ltp(self, exchange_trading_symbols, segment, timeout):  # noqa: ARG002
        if len(exchange_trading_symbols) > 1:
            self.batch_calls += 1
        else:
            self.single_calls += 1
        return {"payload": {key: self.prices[key.split("_", 1)[1]] for key in exchange_trading_symbols}}


def _client(monkeypatch):
    import groww_client
    from groww_client import GrowwClient

    monkeypatch.setattr(groww_client, "desk_session_token", lambda: "")
    monkeypatch.setattr(groww_client, "_env_access_token", lambda: "")
    monkeypatch.setattr(groww_client, "market_is_open", lambda now=None: True)
    client = GrowwClient(mode="PAPER", token="test-token")
    sdk = StubSdk()
    client._require_sdk = lambda: sdk
    ts = int(dt.datetime.now(groww_client.IST).replace(second=0, microsecond=0).timestamp())
    frame = pd.DataFrame([{"ts": ts, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 10}])
    for name in ("TCS", "INFY"):
        client._quotes[name] = (100.0, frame, time.monotonic())
        client._candle_frames[name] = frame
        client._candle_at[name] = time.monotonic()
    return client, sdk


def test_one_batched_call_updates_every_stock_and_refresh_serves_it(monkeypatch):
    client, sdk = _client(monkeypatch)
    got = asyncio.run(client.refresh_ltps(["TCS", "infy", "NOTLOADED"]))
    assert got == {"TCS": 101.5, "INFY": 202.25} and sdk.batch_calls == 1
    # The forming candle follows the new price.
    assert float(client._quotes["TCS"][1].iloc[-1]["close"]) == 101.5
    assert float(client._quotes["TCS"][1].iloc[-1]["high"]) == 101.5
    # Within the second, no second call.
    assert asyncio.run(client.refresh_ltps(["TCS"])) == {} and sdk.batch_calls == 1
    # Even after the 3 s quote interval, refresh() serves the batched price without its own call.
    client._quotes["TCS"] = (101.5, client._quotes["TCS"][1], time.monotonic() - 10)
    ltp, _frame, _src = asyncio.run(client.refresh("TCS"))
    assert ltp == 101.5 and sdk.single_calls == 0


def test_no_batch_without_a_session_or_outside_market_hours(monkeypatch):
    import groww_client

    client, sdk = _client(monkeypatch)
    client.token = ""
    assert asyncio.run(client.refresh_ltps(["TCS"])) == {}
    client.token = "test-token"
    monkeypatch.setattr(groww_client, "market_is_open", lambda now=None: False)
    assert asyncio.run(client.refresh_ltps(["TCS"])) == {} and sdk.batch_calls == 0


def test_tick_store_keeps_one_price_per_second_and_prunes(db):  # noqa: F811
    import tick_store

    tick_store.take()
    base = int(time.time()) - 120  # recent: older than TICK_KEEP_DAYS is pruned
    tick_store.add("tcs", base, 100.0)
    tick_store.add("TCS", base + 0.4, 100.5)  # same second: the later price wins
    tick_store.add("TCS", base + 1, 101.0)
    tick_store.add("INFY", base, 50.0)
    # Not yet written: still readable.
    assert tick_store.between("TCS", base, base + 60) == [[base, 100.5], [base + 1, 101.0]]
    asyncio.run(tick_store.flush(force=True))
    tick_store.add("TCS", base + 1, 101.0)  # a repeat of a written second is not stored twice
    asyncio.run(tick_store.flush(force=True))
    assert tick_store.between("TCS", base, base + 60) == [[base, 100.5], [base + 1, 101.0]]
    assert tick_store.records("GROWW") and not tick_store.records("SIMULATOR") and not tick_store.records("REPLAY")


def test_ticks_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database
    import tick_store

    database.reset_engine()
    database.init_db()
    tick_store.take()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        tick_store.add("SBIN", 1_790_000_005, 800.25)
        body = client.get("/api/ticks", params={"symbol": "sbin", "start": 1_790_000_000, "end": 1_790_000_060}).json()
        assert body["symbol"] == "SBIN" and body["ticks"] == [[1_790_000_005, 800.25]]
        assert client.get("/api/ticks", params={"symbol": "SBIN", "start": 10, "end": 5}).status_code == 400
    tick_store.take()
    database.reset_engine()


@pytest.mark.asyncio
async def test_live_fade_exits_inside_the_reversal_minute(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731
    frame = pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(dt.date(2026, 9, 22), _pullback_then_reversal))
    base = dict(use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.01, gap_entry_short=-0.05,
                gap_exit_short=-0.01, gap_giveback_pct=30, gap_fade_confirm_sma=True)

    async def first_short(**over):
        trades = await replay_symbol(frame, "SIMG", [dt.date(2026, 9, 22)], _settings(db, **{**base, **over}))
        return next(t for t in trades if t["direction"] == "SHORT")

    closed = await first_short()
    live = await first_short(gap_fade_intrabar=True)
    assert live["exit_reason"] == "GAP_FADE" and live["entry_time"] == closed["entry_time"]
    assert live["exit_time"] < closed["exit_time"]  # inside the reversal minute, not after it closed
    assert live["exit_price"] <= closed["exit_price"]  # a short: out lower, before the bounce ran
