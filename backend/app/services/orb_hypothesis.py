"""Opening Range Breakout as a testable hypothesis.

Wraps the ORB logic the live bot already uses (`strategies/orb_strategy.py`)
so it can be graded by the same harness that graded the EMA crossover. The
live strategy is not modified and not imported into the decision path here —
this module only replays its entry rule over history.

Two things the live version cannot supply on historical data:

* **RVOL.** `OpeningRange.qualifies` needs a 20-day average volume the
  historical feed does not carry. Here the opening window's volume is compared
  against the same window on the other days in the sample, which is the same
  question asked with the data actually available.
* **Repeat breakouts.** Live, one position per symbol per day means only the
  first breakout can be taken. That is reproduced explicitly rather than
  emerging from position state, and the alternative is measurable.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass

from app.core.market_clock import IST, OPENING_RANGE_END
from app.services.indicators import OHLCV

SESSION_OPEN = dt.time(9, 15)
LAST_ENTRY = dt.time(14, 45)   # after this a breakout has no room to resolve


@dataclass
class ORBParams:
    rvol_threshold: float = 0.0      # 0 disables the gate
    first_breakout_only: bool = True
    range_end: dt.time = OPENING_RANGE_END


def _ist(ts: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST)


def _sessions(candles: list[OHLCV]) -> dict[dt.date, list[int]]:
    """Bar indices grouped by trading day, in order."""
    days: dict[dt.date, list[int]] = {}
    for i, c in enumerate(candles):
        days.setdefault(_ist(c.ts).date(), []).append(i)
    return days


def generate(symbol: str, candles: list[OHLCV], params: ORBParams | None = None) -> list[tuple[int, str]]:
    """Return (bar_index, side) for every ORB entry in this series.

    The breakout is confirmed on the CLOSE of a bar outside the range, and the
    index returned is that bar — the same convention the EMA harness uses, so
    the two hypotheses are measured from the same point in time.
    """
    p = params or ORBParams()
    days = _sessions(candles)

    # Baseline for RVOL: the opening window's volume on every other day.
    opening_volumes: dict[dt.date, int] = {}
    for day, idxs in days.items():
        vol = sum(candles[i].volume for i in idxs if SESSION_OPEN <= _ist(candles[i].ts).time() < p.range_end)
        if vol > 0:
            opening_volumes[day] = vol
    all_open_vols = list(opening_volumes.values())

    out: list[tuple[int, str]] = []
    for day, idxs in days.items():
        window = [i for i in idxs if SESSION_OPEN <= _ist(candles[i].ts).time() < p.range_end]
        after = [i for i in idxs if _ist(candles[i].ts).time() >= p.range_end]
        if len(window) < 2 or not after:
            continue  # partial session — no range worth breaking

        hi = max(candles[i].high for i in window)
        lo = min(candles[i].low for i in window)
        if hi <= lo:
            continue

        if p.rvol_threshold > 0:
            others = [v for d, v in opening_volumes.items() if d != day]
            if not others:
                continue
            # Compared against the median: one gap-open day with 5x volume
            # would drag a mean high enough to disqualify normal days.
            baseline = statistics.median(others) if others else 0
            rvol = opening_volumes.get(day, 0) / baseline if baseline else 0.0
            if rvol < p.rvol_threshold:
                continue

        fired = False
        for i in after:
            if _ist(candles[i].ts).time() > LAST_ENTRY:
                break
            close = candles[i].close
            side = "BUY" if close > hi else ("SELL" if close < lo else None)
            if side is None:
                continue
            out.append((i, side))
            fired = True
            if p.first_breakout_only:
                break
        if not fired:
            continue
    return sorted(out)
