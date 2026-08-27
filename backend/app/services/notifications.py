"""Trade alerts, with the reason attached.

The console already logs what happened. This channel exists because a
notification is read in isolation, seconds after it fires and usually without
the app in front of you — so "SELL 3703 HDFCBANK" is not enough. Every alert
carries why the trade happened: which breakout fired, what the AI gate said,
or which bracket closed the position.

Kept separate from the `log` channel because logs are a continuous stream and
alerts are discrete events; the browser should surface one and not the other.
"""
from __future__ import annotations

import datetime as dt

from app.core.market_clock import ist_now
from app.services.broadcaster import broadcaster

CHANNEL = "notify"


def _money(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{'-' if value < 0 else ''}₹{abs(value):,.2f}"


async def trade_opened(
    *,
    account: str,
    symbol: str,
    side: str,
    quantity: int,
    price: float,
    charges: float,
    reason: str,
    source: str = "MANUAL",
) -> None:
    value = price * quantity
    desk = "Desk" if account == "MANUAL" else "Bot" if source == "BOT" else "Dashboard"
    await broadcaster.publish(
        CHANNEL,
        {
            "kind": "ENTRY",
            "account": account,
            "source": source,
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": round(price, 2),
            "value": round(value, 2),
            "charges": round(charges, 2),
            "pnl": None,
            "reason": reason,
            "title": f"{desk}: {side} {quantity:,} {symbol}",
            "body": f"Filled at ₹{price:,.2f} · {_money(value)} · charges {_money(charges)}\n{reason}",
            "at": ist_now().isoformat(),
        },
    )


async def trade_closed(
    *,
    account: str,
    symbol: str,
    side: str,
    quantity: int,
    entry_price: float,
    exit_price: float,
    pnl: float,
    reason: str,
) -> None:
    desk = "Desk" if account == "MANUAL" else "Bot"
    move = exit_price - entry_price if side == "BUY" else entry_price - exit_price
    outcome = "profit" if pnl > 0 else "loss" if pnl < 0 else "breakeven"
    await broadcaster.publish(
        CHANNEL,
        {
            "kind": "EXIT",
            "account": account,
            "source": "CLOSE",
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "price": round(exit_price, 2),
            "value": round(exit_price * quantity, 2),
            "charges": None,
            "pnl": round(pnl, 2),
            "reason": reason,
            "title": f"{desk}: closed {symbol} · {_money(pnl)}",
            # The price move is spelled out because a small move with a large
            # position, or a win that nets a loss after costs, is otherwise
            # baffling in a one-line alert.
            "body": f"{side} {quantity:,} · ₹{entry_price:,.2f} → ₹{exit_price:,.2f} "
            f"({move:+.2f}/share) · net {outcome} {_money(pnl)}\n{reason}",
            "at": ist_now().isoformat(),
        },
    )


async def alert(title: str, body: str, level: str = "INFO") -> None:
    """Non-trade events worth interrupting for: circuit breaker, kill switch."""
    await broadcaster.publish(
        CHANNEL,
        {
            "kind": "ALERT",
            "level": level,
            "title": title,
            "body": body,
            "reason": body,
            "at": ist_now().isoformat(),
        },
    )
