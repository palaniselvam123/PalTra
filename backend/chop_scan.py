"""Chop scan (chop_scan.py): how choppy or trending each stock has looked today.

Read-only, no order path. For each stock in the list, the same 1-minute Groww
candles the Cross scan already downloads are resampled to the user's chosen
candle (1/2/3/5/10/15 min from 09:15 IST), enriched with SMA fast / slow
(`indicators.enrich` on the main desk's SMA periods: the same arithmetic the
bot uses), and today's **closed** candles are walked to count the SMA fast /
slow crosses so far. Nothing here predicts the next move: it describes what
the stock has done this session.

Each row carries:
    `crosses_today`           — SMA fast / slow crosses on today's closed candles.
    `minutes_since_last_cross`— minutes between the start of the last cross candle
                                and now (None when there has been none today).
    `avg_minutes_between`     — average gap, in minutes, between today's crosses.
    `avg_move_pct`            — average absolute price change (%) between two
                                consecutive crosses (close-to-close).
    `score`                   — plain-English bucket: `TRENDING`, `MIXED` or `CHOPPY`.
    plus the stock's today activity (volume, 10-minute volume, signed speed),
    from `cross_scan.activity` so the rules match row-for-row.

Scoring (deliberately simple so the owner can read the rules):
    TRENDING — 0 or 1 crosses so far AND, when there was a cross, the last one
               was more than LONG_RUN_MIN minutes ago.
    CHOPPY   — CHOP_MIN_CROSSES or more today, OR a non-trivial average gap
               that is below CHOP_SHORT_GAP_MIN minutes.
    MIXED    — anything else.

The scanner's lifecycle mirrors `cross_scan.CrossScanner` and reuses the same
`fetch(symbol, start, end)` callable: a scan starts only when the frontend
presses Scan now, one pass at a time, up to CROSS_SCAN_CONCURRENCY downloads
in parallel (default 1) and failing stops after `GIVE_UP_AFTER` errors without
a success.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Awaitable, Callable

import pandas as pd

from candles import CANDLE_MINUTES
from cross_scan import (
    CONCURRENCY,
    CALL_GAP_SEC,
    GIVE_UP_AFTER,
    MIN_RESCAN_SEC,
    WARMUP_DAYS,
    activity,
    cache_key,
    clean_symbols,
    closed_bars,
)
from groww_client import IST, market_is_open
from indicators import enrich, sma_gap_pct

log = logging.getLogger("sma.chopscan")

TRENDING = "TRENDING"
MIXED = "MIXED"
CHOPPY = "CHOPPY"

# Thresholds. Picked on the plain-language reading of the owner's example: one
# cross-only morning that then runs for 60+ minutes is TRENDING; a stock that
# crosses four or more times in a day, or whose crosses come on average less
# than 15 minutes apart, is CHOPPY. Everything else reads MIXED.
LONG_RUN_MIN = 60
CHOP_MIN_CROSSES = 4
CHOP_SHORT_GAP_MIN = 15

Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]


@dataclass
class ChopRow:
    symbol: str
    minutes: int  # the candle size the row was read on
    crosses_today: int
    minutes_since_last_cross: float | None
    avg_minutes_between: float | None
    avg_move_pct: float | None  # abs % move between consecutive crosses, on average
    last_cross_direction: str | None  # "BULLISH" or "BEARISH" from the last cross's new side
    score: str
    ltp: float | None
    candle_ts: int  # start of the last closed candle (epoch seconds)
    volume: float | None = None
    volume_window: float | None = None
    move_pct: float | None = None
    speed_pct_per_min: float | None = None
    window_min: int = 10

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("minutes_since_last_cross", "avg_minutes_between", "avg_move_pct", "move_pct", "speed_pct_per_min"):
            if out[key] is not None:
                out[key] = round(out[key], 4)
        return out


def _ist_day(ts: int) -> int:
    """IST day index (midnight to midnight), from a UTC epoch second."""
    return (ts + 19_800) // 86_400


def _crosses(closed: pd.DataFrame, today: int) -> list[dict]:
    """Return each closed candle on day `today` where the SMA fast / slow gap flipped sign.

    The gap is signed `(fast - slow) / slow x 100`; a flip from negative to
    positive is a bullish cross, the other way round is bearish. Zeros do not
    count as a cross.
    """
    if closed is None or len(closed) < 2:
        return []
    hits: list[dict] = []
    prev_gap: float | None = None
    for _, row in closed.iterrows():
        gap = sma_gap_pct(row.get("sma_9"), row.get("sma_21"))
        if gap is None or prev_gap is None:
            prev_gap = gap if gap is not None else prev_gap
            continue
        if prev_gap != 0 and gap != 0 and (prev_gap < 0) != (gap < 0) and _ist_day(int(row["ts"])) == today:
            hits.append({"ts": int(row["ts"]), "close": float(row.get("close") or 0.0), "side": "BULLISH" if gap > 0 else "BEARISH"})
        prev_gap = gap
    return hits


def analyze(frame_1m: pd.DataFrame, sma_fast: int, sma_slow: int, minutes: int, now: dt.datetime, symbol: str = "") -> ChopRow | None:
    """One stock's chop/trend read. None when the tape isn't long enough yet."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    bars = closed_bars(frame_1m, minutes, now)
    if len(bars) < max(int(sma_slow), 2) + 1:
        return None
    enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow)
    today = _ist_day(int(now.timestamp()))
    hits = _crosses(enriched, today)
    ltp = float(frame_1m["close"].iloc[-1]) if len(frame_1m) else None
    minutes_since: float | None = None
    avg_gap: float | None = None
    avg_move: float | None = None
    last_side: str | None = None
    if hits:
        last_ts = hits[-1]["ts"]
        minutes_since = max(0.0, (int(now.timestamp()) - last_ts) / 60)
        last_side = hits[-1]["side"]
        if len(hits) >= 2:
            gaps = [(hits[i]["ts"] - hits[i - 1]["ts"]) / 60 for i in range(1, len(hits))]
            moves = [
                abs(hits[i]["close"] / hits[i - 1]["close"] - 1) * 100
                for i in range(1, len(hits))
                if hits[i - 1]["close"]
            ]
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            avg_move = sum(moves) / len(moves) if moves else None
    score = score_for(len(hits), minutes_since, avg_gap)
    base = ChopRow(
        symbol=symbol,
        minutes=minutes,
        crosses_today=len(hits),
        minutes_since_last_cross=minutes_since,
        avg_minutes_between=avg_gap,
        avg_move_pct=avg_move,
        last_cross_direction=last_side,
        score=score,
        ltp=round(ltp, 2) if ltp is not None else None,
        candle_ts=int(enriched.iloc[-1]["ts"]),
    )
    return replace(base, **activity(frame_1m))


def score_for(crosses: int, minutes_since: float | None, avg_gap: float | None) -> str:
    """The one place the TRENDING / MIXED / CHOPPY rule lives, so the tests pin it."""
    if crosses >= CHOP_MIN_CROSSES:
        return CHOPPY
    if crosses >= 2 and avg_gap is not None and avg_gap < CHOP_SHORT_GAP_MIN:
        return CHOPPY
    if crosses <= 1 and (crosses == 0 or (minutes_since is not None and minutes_since > LONG_RUN_MIN)):
        return TRENDING
    return MIXED


@dataclass
class Status:
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    minutes: int = 5
    sma_fast: int = 9
    sma_slow: int = 21
    as_of: str | None = None
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class ChopScanner:
    """One scan at a time; the last finished result is kept until the next candle closes."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._key: tuple | None = None
        self._started_at = 0.0

    def snapshot(self) -> dict:
        s = self.status
        return {
            "running": s.running,
            "done": s.done,
            "total": s.total,
            "failed": s.failed,
            "error": s.error,
            "minutes": s.minutes,
            "sma_fast": s.sma_fast,
            "sma_slow": s.sma_slow,
            "as_of": s.as_of,
            "market_open": s.market_open,
            "rows": s.rows,
        }

    def start(
        self,
        symbols: list[str],
        fetch: Fetch,
        *,
        minutes: int = 5,
        sma_fast: int = 9,
        sma_slow: int = 21,
        force: bool = False,
        now: dt.datetime | None = None,
    ) -> dict:
        if minutes not in CANDLE_MINUTES:
            raise ValueError(f"Candle size must be one of {', '.join(str(m) for m in CANDLE_MINUTES)} minutes")
        names = clean_symbols(symbols)
        if not names:
            raise ValueError("No stocks to scan")
        now = now or dt.datetime.now(IST)
        key = (cache_key(now, minutes), minutes, sma_fast, sma_slow, tuple(names))
        if self.status.running:
            return self.snapshot()
        fresh = self._key == key and self.status.as_of is not None
        if fresh and not force:
            return self.snapshot()
        if time.monotonic() - self._started_at < MIN_RESCAN_SEC and self._key is not None:
            return self.snapshot()
        self._started_at = time.monotonic()
        self.status.running = True
        self.status.done = 0
        self.status.total = len(names)
        self.status.failed = 0
        self.status.error = None
        self.status.minutes, self.status.sma_fast, self.status.sma_slow = minutes, sma_fast, sma_slow
        self._task = asyncio.get_running_loop().create_task(self._run(names, fetch, key, minutes, sma_fast, sma_slow, now))
        return self.snapshot()

    async def _run(self, names: list[str], fetch: Fetch, key: tuple, minutes: int, sma_fast: int, sma_slow: int, now: dt.datetime) -> None:
        start = (now - dt.timedelta(days=WARMUP_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        end = now.replace(tzinfo=None)
        rows: list[ChopRow] = []
        errors: dict[str, int] = {}
        ok = 0
        gate = asyncio.Semaphore(CONCURRENCY)
        abort = asyncio.Event()

        async def one(symbol: str, delay: float) -> None:
            nonlocal ok
            await asyncio.sleep(delay)
            if abort.is_set():
                return
            async with gate:
                if abort.is_set():
                    return
                try:
                    frame = await fetch(symbol, start, end)
                    row = analyze(frame, sma_fast, sma_slow, minutes, now, symbol=symbol)
                    ok += 1
                    if row is not None:
                        rows.append(row)
                except Exception as exc:  # noqa: BLE001
                    text = (str(exc).strip() or type(exc).__name__)[:200]
                    errors[text] = errors.get(text, 0) + 1
                    self.status.failed += 1
                    if ok == 0 and self.status.failed >= GIVE_UP_AFTER:
                        abort.set()
                finally:
                    self.status.done += 1

        try:
            await asyncio.gather(*(one(name, i * CALL_GAP_SEC) for i, name in enumerate(names)))
        except Exception as exc:  # noqa: BLE001
            log.exception("chop scan failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        # The most trending first, then mixed, then choppy. Inside a bucket:
        # TRENDING keeps the longest run on top; CHOPPY keeps the most crosses on top.
        bucket = {TRENDING: 0, MIXED: 1, CHOPPY: 2}
        rows.sort(
            key=lambda r: (
                bucket.get(r.score, 3),
                -(r.minutes_since_last_cross or 0.0) if r.score == TRENDING else -r.crosses_today if r.score == CHOPPY else 0,
            )
        )
        self.status.rows = [r.as_dict() for r in rows]
        self.status.as_of = now.isoformat(timespec="seconds")
        self.status.market_open = market_is_open(now)
        if ok == 0 and errors:
            self.status.error = max(errors, key=errors.get)
            self._key = None
        else:
            self.status.error = None if not errors else f"{self.status.failed} stock(s) could not be read ({max(errors, key=errors.get)})"
            self._key = key
        self.status.running = False
