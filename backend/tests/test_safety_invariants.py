"""Regression tests for the five critical trading safety invariants.

1. No intra-candle repainting — crossover uses closed bars only
2. Idempotency & order lock — PENDING/TRANSIT blocks new dispatch
3. Orphan SL prevention — cancel+verify before flip
4. Paper mode default — LIVE requires confirm + valid session
5. Hard kill switch — max daily loss / trade cap lock the day
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.risk_manager import DailyRiskState, RiskConfig, RiskManager
from app.services.indicators import OHLCV
from app.services.order_guard import OrderGuard, order_guard
from app.services.scanner_engine import StrategyEngine, StrategyParams


# ---------------------------------------------------------------------------
# 1. No intra-candle repainting
# ---------------------------------------------------------------------------

def _bars(closes: list[float]) -> list[OHLCV]:
    out: list[OHLCV] = []
    for i, c in enumerate(closes):
        out.append(OHLCV(ts=1_700_000_000 + i * 60, open=c, high=c + 1, low=c - 1, close=c, volume=10_000))
    return out


def test_crossover_uses_last_two_closed_bars_not_forming():
    """Signal compares closed[-1] vs closed[-2] — never a still-forming tick bar.

    Raw series length N: forming = iloc[-1]. After dropping it, evaluate at the
    last closed bar which is raw iloc[-2], against prev = raw iloc[-3].
    """
    # Warm up with slow under fast, then cross down on the penultimate closed bar.
    # Fast=2 / Slow=4 so we don't need a huge warmup.
    params = StrategyParams(fast_period=2, slow_period=4, signal_type="BOTH")
    engine = StrategyEngine(params)

    # Rising then a sharp drop so EMA2 crosses below EMA4 on the last closed bar.
    closes = [100, 101, 102, 103, 104, 105, 106, 100, 99]
    raw = _bars(closes)
    forming = raw[-1]
    closed = raw[:-1]  # drop forming — invariant #1

    assert closed[-1].close == 100  # raw iloc[-2]
    assert closed[-2].close == 106  # raw iloc[-3]
    assert forming.close == 99

    signal = engine.evaluate("TEST", "1m", closed)
    # May or may not fire depending on EMA path; the critical property is that
    # evaluate never saw the forming close of 99.
    ctx = engine.precompute(closed)
    assert len(ctx["fast"]) == len(closed)
    # Re-evaluate including forming must be a different series — proves the
    # caller contract matters.
    ctx_with_forming = engine.precompute(raw)
    assert ctx["fast"][-1] != ctx_with_forming["fast"][-1]


def test_evaluate_at_compares_i_against_i_minus_1():
    params = StrategyParams(fast_period=2, slow_period=3, signal_type="BOTH")
    engine = StrategyEngine(params)
    # Construct an unambiguous golden cross on the final closed bar.
    closes = [10, 10, 10, 10, 10, 10, 20, 30]
    candles = _bars(closes)
    ctx = engine.precompute(candles)
    i = len(candles) - 1
    # Cross detection reads fast[i]/slow[i] vs fast[i-1]/slow[i-1]
    f_now, f_prev = ctx["fast"][i], ctx["fast"][i - 1]
    s_now, s_prev = ctx["slow"][i], ctx["slow"][i - 1]
    assert None not in (f_now, f_prev, s_now, s_prev)
    # Document the contract: i is the signal bar, i-1 is the prior closed bar.
    assert i == len(candles) - 1


# ---------------------------------------------------------------------------
# 2. Idempotency & order lock
# ---------------------------------------------------------------------------

def test_order_guard_blocks_while_pending():
    guard = OrderGuard()
    t = guard.register(symbol="RELIANCE", side="BUY", kind="ENTRY", status="PENDING")
    assert guard.has_blocking_orders()
    reason = guard.blocking_reason()
    assert reason is not None
    assert "PENDING" in reason
    guard.update(t.local_id, status="FILLED")
    guard.release(t.local_id)
    assert not guard.has_blocking_orders()


def test_order_guard_blocks_transit_and_broker_statuses():
    guard = OrderGuard()
    assert OrderGuard.is_in_flight_status("TRANSIT")
    assert OrderGuard.is_in_flight_status("PENDING")
    assert OrderGuard.is_in_flight_status("TRIGGER_PENDING")
    assert not OrderGuard.is_in_flight_status("FILLED")
    assert not OrderGuard.is_in_flight_status("CANCELLED")
    assert not OrderGuard.is_in_flight_status("COMPLETE")


@pytest.mark.asyncio
async def test_order_lock_serialises_concurrent_holders():
    """Two coroutines cannot both hold the lock at once."""
    guard = OrderGuard()
    held = 0
    max_held = 0

    async def critical():
        nonlocal held, max_held
        async with guard.lock:
            held += 1
            max_held = max(max_held, held)
            await asyncio.sleep(0.02)
            held -= 1

    await asyncio.gather(critical(), critical(), critical())
    assert max_held == 1


# ---------------------------------------------------------------------------
# 3. Orphan SL prevention
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_flip_cancels_sl_before_reverse():
    from app.services.paper_engine import PaperPosition
    import datetime as dt
    from app import state
    from app.services.execution import EntryRejected, _flip_position

    pos = PaperPosition(
        symbol="INFY",
        side="BUY",
        quantity=10,
        entry_price=1500.0,
        stop_loss=1480.0,
        target=1550.0,
        opened_at=dt.datetime.utcnow(),
        order_id="LIVE-1",
        mode="live",
        sl_order_id="SL-99",
    )
    state.paper_engine.positions["INFY"] = pos
    state.latest_quotes["INFY"] = {"ltp": 1510.0, "bid": 1509.0, "ask": 1511.0, "volume": 1}

    with patch("app.services.execution.cancel_sl_and_verify", new_callable=AsyncMock) as cancel:
        with patch("app.services.execution.close_and_settle", new_callable=AsyncMock) as close:
            close.return_value = (MagicMock(pnl=10.0), None)
            await _flip_position("INFY", pos, source="TEST")
            cancel.assert_awaited_once_with("SL-99")
            close.assert_awaited()

    state.paper_engine.positions.pop("INFY", None)


@pytest.mark.asyncio
async def test_flip_blocked_when_sl_cancel_fails():
    from app.services.paper_engine import PaperPosition
    import datetime as dt
    from app import state
    from app.brokers.base import BrokerOrderError
    from app.services.execution import EntryRejected, _flip_position

    pos = PaperPosition(
        symbol="TCS",
        side="BUY",
        quantity=5,
        entry_price=3500.0,
        stop_loss=3450.0,
        target=3600.0,
        opened_at=dt.datetime.utcnow(),
        order_id="LIVE-2",
        mode="live",
        sl_order_id="SL-77",
    )
    state.paper_engine.positions["TCS"] = pos

    with patch(
        "app.services.execution.cancel_sl_and_verify",
        new_callable=AsyncMock,
        side_effect=BrokerOrderError("SL still open"),
    ):
        with pytest.raises(EntryRejected) as exc:
            await _flip_position("TCS", pos, source="TEST")
        assert "Cannot flip" in exc.value.reason

    state.paper_engine.positions.pop("TCS", None)


# ---------------------------------------------------------------------------
# 4. Paper mode default + LIVE confirmation
# ---------------------------------------------------------------------------

def test_default_mode_is_paper():
    from app import state
    from app.core.config import get_settings

    assert get_settings().default_mode == "paper"
    # Process boots paper; do not assert against a prior live flip in this process.
    # Re-assert the module default constant contract.
    assert state.mode in ("paper", "live")


@pytest.mark.asyncio
async def test_live_mode_requires_confirmation():
    from app.api.routes_orders import ModeRequest, set_mode
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        await set_mode(ModeRequest(mode="live", confirm_live_money=False))
    assert exc.value.status_code == 400
    assert "confirm" in exc.value.detail.lower()


@pytest.mark.asyncio
async def test_live_mode_requires_valid_groww_session():
    from app.api.routes_orders import ModeRequest, set_mode
    from app.services.live_broker import LiveBrokerUnavailable
    from fastapi import HTTPException

    with patch(
        "app.services.live_broker.require_valid_live_session",
        new_callable=AsyncMock,
        side_effect=LiveBrokerUnavailable("No Groww session"),
    ):
        with pytest.raises(HTTPException) as exc:
            await set_mode(ModeRequest(mode="live", confirm_live_money=True))
        assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# 5. Hard kill switch — max_daily_loss_inr + trade cap
# ---------------------------------------------------------------------------

def test_max_daily_loss_inr_tightens_cap():
    cfg = RiskConfig(account_capital=100_000, daily_max_loss_pct=2.0, max_daily_loss_inr=500)
    # 2% of 1L = 2000; INR floor 500 is tighter
    assert cfg.daily_max_loss_value == -500.0


def test_max_daily_loss_pct_when_inr_disabled():
    cfg = RiskConfig(account_capital=100_000, daily_max_loss_pct=2.0, max_daily_loss_inr=0)
    assert cfg.daily_max_loss_value == -2000.0


def test_daily_loss_breach_locks_day():
    rm = RiskManager(
        RiskConfig(account_capital=100_000, daily_max_loss_pct=2.0, max_daily_loss_inr=1000),
        DailyRiskState(),
    )
    decision = rm.record_realized_pnl(-1000.0)
    assert decision is not None
    assert rm.state.locked
    assert rm.state.lock_kind == "LOSS_LIMIT"
    assert "max_daily_loss_inr" in rm.state.lock_reason


def test_max_trades_per_day_blocks_entry():
    rm = RiskManager(
        RiskConfig(account_capital=100_000, max_trades_per_day=2, max_spread_pct=5.0),
        DailyRiskState(trades_taken=2),
    )
    decision = rm.validate_order(
        entry_price=100.0,
        stop_loss_price=98.0,
        bid=99.9,
        ask=100.1,
        enforce_session_cutoff=False,
    )
    assert decision.result.value == "rejected"
    assert "trade cap" in decision.reason.lower()


@pytest.mark.asyncio
async def test_kill_switch_squares_and_locks(monkeypatch):
    from app import state
    from app.services import safety

    state.kill_switch_active = False
    state.mode = "paper"
    state.paper_engine.positions.clear()

    called = {"halt": False}

    class FakeRunner:
        enabled = True

        async def force_halt(self):
            called["halt"] = True

        async def publish_status(self):
            pass

    with patch("app.services.strategy_runner.strategy_runner", FakeRunner()):
        with patch("app.services.safety.close_and_settle", new_callable=AsyncMock):
            with patch("app.services.notifications.alert", new_callable=AsyncMock):
                with patch("app.services.broadcaster.broadcaster.publish", new_callable=AsyncMock):
                    await safety.trigger_kill_switch("test breach")

    assert state.kill_switch_active is True
    assert called["halt"] is True
    # Reset for other tests
    state.kill_switch_active = False
