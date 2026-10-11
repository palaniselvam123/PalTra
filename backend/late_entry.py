"""Late entry (late_entry.py): a cross a filter refused may still trade a few minutes later.

`late_entry_min` (0 = off, the default; per bot and per stock, PAPER and LIVE).
When a fresh SMA cross is refused only because a ticked entry filter (VWAP,
RSI, volume, density, Bollinger, gap range, candle direction) or the ADX
filter said no, the cross stays armed. On every later closed candle, while the
fast SMA is still on the cross's side of the slow SMA and no more than
`late_entry_min` minutes have passed since the cross candle, the bot asks the
same checks again; the first candle on which they all pass places the order
through the usual path (`apply_signal`: cut-off, trade cap, stop, flip and
one-LIVE-bot-per-stock all apply). It is dropped when the SMAs cross back, the
window ends, a position is opened, or the entry cut-off is reached. A cross
refused by the cut-off or the trade cap is never armed. Gap mode has its own
waiting entry and ignores this setting.

Pure helpers only; the engine keeps the state (`StrategyEngine._late_pending`).
"""
from __future__ import annotations

MAX_MIN = 375


def window_min(cfg) -> int:
    try:
        value = int(getattr(cfg, "late_entry_min", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_MIN, value))


def refused_by_filter(signal: str, result: str) -> bool:
    """Whether `apply_signal`'s answer was a filter / ADX refusal that a later candle may lift."""
    text = result or ""
    if "no new entries after" in text or "trade cap" in text:
        return False
    return text.startswith(f"{signal} ignored — ") or text.startswith(f"closed on {signal} — ")


def judge(direction: str, cross_ts: int, bar_ts: int, gap: float | None, window: int) -> tuple[str, str]:
    """("try" | "drop", note) for a waiting cross on this closed candle."""
    waited = (bar_ts - cross_ts) / 60
    if gap is None:
        return "drop", "SMA not ready"
    if (direction == "LONG" and gap <= 0) or (direction == "SHORT" and gap >= 0):
        return "drop", "the SMAs crossed back before the filters agreed"
    if waited > window:
        return "drop", f"the filters did not agree within {window} min of the cross"
    return "try", f"{waited:.0f} min after the cross"
