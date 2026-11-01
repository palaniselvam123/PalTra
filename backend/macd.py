"""MACD check and exit (macd.py): MACD line vs its signal line, on the bot's own closed candles.

The bot's signals stay the SMA cross; MACD is an extra check. Both parts are off by default,
per bot and per stock, PAPER and LIVE, replays too.

    MACD line   = EMA(close, fast) − EMA(close, slow)            (defaults 12 / 26)
    signal line = EMA(MACD line, signal)                         (default 9)
    histogram   = MACD line − signal line

* **Entry** (`use_macd`): a buy only while the histogram is above zero (MACD above its signal
  line), a sell only while it is below. Wired through `_entry_block` like the other filters.
  Refused until there are `slow + signal` closed candles.
* **Exit** (`macd_exit`): an open trade closes on the first candle closed after the entry on
  which MACD crosses its signal line against it. Exit reason `MACD_CROSS`.

EMAs are `ewm(adjust=False)`, as Groww and TradingView draw them. Nothing here places an order.
"""
from __future__ import annotations

import pandas as pd

DEFAULT_FAST = 12
DEFAULT_SLOW = 26
DEFAULT_SIGNAL = 9
MAX_LEN = 200
EXIT_REASON = "MACD_CROSS"


def lengths(cfg) -> tuple[int, int, int]:
    def read(name: str, default: int) -> int:
        try:
            value = int(getattr(cfg, name, None) or default)
        except (TypeError, ValueError):
            value = default
        return max(1, min(MAX_LEN, value))

    return read("macd_fast", DEFAULT_FAST), read("macd_slow", DEFAULT_SLOW), read("macd_signal", DEFAULT_SIGNAL)


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "use_macd", False))


def exit_on(cfg) -> bool:
    return bool(getattr(cfg, "macd_exit", False))


def series(closed: pd.DataFrame, fast: int, slow: int, signal: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(MACD line, signal line, histogram)."""
    close = pd.to_numeric(closed["close"], errors="coerce")
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    sig = line.ewm(span=signal, adjust=False).mean()
    return line, sig, line - sig


def _ready(closed: pd.DataFrame | None, cfg) -> str | None:
    fast, slow, signal = lengths(cfg)
    need = max(fast, slow) + signal
    have = 0 if closed is None else len(closed)
    if have < need:
        return f"MACD is not ready ({have} of {need} candles)"
    return None


def _label(cfg) -> str:
    fast, slow, signal = lengths(cfg)
    return f"MACD {fast},{slow},{signal}"


def entry_reason(frame: pd.DataFrame, direction: str, cfg) -> str | None:
    """Why the MACD check refuses this entry, or None when it agrees. The forming candle is never read."""
    closed = frame.iloc[:-1] if frame is not None else None
    waiting = _ready(closed, cfg)
    if waiting:
        return waiting
    line, sig, hist = series(closed, *lengths(cfg))
    h = float(hist.iloc[-1])
    if direction == "LONG" and h <= 0:
        return f"{_label(cfg)}: MACD {float(line.iloc[-1]):.3f} is not above its signal {float(sig.iloc[-1]):.3f}"
    if direction == "SHORT" and h >= 0:
        return f"{_label(cfg)}: MACD {float(line.iloc[-1]):.3f} is not below its signal {float(sig.iloc[-1]):.3f}"
    return None


def passed_text(frame: pd.DataFrame, direction: str, cfg) -> str:
    line, sig, _hist = series(frame.iloc[:-1], *lengths(cfg))
    word = "above" if direction == "LONG" else "below"
    return f"{_label(cfg)}: MACD {float(line.iloc[-1]):.3f} {word} its signal {float(sig.iloc[-1]):.3f}"


def crossed_against(closed: pd.DataFrame, side: str, cfg) -> tuple[bool, str]:
    """Did MACD cross its signal line against `side` (LONG / SHORT) on the last closed candle?"""
    if _ready(closed, cfg) or len(closed) < 2:
        return False, ""
    line, sig, hist = series(closed, *lengths(cfg))
    before, now = float(hist.iloc[-2]), float(hist.iloc[-1])
    if side == "LONG" and before >= 0 > now:
        return True, f"{_label(cfg)}: MACD {float(line.iloc[-1]):.3f} crossed below its signal {float(sig.iloc[-1]):.3f}"
    if side == "SHORT" and before <= 0 < now:
        return True, f"{_label(cfg)}: MACD {float(line.iloc[-1]):.3f} crossed above its signal {float(sig.iloc[-1]):.3f}"
    return False, ""
