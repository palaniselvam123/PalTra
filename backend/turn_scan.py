"""Turning scan: candles turning against the SMA trend before the SMAs cross (read-only; the Scalp page's "Turning" tab).

Owner's example (ICICI Bank, 5-minute candles): SMA 9 below SMA 21 since the morning (bearish), then from 14:15 green
candles in a row closing back above SMA 9 while SMA 9 is still under SMA 21; the SMAs only crossed at 14:40. This scan
lists the stocks in that state now, both ways (a bullish trend with red candles turning it down too).

Per stock, on the picked candle (`minutes` 1/3/5/10/15; `cross_scan.closed_bars`, closed candles only, `enrich` with the
main desk's SMA periods), on the latest session in the download:

* the **trend** (`trend` BULLISH / BEARISH: SMA fast vs slow on the last closed candle), since when (`trend_since`, the
  last cross, across days) and for how many candles (`trend_candles`);
* the **counter run** (`counter_candles`): how many of the last closed candles in a row went the other way (`rule`
  COLOUR = green / red, CLOSES = each close beyond the one before, BOTH), since when (`counter_since`); a row is listed
  only once the run is at least `count` (default 3) and, with `need_fast` (on), the last close is beyond the fast SMA on
  the turn's side;
* how close the cross is: the signed SMA gap % now and when the run began (`gap_pct`, `gap_at_turn_pct`), how much of it
  has closed (`gap_closed_pct`), and the candles / minutes to the cross at the gap's pace over the run
  (`candles_to_cross`, `minutes_to_cross`);
* the move since the run began (`move_since_turn_pct`, signed the turn's way) and today's move signed the trend's way
  (`day_move_pct`), plus the Cross scan's activity (volume, speed).

`turn` is the way the candles are turning (LONG = up against a bearish trend). Nothing here places an order; nothing in the
strategy reads it. The bot's own "Early reversal entry" (counter_candles.py) trades the same picture when it is ticked.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from cross_scan import CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, activity, cache_key, clean_symbols, closed_bars
from groww_client import IST, market_is_open
from indicators import enrich

log = logging.getLogger("sma.turn_scan")

CANDLE_CHOICES = (1, 3, 5, 10, 15)
RULES = ("COLOUR", "CLOSES", "BOTH")
WARMUP_DAYS = 4  # calendar days back, so SMA 21 is formed at the open
DEFAULT_COUNT = 3
MAX_COUNT = 10
MIN_RESCAN_SEC = 60
BULLISH, BEARISH = "BULLISH", "BEARISH"
LONG, SHORT = "LONG", "SHORT"

Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]
Job = Callable[[str, dt.datetime, dt.datetime], Awaitable["TurnRow | None"]]


@dataclass
class TurnRow:
    symbol: str
    trend: str  # BULLISH | BEARISH: SMA fast vs slow now
    turn: str  # LONG (candles turning up) | SHORT
    trend_since: str | None  # HH:MM (or "DD Mon HH:MM" on an earlier day) of the last SMA cross
    trend_candles: int
    counter_candles: int
    counter_since: str
    close: float
    sma_fast: float
    sma_slow: float
    gap_pct: float
    gap_at_turn_pct: float | None
    gap_closed_pct: float | None
    candles_to_cross: float | None
    minutes_to_cross: float | None
    move_since_turn_pct: float | None
    day_move_pct: float | None
    candle_ts: int
    volume: float | None = None
    volume_window: float | None = None
    move_pct: float | None = None
    speed_pct_per_min: float | None = None
    window_min: int = 10

    def as_dict(self) -> dict:
        out = asdict(self)
        for key, value in out.items():
            if isinstance(value, float):
                out[key] = round(value, 4)
        return out


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def _gap(row) -> float | None:
    fast, slow = _num(row.get("sma_9")), _num(row.get("sma_21"))
    if fast is None or slow is None or slow == 0:
        return None
    return (fast - slow) / slow * 100


def _day(ts: int) -> int:
    return (int(ts) + 19_800) // 86_400


def _label(ts: int, today: int) -> str:
    at = dt.datetime.fromtimestamp(int(ts), IST)
    return at.strftime("%H:%M") if _day(ts) == today else at.strftime("%d %b %H:%M")


def _against(bar, prev, turn: str, rule: str) -> bool:
    """Did this closed candle go the turn's way (LONG = up)?"""
    o, c = _num(bar.get("open")), _num(bar.get("close"))
    pc = _num(prev.get("close")) if prev is not None else None
    sign = 1 if turn == LONG else -1
    colour = o is not None and c is not None and (c - o) * sign > 0
    closes = pc is not None and c is not None and (c - pc) * sign > 0
    if rule == "COLOUR":
        return colour
    if rule == "CLOSES":
        return closes
    return colour and closes


def read(
    frame_1m: pd.DataFrame,
    sma_fast: int,
    sma_slow: int,
    minutes: int,
    now: dt.datetime,
    *,
    symbol: str = "",
    count: int = DEFAULT_COUNT,
    rule: str = "COLOUR",
    need_fast: bool = True,
) -> TurnRow | None:
    """One stock's turning read, or None when its candles are not turning against the trend."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    rule = rule if rule in RULES else "COLOUR"
    count = max(1, min(MAX_COUNT, int(count)))
    bars = closed_bars(frame_1m, minutes, now)
    if len(bars) < int(sma_slow) + count + 1:
        return None
    closed = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow).reset_index(drop=True)
    last = closed.iloc[-1]
    gap = _gap(last)
    if gap is None or gap == 0:
        return None
    trend = BULLISH if gap > 0 else BEARISH
    turn = SHORT if trend == BULLISH else LONG
    today = _day(int(last["ts"]))
    # The counter run: the last closed candles in a row that went the turn's way, on today's session only.
    run = 0
    for i in range(len(closed) - 1, 0, -1):
        bar = closed.iloc[i]
        if _day(int(bar["ts"])) != today or not _against(bar, closed.iloc[i - 1], turn, rule):
            break
        run += 1
    if run < count:
        return None
    close = _num(last.get("close"))
    fast = _num(last.get("sma_9"))
    if close is None or fast is None:
        return None
    if need_fast and (close - fast) * (1 if turn == LONG else -1) <= 0:
        return None
    first = len(closed) - run
    # The trend's run: back to the candle after the last SMA cross.
    start = len(closed) - 1
    while start > 0:
        g = _gap(closed.iloc[start - 1])
        if g is None or g == 0 or (g > 0) != (gap > 0):
            break
        start -= 1
    trend_known = start > 0 and _gap(closed.iloc[start - 1]) is not None
    before = closed.iloc[first - 1]
    gap_then = _gap(before)
    closed_share = None
    to_cross = None
    if gap_then is not None and gap_then != 0 and (gap_then > 0) == (gap > 0):
        closed_share = (1 - gap / gap_then) * 100
        pace = (gap_then - gap) / run  # per candle, towards zero when it narrows
        if pace != 0 and (pace > 0) == (gap > 0):
            to_cross = gap / pace
    base = _num(before.get("close"))
    sign = 1 if turn == LONG else -1
    move_turn = (close / base - 1) * 100 * sign if base else None
    today_rows = closed[closed["ts"].astype("int64").map(_day) == today]
    opened = _num(today_rows.iloc[0].get("open")) if len(today_rows) else None
    day_move = (close / opened - 1) * 100 * (1 if trend == BULLISH else -1) if opened else None
    row = TurnRow(
        symbol=symbol,
        trend=trend,
        turn=turn,
        trend_since=_label(int(closed.iloc[start]["ts"]), today) if trend_known else None,
        trend_candles=len(closed) - start,
        counter_candles=run,
        counter_since=_label(int(closed.iloc[first]["ts"]), today),
        close=round(close, 2),
        sma_fast=fast,
        sma_slow=float(last["sma_21"]),
        gap_pct=gap,
        gap_at_turn_pct=gap_then,
        gap_closed_pct=closed_share,
        candles_to_cross=to_cross,
        minutes_to_cross=to_cross * minutes if to_cross is not None else None,
        move_since_turn_pct=move_turn,
        day_move_pct=day_move,
        candle_ts=int(last["ts"]),
    )
    for key, value in activity(frame_1m).items():
        setattr(row, key, value)
    return row


def sort_key(row: TurnRow):
    """Soonest cross first, then the longest counter run, then the longest trend."""
    soon = row.candles_to_cross if row.candles_to_cross is not None else 1e9
    return (soon, -row.counter_candles, -row.trend_candles, row.symbol)


def alert_text(rows: list[dict], minutes: int) -> str:
    lines = [f"Turning before the cross ({minutes}-min candles):"]
    for r in rows[:15]:
        way = "up against a bearish trend" if r["turn"] == LONG else "down against a bullish trend"
        soon = f", cross in ~{r['minutes_to_cross']:.0f} min" if r.get("minutes_to_cross") is not None else ""
        lines.append(f"• {r['symbol']} {way}: {r['counter_candles']} candles since {r['counter_since']}{soon}")
    if len(rows) > 15:
        lines.append(f"…and {len(rows) - 15} more")
    return "\n".join(lines)


@dataclass
class Status:
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    minutes: int = 5
    count: int = DEFAULT_COUNT
    rule: str = "COLOUR"
    need_fast: bool = True
    sma_fast: int = 9
    sma_slow: int = 21
    as_of: str | None = None
    finished_at: str | None = None
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class TurnScanner:
    """One pass at a time; the last finished result is kept until a new candle closes."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._key: tuple | None = None
        self._started_at = 0.0
        # Called with the finished rows (the alert); never fails the scan.
        self.on_done: Callable[[list[dict], int], None] | None = None

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
            "count": s.count,
            "rule": s.rule,
            "need_fast": s.need_fast,
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
        count: int = DEFAULT_COUNT,
        rule: str = "COLOUR",
        need_fast: bool = True,
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
        if rule not in RULES:
            raise ValueError(f"rule must be one of {', '.join(RULES)}")
        count = max(1, min(MAX_COUNT, int(count)))
        now = now or dt.datetime.now(IST)
        key = (cache_key(now, minutes), minutes, count, rule, need_fast, sma_fast, sma_slow, tuple(names))
        if self.status.running:
            return self.snapshot()
        if self._key == key and self.status.as_of is not None and not force:
            return self.snapshot()
        if time.monotonic() - self._started_at < MIN_RESCAN_SEC and self._key is not None and not force:
            return self.snapshot()
        self._started_at = time.monotonic()
        s = self.status
        s.running, s.done, s.total, s.failed, s.error = True, 0, len(names), 0, None
        s.minutes, s.count, s.rule, s.need_fast, s.sma_fast, s.sma_slow = minutes, count, rule, need_fast, sma_fast, sma_slow
        self._task = asyncio.get_running_loop().create_task(self._run(names, fetch, key, now, job))
        return self.snapshot()

    async def _run(self, names, fetch: Fetch, key: tuple, now: dt.datetime, job: Job | None) -> None:
        s = self.status
        start = (now - dt.timedelta(days=WARMUP_DAYS)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        end = now.replace(tzinfo=None)
        rows: list[TurnRow] = []
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
                        row = read(
                            await fetch(symbol, start, end), s.sma_fast, s.sma_slow, s.minutes, now,
                            symbol=symbol, count=s.count, rule=s.rule, need_fast=s.need_fast,
                        )
                    ok += 1
                    if row is not None:
                        rows.append(row)
                except Exception as exc:  # noqa: BLE001
                    text = (str(exc).strip() or type(exc).__name__)[:200]
                    errors[text] = errors.get(text, 0) + 1
                    s.failed += 1
                    if ok == 0 and s.failed >= GIVE_UP_AFTER:
                        abort.set()
                finally:
                    s.done += 1

        try:
            await asyncio.gather(*(one(name, i * CALL_GAP_SEC) for i, name in enumerate(names)))
        except Exception as exc:  # noqa: BLE001
            log.exception("turning scan failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        rows.sort(key=sort_key)
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
        if self.on_done is not None and ok > 0:
            try:
                self.on_done(s.rows, s.minutes)
            except Exception:  # noqa: BLE001
                log.exception("turning scan on_done failed")


class AlertMemory:
    """Each stock and turn once per IST day (memory only)."""

    def __init__(self) -> None:
        self.day: dt.date | None = None
        self.sent: set[tuple[str, str]] = set()

    def fresh(self, rows: list[dict], now: dt.datetime) -> list[dict]:
        day = now.astimezone(IST).date()
        if day != self.day:
            self.day, self.sent = day, set()
        out = [r for r in rows if (r["symbol"], r["turn"]) not in self.sent]
        self.sent.update((r["symbol"], r["turn"]) for r in out)
        return out
