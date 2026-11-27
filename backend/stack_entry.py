"""Three SMAs stacked with widening gaps as an entry (stack_entry.py).

Off by default (`stack_entry`), per bot and per stock, PAPER and LIVE, replays too (owner's request: the Trend quality
card's "3 SMAs" read as a bot strategy). A flat armed stock is entered when, on the bot's closed candles:

* the three moving averages are **stacked** one way: fast above slow above the third = a buy, the mirror = a sell;
* **both gaps** (fast vs slow, slow vs third) have **widened** on each of the last `stack_widen` (1-10, default 2)
  closed candles in a row, so the trend is still opening up;
* each gap is at least `stack_min_gap` % wide (0-5, default 0.05; 0 = any), so lines that barely differ do not count.

The three lines: SMA fast / slow and a third, longer one (`stack_third`, default 50, longer than the slow SMA). A bot
that already trades three lines (`sma_medium` set) uses its own fast / medium / slow and `stack_third` is not read.

Asked once per closed candle (`StrategyEngine._stack_entry_pass`); each widening run enters at most once (a run that
began at or before the candle of the last entry is the same run), a run the entry checks refuse is asked again on its
next candle while it still widens, and one that has widened `RUN_CAP` (30) candles or more is too old to join. The order goes through `apply_signal` (entry reason `STACK_WIDEN`), so every ticked entry check, the stop, flip,
market hours, the cut-off, caps, the wait after an exit and one LIVE bot per stock apply. Exits are the bot's own.

`stack_only` (off by default): only this opens trades; the bot's own signals (the SMA cross, a turn, join …) open
nothing on the stock (a cross still closes a trade, it does not reverse). Force, news and hand-off orders still pass.

Nothing here places an order itself.
"""
from __future__ import annotations

import pandas as pd

import trend_trade

ENTRY_REASON = "STACK_WIDEN"
WIDEN_MAX = 10
THIRD_DEFAULT = 50
THIRD_MAX = 200
MIN_GAP_DEFAULT = 0.05
RUN_CAP = 30  # chop_scan.WIDEN_MAX: how far back a widening run is counted


def enabled(cfg) -> bool:
    return bool(getattr(cfg, "stack_entry", False))


def only(cfg) -> bool:
    return enabled(cfg) and bool(getattr(cfg, "stack_only", False))


def widen_need(cfg) -> int:
    try:
        value = int(getattr(cfg, "stack_widen", 2) or 2)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(WIDEN_MAX, value))


def min_gap(cfg) -> float:
    try:
        value = float(getattr(cfg, "stack_min_gap", MIN_GAP_DEFAULT))
    except (TypeError, ValueError):
        value = MIN_GAP_DEFAULT
    return max(0.0, min(5.0, value))


def periods(cfg) -> tuple[int, int, int] | None:
    """(fast, middle, longest) the entry reads, or None when the third line is not longer than the slow one."""
    fast, slow = int(getattr(cfg, "sma_fast", 9) or 9), int(getattr(cfg, "sma_slow", 21) or 21)
    medium = int(getattr(cfg, "sma_medium", 0) or 0)
    if medium and fast < medium < slow:
        return fast, medium, slow
    try:
        third = int(getattr(cfg, "stack_third", THIRD_DEFAULT) or THIRD_DEFAULT)
    except (TypeError, ValueError):
        third = THIRD_DEFAULT
    return (fast, slow, third) if slow < third <= THIRD_MAX else None


def longest(cfg) -> int:
    """The longest SMA the entry needs formed (for how many days of candles to keep); 0 when the entry is off."""
    if not enabled(cfg):
        return 0
    found = periods(cfg)
    return found[2] if found else 0


def read(frame: pd.DataFrame | None, cfg) -> dict | None:
    """The three-SMA read on the closed candles of `frame` (its last row is the forming candle), or None."""
    found = periods(cfg)
    if found is None or frame is None or len(frame) < 2:
        return None
    from chop_scan import three_read  # here, not at the top: chop_scan's imports reach back to the engine

    return three_read(frame.iloc[:-1], *found)


def call(frame: pd.DataFrame | None, cfg) -> tuple[str | None, int | None, str]:
    """(LONG / SHORT / None, the candle the widening run began on, why in plain words)."""
    found = periods(cfg)
    if found is None:
        return None, None, "the third SMA must be longer than the slow SMA"
    got = read(frame, cfg)
    names = f"SMA {found[0]} / {found[1]} / {found[2]}"
    if got is None or got.get("gap_slow_third_pct") is None:
        return None, None, f"{names}: SMA {found[2]} is not formed yet"
    a, b = float(got["gap_fast_slow_pct"]), float(got["gap_slow_third_pct"])
    gaps = f"gaps {a:+.2f}% · {b:+.2f}%"
    if not got.get("stack3"):
        return None, None, f"{names} not stacked ({gaps})"
    side = "LONG" if got["stack3"] == "BULLISH" else "SHORT"
    word = "up" if side == "LONG" else "down"
    need, floor = widen_need(cfg), min_gap(cfg)
    widened = int(got.get("widen_candles") or 0)
    if widened < need:
        return None, None, f"{names} stacked {word}, widening {widened} of {need} candle(s) ({gaps})"
    if floor > 0 and min(abs(a), abs(b)) < floor:
        return None, None, f"{names} stacked {word} and widening, but a gap is under {floor:g}% ({gaps})"
    if widened >= RUN_CAP:
        # The read counts a run only this far back, so its start is not known: too old to join, and never traded twice.
        return None, None, f"{names} stacked {word}, widening for {RUN_CAP}+ candles: too late to join ({gaps})"
    closed = frame.iloc[:-1]
    start = int(closed["ts"].iloc[-min(widened, len(closed))])
    return side, start, f"{names} stacked {word}, both gaps widening {widened} candle(s) ({gaps})"


def blocks(cfg, why: str | None) -> str | None:
    """Why the bot's own signal may not open a trade here (`stack_only`), or None."""
    if not only(cfg):
        return None
    reason = str(why or "SMA_CROSS").split(":", 1)[0].split("@", 1)[0]
    if reason == ENTRY_REASON or reason.startswith(trend_trade.PASS_REASONS):
        return None
    return "only the three-SMA stack with widening gaps opens trades on this stock"
