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

CANDLE_MINUTES = (1, 2, 3, 5, 10, 15, 30, 60)
# Sizes that need more 1-minute history than the five days every stock gets (history_days).
LONG_MINUTES = 30
SESSION_MINUTES = 375  # 09:15-15:30
BASE_HISTORY_DAYS = 5  # what the live tape always downloads
MAX_HISTORY_DAYS = 29  # Groww's candle download takes at most 30 days
BASE_TAPE_BARS = 2500  # 1-minute bars a stock's tape keeps
MAX_TAPE_BARS = 8000
SPARE_CANDLES = 10  # candles past the slow SMA, so it is formed before the first one of the day
DEFAULT_MINUTES = 1
_IST_OFFSET = 5 * 3600 + 30 * 60  # epoch seconds to IST wall clock
_SESSION_OPEN = 9 * 3600 + 15 * 60  # 09:15 IST


def candle_minutes(cfg, own: dict | None = None) -> int:
    """The interval a stock trades on: its own setting (`own`), else the bot's; 1 for candle patterns / colour."""
    own = own or {}
    mode = own.get("entry_mode", getattr(cfg, "entry_mode", None))
    if str(mode or "SMA").upper() in ("PATTERN", "COLOUR"):
        return 1
    if str(mode or "SMA").upper() == "NTH_CANDLE":
        # Second candle entries (nth_candle.py): the bot trades on the second-candle size itself.
        try:
            value = int(own.get("nth_candle_minutes", getattr(cfg, "nth_candle_minutes", None)) or 5)
        except (TypeError, ValueError):
            return 5
        return value if value in CANDLE_MINUTES else 5
    try:
        value = int(own.get("candle_minutes", getattr(cfg, "candle_minutes", None)) or DEFAULT_MINUTES)
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    return value if value in CANDLE_MINUTES else DEFAULT_MINUTES


def history_need(minutes: int, sma_slow: int) -> tuple[int, int]:
    """(calendar days of 1-minute candles, bars to keep) a stock on `minutes` candles needs for its slow SMA.

    Up to 15-minute candles this is the five days and 2,500 bars every stock has always had. A 30-minute or 1-hour
    candle has only 13 or 7 a session, so its slow SMA needs more days (owner's request: 30 min and 1 hour as a bot
    candle); never more than Groww's 30-day download, so a very long SMA on 1-hour candles is not formed."""
    try:
        minutes, slow = int(minutes), max(2, int(sma_slow or 21))
    except (TypeError, ValueError):
        return BASE_HISTORY_DAYS, BASE_TAPE_BARS
    if minutes < LONG_MINUTES:
        return BASE_HISTORY_DAYS, BASE_TAPE_BARS
    per_session = -(-SESSION_MINUTES // minutes)
    sessions = -(-(slow + SPARE_CANDLES) // per_session)
    days = -(-sessions * 7 // 5) + 3  # weekends, and a holiday or two to spare
    bars = (sessions + 1) * SESSION_MINUTES
    return max(BASE_HISTORY_DAYS, min(MAX_HISTORY_DAYS, days)), max(BASE_TAPE_BARS, min(MAX_TAPE_BARS, bars))


def formed_candles(minutes: int) -> int:
    """About how many closed candles of this size the longest download holds (the longest slow SMA it can form)."""
    sessions = (MAX_HISTORY_DAYS - 3) * 5 // 7
    return sessions * (-(-SESSION_MINUTES // max(1, int(minutes)))) - SPARE_CANDLES


# What each engine's stocks need now: symbol -> {engine id: (days, bars)}. The quote client is shared by every bot
# and has no settings of its own, so the engines say here what they trade a stock on (StrategyEngine.tick) and the
# client reads the most any of them asks for. Memory only.
_NEEDS: dict[str, dict[int, tuple[int, int]]] = {}


def note_need(owner: int, symbol: str, minutes: int, sma_slow: int) -> tuple[int, int]:
    """An engine trades `symbol` on these candles: keep (or drop) its longer-history ask. Returns the ask."""
    need = history_need(minutes, sma_slow)
    name = (symbol or "").upper()
    if need == (BASE_HISTORY_DAYS, BASE_TAPE_BARS):
        asks = _NEEDS.get(name)
        if asks is not None:
            asks.pop(owner, None)
            if not asks:
                _NEEDS.pop(name, None)
    else:
        _NEEDS.setdefault(name, {})[owner] = need
    return need


def need_of(symbol: str) -> tuple[int, int]:
    """(days, bars) the stock's tape should hold: the most any engine asked for, else the usual five days."""
    asks = _NEEDS.get((symbol or "").upper())
    if not asks:
        return BASE_HISTORY_DAYS, BASE_TAPE_BARS
    return max(d for d, _ in asks.values()), max(b for _, b in asks.values())


def bucket_start(ts: int, minutes: int) -> int:
    """Start (epoch seconds) of the `minutes` candle holding `ts`, counted from 09:15 IST."""
    if minutes <= 1:
        return int(ts) - int(ts) % 60
    span = minutes * 60
    local = int(ts) + _IST_OFFSET
    anchor = local - local % 86400 + _SESSION_OPEN
    return anchor + ((local - anchor) // span) * span - _IST_OFFSET


_F64, _I64 = np.dtype("float64"), np.dtype("int64")


def last_prices(frame: pd.DataFrame, cols: list[str]) -> np.ndarray | None:
    """`frame[cols].iloc[-1].to_numpy()` read straight from the columns, without building `frame[cols]`.

    Only for float64 / int64 columns with at least one float64 among them (pandas then gives a float64 row);
    None for any other mix, which the caller reads the pandas way.
    """
    if not cols or not frame.columns.is_unique:
        return None
    dtypes = [frame[c].dtype for c in cols]
    if _F64 not in dtypes or any(d != _F64 and d != _I64 for d in dtypes):
        return None
    return np.array([frame[c].to_numpy()[-1] for c in cols], dtype="float64")


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
    if not frame.columns.is_unique:
        return pd.DataFrame(out, columns=list(frame.columns))
    # Every array above is new (fancy indexing, reduceat), so the frame wraps them without a copy, in the tape's
    # column order. Passing `columns=` instead took three times as long, and this runs per stock on every pass.
    return pd.DataFrame({name: out[name] for name in frame.columns}, copy=False)
