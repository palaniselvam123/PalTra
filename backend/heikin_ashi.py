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

**Entries, exits or both** (`ha_scope`: `BOTH` default | `ENTRY` | `EXIT`, per bot and per stock, PAPER and LIVE,
replays too; owner's request: enter on normal candles and exit on Heikin Ashi ones, or the other way round): with
`ENTRY` only the rules that open a trade read the Heikin Ashi colours (candle direction check, candle colour entries,
Force following the colour, Second candle, early reversal and counter re-entry, smaller-candle entry check) and every
exit reads the real candles; with `EXIT` it is the other way round (the colour turn of an open trade, counter
candles, smaller-candle exit and colour-turn confirm, the SMA 9 turn's colour waits / holds, the turn-trade colour
exit, recovery hold). In candle colour mode a trade closed by an exit read on one kind of candle opens the other
side only when the entry read on the other kind asks for it too, else the stock waits flat. Each caller says which
it is (`role`); `for_role` gives a settings view for code that reads `on(cfg)` without a role.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


SCOPES = ("BOTH", "ENTRY", "EXIT")
ROLES = ("entry", "exit")


def scope(cfg) -> str:
    value = str(getattr(cfg, "ha_scope", None) or "BOTH").upper()
    return value if value in SCOPES else "BOTH"


def on(cfg, role: str | None = None) -> bool:
    """Heikin Ashi colours for a rule of this role ("entry" / "exit"); no role = whenever the switch is on."""
    if not bool(getattr(cfg, "ha_colours", False)):
        return False
    if role is None:
        return True
    want = scope(cfg)
    return want == "BOTH" or want == role.upper()


def split(cfg) -> bool:
    """True when entries and exits read different candles (the switch on, for one of them only)."""
    return bool(getattr(cfg, "ha_colours", False)) and scope(cfg) != "BOTH"


class _Role:
    """The settings as a rule of one role reads them: `ha_colours` already decided for that role."""

    def __init__(self, cfg, value: bool):
        self._cfg = cfg
        self._value = value

    def __getattr__(self, name):
        if name == "ha_colours":
            return self.__dict__["_value"]
        if name == "ha_scope":
            return "BOTH"
        return getattr(self.__dict__["_cfg"], name)


def for_role(cfg, role: str):
    """`cfg` for code that asks `on(cfg)` without a role (candle colour calls, smaller-candle colours)."""
    if cfg is None or not split(cfg):
        return cfg
    return _Role(cfg, on(cfg, role))


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


def view(frame: pd.DataFrame | None, cfg, role: str | None = None) -> pd.DataFrame | None:
    """`frame` as the colour rules read it: unchanged when the switch is off (or not for this role), else with `open`
    set so close − open is the Heikin Ashi body (the close itself stays real)."""
    if frame is None or not on(cfg, role) or len(frame) == 0 or not {"open", "high", "low", "close"} <= set(frame.columns):
        return frame
    ha_open, ha_close = candles(frame)
    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    out["ha_open"], out["ha_close"] = ha_open, ha_close
    out["open"] = (close - (ha_close - ha_open)).where(ha_open.notna() & ha_close.notna(), out["open"])
    return out
