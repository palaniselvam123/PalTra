"""Paper trading engine: simulates fills against live/simulated tick prices,
applying slippage and approximate Indian intraday equity charges so the P&L
shown in Paper mode is a realistic preview of Live mode, not a fantasy number.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.brokers.base import OrderRequest, OrderResult
from app.core.config import get_settings

# Approximate NSE intraday equity (MIS) charges, one-way, as a fraction of turnover.
BROKERAGE_PCT = 0.0003       # 0.03% of turnover...
BROKERAGE_CAP = 20.0         # ...but never more than Rs 20 per order, which is what discount brokers bill
STT_PCT = 0.00025             # sell-side only, intraday equity
EXCHANGE_TXN_PCT = 0.0000325
GST_PCT = 0.18                # on (brokerage + exchange txn charges)
SEBI_PCT = 0.000001
STAMP_DUTY_PCT = 0.00003      # buy-side only


@dataclass
class PaperPosition:
    symbol: str
    side: str
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    opened_at: dt.datetime
    order_id: str
    trade_id: int | None = None


@dataclass
class PaperFill:
    order_id: str
    symbol: str
    side: str
    quantity: int
    requested_price: float
    filled_price: float
    charges: float


@dataclass
class PaperCloseResult:
    symbol: str
    side: str
    quantity: int
    entry_price: float
    exit_price: float
    pnl: float
    trade_id: int | None
    entry_charges: float = 0.0
    exit_charges: float = 0.0


def estimate_charges(side: str, price: float, quantity: int) -> float:
    turnover = price * quantity
    # Uncapped percentage brokerage overstated costs badly on large orders —
    # 812 rupees on 27 lakh of turnover where Groww bills 20. Everything else
    # here genuinely is a percentage of turnover; brokerage is not.
    brokerage = min(turnover * BROKERAGE_PCT, BROKERAGE_CAP)
    exchange_txn = turnover * EXCHANGE_TXN_PCT
    gst = (brokerage + exchange_txn) * GST_PCT
    sebi = turnover * SEBI_PCT
    stt = turnover * STT_PCT if side == "SELL" else 0.0
    stamp = turnover * STAMP_DUTY_PCT if side == "BUY" else 0.0
    return round(brokerage + exchange_txn + gst + sebi + stt + stamp, 2)


def round_trip_cost_per_share(side: str, price: float, quantity: int, slippage_pct: float) -> float:
    """What one share must move just to break even.

    Slippage is charged on both legs and charges on both legs, so this is the
    hurdle a target has to clear before the trade can make anything at all.
    """
    if quantity <= 0 or price <= 0:
        return 0.0
    exit_side = "SELL" if side == "BUY" else "BUY"
    charges = estimate_charges(side, price, quantity) + estimate_charges(exit_side, price, quantity)
    slippage = price * (slippage_pct / 100) * 2
    return slippage + charges / quantity


class PaperEngine:
    """Fills market orders instantly against the current tick, with a
    slippage haircut against the trader. This is intentionally pessimistic:
    a strategy that isn't profitable after paper slippage + charges has no
    business going live.
    """

    def __init__(self):
        self._slippage_pct = get_settings().paper_slippage_pct
        self._order_counter = 0
        self.positions: dict[str, PaperPosition] = {}

    @property
    def slippage_pct(self) -> float:
        return self._slippage_pct

    def expected_fill_price(self, side: str, ltp: float) -> float:
        """The price an entry would actually fill at, slippage included.

        Sizing must use this rather than the strategy's intended entry: the
        two differ by the slippage haircut, and on a tight stop that gap
        multiplies the real risk (an observed trade sized for ₹1,000 of risk
        carried ₹2,370).
        """
        slip = ltp * (self._slippage_pct / 100)
        return round(ltp + slip if side == "BUY" else ltp - slip, 2)

    def _next_order_id(self) -> str:
        self._order_counter += 1
        return f"PAPER-{self._order_counter:06d}"

    def fill_market_order(self, order: OrderRequest, ltp: float) -> tuple[OrderResult, PaperFill]:
        slip = ltp * (self._slippage_pct / 100)
        filled_price = ltp + slip if order.side == "BUY" else ltp - slip
        filled_price = round(filled_price, 2)

        order_id = self._next_order_id()
        charges = estimate_charges(order.side, filled_price, order.quantity)

        self.positions[order.symbol] = PaperPosition(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            entry_price=filled_price,
            stop_loss=order.stop_loss or 0.0,
            target=order.target or 0.0,
            opened_at=dt.datetime.utcnow(),
            order_id=order_id,
        )

        result = OrderResult(broker_order_id=order_id, status="FILLED", filled_price=filled_price)
        fill = PaperFill(
            order_id=order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            requested_price=ltp,
            filled_price=filled_price,
            charges=charges,
        )
        return result, fill

    def close_position(self, symbol: str, ltp: float) -> PaperCloseResult | None:
        """Squares off a paper position at the current tick and returns net
        realized P&L (after simulated slippage + charges on both legs), or
        None if there was no open position.
        """
        pos = self.positions.pop(symbol, None)
        if pos is None:
            return None

        slip = ltp * (self._slippage_pct / 100)
        exit_price = ltp - slip if pos.side == "BUY" else ltp + slip
        exit_price = round(exit_price, 2)

        gross = (exit_price - pos.entry_price) * pos.quantity if pos.side == "BUY" else (pos.entry_price - exit_price) * pos.quantity
        entry_charges = estimate_charges(pos.side, pos.entry_price, pos.quantity)
        exit_side = "SELL" if pos.side == "BUY" else "BUY"
        exit_charges = estimate_charges(exit_side, exit_price, pos.quantity)

        return PaperCloseResult(
            symbol=symbol,
            side=pos.side,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=round(gross - entry_charges - exit_charges, 2),
            trade_id=pos.trade_id,
            entry_charges=entry_charges,
            exit_charges=exit_charges,
        )
