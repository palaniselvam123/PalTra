"""VWAP + ATR buffer stop (`stop_type = "VWAP"`, PAPER and LIVE, per bot and per stock, replays too).

    LONG   stop = VWAP − vwap_stop_atr × ATR
    SHORT  stop = VWAP + vwap_stop_atr × ATR

The session VWAP is where the day's money has traded on average ("institutional fair value"); a buy that
holds above it is still in demand, one that falls through it has lost the day's buyers. The ATR buffer
(default 0.5 × ATR, the same Wilder ATR the ATR stop uses, on the bot's own candle) keeps a wick through
the line from closing the trade. Both are read on the bot's closed candles, once per newly closed candle
(`StrategyEngine._trail_vwap_stop`), and the stop only ever tightens, like the Supertrend stop: a long's
stop rises with VWAP, never falls. At entry the level is used only when it already guards the real position
(under a long, above a short); else the ATR stop guards the trade until it does. In LIVE the exchange stop
is moved in place with `modify_sl` (`_move_stop`); a position restored without its stop id stays at its
saved stop. Hit on the price like any stop (`VWAP_SL_HIT`; in LIVE Groww's stop fills it, no second exit).
"""
from __future__ import annotations

import pandas as pd

from indicators import _session_vwap, atr_wilder

STOP_TYPE = "VWAP"
DEFAULT_BUFFER = 0.5
MIN_BUFFER = 0.0
MAX_BUFFER = 5.0
HIT_REASON = "VWAP_SL_HIT"


def stop_on(cfg) -> bool:
    """VWAP − k × ATR is this entry's stop (only when the stop itself is on)."""
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    return str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() == STOP_TYPE


def buffer(cfg) -> float:
    try:
        k = float(getattr(cfg, "vwap_stop_atr", DEFAULT_BUFFER))
    except (TypeError, ValueError):
        return DEFAULT_BUFFER
    if k != k:  # NaN
        return DEFAULT_BUFFER
    return max(MIN_BUFFER, min(MAX_BUFFER, k))


ATR_PERIOD = 14


def _atr(closed: pd.DataFrame) -> float | None:
    """The Wilder ATR on the closed candles: the enriched column when the frame has it, else computed here
    (a replay or research frame may carry plain OHLC only)."""
    series = closed["atr_14"] if "atr_14" in closed.columns else None
    if series is None:
        if not {"high", "low", "close"}.issubset(closed.columns) or len(closed) < ATR_PERIOD + 1:
            return None
        try:
            series = atr_wilder(closed, ATR_PERIOD)
        except Exception:  # noqa: BLE001
            return None
    for idx in (-1, -2):
        if len(series) < abs(idx):
            continue
        try:
            value = float(series.iloc[idx])
        except (TypeError, ValueError):
            continue
        if value == value and value > 0:
            return value
    return None


def level(closed: pd.DataFrame | None, direction: str, cfg, atr: float | None = None) -> float | None:
    """VWAP ∓ buffer × ATR on the closed candles, whatever side of the price it is on; None without a VWAP or ATR.

    `atr` (the ATR the entry was sized with) is used when given; else it is read from the candles."""
    if closed is None or len(closed) == 0:
        return None
    vwap = _session_vwap(closed)
    atr = atr if atr is not None and atr > 0 else _atr(closed)
    if vwap is None or atr is None:
        return None
    k = buffer(cfg)
    return vwap - k * atr if direction == "LONG" else vwap + k * atr


def stop_level(closed: pd.DataFrame | None, direction: str, cfg, price: float | None = None, atr: float | None = None) -> float | None:
    """The stop when it guards `direction` (under a long, above a short, against `price` when given), else None."""
    value = level(closed, direction, cfg, atr=atr)
    if value is None:
        return None
    if price is not None and price > 0:
        if direction == "LONG" and value >= price:
            return None
        if direction == "SHORT" and value <= price:
            return None
    return float(value)
