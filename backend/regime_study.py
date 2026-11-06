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
`by_stock` gives each stock its own trend score (% of its windows that trended), choppy score (% with 2+ SMA crosses),
SMA-follow result and the bot it suits (`suggest`: 3 trend, 2 sideways, 1 mixed, None too few windows), with the
reasons in plain words. Display only: nothing is armed or moved.
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
        "by_stock": by_stock(samples, base),
    }


# Per-stock verdict (`by_stock`): where each stock belongs, from what it actually did in the past windows.
MIN_STOCK_WINDOWS = 8  # fewer windows than this = "not enough data"
TREND_HOME = 3  # the trend bot
CHOP_HOME = 2  # the sideways (chop) bot
MAIN_HOME = 1


def _stock_row(name: str, picked: list[dict], base: float) -> dict:
    """One stock's trend and choppy score, the bot it suits and the reasons, in plain words."""
    n = len(picked)
    trended = sum(1 for s in picked if s["trended"]) / n * 100
    whipsaw = sum(1 for s in picked if s["next_crosses"] >= 2) / n * 100
    crosses = float(np.mean([s["next_crosses"] for s in picked]))
    follow = float(np.mean([s["cross_pnl_pct"] for s in picked]))
    follow_win = sum(1 for s in picked if s["cross_pnl_pct"] > 0) / n * 100
    eff = float(np.mean([s["efficiency"] for s in picked]))
    adx_vals = [s["adx"] for s in picked if s.get("adx") is not None]
    adx = float(np.mean(adx_vals)) if adx_vals else None
    move = float(np.mean([s["move_atr"] for s in picked]))
    by_time: dict[str, list[dict]] = {}
    for s in picked:
        by_time.setdefault(s["at"], []).append(s)
    times = [
        {"at": at, "windows": len(rows), "trended_pct": round(sum(1 for r in rows if r["trended"]) / len(rows) * 100, 1)}
        for at, rows in sorted(by_time.items())
    ]
    row = {
        "symbol": name,
        "windows": n,
        "days": len({s["day"] for s in picked}),
        "trend_score": round(trended, 1),
        "chop_score": round(whipsaw, 1),
        "avg_crosses": round(crosses, 2),
        "follow_pct": round(follow, 3),
        "follow_win_pct": round(follow_win, 1),
        "efficiency": round(eff, 3),
        "adx": round(adx, 1) if adx is not None else None,
        "move_atr": round(move, 2),
        "by_time": times,
    }
    row.update(suggest(row, base))
    return row


def suggest(row: dict, base: float) -> dict:
    """`bot` 3 (trend), 2 (sideways), 1 (keep on the main bot) or None (not enough data), with `why` lines."""
    n = row["windows"]
    t, c, f = row["trend_score"], row["chop_score"], row["follow_pct"]
    facts = [
        f"Trend score {t:.0f}: in {t:.0f} of 100 windows the price moved at least 1 ATR mostly one way "
        f"(all stocks in this test: {base:.0f}).",
        f"Choppy score {c:.0f}: in {c:.0f} of 100 windows the SMA lines crossed 2 or more times "
        f"(on average {row['avg_crosses']:.1f} crosses a window).",
        f"Following the SMA side made {f:+.3f}% a window on average before charges, and was in profit in "
        f"{row['follow_win_pct']:.0f}% of windows.",
        f"How straight it moved: {row['efficiency']:.2f} (0 = back where it started, 1 = a straight line)"
        + (f"; ADX on average {row['adx']:.0f} (under 20 weak, over 25 strong)." if row.get("adx") is not None else "."),
    ]
    best = max(row["by_time"], key=lambda x: x["trended_pct"], default=None)
    worst = min(row["by_time"], key=lambda x: x["trended_pct"], default=None)
    if best and worst and best["at"] != worst["at"] and best["trended_pct"] - worst["trended_pct"] >= 25:
        facts.append(
            f"Time of day matters: from {best['at']} it trended {best['trended_pct']:.0f}% of the time, "
            f"from {worst['at']} only {worst['trended_pct']:.0f}%."
        )
    if n < MIN_STOCK_WINDOWS:
        return {
            "bot": None,
            "verdict": "NOT_ENOUGH",
            "headline": f"Only {n} windows read: too few to say. Test more days.",
            "why": facts,
        }
    trend_bar = max(base + 10, 40.0)
    if t >= trend_bar and c <= 30 and f > 0:
        return {
            "bot": TREND_HOME,
            "verdict": "TRENDING",
            "headline": "Trends often and the SMA side paid: suits the trend bot (Bot 3).",
            "why": facts
            + [
                f"Bot 3 because its trend score ({t:.0f}) is at least {trend_bar:.0f}, its choppy score ({c:.0f}) is "
                "30 or less, and following the SMAs made money."
            ],
        }
    if c >= 40 or (t <= base - 10 and f < 0):
        reason = (
            f"its choppy score ({c:.0f}) is 40 or more"
            if c >= 40
            else f"it trended less than usual ({t:.0f} vs {base:.0f}) and following the SMAs lost money"
        )
        return {
            "bot": CHOP_HOME,
            "verdict": "CHOPPY",
            "headline": "Mostly sideways with many crosses: SMA-cross entries lose here. Suits the sideways bot (Bot 2) or skip it.",
            "why": facts
            + [
                f"Bot 2 because {reason}. On Bot 1 / Bot 3 every extra cross is a small loss plus charges.",
                "If Bot 2 also loses on it in a backtest, take it off the bots altogether.",
            ],
        }
    return {
        "bot": MAIN_HOME,
        "verdict": "MIXED",
        "headline": "Sometimes trends, sometimes chops: keep it on the main bot (Bot 1) and let the hand-offs move it on the day.",
        "why": facts
        + [
            f"Not Bot 3: needs a trend score of {trend_bar:.0f}+, choppy 30 or less and a profit following the SMAs. "
            "Not Bot 2: needs a choppy score of 40+, or fewer trends than usual with a loss."
        ],
    }


def by_stock(samples: list[dict], base: float | None = None) -> list[dict]:
    """Every stock's scores and suggested bot, trending first."""
    if not samples:
        return []
    if base is None:
        base = sum(1 for s in samples if s["trended"]) / len(samples) * 100
    groups: dict[str, list[dict]] = {}
    for s in samples:
        groups.setdefault(s["symbol"], []).append(s)
    rows = [_stock_row(name, picked, base) for name, picked in groups.items()]
    rows.sort(key=lambda r: (r["bot"] is None, -(r["trend_score"] - r["chop_score"])))
    return rows


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

