"""Live SMA re-check at execution (live_sma.py; owner's SHREECEM case of 23 Nov: a buy judged on the 09:40 close, with
SMA 9 just above SMA 21, filled at the 09:45 open, and SMA 9 fell under SMA 21 inside that candle).

`live_sma_check` (off by default; per bot and per stock, PAPER and LIVE, replays too) and `live_sma_min_gap` (% , 0 =
SMA 9 only has to be on the trade's side of SMA 21). The entry checks read closed candles; this one reads the SMAs as if
the forming candle closed at the live price now: SMA fast / slow over the closed closes plus the live price (`live_gap`).

  * An entry that orders at once (`apply_signal`): refused unless the live gap is on the signal's side by at least the
    minimum (joins the other entry checks, so late entry can retry it).
  * An entry that waits (`PullbackOrder`: entry-candle confirm, candle-extreme limit, tall-candle / day-extreme / colour
    pullback): judged on every price while it waits and once more at the fill; the first price on which the live SMAs
    are against the signal drops it (`StrategyEngine._watch_pullbacks`).

Judged on the signal's side (a flipped stock follows its signal, like the cross). Direct orders (Force, news, hand-offs)
never read it. Pure helpers; the engine keeps the state.
"""
from __future__ import annotations

import pandas as pd

MAX_GAP = 5.0
NOTE = "live SMA check"


def on(cfg) -> bool:
    return bool(getattr(cfg, "live_sma_check", False))


def min_gap(cfg) -> float:
    try:
        raw = getattr(cfg, "live_sma_min_gap", None)
        value = float(0.0 if raw is None else raw)
    except (TypeError, ValueError):
        return 0.0
    if value != value:
        return 0.0
    return max(0.0, min(MAX_GAP, value))


def live_gap(frame: pd.DataFrame | None, price: float | None, fast: int, slow: int) -> float | None:
    """Signed (SMA fast − SMA slow) ÷ SMA slow × 100 with the forming candle's close set to `price`. `frame` holds the
    closed candles plus the forming one last. None when there are too few candles or no price."""
    if frame is None or getattr(frame, "empty", True) or "close" not in frame.columns:
        return None
    try:
        live = float(price)
    except (TypeError, ValueError):
        return None
    if not live > 0:
        return None
    fast, slow = max(1, int(fast)), max(1, int(slow))
    closes = pd.to_numeric(frame["close"], errors="coerce").tolist()
    closes[-1] = live
    if len(closes) < max(fast, slow):
        return None
    tail_f, tail_s = closes[-fast:], closes[-slow:]
    if any(pd.isna(x) for x in tail_s) or any(pd.isna(x) for x in tail_f):
        return None
    sma_f = sum(tail_f) / fast
    sma_s = sum(tail_s) / slow
    if sma_s == 0:
        return None
    return (sma_f - sma_s) / sma_s * 100.0


def refusal(frame: pd.DataFrame | None, price: float | None, direction: str, cfg) -> str | None:
    """None when the check is off or the live SMAs agree with the signal's side, else why not."""
    if not on(cfg):
        return None
    gap = live_gap(frame, price, int(getattr(cfg, "sma_fast", 9) or 9), int(getattr(cfg, "sma_slow", 21) or 21))
    if gap is None:
        return f"{NOTE}: SMAs not known on the live price"
    need = min_gap(cfg)
    signed = gap if direction == "LONG" else -gap
    if signed > 0 and signed >= need:
        return None
    where = "above" if direction == "LONG" else "below"
    floor = f" by at least {need:g}%" if need > 0 else ""
    return f"{NOTE}: on the live price {float(price):.2f} SMA 9 is not {where} SMA 21{floor} (gap {gap:+.3f}%)"
