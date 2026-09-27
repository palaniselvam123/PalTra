"""Idempotency & order-lock for every dispatch path.

Safety invariant #2: never place a new order while another is PENDING/TRANSIT,
and serialise all order execution behind a single asyncio.Lock so concurrent
signals cannot double-fire.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum

# Terminal statuses — anything else blocks new entries.
_TERMINAL = frozenset({
    "FILLED",
    "COMPLETE",
    "COMPLETED",
    "REJECTED",
    "CANCELLED",
    "CANCELED",
    "EXPIRED",
    "FAILED",
    "SKIPPED",
})

# Explicit in-flight statuses the Groww SDK (and our local mailbox) use.
_IN_FLIGHT = frozenset({
    "PENDING",
    "TRANSIT",
    "NEW",
    "OPEN",
    "TRIGGER_PENDING",
    "ACKED",
    "APPROVED",
    "PLACED",
    "SENT",
})


class OrderPhase(str, Enum):
    PENDING = "PENDING"
    TRANSIT = "TRANSIT"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class TrackedOrder:
    local_id: str
    symbol: str
    side: str
    status: str
    broker_order_id: str = ""
    kind: str = "ENTRY"  # ENTRY | SL | EXIT | SQUARE_OFF
    note: str = ""


@dataclass
class OrderGuard:
    """Process-wide order serialisation + in-flight registry."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _orders: dict[str, TrackedOrder] = field(default_factory=dict)
    _seq: int = 0

    def _next_id(self) -> str:
        self._seq += 1
        return f"LOCAL-{self._seq:06d}"

    def register(self, *, symbol: str, side: str, kind: str = "ENTRY", status: str = "PENDING") -> TrackedOrder:
        tracked = TrackedOrder(
            local_id=self._next_id(),
            symbol=symbol,
            side=side,
            status=status.upper(),
            kind=kind,
        )
        self._orders[tracked.local_id] = tracked
        return tracked

    def update(
        self,
        local_id: str,
        *,
        status: str | None = None,
        broker_order_id: str | None = None,
        note: str | None = None,
    ) -> TrackedOrder | None:
        tracked = self._orders.get(local_id)
        if tracked is None:
            return None
        if status is not None:
            tracked.status = status.upper()
        if broker_order_id is not None:
            tracked.broker_order_id = broker_order_id
        if note is not None:
            tracked.note = note
        return tracked

    def release(self, local_id: str) -> None:
        self._orders.pop(local_id, None)

    def in_flight(self) -> list[TrackedOrder]:
        out: list[TrackedOrder] = []
        for o in self._orders.values():
            status = o.status.upper()
            if status in _TERMINAL:
                continue
            if status in _IN_FLIGHT or status not in _TERMINAL:
                out.append(o)
        return out

    def has_blocking_orders(self) -> bool:
        return bool(self.in_flight())

    def blocking_reason(self) -> str | None:
        blocked = self.in_flight()
        if not blocked:
            return None
        parts = [
            f"{o.kind} {o.side} {o.symbol} ({o.status}"
            + (f"/{o.broker_order_id}" if o.broker_order_id else "")
            + ")"
            for o in blocked
        ]
        return "Order already in PENDING/TRANSIT — refusing new dispatch: " + "; ".join(parts)

    @staticmethod
    def is_in_flight_status(status: str | None) -> bool:
        if not status:
            return False
        s = status.upper()
        if s in _TERMINAL:
            return False
        return s in _IN_FLIGHT or s not in _TERMINAL


order_guard = OrderGuard()
