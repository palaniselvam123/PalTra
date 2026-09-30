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


def _open_health() -> FeedHealth:
    closed = _closed_health()
    return FeedHealth(
        source=closed.source,
        session="OPEN",
        market_open=True,
        connected=True,
        last_tick_at=closed.last_tick_at,
        last_change_at=closed.last_change_at,
        stale=False,
        error=None,
        symbols=1,
        poll_interval_sec=2.0,
    )


def _arm_live(monkeypatch):
    monkeypatch.setattr(state, "manual_live", True)
    monkeypatch.setattr(market_data, "health", _open_health)

    async def margin():
        return {
            "funds_source": "groww",
            "margin_available": 1_000_000,
            "balance": 100_000,
            "max_leverage": 1,
            "open_exposure": 0,
            "funds_error": None,
        }

    monkeypatch.setattr(manual_desk, "margin_snapshot", margin)


@pytest.mark.asyncio
async def test_live_order_without_a_groww_id_is_not_booked(desk, monkeypatch):
    from app.brokers.base import OrderResult

    _arm_live(monkeypatch)

    class Client:
        async def is_token_valid(self):
            return True

        async def place_order(self, _order):
            return OrderResult(broker_order_id="", status="PLACED", message="Invalid trading symbol.")

    monkeypatch.setattr("app.services.groww_funds.groww_client", lambda: Client())
    with pytest.raises(ManualOrderRejected) as exc:
        await manual_desk.place(symbol="WIPRO", side="BUY", quantity=1)
    assert exc.value.status_code == 502
    assert "Invalid trading symbol" in exc.value.reason
    assert "WIPRO" not in desk.positions


@pytest.mark.asyncio
async def test_live_order_books_only_the_groww_id(desk, monkeypatch):
    from app.brokers.base import OrderResult

    _arm_live(monkeypatch)

    class Client:
        async def is_token_valid(self):
            return True

        async def place_order(self, _order):
            return OrderResult(broker_order_id="GMKTESTORDER1", status="EXECUTED", filled_price=100.0)

        async def get_order_fill(self, order_id):
            assert order_id == "GMKTESTORDER1"
            return "EXECUTED", 100.0

    monkeypatch.setattr("app.services.groww_funds.groww_client", lambda: Client())
    fill = await manual_desk.place(symbol="WIPRO", side="BUY", quantity=1)
    assert fill.sent_to_groww is True
    assert fill.order_id == "GMKTESTORDER1"
    assert fill.status == "EXECUTED"
    assert fill.filled_price == 100.0
    assert desk.positions["WIPRO"].order_id == "GMKTESTORDER1"


@pytest.mark.asyncio
async def test_practice_position_is_not_sold_on_groww(desk, monkeypatch):
    await manual_desk.place(symbol="WIPRO", side="BUY", quantity=1)
    assert desk.positions["WIPRO"].order_id.startswith("PAPER-")
    monkeypatch.setattr(state, "manual_live", True)

    async def explode(_order):
        raise AssertionError("a practice close must not call Groww")

    async def settle(symbol, _reason, account=None, exit_price=None):
        assert exit_price is None
        state.manual_engine.positions.pop(symbol, None)

        class _Closed:
            pnl = 0.0
            exit_price = 100.0

        return _Closed(), None

    monkeypatch.setattr(manual_desk, "_send_groww_order", explode)
    monkeypatch.setattr(manual_desk, "close_and_settle", settle)
    await manual_desk.close("WIPRO")
    assert "WIPRO" not in desk.positions


@pytest.mark.asyncio
async def test_a_rejected_desk_exit_is_not_retried_on_the_next_tick(desk, monkeypatch):
    manual_desk._exiting.clear()
    manual_desk._exit_blocked_until.clear()
    await manual_desk.place(symbol="WIPRO", side="BUY", quantity=1, stop_loss=90)
    calls: list[str] = []

    async def boom(symbol, reason="MANUAL CLOSE"):
        calls.append(reason)
        raise ManualOrderRejected("Intraday orders are not available as market is about to close")

    monkeypatch.setattr(manual_desk, "close", boom)
    await manual_desk.monitor_tick("WIPRO", 80)
    await manual_desk.monitor_tick("WIPRO", 80)
    assert calls == ["DESK STOP-LOSS HIT"]
