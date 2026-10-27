"""Supertrend (supertrend.py): one line that follows the price at an ATR distance.

The same arithmetic as Groww's / TradingView's "Supertrend" (ATR length 10,
factor 3 by default): ATR is Wilder's (RMA, seeded with the simple average of
the first `period` true ranges); the bands sit `factor` × ATR above and below
(high + low) / 2, a band only tightens while the trend holds, and the trend
flips when a candle closes beyond the band on the other side. Read on closed
candles of the bot's own size. Both parts are off by default and judged per bot
and per stock, in PAPER and LIVE.

* **Entry** (`use_supertrend`): a buy only while the Supertrend is up (green,
  under the price), a sell only while it is down. Wired through `_entry_block`.
* **Exit** (`st_exit`): an open trade closes on the first candle closed after
  the entry on which the Supertrend flips against it (a close beyond the line).
  Exit reason `SUPERTREND`.

* **Stop loss** (`stop_type = "SUPERTREND"`, with the stop on): the stop sits on the Supertrend line of the last
  closed candle when the line is on the safe side of the real position (under the price for a long, above it for a
  short), else the ATR stop is kept until it is. On each newly closed candle `StrategyEngine._trail_supertrend` moves it
  to the new line, tighter only; in LIVE the exchange stop is moved with Groww's modify (like the trailing stop). Hit
  on the price like any stop: exit reason `ST_SL_HIT`.

Nothing here places an order on its own; the engine decides.
"""
from __future__ import annotations

import math

import pandas as pd

DEFAULT_PERIOD = 10
DEFAULT_FACTOR = 3.0
MAX_PERIOD = 100
MIN_FACTOR, MAX_FACTOR = 0.5, 10.0


def settings(cfg) -> tuple[int, float]:
    try:
        period = int(getattr(cfg, "st_period", None) or DEFAULT_PERIOD)
    except (TypeError, ValueError):
        period = DEFAULT_PERIOD
    try:
        factor = float(getattr(cfg, "st_mult", None) or DEFAULT_FACTOR)
    except (TypeError, ValueError):
        factor = DEFAULT_FACTOR
    if not math.isfinite(factor):
        factor = DEFAULT_FACTOR
    return max(1, min(MAX_PERIOD, period)), max(MIN_FACTOR, min(MAX_FACTOR, factor))


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "use_supertrend", False))


def exit_on(cfg) -> bool:
    return bool(getattr(cfg, "st_exit", False))


def stop_on(cfg) -> bool:
    """The Supertrend line is this entry's stop (only when the stop itself is on)."""
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    return str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() == "SUPERTREND"


def stop_level(closed: pd.DataFrame | None, direction: str, cfg) -> float | None:
    """The Supertrend line on the last closed candle when it guards `direction` (under a long, above a short)."""
    if closed is None or len(closed) < 2:
        return None
    line, trend = _state(closed, cfg)
    if line[-1] is None or trend[-1] is None:
        return None
    if direction == "LONG" and trend[-1] == 1:
        return float(line[-1])
    if direction == "SHORT" and trend[-1] == -1:
        return float(line[-1])
    return None


def compute(frame: pd.DataFrame, period: int, factor: float) -> tuple[list[float | None], list[int | None]]:
    """(line, direction) per row: direction 1 = up (line under the price), -1 = down.

    None until `period` candles have given the ATR.
    """
    n = len(frame)
    line: list[float | None] = [None] * n
    trend: list[int | None] = [None] * n
    if n == 0:
        return line, trend
    high = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
    atr = None
    trs: list[float] = []
    upper = lower = None
    direction = 1
    for i in range(n):
        prev_close = close[i - 1] if i > 0 else close[i]
        tr = max(high[i] - low[i], abs(high[i] - prev_close), abs(low[i] - prev_close))
        if not math.isfinite(tr):
            continue
        if atr is None:
            trs.append(tr)
            if len(trs) < period:
                continue
            atr = sum(trs) / period
        else:
            atr = (atr * (period - 1) + tr) / period
        mid = (high[i] + low[i]) / 2
        basic_up, basic_dn = mid + factor * atr, mid - factor * atr
        if upper is None:
            upper, lower = basic_up, basic_dn
            direction = 1 if close[i] >= mid else -1
        else:
            upper = basic_up if basic_up < upper or prev_close > upper else upper
            lower = basic_dn if basic_dn > lower or prev_close < lower else lower
            if direction == -1 and close[i] > upper:
                direction = 1
            elif direction == 1 and close[i] < lower:
                direction = -1
        trend[i] = direction
        line[i] = lower if direction == 1 else upper
    return line, trend


def _state(closed: pd.DataFrame, cfg) -> tuple[list[float | None], list[int | None]]:
    period, factor = settings(cfg)
    return compute(closed, period, factor)


def entry_reason(frame: pd.DataFrame, direction: str, cfg) -> str | None:
    """Why the Supertrend refuses this entry, or None when it agrees. The forming candle is not read."""
    closed = frame.iloc[:-1] if frame is not None else None
    period, factor = settings(cfg)
    if closed is None or len(closed) <= period:
        have = 0 if closed is None else len(closed)
        return f"Supertrend is not ready ({have} of {period + 1} candles)"
    line, trend = compute(closed, period, factor)
    if trend[-1] is None:
        return "Supertrend is not ready"
    if direction == "LONG" and trend[-1] != 1:
        return f"Supertrend is down ({line[-1]:.2f} above the price)"
    if direction == "SHORT" and trend[-1] != -1:
        return f"Supertrend is up ({line[-1]:.2f} under the price)"
    return None


def passed_text(frame: pd.DataFrame, direction: str, cfg) -> str:
    line, _trend = _state(frame.iloc[:-1], cfg)
    word = "up, under" if direction == "LONG" else "down, above"
    return f"Supertrend {word} the price ({line[-1]:.2f})"


def flipped_against(closed: pd.DataFrame, side: str, cfg) -> tuple[bool, str]:
    """Did the Supertrend flip against `side` (LONG / SHORT) on the last closed candle?"""
    if closed is None or len(closed) < 2:
        return False, ""
    line, trend = _state(closed, cfg)
    before, now = trend[-2], trend[-1]
    if before is None or now is None or before == now:
        return False, ""
    close = float(closed["close"].iloc[-1])
    if side == "LONG" and now == -1:
        return True, f"close {close:.2f} fell below the Supertrend; it turned down ({line[-1]:.2f})"
    if side == "SHORT" and now == 1:
        return True, f"close {close:.2f} rose above the Supertrend; it turned up ({line[-1]:.2f})"
    return False, ""
