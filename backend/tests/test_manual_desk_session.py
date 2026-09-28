"""Practice desk orders can fill after the close. Real Groww orders cannot."""
from __future__ import annotations

import pytest

from app import state
from app.services import manual_desk
from app.services.manual_desk import ManualOrderRejected
from app.services.market_data import DataSource, FeedHealth, market_data
from app.services.paper_engine import PaperEngine


def _closed_health() -> FeedHealth:
    return FeedHealth(
        source="live",
        session="CLOSED",
        market_open=False,
        connected=True,
        last_tick_at=None,
        last_change_at=None,
        stale=False,
        error=None,
        symbols=1,
        poll_interval_sec=2.0,
    )


@pytest.fixture
def desk(monkeypatch):
    engine = PaperEngine()
    monkeypatch.setattr(state, "manual_engine", engine)
    monkeypatch.setattr(state, "manual_live", False)
    monkeypatch.setattr(
        state,
        "latest_quotes",
        {"WIPRO": {"ltp": 100.0, "bid": 99.9, "ask": 100.1, "volume": 1}},
    )
    monkeypatch.setattr(market_data, "source", DataSource.LIVE)
    monkeypatch.setattr(market_data, "health", _closed_health)
    monkeypatch.setattr(manual_desk.instrument_master, "get", lambda _symbol: None)

    async def margin():
        return {
            "funds_source": "paper",
            "margin_available": 1_000_000,
            "balance": 100_000,
            "max_leverage": 50,
            "open_exposure": 0,
        }

    async def record_open_trade(**_kwargs):
        return 7

    async def publish(*_args, **_kwargs):
        return None

    async def trade_opened(**_kwargs):
        return None

    monkeypatch.setattr(manual_desk, "margin_snapshot", margin)
    monkeypatch.setattr(manual_desk, "record_open_trade", record_open_trade)
    monkeypatch.setattr(manual_desk.broadcaster, "publish", publish)
    monkeypatch.setattr(manual_desk.notifications, "trade_opened", trade_opened)
    return engine


@pytest.mark.asyncio
async def test_practice_buy_fills_after_the_close(desk):
    fill = await manual_desk.place(symbol="WIPRO", side="BUY", quantity=1)
    assert fill.order_id.startswith("PAPER-")
    assert fill.symbol == "WIPRO"
    assert fill.quantity == 1
    assert "WIPRO" in desk.positions


@pytest.mark.asyncio
async def test_real_groww_buy_waits_until_the_open(desk, monkeypatch):
    monkeypatch.setattr(state, "manual_live", True)
    with pytest.raises(ManualOrderRejected) as exc:
        await manual_desk.place(symbol="WIPRO", side="BUY", quantity=1)
    assert exc.value.status_code == 409
    assert "09:15" in exc.value.reason
    assert "WIPRO" not in desk.positions
