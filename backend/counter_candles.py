"""Counter candles (counter_candles.py): a run of candles against the trade turns it.

Read on closed candles of the bot's own size. Both parts are off by default and
judged per bot and per stock, in PAPER and LIVE.

* **Exit** (`counter_exit_count` N, 0 = off): an open trade closes when the last
  N closed candles all went against it — green for a short, red for a long
  (`counter_rule` COLOUR), or each close beyond the one before (CLOSES), or
  both (BOTH). With `counter_need_fast` on (default) the last close must also
  be past the fast SMA on that side, so a short bounce inside the trend does
  not end the trade. With `counter_min_body` (× ATR, 0 = off) each of those N
  candles must also have a body |close − open| of at least that × its ATR, so
  small green candles in a short (or red in a buy) do not count. Exit reason
  `COUNTER_CANDLES`.
* **Entry** (`counter_entry`, off by default): a flat stock is entered the
  way such a run points, before the SMAs cross: the run must go against the
  side the SMAs are still on (fast below slow, then N green candles closing
  above the fast SMA buys). Once per SMA run; every check, the cut-off, the
  caps, the stop, the flip and one-LIVE-bot-per-stock still apply
  (`StrategyEngine.apply_signal`). The SMA cross that follows is then a normal
  cross. Gap mode and candle patterns ignore it.

Nothing here places an order on its own; the engine decides.
"""
from __future__ import annotations

import math

import pandas as pd

from indicators import _direction_reason

RULES = ("COLOUR", "CLOSES", "BOTH")
MAX_COUNT = 10
MAX_MIN_BODY = 5.0


def exit_count(cfg) -> int:
    try:
        n = int(getattr(cfg, "counter_exit_count", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_COUNT, n))


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "counter_entry", False))


def entry_count(cfg) -> int:
    """Candles a reversal entry needs: the exit's count when set, else 3."""
    n = exit_count(cfg)
    return n if n > 0 else 3


def rule(cfg) -> str:
    value = (getattr(cfg, "counter_rule", None) or "COLOUR").upper()
    return value if value in RULES else "COLOUR"


def need_fast(cfg) -> bool:
    value = getattr(cfg, "counter_need_fast", None)
    return True if value is None else bool(value)


def min_body(cfg) -> float:
    """Smallest body (× ATR) a counter candle needs for the exit; 0 = any size."""
    value = _num(getattr(cfg, "counter_min_body", 0) or 0)
    if value is None or not math.isfinite(value):
        return 0.0
    return max(0.0, min(MAX_MIN_BODY, value))


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def run_towards(
    closed: pd.DataFrame, side: str, count: int, how: str, fast: bool, body: float = 0.0
) -> tuple[bool, str]:
    """Did the last `count` closed candles all move towards `side` (LONG = up)?

    `closed` holds closed candles only (no forming one), with `sma_9` when
    `fast` is asked for and `atr_14` when `body` (× ATR) is above 0. Returns
    (yes, a short note for the screen).
    """
    if closed is None or count <= 0:
        return False, ""
    reasons = _direction_reason(closed, side, count, how)
    if reasons:
        return False, reasons[0]
    colour = "green" if side == "LONG" else "red"
    note = f"{count} {colour} candles in a row" if how == "COLOUR" else f"{count} candles {'up' if side == 'LONG' else 'down'} in a row"
    if body > 0:
        for _, bar in closed.tail(count).iterrows():
            size, atr = _num(bar.get("close")), _num(bar.get("atr_14"))
            opened = _num(bar.get("open"))
            if size is None or opened is None or atr is None or atr <= 0:
                return False, "ATR not ready for the counter-candle body check"
            size = abs(size - opened)
            if size < body * atr:
                return False, f"{note}, but a body of {size:.2f} is under {body:g} × ATR ({body * atr:.2f})"
        note += f", each body ≥ {body:g} × ATR"
    if fast:
        close, sma = _num(closed["close"].iloc[-1]), _num(closed.iloc[-1].get("sma_9"))
        if close is None or sma is None:
            return False, "fast SMA not ready"
        if (close - sma) * (1 if side == "LONG" else -1) <= 0:
            return False, f"{note}, but the close {close:.2f} is not {'above' if side == 'LONG' else 'below'} the fast SMA {sma:.2f}"
        note += f", close {'above' if side == 'LONG' else 'below'} the fast SMA"
    return True, note


def against(side: str) -> str:
    return "SHORT" if side == "LONG" else "LONG"


def sma_side(closed: pd.DataFrame) -> str | None:
    """LONG when the fast SMA is above the slow on the last closed candle, SHORT below."""
    if closed is None or len(closed) == 0:
        return None
    fast, slow = _num(closed.iloc[-1].get("sma_9")), _num(closed.iloc[-1].get("sma_21"))
    if fast is None or slow is None or fast == slow:
        return None
    return "LONG" if fast > slow else "SHORT"
