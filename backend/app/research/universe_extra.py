"""Which symbols the movers rankings should consider.

The research map (`SECTOR_OF`) is 125 verified names with sector labels, chosen
so the cross-sectional work has a defensible population. The live feed polls a
different set: the bot's core twenty plus whatever the user added to their
watchlist, which is persisted and restored at startup.

Ranking only the research map meant a watchlist name was *absent* from the
tables rather than ranked — and on screen an absent stock is indistinguishable
from one that did not move. ANTELOPUS, up 6.5% and sitting in the watchlist
since August, appeared nowhere for exactly that reason.

There is deliberately no separate store here. The watchlist already persists
additions and validates them against the instrument master, so a second list
would be a second source of truth to keep in sync — and the symbol would still
be missing from whichever one the rankings happened to read.
"""
from __future__ import annotations


def movers_universe() -> set[str]:
    """The research map plus everything the live feed is polling."""
    from app.research.cross_sectional import SECTOR_OF
    from app.services.market_data import market_data

    return set(SECTOR_OF) | {s.upper() for s in market_data.symbols}
