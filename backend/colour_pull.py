"""Candle-colour entries that wait for a pullback (owner's request: a colour buy went in at the top of a strong
green candle).

In candle-colour mode (``entry_mode = "COLOUR"``) the order normally goes at the market at the start of the next
candle, i.e. at the close of the candle that confirmed the colour, which on a strong candle is its top. With
``colour_pull`` set, the entry instead waits as a `PullbackOrder` at a level inside the last closed colour candle
(real prices, never Heikin Ashi), on the side the real order goes (a flipped stock's buy signal sells):

* ``HALF`` – half-way back: the middle of the candle, (high + low) / 2.
* ``LOW``  – the candle's low for a buy, its high for a sell.
* ``SMA9`` – the fast SMA on the colour candles when it sits inside the candle on the pullback side
  (between the low and the close for a buy), else the candle's low (high for a sell).

``colour_pull_candles`` (1-3) colour candles are waited after the signal candle; if the price never comes back,
``colour_pull_market`` decides: off = no trade (the next colour signal decides again), on = buy at the market once
the wait is over, so the move is not missed. Every entry check, the stop, flip, cut-off, caps and one LIVE bot per
stock still apply (the fill goes through ``StrategyEngine._open`` like every other waiting entry). PAPER and LIVE,
per bot and per stock, replays too. Off by default.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

LEVELS = ("OFF", "HALF", "LOW", "SMA9")
NOTE = "colour pullback"
MARKET_TAG = "MARKET_AFTER_WAIT"
MAX_CANDLES = 3


def _get(cfg: Any, name: str, default: Any) -> Any:
    value = getattr(cfg, name, None)
    return default if value is None else value


def level_rule(cfg: Any) -> str:
    rule = str(_get(cfg, "colour_pull", "OFF") or "OFF").upper()
    return rule if rule in LEVELS else "OFF"


def on(cfg: Any) -> bool:
    return level_rule(cfg) != "OFF"


def candles(cfg: Any) -> int:
    try:
        n = int(_get(cfg, "colour_pull_candles", 1))
    except (TypeError, ValueError):
        n = 1
    return max(1, min(MAX_CANDLES, n))


def market_after(cfg: Any) -> bool:
    return bool(_get(cfg, "colour_pull_market", False))


def level(frame: pd.DataFrame | None, side: str, cfg: Any, sma_fast: int) -> tuple[float, int] | None:
    """(the limit, the start of the signal candle) on the last closed colour candle of `frame` (forming one last),
    for the real order's `side` (LONG buys, SHORT sells). None when there is no closed candle."""
    if frame is None or len(frame) < 2:
        return None
    candle = frame.iloc[-2]
    try:
        high, low, close = float(candle["high"]), float(candle["low"]), float(candle["close"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (high > 0 and low > 0 and high >= low):
        return None
    rule = level_rule(cfg)
    if rule == "HALF":
        limit = (high + low) / 2
    elif rule == "SMA9":
        closes = pd.to_numeric(frame["close"].iloc[:-1], errors="coerce")
        n = max(1, int(sma_fast or 9))
        sma = float(closes.tail(n).mean()) if len(closes) >= n else float("nan")
        if side == "LONG":
            limit = sma if low <= sma < close else low
        else:
            limit = sma if close < sma <= high else high
    else:
        limit = low if side == "LONG" else high
    return limit, int(candle["ts"])
