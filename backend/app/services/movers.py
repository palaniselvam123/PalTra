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
from app.research import price_history
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
    origin: str = price_history.LIVE
    resolution_min: int = 1

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
            "origin": self.origin,
            "resolution_min": self.resolution_min,
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
    """Symbols whose move arrived quickly, on any day the app has prices for.

    Speed uses the price `window_min` ago as its baseline, not the session open,
    so a stock that gapped up and then went flat does not register as fast.

    Works on a historical day as well as a live one. The underlying record is
    coarser there — 5-minute bars rather than 1-minute — so a short window
    contains fewer observations and the rate is correspondingly blunter. Each
    result carries the resolution it was computed at rather than leaving the
    caller to assume.
    """
    rows, origin = price_history.movers(day, source, as_of)
    if not rows:
        return []

    resolution = rows[0].resolution_min
    # A window shorter than the record's own spacing cannot contain a baseline.
    effective_window = max(window_min, resolution)
    out: list[FastMover] = []

    for m in rows:
        if abs(m.pct_from_open) < min_move:
            continue
        baseline_at = dt.datetime.fromtimestamp(m.last_ts, tz=dt.timezone.utc).astimezone(
            IST
        ) - dt.timedelta(minutes=effective_window)
        past = price_history.price_at(m.symbol, baseline_at, source, tolerance_min=effective_window)
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
                origin=origin,
                resolution_min=resolution,
            )
        )
    return sorted(out, key=lambda f: abs(f.speed_pct_per_min), reverse=True)


def peak_fast_movers(
    day: dt.date,
    source: str = "live",
    until: dt.datetime | None = None,
    window_min: int = SPEED_WINDOW_MIN,
    min_speed: float = FAST_PCT_PER_MIN,
    min_move: float = MIN_MOVE_PCT,
) -> list[FastMover]:
    """Each symbol's FASTEST window during a session, and when it happened.

    `fast_movers` answers "what is moving now", which is the right question
    while the market is open and a useless one afterwards: measured at 15:25 it
    only ever describes the last few minutes before the close. Reviewing a past
    morning needs the other question — what moved fast *at any point* — so this
    walks the session and keeps each symbol's peak.

    `until` bounds the scan, which is how "what moved fast in the morning" is
    expressed: pass the 11:00 cutoff.
    """
    rows, origin = price_history.movers(day, source, until)
    if not rows:
        return []
    resolution = rows[0].resolution_min
    step = max(resolution, 1)
    out: list[FastMover] = []

    for m in rows:
        series = price_history.series(m.symbol, day, source, until)
        if len(series) < 2:
            continue
        best: FastMover | None = None
        for i, point in enumerate(series):
            # The bar `window_min` earlier, in index terms for this resolution.
            back = max(0, i - max(1, window_min // step))
            if back == i:
                continue
            prior = series[back]
            if prior.price <= 0:
                continue
            elapsed_min = max((point.ts - prior.ts) / 60.0, 1.0)
            window_move = (point.price - prior.price) / prior.price * 100
            speed = window_move / elapsed_min
            if abs(speed) < min_speed:
                continue
            pct_open = ((point.price - m.open_price) / m.open_price * 100) if m.open_price else 0.0
            if abs(pct_open) < min_move:
                continue
            candidate = FastMover(
                symbol=m.symbol,
                pct_from_open=pct_open,
                speed_pct_per_min=speed,
                window_move_pct=window_move,
                last_price=point.price,
                last_ts=point.ts,
                direction="UP" if speed > 0 else "DOWN",
                origin=origin,
                resolution_min=resolution,
            )
            if best is None or abs(candidate.speed_pct_per_min) > abs(best.speed_pct_per_min):
                best = candidate
        if best is not None:
            out.append(best)
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
