"""Morning movers: which stocks are going up, and which are moving fast.

Two different questions, deliberately kept apart:

* **Moving** — how far a stock is from its session open. A simple ranking.
* **Moving FAST** — how much of that move arrived in the last few minutes.
  A stock up 3% that got there over two hours is not the same event as one that
  did it in ten minutes, and only the second is worth interrupting someone for.

Speed is measured as percentage points per minute over a short trailing window,
so it is a rate rather than a level. That distinction is the whole point of the
alerting side: a level threshold fires once and then keeps firing all day, while
a rate threshold fires when something is actually happening.

**Alert de-duplication is per symbol per direction per session.** Without it a
fast mover crossing the threshold on consecutive polls would send a message
every thirty seconds, which trains the recipient to ignore the channel — the
same failure mode as a validator that cries wolf.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from app.core.market_clock import IST, ist_now
from app.research.snapshots import Mover, ist_time_str, snapshot_store

# Defaults, chosen to be legible rather than tuned. They are configurable at the
# API and nothing here was fitted to an outcome.
MORNING_END_HHMM = (11, 0)      # "the morning" for the movers view
SPEED_WINDOW_MIN = 10           # trailing window for the rate calculation
FAST_PCT_PER_MIN = 0.10         # 0.10 pp/min = ~1% in ten minutes
MIN_MOVE_PCT = 0.75             # ignore rate spikes on a stock that has barely moved


@dataclass
class FastMover:
    symbol: str
    pct_from_open: float
    speed_pct_per_min: float
    window_move_pct: float
    last_price: float
    last_ts: int
    direction: str              # UP | DOWN

    @property
    def last_time_ist(self) -> str:
        return ist_time_str(self.last_ts)

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "pct_from_open": round(self.pct_from_open, 3),
            "speed_pct_per_min": round(self.speed_pct_per_min, 4),
            "window_move_pct": round(self.window_move_pct, 3),
            "last_price": self.last_price,
            "last_ts": self.last_ts,
            "last_time_ist": self.last_time_ist,
            "direction": self.direction,
        }


def morning_cutoff(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(*MORNING_END_HHMM), tzinfo=IST)


def ranked_movers(
    day: dt.date, source: str = "live", as_of: dt.datetime | None = None, limit: int = 0
) -> list[Mover]:
    movers = snapshot_store.movers(day, source, as_of)
    return movers[:limit] if limit else movers


def split_gainers_losers(movers: list[Mover], top: int = 15) -> dict:
    gainers = [m for m in movers if m.pct_from_open > 0][:top]
    losers = [m for m in movers if m.pct_from_open < 0][-top:][::-1]
    return {"gainers": gainers, "losers": losers}


def fast_movers(
    day: dt.date,
    source: str = "live",
    as_of: dt.datetime | None = None,
    window_min: int = SPEED_WINDOW_MIN,
    min_speed: float = FAST_PCT_PER_MIN,
    min_move: float = MIN_MOVE_PCT,
) -> list[FastMover]:
    """Symbols whose move is arriving quickly right now.

    Speed uses the price `window_min` ago as its baseline, not the session open,
    so a stock that gapped up and then went flat does not register as fast.
    """
    now = as_of or ist_now()
    baseline_at = now - dt.timedelta(minutes=window_min)
    out: list[FastMover] = []

    for m in snapshot_store.movers(day, source, as_of):
        if abs(m.pct_from_open) < min_move:
            continue
        past = snapshot_store.price_at(m.symbol, baseline_at, source, tolerance_min=window_min)
        if past is None or past.price <= 0:
            continue
        elapsed_min = max((m.last_ts - past.ts) / 60.0, 1.0)
        window_move = (m.last_price - past.price) / past.price * 100
        speed = window_move / elapsed_min
        if abs(speed) < min_speed:
            continue
        out.append(
            FastMover(
                symbol=m.symbol,
                pct_from_open=m.pct_from_open,
                speed_pct_per_min=speed,
                window_move_pct=window_move,
                last_price=m.last_price,
                last_ts=m.last_ts,
                direction="UP" if speed > 0 else "DOWN",
            )
        )
    return sorted(out, key=lambda f: abs(f.speed_pct_per_min), reverse=True)


@dataclass
class AlertLedger:
    """Remembers which (symbol, direction) already alerted this session."""

    day: dt.date | None = None
    sent: set[tuple[str, str]] = field(default_factory=set)

    def should_send(self, symbol: str, direction: str, day: dt.date) -> bool:
        if self.day != day:
            self.day = day
            self.sent.clear()
        key = (symbol, direction)
        if key in self.sent:
            return False
        self.sent.add(key)
        return True

    def reset(self) -> None:
        self.day = None
        self.sent.clear()


alert_ledger = AlertLedger()


def format_alert(f: FastMover) -> str:
    arrow = "UP" if f.direction == "UP" else "DOWN"
    return (
        f"{f.symbol} moving {arrow} fast\n"
        f"{f.pct_from_open:+.2f}% from open, {f.window_move_pct:+.2f}% in the last "
        f"{SPEED_WINDOW_MIN} min ({f.speed_pct_per_min:+.3f}%/min)\n"
        f"Last {f.last_price:.2f} at {f.last_time_ist} IST"
    )
