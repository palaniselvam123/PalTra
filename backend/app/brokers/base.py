"""Adapter interface every broker client must implement, so the strategy/risk
engine and API routes never talk to a broker SDK directly. Add a new broker
by subclassing BrokerClient and registering it in brokers/__init__.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal

Side = Literal["BUY", "SELL"]
OrderType = Literal["MARKET", "LIMIT", "SL", "SL-M"]


@dataclass
class Quote:
    symbol: str
    ltp: float
    bid: float
    ask: float
    volume: int


@dataclass
class OrderRequest:
    symbol: str
    side: Side
    quantity: int
    order_type: OrderType = "MARKET"
    price: float | None = None
    trigger_price: float | None = None
    stop_loss: float | None = None
    target: float | None = None
    tag: str = "orb-strategy"


@dataclass
class OrderResult:
    broker_order_id: str
    status: str  # PLACED | REJECTED | FILLED | CANCELLED
    filled_price: float | None = None
    message: str = ""


class BrokerAuthError(Exception):
    pass


class BrokerOrderError(Exception):
    pass


class BrokerClient(ABC):
    """Every method that hits the network must be async and must never raise
    a raw SDK exception past this boundary — wrap in BrokerAuthError /
    BrokerOrderError so callers get a consistent contract regardless of
    broker.
    """

    name: str

    @abstractmethod
    async def login(self, api_key: str, api_secret: str, totp_secret: str) -> str:
        """Runs the daily TOTP + access-token workflow. Returns the access token."""

    @abstractmethod
    async def is_token_valid(self) -> bool: ...

    @abstractmethod
    async def get_quote(self, symbol: str) -> Quote: ...

    @abstractmethod
    async def place_order(self, order: OrderRequest) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> None: ...

    @abstractmethod
    async def square_off_all(self) -> list[OrderResult]:
        """Used by the emergency kill switch and the end-of-day hard cut-off."""

    @abstractmethod
    async def cancel_all_open_orders(self) -> None: ...
