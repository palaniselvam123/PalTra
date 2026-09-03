"""One price lookup over two stores, newest and finest first.

There are two records of what a stock traded at, and they cover different
ground:

* **`intraday_prices`** — written live, one point per minute, but only for the
  sessions the recorder was actually running.
* **`research_candles`** — 5-minute OHLCV fetched from broker history, covering
  months back, but arriving after the fact and at coarser resolution.

Asking "what was RELIANCE at 11:00" should not require the caller to know which
of those has the answer. This module tries the minute record first because it is
finer, then falls back to history.

**The resolution is always reported.** A 5-minute answer to a question about
11:02 is a genuinely different thing from a 1-minute one, and silently blending
them would let a caller believe a historical price is minute-accurate. Every
result names the store it came from, the resolution in minutes, and the exact
timestamp found.

**Neither store is ever extrapolated.** If no bar exists near the requested
time, the answer is "not recorded" — the same rule the live recorder follows.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.core.market_clock import IST
from app.research.snapshots import PricePoint, ist_date, ist_time_str, snapshot_store
from app.research.store import store as research_store

LIVE = "live_minute_record"
HISTORY = "broker_history_5m"


@dataclass(frozen=True)
class HistoricalPrice:
    symbol: str
    ts: int
    price: float
    open_price: float | None
    origin: str                 # LIVE | HISTORY
    resolution_min: int

    @property
    def time_ist(self) -> str:
        return ist_time_str(self.ts)

    @property
    def pct_from_open(self) -> float | None:
        if not self.open_price:
            return None
        return (self.price - self.open_price) / self.open_price * 100

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "price": round(self.price, 2),
            "recorded_at_ist": self.time_ist,
            "open_price": round(self.open_price, 2) if self.open_price else None,
            "pct_from_open": round(self.pct_from_open, 3) if self.pct_from_open is not None else None,
            "origin": self.origin,
            "resolution_min": self.resolution_min,
        }


@dataclass(frozen=True)
class HistoricalMover:
    symbol: str
    open_price: float
    last_price: float
    last_ts: int
    pct_from_open: float
    high_price: float
    low_price: float
    points: int
    origin: str
    resolution_min: int
    first_ts: int

    @property
    def last_time_ist(self) -> str:
        return ist_time_str(self.last_ts)

    @property
    def first_time_ist(self) -> str:
        return ist_time_str(self.first_ts) if self.first_ts else ""

    @property
    def baseline_is_session_open(self) -> bool:
        """Broker history always starts at the open; the live record may not."""
        if self.origin == HISTORY:
            return True
        if not self.first_ts:
            return False
        t = dt.datetime.fromtimestamp(self.first_ts, tz=dt.timezone.utc).astimezone(IST)
        return 0 <= (t.hour * 60 + t.minute) - (9 * 60 + 15) <= 20


def _history_bars(symbol: str, day: dt.date):
    """That day's 5-minute bars for one symbol, from the research store."""
    return research_store.read(symbol, "5m", "live", start=day, end=day)


def price_at(
    symbol: str, when: dt.datetime, source: str = "live", tolerance_min: int = 15
) -> HistoricalPrice | None:
    """The price at or before `when`, from whichever store has it at best resolution."""
    symbol = symbol.upper()
    day = ist_date(int(when.timestamp()))

    live_point: PricePoint | None = snapshot_store.price_at(symbol, when, source, tolerance_min)
    if live_point is not None:
        return HistoricalPrice(
            symbol=live_point.symbol, ts=live_point.ts, price=live_point.price,
            open_price=live_point.open_price, origin=LIVE, resolution_min=1,
        )

    # Broker history is real NSE data. Using it to answer a question about
    # TODAY while the synthetic feed is running would silently swap one world
    # for the other, so for the current day the requested source is honoured
    # strictly. A question about a PAST day is unambiguously about the real
    # market — nobody asks what the simulator printed last June — so history
    # answers it whatever feed happens to be selected, with `origin` saying so.
    if source != "live" and day >= ist_date(int(dt.datetime.now(dt.timezone.utc).timestamp())):
        return None

    bars = _history_bars(symbol, day)
    if not bars:
        return None
    target = int(when.timestamp())
    floor = target - tolerance_min * 60
    candidates = [b for b in bars if floor <= b.ts <= target]
    if not candidates:
        return None
    bar = max(candidates, key=lambda b: b.ts)
    return HistoricalPrice(
        symbol=symbol, ts=bar.ts, price=bar.close, open_price=bars[0].open,
        origin=HISTORY, resolution_min=5,
    )


def movers(
    day: dt.date, source: str = "live", as_of: dt.datetime | None = None
) -> tuple[list[HistoricalMover], str]:
    """Every symbol's move for a day, from whichever store covers it.

    Returns the movers and the origin, so a caller can label a 5-minute
    reconstruction as such rather than presenting it as the live record.
    """
    live = snapshot_store.movers(day, source, as_of)
    if live:
        return (
            [
                HistoricalMover(
                    symbol=m.symbol, open_price=m.open_price, last_price=m.last_price,
                    last_ts=m.last_ts, pct_from_open=m.pct_from_open, high_price=m.high_price,
                    low_price=m.low_price, points=m.points, origin=LIVE, resolution_min=1,
                    first_ts=m.first_ts,
                )
                for m in live
            ],
            LIVE,
        )

    if source != "live" and day >= ist_date(int(dt.datetime.now(dt.timezone.utc).timestamp())):
        return [], LIVE

    cutoff = int(as_of.timestamp()) if as_of else None
    out: list[HistoricalMover] = []
    for symbol in research_store.symbols("5m", "live"):
        bars = _history_bars(symbol, day)
        if cutoff is not None:
            bars = [b for b in bars if b.ts <= cutoff]
        if not bars:
            continue
        open_price = bars[0].open
        if open_price <= 0:
            continue
        last = bars[-1]
        out.append(
            HistoricalMover(
                symbol=symbol,
                open_price=open_price,
                last_price=last.close,
                last_ts=last.ts,
                pct_from_open=(last.close - open_price) / open_price * 100,
                high_price=max(b.high for b in bars),
                low_price=min(b.low for b in bars),
                points=len(bars),
                origin=HISTORY,
                resolution_min=5,
                first_ts=bars[0].ts,
            )
        )
    return sorted(out, key=lambda m: m.pct_from_open, reverse=True), HISTORY


def series(
    symbol: str, day: dt.date, source: str = "live", until: dt.datetime | None = None
) -> list[HistoricalPrice]:
    """That day's price points for one symbol, from whichever store covers it."""
    symbol = symbol.upper()
    cutoff = int(until.timestamp()) if until else None

    live = snapshot_store.session_series(symbol, day, source)
    if live:
        pts = [p for p in live if cutoff is None or p.ts <= cutoff]
        return [
            HistoricalPrice(p.symbol, p.ts, p.price, p.open_price, LIVE, 1) for p in pts
        ]

    if source != "live" and day >= ist_date(int(dt.datetime.now(dt.timezone.utc).timestamp())):
        return []

    bars = _history_bars(symbol, day)
    if not bars:
        return []
    open_price = bars[0].open
    return [
        HistoricalPrice(symbol, b.ts, b.close, open_price, HISTORY, 5)
        for b in bars
        if cutoff is None or b.ts <= cutoff
    ]


def available_days(source: str = "live") -> dict:
    """Which days each store can answer for."""
    recorded = snapshot_store.recorded_days(source)
    history: list[str] = []
    if source == "live":
        stats = research_store.stats("5m", "live")
        history = [stats.get("start"), stats.get("end")]
    return {
        "live_minute_days": [d.isoformat() for d in recorded],
        "history_range": [d for d in history if d],
        "history_resolution_min": 5,
        "live_resolution_min": 1,
    }
