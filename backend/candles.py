"""Candle interval for the SMA bots (``candle_minutes``: 1, 2, 3, 5, 10 or 15).

The bot always fetches Groww's 1-minute candles. With an interval above 1
each stock's tape is grouped into candles of that many minutes, counted from
09:15 IST each day, before anything reads it: the SMAs, ATR, filters, stops,
gap mode, Bollinger exit, the chart and the replays all see the longer
candles, and a signal is judged once each longer candle closes. The last
candle is the forming one, as with 1-minute candles.

Candle-pattern entries keep their own ``pattern_tf`` on the 1-minute tape.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CANDLE_MINUTES = (1, 2, 3, 5, 10, 15)
DEFAULT_MINUTES = 1
_IST_OFFSET = 5 * 3600 + 30 * 60  # epoch seconds to IST wall clock
_SESSION_OPEN = 9 * 3600 + 15 * 60  # 09:15 IST


def candle_minutes(cfg, own: dict | None = None) -> int:
    """The interval a stock trades on: its own setting (`own`), else the bot's; 1 for candle patterns."""
    own = own or {}
    mode = own.get("entry_mode", getattr(cfg, "entry_mode", None))
    if str(mode or "SMA").upper() == "PATTERN":
        return 1
    try:
        value = int(own.get("candle_minutes", getattr(cfg, "candle_minutes", None)) or DEFAULT_MINUTES)
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    return value if value in CANDLE_MINUTES else DEFAULT_MINUTES


def bucket_start(ts: int, minutes: int) -> int:
    """Start (epoch seconds) of the `minutes` candle holding `ts`, counted from 09:15 IST."""
    if minutes <= 1:
        return int(ts) - int(ts) % 60
    span = minutes * 60
    local = int(ts) + _IST_OFFSET
    anchor = local - local % 86400 + _SESSION_OPEN
    return anchor + ((local - anchor) // span) * span - _IST_OFFSET


def resample(frame: pd.DataFrame | None, minutes: int) -> pd.DataFrame | None:
    """1-minute candles (oldest first, forming last) as `minutes` candles.

    Open of the first minute, highest high, lowest low, close of the last
    minute. `volume` is Groww's running session total, so a candle keeps its
    last minute's total (indicators.derive_minute_volume takes the difference).
    """
    if frame is None or minutes <= 1 or getattr(frame, "empty", True) or "ts" not in frame.columns:
        return frame
    ts = frame["ts"].to_numpy(dtype="int64")
    span = minutes * 60
    local = ts + _IST_OFFSET
    anchor = local - local % 86400 + _SESSION_OPEN
    start = anchor + ((local - anchor) // span) * span - _IST_OFFSET
    first = np.flatnonzero(np.r_[True, start[1:] != start[:-1]])
    last = np.r_[first[1:] - 1, len(ts) - 1]
    out: dict[str, np.ndarray] = {"ts": start[first]}
    if "open" in frame.columns:
        out["open"] = frame["open"].to_numpy(dtype="float64")[first]
    if "high" in frame.columns:
        out["high"] = np.maximum.reduceat(frame["high"].to_numpy(dtype="float64"), first)
    if "low" in frame.columns:
        out["low"] = np.minimum.reduceat(frame["low"].to_numpy(dtype="float64"), first)
    for name in frame.columns:
        if name in out:
            continue
        # close, the running volume and anything else: the candle's last minute.
        out[name] = frame[name].to_numpy()[last]
    return pd.DataFrame(out, columns=list(frame.columns))
