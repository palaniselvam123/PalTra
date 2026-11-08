"""First candles (read-only; the Scalp page's Most active card, "First candles"): the day's first N candles of each stock,
so the owner no longer opens every chart to check their colours before the Second candle routine.

For each stock, on the latest session in its 1-minute download (today in market hours, the last session after hours),
the candles of `minutes` (1/3/5/10/15) from 09:15 are read up to the Nth (`candle_no` 1-6), closed candles only:
each one's colour (G / R / D for a doji) and body %, how many were green / red and the bodies added up, and the call
by the same rule as the Second candle test (`second_candle.majority`): `NTH` = the Nth candle's colour, `MAJORITY` =
the way most of candles 1..N went (a tie = no call), `AGREE` = the Nth candle's colour only when most agree;
`majority_by` COUNT (green vs red) | SIZE (bodies added up); a body under `min_body_pct` = no call. `matched` = how many
of candles 1..N went the call's way. Before the Nth candle has closed the row says when it will (`waiting`).

`FirstCandleScanner` mirrors the 09:30 morning scan (one pass at a time, paced Groww calls). No order path; nothing in
the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from candles import resample
from cross_scan import CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, clean_symbols
from groww_client import IST
from second_candle import MAJORITY_BY, SIDE_RULES, majority

log = logging.getLogger(__name__)
SIZES = (1, 3, 5, 10, 15)
MAX_CANDLE_NO = 6
DOWNLOAD_DAYS = 5  # enough to reach the last session over a weekend / holiday
MIN_RESCAN_SEC = 20
Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]


@dataclass
class Rule:
    minutes: int = 5
    candle_no: int = 2
    side_rule: str = "NTH"
    majority_by: str = "COUNT"
    min_body_pct: float = 0.0

    def clean(self) -> "Rule":
        self.minutes = self.minutes if self.minutes in SIZES else 5
        self.candle_no = max(1, min(MAX_CANDLE_NO, int(self.candle_no or 2)))
        self.side_rule = self.side_rule if self.side_rule in SIDE_RULES else "NTH"
        self.majority_by = self.majority_by if self.majority_by in MAJORITY_BY else "COUNT"
        self.min_body_pct = max(0.0, min(5.0, float(self.min_body_pct or 0.0)))
        return self


def _closed(tape: pd.DataFrame, minutes: int, now: dt.datetime) -> tuple[pd.DataFrame, dt.date | None]:
    """The latest session's candles of `minutes`, closed ones only, and that session's date."""
    if tape is None or getattr(tape, "empty", True):
        return pd.DataFrame(), None
    days = pd.to_datetime(tape["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    day = days.max()
    today = tape[days.values == day].reset_index(drop=True)
    candles = resample(today, minutes) if minutes > 1 else today
    if candles is None or candles.empty:
        return pd.DataFrame(), day
    cut = now.timestamp()
    closed = candles[candles["ts"].astype("int64") + minutes * 60 <= cut].reset_index(drop=True)
    return closed, day


def read(symbol: str, tape: pd.DataFrame, now: dt.datetime, rule: Rule) -> dict | None:
    candles, day = _closed(tape, rule.minutes, now)
    if day is None:
        return None
    n = rule.candle_no
    first = candles.iloc[:n]
    colours, bodies = [], []
    for _, c in first.iterrows():
        o, cl = float(c["open"]), float(c["close"])
        colours.append("G" if cl > o else "R" if cl < o else "D")
        bodies.append(round(abs(cl - o) / o * 100, 3) if o > 0 else 0.0)
    row: dict = {"symbol": symbol, "day": day.isoformat(), "colours": colours, "bodies": bodies, "closed": len(first)}
    if len(first) < n:
        open_at = dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=rule.minutes * n)
        row.update(call=None, why=f"waiting: candle {n} closes at {open_at.strftime('%H:%M')}", matched=None)
        return row
    most, greens, reds, net = majority(first, n, rule.majority_by)
    row.update(greens=greens, reds=reds, net_body=round(net, 2))
    nth = colours[-1]
    nth_side = "LONG" if nth == "G" else "SHORT" if nth == "R" else None
    call, why = None, None
    if rule.side_rule == "MAJORITY":
        call = most
        why = None if most else "no majority (tie)"
    else:
        if nth_side is None:
            why = f"candle {n} is a doji"
        elif rule.side_rule == "AGREE" and most != nth_side:
            why = f"candle {n} is against the majority"
        else:
            call = nth_side
    if call and rule.min_body_pct > 0 and bodies[-1] < rule.min_body_pct:
        call, why = None, f"candle {n} body {bodies[-1]:.2f}% under {rule.min_body_pct:g}%"
    want = "G" if call == "LONG" else "R" if call == "SHORT" else None
    row.update(
        call=call,
        why=why,
        matched=sum(1 for c in colours if c == want) if want else None,
        entry_at=(dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=rule.minutes * n)).strftime("%H:%M"),
        last=round(float(candles["close"].iloc[-1]), 2) if not candles.empty else None,
    )
    return row


def sort_key(row: dict) -> tuple:
    has = 0 if row.get("call") else 1
    return (has, -(row.get("matched") or 0), row["symbol"])


@dataclass
class Status:
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    as_of: str | None = None
    finished_at: str | None = None
    rule: dict = field(default_factory=dict)
    rows: list[dict] = field(default_factory=list)


class FirstCandleScanner:
    """One pass at a time; the last finished rows stay until the next."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._started_at = 0.0

    def stop(self, reason: str) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.status.running:
            self.status.running = False
            self.status.error = reason

    def snapshot(self) -> dict:
        s = self.status
        return {
            "running": s.running, "done": s.done, "total": s.total, "failed": s.failed, "error": s.error,
            "as_of": s.as_of, "finished_at": s.finished_at, "rule": s.rule, "rows": s.rows,
        }

    def start(self, symbols: list[str], fetch: Fetch, rule: Rule, *, now: dt.datetime | None = None) -> dict:
        names = clean_symbols(symbols)
        if not names:
            raise ValueError("No stocks to read")
        if self.status.running or time.monotonic() - self._started_at < MIN_RESCAN_SEC:
            return self.snapshot()
        self._started_at = time.monotonic()
        rule = rule.clean()
        s = self.status
        s.running, s.done, s.total, s.failed, s.error = True, 0, len(names), 0, None
        s.rule = rule.__dict__.copy()
        self._task = asyncio.get_running_loop().create_task(self._run(names, fetch, rule, now))
        return self.snapshot()

    async def _run(self, names: list[str], fetch: Fetch, rule: Rule, now: dt.datetime | None) -> None:
        now = now or dt.datetime.now(IST)
        start = (now - dt.timedelta(days=DOWNLOAD_DAYS)).replace(tzinfo=None)
        end = now.replace(tzinfo=None)
        rows: list[dict] = []
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
                    row = await asyncio.to_thread(read, symbol, frame, now, rule)
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
            log.exception("first-candle read failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        rows.sort(key=sort_key)
        s = self.status
        s.as_of = now.isoformat(timespec="seconds")
        s.rows = rows
        if ok == 0 and errors:
            s.error = max(errors, key=errors.get)
        else:
            s.error = None if not errors else f"{s.failed} stock(s) could not be read ({max(errors, key=errors.get)})"
        s.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        s.running = False
