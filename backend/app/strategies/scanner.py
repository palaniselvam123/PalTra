"""Pre-market / opening scanner: RVOL filter + 15-minute opening range.

Restricted to a fixed high-liquidity universe (Nifty 50 / F&O) so the bot
never wanders into illiquid, hard-to-exit names.
"""
from __future__ import annotations

from dataclasses import dataclass

NIFTY50_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "ITC", "LT", "SBIN",
    "BHARTIARTL", "AXISBANK", "KOTAKBANK", "HINDUNILVR", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "WIPRO", "NTPC",
]


@dataclass
class OpeningRange:
    symbol: str
    high: float
    low: float
    volume_first_15m: int
    avg_20d_volume: float
    # Set when true 20-day history isn't available and RVOL was derived some
    # other way (e.g. cross-sectionally against the rest of the universe).
    rvol_override: float | None = None

    @property
    def rvol(self) -> float:
        if self.rvol_override is not None:
            return self.rvol_override
        if self.avg_20d_volume <= 0:
            return 0.0
        # avg_20d_volume is a full-day average; scale to a 15-min slice (~6.25% of a 6h15m session).
        expected_15m_volume = self.avg_20d_volume * 0.0625
        return self.volume_first_15m / expected_15m_volume if expected_15m_volume else 0.0

    @property
    def qualifies(self) -> bool:
        return self.rvol >= 2.0


class Scanner:
    def __init__(self, universe: list[str] | None = None, rvol_threshold: float = 2.0):
        self.universe = universe or NIFTY50_UNIVERSE
        self.rvol_threshold = rvol_threshold

    def evaluate(self, ranges: list[OpeningRange]) -> list[OpeningRange]:
        """Given each symbol's computed opening range + volume, return only
        the ones clearing the RVOL bar — these feed the ORB strategy.
        """
        return [r for r in ranges if r.rvol >= self.rvol_threshold]
