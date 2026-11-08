"""Hand-off entries wait for the forming candle (handoff_candle.py; `handoff_candle`, per bot and per stock, PAPER and LIVE,
replays too). A hand-off order (`HANDOFF`: Bot 3 taking a trend from Bot 1 at once, Bot 2's `handoff_now`) used to go at
once, on the side the sending bot chose. With this set on the **receiving** bot:

* `WAIT`: the order waits until the forming candle (the bot's own candle, `_frames` last row) is
  `entry_candle_pct` (0.05) % past its open on the hand-off's side (green so far for a buy, red for a sell); the level
  follows each new forming candle; given up after `entry_candle_max` (2) closed candles: no trade.
* `FOLLOW`: the order goes the forming candle's way once it is that far past its open either way: green buys, red
  sells, whatever side the hand-off asked for; given up the same way.

`OFF` (default) orders at once as before. Nothing here places an order: `StrategyEngine._watch_handoff_waits` sends it
through `direct_order` (entry reason `HANDOFF:CANDLE_CONFIRM`), so every rule `direct_order` keeps still applies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

MODES = ("OFF", "WAIT", "FOLLOW")
CONFIRM_TAG = ":CANDLE_CONFIRM"


def mode(cfg) -> str:
    value = str(getattr(cfg, "handoff_candle", None) or "OFF").upper()
    return value if value in MODES else "OFF"


@dataclass
class Wait:
    want: str          # LONG / SHORT: the side the hand-off asked for
    signal_ts: int     # the closed candle on the tape when it arrived
    span_sec: int      # the bot's candle length
    candles: int       # give up after this many more closed candles
    pct: float
    mode: str
    note: str = ""


def decide(m: str, want: str, price, forming_open, pct: float) -> str | None:
    """The side to order now, or None to keep waiting: the price `pct` % past the forming candle's open."""
    try:
        p, o = float(price), float(forming_open)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(p) and math.isfinite(o)) or p <= 0 or o <= 0 or m not in ("WAIT", "FOLLOW"):
        return None
    step = o * max(0.0, pct) / 100.0
    up, down = p >= o + step and p > o, p <= o - step and p < o
    if m == "WAIT":
        return want if (up if want == "LONG" else down) else None
    return "LONG" if up else "SHORT" if down else None
