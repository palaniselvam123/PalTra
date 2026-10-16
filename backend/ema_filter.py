"""EMA cross check (ema_filter.py): a second pair of lines, EMA fast / EMA slow.

Like Groww's "EMA Cross 9 200". The bot's signals stay the SMA cross; the EMAs
are an extra check, read on closed candles of the bot's own size. Both parts are
off by default and judged per bot and per stock, in PAPER and LIVE.

* **Entry** (`use_ema`): a buy only while EMA fast is above EMA slow, a sell only
  while it is below. Wired through `_entry_block` like the other entry filters.
* **Exit** (`ema_exit`): an open trade closes on the first candle closed after
  the entry on which the EMAs cross against it (fast drops below slow in a buy,
  rises above it in a sell). Exit reason `EMA_CROSS`.

`ema_fast` / `ema_slow` are the lengths (defaults 9 and 200). Exponential
averages start from the first close (`ewm(adjust=False)`), so the slow one is
only trusted once there are `ema_slow` closed candles. Nothing here places an
order on its own; the engine decides.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_FAST = 9
DEFAULT_SLOW = 200
MAX_LEN = 500


def lengths(cfg) -> tuple[int, int]:
    def read(name: str, default: int) -> int:
        try:
            value = int(getattr(cfg, name, None) or default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(MAX_LEN, value))

    return read("ema_fast", DEFAULT_FAST), read("ema_slow", DEFAULT_SLOW)


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "use_ema", False))


def exit_on(cfg) -> bool:
    return bool(getattr(cfg, "ema_exit", False))


def emas(closed: pd.DataFrame, fast: int, slow: int) -> tuple[pd.Series, pd.Series]:
    close = pd.to_numeric(closed["close"], errors="coerce")
    return close.ewm(span=fast, adjust=False).mean(), close.ewm(span=slow, adjust=False).mean()


def _ready(closed: pd.DataFrame | None, fast: int, slow: int) -> str | None:
    """None when there are enough closed candles for both EMAs, else why not."""
    need = max(fast, slow)
    have = 0 if closed is None else len(closed)
    if have < need:
        return f"EMA {need} is not ready ({have} of {need} candles)"
    return None


def entry_reason(frame: pd.DataFrame, direction: str, cfg) -> str | None:
    """Why the EMA check refuses this entry, or None when it agrees.

    `frame` ends with the forming candle, which is never read.
    """
    fast, slow = lengths(cfg)
    closed = frame.iloc[:-1] if frame is not None else None
    waiting = _ready(closed, fast, slow)
    if waiting:
        return waiting
    f, s = emas(closed, fast, slow)
    a, b = float(f.iloc[-1]), float(s.iloc[-1])
    if direction == "LONG" and a <= b:
        return f"EMA {fast} {a:.2f} is not above EMA {slow} {b:.2f}"
    if direction == "SHORT" and a >= b:
        return f"EMA {fast} {a:.2f} is not below EMA {slow} {b:.2f}"
    return None


def passed_text(frame: pd.DataFrame, direction: str, cfg) -> str:
    fast, slow = lengths(cfg)
    f, s = emas(frame.iloc[:-1], fast, slow)
    word = "above" if direction == "LONG" else "below"
    return f"EMA {fast} {float(f.iloc[-1]):.2f} {word} EMA {slow} {float(s.iloc[-1]):.2f}"


def crossed_against(closed: pd.DataFrame, side: str, cfg) -> tuple[bool, str]:
    """Did the EMAs cross against `side` (LONG / SHORT) on the last closed candle?"""
    fast, slow = lengths(cfg)
    if _ready(closed, fast, slow) or len(closed) < 2:
        return False, ""
    f, s = emas(closed, fast, slow)
    before = float(f.iloc[-2]) - float(s.iloc[-2])
    now = float(f.iloc[-1]) - float(s.iloc[-1])
    if side == "LONG" and before >= 0 > now:
        return True, f"EMA {fast} {float(f.iloc[-1]):.2f} crossed below EMA {slow} {float(s.iloc[-1]):.2f}"
    if side == "SHORT" and before <= 0 < now:
        return True, f"EMA {fast} {float(f.iloc[-1]):.2f} crossed above EMA {slow} {float(s.iloc[-1]):.2f}"
    return False, ""
