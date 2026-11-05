"""Sideways test (regime_study.py; read-only research, no order path, nothing in the strategy reads it).

The owner's question: "can the bots tell a sideways stock from a trending one early enough to route it?" A label
only helps if it holds for the next stretch, so this measures it on past days instead of guessing. For each stock
and each past trading day, at every check time (10:00 to 14:30 IST, hourly), it reads the signals people use to
spot chop on the WINDOW minutes just finished, and what the stock did in the WINDOW minutes after:

Signals (the window just closed, on the bot's candle size, closed candles only):
* ``crosses``    SMA fast / slow crosses
* ``turns``      SMA fast turns (its slope over 2 candles changing sign, small wiggles under TURN_MIN_ATR ignored)
* ``adx``        Wilder ADX 14 on the last closed candle (`indicators.enrich`)
* ``efficiency`` |net move| / sum of |close-to-close moves| (1 = a straight line, 0 = back where it started)
* ``overlap``    average overlap of each candle's range with the one before (intersection / union, 0-1)
* ``gap``        |SMA fast − SMA slow| / SMA slow in % at the check time

Outcome (the next WINDOW minutes):
* ``trended``    efficiency ≥ 0.5 and a move of at least 1 ATR (the same rule as trend_phases.py)
* ``next_crosses``, ``move_atr`` (|net move| in ATRs) and ``cross_pnl_pct``: what simply following the SMA side
  would have made, in % of the price, before charges (hold the side the SMAs point to at the check time, flip on
  each cross, close at the end of the window). A rough stand-in for the bot, not its exact settings.

`summarize` buckets each signal and reports, per bucket, how often the next stretch trended, the average next
crosses, move and cross-follow P&L, against the base rate of all samples, plus how far apart the buckets are
(`spread`): a signal whose buckets all trend about as often as the base rate does not predict anything.
`RegimeStudy` downloads each stock's 1-minute Groww candles once (one stock at a time, after the bots' rate limit
clears) and keeps the last report in `regime_study.json` next to the SMA DB, so it survives a restart.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import indicators
from candles import resample

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

CHECK_TIMES = tuple(dt.time(h, m) for h, m in ((10, 0), (11, 0), (12, 0), (13, 0), (14, 0), (14, 30)))
WINDOWS = (30, 60, 90)
SIZES = (1, 3, 5, 10, 15)
MIN_DAYS, MAX_DAYS = 3, 30
MAX_STOCKS = 200
SESSION_END = dt.time(15, 30)
TREND_EFFICIENCY = 0.5
TREND_ATR = 1.0
TURN_MIN_ATR = 0.05

BUCKETS: dict[str, list[tuple[str, float, float]]] = {
    "crosses": [("0", 0, 0.5), ("1", 0.5, 1.5), ("2", 1.5, 2.5), ("3+", 2.5, 1e9)],
    "turns": [("0", 0, 0.5), ("1", 0.5, 1.5), ("2", 1.5, 2.5), ("3+", 2.5, 1e9)],
    "adx": [("under 15", -1, 15), ("15-20", 15, 20), ("20-25", 20, 25), ("25-30", 25, 30), ("30+", 30, 1e9)],
    "efficiency": [("under 0.2", -1, 0.2), ("0.2-0.4", 0.2, 0.4), ("0.4-0.6", 0.4, 0.6), ("0.6+", 0.6, 1e9)],
    "overlap": [("under 0.3", -1, 0.3), ("0.3-0.5", 0.3, 0.5), ("0.5-0.7", 0.5, 0.7), ("0.7+", 0.7, 1e9)],
    "gap": [("under 0.05%", -1, 0.05), ("0.05-0.1%", 0.05, 0.1), ("0.1-0.2%", 0.1, 0.2), ("0.2%+", 0.2, 1e9)],
}
LABELS = {
    "crosses": "SMA crosses in the last window",
    "turns": "SMA fast turns (V / U) in the last window",
    "adx": "ADX 14 at the check time",
    "efficiency": "How straight the last window moved (efficiency)",
    "overlap": "Candle overlap in the last window",
    "gap": "SMA gap at the check time",
}


def _efficiency(close: np.ndarray) -> float | None:
    if len(close) < 2:
        return None
    path = float(np.abs(np.diff(close)).sum())
    return abs(float(close[-1] - close[0])) / path if path > 0 else 0.0


def _crosses(diff: np.ndarray) -> int:
    signs = np.sign(diff[~np.isnan(diff)])
    signs = signs[signs != 0]
    return int((signs[1:] != signs[:-1]).sum()) if len(signs) > 1 else 0


def _turns(fast: np.ndarray, atr: float) -> int:
    if len(fast) < 4 or not atr or atr <= 0:
        return 0
    slope = fast[2:] - fast[:-2]
    slope = slope[~np.isnan(slope)]
    signs = [np.sign(s) for s in slope if abs(s) >= TURN_MIN_ATR * atr]
    return int(sum(1 for a, b in zip(signs, signs[1:]) if a != b))


def _overlap(high: np.ndarray, low: np.ndarray) -> float | None:
    if len(high) < 2:
        return None
    vals = []
    for i in range(1, len(high)):
        inter = min(high[i], high[i - 1]) - max(low[i], low[i - 1])
        union = max(high[i], high[i - 1]) - min(low[i], low[i - 1])
        vals.append(max(0.0, inter) / union if union > 0 else 1.0)
    return float(np.mean(vals))


def _cross_pnl(close: np.ndarray, diff: np.ndarray, start_close: float, start_side: float) -> float:
    """% made by holding the SMA side from the check time, flipping on each cross, to the end of the window."""
    side = start_side
    prev = start_close
    pnl = 0.0
    for c, d in zip(close, diff):
        pnl += side * (c - prev)
        prev = c
        if not np.isnan(d) and d != 0 and np.sign(d) != side:
            side = np.sign(d)
    return pnl / start_close * 100 if start_close else 0.0


def analyze_stock(
    symbol: str,
    tape: pd.DataFrame | None,
    days: list[dt.date],
    minutes: int = 5,
    window: int = 60,
    sma_fast: int = 9,
    sma_slow: int = 21,
) -> list[dict]:
    """Every (day, check time) sample of one stock: the signals of the last `window` minutes and the next ones."""
    if tape is None or getattr(tape, "empty", True) or len(tape) < sma_slow + 5:
        return []
    frame = resample(tape, minutes) if minutes > 1 else tape.copy()
    if frame is None or len(frame) < sma_slow + 5:
        return []
    rich = indicators.enrich(frame.reset_index(drop=True), sma_fast, sma_slow, 14)
    ts = rich["ts"].astype("int64").to_numpy()
    close = rich["close"].astype(float).to_numpy()
    high = rich["high"].astype(float).to_numpy()
    low = rich["low"].astype(float).to_numpy()
    fast = rich["sma_fast"].astype(float).to_numpy()
    slow = rich["sma_slow"].astype(float).to_numpy()
    atr = rich["atr_14"].astype(float).to_numpy()
    adx = rich["adx_14"].astype(float).to_numpy() if "adx_14" in rich else np.full(len(rich), np.nan)
    diff = fast - slow
    step = minutes * 60
    out: list[dict] = []
    for day in days:
        for at in CHECK_TIMES:
            t0 = dt.datetime.combine(day, at, tzinfo=IST)
            if (t0 + dt.timedelta(minutes=window)).time() > SESSION_END:
                continue
            t = int(t0.timestamp())
            w = window * 60
            # Candles closed by the check time inside the past window, and those inside the next window.
            past = np.where((ts >= t - w) & (ts + step <= t))[0]
            future = np.where((ts >= t) & (ts + step <= t + w))[0]
            if len(past) < 2 or len(future) < 1:
                continue
            last = int(past[-1])
            a = atr[last]
            if np.isnan(a) or a <= 0 or np.isnan(fast[last]) or np.isnan(slow[last]) or slow[last] == 0:
                continue
            first = max(int(past[0]) - 1, 0)
            seg_close = close[future]
            move = abs(float(seg_close[-1] - close[last]))
            fut_eff = _efficiency(np.concatenate([[close[last]], seg_close])) or 0.0
            out.append(
                {
                    "symbol": symbol,
                    "day": day.isoformat(),
                    "at": at.strftime("%H:%M"),
                    "crosses": _crosses(diff[first : last + 1]),
                    "turns": _turns(fast[first : last + 1], a),
                    "adx": None if np.isnan(adx[last]) else round(float(adx[last]), 2),
                    "efficiency": round(_efficiency(close[first : last + 1]) or 0.0, 3),
                    "overlap": None if (ov := _overlap(high[past], low[past])) is None else round(ov, 3),
                    "gap": round(abs(float(diff[last])) / float(slow[last]) * 100, 4),
                    "trended": bool(fut_eff >= TREND_EFFICIENCY and move / a >= TREND_ATR),
                    "next_crosses": _crosses(diff[[last, *future.tolist()]]),
                    "move_atr": round(move / a, 3),
                    "cross_pnl_pct": round(_cross_pnl(seg_close, diff[future], close[last], float(np.sign(diff[last]) or 1.0)), 4),
                }
            )
    return out


def _bucket_rows(samples: list[dict], key: str, base: float) -> list[dict]:
    rows = []
    for label, lo, hi in BUCKETS[key]:
        picked = [s for s in samples if s.get(key) is not None and lo <= float(s[key]) < hi]
        n = len(picked)
        if not n:
            rows.append({"bucket": label, "samples": 0})
            continue
        trended = sum(1 for s in picked if s["trended"]) / n * 100
        rows.append(
            {
                "bucket": label,
                "samples": n,
                "trended_pct": round(trended, 1),
                "vs_base": round(trended - base, 1),
                "next_crosses": round(float(np.mean([s["next_crosses"] for s in picked])), 2),
                "move_atr": round(float(np.mean([s["move_atr"] for s in picked])), 2),
                "cross_pnl_pct": round(float(np.mean([s["cross_pnl_pct"] for s in picked])), 3),
                "win_pct": round(sum(1 for s in picked if s["cross_pnl_pct"] > 0) / n * 100, 1),
            }
        )
    return rows


def summarize(samples: list[dict], min_bucket: int = 30) -> dict:
    """Per signal: its buckets against the base rate, and how far apart they are (`spread`, in points of trended %)."""
    n = len(samples)
    if not n:
        return {"samples": 0, "signals": [], "best": None}
    base = sum(1 for s in samples if s["trended"]) / n * 100
    signals = []
    for key in BUCKETS:
        rows = _bucket_rows(samples, key, base)
        usable = [r for r in rows if r["samples"] >= min_bucket]
        spread = (max(r["trended_pct"] for r in usable) - min(r["trended_pct"] for r in usable)) if len(usable) >= 2 else 0.0
        pnl_spread = (
            max(r["cross_pnl_pct"] for r in usable) - min(r["cross_pnl_pct"] for r in usable) if len(usable) >= 2 else 0.0
        )
        signals.append(
            {
                "key": key,
                "label": LABELS[key],
                "buckets": rows,
                "spread": round(spread, 1),
                "pnl_spread": round(pnl_spread, 3),
                "verdict": verdict(spread),
            }
        )
    signals.sort(key=lambda s: s["spread"], reverse=True)
    return {
        "samples": n,
        "stocks": len({s["symbol"] for s in samples}),
        "days": len({s["day"] for s in samples}),
        "base_trended_pct": round(base, 1),
        "base_next_crosses": round(float(np.mean([s["next_crosses"] for s in samples])), 2),
        "base_cross_pnl_pct": round(float(np.mean([s["cross_pnl_pct"] for s in samples])), 3),
        "min_bucket": min_bucket,
        "signals": signals,
        "best": signals[0]["key"] if signals and signals[0]["spread"] >= 10 else None,
    }


def verdict(spread: float) -> str:
    """Plain words for how much a signal separates trending from sideways next windows."""
    if spread >= 20:
        return "strong: its buckets lead to very different next hours"
    if spread >= 10:
        return "some: worth testing as a filter"
    return "weak: about the same as guessing"


def trading_days(end: dt.date, count: int) -> list[dt.date]:
    """The last `count` weekdays before `end` (no holiday list: a holiday simply has no candles)."""
    out: list[dt.date] = []
    day = end
    while len(out) < count:
        day -= dt.timedelta(days=1)
        if day.weekday() < 5:
            out.append(day)
    return sorted(out)


def report_path() -> Path:
    explicit = os.environ.get("REGIME_STUDY_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("regime_study.json")
    return Path("regime_study.json")


class RegimeStudy:
    """One study at a time: download each stock, read its samples, summarize. Memory plus the last report on disk."""

    def __init__(self) -> None:
        self.status = "IDLE"
        self.done = 0
        self.total = 0
        self.errors: list[dict] = []
        self.params: dict = {}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.report: dict | None = None
        self._task: asyncio.Task | None = None
        self._stop = False
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(report_path().read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and raw.get("report"):
            self.report = raw.get("report")
            self.params = raw.get("params") or {}
            self.finished_at = raw.get("finished_at")
            self.status = "DONE"

    def _save(self) -> None:
        try:
            report_path().write_text(json.dumps({"report": self.report, "params": self.params, "finished_at": self.finished_at}))
        except OSError as exc:
            log.warning("could not save the sideways test: %s", exc)

    @property
    def running(self) -> bool:
        return self.status == "RUNNING"

    def view(self) -> dict:
        return {
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "errors": self.errors[-20:],
            "params": self.params,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report": self.report,
        }

    def start(self, symbols: list[str], fetch, days: int, minutes: int, window: int, sma_fast: int, sma_slow: int, wait=None) -> dict:
        if self.running:
            raise RuntimeError("A sideways test is already running.")
        names = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))[:MAX_STOCKS]
        if not names:
            raise ValueError("Pick at least one stock.")
        self.status = "RUNNING"
        self.done, self.total = 0, len(names)
        self.errors = []
        self._stop = False
        self.params = {"symbols": len(names), "days": days, "minutes": minutes, "window": window, "sma_fast": sma_fast, "sma_slow": sma_slow}
        self.started_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        self.finished_at = None
        self._task = asyncio.create_task(self._run(names, fetch, days, minutes, window, sma_fast, sma_slow, wait))
        return self.view()

    def stop(self) -> dict:
        self._stop = True
        return self.view()

    async def _run(self, names, fetch, days, minutes, window, sma_fast, sma_slow, wait) -> None:
        day_list = trading_days(dt.datetime.now(IST).date(), days)
        start = dt.datetime.combine(day_list[0] - dt.timedelta(days=5), dt.time(9, 15), tzinfo=IST)
        end = dt.datetime.combine(day_list[-1], SESSION_END, tzinfo=IST)
        samples: list[dict] = []
        try:
            for name in names:
                if self._stop:
                    break
                try:
                    if wait is not None:
                        await wait()
                    tape = await fetch(name, start, end)
                    got = await asyncio.to_thread(analyze_stock, name, tape, day_list, minutes, window, sma_fast, sma_slow)
                    samples.extend(got)
                except Exception as exc:  # noqa: BLE001 - one stock's failure never stops the study
                    self.errors.append({"symbol": name, "error": str(exc)[:200]})
                self.done += 1
                await asyncio.sleep(0)
            self.report = await asyncio.to_thread(summarize, samples)
            self.report["stopped"] = self._stop
            self.status = "DONE"
        except Exception as exc:  # noqa: BLE001
            log.exception("sideways test failed")
            self.errors.append({"symbol": "", "error": str(exc)[:200]})
            self.status = "FAILED"
        finally:
            self.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
            if self.status == "DONE":
                self._save()


_STUDY: RegimeStudy | None = None


def study() -> RegimeStudy:
    global _STUDY
    if _STUDY is None:
        _STUDY = RegimeStudy()
    return _STUDY

