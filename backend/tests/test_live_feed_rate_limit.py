"""The desk's live price feed spreads its Groww calls and backs off on a rate limit.

Groww's limit is per account and shared with the bots and scans. The feed used
to ask for a full quote of every symbol in one burst every ~20 s and kept
retrying through a "rate limit" answer. Stub client only; no order path.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import groww_pressure
from app.brokers.groww_client import BrokerOrderError, GrowwClient
from app.services import market_data as md
from app.services.market_data import LiveGrowwFeed

RATE = "Groww LTP fetch failed: Rate limit has breached for your request. Please try again later."


class StubClient:
    def __init__(self, ltp_error: str | None = None, quote_error: str | None = None):
        self.ltp_error = ltp_error
        self.quote_error = quote_error
        self.ltp_calls = 0
        self.quotes: list[str] = []

    async def get_ltp_batch(self, symbols):
        self.ltp_calls += 1
        if self.ltp_error:
            raise BrokerOrderError(self.ltp_error)
        return {s: 100.0 for s in symbols}

    async def get_full_quote(self, symbol):
        self.quotes.append(symbol)
        if self.quote_error:
            raise BrokerOrderError(self.quote_error)
        return SimpleNamespace(ltp=100.0, bid=99.9, ask=100.1, volume=10)


@pytest.fixture(autouse=True)
def _clean():
    groww_pressure.reset_for_tests()
    yield
    groww_pressure.reset_for_tests()


@pytest.mark.asyncio
async def test_depth_goes_round_the_symbols_a_few_at_a_time():
    client = StubClient()
    feed = LiveGrowwFeed(client, ["A", "B", "C", "D", "E"], depth_per_poll=2)
    for _ in range(3):
        await feed._refresh_depth()
    assert client.quotes == ["A", "B", "C", "D", "E", "A"]
    assert feed._spreads["A"] == pytest.approx((0.1, 0.1))


@pytest.mark.asyncio
async def test_a_rate_limit_on_depth_stops_the_round_and_pauses():
    client = StubClient(quote_error="Rate limit has breached for your request")
    feed = LiveGrowwFeed(client, ["A", "B", "C"], depth_per_poll=3)
    await feed._refresh_depth()
    assert client.quotes == ["A"]  # no hammering the rest
    assert feed.rate_limits == 1
    assert feed._paused_until > time.monotonic() + 10
    assert "rate limit" in feed.last_error.lower()
    await feed._refresh_depth()  # depth stays paused
    assert client.quotes == ["A"]
    snap = groww_pressure.snapshot()
    assert snap["feed_rate_limits"] == 1
    assert snap["scans_yielding_now"] is True


@pytest.mark.asyncio
async def test_a_rate_limited_price_call_waits_before_asking_again(monkeypatch):
    monkeypatch.setattr(md, "is_market_open", lambda *a: True)
    client = StubClient(ltp_error=RATE)
    feed = LiveGrowwFeed(client, ["A"], depth_per_poll=0)
    sleeps: list[float] = []

    async def fake_sleep(sec):
        sleeps.append(sec)
        if len(sleeps) >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(md.asyncio, "sleep", fake_sleep)
    with pytest.raises(asyncio.CancelledError):
        async for _ in feed.stream():
            pass
    assert client.ltp_calls == 1
    assert sleeps[0] > 15  # the pause, not the old 5 s retry
    assert feed.last_error.startswith("Groww rate limit")


@pytest.mark.asyncio
async def test_prices_flow_when_groww_answers(monkeypatch):
    monkeypatch.setattr(md, "is_market_open", lambda *a: True)
    feed = LiveGrowwFeed(StubClient(), ["A", "B"], depth_per_poll=1)
    gen = feed.stream()
    tick = await gen.__anext__()
    await gen.aclose()
    assert tick.symbol in {"A", "B"} and tick.ltp == 100.0
    assert feed.last_error is None


@pytest.mark.asyncio
async def test_the_batch_error_is_not_prefixed_twice(monkeypatch):
    client = GrowwClient.__new__(GrowwClient)

    async def fail(chunk):
        raise BrokerOrderError(RATE)

    monkeypatch.setattr(client, "_ltp_chunk", fail)
    with pytest.raises(BrokerOrderError) as err:
        await client.get_ltp_batch(["A"])
    assert str(err.value).count("LTP fetch failed") == 1
