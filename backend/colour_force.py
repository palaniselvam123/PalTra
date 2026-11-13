"""Force order follows the candle colour (``colour_force``, off by default; per bot and per stock, PAPER and LIVE).

In candle-colour mode (``entry_mode = "COLOUR"``) the bot's own candle is 1 minute, so a Force order used to read
SMA fast vs slow on that 1-minute candle, which can point the other way from the colour candles on the chart.
With this on, Force reads the last *closed* colour candle of ``pattern_tf`` minutes with the bot's own colour rule
(``candle_patterns.colour_call``: candles in a row, smallest body, Heikin Ashi colours, smaller candles inside):
green buys, red sells, no colour (a doji, a small body, a mixed run) places no order. The forming colour candle is
never read. Other modes, or the switch off, keep the SMA side.
"""

from __future__ import annotations

import pandas as pd

from candle_patterns import closes_bucket, colour_call, setting, uses_colour


def on(cfg) -> bool:
    return bool(getattr(cfg, "colour_force", False)) and uses_colour(cfg)


def side(frame: pd.DataFrame | None, cfg) -> tuple[str | None, str]:
    """("BULLISH" | "BEARISH" | None, plain note) from the last closed colour candle.

    `frame` is the enriched 1-minute tape with the forming minute last. The tape is cut just after the last 1-minute
    candle that closed a colour candle, so ``colour_call`` reads that colour candle as if it had just closed.
    """
    if frame is None or len(frame) < 2:
        return None, "no closed colour candle yet"
    tf = int(setting(cfg, "pattern_tf"))
    closed = frame.iloc[:-1]
    ends = [i for i, ts in enumerate(closed["ts"].astype("int64")) if tf <= 1 or closes_bucket(int(ts), tf)]
    if not ends:
        return None, f"no closed {tf}-minute colour candle yet"
    # colour_call drops the last row as the forming one: keep one row after the colour candle's last minute.
    cut = frame.iloc[: ends[-1] + 2]
    call = colour_call(cut, cfg)
    if call.side is None:
        return None, call.note
    return ("BULLISH" if call.side == "LONG" else "BEARISH"), call.note
