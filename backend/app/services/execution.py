"""Single entry path for opening a position.

Both the manual order form and the autonomous strategy runner go through
`place_paper_entry`, so the bot is subject to exactly the same risk gate as a
human click — there is deliberately no "trusted" internal shortcut that skips
validation.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import state
from app.brokers.base import OrderRequest
from app.services.broadcaster import broadcaster
from app.services import notifications
from app.services.market_data import DataSource, market_data
from app.services.paper_engine import round_trip_cost_per_share
from app.services.trade_ledger import record_open_trade


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


async def place_paper_entry(
    *,
    symbol: str,
    side: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    source: str = "MANUAL",
    reason: str = "",
    quantity_override: int | None = None,
) -> EntryResult:
    if state.kill_switch_active:
        raise EntryRejected("Kill switch is active. No new orders until reset.", 423)

    if side not in ("BUY", "SELL"):
        raise EntryRejected("side must be BUY or SELL", 400)

    if symbol in state.paper_engine.positions:
        raise EntryRejected(f"Already holding a position in {symbol}", 409)

    quote = state.latest_quotes.get(symbol)
    if not quote:
        raise EntryRejected(f"No live quote for {symbol} yet.", 404)

    # On real market data, prices outside the session are frozen at last
    # close. Filling against them would produce trades that could never have
    # happened, which is exactly the self-deception forward paper trading is
    # meant to avoid.
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

    # Direction sanity: a stop-loss on the wrong side of entry means the trade
    # is already beyond its own risk limit before it opens.
    if side == "BUY" and stop_loss >= entry_price:
        raise EntryRejected("For a BUY, stop-loss must be below entry price.", 400)
    if side == "SELL" and stop_loss <= entry_price:
        raise EntryRejected("For a SELL, stop-loss must be above entry price.", 400)

    # Size against the price this will REALLY fill at, not the price the
    # strategy hoped for. The two differ by the slippage haircut, and when the
    # stop is tight that gap is a large fraction of the risk being sized on.
    expected_fill = state.paper_engine.expected_fill_price(side, quote["ltp"])

    # If slippage alone carries the fill past the stop, the position opens
    # already beyond its own risk limit and would be closed on the next tick.
    # A long's stop sits below its entry and a short's sits above, so the
    # violation is the fill landing on the wrong side of the stop.
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
        # The cut-off rule refers to the real NSE session; on the synthetic feed
        # there isn't one, so it would just make the sandbox untestable after
        # 3pm. Live data always enforces it.
        enforce_session_cutoff=market_data.source is DataSource.LIVE,
        fixed_quantity=quantity_override,
    )
    if decision.result.value == "rejected":
        await broadcaster.publish("log", {"level": "ERROR", "message": f"[{source}] Order rejected: {decision.reason}"})
        raise EntryRejected(decision.reason, 403)

    quantity = decision.sized_quantity or 0

    # Would this trade pay for itself? Slippage and charges are both charged
    # twice, so a target closer than that hurdle is a guaranteed loss however
    # perfectly the strategy plays out.
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

    if state.mode == "live":
        # Live dispatch stays disabled until a broker session is validated end
        # to end in Paper mode — see README.
        raise EntryRejected("Live order dispatch is not enabled in this build. Use Paper mode.", 501)

    order = OrderRequest(
        symbol=symbol,
        side=side,
        quantity=quantity,
        stop_loss=stop_loss,
        target=target,
    )
    result, fill = state.paper_engine.fill_market_order(order, quote["ltp"])
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
    )
    position = state.paper_engine.positions.get(symbol)
    if position:
        position.trade_id = trade_id

    await broadcaster.publish(
        "log",
        {
            "level": "INFO",
            "message": f"[{source}] {side} {order.quantity} {symbol} @ ₹{fill.filled_price} "
            f"| SL ₹{stop_loss} | TGT ₹{target} | charges ~₹{fill.charges}",
        },
    )
    await broadcaster.publish("order_filled", {"order": result.__dict__, "fill": fill.__dict__})

    await notifications.trade_opened(
        account=state.ACCOUNT_AUTO,
        symbol=symbol,
        side=side,
        quantity=order.quantity,
        price=fill.filled_price,
        charges=fill.charges,
        source=source,
        reason=reason
        or (
            f"Risk-sized to {order.quantity} at 1% of capital over the ₹{abs(expected_fill - stop_loss):.2f} "
            f"stop distance. SL ₹{stop_loss} · target ₹{target}."
        ),
    )

    return EntryResult(
        order_id=result.broker_order_id,
        symbol=symbol,
        side=side,
        quantity=order.quantity,
        filled_price=fill.filled_price,
        charges=fill.charges,
        trade_id=trade_id,
    )
