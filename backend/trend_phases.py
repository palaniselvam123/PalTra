"""Trend phases (trend_phases.py): today's candles split into choppy / strong bull / strong bear.

Read-only, no order path. Used by the Chop scan (`chop_scan.analyze`) for the
Trend quality card's "Phase" column, timeline and filter. Built from the owner's
example (Bajaj Finserv, 15 Oct, 5-minute candles): 09:45-12:10 choppy,
12:10-13:35 strong bullish, 13:35-14:40 strong bearish.

Each closed candle is judged on the window of the last WINDOW candles:

    efficiency  |net close move| / sum of |close-to-close moves| (Kaufman's
                efficiency ratio): 1 = a straight line, near 0 = back and forth
    strength    |net move| in ATRs (Wilder ATR of the same candles)

A candle is trending up when efficiency >= MIN_EFFICIENCY and the net move is
at least MIN_ATR_MOVE ATRs up (down for bearish). Runs of trending candles of
one side, with gaps of at most BRIDGE candles closed, become a phase when they
have at least MIN_RUN trending candles; shorter bursts count as choppy. A phase
is backdated to the candle after the window's lowest close (highest for a bear
phase), since a window only sees a trend once part of it has happened.
Everything else is CHOPPY. Only today's candles are labelled; earlier days feed
the ATR and the first windows.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

BULL = "BULL"
BEAR = "BEAR"
CHOP = "CHOP"

WINDOW = 6
MIN_EFFICIENCY = 0.5
MIN_ATR_MOVE = 1.0
MIN_RUN = 5
BRIDGE = 1


def _labels(close: np.ndarray, atr: np.ndarray) -> list[str]:
    out = [CHOP] * len(close)
    for i in range(WINDOW, len(close)):
        net = close[i] - close[i - WINDOW]
        path = float(np.abs(np.diff(close[i - WINDOW : i + 1])).sum())
        a = atr[i]
        if not path or not np.isfinite(a) or a <= 0:
            continue
        if abs(net) / path >= MIN_EFFICIENCY and abs(net) >= MIN_ATR_MOVE * a:
            out[i] = BULL if net > 0 else BEAR
    return out


def _bridge(labels: list[str]) -> list[str]:
    """Close gaps of up to BRIDGE choppy candles between two candles of the same side."""
    out = list(labels)
    i = 0
    while i < len(out):
        if out[i] == CHOP:
            j = i
            while j < len(out) and out[j] == CHOP:
                j += 1
            if 0 < i and j < len(out) and j - i <= BRIDGE and out[i - 1] == out[j]:
                for k in range(i, j):
                    out[k] = out[j]
            i = j
        else:
            i += 1
    return out


def phases(enriched: pd.DataFrame, today_start: int) -> list[dict]:
    """Today's phases, oldest first: {side, start_ts, end_ts, candles, move_pct}.

    `enriched` holds closed candles (oldest first) with `ts`, `close` and `atr_14`;
    `today_start` is the first epoch second of today's session (earlier candles only
    feed the windows). Adjacent phases never share a side.
    """
    if enriched is None or len(enriched) == 0 or "close" not in enriched or "ts" not in enriched:
        return []
    close = enriched["close"].to_numpy(dtype="float64")
    atr = enriched["atr_14"].to_numpy(dtype="float64") if "atr_14" in enriched else np.full(len(close), np.nan)
    ts = enriched["ts"].to_numpy(dtype="int64")
    raw = _bridge(_labels(close, atr))

    labels = [CHOP] * len(close)
    i = 0
    while i < len(raw):
        side = raw[i]
        j = i
        while j < len(raw) and raw[j] == side:
            j += 1
        if side != CHOP and j - i >= MIN_RUN:
            # Backdate to the turn inside the first window: after its lowest close (bull) / highest (bear).
            lo = max(0, i - WINDOW)
            seg = close[lo : i + 1]
            turn = lo + int(np.argmin(seg) if side == BULL else np.argmax(seg))
            start = min(i, turn + 1)
            for k in range(start, j):
                labels[k] = side
        i = j

    out: list[dict] = []
    first_today = int(np.searchsorted(ts, today_start))
    i = first_today
    while i < len(labels):
        side = labels[i]
        j = i
        while j < len(labels) and labels[j] == side:
            j += 1
        base = close[i - 1] if i > 0 else close[i]
        out.append(
            {
                "side": side,
                "start_ts": int(ts[i]),
                "end_ts": int(ts[j - 1]),
                "candles": j - i,
                "move_pct": round((close[j - 1] / base - 1) * 100, 2) if base else None,
            }
        )
        i = j
    return out


def summary(found: list[dict]) -> dict:
    """Row fields for the scan: the phase now, since when, its move, and counts of today's strong phases."""
    if not found:
        return {"phase": None, "phase_since_ts": None, "phase_move_pct": None, "phase_candles": 0, "phases": [], "strong_phases": 0}
    now = found[-1]
    return {
        "phase": now["side"],
        "phase_since_ts": now["start_ts"],
        "phase_move_pct": now["move_pct"],
        "phase_candles": now["candles"],
        "phases": found,
        "strong_phases": sum(1 for p in found if p["side"] != CHOP),
    }
