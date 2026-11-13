"""Candle colour: a separate rule for closing a trade (owner's request).

In candle-colour mode (`entry_mode = "COLOUR"`) one reading of the colour candle used to open a trade and close it.
With `colour_exit_on` (off by default; per bot and per stock, PAPER and LIVE, replays too) an open trade is judged
by its own rule instead, while entries keep the usual ones:

    colour_exit_count   1-5 candles of the other colour in a row (default 2)
    colour_exit_body    smallest body of each, % of the close (0 = any; default 0.2)
    colour_exit_inside  let "When the smaller candles inside agree" close a trade too (off by default)

`exit_cfg(cfg)` is the bot's settings seen through that rule (count, % body, no ATR / per-size / per-candle bodies,
inside candles only when ticked), read by the same `candle_patterns.colour_call`; candle size and Heikin Ashi colours
are the bot's own. When it closes a trade (COLOUR_TURN) the other side opens only if the entry rule asks for it on that
candle too; otherwise the stock stays flat until the entry rule says so. No order path of its own.
"""
from __future__ import annotations

COUNT_MAX = 5
BODY_MAX = 2.0
DEFAULT_COUNT = 2
DEFAULT_BODY = 0.2


def on(cfg) -> bool:
    return bool(getattr(cfg, "colour_exit_on", False))


def count(cfg) -> int:
    try:
        n = int(getattr(cfg, "colour_exit_count", DEFAULT_COUNT) or DEFAULT_COUNT)
    except (TypeError, ValueError):
        n = DEFAULT_COUNT
    return min(max(n, 1), COUNT_MAX)


def body(cfg) -> float:
    raw = getattr(cfg, "colour_exit_body", DEFAULT_BODY)
    try:
        b = float(DEFAULT_BODY if raw is None else raw)
    except (TypeError, ValueError):
        b = DEFAULT_BODY
    if b != b:
        b = DEFAULT_BODY
    return min(max(b, 0.0), BODY_MAX)


def inside(cfg) -> bool:
    return bool(getattr(cfg, "colour_exit_inside", False))


class _View:
    """The bot's settings with the exit rule laid over the colour fields; everything else reads through."""

    def __init__(self, cfg, over: dict):
        self._cfg = cfg
        self._over = over

    def __getattr__(self, name):
        over = self.__dict__.get("_over", {})
        if name in over:
            return over[name]
        return getattr(self.__dict__["_cfg"], name)


def exit_cfg(cfg):
    """`cfg` as the exit rule reads it (for `colour_call`)."""
    return _View(
        cfg,
        {
            "colour_count": count(cfg),
            "colour_min_body": body(cfg),
            "colour_body_unit": "PCT",
            "colour_body_by_tf": "",
            "colour_body_seq": "",
            "colour_inside_minutes": getattr(cfg, "colour_inside_minutes", 0) if inside(cfg) else 0,
        },
    )


def describe(cfg) -> str:
    n = count(cfg)
    words = f"{n} candle{'s' if n > 1 else ''} of the other colour in a row"
    if body(cfg) > 0:
        words += f", bodies at least {body(cfg):g}%"
    if inside(cfg):
        words += ", or one candle when its smaller candles all agree"
    return words
