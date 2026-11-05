"""Hand a stock on after a stop-loss exit (`stop_handoff`, off by default; per bot and per stock, PAPER and LIVE, replays
too). Bookkeeping of where a stock trades next; no order is placed here.

The other hand-offs move a stock on a recovered loss / losing V-turn (→ Bot 2) or a trend breakout (→ Bot 3); a stop-loss
exit left the stock where it was. With this set, right after any stop closes a trade (ATR, trailing, Supertrend, VWAP,
staged plan, SMA-gap stop; not a target), the stock moves:

* ``BACK`` – back to the bot it first came from (`handoff_chain.moved()` live; the replayed bot in a backtest that plays
  the hand-offs). A stock that never moved stays.
* ``BOT2`` – to Bot 2, the choppy bot (with the chop recipe, like a recovery hand-off: `recovery.HANDOFF_SETTINGS`).
* ``BOT3`` – to Bot 3, the trend bot (its own settings).

With `stop_handoff_loss` (on by default) only a stop that closed at a loss before charges moves the stock; a trailing
stop that locked in a profit leaves it. The target bot waits for its next cross. The usual hand-off rules apply: in
LIVE only once Groww shows no shares, a PAPER trade never arms a LIVE bot, one LIVE bot per stock.
"""
from __future__ import annotations

CHOICES = ("OFF", "BACK", "BOT2", "BOT3")
KIND = "STOP_EXIT"
#: The exit reasons a stop books (strategy_engine._watch_stop); never a target.
STOP_REASONS = frozenset({"ATR_SL_HIT", "TSL_HIT", "ST_SL_HIT", "VWAP_SL_HIT", "PLAN_SL_HIT", "GAP_SL_HIT"})


def choice(cfg) -> str:
    raw = str(getattr(cfg, "stop_handoff", None) or "OFF").upper()
    return raw if raw in CHOICES else "OFF"


def on(cfg) -> bool:
    return choice(cfg) != "OFF"


def loss_only(cfg) -> bool:
    value = getattr(cfg, "stop_handoff_loss", None)
    return True if value is None else bool(value)


def target(cfg, current: int, origin: int | None) -> tuple[int | None, str]:
    """(bot to move to, or None to stay; why it stays)."""
    pick = choice(cfg)
    if pick == "OFF":
        return None, "off"
    if pick == "BACK":
        if origin is None or int(origin) == int(current):
            return None, f"stays on Bot {current}: it did not come from another bot"
        return int(origin), ""
    to_bot = 2 if pick == "BOT2" else 3
    if to_bot == int(current):
        return None, f"stays: already on Bot {to_bot}"
    return to_bot, ""
