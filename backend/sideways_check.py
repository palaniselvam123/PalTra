"""Skip sideways candles (sideways_check.py): no entry while the last candles went nowhere.

Off by default (`use_sideways`), per bot and per stock, PAPER and LIVE, replays too, entries only. Wired through
`strategy_engine._entry_block` like the other checks (check name `sideways`, chart ✕ "Sideways"). Owner's request:
the SMAs stay stacked bullish for several candles after the price has gone flat, so the bot bought at the top of a
small cluster of mixed candles that then broke down.

It is the Trend quality card's phase rule (`trend_phases`) read on the bot's own closed candles, over the last
`sideways_candles` (6) candles:

    efficiency   |net close move| / sum of |close-to-close moves| (1 = a straight line, near 0 = back and forth);
                 at least `sideways_eff` (0.5) must be reached
    move         |net close move| in ATRs (the candles' Wilder ATR 14); at least `sideways_atr` (1.0), 0 = not checked

Either one short = the candles went sideways = the entry is refused (late entry can retry it). The check does not
care which way the candles moved, so it never blocks the first entry after a cross on its side; the cross and the
other checks say which way. Not enough candles yet refuses, like the other checks. Nothing here places or changes
an order.
"""
from __future__ import annotations

import math

import pandas as pd

DEFAULT_CANDLES = 6
MIN_CANDLES = 3
MAX_CANDLES = 30
DEFAULT_EFF = 0.5
MIN_EFF = 0.1
MAX_EFF = 1.0
DEFAULT_ATR = 1.0
MAX_ATR = 5.0


def on(cfg) -> bool:
    return bool(getattr(cfg, "use_sideways", False))


def _num(value, default: float, lo: float, hi: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(out):
        return default
    return max(lo, min(hi, out))


def candles(cfg) -> int:
    return int(_num(getattr(cfg, "sideways_candles", DEFAULT_CANDLES), DEFAULT_CANDLES, MIN_CANDLES, MAX_CANDLES))


def min_eff(cfg) -> float:
    return _num(getattr(cfg, "sideways_eff", DEFAULT_EFF), DEFAULT_EFF, MIN_EFF, MAX_EFF)


def min_atr(cfg) -> float:
    return _num(getattr(cfg, "sideways_atr", DEFAULT_ATR), DEFAULT_ATR, 0.0, MAX_ATR)


def read(frame: pd.DataFrame | None, cfg) -> dict | None:
    """{eff, net, atr, moved_atr, n} over the last N closed candles, or None when not ready."""
    n = candles(cfg)
    closed = frame.iloc[:-1] if frame is not None and len(frame) >= 2 else None
    if closed is None or len(closed) < n + 1 or "close" not in closed.columns:
        return None
    close = pd.to_numeric(closed["close"].iloc[-(n + 1):], errors="coerce").to_numpy(dtype="float64")
    if not all(math.isfinite(c) for c in close):
        return None
    net = float(close[-1] - close[0])
    path = float(sum(abs(close[i + 1] - close[i]) for i in range(n)))
    atr = None
    if "atr_14" in closed.columns:
        a = pd.to_numeric(closed["atr_14"].iloc[-1:], errors="coerce").iloc[0]
        atr = float(a) if pd.notna(a) and a > 0 else None
    return {
        "n": n,
        "net": net,
        "eff": abs(net) / path if path > 0 else 0.0,
        "atr": atr,
        "moved_atr": abs(net) / atr if atr else None,
    }


def entry_reason(frame: pd.DataFrame | None, direction: str, cfg) -> str | None:
    """Why the sideways check refuses this entry, or None when the candles are moving."""
    got = read(frame, cfg)
    if got is None:
        return "Sideways check is not ready (needs more candles)"
    why = []
    need_eff = min_eff(cfg)
    if got["eff"] < need_eff:
        why.append(f"last {got['n']} candles went back and forth (efficiency {got['eff']:.2f}, needs {need_eff:.2f})")
    need_atr = min_atr(cfg)
    if need_atr > 0:
        if got["moved_atr"] is None:
            why.append("ATR not known yet")
        elif got["moved_atr"] < need_atr:
            why.append(f"last {got['n']} candles moved only {got['moved_atr']:.2f} ATR, needs {need_atr:g}")
    # Joined with " and ", not "; ": the engine splits a refusal into checks on "; ".
    return f"Sideways candles: {' and '.join(why)}" if why else None


def passed_text(frame: pd.DataFrame | None, direction: str, cfg) -> str:
    got = read(frame, cfg)
    if got is None:
        return "Not sideways"
    moved = f", moved {got['moved_atr']:.2f} ATR" if got["moved_atr"] is not None else ""
    return f"Not sideways: last {got['n']} candles efficiency {got['eff']:.2f}{moved}"
