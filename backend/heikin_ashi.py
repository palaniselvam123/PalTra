"""Heikin Ashi colours (heikin_ashi.py): the colour rules read smoothed candles.

Off by default (`ha_colours`), per bot and per stock, PAPER and LIVE, replays too. Heikin Ashi
candles are averages, so a trend shows as a long run of one colour and a lone red candle in a
rise (or green in a fall) mostly disappears:

    HA close = (open + high + low + close) / 4
    HA open  = (previous HA open + previous HA close) / 2   (the first: (open + close) / 2)

With it on, every rule that reads a candle's **colour** reads the Heikin Ashi one instead: the
candle direction check (COLOUR / BOTH), counter candles (exit, early reversal, re-entry), candle
colour entries, the SMA 9 turn's "other colour" waits and the turn-trade colour exit. Prices
are never changed: orders, stops, targets, SMAs, closes and P&L stay on the real candles.
`view` gives the frame those rules read: the real close, with `open` moved so close − open is
the Heikin Ashi body (its sign is the Heikin Ashi colour, its size the Heikin Ashi body).
Candle patterns keep the real candles.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def on(cfg) -> bool:
    return bool(getattr(cfg, "ha_colours", False))


def candles(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(HA open, HA close) for every row of `frame`, oldest first."""
    o = pd.to_numeric(frame["open"], errors="coerce").to_numpy(dtype=float)
    h = pd.to_numeric(frame["high"], errors="coerce").to_numpy(dtype=float)
    lo = pd.to_numeric(frame["low"], errors="coerce").to_numpy(dtype=float)
    c = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
    ha_close = (o + h + lo + c) / 4.0
    ha_open = np.full(len(c), np.nan)
    prev_o = prev_c = np.nan
    for i in range(len(c)):
        if np.isnan(prev_o) or np.isnan(prev_c):
            ha_open[i] = (o[i] + c[i]) / 2.0
        else:
            ha_open[i] = (prev_o + prev_c) / 2.0
        if not np.isnan(ha_open[i]) and not np.isnan(ha_close[i]):
            prev_o, prev_c = ha_open[i], ha_close[i]
    return pd.Series(ha_open, index=frame.index), pd.Series(ha_close, index=frame.index)


def view(frame: pd.DataFrame | None, cfg) -> pd.DataFrame | None:
    """`frame` as the colour rules read it: unchanged when the switch is off, else with `open`
    set so close − open is the Heikin Ashi body (the close itself stays real)."""
    if frame is None or not on(cfg) or len(frame) == 0 or not {"open", "high", "low", "close"} <= set(frame.columns):
        return frame
    ha_open, ha_close = candles(frame)
    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    out["ha_open"], out["ha_close"] = ha_open, ha_close
    out["open"] = (close - (ha_close - ha_open)).where(ha_open.notna() & ha_close.notna(), out["open"])
    return out
