"""Scan accuracy (scan_accuracy.py): how a saved scan turned out, checked after the day closed.

For one saved run (scan_history.py), each stock's 1-minute Groww candles for that
day are downloaded once more and read from the scan's time to the close:

* the scan's **call**: the side the row pointed to. Cross scan: the side the SMAs
  are crossing to (or crossed to). Trend quality: the side the fast SMA was on
  for the scan's candle size (else the last cross's side).
* the price at the scan (the last 1-minute close before it), then the move
  15, 30 and 60 minutes later and at the close, in % **with the call** (a fall
  counts as + for a bearish call), plus the best and worst move on the way.
* the next SMA cross on the scan's candle size after the scan: for Cross scan
  "about to cross" rows, did it cross the predicted way and within double the
  estimated time; for Trend quality, how long the trend held (no cross = till
  the close).

`summarize` turns the rows into plain hit rates per group (all, quiet runners,
strong trends; approaching / just crossed). Read-only: candles only, no order
path, nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from chop_scan import _crosses, _ist_day
from cross_scan import APPROACHING, CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, WARMUP_DAYS, closed_bars
from groww_client import IST
from indicators import enrich

log = logging.getLogger("sma.scanaccuracy")

AFTER_MIN = (15, 30, 60)
CLOSE_TIME = dt.time(15, 30)
ON_TIME_FACTOR = 2  # a predicted cross "on time" when it came within double the estimate

Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]
Job = Callable[[str, dt.datetime, dt.datetime], Awaitable[dict | None]]


def scan_time(as_of: str) -> dt.datetime:
    t = dt.datetime.fromisoformat(as_of)
    return t if t.tzinfo else t.replace(tzinfo=IST)


def day_closed(as_of: str, now: dt.datetime | None = None) -> bool:
    """True once the scan's trading day has ended (after 15:30 IST that day)."""
    t = scan_time(as_of).astimezone(IST)
    close = dt.datetime.combine(t.date(), CLOSE_TIME, tzinfo=IST)
    return (now or dt.datetime.now(IST)) >= close + dt.timedelta(minutes=1)


def window(as_of: str) -> tuple[dt.datetime, dt.datetime]:
    """The download for a check: WARMUP_DAYS before the scan's day (so the SMAs are formed) to its close."""
    day = scan_time(as_of).astimezone(IST).date()
    start = dt.datetime.combine(day - dt.timedelta(days=WARMUP_DAYS), dt.time(0, 0))
    end = dt.datetime.combine(day, dt.time(15, 31))
    return start, end


def _by_minutes(book: dict | None, minutes: int):
    if not isinstance(book, dict):
        return None
    return book.get(minutes, book.get(str(minutes)))


def call_side(kind: str, row: dict, minutes: int) -> str | None:
    if kind == "cross":
        side = row.get("side")
    else:
        side = _by_minutes(row.get("side_by_minutes"), minutes) or row.get("last_cross_direction")
    return side if side in ("BULLISH", "BEARISH") else None


def _r(value: float | None, digits: int = 3) -> float | None:
    return None if value is None else round(float(value), digits)


def evaluate(kind: str, row: dict, frame_1m: pd.DataFrame, as_of: str, minutes: int, sma_fast: int, sma_slow: int) -> dict | None:
    """One stock: what happened after the scan. None when the day's candles are missing."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    t0 = scan_time(as_of)
    t0_ts = int(t0.timestamp())
    day_idx = _ist_day(t0_ts)
    close_ts = int(dt.datetime.combine(t0.astimezone(IST).date(), CLOSE_TIME, tzinfo=IST).timestamp())
    df = frame_1m.sort_values("ts").reset_index(drop=True)
    ts = df["ts"].astype("int64")
    day = df[(ts + 19_800) // 86_400 == day_idx]
    if day.empty:
        return None
    side = call_side(kind, row, minutes)
    sign = -1.0 if side == "BEARISH" else 1.0

    # Price at the scan: the last 1-minute candle that had closed by then.
    before = day[day["ts"].astype("int64") + 60 <= t0_ts]
    entry = float(before["close"].iloc[-1]) if len(before) else (float(row["ltp"]) if row.get("ltp") else None)
    if not entry:
        return None

    def move(price: float | None) -> float | None:
        return None if price is None else sign * (price / entry - 1) * 100

    def price_at(at_ts: int) -> float | None:
        if at_ts > close_ts + 60:
            return None
        done = day[day["ts"].astype("int64") + 60 <= at_ts]
        return float(done["close"].iloc[-1]) if len(done) and int(done["ts"].iloc[-1]) + 60 > t0_ts else None

    after = day[day["ts"].astype("int64") + 60 > t0_ts]
    out = {
        "symbol": row.get("symbol"),
        "side": side,
        "entry": _r(entry, 2),
        "close_price": None,
        "close_pct": None,
        "best_pct": None,
        "worst_pct": None,
    }
    for k in AFTER_MIN:
        out[f"after_{k}_pct"] = _r(move(price_at(t0_ts + k * 60)))
    if len(after):
        last = float(after["close"].iloc[-1])
        out["close_price"] = _r(last, 2)
        out["close_pct"] = _r(move(last))
        highs = [move(float(v)) for v in after["high"]]
        lows = [move(float(v)) for v in after["low"]]
        both = highs + lows
        out["best_pct"] = _r(max(both))
        out["worst_pct"] = _r(min(both))

    # The next SMA cross on the scan's candle size, closed after the scan.
    end_now = dt.datetime.fromtimestamp(close_ts + 60, tz=IST)
    bars = closed_bars(df, minutes, end_now)
    next_cross = None
    if len(bars) > max(int(sma_slow), 2):
        enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow)
        for hit in _crosses(enriched, day_idx):
            if hit["ts"] + minutes * 60 > t0_ts:
                next_cross = hit
                break
    out["next_cross_after_min"] = _r((next_cross["ts"] + minutes * 60 - t0_ts) / 60, 1) if next_cross else None
    out["next_cross_side"] = next_cross["side"] if next_cross else None
    out["held_min"] = out["next_cross_after_min"] if next_cross else _r(max(0, close_ts - t0_ts) / 60, 1)

    if kind == "cross":
        out["state"] = row.get("state")
        predicted = row.get("minutes_to_cross")
        out["predicted_min"] = _r(predicted, 1) if predicted is not None else None
        if row.get("state") == APPROACHING:
            crossed = next_cross is not None and next_cross["side"] == side
            out["crossed"] = crossed
            limit = max(float(predicted or 0) * ON_TIME_FACTOR, 2 * minutes)
            out["on_time"] = bool(crossed and out["next_cross_after_min"] is not None and out["next_cross_after_min"] <= limit)
    else:
        out["quiet_runner"] = bool(row.get("quiet_runner"))
        out["trend"] = row.get("trend")
        out["score"] = row.get("score")
    return out


def _pct(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _group(name: str, label: str, rows: list[dict]) -> dict:
    called = [r for r in rows if r.get("side")]
    out = {"key": name, "label": label, "count": len(rows), "with_call": len(called)}
    for col in [f"after_{k}_pct" for k in AFTER_MIN] + ["close_pct"]:
        vals = [r[col] for r in called if r.get(col) is not None]
        out[f"right_{col}"] = _pct(sum(1 for v in vals if v > 0), len(vals))
        out[f"avg_{col}"] = _avg(vals)
    out["avg_best_pct"] = _avg([r["best_pct"] for r in called if r.get("best_pct") is not None])
    out["avg_worst_pct"] = _avg([r["worst_pct"] for r in called if r.get("worst_pct") is not None])
    held = [r["held_min"] for r in rows if r.get("held_min") is not None]
    out["avg_held_min"] = _avg(held)
    approaching = [r for r in rows if "crossed" in r]
    if approaching:
        out["crossed_pct"] = _pct(sum(1 for r in approaching if r["crossed"]), len(approaching))
        out["on_time_pct"] = _pct(sum(1 for r in approaching if r.get("on_time")), len(approaching))
    return out


def summarize(kind: str, rows: list[dict]) -> list[dict]:
    groups = [_group("all", "All stocks", rows)]
    if kind == "cross":
        groups.append(_group("approaching", "About to cross", [r for r in rows if r.get("state") == APPROACHING]))
        groups.append(_group("crossed", "Just crossed", [r for r in rows if r.get("state") != APPROACHING]))
    else:
        groups.append(_group("quiet", "Quiet runners", [r for r in rows if r.get("quiet_runner")]))
        groups.append(_group("strong", "Strong trend", [r for r in rows if r.get("trend")]))
        groups.append(_group("trending", "Trending score", [r for r in rows if r.get("score") == "TRENDING"]))
    return [g for g in groups if g["count"]]


@dataclass
class CheckStatus:
    running: bool = False
    run_id: int | None = None
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    errors: dict = field(default_factory=dict)


class AccuracyChecker:
    """One check at a time; the result is saved with the run (scan_history.save_check)."""

    def __init__(self) -> None:
        self.status = CheckStatus()
        self._task: asyncio.Task | None = None

    def snapshot(self) -> dict:
        s = self.status
        return {"running": s.running, "run_id": s.run_id, "done": s.done, "total": s.total, "failed": s.failed, "error": s.error}

    def stop(self, reason: str) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.status.running:
            self.status.running = False
            self.status.error = reason

    def start(self, run: dict, fetch: Fetch, *, save: Callable[[int, dict], None], job: Job | None = None, now: dt.datetime | None = None) -> dict:
        if self.status.running:
            raise RuntimeError(f"A check is already running (scan #{self.status.run_id}). Wait for it to finish.")
        if not day_closed(run["as_of"], now):
            raise ValueError("That day is not over yet. Check a scan after 15:30 on its day, when the whole day can be read.")
        rows = run.get("rows") or []
        if not rows:
            raise ValueError("That scan has no stocks to check.")
        self.status = CheckStatus(running=True, run_id=run["id"], total=len(rows))
        self._task = asyncio.get_running_loop().create_task(self._run(run, fetch, save, job))
        return self.snapshot()

    async def _run(self, run: dict, fetch: Fetch, save: Callable[[int, dict], None], job: Job | None) -> None:
        kind = run["kind"]
        minutes = int(run.get("minutes") or 5)
        meta = run.get("meta") or {}
        fast, slow = int(meta.get("sma_fast") or 9), int(meta.get("sma_slow") or 21)
        start, end = window(run["as_of"])
        results: list[dict] = []
        ok = 0
        gate = asyncio.Semaphore(CONCURRENCY)
        abort = asyncio.Event()

        async def one(row: dict, delay: float) -> None:
            nonlocal ok
            await asyncio.sleep(delay)
            if abort.is_set():
                return
            async with gate:
                if abort.is_set():
                    return
                symbol = str(row.get("symbol") or "")
                try:
                    if job is not None:
                        res = await job(symbol, start, end, kind, row, run["as_of"], minutes, fast, slow)
                    else:
                        frame = await fetch(symbol, start, end)
                        res = await asyncio.to_thread(evaluate, kind, row, frame, run["as_of"], minutes, fast, slow)
                    ok += 1
                    if res is not None:
                        results.append(res)
                except Exception as exc:  # noqa: BLE001
                    text = (str(exc).strip() or type(exc).__name__)[:200]
                    self.status.errors[text] = self.status.errors.get(text, 0) + 1
                    self.status.failed += 1
                    if ok == 0 and self.status.failed >= GIVE_UP_AFTER:
                        abort.set()
                finally:
                    self.status.done += 1

        try:
            await asyncio.gather(*(one(r, i * CALL_GAP_SEC) for i, r in enumerate(run.get("rows") or [])))
            errors = self.status.errors
            if ok == 0 and errors:
                self.status.error = max(errors, key=errors.get)
                return
            order = {r.get("symbol"): i for i, r in enumerate(run.get("rows") or [])}
            results.sort(key=lambda r: order.get(r.get("symbol"), 10**6))
            result = {
                "run_id": run["id"],
                "kind": kind,
                "as_of": run["as_of"],
                "minutes": minutes,
                "checked": len(results),
                "failed": self.status.failed,
                "error": f"{self.status.failed} stock(s) could not be read ({max(errors, key=errors.get)})" if errors else None,
                "summary": summarize(kind, results),
                "rows": results,
            }
            await asyncio.to_thread(save, run["id"], result)
            self.status.error = result["error"]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("scan accuracy check failed")
            self.status.error = (str(exc) or type(exc).__name__)[:200]
        finally:
            self.status.running = False
