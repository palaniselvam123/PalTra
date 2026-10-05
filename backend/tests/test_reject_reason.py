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


# ---- the refusal reaches the screen and Telegram -----------------------------

import datetime as dt  # noqa: E402

from strategy_engine import order_refused_alert  # noqa: E402

_MARGIN = "Groww REJECTED the order: Add ₹20092.46 to your Groww Balance to place the order. Nothing was booked."


@pytest.fixture
def paper_engine(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database

    database.reset_engine()
    database.init_db()
    from tests.test_sma_atr_terminal import _FakeBroker

    return StrategyEngine(broker=_FakeBroker())


def test_the_refusal_text_is_groww_s_own_words():
    text = order_refused_alert(mode="LIVE", symbol="ANTELOPUS", text=_MARGIN, when=dt.datetime(2026, 10, 5, 14, 44, 16))
    assert text.splitlines() == [
        "PalTra order refused",
        "LIVE ANTELOPUS",
        _MARGIN,
        "05 Oct 14:44:16 IST",
    ]


def test_a_refusal_alerts_once_and_stays_on_the_stock(paper_engine, monkeypatch):
    sent: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", sent.append)
    engine = paper_engine
    cfg = engine.load_config()
    cfg.trade_symbols = "ANTELOPUS"
    engine._cfg_cache = cfg
    engine._focus = "ANTELOPUS"
    engine._note_broker_block(SlCancelFailed(_MARGIN))
    engine._note_broker_block(SlCancelFailed(_MARGIN))  # retried next minute
    assert len(sent) == 1 and "Add ₹20092.46" in sent[0]
    # The next tick replaces the note; the refusal is still on the stock.
    engine._signals["ANTELOPUS"] = "ANTELOPUS no order — waiting for an SMA cross."
    book = next(b for b in engine.snapshot()["books"] if b["symbol"] == "ANTELOPUS")
    assert book["last_reject"] == _MARGIN
    assert book["last_reject_at"]
    # A different reason is news and alerts again.
    engine._note_broker_block(SlCancelFailed("Groww REJECTED the order: Stock is not allowed for intraday (MIS). Nothing was booked."))
    assert len(sent) == 2
