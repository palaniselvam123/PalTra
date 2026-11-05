"""How exits combine: any one closes the trade (OR, the default) or the ticked ones must agree (AND).

Every exit normally closes the trade on its own: the first one that fires wins (OR). With `exit_join = "AND"` the
exits ticked in `exit_group` (CSV of `KEYS`) only vote: when one fires, `StrategyEngine._exit_now` notes it on the
position (`OpenPosition.exit_votes`, key → time) and keeps the trade; the trade closes once `exit_need` of them
(0 = every ticked one) have fired within the last `exit_within` candles of the bot's own size. The exit that completes
the group closes it under its own reason, so "reverse instead of only closing" still follows it. The SMA cross votes
in `_apply_locked` (a cross that completes the group closes and reverses as usual; one that does not holds the trade,
like the cross exit switched off).

Never in the group, always on their own: the stop-loss and every stop type, targets (TSL, pivot), the gap fade, the
square-off, the loss limit, the kill switch, recovery and candle-pattern / candle-colour entry modes. Exits not ticked
keep closing on their own too. Off by default; per bot and per stock, PAPER and LIVE, replays too.
"""
from __future__ import annotations

KEYS: dict[str, tuple[str, ...]] = {
    "CROSS": ("MA_CROSS",),
    "COLOUR": ("COLOUR_TURN",),
    "VTURN": ("SMA9_VTURN",),
    "TURN": ("SMA9_TURN",),
    "BEND": ("SMA9_BEND",),
    "DECEL": ("SMA9_DECEL",),
    "COUNTER": ("COUNTER_CANDLES",),
    "SMALL": ("SMALL_CANDLE_EXIT",),
    "XCANDLE": ("EXIT_CANDLE_CROSS",),
    "ST": ("SUPERTREND",),
    "BB": ("BB_MIDDLE", "BB_TARGET"),
    "EMA": ("EMA_CROSS",),
    "MACD": ("MACD_CROSS",),
    "CVD": ("CVD_DIVERGENCE",),
    "SLOW": ("SLOW_SMA_BREACH",),
}
LABELS: dict[str, str] = {
    "CROSS": "SMA cross",
    "COLOUR": "candle colour",
    "VTURN": "SMA 9 V-turn",
    "TURN": "SMA 9 turn",
    "BEND": "SMA 9 bend",
    "DECEL": "SMA 9 deceleration",
    "COUNTER": "counter candles",
    "SMALL": "smaller candles",
    "XCANDLE": "exit candle cross",
    "ST": "Supertrend",
    "BB": "Bollinger",
    "EMA": "EMA cross",
    "MACD": "MACD",
    "CVD": "cumulative delta",
    "SLOW": "slow SMA",
}
DEFAULT_WITHIN = 3
MAX_WITHIN = 20


def clean_group(value) -> str:
    """The group as a tidy CSV of known keys in a fixed order ("" = none). Unknown keys are refused."""
    if value is None:
        return ""
    parts = value if isinstance(value, (list, tuple, set)) else str(value).replace(";", ",").split(",")
    seen = {str(p).strip().upper() for p in parts if p and str(p).strip()}
    unknown = seen - set(KEYS)
    if unknown:
        raise ValueError(f"exit_group takes {', '.join(KEYS)}; not {', '.join(sorted(unknown))}")
    return ",".join(k for k in KEYS if k in seen)


def group(cfg) -> list[str]:
    try:
        return [k for k in clean_group(getattr(cfg, "exit_group", "") or "").split(",") if k]
    except ValueError:
        return []


def active(cfg) -> bool:
    """AND mode with at least two exits ticked (one alone is just that exit)."""
    return str(getattr(cfg, "exit_join", "") or "OR").upper() == "AND" and len(group(cfg)) >= 2


def key_for(reason: str) -> str | None:
    for key, reasons in KEYS.items():
        if reason in reasons:
            return key
    return None


def in_group(cfg, reason: str) -> bool:
    key = key_for(reason)
    return key is not None and active(cfg) and key in group(cfg)


def need(cfg) -> int:
    ticked = len(group(cfg))
    try:
        n = int(getattr(cfg, "exit_need", 0) or 0)
    except (TypeError, ValueError):
        n = 0
    return ticked if n <= 0 else min(n, ticked)


def within(cfg) -> int:
    try:
        n = int(getattr(cfg, "exit_within", DEFAULT_WITHIN) or DEFAULT_WITHIN)
    except (TypeError, ValueError):
        n = DEFAULT_WITHIN
    return max(1, min(MAX_WITHIN, n))


def vote(votes: dict, cfg, reason: str, now_ts: float, candle_minutes: int) -> tuple[bool, str]:
    """Note `reason`'s vote at `now_ts`; (complete, text). Votes older than the window are dropped."""
    key = key_for(reason)
    window = within(cfg) * max(1, int(candle_minutes)) * 60
    for k in [k for k, t in votes.items() if now_ts - t > window]:
        votes.pop(k, None)
    if key is not None:
        votes[key] = now_ts
    ticked = group(cfg)
    have = [k for k in ticked if k in votes]
    missing = [k for k in ticked if k not in votes]
    done = len(have) >= need(cfg)
    said = ", ".join(f"{LABELS[k]} ✓" for k in have)
    if done:
        return True, f"exits agree ({said})"
    left = need(cfg) - len(have)
    waiting = " or ".join(LABELS[k] for k in missing) if left < len(missing) else ", ".join(LABELS[k] for k in missing)
    count = f"{left} more of: " if left < len(missing) else "waiting for "
    return False, f"{said}; {count}{waiting} within {within(cfg)} candles (exits set to AND)"
