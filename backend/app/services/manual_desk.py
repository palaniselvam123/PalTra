"""The discretionary trading desk — a second virtual wallet, separate from the
strategy account in every way that matters.

Two deliberate differences from `execution.place_paper_entry`:

* **You choose the quantity.** The strategy account derives size from the 1%
  rule because a bot cannot judge conviction. A human can, so the desk takes
  the quantity you type — the way a broker's order ticket does.
* **No daily circuit breaker.** The loss limit exists to stop an unattended
  bot compounding a bad day. Here you are the risk manager, and halting your
  own account for the rest of the day without being asked would be wrong.

What it does NOT relax is affordability. Quantity is checked against the
wallet's margin — balance × leverage — because a position the account cannot
carry is not a risk decision, it is an order a real broker would reject.
Brackets are optional and, when set, enforced tick-by-tick exactly as the
strategy account's are.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import state
from app.brokers.base import OrderRequest
from app.services import notifications
from app.services.broadcaster import broadcaster
from app.services.instruments import instrument_master
from app.services.market_data import DataSource, market_data
from app.services.paper_engine import estimate_charges, round_trip_cost_per_share
from app.services.trade_ledger import close_and_settle, record_open_trade
from app.strategies.scanner import NIFTY50_UNIVERSE

ACCOUNT = state.ACCOUNT_MANUAL


class ManualOrderRejected(Exception):
    def __init__(self, reason: str, status_code: int = 400):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


@dataclass
class ManualFill:
    order_id: str
    symbol: str
    side: str
    quantity: int
    filled_price: float
    charges: float
    trade_id: int


def watchlist() -> list[dict]:
    """Every symbol the feed is currently streaming, with its live quote.

    Restricted to what actually ticks — listing symbols with no price would
    give you a buy button that silently cannot fill.
    """
    rows = []
    for symbol in market_data.symbols:
        quote = state.latest_quotes.get(symbol)
        if not quote:
            continue
        ltp = quote["ltp"]
        spread = quote["ask"] - quote["bid"]
        inst = instrument_master.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "ltp": round(ltp, 2),
                "bid": round(quote["bid"], 2),
                "ask": round(quote["ask"], 2),
                "spread": round(spread, 2),
                "spread_pct": round(spread / ltp * 100, 3) if ltp else None,
                "volume": quote.get("volume", 0),
                "in_universe": symbol in NIFTY50_UNIVERSE,
                "has_position": symbol in state.manual_engine.positions,
                # None only for a symbol the instrument master itself doesn't
                # recognise, which should not happen for anything that made it
                # into the watchlist via the add path.
                "intraday_allowed": inst.intraday_allowed if inst else None,
            }
        )
    return sorted(rows, key=lambda r: r["symbol"])


async def margin_snapshot() -> dict:
    """What the desk can currently deploy."""
    from app.services.trade_ledger import get_account_summary

    summary = await get_account_summary(ACCOUNT)
    leverage = state.risk_manager.config.max_leverage
    buying_power = summary["balance"] * leverage
    return {
        **summary,
        "max_leverage": leverage,
        "buying_power": round(buying_power, 2),
        "margin_available": round(max(0.0, buying_power - summary["open_exposure"]), 2),
    }


def preview(symbol: str, side: str, quantity: int) -> dict:
    """Order-ticket preview: what this will cost before you commit to it.

    The number that matters is `breakeven_move` — how far the price has to
    travel before the trade is worth anything. Quoting charges alone
    understates it badly, because slippage is charged on both legs and is
    invisible in the P&L: it is baked into the fill prices rather than shown
    as a line item. On a ₹2,632 stock the charges were ₹142 while slippage was
    ₹263, so a charges-only figure hid two thirds of the real hurdle.
    """
    quote = state.latest_quotes.get(symbol)
    if not quote:
        raise ManualOrderRejected(f"No live quote for {symbol} yet.", 404)

    fill = state.manual_engine.expected_fill_price(side, quote["ltp"])
    charges = estimate_charges(side, fill, quantity)
    exit_side = "SELL" if side == "BUY" else "BUY"
    round_trip_charges = round(charges + estimate_charges(exit_side, fill, quantity), 2)
    slippage_round_trip = round(fill * (state.manual_engine.slippage_pct / 100) * 2 * quantity, 2)
    breakeven_per_share = round_trip_cost_per_share(side, fill, quantity, state.manual_engine.slippage_pct)

    return {
        "symbol": symbol,
        "side": side,
        "quantity": quantity,
        "ltp": round(quote["ltp"], 2),
        "expected_fill": fill,
        "order_value": round(fill * quantity, 2),
        "estimated_charges": charges,
        "estimated_round_trip_charges": round_trip_charges,
        "slippage_round_trip": slippage_round_trip,
        "total_round_trip_cost": round(round_trip_charges + slippage_round_trip, 2),
        "breakeven_move_per_share": round(breakeven_per_share, 2),
        "breakeven_move_pct": round(breakeven_per_share / fill * 100, 3) if fill else None,
    }


async def place(
    *, symbol: str, side: str, quantity: int, stop_loss: float | None = None, target: float | None = None
) -> ManualFill:
    if side not in ("BUY", "SELL"):
        raise ManualOrderRejected("side must be BUY or SELL")
    if quantity <= 0:
        raise ManualOrderRejected("Quantity must be at least 1")
    if symbol in state.manual_engine.positions:
        raise ManualOrderRejected(f"Already holding {symbol} on the manual desk. Close it first.", 409)

    # A symbol can be watched without being MIS-eligible — Groww's own app
    # works the same way. Only block here, on the order, not on the add:
    # a real broker refuses the order, not the watch.
    inst = instrument_master.get(symbol)
    if inst is not None and not inst.intraday_allowed:
        raise ManualOrderRejected(
            f"{symbol} ({inst.name}) is not intraday-eligible on Groww right now — MIS orders "
            "would be refused (delivery-only, surveillance, or similar). This paper engine mirrors "
            "that restriction rather than letting you trade something a real broker would not.",
            409,
        )

    quote = state.latest_quotes.get(symbol)
    if not quote:
        raise ManualOrderRejected(f"No live quote for {symbol} yet.", 404)

    # Same session honesty rule as the strategy account: filling against
    # frozen out-of-hours prices invents trades that could never have happened.
    health = market_data.health()
    if market_data.source is DataSource.LIVE:
        if not health.market_open:
            raise ManualOrderRejected(
                f"Market is {health.session} — live quotes are frozen at last close, so no entries.", 409
            )
        if health.stale:
            raise ManualOrderRejected("Live feed looks stale — entry blocked.", 409)

    fill_price = state.manual_engine.expected_fill_price(side, quote["ltp"])
    order_value = fill_price * quantity

    margin = await margin_snapshot()
    if order_value > margin["margin_available"]:
        raise ManualOrderRejected(
            f"Order needs ₹{order_value:,.0f} but only ₹{margin['margin_available']:,.0f} of margin is free "
            f"(balance ₹{margin['balance']:,.0f} × {margin['max_leverage']}× leverage, "
            f"₹{margin['open_exposure']:,.0f} already deployed).",
            403,
        )

    # Brackets are optional here, but a stop on the wrong side of entry is
    # always a mistake rather than a preference.
    if stop_loss:
        if side == "BUY" and stop_loss >= fill_price:
            raise ManualOrderRejected("For a BUY, stop-loss must be below the entry price.")
        if side == "SELL" and stop_loss <= fill_price:
            raise ManualOrderRejected("For a SELL, stop-loss must be above the entry price.")
    if target:
        if side == "BUY" and target <= fill_price:
            raise ManualOrderRejected("For a BUY, target must be above the entry price.")
        if side == "SELL" and target >= fill_price:
            raise ManualOrderRejected("For a SELL, target must be below the entry price.")

    order = OrderRequest(
        symbol=symbol, side=side, quantity=quantity, stop_loss=stop_loss or 0.0, target=target or 0.0
    )
    result, fill = state.manual_engine.fill_market_order(order, quote["ltp"])

    trade_id = await record_open_trade(
        symbol=symbol,
        side=side,
        quantity=quantity,
        entry_price=fill.filled_price,
        stop_loss=stop_loss or 0.0,
        target=target or 0.0,
        source="MANUAL",
        entry_charges=fill.charges,
        account=ACCOUNT,
        feed_source=market_data.health().source,
    )
    position = state.manual_engine.positions.get(symbol)
    if position:
        position.trade_id = trade_id

    await broadcaster.publish(
        "log",
        {
            "level": "INFO",
            "message": f"[DESK] {side} {quantity} {symbol} @ ₹{fill.filled_price} "
            f"| value ₹{order_value:,.0f} | charges ~₹{fill.charges}"
            + (f" | SL ₹{stop_loss}" if stop_loss else "")
            + (f" | TGT ₹{target}" if target else ""),
        },
    )
    await broadcaster.publish("manual_order_filled", {"symbol": symbol, "side": side, "quantity": quantity})

    bracket = []
    if stop_loss:
        bracket.append(f"SL ₹{stop_loss}")
    if target:
        bracket.append(f"target ₹{target}")
    await notifications.trade_opened(
        account=ACCOUNT,
        symbol=symbol,
        side=side,
        quantity=quantity,
        price=fill.filled_price,
        charges=fill.charges,
        source="MANUAL",
        reason="Manual order you placed on the trading desk"
        + (" · " + ", ".join(bracket) + " enforced on every tick" if bracket else " · no bracket set"),
    )

    return ManualFill(
        order_id=result.broker_order_id,
        symbol=symbol,
        side=side,
        quantity=quantity,
        filled_price=fill.filled_price,
        charges=fill.charges,
        trade_id=trade_id,
    )


async def close(symbol: str, reason: str = "MANUAL CLOSE") -> dict:
    result, _ = await close_and_settle(symbol, reason, account=ACCOUNT)
    if result is None:
        raise ManualOrderRejected(f"No open manual position in {symbol} (or no live quote yet).", 404)
    return {"symbol": symbol, "pnl": result.pnl, "exit_price": result.exit_price}


async def square_off_all() -> list[dict]:
    out = []
    for symbol in list(state.manual_engine.positions.keys()):
        result, _ = await close_and_settle(symbol, "DESK SQUARE-OFF ALL", account=ACCOUNT)
        if result:
            out.append({"symbol": symbol, "pnl": result.pnl})
    return out


async def monitor_tick(symbol: str, ltp: float) -> None:
    """Bracket enforcement for the desk, mirroring the strategy account's.

    A stop-loss you typed into a form is only a number in a table unless
    something checks it on every tick — so this runs regardless of whether the
    bot is on, exactly like `strategy_runner.monitor_tick`.
    """
    position = state.manual_engine.positions.get(symbol)
    if position is None or symbol in _exiting:
        return

    reason: str | None = None
    if position.side == "BUY":
        if position.stop_loss and ltp <= position.stop_loss:
            reason = "STOP-LOSS HIT"
        elif position.target and ltp >= position.target:
            reason = "TARGET HIT"
    else:
        if position.stop_loss and ltp >= position.stop_loss:
            reason = "STOP-LOSS HIT"
        elif position.target and ltp <= position.target:
            reason = "TARGET HIT"

    if reason is None:
        return

    _exiting.add(symbol)
    try:
        await close_and_settle(symbol, f"DESK {reason}", account=ACCOUNT)
    finally:
        _exiting.discard(symbol)


_exiting: set[str] = set()


def positions() -> list[dict]:
    out = []
    for pos in state.manual_engine.positions.values():
        quote = state.latest_quotes.get(pos.symbol)
        ltp = quote["ltp"] if quote else pos.entry_price
        gross = (
            (ltp - pos.entry_price) * pos.quantity
            if pos.side == "BUY"
            else (pos.entry_price - ltp) * pos.quantity
        )
        out.append(
            {
                "symbol": pos.symbol,
                "side": pos.side,
                "quantity": pos.quantity,
                "entry_price": pos.entry_price,
                "stop_loss": pos.stop_loss,
                "target": pos.target,
                "order_id": pos.order_id,
                "trade_id": pos.trade_id,
                "ltp": round(ltp, 2),
                "value": round(pos.entry_price * pos.quantity, 2),
                "unrealised": round(gross, 2),
                "unrealised_pct": round(gross / (pos.entry_price * pos.quantity) * 100, 2)
                if pos.entry_price and pos.quantity
                else 0.0,
            }
        )
    return sorted(out, key=lambda p: p["symbol"])
