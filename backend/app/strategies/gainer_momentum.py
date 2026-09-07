"""Top-gainer momentum strategy.

Buys the strongest names of the session — the top N by move from the open —
sizing each position as a share of available cash rather than by stop
distance, with percentage stop and target.

This is a different animal from the ORB strategy next door, and the
difference is worth stating plainly because it changes what the risk engine
can protect you from:

* ORB sizes by the 1% rule: the stop distance decides the quantity, so a
  losing trade costs about 1% of the account whatever the instrument.
* This strategy sizes by allocation: you choose the share of cash to deploy,
  and the loss on a stop-out is `(allocation / max_positions) × stop_pct`.
  At the defaults (50% of 5× buying power, 3 names, 1.2% stop) a single
  stop-out is about 1% of the account — in the same neighbourhood as ORB.
  The old 100% / 20% / leftover-dump combination is what put a 1-lakh
  account into ₹10–50 lakh of LALITHAA for forty seconds.

The percentage levels have to be reachable before the 15:30 square-off.
NSE large-caps typically travel 1–2% in a session, not 20%. A 20% stop and
35% target on an intraday book are ornaments: the cut-off closes every
position at a random last tick, and the P&L is noise plus charges.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace


@dataclass
class GainerConfig:
    """Percentages are of entry price; allocation is of available cash."""

    top_n: int = 50
    # Share of BUYING POWER (balance x leverage) to put to work, not a share
    # of cash. At 1 lakh and 5x MIS leverage the full budget is 5 lakh.
    allocation_pct: float = 50.0
    # How many names that budget is split across. Spreading it means a single
    # stop-out costs 1/N of the deployed total instead of all of it — the one
    # meaningful protection available once the 1% sizer is out of the picture.
    max_positions: int = 3
    # Intraday brackets. A 20% stop is a swing-trade level jammed into a
    # same-day book: it almost never hits, so the 15:30 cut-off is the real
    # exit. 1.2% / 2.4% is 1:2 R:R inside a typical NSE session range.
    stop_loss_pct: float = 1.2
    target_pct: float = 2.4
    # A name has to be up at least this much to count as momentum at all;
    # without it the "top 20" on a flat day is just the 20 least-flat names.
    min_gain_pct: float = 0.0
    # Once most of the allocation is committed, the remaining budget buys a
    # share or two. Those positions cannot move the account but still consume
    # a slot in the daily trade cap and clutter the book, so they are skipped.
    min_order_value: float = 5_000.0


@dataclass
class GainerSignal:
    symbol: str
    entry: float
    stop_loss: float
    target: float
    quantity: int
    pct_from_open: float
    allocation_value: float


def plan_entry(
    *,
    symbol: str,
    price: float,
    pct_from_open: float,
    budget: float,
    config: GainerConfig,
) -> GainerSignal | None:
    """Turns a ranked mover into a sized order, or None if it can't be filled.

    Long-only: the strategy's premise is that strength continues, so there is
    no short branch. A short here would need its own thesis and its own
    levels rather than a mirrored copy of these.
    """
    if price <= 0 or budget <= 0:
        return None
    if pct_from_open < config.min_gain_pct:
        return None

    # `budget` is the rupees this one position may spend; the allocation
    # percentage and the split across positions are applied by the caller.
    quantity = int(budget // price)
    if quantity <= 0:
        return None
    if quantity * price < config.min_order_value:
        return None

    stop_loss = round(price * (1 - config.stop_loss_pct / 100.0), 2)
    target = round(price * (1 + config.target_pct / 100.0), 2)

    # A stop that rounds onto the entry would be a position with no exit.
    if stop_loss >= price:
        return None

    return GainerSignal(
        symbol=symbol,
        entry=round(price, 2),
        stop_loss=stop_loss,
        target=target,
        quantity=quantity,
        pct_from_open=round(pct_from_open, 2),
        allocation_value=round(quantity * price, 2),
    )


# A 2/3 EMA pair fires on a one-paise wiggle. 9/21 is the shortest pair this
# bot will actually trade — anything tighter is tick noise with a name.
MIN_FAST_PERIOD = 8
MIN_SLOW_GAP = 8


def ma_pair_is_noise(fast_period: int, slow_period: int) -> bool:
    return fast_period < MIN_FAST_PERIOD or (slow_period - fast_period) < MIN_SLOW_GAP


def even_slot_budget(
    *,
    balance: float,
    leverage: float,
    allocation_pct: float,
    deployed: float,
    max_positions: int,
    open_count: int,
) -> float:
    """Rupees available for ONE new name.

    Splits the *total* allocation evenly across `max_positions`, then takes
    the min of that and remaining buying power. Using remaining / slots_left
    dumps the leftover book into the last name — that is how a 1-lakh
    account opened 15,605 shares of LALITHAA.
    """
    if max_positions <= 0 or balance <= 0:
        return 0.0
    buying_power = balance * leverage * (allocation_pct / 100.0)
    remaining = buying_power - deployed
    slots_left = max_positions - open_count
    if remaining <= 0 or slots_left <= 0:
        return 0.0
    return min(remaining, buying_power / max_positions)


def past_last_entry(now: dt.time, square_off: dt.time, buffer_min: int) -> bool:
    """True when there is not enough session left for the thesis to play out."""
    if buffer_min <= 0:
        return now >= square_off
    cutoff = (dt.datetime.combine(dt.date(2000, 1, 1), square_off) - dt.timedelta(minutes=buffer_min)).time()
    return now >= cutoff


def cap_to_risk(signal: GainerSignal, risk_qty: int) -> GainerSignal | None:
    """Never let allocation size exceed the 1% risk rule."""
    qty = min(signal.quantity, max(int(risk_qty), 0))
    if qty <= 0:
        return None
    if qty == signal.quantity:
        return signal
    return replace(signal, quantity=qty, allocation_value=round(qty * signal.entry, 2))

