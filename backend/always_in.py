"""Always in a trade (always_in.py): a bot that holds every armed stock all the time.

Off by default (`always_in`), per bot and per stock, PAPER and LIVE, replays too. The usual bot
only enters on a fresh SMA cross. With this on, every armed stock that is flat is entered at
once on the side the SMAs point to now (SMA fast above slow = long, below = short), whenever
that cross happened and whatever the candles look like:

* when the bot starts or the stock is armed (a cross printed before does not matter);
* right after any exit (stop, target, trailing stop, any exit rule): the next pass of the
  engine enters again, on the side the SMAs point to then.

Every entry goes through the normal entry path (`StrategyEngine.apply_signal`, entry reason
`ALWAYS_IN`), so the ticked entry checks (VWAP, RSI, …), the stop, flip, market hours, the
entry cut-off, the trade caps, the loss limit and one LIVE bot per stock all still apply. A
check that says no is asked again on the next closed candle. Exits are the bot's own exit
rules, unchanged; an opposite cross still closes and reverses as usual. Gap mode and candle
patterns / colour have their own entries and ignore it. Nothing here places an order itself.

**Reverse after a loss** (`always_in_reverse`, off by default, needs `always_in`): when the trade
that just closed lost money (its P&L before charges below zero), the next entry goes the other
way of that trade's signal side (a losing long is followed by a short), whatever the SMAs say;
after a winning or flat trade the SMAs' side is used as above. Kept until that entry opens
(asked again on each closed candle while a check says no), and dropped with the day.
"""
from __future__ import annotations

import pandas as pd

ENTRY_REASON = "ALWAYS_IN"
REVERSE_REASON = "ALWAYS_IN_REVERSE"


def enabled(cfg) -> bool:
    return bool(getattr(cfg, "always_in", False))


def reverse_on(cfg) -> bool:
    return enabled(cfg) and bool(getattr(cfg, "always_in_reverse", False))


def lost(gross_pnl) -> bool:
    """The closed trade lost money before charges."""
    try:
        return gross_pnl is not None and float(gross_pnl) < 0
    except (TypeError, ValueError):
        return False


def opposite(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"


def side(frame: pd.DataFrame | None) -> str | None:
    """LONG / SHORT from SMA fast vs slow on the last closed candle (the forming one is never read)."""
    if frame is None or len(frame) < 2 or not {"sma_9", "sma_21"} <= set(frame.columns):
        return None
    bar = frame.iloc[-2]
    fast, slow = bar.get("sma_9"), bar.get("sma_21")
    if fast is None or slow is None or pd.isna(fast) or pd.isna(slow) or fast == slow:
        return None
    return "LONG" if float(fast) > float(slow) else "SHORT"
