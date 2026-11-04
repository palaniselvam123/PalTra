"""Reverse instead of only closing (reverse_exit.py), and the manual Reverse button.

**Setting** (`reverse_on_exit`, CSV of `VTURN` / `TURN` / `CANDLES`, "" = off by default; per bot and per stock, PAPER
and LIVE, replays too). Some exits only close the trade. With a key ticked, the exit closes the trade as before and the
bot then asks for the other side at once through `apply_signal` (entry reason `EXIT_REVERSE`, alert off), so every entry
check, the stop, flip, market hours, the cut-off, the caps, the loss lock and one LIVE bot per stock still apply:

* `VTURN`   – the SMA 9 V-turn exit (`SMA9_VTURN`, slope_exit.py)
* `TURN`    – the SMA 9 turn exit (`SMA9_TURN`, slope_exit.py)
* `CANDLES` – the counter-candle exit (`COUNTER_CANDLES`, counter_candles.py)

The other side is the opposite of the closed trade's signal side, so a flipped stock stays flipped. A refusal leaves
the stock flat; the bot's own signals take over from there.

**Close a reversed trade on the other colour** (`reverse_colour_exit`, off by default; per bot and per stock, PAPER and
LIVE, replays too): a trade opened by that reverse (entry reason `EXIT_REVERSE`) closes on the first candle of the other
colour that opened with or after its entry (red for a long, green for a short), like "Close a turn trade on the other
colour" does for SMA 9 turn trades (`StrategyEngine._watch_turn_colour`, exit `COLOUR_TURN`, via `_exit_now`, which
cancels a LIVE stop first). No reverse after it, so a stock never ping-pongs. A manual Reverse (`MANUAL_REVERSE`) is not
affected.

**Manual Reverse** (`StrategyEngine.reverse_position`, `POST /api/bot/reverse` and the research / bots / replay forms):
closes the open trade (`MANUAL_REVERSE`) and opens the same stock the other way at once (entry reason `MANUAL_REVERSE`,
like a direct order: no cross or entry filter is read; market hours, the cut-off, the Trade list, the caps, the loss
lock and one LIVE bot per stock apply). A LIVE bot needs `confirm_live`.
"""
from __future__ import annotations

KEYS: dict[str, str] = {
    "VTURN": "SMA9_VTURN",
    "TURN": "SMA9_TURN",
    "CANDLES": "COUNTER_CANDLES",
}
ENTRY_REASON = "EXIT_REVERSE"
MANUAL_REASON = "MANUAL_REVERSE"


def clean(value) -> str:
    """The setting as a tidy CSV of known keys, in a fixed order ("" = off)."""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        parts = [str(p) for p in value]
    else:
        parts = str(value).replace(";", ",").split(",")
    seen = {p.strip().upper() for p in parts if p and p.strip()}
    unknown = seen - set(KEYS)
    if unknown:
        raise ValueError(f"reverse_on_exit takes {', '.join(KEYS)}; not {', '.join(sorted(unknown))}")
    return ",".join(k for k in KEYS if k in seen)


def keys(cfg) -> set[str]:
    try:
        return set(filter(None, clean(getattr(cfg, "reverse_on_exit", "") or "").split(",")))
    except ValueError:
        return set()


def on(cfg, exit_reason: str) -> bool:
    """Does this exit reason reverse under the bot's / stock's setting?"""
    return any(KEYS[k] == exit_reason for k in keys(cfg))


def colour_exit_on(cfg) -> bool:
    return bool(getattr(cfg, "reverse_colour_exit", False))


def colour_exit_applies(cfg, entry_why: str) -> bool:
    """Does the reversed-trade colour exit cover a trade with this entry reason?"""
    return colour_exit_on(cfg) and (entry_why or "").startswith(ENTRY_REASON)


def opposite_signal(signal_side: str) -> str:
    """The signal that opens the other side of a trade whose signal side was `signal_side`."""
    return "BEARISH" if signal_side == "LONG" else "BULLISH"
