"""A Groww rejection says why, in Groww's words. Nothing here places an order."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from groww_client import GrowwClient, OrderAck
from strategy_engine import SlCancelFailed, StrategyEngine


class _ReadOnlySdk:
    """Answers order reads only. Any order call fails the test."""

    def __init__(self, detail):
        self.detail = detail
        self.calls = []

    def get_order_detail(self, segment, groww_order_id, timeout=None):
        self.calls.append(("get_order_detail", segment, groww_order_id))
        return self.detail

    def __getattr__(self, name):
        if name in ("place_order", "modify_order", "cancel_order"):
            raise AssertionError(f"{name} must not be called")
        raise AttributeError(name)


def _client(sdk) -> GrowwClient:
    client = GrowwClient(mode="PAPER")
    client.mode = "LIVE"  # read path only; no order is sent in these tests
    client._sdk = sdk
    client.adopt_saved_session = lambda **_: None
    return client


def test_the_remark_is_read_from_the_order_detail_and_cached():
    sdk = _ReadOnlySdk({"payload": {"order_status": "REJECTED", "remark": "RMS: Margin Exceeds, Required 1,12,000"}})
    client = _client(sdk)
    assert asyncio.run(client.order_remark("GRW1")) == "RMS: Margin Exceeds, Required 1,12,000"
    assert asyncio.run(client.order_remark("GRW1")) == "RMS: Margin Exceeds, Required 1,12,000"
    assert sdk.calls == [("get_order_detail", "CASH", "GRW1")]


def test_no_remark_or_a_failed_read_gives_an_empty_reason():
    assert asyncio.run(_client(_ReadOnlySdk({"payload": {"order_status": "REJECTED"}})).order_remark("GRW2")) == ""

    class Broken(_ReadOnlySdk):
        def get_order_detail(self, segment, groww_order_id, timeout=None):
            raise RuntimeError("timeout")

    assert asyncio.run(_client(Broken({})).order_remark("GRW3")) == ""
    assert asyncio.run(GrowwClient(mode="PAPER").order_remark("PAPER1")) == ""


class _RejectingBroker:
    """Accepts the order id, then reports it REJECTED with a remark."""

    def __init__(self, remark: str):
        self.remark = remark

    async def read_order(self, order_id):
        return "REJECTED", None

    async def order_remark(self, order_id):
        return self.remark


def _engine(broker) -> StrategyEngine:
    engine = StrategyEngine.__new__(StrategyEngine)
    engine.broker = broker

    async def _no_wait(_s):
        return None

    engine._sleep = _no_wait
    return engine


@pytest.mark.asyncio
async def test_a_rejection_after_acceptance_shows_groww_reason():
    engine = _engine(_RejectingBroker("Stock is not allowed for intraday (MIS)"))
    with pytest.raises(SlCancelFailed) as err:
        await engine._require_live_fill(OrderAck("GRW9", "OPEN"), SimpleNamespace())
    assert str(err.value) == "Groww REJECTED the order: Stock is not allowed for intraday (MIS). Nothing was booked."


@pytest.mark.asyncio
async def test_without_a_reason_the_message_stays_as_before():
    engine = _engine(_RejectingBroker(""))
    with pytest.raises(SlCancelFailed) as err:
        await engine._require_live_fill(OrderAck("GRW9", "OPEN"), SimpleNamespace())
    assert str(err.value) == "Groww REJECTED the order. Nothing was booked."


@pytest.mark.asyncio
async def test_an_immediate_rejection_keeps_the_message_groww_sent():
    engine = _engine(_RejectingBroker("ignored"))
    with pytest.raises(SlCancelFailed) as err:
        await engine._require_live_fill(OrderAck("GRW9", "REJECTED", None, "Price outside circuit"), SimpleNamespace())
    assert "Price outside circuit" in str(err.value)
