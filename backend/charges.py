"""Groww intraday statutory charges for NSE cash MIS.

Rates are the Groww intraday schedule (not a generic approximation):

- Brokerage: min(₹20, 0.05% of leg turnover) per leg, so ₹40 max round-trip
- STT: 0.025% of sell-side turnover only
- NSE transaction charge: 0.00297% of total turnover
- SEBI turnover fee: 0.0001% of total turnover
- Stamp duty: 0.003% of buy-side turnover only
- GST: 18% of (brokerage + exchange charge + SEBI fee)

`gross_pnl` is (sell_price - buy_price) * qty, which is the round-trip result
whether the buy leg was the entry (long) or the cover (short).
"""
from __future__ import annotations


def calculate_charges(buy_price: float, sell_price: float, qty: int) -> dict:
    qty = int(qty)
    buy_turnover = float(buy_price) * qty
    sell_turnover = float(sell_price) * qty
    total_turnover = buy_turnover + sell_turnover

    brokerage_buy = min(20.0, 0.0005 * buy_turnover)
    brokerage_sell = min(20.0, 0.0005 * sell_turnover)
    brokerage = brokerage_buy + brokerage_sell

    stt = 0.00025 * sell_turnover
    exchange_charge = 0.0000297 * total_turnover
    sebi_fee = 0.000001 * total_turnover
    stamp_duty = 0.00003 * buy_turnover
    gst = 0.18 * (brokerage + exchange_charge + sebi_fee)

    total_charges = brokerage + stt + exchange_charge + sebi_fee + stamp_duty + gst
    gross_pnl = (float(sell_price) - float(buy_price)) * qty
    net_pnl = gross_pnl - total_charges

    return {
        "buy_turnover": buy_turnover,
        "sell_turnover": sell_turnover,
        "total_turnover": total_turnover,
        "brokerage_buy": brokerage_buy,
        "brokerage_sell": brokerage_sell,
        "brokerage": brokerage,
        "stt": stt,
        "exchange_charge": exchange_charge,
        "sebi_fee": sebi_fee,
        "stamp_duty": stamp_duty,
        "gst": gst,
        "total_charges": total_charges,
        "gross_pnl": gross_pnl,
        "net_pnl": net_pnl,
    }


def legs_for(direction: str, entry_price: float, exit_price: float) -> tuple[float, float]:
    """Return (buy_price, sell_price) for a closed long or short."""
    if direction == "LONG":
        return float(entry_price), float(exit_price)
    return float(exit_price), float(entry_price)
