"""Live Groww dispatch helpers.

Isolates broker session lookup and the Exchange Stop-Loss lifecycle so
`execution.py` stays focused on the shared risk gate. Live orders are MIS
only — this desk is an intraday terminal.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.brokers.base import BrokerOrderError, OrderRequest, OrderResult
from app.brokers.groww_client import GrowwClient


class LiveBrokerUnavailable(Exception):
    """Raised when LIVE_MONEY is requested without a valid Groww session."""


def get_live_client() -> GrowwClient:
    """Returns the in-memory Groww session, or raises if LIVE cannot proceed."""
    # Local import: routes_auth owns the session registry; importing at module
    # load would cycle through FastAPI routers during startup.
    from app.api.routes_auth import _active_clients

    client = _active_clients.get("groww")
    if client is None:
        raise LiveBrokerUnavailable(
            "No Groww session. Save API credentials and Connect Live Data before switching to LIVE_MONEY."
        )
    return client


async def require_valid_live_session() -> GrowwClient:
    client = get_live_client()
    if not await client.is_token_valid():
        raise LiveBrokerUnavailable(
            "Groww access token is missing or expired. Re-run Connect Live Data before LIVE_MONEY."
        )
    return client


@dataclass
class LiveEntryFill:
    entry: OrderResult
    sl: OrderResult | None
    filled_price: float
    charges: float


async def place_mis_entry_with_sl(
    *,
    symbol: str,
    side: str,
    quantity: int,
    stop_loss: float,
    target: float | None = None,
) -> LiveEntryFill:
    """Market entry + Exchange SL. Target is tracked locally (and by the
    bracket monitor); Groww's place_order path here only attaches the hard SL
    that must never be orphaned on a flip.
    """
    client = await require_valid_live_session()

    entry = await client.place_order(
        OrderRequest(
            symbol=symbol,
            side=side,  # type: ignore[arg-type]
            quantity=quantity,
            order_type="MARKET",
            stop_loss=stop_loss,
            target=target,
            tag="live-entry",
        )
    )

    # Wait briefly for a fill price; some SDK responses return it immediately.
    filled = entry.filled_price
    if filled is None or filled <= 0:
        # Fall back to LTP — still better than inventing a zero fill.
        quote = await client.get_quote(symbol)
        filled = quote.ltp

    sl_side = "SELL" if side == "BUY" else "BUY"
    sl_result: OrderResult | None = None
    try:
        sl_result = await client.place_order(
            OrderRequest(
                symbol=symbol,
                side=sl_side,  # type: ignore[arg-type]
                quantity=quantity,
                order_type="SL-M",
                trigger_price=stop_loss,
                tag="live-sl",
            )
        )
    except BrokerOrderError:
        # Entry is live without a broker SL — surface clearly so the operator
        # (and kill switch) can react. Re-raise after attaching context.
        raise BrokerOrderError(
            f"Entry {entry.broker_order_id} filled for {symbol} but Exchange SL placement failed. "
            "Position is unprotected — cancel/square-off immediately."
        )

    from app.services.paper_engine import estimate_charges

    charges = estimate_charges(side, float(filled), quantity)
    return LiveEntryFill(entry=entry, sl=sl_result, filled_price=float(filled), charges=charges)


async def cancel_sl_and_verify(sl_broker_order_id: str, *, timeout_sec: float = 8.0) -> None:
    """Orphan-SL prevention: cancel then confirm the order is no longer open.

    Safety invariant #3 — never fire a reverse order while an Exchange SL is
    still working; that leaves a naked opposite leg if the SL later triggers.
    """
    if not sl_broker_order_id:
        return

    client = await require_valid_live_session()
    await client.cancel_order(sl_broker_order_id)

    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        status = await _order_status(client, sl_broker_order_id)
        if status is None:
            return  # disappeared from the book — treat as cancelled
        if status.upper() in {"CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "COMPLETE", "COMPLETED", "FILLED"}:
            # COMPLETE/FILLED means the SL already fired — caller must not flip.
            if status.upper() in {"COMPLETE", "COMPLETED", "FILLED"}:
                raise BrokerOrderError(
                    f"SL {sl_broker_order_id} already filled during cancel — cannot flip position."
                )
            return
        await asyncio.sleep(0.35)

    raise BrokerOrderError(
        f"SL {sl_broker_order_id} cancel not confirmed within {timeout_sec:.0f}s — reverse order blocked."
    )


async def _order_status(client: GrowwClient, broker_order_id: str) -> str | None:
    sdk = client._require_session()
    try:
        raw = await asyncio.to_thread(sdk.get_order_list, segment=client.SEGMENT)
    except Exception as exc:  # noqa: BLE001
        raise BrokerOrderError(f"Could not list orders to verify SL cancel: {exc}") from exc

    orders = raw.get("payload", raw) if isinstance(raw, dict) else raw
    for o in orders or []:
        oid = str(
            o.get("groww_order_id")
            or o.get("order_id")
            or o.get("orderId")
            or ""
        )
        if oid == broker_order_id:
            return str(o.get("order_status") or o.get("status") or "")
    return None


async def broker_has_in_flight_orders() -> str | None:
    """Ask Groww whether any open/pending orders exist. Returns a reason string
    if so, else None. Network failure fails closed (blocks new orders).
    """
    try:
        client = await require_valid_live_session()
    except LiveBrokerUnavailable:
        return None

    sdk = client._require_session()
    try:
        raw = await asyncio.to_thread(sdk.get_order_list, segment=client.SEGMENT)
    except Exception as exc:  # noqa: BLE001
        return f"Could not verify open orders at broker: {exc}"

    from app.services.order_guard import OrderGuard

    orders = raw.get("payload", raw) if isinstance(raw, dict) else raw
    blocking: list[str] = []
    for o in orders or []:
        status = str(o.get("order_status") or o.get("status") or "")
        if OrderGuard.is_in_flight_status(status):
            sym = o.get("trading_symbol") or o.get("symbol") or "?"
            oid = o.get("groww_order_id") or o.get("order_id") or "?"
            blocking.append(f"{sym} {status}/{oid}")
    if blocking:
        return "Broker has PENDING/TRANSIT orders: " + "; ".join(blocking[:5])
    return None


async def live_square_off_and_cancel() -> None:
    """Kill-switch / day-lock path for LIVE_MONEY."""
    try:
        client = await require_valid_live_session()
    except LiveBrokerUnavailable:
        return
    try:
        await client.cancel_all_open_orders()
    except BrokerOrderError:
        pass
    try:
        await client.square_off_all()
    except BrokerOrderError:
        pass
