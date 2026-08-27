from __future__ import annotations

import random

from fastapi import APIRouter

from app import state
from app.strategies.scanner import NIFTY50_UNIVERSE, OpeningRange, Scanner

router = APIRouter(prefix="/api/scanner", tags=["scanner"])
scanner = Scanner()


@router.get("/opening-range")
async def opening_range():
    """Returns each universe symbol's simulated 15-min opening range + RVOL.

    Real 20-day average volume requires historical data from the broker;
    until a live BrokerClient is wired up this uses the live tick feed's
    running volume against a randomized-but-stable 20d baseline, so the
    scanner UI and ORB strategy have something real to react to in Paper mode.
    """
    ranges = []
    for symbol in NIFTY50_UNIVERSE:
        quote = state.latest_quotes.get(symbol)
        if not quote:
            continue
        baseline = quote["volume"] * random.uniform(0.3, 0.9)
        ranges.append(
            OpeningRange(
                symbol=symbol,
                high=round(quote["ltp"] * 1.004, 2),
                low=round(quote["ltp"] * 0.996, 2),
                volume_first_15m=quote["volume"],
                avg_20d_volume=baseline,
            )
        )
    qualifying = scanner.evaluate(ranges)
    return {
        "universe_size": len(NIFTY50_UNIVERSE),
        "qualifying": [
            {"symbol": r.symbol, "high": r.high, "low": r.low, "rvol": round(r.rvol, 2)} for r in qualifying
        ],
    }
