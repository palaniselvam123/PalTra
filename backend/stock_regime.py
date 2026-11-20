"""Choppy or trending? A light read of each armed stock for the terminal's Stocks bar (display only).

Same arithmetic as the Scalp page's Trend quality card (`chop_scan`), on the bot's own candle size and from the 1-minute
tape the bot already holds (`StrategyEngine._tapes`), so no extra Groww call:

* `score` TRENDING / MIXED / CHOPPY from today's closed-candle SMA fast / slow crosses (`chop_scan.score_for`);
* `phase` BULL / BEAR / CHOP now, since when and its move, from `trend_phases` (efficiency over 6 candles and an ATR
  move, runs of 5+ candles).

Cached per stock until a new candle closes. The trend trade rule (trend_trade.py) and the fade un-arm read it;
no order path.

* `trend_side` / `trend_score` / `trend`: the card's strong-trend read (`trend_strength.read`, 7 checks).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import chop_scan
import trend_phases
import trend_strength
from cross_scan import closed_bars
from indicators import enrich

SESSION_OPEN_SEC = 9 * 3600 + 15 * 60

_cache: dict[tuple, dict | None] = {}


def read(frame_1m: pd.DataFrame | None, sma_fast: int, sma_slow: int, minutes: int, now: dt.datetime) -> dict | None:
    """The stock's read now, or None while the tape is too short."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    bars = closed_bars(frame_1m, minutes, now)
    if len(bars) < max(int(sma_slow), 2) + 1:
        return None
    enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), int(sma_fast), int(sma_slow))
    today = chop_scan._ist_day(int(now.timestamp()))
    hits = chop_scan._crosses(enriched, today)
    minutes_since = avg_gap = None
    if hits:
        minutes_since = max(0.0, (int(now.timestamp()) - hits[-1]["ts"]) / 60)
        if len(hits) >= 2:
            gaps = [(hits[i]["ts"] - hits[i - 1]["ts"]) / 60 for i in range(1, len(hits))]
            avg_gap = sum(gaps) / len(gaps)
    found = trend_phases.phases(enriched, today * 86_400 - 19_800 + SESSION_OPEN_SEC)
    now_phase = found[-1] if found else None
    # The Trend quality card's trend score (share of its 7 checks passed for the SMAs' side), read by trend_trade.py.
    try:
        strong = trend_strength.read(enriched, chop_scan._vwap_today(frame_1m, now), now=now, tape=frame_1m)
    except Exception:  # noqa: BLE001 - a read that fails is no trend
        strong = {}
    return {
        "trend_side": strong.get("trend_side"),
        "trend_score": strong.get("trend_score"),
        "trend": strong.get("trend"),
        "trend_checks": strong.get("trend_checks") or {},
        "trend_mode": strong.get("trend_mode"),
        # The newest closed candle the read is on (real prices): the trend entry's doji skip reads its body.
        "last_bar": _bar(bars.iloc[-1]),
        "minutes": int(minutes),
        "score": chop_scan.score_for(len(hits), minutes_since, avg_gap),
        "crosses": len(hits),
        "phase": now_phase["side"] if now_phase else None,
        "phase_since": dt.datetime.fromtimestamp(now_phase["start_ts"], chop_scan.IST).strftime("%H:%M") if now_phase else None,
        "phase_move_pct": now_phase["move_pct"] if now_phase else None,
        "phase_candles": now_phase["candles"] if now_phase else 0,
        "strong_phases": sum(1 for p in found if p["side"] != trend_phases.CHOP),
        "phase_early": trend_phases.too_early(found),
        "phases": [{"side": p["side"], "start": p["start_ts"], "end": p["end_ts"]} for p in found],
    }


def _bar(row) -> dict | None:
    try:
        return {k: float(row[k]) for k in ("open", "high", "low", "close")} | {"ts": int(row["ts"])}
    except (KeyError, TypeError, ValueError):
        return None


def cached(owner: int, symbol: str, frame_1m, sma_fast: int, sma_slow: int, minutes: int, now: dt.datetime) -> dict | None:
    """`read`, worked out once per newly closed candle of that stock (per engine `owner`)."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    slot = int(now.timestamp()) // (int(minutes) * 60)
    key = (owner, symbol, int(sma_fast), int(sma_slow), int(minutes))
    hit = _cache.get(key)
    if hit is not None and hit.get("_slot") == slot and hit.get("_len") == len(frame_1m):
        return hit["value"]
    try:
        value = read(frame_1m, sma_fast, sma_slow, minutes, now)
    except Exception:  # noqa: BLE001 - display only; never break the state payload
        value = None
    if len(_cache) > 2000:
        _cache.clear()
    _cache[key] = {"_slot": slot, "_len": len(frame_1m), "value": value}
    return value
