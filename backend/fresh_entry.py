"""Fresh trend entry check (fresh_entry.py): enter only when a trend has just started with volume behind it.

Off by default (`use_fresh`), per bot and per stock, PAPER and LIVE, replays too, entries only. Wired through
`strategy_engine._entry_block` like the other checks (check name `fresh`, chart ✕ "Fresh"). It is the Trend
quality card's "Fresh trend" flag (`trend_strength.fresh`) as a bot rule, the signal that did best in the owner's
30-day test. On the bot's own closed candles, for the signal's side, all must hold:

    cross    the fast / slow SMAs crossed to this side within the last FRESH_CANDLES closed candles
    vwap     the last close and the fast SMA are on this side of today's VWAP
    gap      the SMA gap is wider than on the candle before
    volume   Groww's running session volume at the last closed candle is at least `fresh_rvol` (1.5) times the
             same candle of the earlier sessions in the frame (0 = not checked; unknown refuses)

With the SMA cross entry, `late_entry_min` (three candles' worth) lets a cross that fails on its own candle
still enter on the next two while the cross is fresh. Nothing here places or changes an order.
"""
from __future__ import annotations

import pandas as pd

import nth_candle
from indicators import _session_vwap, sma_gap_pct
from trend_strength import FRESH_CANDLES, FRESH_MIN_RVOL

MAX_RVOL = 20.0


def on(cfg) -> bool:
    return bool(getattr(cfg, "use_fresh", False))


def min_rvol(cfg) -> float:
    try:
        value = float(getattr(cfg, "fresh_rvol", FRESH_MIN_RVOL))
    except (TypeError, ValueError):
        return FRESH_MIN_RVOL
    if value != value:  # NaN
        return FRESH_MIN_RVOL
    return max(0.0, min(MAX_RVOL, value))


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def read(frame: pd.DataFrame | None, direction: str, cfg) -> tuple[dict, dict] | None:
    """({check: passed}, numbers for the words) for `direction` on the closed candles; None when not ready."""
    closed = frame.iloc[:-1] if frame is not None and len(frame) >= 2 else None
    if closed is None or len(closed) < FRESH_CANDLES + 1 or not {"sma_9", "sma_21", "close", "ts"} <= set(closed.columns):
        return None
    tail = closed.iloc[-(FRESH_CANDLES + 1):]
    gaps = []
    for a, b in zip(tail["sma_9"], tail["sma_21"]):
        fast, slow = _num(a), _num(b)
        gap = sma_gap_pct(fast, slow) if fast is not None and slow is not None else None
        if gap is None:
            return None
        gaps.append(gap)
    sign = 1 if direction == "LONG" else -1
    last = closed.iloc[-1]
    close, fast = _num(last.get("close")), _num(last.get("sma_9"))
    vwap = _session_vwap(closed)
    checks = {
        # On this side now, and on the other side (or flat) somewhere in the window before: the cross is that recent.
        "cross": gaps[-1] * sign > 0 and any(g * sign <= 0 for g in gaps[:-1]),
        "vwap": vwap is not None and close is not None and fast is not None and (close - vwap) * sign > 0 and (fast - vwap) * sign > 0,
        "gap": gaps[-1] * sign > 0 and abs(gaps[-1]) > abs(gaps[-2]),
    }
    need = min_rvol(cfg)
    rvol = None
    if need > 0:
        days = nth_candle._session_days(closed)
        today = days.iloc[-1]
        rvol = nth_candle.rvol_at(closed, days, today, int((days == today).sum()))
        checks["volume"] = rvol is not None and rvol >= need
    return checks, {"gap": gaps[-1], "gap_before": gaps[-2], "vwap": vwap, "close": close, "fast": fast, "rvol": rvol, "need": need}


def entry_reason(frame: pd.DataFrame | None, direction: str, cfg) -> str | None:
    """Why the fresh-trend check refuses this entry, or None when every part holds."""
    got = read(frame, direction, cfg)
    if got is None:
        return "Fresh trend is not ready (needs more candles)"
    checks, n = got
    side = "above" if direction == "LONG" else "below"
    why = []
    if not checks["cross"]:
        why.append(f"no SMA cross to this side in the last {FRESH_CANDLES} candles")
    if not checks["vwap"]:
        why.append("VWAP not known yet" if n["vwap"] is None else f"price {n['close']:.2f} and fast SMA {n['fast']:.2f} are not both {side} VWAP {n['vwap']:.2f}")
    if not checks["gap"]:
        why.append(f"SMA gap {n['gap']:+.3f}% is not wider than {n['gap_before']:+.3f}%")
    if "volume" in checks and not checks["volume"]:
        why.append("volume vs usual not known yet" if n["rvol"] is None else f"volume {n['rvol']:.1f}× usual, needs {n['need']:g}×")
    return f"Fresh trend: {'; '.join(why)}" if why else None


def passed_text(frame: pd.DataFrame | None, direction: str, cfg) -> str:
    got = read(frame, direction, cfg)
    if got is None:
        return "Fresh trend"
    _checks, n = got
    vol = f", volume {n['rvol']:.1f}× usual" if n["rvol"] is not None else ""
    return f"Fresh trend: new cross, {'above' if direction == 'LONG' else 'below'} VWAP {n['vwap']:.2f}, gap {n['gap']:+.3f}% widening{vol}"
