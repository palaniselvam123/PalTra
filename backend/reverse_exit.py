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

**Reversed trades' own settings** (`reverse_settings`, JSON, "" = none; per bot and per stock, PAPER and LIVE, replays
too): settings used only for the reverse order and while that reversed trade is open, on top of the bot's / stock's own
(`overlay`, `StrategyEngine._trade_cfg`). Only the fields in `FIELDS` can be set, in four groups: the entry checks (VWAP,
RSI ranges, ADX, cumulative delta; read when the reverse is asked for, so a check switched off here orders the reverse
without it), the counter-candle exit, the other exits (Bollinger, Supertrend, SMA 9 V-turn, SMA cross) and the trailing
stop / target numbers (read while the stop type is TSL). A field left out keeps the bot's value. Values are checked by
the terminal's own `ConfigUpdate`. A manual Reverse is not covered.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

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


GROUPS: dict[str, tuple[str, ...]] = {
    "entry": (
        "use_vwap", "use_rsi", "rsi_long_min", "rsi_long_max", "rsi_short_min", "rsi_short_max",
        "use_adx_filter", "adx_threshold", "use_cvd", "cvd_lookback",
    ),
    "counter": ("counter_exit_count", "counter_rule", "counter_need_fast", "counter_min_body"),
    "exits": ("bb_exit", "st_exit", "vturn_exit", "cross_exit"),
    "stop": (
        "tsl_mode", "tsl_sl_points", "tsl_trail_points", "tsl_target_points", "tsl_sl_pct", "tsl_trail_pct",
        "tsl_target_pct", "tsl_sl_atr", "tsl_trail_atr", "tsl_target_atr",
    ),
}
FIELDS: tuple[str, ...] = tuple(f for group in GROUPS.values() for f in group)


def parse_settings(value) -> dict:
    """The saved setting as a dict of known fields ({} when off or unreadable)."""
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k in FIELDS and v is not None}


def clean_settings(value, check=None) -> str:
    """The setting as canonical JSON ("" = none). Unknown fields are refused; `check(fields)` (the terminal's
    ConfigUpdate) checks the values and returns them cleaned."""
    if value is None or value == "":
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("reverse_settings must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError("reverse_settings must be a JSON object")
    unknown = sorted(k for k in value if k not in FIELDS)
    if unknown:
        raise ValueError(f"reverse_settings cannot set {', '.join(unknown)}")
    fields = {k: v for k, v in value.items() if v is not None}
    if check is not None:
        fields = check(fields)
    for side in ("long", "short"):
        lo, hi = fields.get(f"rsi_{side}_min"), fields.get(f"rsi_{side}_max")
        if lo is not None and hi is not None and float(lo) > float(hi):
            raise ValueError(f"reverse_settings: {side} RSI low must be at or below the high")
    if not fields:
        return ""
    return json.dumps({k: fields[k] for k in FIELDS if k in fields}, separators=(",", ":"))


def own_settings(cfg) -> dict:
    return parse_settings(getattr(cfg, "reverse_settings", None))


def overlay(cfg, groups: tuple[str, ...] | None = None):
    """`cfg` with the reversed trades' own settings on top (a copy; `cfg` itself is not changed). `groups` limits it
    to those groups' fields."""
    own = own_settings(cfg)
    if groups is not None:
        keep = {f for g in groups for f in GROUPS.get(g, ())}
        own = {k: v for k, v in own.items() if k in keep}
    if not own or cfg is None:
        return cfg
    try:
        from models import BotConfig
    except ImportError:  # pragma: no cover
        BotConfig = None
    if BotConfig is not None and isinstance(cfg, BotConfig):
        data = {col.name: getattr(cfg, col.name) for col in BotConfig.__table__.columns}
        data.update(own)
        return BotConfig(**data)
    base = {name: getattr(cfg, name) for name in dir(cfg) if not name.startswith("_") and not callable(getattr(cfg, name, None))}
    return SimpleNamespace(**{**base, **own})


def is_reversed(entry_why: str | None) -> bool:
    return (entry_why or "").startswith(ENTRY_REASON)


def colour_exit_on(cfg) -> bool:
    return bool(getattr(cfg, "reverse_colour_exit", False))


def colour_exit_applies(cfg, entry_why: str) -> bool:
    """Does the reversed-trade colour exit cover a trade with this entry reason?"""
    return colour_exit_on(cfg) and (entry_why or "").startswith(ENTRY_REASON)


def opposite_signal(signal_side: str) -> str:
    """The signal that opens the other side of a trade whose signal side was `signal_side`."""
    return "BEARISH" if signal_side == "LONG" else "BULLISH"
