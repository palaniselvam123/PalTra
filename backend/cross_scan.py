"""Cross scan: which stocks' SMA fast / slow are about to cross on the N-minute candle.

Read-only. It downloads each stock's recent 1-minute candles from Groww, builds
N-minute candles the way the bot does (`candles.resample`, from 09:15 IST),
averages the closes the way the bot does (`indicators.enrich`), and ranks the
stocks by how close the two averages are to crossing. It places no order and
changes no setting; arming a stock from the list goes through the arm prompt.

Only closed candles are read: the candle still forming is dropped first, so a
row says what the last finished candle said. The estimate of how long a cross
is away is the bot's own heads-up arithmetic (`strategy_engine.minutes_until_cross`)
turned into candles x the candle length, which is what the bot's number means
once the candle is longer than a minute.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from candles import CANDLE_MINUTES, bucket_start, resample
from groww_client import IST, market_is_open
from indicators import enrich, sma_gap_pct

log = logging.getLogger("sma.crossscan")

MAX_SYMBOLS = 250
CONCURRENCY = 3  # parallel candle downloads: gentle on Groww's rate limit
CALL_GAP_SEC = 0.2  # between starting two downloads
WARMUP_DAYS = 4  # calendar days of 1-minute candles before now, so SMA 21 is formed at the open
MIN_RESCAN_SEC = 60  # "Scan now" at most once a minute
LOOKBACK = 3  # closed candles whose gap slope is read (the bot's heads-up uses the same)
GIVE_UP_AFTER = 5  # this many failures before any success: stop and say why
_SYMBOL = re.compile(r"^[A-Z0-9&_-]{1,20}$")

APPROACHING = "APPROACHING"
CROSSED = "CROSSED"
BULLISH = "BULLISH"  # fast SMA is (or will be) above slow
BEARISH = "BEARISH"

Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]


@dataclass
class ScanRow:
    symbol: str
    state: str  # APPROACHING | CROSSED
    side: str  # BULLISH | BEARISH
    ltp: float | None
    sma_fast: float
    sma_slow: float
    gap_pct: float  # signed (fast - slow) / slow x 100 on the last closed candle
    slope_pct: float  # gap change per candle
    candles_to_cross: float | None  # APPROACHING only
    minutes_to_cross: float | None
    crossed_candles_ago: int | None  # CROSSED only: 0 = the last closed candle
    candle_ts: int  # start of the last closed candle (epoch seconds)

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("gap_pct", "slope_pct", "sma_fast", "sma_slow", "candles_to_cross", "minutes_to_cross"):
            if out[key] is not None:
                out[key] = round(out[key], 4)
        return out


def outlook(closed: pd.DataFrame, minutes: int, symbol: str = "", ltp: float | None = None) -> ScanRow | None:
    """Read one stock's enriched CLOSED candles. None when nothing is close to a cross.

    A cross on the last two closed candles is `CROSSED`; otherwise the gap closing
    toward zero is `APPROACHING` (the same rule as `strategy_engine.minutes_until_cross`).
    """
    if closed is None or len(closed) < LOOKBACK:
        return None
    tail = closed.iloc[-LOOKBACK:]
    gaps: list[float] = []
    for _, row in tail.iterrows():
        gap = sma_gap_pct(row.get("sma_9"), row.get("sma_21"))
        if gap is None:
            return None
        gaps.append(gap)
    last = tail.iloc[-1]
    gap, prev = gaps[-1], gaps[-2]
    slope = (gaps[-1] - gaps[0]) / (len(gaps) - 1)
    base = dict(
        symbol=symbol,
        ltp=ltp,
        sma_fast=float(last["sma_9"]),
        sma_slow=float(last["sma_21"]),
        gap_pct=gap,
        slope_pct=slope,
        candle_ts=int(last["ts"]),
    )
    # Crossed between the last two closed candles (0), or one candle earlier and still on that side (1).
    def flipped(then: float, now: float) -> bool:
        return then != 0 and now != 0 and (then < 0) != (now < 0)

    ago = 0 if flipped(prev, gap) else 1 if flipped(gaps[0], prev) and (gap < 0) == (prev < 0) else None
    if ago is not None:
        side = BULLISH if gap > 0 else BEARISH
        return ScanRow(state=CROSSED, side=side, candles_to_cross=None, minutes_to_cross=None, crossed_candles_ago=ago, **base)
    if gap < 0 and slope > 0:
        candles = abs(gap) / slope
        return ScanRow(state=APPROACHING, side=BULLISH, candles_to_cross=candles, minutes_to_cross=candles * minutes, crossed_candles_ago=None, **base)
    if gap > 0 and slope < 0:
        candles = gap / abs(slope)
        return ScanRow(state=APPROACHING, side=BEARISH, candles_to_cross=candles, minutes_to_cross=candles * minutes, crossed_candles_ago=None, **base)
    return None


def closed_bars(frame_1m: pd.DataFrame, minutes: int, now: dt.datetime) -> pd.DataFrame:
    """The stock's N-minute candles with the one still forming dropped."""
    bars = resample(frame_1m, minutes)
    if bars is None or bars.empty:
        return pd.DataFrame()
    now_ts = int(now.timestamp())
    # A bar is closed once its whole span is in the past.
    bars = bars[bars["ts"].astype("int64") + minutes * 60 <= now_ts]
    return bars.reset_index(drop=True)


def scan_one(symbol: str, frame_1m: pd.DataFrame, sma_fast: int, sma_slow: int, minutes: int, now: dt.datetime) -> ScanRow | None:
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    bars = closed_bars(frame_1m, minutes, now)
    if len(bars) < max(int(sma_slow), LOOKBACK) + 1:
        return None
    # The cross reads closes only. Dropping Groww's running volume keeps enrich() from working out
    # each candle's volume, which logs a warning at every day boundary: four days of 213 stocks flooded the log.
    enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow)
    ltp = float(frame_1m["close"].iloc[-1])
    return outlook(enriched, minutes, symbol=symbol, ltp=round(ltp, 2))


def clean_symbols(raw: list[str]) -> list[str]:
    out: list[str] = []
    for item in raw:
        name = (item or "").strip().upper()
        if _SYMBOL.match(name) and name not in out:
            out.append(name)
    return out[:MAX_SYMBOLS]


def cache_key(now: dt.datetime, minutes: int) -> int:
    """Changes when a new candle closes; constant after the close and on weekends."""
    if not market_is_open(now):
        return -1
    return bucket_start(int(now.timestamp()), minutes)


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
    as_of: str | None = None  # when the last finished pass started (IST)
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class CrossScanner:
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
        """Begin a pass unless one is running or the result is already for this candle."""
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
        rows: list[ScanRow] = []
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
                    row = scan_one(symbol, frame, sma_fast, sma_slow, minutes, now)
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
            log.exception("cross scan failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        # Soonest to cross first, then the stocks that just crossed (newest first).
        rows.sort(key=lambda r: (0, r.minutes_to_cross or 0.0) if r.state == APPROACHING else (1, r.crossed_candles_ago or 0))
        self.status.rows = [r.as_dict() for r in rows]
        self.status.as_of = now.isoformat(timespec="seconds")
        self.status.market_open = market_is_open(now)
        if ok == 0 and errors:
            self.status.error = max(errors, key=errors.get)
            self._key = None  # nothing worth caching: the next ask tries again
        else:
            self.status.error = None if not errors else f"{self.status.failed} stock(s) could not be read ({max(errors, key=errors.get)})"
            self._key = key
        self.status.running = False
