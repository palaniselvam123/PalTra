"""Loss limit per trade in rupees (`trade_loss_rs`, 0 = off; per bot and per stock, PAPER and LIVE, replays too).

Owner's case: with one quantity for every stock, a stop in points or % risks very different money: a ₹1,000 stock
moving against the trade for six candles costs ₹20-30,000, a ₹50,000 stock two candles a lakh. This rule is the same
money for every stock: once a trade's open loss before charges (points against × quantity) reaches the limit, that one
trade is closed (`TRADE_LOSS_LIMIT`, via `StrategyEngine._exit_now`: a LIVE stop is cancelled first). A trade in profit
is never touched by it, so winners run; other stocks and the bot keep trading (the daily loss limit halts the bot).
Judged on every price by `StrategyEngine._watch_trade_loss`; the fill can be a little past the limit (a gap, a fast
move), like any stop judged on the price.
"""
from __future__ import annotations

EXIT_REASON = "TRADE_LOSS_LIMIT"
MAX_RS = 10_000_000.0


def limit(cfg) -> float:
    """The loss in ₹ at which one trade is closed; 0 = off."""
    try:
        value = float(getattr(cfg, "trade_loss_rs", 0) or 0)
    except (TypeError, ValueError):
        return 0.0
    return min(MAX_RS, value) if value > 0 else 0.0


def on(cfg) -> bool:
    return limit(cfg) > 0


def open_loss(direction: str, entry: float, price: float, qty: int) -> float:
    """The trade's loss now in ₹ before charges (0 while it is in profit or flat)."""
    move = (price - entry) if (direction or "").upper() == "LONG" else (entry - price)
    return max(0.0, -move * max(0, int(qty or 0)))


def level(direction: str, entry: float, qty: int, cfg) -> float | None:
    """The price at which the limit is reached, for the screens; None when off or no quantity."""
    cap, shares = limit(cfg), int(qty or 0)
    if cap <= 0 or shares <= 0:
        return None
    step = cap / shares
    return entry - step if (direction or "").upper() == "LONG" else entry + step


def hit(direction: str, entry: float, price: float, qty: int, cfg) -> bool:
    cap = limit(cfg)
    return cap > 0 and open_loss(direction, entry, price, qty) >= cap
