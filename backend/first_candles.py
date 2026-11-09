"""First candles (read-only; the Scalp page's Most active card, "First candles"): the day's first N candles of each stock,
so the owner no longer opens every chart to check their colours before the Second candle routine.

For each stock, on the latest session in its 1-minute download (today in market hours, the last session after hours),
the candles of `minutes` (1/3/5/10/15) from 09:15 are read up to the Nth (`candle_no` 1-6), closed candles only:
each one's colour (G / R / D for a doji) and body %, how many were green / red and the bodies added up, and the call
by the same rule as the Second candle test (`second_candle.majority`; `dir_rule` COLOUR / CLOSES / BOTH says what makes a
candle up or down, `dirs`): `NTH` = the Nth candle's colour, `MAJORITY` =
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

import numpy as np
import pandas as pd

from candles import resample
from cross_scan import CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, clean_symbols
from groww_client import IST
import heikin_ashi
from nth_candle import DIR_RULES, candle_moves, dir_words
from types import SimpleNamespace
from second_candle import MAJORITY_BY, SIDE_RULES, majority

log = logging.getLogger(__name__)
SIZES = (1, 3, 5, 10, 15)
MAX_CANDLE_NO = 6
MAX_LAST = 6
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
    #: What makes a candle up / down (nth_candle.DIR_RULES): COLOUR green / red, CLOSES close vs the previous close, BOTH.
    dir_rule: str = "COLOUR"
    #: Read Heikin Ashi candles (colour and body from the HA candle; closes stay real), like the bot's `ha_colours`.
    ha: bool = False
    #: Also the last N closed candles before the read time (0 = off), so a saved scan shows what the chart looked like.
    last_n: int = 3

    def clean(self) -> "Rule":
        self.minutes = self.minutes if self.minutes in SIZES else 5
        self.candle_no = max(1, min(MAX_CANDLE_NO, int(self.candle_no or 2)))
        self.side_rule = self.side_rule if self.side_rule in SIDE_RULES else "NTH"
        self.majority_by = self.majority_by if self.majority_by in MAJORITY_BY else "COUNT"
        self.min_body_pct = max(0.0, min(5.0, float(self.min_body_pct or 0.0)))
        self.dir_rule = self.dir_rule if self.dir_rule in DIR_RULES else "COLOUR"
        self.ha = bool(self.ha)
        self.last_n = max(0, min(MAX_LAST, int(self.last_n or 0)))
        return self


def _closed(tape: pd.DataFrame, minutes: int, now: dt.datetime) -> tuple[pd.DataFrame, dt.date | None, float | None]:
    """The latest session's candles of `minutes`, closed ones only, that session's date and the close before it."""
    if tape is None or getattr(tape, "empty", True):
        return pd.DataFrame(), None, None
    days = pd.to_datetime(tape["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    day = days.max()
    today = tape[days.values == day].reset_index(drop=True)
    earlier = tape[days.values < day]
    prev = float(earlier["close"].iloc[-1]) if not earlier.empty else None
    candles = resample(today, minutes) if minutes > 1 else today
    if candles is None or candles.empty:
        return pd.DataFrame(), day, prev
    cut = now.timestamp()
    closed = candles[candles["ts"].astype("int64") + minutes * 60 <= cut].reset_index(drop=True)
    return closed, day, prev


def _shapes(candles: pd.DataFrame, ha: bool) -> list[list[float]]:
    """Each candle as [open, high, low, close] to draw: the real one, or the Heikin Ashi one (HA open / close, its high
    and low stretched to cover them), seeded on the session's first candle like the bot's `ha_colours`."""
    if candles is None or candles.empty:
        return []
    o = candles["open"].astype(float).to_numpy()
    h = candles["high"].astype(float).to_numpy()
    lo = candles["low"].astype(float).to_numpy()
    c = candles["close"].astype(float).to_numpy()
    if ha:
        ho, hc = heikin_ashi.candles(candles)
        o, c = ho.to_numpy(dtype=float), hc.to_numpy(dtype=float)
        h = np.maximum.reduce([h, o, c])
        lo = np.minimum.reduce([lo, o, c])
    return [[round(float(a), 2), round(float(b), 2), round(float(x), 2), round(float(d), 2)] for a, b, x, d in zip(o, h, lo, c)]


def _last(candles: pd.DataFrame, rule: Rule) -> dict:
    """The last `last_n` closed candles of the session before the read time, as drawn (HA when `ha`), with the time
    the last one closed: {"last_ohlc": [...], "last_at": "HH:MM"} (empty when off or nothing has closed)."""
    if not rule.last_n or candles is None or candles.empty:
        return {"last_ohlc": [], "last_at": None}
    shapes = _shapes(candles, rule.ha)[-rule.last_n:]
    closed_at = int(candles["ts"].iloc[-1]) + rule.minutes * 60
    return {"last_ohlc": shapes, "last_at": dt.datetime.fromtimestamp(closed_at, IST).strftime("%H:%M")}


def read(symbol: str, tape: pd.DataFrame, now: dt.datetime, rule: Rule) -> dict | None:
    candles, day, prev_close = _closed(tape, rule.minutes, now)
    if day is None:
        return None
    last = _last(candles, rule)
    n = rule.candle_no
    first = candles.iloc[:n]
    shapes = _shapes(first, rule.ha)
    if rule.ha and not first.empty:
        first = heikin_ashi.view(first, SimpleNamespace(ha_colours=True))
    colours, bodies = [], []
    for (o, _h, _l, cl) in shapes:
        colours.append("G" if cl > o else "R" if cl < o else "D")
        bodies.append(round(abs(cl - o) / o * 100, 3) if o > 0 else 0.0)
    moves = candle_moves(first["open"].astype(float), first["close"].astype(float), prev_close, rule.dir_rule)
    dirs = ["U" if m > 0 else "D" if m < 0 else "0" for m in moves]
    row: dict = {"symbol": symbol, "day": day.isoformat(), "colours": colours, "bodies": bodies, "dirs": dirs,
                 "dir_rule": rule.dir_rule, "ha": rule.ha, "ohlc": shapes, "closed": len(first), **last}
    if len(first) < n:
        open_at = dt.datetime.combine(day, dt.time(9, 15), IST) + dt.timedelta(minutes=rule.minutes * n)
        row.update(call=None, why=f"waiting: candle {n} closes at {open_at.strftime('%H:%M')}", matched=None)
        return row
    most, greens, reds, net = majority(first, n, rule.majority_by, rule.dir_rule, prev_close)
    row.update(greens=greens, reds=reds, net_body=round(net, 2))
    nth = dirs[-1]
    nth_side = "LONG" if nth == "U" else "SHORT" if nth == "D" else None
    up_word, down_word = dir_words(rule.dir_rule)
    call, why = None, None
    if rule.side_rule == "MAJORITY":
        call = most
        why = None if most else "no majority (tie)"
    else:
        if nth_side is None:
            why = f"candle {n} is a doji" if rule.dir_rule == "COLOUR" else f"candle {n} is neither {up_word} nor {down_word}"
        elif rule.side_rule == "AGREE" and most != nth_side:
            why = f"candle {n} is against the majority"
        else:
            call = nth_side
    o_n = float(first["open"].iloc[-1])
    size = abs(moves[-1]) / o_n * 100 if rule.dir_rule == "CLOSES" and o_n > 0 else bodies[-1]
    if call and rule.min_body_pct > 0 and size < rule.min_body_pct:
        word = "move" if rule.dir_rule == "CLOSES" else "body"
        call, why = None, f"candle {n} {word} {size:.2f}% under {rule.min_body_pct:g}%"
    want = "U" if call == "LONG" else "D" if call == "SHORT" else None
    row.update(
        call=call,
        why=why,
        matched=sum(1 for d in dirs if d == want) if want else None,
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
        self._at: str | None = None

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
        rule = rule.clean()
        when = now.isoformat(timespec="minutes") if now else None
        same = self.status.rule == rule.__dict__ and self._at == when
        if self.status.running or (same and time.monotonic() - self._started_at < MIN_RESCAN_SEC):
            return self.snapshot()
        self._started_at = time.monotonic()
        self._at = when
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
