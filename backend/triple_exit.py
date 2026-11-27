"""Close a three-line trade on the fast / medium cross (owner's request: with SMA 9 / 21 / 50 the trade opens when the
three lines stack up, but closed only when they stacked the other way, giving back much of the profit).

With a third line set (`sma_medium`, so the bot enters on fast > medium > slow and its mirror) and `triple_exit` on
(off by default), an open trade closes on a candle closed after the entry on which the fast SMA crossed the medium SMA
against it: under it for a buy, over it for a sell (`FAST_MEDIUM_CROSS`). No reverse: a new trade still needs the whole
stack the other way. The slow line stays the trend filter for entries only. Per bot and per stock, PAPER and LIVE,
replays too. No order path of its own: the exit goes through the bot's `_exit_now` like every other exit.
"""
from __future__ import annotations

import pandas as pd

REASON = "FAST_MEDIUM_CROSS"


def medium(cfg) -> int | None:
    try:
        value = getattr(cfg, "sma_medium", None)
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def on(cfg) -> bool:
    """The exit runs only in three-line mode (a medium SMA set) with its switch on."""
    return bool(getattr(cfg, "triple_exit", False)) and medium(cfg) is not None


def crossed_against(closed: pd.DataFrame, side: str, cfg) -> tuple[bool, str]:
    """Whether the fast SMA crossed the medium SMA against `side` on the newest of these closed candles."""
    fast_n, mid_n = int(getattr(cfg, "sma_fast", 9) or 9), medium(cfg)
    if mid_n is None or closed is None or len(closed) < mid_n + 1:
        return False, ""
    close = pd.to_numeric(closed["close"], errors="coerce")
    fast = close.rolling(fast_n).mean()
    mid = close.rolling(mid_n).mean()
    f0, f1, m0, m1 = fast.iloc[-2], fast.iloc[-1], mid.iloc[-2], mid.iloc[-1]
    if any(pd.isna(v) for v in (f0, f1, m0, m1)):
        return False, ""
    if side == "LONG" and f0 >= m0 and f1 < m1:
        return True, f"SMA {fast_n} crossed under SMA {mid_n} ({f1:.2f} < {m1:.2f})"
    if side == "SHORT" and f0 <= m0 and f1 > m1:
        return True, f"SMA {fast_n} crossed over SMA {mid_n} ({f1:.2f} > {m1:.2f})"
    return False, ""
