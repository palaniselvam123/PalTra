"""Carry-over scan: did yesterday's SMA trend carry on today? (read-only; the Scalp page's "Carry-over" tab).

For each stock, on the bot's candle size (`minutes`, 1/3/5/10/15), from the same 1-minute Groww candles the other scans
download (closed candles only, the SMAs computed straight across the days like the bot's):

* **Yesterday's close** (`prev_*`): which way SMA fast vs slow pointed on yesterday's last candle (`LONG` up / `SHORT`
  down), the SMA fast and slow there (the "last day moving average"), their gap %, the close, and since when that side
  had held (`prev_since`, the start of the run, across days).
* **Today**: the open's gap from yesterday's close in % (`gap_pct`, signed; `gap_with` when it went the trend's way);
  whether the trend **continued** (no SMA cross against it on today's closed candles), **reversed** (crossed against it and
  still against, `reversed_at`) or came **back** (crossed against it, then back with it); the last price's deviation from
  yesterday's closing SMA fast in ₹ and % (`dev_rs`, `dev_pct`, signed: + above, − below) and from the slow SMA
  (`dev_slow_pct`); the move since yesterday's close signed the trend's way (`move_with_pct`); and the furthest the price
  went with and against yesterday's direction (`best_pct` / `best_at`, `worst_pct` / `worst_at`, from yesterday's close).

"Today" is the latest session in the download (the one before it is "yesterday"), so after hours or on a weekend it reads
the last two sessions. Nothing here places an order; nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from cross_scan import CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, cache_key, clean_symbols, closed_bars
from groww_client import IST, market_is_open
from indicators import enrich

log = logging.getLogger("sma.carry_scan")

CANDLE_CHOICES = (1, 3, 5, 10, 15)
WARMUP_DAYS = 7  # calendar days back: yesterday's SMAs need a session before it, over a weekend too
CONTINUED, REVERSED, BACK = "CONTINUED", "REVERSED", "BACK"
LONG, SHORT = "LONG", "SHORT"


@dataclass
class CarryRow:
    symbol: str
    prev_day: str
    prev_side: str
    prev_since: str | None
    prev_close: float
    prev_sma_fast: float
    prev_sma_slow: float
    prev_gap_pct: float
    day: str
    open: float | None
    gap_pct: float | None
    gap_with: bool | None
    status: str
    reversed_at: str | None
    back_at: str | None
    side_now: str | None
    last: float
    last_at: str
    dev_rs: float
    dev_pct: float
    dev_slow_pct: float
    move_with_pct: float
    best_pct: float
    best_at: str | None
    worst_pct: float
    worst_at: str | None
    candles_today: int

    def as_dict(self) -> dict:
        return asdict(self)


def _days(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts.astype("int64"), unit="s", utc=True).dt.tz_convert(IST).dt.date


def _clock(ts) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def _when(ts) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%d %b %H:%M")


def _side(fast, slow) -> str | None:
    if fast is None or slow is None or pd.isna(fast) or pd.isna(slow) or fast == slow:
        return None
    return LONG if fast > slow else SHORT


def _pct(a: float, b: float) -> float:
    return round((a - b) / b * 100, 3) if b else 0.0


def read(frame_1m: pd.DataFrame, sma_fast: int, sma_slow: int, minutes: int, now: dt.datetime, symbol: str = "") -> CarryRow | None:
    """One stock's carry-over row, or None without two sessions of candles and formed SMAs at yesterday's close."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    tape = frame_1m.sort_values("ts").reset_index(drop=True)
    days = _days(tape["ts"])
    sessions = sorted(set(days))
    if len(sessions) < 2:
        return None
    today, yday = sessions[-1], sessions[-2]
    bars = closed_bars(tape, minutes, now)
    if bars is None or bars.empty:
        return None
    bars = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow).reset_index(drop=True)
    bar_days = _days(bars["ts"])
    prev = bars[bar_days == yday]
    if prev.empty:
        return None
    last_prev = prev.iloc[-1]
    prev_side = _side(last_prev.get("sma_fast"), last_prev.get("sma_slow"))
    if prev_side is None:
        return None
    # Since when yesterday's side had held (walking back across days).
    i = int(prev.index[-1])
    while i > 0 and _side(bars.at[i - 1, "sma_fast"], bars.at[i - 1, "sma_slow"]) == prev_side:
        i -= 1
    prev_since = _when(bars.at[i, "ts"])
    prev_close = float(last_prev["close"])
    fast0, slow0 = float(last_prev["sma_fast"]), float(last_prev["sma_slow"])
    sign = 1 if prev_side == LONG else -1

    # Today on the bot's candle: the first closed candle against yesterday's side, and whether it came back.
    now_bars = bars[bar_days == today]
    status, reversed_at, back_at, side_now = CONTINUED, None, None, prev_side
    for _, b in now_bars.iterrows():
        s = _side(b.get("sma_fast"), b.get("sma_slow"))
        if s is None:
            continue
        side_now = s
        if s != prev_side and reversed_at is None:
            reversed_at, status = _clock(b["ts"]), REVERSED
        elif s == prev_side and status == REVERSED:
            back_at, status = _clock(b["ts"]), BACK
        elif s != prev_side and status == BACK:
            status, back_at = REVERSED, None

    # Today's 1-minute tape: open, last, best / worst from yesterday's close.
    mine = tape[days == today]
    if mine.empty:
        return None
    first = mine.iloc[0]
    open_ = float(first["open"]) if pd.notna(first.get("open")) else None
    gap = _pct(open_, prev_close) if open_ else None
    last = float(mine.iloc[-1]["close"])
    hi_i, lo_i = mine["high"].idxmax(), mine["low"].idxmin()
    up, down = _pct(float(mine.at[hi_i, "high"]), prev_close), _pct(float(mine.at[lo_i, "low"]), prev_close)
    best, worst = (up, down) if sign > 0 else (-down, -up)
    best_at, worst_at = (_clock(mine.at[hi_i, "ts"]), _clock(mine.at[lo_i, "ts"])) if sign > 0 else (
        _clock(mine.at[lo_i, "ts"]), _clock(mine.at[hi_i, "ts"]))
    return CarryRow(
        symbol=symbol,
        prev_day=yday.isoformat(),
        prev_side=prev_side,
        prev_since=prev_since,
        prev_close=round(prev_close, 2),
        prev_sma_fast=round(fast0, 2),
        prev_sma_slow=round(slow0, 2),
        prev_gap_pct=_pct(fast0, slow0),
        day=today.isoformat(),
        open=round(open_, 2) if open_ else None,
        gap_pct=gap,
        gap_with=None if gap is None or gap == 0 else (gap * sign > 0),
        status=status,
        reversed_at=reversed_at,
        back_at=back_at,
        side_now=side_now if len(now_bars) else None,
        last=round(last, 2),
        last_at=_clock(mine.iloc[-1]["ts"]),
        dev_rs=round(last - fast0, 2),
        dev_pct=_pct(last, fast0),
        dev_slow_pct=_pct(last, slow0),
        move_with_pct=round(_pct(last, prev_close) * sign, 3),
        best_pct=round(best, 3),
        best_at=best_at,
        worst_pct=round(worst, 3),
        worst_at=worst_at,
        candles_today=len(now_bars),
    )


def sort_key(row: CarryRow) -> tuple:
    """Continued first, then back with it, then reversed; within each, the biggest move the trend's way."""
    order = {CONTINUED: 0, BACK: 1, REVERSED: 2}
    return (order.get(row.status, 3), -row.move_with_pct, row.symbol)


# ---- scanner lifecycle (mirrors morning_scan.MorningScanner) ---------------------------------

MIN_RESCAN_SEC = 30
Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]
Job = Callable[[str, dt.datetime, dt.datetime], Awaitable["CarryRow | None"]]


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
    finished_at: str | None = None
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class CarryScanner:
    """One pass at a time; the last finished result is kept until a new candle closes."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._key: tuple | None = None
        self._started_at = 0.0

    def stop(self, reason: str) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.status.running:
            self.status.running = False
            self.status.error = reason
            self._key = None

    def snapshot(self) -> dict:
        s = self.status
        return {
            "running": s.running,
            "done": s.done,
            "total": s.total,
            "failed": s.failed,
            "error": s.error,
            "minutes": s.minutes,
            "minutes_choices": list(CANDLE_CHOICES),
            "sma_fast": s.sma_fast,
            "sma_slow": s.sma_slow,
            "as_of": s.as_of,
            "finished_at": s.finished_at,
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
        job: Job | None = None,
    ) -> dict:
        names = clean_symbols(symbols)
        if not names:
            raise ValueError("No stocks to scan")
        if minutes not in CANDLE_CHOICES:
            raise ValueError(f"minutes must be one of {', '.join(map(str, CANDLE_CHOICES))}")
        now = now or dt.datetime.now(IST)
        key = (cache_key(now, minutes), minutes, sma_fast, sma_slow, tuple(names))
        if self.status.running:
            return self.snapshot()
        if self._key == key and self.status.as_of is not None and not force:
            return self.snapshot()
        if time.monotonic() - self._started_at < MIN_RESCAN_SEC and self._key is not None and not force:
            return self.snapshot()
        self._started_at = time.monotonic()
        s = self.status
        s.running, s.done, s.total, s.failed, s.error = True, 0, len(names), 0, None
        s.minutes, s.sma_fast, s.sma_slow = minutes, sma_fast, sma_slow
        self._task = asyncio.get_running_loop().create_task(self._run(names, fetch, key, minutes, sma_fast, sma_slow, now, job))
        return self.snapshot()

    async def _run(self, names, fetch: Fetch, key: tuple, minutes: int, sma_fast: int, sma_slow: int, now: dt.datetime,
                   job: Job | None) -> None:
        start = (now - dt.timedelta(days=WARMUP_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        end = now.replace(tzinfo=None)
        rows: list[CarryRow] = []
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
                    if job is not None:
                        row = await job(symbol, start, end)
                    else:
                        row = read(await fetch(symbol, start, end), sma_fast, sma_slow, minutes, now, symbol=symbol)
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
            log.exception("carry-over scan failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        rows.sort(key=sort_key)
        s = self.status
        s.as_of = now.isoformat(timespec="seconds")
        s.market_open = market_is_open(now)
        s.rows = [r.as_dict() for r in rows]
        if ok == 0 and errors:
            s.error = max(errors, key=errors.get)
            self._key = None
        else:
            s.error = None if not errors else f"{s.failed} stock(s) could not be read ({max(errors, key=errors.get)})"
            self._key = key
        s.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        s.running = False
