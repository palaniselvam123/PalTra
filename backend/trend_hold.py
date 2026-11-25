"""Trend hold test: does a strong trend / strong phase keep going to the end of the day? (read-only research, no order
path, nothing armed; nothing in the strategy reads it; the Scalp page's "Trend hold" tab,
`frontend/src/components/Scalp/TrendHold.tsx`).

The owner's question: "when Trend quality shows a stock strong (7 of 7, 6 of 7 checks) or in a strong phase, how far
does it go by the end of the day?" Like the Strategy lab, it reads past days of the picked stocks on the cached Groww
1-minute candles (`strategy_lab.load_tape`) with a chosen bot's SMA periods and candle size:

* **Reads** (`day_reads`): from `first` to `last` every `every_min` minutes, the Trend quality card's read on the
  candles closed before that minute (the same arithmetic as `auto_arm.strong_read` / `chop_scan.analyze`: the 7
  strong-trend checks of `trend_strength.read`, or `ha_trend.checks` on Heikin Ashi candles, and the phase now of
  `trend_phases` / `ha_trend`). SMAs, ATR and Heikin Ashi candles only look back, so the day's candles are enriched
  once and each minute reads the candles closed by then.
* **Picked** (`qualifies`): `kind` TREND = at least `min_checks` of the 7 checks pass for the SMAs' side (with
  `strong_only` the card must also read Strong: stack / VWAP / slope pass); PHASE = the phase now is BULL / BEAR (not
  "too early") for at least `phase_min` candles; EITHER / BOTH = one or both, same side. `side` BOTH / UP / DOWN; the
  last price must be inside `price_min`..`price_max` (0 = no bound). Each stock once a day, at its first qualifying read;
  at most `max_day` a day (earliest first, then most checks).
* **Followed** (`follow`): entry at the open of the 1-minute candle starting at the read minute, the trend's way, `qty`
  shares; held to `square_off` (out at that minute's open): points, %, ₹ before and after charges, best / worst and when.
  The read keeps being taken every `every_min` minutes: `lost_at` = the first read that no longer qualifies (or turned),
  `held_to_end` when none did, and what closing there instead would have made (`lost_pts`). `curve` is the signed % from
  the entry at every minute, for the average line; `GET /api/trend-hold/chart` gives one pick's price by minute (or by
  second where the bots recorded them, `tick_store`).

Before charges unless stated; fills at minute opens, no slippage.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import statistics
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pandas as pd

from charges import calculate_charges

log = logging.getLogger(__name__)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KINDS = ("TREND", "PHASE", "EITHER", "BOTH")
SIDES = ("BOTH", "UP", "DOWN")
SIZES = (1, 3, 5, 10, 15)
STEPS = (1, 2, 3, 5, 10, 15, 30)
MAX_STOCKS = 250
MIN_DAYS, MAX_DAYS = 1, 30
MAX_DAY = 100
SESSION_OPEN = dt.time(9, 15)


@dataclass(frozen=True)
class Rule:
    kind: str = "TREND"
    min_checks: int = 6
    strong_only: bool = True
    phase_min: int = 5
    side: str = "BOTH"
    minutes: int = 5
    sma_fast: int = 9
    sma_slow: int = 21
    candles: str = "NORMAL"
    first: dt.time = dt.time(10, 0)
    last: dt.time = dt.time(14, 0)
    every_min: int = 5
    square_off: dt.time = dt.time(15, 15)
    qty: int = 100
    price_min: float = 0.0
    price_max: float = 0.0
    max_day: int = 20
    bot: int = 1

    def clean(self) -> "Rule":
        lo, hi = float(self.price_min or 0), float(self.price_max or 0)
        if lo and hi and lo > hi:
            lo, hi = hi, lo
        first, last = min(self.first, self.last), max(self.first, self.last)
        return replace(
            self,
            kind=self.kind if self.kind in KINDS else "TREND",
            side=self.side if self.side in SIDES else "BOTH",
            min_checks=max(1, min(7, int(self.min_checks))),
            phase_min=max(1, min(60, int(self.phase_min))),
            minutes=self.minutes if self.minutes in SIZES else 5,
            candles="HA" if self.candles == "HA" else "NORMAL",
            every_min=self.every_min if self.every_min in STEPS else 5,
            qty=max(1, int(self.qty)),
            price_min=max(0.0, lo), price_max=max(0.0, hi),
            max_day=max(1, min(MAX_DAY, int(self.max_day))),
            first=first, last=min(last, self.square_off),
        )

    def view(self) -> dict:
        out = {k: getattr(self, k) for k in self.__dataclass_fields__}
        for k in ("first", "last", "square_off"):
            out[k] = getattr(self, k).strftime("%H:%M")
        return out


def _ts(day: dt.date, t: dt.time) -> int:
    return int(dt.datetime.combine(day, t, tzinfo=IST).timestamp())


def _hhmm(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def read_times(rule: Rule, start: dt.time, end: dt.time) -> list[dt.time]:
    """Every `every_min` minutes from `start` to `end` (both kept when on the step)."""
    out, t = [], dt.datetime.combine(dt.date(2000, 1, 1), start)
    stop = dt.datetime.combine(dt.date(2000, 1, 1), end)
    while t <= stop:
        out.append(t.time())
        t += dt.timedelta(minutes=rule.every_min)
    return out


def day_reads(frame: pd.DataFrame, day: dt.date, times: list[dt.time], rule: Rule) -> dict[str, dict]:
    """The Trend quality read at each of `times` on `day` (candles closed before it), keyed HH:MM. A minute with too
    few candles, or none of that day yet, is left out."""
    import ha_trend
    import trend_phases
    import trend_strength
    from candles import resample
    from chop_scan import WARMUP_DAYS, _vwap_today, ha_bars
    from indicators import enrich

    if frame is None or frame.empty:
        return {}
    ts_all = frame["ts"].astype("int64")
    start = _ts(day - dt.timedelta(days=WARMUP_DAYS), dt.time(0, 0))
    end = _ts(day + dt.timedelta(days=1), dt.time(0, 0))
    part = frame[(ts_all >= start) & (ts_all < end)].sort_values("ts").reset_index(drop=True)
    today_start = _ts(day, SESSION_OPEN)
    if part.empty or int(part["ts"].iloc[-1]) < today_start:
        return {}
    bars = resample(part, rule.minutes)
    if bars is None or bars.empty:
        return {}
    bars = bars.reset_index(drop=True)
    real_close = bars["close"].to_numpy(dtype="float64")
    shaped = ha_bars(bars) if rule.candles == "HA" else bars
    enriched = enrich(shaped.drop(columns=["volume"], errors="ignore"), rule.sma_fast, rule.sma_slow)
    ends = bars["ts"].to_numpy(dtype="int64") + rule.minutes * 60
    tape_ts = part["ts"].to_numpy(dtype="int64")
    out: dict[str, dict] = {}
    for t in times:
        now = dt.datetime.combine(day, t, tzinfo=IST)
        cut = int(now.timestamp())
        k = int(np.searchsorted(ends, cut, side="right"))
        if k < max(int(rule.sma_slow), 2) + 1:
            continue
        view = enriched.iloc[:k]
        if int(view["ts"].iloc[-1]) < today_start:
            continue
        tape = part.iloc[: int(np.searchsorted(tape_ts, cut, side="left"))]
        if tape.empty:
            continue
        vwap = _vwap_today(tape, now)
        if rule.candles == "HA":
            read = ha_trend.checks(view, vwap, float(real_close[k - 1]))
            phase = ha_trend.summary(ha_trend.phases(view, today_start))
        else:
            read = trend_strength.read(view, vwap, now=now, tape=tape)
            phase = trend_phases.summary(trend_phases.phases(view, today_start))
        checks = {key: bool(v) for key, v in (read.get("trend_checks") or {}).items() if isinstance(v, bool)}
        out[t.strftime("%H:%M")] = {
            "price": float(tape["close"].iloc[-1]),
            "trend": read.get("trend"),
            "trend_side": read.get("trend_side"),
            "trend_mode": read.get("trend_mode"),
            "passed": sum(checks.values()),
            "of": len(checks) or 7,
            "missed": [key for key, ok in checks.items() if not ok],
            "phase": phase.get("phase"),
            "phase_candles": int(phase.get("phase_candles") or 0),
            "phase_early": bool(phase.get("phase_early")),
            "phase_since": _hhmm(phase["phase_since_ts"]) if phase.get("phase_since_ts") else None,
        }
    return out


def qualifies(read: dict | None, rule: Rule) -> str | None:
    """'UP' / 'DOWN' when the read passes the rule, else None."""
    if not read:
        return None
    trend_side = {"BULLISH": "UP", "BEARISH": "DOWN"}.get(read.get("trend_side") or "")
    trend_ok = bool(trend_side) and read["passed"] >= rule.min_checks and (
        not rule.strong_only or read.get("trend") in ("STRONG_BULLISH", "STRONG_BEARISH"))
    phase_side = {"BULL": "UP", "BEAR": "DOWN"}.get(read.get("phase") or "")
    phase_ok = bool(phase_side) and not read.get("phase_early") and read["phase_candles"] >= rule.phase_min
    if rule.kind == "TREND":
        side = trend_side if trend_ok else None
    elif rule.kind == "PHASE":
        side = phase_side if phase_ok else None
    elif rule.kind == "BOTH":
        side = trend_side if trend_ok and phase_ok and trend_side == phase_side else None
    else:  # EITHER
        side = trend_side if trend_ok else (phase_side if phase_ok else None)
    if side and rule.side != "BOTH" and side != rule.side:
        return None
    return side


def _why(read: dict, rule: Rule) -> str:
    parts = []
    if rule.kind != "PHASE":
        text = f"{read['passed']} of {read['of']} checks ({(read.get('trend_side') or '').lower()})"
        if read.get("trend"):
            text += ", Strong"
        if read.get("trend_mode") == "MORNING":
            text += ", morning rules"
        if read.get("missed"):
            text += "; missed: " + ", ".join(read["missed"])
        parts.append(text)
    if rule.kind != "TREND" and read.get("phase"):
        parts.append(f"phase {read['phase']} {read['phase_candles']} candles" + (f" since {read['phase_since']}" if read.get("phase_since") else ""))
    return " · ".join(parts)


def follow(tape: pd.DataFrame, day: dt.date, at: str, side: str, rule: Rule, reads: dict[str, dict]) -> dict | None:
    """Enter at the open of the minute starting at `at`, follow to the square-off; None without candles."""
    hh, mm = at.split(":")
    entry_ts = _ts(day, dt.time(int(hh), int(mm)))
    end_ts = _ts(day, rule.square_off)
    ts = tape["ts"].to_numpy(dtype="int64")
    mask = (ts >= entry_ts) & (ts < end_ts)
    part = tape[mask]
    if part.empty:
        return None
    sign = 1 if side == "UP" else -1
    o = part["open"].to_numpy(dtype="float64")
    h = part["high"].to_numpy(dtype="float64")
    lo = part["low"].to_numpy(dtype="float64")
    c = part["close"].to_numpy(dtype="float64")
    pts = part["ts"].to_numpy(dtype="int64")
    entry = float(o[0]) if np.isfinite(o[0]) and o[0] > 0 else float(c[0])
    after = tape[(ts >= end_ts) & (ts < _ts(day, dt.time(15, 31)))]
    exit_px = float(after["open"].iloc[0]) if len(after) else float(c[-1])
    exit_at = _hhmm(int(after["ts"].iloc[0])) if len(after) else _hhmm(int(pts[-1]) + 60)
    fav = (h - entry) * sign if sign == 1 else (entry - lo)
    adv = (lo - entry) if sign == 1 else (entry - h)
    bi, wi = int(np.argmax(fav)), int(np.argmin(adv))
    points = (exit_px - entry) * sign
    qty = rule.qty
    buy, sell = (entry, exit_px) if sign == 1 else (exit_px, entry)
    fees = calculate_charges(buy, sell, qty)["total_charges"]
    # The read after the entry: the first one that no longer passes (or turned the other way).
    lost_at, strong_reads, after_reads = None, 0, 0
    for key in sorted(k for k in reads if at < k < rule.square_off.strftime("%H:%M")):
        after_reads += 1
        if qualifies(reads[key], rule) == side:
            strong_reads += 1
            continue
        lost_at = key
        break
    lost_pts = None
    if lost_at:
        lh, lm = lost_at.split(":")
        at_ts = _ts(day, dt.time(int(lh), int(lm)))
        hit = part[part["ts"].astype("int64") >= at_ts]
        if len(hit):
            lost_pts = round((float(hit["open"].iloc[0]) - entry) * sign, 2)
    curve = [round(float(v), 3) for v in (c - entry) * sign / entry * 100]
    return {
        "entry": round(entry, 2), "exit": round(exit_px, 2), "exit_at": exit_at,
        "points": round(points, 2), "pct": round(points / entry * 100, 3),
        "pnl": round(points * qty, 2), "charges": round(fees, 2), "net": round(points * qty - fees, 2),
        "best_pts": round(float(fav[bi]), 2), "best_pct": round(float(fav[bi]) / entry * 100, 3), "best_at": _hhmm(int(pts[bi])),
        "worst_pts": round(float(adv[wi]), 2), "worst_pct": round(float(adv[wi]) / entry * 100, 3), "worst_at": _hhmm(int(pts[wi])),
        "best_rs": round(float(fav[bi]) * qty, 2), "worst_rs": round(float(adv[wi]) * qty, 2),
        "lost_at": lost_at, "held_to_end": lost_at is None, "strong_reads": strong_reads, "after_reads": after_reads,
        "lost_pts": lost_pts, "lost_pnl": round(lost_pts * qty, 2) if lost_pts is not None else None,
        "minutes": int(len(part)), "curve": curve,
    }


def analyze_stock(symbol: str, tape: pd.DataFrame, days: list[dt.date], rule: Rule) -> list[dict]:
    """Every qualifying (day, first read) of one stock with its outcome: candidates, before the per-day cap."""
    out: list[dict] = []
    if tape is None or tape.empty:
        return out
    tape = tape.sort_values("ts").reset_index(drop=True)
    times = read_times(rule, rule.first, rule.square_off)
    last_key = rule.last.strftime("%H:%M")
    for day in days:
        try:
            reads = day_reads(tape, day, times, rule)
        except Exception:  # noqa: BLE001 - one bad day never stops the stock
            log.exception("trend hold read failed for %s %s", symbol, day)
            continue
        for key in sorted(reads):
            if key > last_key:
                break
            read = reads[key]
            side = qualifies(read, rule)
            if not side:
                continue
            price = read["price"]
            if (rule.price_min and price < rule.price_min) or (rule.price_max and price > rule.price_max):
                continue
            got = follow(tape, day, key, side, rule, reads)
            if got is None:
                break
            out.append({"symbol": symbol, "day": day.isoformat(), "at": key, "side": "LONG" if side == "UP" else "SHORT",
                        "price": round(price, 2), "passed": read["passed"], "of": read["of"], "trend": read.get("trend"),
                        "phase": read.get("phase"), "phase_candles": read["phase_candles"], "why": _why(read, rule), **got})
            break
    return out


def cap_days(rows: list[dict], max_day: int) -> list[dict]:
    """At most `max_day` picks a day: the earliest first, then the most checks passed."""
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["day"], []).append(r)
    out = []
    for day in sorted(by):
        out.extend(sorted(by[day], key=lambda r: (r["at"], -r["passed"], r["symbol"]))[:max_day])
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.mean(xs), 3) if xs else None


def _rs(v: float) -> str:
    return f"{'−' if v < 0 else ''}₹{abs(v):,.0f}"


def summarize(rows: list[dict], rule: Rule) -> dict:
    n = len(rows)
    if not n:
        return {"trades": 0, "answer": ["No stock passed the rule on these days. Try fewer checks, a longer window or more stocks."]}
    held = [r for r in rows if r["held_to_end"]]
    wins = [r for r in rows if r["pnl"] > 0]
    total, net = sum(r["pnl"] for r in rows), sum(r["net"] for r in rows)
    lost_total = sum((r["lost_pnl"] if r["lost_pnl"] is not None else r["pnl"]) for r in rows)
    by_day = []
    run = 0.0
    for day in sorted({r["day"] for r in rows}):
        part = [r for r in rows if r["day"] == day]
        pnl = sum(r["pnl"] for r in part)
        run += pnl
        by_day.append({"day": day, "trades": len(part), "pnl": round(pnl, 2), "net": round(sum(r["net"] for r in part), 2),
                       "held": sum(1 for r in part if r["held_to_end"]), "wins": sum(1 for r in part if r["pnl"] > 0),
                       "running": round(run, 2)})
    by_checks = []
    for k in sorted({r["passed"] for r in rows}, reverse=True):
        part = [r for r in rows if r["passed"] == k]
        by_checks.append({"passed": k, "trades": len(part), "win_pct": round(sum(1 for r in part if r["pnl"] > 0) / len(part) * 100, 1),
                          "held_pct": round(sum(1 for r in part if r["held_to_end"]) / len(part) * 100, 1),
                          "avg_pct": _mean([r["pct"] for r in part]), "avg_best_pct": _mean([r["best_pct"] for r in part]),
                          "pnl": round(sum(r["pnl"] for r in part), 2)})
    what = {"TREND": f"{rule.min_checks}+ of 7 checks", "PHASE": f"a strong phase ({rule.phase_min}+ candles)",
            "EITHER": "a strong trend or a strong phase", "BOTH": "a strong trend and a strong phase"}[rule.kind]
    answer = [
        f"{n} picks on {len(by_day)} day(s) read {what}. {len(held)} ({len(held) / n * 100:.0f}%) still read it at every check "
        f"to {rule.square_off.strftime('%H:%M')}.",
        f"Held to {rule.square_off.strftime('%H:%M')}: {len(wins)} of {n} ended in profit ({len(wins) / n * 100:.0f}%), "
        f"average {statistics.mean(r['pct'] for r in rows):+.2f}% a trade, {_rs(total)} before charges ({_rs(net)} after) at {rule.qty} shares.",
        f"On the way the best was {statistics.mean(r['best_pct'] for r in rows):+.2f}% on average and the worst "
        f"{statistics.mean(r['worst_pct'] for r in rows):+.2f}%.",
        f"Closing when the reading was lost instead would have made {_rs(lost_total)} before charges.",
    ]
    if len(by_day) < 5:
        answer.append("Fewer than 5 days: read it as a hint, not proof.")
    return {
        "trades": n, "days": len(by_day), "stocks": len({r["symbol"] for r in rows}),
        "held": len(held), "held_pct": round(len(held) / n * 100, 1), "wins": len(wins), "win_pct": round(len(wins) / n * 100, 1),
        "pnl": round(total, 2), "net": round(net, 2), "lost_pnl": round(lost_total, 2),
        "avg_pct": _mean([r["pct"] for r in rows]), "avg_best_pct": _mean([r["best_pct"] for r in rows]),
        "avg_worst_pct": _mean([r["worst_pct"] for r in rows]),
        "by_day": by_day, "by_checks": by_checks, "answer": answer,
    }


def report_path() -> Path:
    explicit = os.environ.get("TREND_HOLD_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("trend_hold.json")
    return Path("trend_hold.json")


def last_trading_days(today: dt.date, count: int) -> list[dt.date]:
    """The last `count` weekdays before today (a holiday simply has no candles)."""
    out: list[dt.date] = []
    day = today
    while len(out) < count:
        day -= dt.timedelta(days=1)
        if day.weekday() < 5:
            out.append(day)
    return sorted(out)


class TrendHold:
    """One test at a time: read each stock's days, keep the picks, summarize. Memory plus disk (last run)."""

    def __init__(self) -> None:
        self.status = "IDLE"
        self.done = self.total = 0
        self.errors: list[dict] = []
        self.params: dict = {}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.report: dict | None = None
        self.rows: list[dict] = []
        self._task: asyncio.Task | None = None
        self._stop = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(report_path().read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and raw.get("report"):
            self.report, self.rows = raw["report"], raw.get("rows") or []
            self.params, self.finished_at = raw.get("params") or {}, raw.get("finished_at")
            self.status = "DONE"

    def _save(self) -> None:
        try:
            report_path().write_text(json.dumps({"report": self.report, "rows": self.rows, "params": self.params,
                                                 "finished_at": self.finished_at}, default=str))
        except OSError as exc:
            log.warning("could not save the trend hold test: %s", exc)

    @property
    def running(self) -> bool:
        return self.status == "RUNNING"

    def view(self, with_rows: bool = True) -> dict:
        return {"status": self.status, "done": self.done, "total": self.total, "errors": self.errors[-20:],
                "params": self.params, "started_at": self.started_at, "finished_at": self.finished_at,
                "report": self.report, "rows": self.rows if with_rows else []}

    def start(self, symbols: list[str], fetch, days: list[dt.date], rule: Rule, wait=None) -> dict:
        if self.running:
            raise RuntimeError("A trend hold test is already running.")
        names = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))[:MAX_STOCKS]
        if not names:
            raise ValueError("Pick at least one stock.")
        if not days:
            raise ValueError("Pick at least one day.")
        rule = rule.clean()
        self.status, self.done, self.total = "RUNNING", 0, len(names)
        self.errors, self._stop = [], False
        self.params = {"symbols": len(names), "days": [d.isoformat() for d in days], **rule.view()}
        self.started_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        self.finished_at = None
        self._task = asyncio.create_task(self._run(names, fetch, days, rule, wait))
        return self.view(with_rows=False)

    def stop(self) -> dict:
        self._stop = True
        return self.view(with_rows=False)

    async def _run(self, names: list[str], fetch, days: list[dt.date], rule: Rule, wait) -> None:
        from chop_scan import WARMUP_DAYS
        from strategy_lab import load_tape

        today = dt.datetime.now(IST).date()
        first, last = days[0] - dt.timedelta(days=WARMUP_DAYS), days[-1]
        rows: list[dict] = []
        finished = False
        try:
            for name in names:
                if self._stop:
                    break
                try:
                    if wait is not None:
                        await wait()
                    tape = await load_tape(name, first, last, fetch, today=today)
                    rows.extend(await asyncio.to_thread(analyze_stock, name, tape, days, rule))
                except Exception as exc:  # noqa: BLE001 - one stock never stops the test
                    self.errors.append({"symbol": name, "error": str(exc)[:200]})
                self.done += 1
                await asyncio.sleep(0)
            self.rows = cap_days(rows, rule.max_day)
            self.report = await asyncio.to_thread(summarize, self.rows, rule)
            self.report["stopped"] = self._stop
            finished = True
        except Exception as exc:  # noqa: BLE001
            log.exception("trend hold test failed")
            self.errors.append({"symbol": "", "error": str(exc)[:200]})
            self.status = "FAILED"
        finally:
            self.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
            if finished:
                try:
                    await asyncio.to_thread(self._save)
                finally:
                    self.status = "DONE"


def chart_points(tape: pd.DataFrame, day: dt.date, start: str = "09:15", end: str = "15:30") -> list[list[float]]:
    """[[ts, open, high, low, close], ...] of the day's 1-minute candles from `start` to `end`."""
    sh, sm = start.split(":")
    eh, em = end.split(":")
    lo, hi = _ts(day, dt.time(int(sh), int(sm))), _ts(day, dt.time(int(eh), int(em)))
    ts = tape["ts"].astype("int64")
    part = tape[(ts >= lo) & (ts < hi)].sort_values("ts")
    return [[int(r.ts), float(r.open), float(r.high), float(r.low), float(r.close)] for r in part.itertuples()]


_TEST: TrendHold | None = None


def test() -> TrendHold:
    global _TEST
    if _TEST is None:
        _TEST = TrendHold()
    return _TEST
