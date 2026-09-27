"""Single entry path for opening a position.

Both the manual order form and the autonomous strategy runner go through
`place_entry`, so the bot is subject to exactly the same risk gate as a
human click — there is deliberately no "trusted" internal shortcut that skips
validation.

Critical trading safety invariants enforced here:
  1. Callers must only evaluate signals on closed candles (enforced upstream).
  2. asyncio.Lock + PENDING/TRANSIT refusal (order_guard).
  3. Orphan-SL cancel+verify before any Long↔Short flip (live_broker).
  4. PAPER_TRADING is the default; LIVE_MONEY requires mode + valid session.
  5. Kill switch / day lock short-circuit before any broker call.
"""
from __future__ import annotations

from dataclasses import dataclass

import datetime as dt

from app import state
from app.brokers.base import BrokerOrderError, OrderRequest
from app.services import notifications
from app.services.broadcaster import broadcaster
from app.services.live_broker import (
    LiveBrokerUnavailable,
    broker_has_in_flight_orders,
    cancel_sl_and_verify,
    place_mis_entry_with_sl,
)
from app.services.market_data import DataSource, market_data
from app.services.order_guard import order_guard
from app.services.paper_engine import PaperPosition, round_trip_cost_per_share
from app.services.trade_ledger import close_and_settle, record_open_trade


class EntryRejected(Exception):
    def __init__(self, reason: str, status_code: int = 403):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass
class EntryResult:
    order_id: str
    symbol: str
    side: str
    quantity: int
    filled_price: float
    charges: float
    trade_id: int
    mode: str = "paper"
    sl_order_id: str = ""


# Back-compat alias — many call sites still import this name.
place_paper_entry = None  # set after place_entry is defined


async def place_entry(
    *,
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    source: str = "MANUAL",
    reason: str = "",
    quantity_override: int | None = None,
    allow_flip: bool = False,
) -> EntryResult:
    """Risk-gated entry. Serialised; refuses when another order is in flight."""

    if state.kill_switch_active:
        raise EntryRejected("Kill switch is active. No new orders until reset.", 423)

    if side not in ("BUY", "SELL"):
        raise EntryRejected("side must be BUY or SELL", 400)

    quote = state.latest_quotes.get(symbol)
    if not quote:
        raise EntryRejected(f"No live quote for {symbol} yet.", 404)

    health = market_data.health()
    if market_data.source is DataSource.LIVE:
        if not health.market_open:
            raise EntryRejected(
                f"Market is {health.session} — live quotes are frozen at last close. "
                "No entries outside 09:15–15:30 IST on live data.",
                409,
            )
        if health.stale:
            raise EntryRejected("Live feed looks stale (no price change recently) — entry blocked.", 409)

    if side == "BUY" and stop_loss >= entry_price:
        raise EntryRejected("For a BUY, stop-loss must be below entry price.", 400)
    if side == "SELL" and stop_loss <= entry_price:
        raise EntryRejected("For a SELL, stop-loss must be above entry price.", 400)

    expected_fill = state.paper_engine.expected_fill_price(side, quote["ltp"])

    if side == "BUY" and stop_loss > 0 and expected_fill <= stop_loss:
        raise EntryRejected(
            f"Expected fill ₹{expected_fill} is already at/through the ₹{stop_loss} stop after slippage.", 409
        )
    if side == "SELL" and expected_fill >= stop_loss:
        raise EntryRejected(
            f"Expected fill ₹{expected_fill} is already at/through the ₹{stop_loss} stop after slippage.", 409
        )

    decision = state.risk_manager.validate_order(
        entry_price=expected_fill,
        stop_loss_price=stop_loss,
        bid=quote["bid"],
        ask=quote["ask"],
        enforce_session_cutoff=market_data.source is DataSource.LIVE,
        fixed_quantity=quantity_override,
    )
    if decision.result.value == "rejected":
        await broadcaster.publish("log", {"level": "ERROR", "message": f"[{source}] Order rejected: {decision.reason}"})
        raise EntryRejected(decision.reason, 403)

    quantity = decision.sized_quantity or 0

    cost_per_share = round_trip_cost_per_share(side, expected_fill, quantity, state.paper_engine.slippage_pct)
    edge_ok, edge_reason = state.risk_manager.min_edge_check(expected_fill, target, cost_per_share)
    if not edge_ok:
        await broadcaster.publish("log", {"level": "WARN", "message": f"[{source}] Order rejected: {edge_reason}"})
        raise EntryRejected(edge_reason, 403)

    if state.risk_manager.exposure_capped(expected_fill, stop_loss):
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": f"[{source}] {symbol} size cut to {quantity} by the "
                f"{state.risk_manager.config.max_leverage}x leverage cap — the stop is tight enough that the "
                f"1% risk rule alone would have asked for far more exposure than the account can carry.",
            },
        )

    # ---- serialised dispatch (invariant #2) --------------------------------
    async with order_guard.lock:
        local_block = order_guard.blocking_reason()
        if local_block:
            raise EntryRejected(local_block, 409)

        if state.mode == "live":
            broker_block = await broker_has_in_flight_orders()
            if broker_block:
                raise EntryRejected(broker_block, 409)

        existing = state.paper_engine.positions.get(symbol)
        if existing is not None:
            if existing.side == side:
                raise EntryRejected(f"Already holding a {side} position in {symbol}", 409)
            if not allow_flip:
                raise EntryRejected(
                    f"Already holding a {existing.side} in {symbol}. "
                    "Close it (or pass allow_flip) before reversing.",
                    409,
                )
            # Invariant #3 — cancel Exchange SL and verify before reverse.
            await _flip_position(symbol, existing, source=source)

        tracked = order_guard.register(symbol=symbol, side=side, kind="ENTRY", status="PENDING")
        try:
            if state.mode == "live":
                result = await _dispatch_live(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    stop_loss=stop_loss,
                    target=target,
                    source=source,
                    reason=reason,
                    tracked_id=tracked.local_id,
                )
            else:
                result = await _dispatch_paper(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    stop_loss=stop_loss,
                    target=target,
                    source=source,
                    reason=reason,
                    quote_ltp=quote["ltp"],
                    tracked_id=tracked.local_id,
                )
            order_guard.update(tracked.local_id, status="FILLED", broker_order_id=result.order_id)
            return result
        except Exception:
            order_guard.update(tracked.local_id, status="FAILED")
            raise
        finally:
            # Clear the in-flight slot once we have a terminal outcome so the
            # next signal is not permanently blocked by a finished order.
            order_guard.release(tracked.local_id)


async def _flip_position(symbol: str, existing: PaperPosition, *, source: str) -> None:
    """Cancel Exchange SL (if any), verify, then square the open side."""
    if existing.sl_order_id and existing.mode == "live":
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": (
                    f"[{source}] Orphan-SL prevention: cancelling Exchange SL "
                    f"{existing.sl_order_id} on {symbol} before flip"
                ),
            },
        )
        try:
            await cancel_sl_and_verify(existing.sl_order_id)
        except (BrokerOrderError, LiveBrokerUnavailable) as exc:
            raise EntryRejected(f"Cannot flip {symbol}: {exc}", 409) from exc
        existing.sl_order_id = ""

    result, breaker = await close_and_settle(symbol, f"{source} FLIP SQUARE-OFF")
    if result is None:
        # Position may already be gone after SL fired during cancel.
        if symbol in state.paper_engine.positions:
            raise EntryRejected(f"Could not square off {symbol} before flip", 409)
    if breaker is not None:
        from app.services.safety import trigger_kill_switch

        await trigger_kill_switch(breaker.reason)
        raise EntryRejected(f"Day locked during flip: {breaker.reason}", 423)


async def _dispatch_paper(
    *,
    symbol: str,
    side: str,
    quantity: int,
    stop_loss: float,
    target: float,
    source: str,
    reason: str,
    quote_ltp: float,
    tracked_id: str,
) -> EntryResult:
    order_guard.update(tracked_id, status="TRANSIT")
    order = OrderRequest(
        symbol=symbol,
        side=side,  # type: ignore[arg-type]
        quantity=quantity,
        stop_loss=stop_loss,
        target=target,
    )
    result, fill = state.paper_engine.fill_market_order(order, quote_ltp)
    state.risk_manager.record_fill()

    trade_id = await record_open_trade(
        symbol=symbol,
        side=side,
        quantity=order.quantity,
        entry_price=fill.filled_price,
        stop_loss=stop_loss,
        target=target,
        source=source,
        entry_charges=fill.charges,
        feed_source=market_data.health().source,
        mode="paper",
    )
    position = state.paper_engine.positions.get(symbol)
    if position:
        position.trade_id = trade_id
        position.mode = "paper"
        position.order_status = "FILLED"

    await _announce_fill(
        source=source,
        side=side,
        quantity=order.quantity,
        symbol=symbol,
        filled_price=fill.filled_price,
        stop_loss=stop_loss,
        target=target,
        charges=fill.charges,
        reason=reason,
        mode="paper",
        order_id=result.broker_order_id,
        expected_fill_gap=abs(fill.filled_price - stop_loss),
    )

    return EntryResult(
        order_id=result.broker_order_id,
        symbol=symbol,
        side=side,
        quantity=order.quantity,
        filled_price=fill.filled_price,
        charges=fill.charges,
        trade_id=trade_id,
        mode="paper",
    )


async def _dispatch_live(
    *,
    symbol: str,
    side: str,
    quantity: int,
    stop_loss: float,
    target: float,
    source: str,
    reason: str,
    tracked_id: str,
) -> EntryResult:
    order_guard.update(tracked_id, status="TRANSIT")
    try:
        fill = await place_mis_entry_with_sl(
            symbol=symbol,
            side=side,
            quantity=quantity,
            stop_loss=stop_loss,
            target=target,
        )
    except LiveBrokerUnavailable as exc:
        raise EntryRejected(str(exc), 503) from exc
    except BrokerOrderError as exc:
        raise EntryRejected(f"Live order failed: {exc}", 502) from exc

    state.risk_manager.record_fill()
    sl_id = fill.sl.broker_order_id if fill.sl else ""

    trade_id = await record_open_trade(
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=fill.filled_price,
        stop_loss=stop_loss,
        target=target,
        source=source,
        entry_charges=fill.charges,
        feed_source=market_data.health().source,
        mode="live",
    )

    # Mirror into the in-memory book so bracket monitoring + kill switch work
    # the same way as paper. The Exchange SL is the hard floor; the local
    # monitor still tracks target / trailing.
    state.paper_engine.positions[symbol] = PaperPosition(
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=fill.filled_price,
        stop_loss=stop_loss,
        target=target,
        opened_at=dt.datetime.utcnow(),
        order_id=fill.entry.broker_order_id,
        trade_id=trade_id,
        mode="live",
        entry_broker_order_id=fill.entry.broker_order_id,
        sl_order_id=sl_id,
        order_status="FILLED",
    )

    await _announce_fill(
        source=source,
        side=side,
        quantity=quantity,
        symbol=symbol,
        filled_price=fill.filled_price,
        stop_loss=stop_loss,
        target=target,
        charges=fill.charges,
        reason=reason,
        mode="live",
        order_id=fill.entry.broker_order_id,
        expected_fill_gap=abs(fill.filled_price - stop_loss),
        sl_order_id=sl_id,
    )

    return EntryResult(
        order_id=fill.entry.broker_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled_price=fill.filled_price,
        charges=fill.charges,
        trade_id=trade_id,
        mode="live",
        sl_order_id=sl_id,
    )


async def _announce_fill(
    *,
    source: str,
    side: str,
    quantity: int,
    symbol: str,
    filled_price: float,
    stop_loss: float,
    target: float,
    charges: float,
    reason: str,
    mode: str,
    order_id: str,
    expected_fill_gap: float,
    sl_order_id: str = "",
) -> None:
    mode_tag = "LIVE_MONEY" if mode == "live" else "PAPER"
    sl_bit = f" | Exch SL {sl_order_id}" if sl_order_id else ""
    await broadcaster.publish(
        "log",
        {
            "level": "INFO",
            "message": (
                f"[{source}/{mode_tag}] {side} {quantity} {symbol} @ ₹{filled_price} "
                f"| SL ₹{stop_loss} | TGT ₹{target} | charges ~₹{charges}{sl_bit}"
            ),
        },
    )
    await broadcaster.publish(
        "order_filled",
        {
            "order": {"broker_order_id": order_id, "status": "FILLED", "mode": mode},
            "fill": {
                "order_id": order_id,
                "symbol": symbol,
                "side": side,
                "quantity": quantity,
                "filled_price": filled_price,
                "charges": charges,
                "mode": mode,
                "sl_order_id": sl_order_id,
            },
        },
    )
    await notifications.trade_opened(
        account=state.ACCOUNT_AUTO,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=filled_price,
        charges=charges,
        source=source,
        reason=reason
        or (
            f"Risk-sized to {quantity} at 1% of capital over the ₹{expected_fill_gap:.2f} "
            f"stop distance. SL ₹{stop_loss} · target ₹{target}."
        ),
    )


# Preserve the historical import name used across the bot and routes.
async def place_paper_entry(**kwargs) -> EntryResult:
    return await place_entry(**kwargs)
