"""Nifty direction check (index_check.py): take a trade only while the market goes the same way.

Off by default (`use_index`), per bot and per stock, PAPER and LIVE, replays too; entries only (owner's request: most
intraday losses come from buying a stock while Nifty is falling). Read in `StrategyEngine.apply_signal` next to the other
checks that need more than the stock's own candles; a refusal joins the entry checks' line, so late entry can wait for
the market to turn. Direct orders (Force, news, hand-offs) never read it.

The index is Nifty 50 (`INDEX`), read from its own 1-minute candles (the bots' quote client, a replay's cached candles).
An index has no traded volume, so there is no VWAP for it; the rule (`index_rule`) is one of:

* `DAY`  – a buy only while Nifty is above today's first open, a sell only while below.
* `SMA`  – a buy only while Nifty's SMA fast is above its SMA slow on the last closed candle of the size the bot's
           exits read (the bot's own SMA periods), a sell only while below. The default.
* `BOTH` – both of the above must agree.

Nifty not loaded (no Groww session, candles missing) refuses the entry and says so, like the other checks.
"""
from __future__ import annotations

import pandas as pd

from candles import resample

INDEX = "NIFTY"
NAME = "Nifty"
RULES = ("SMA", "DAY", "BOTH")
DEFAULT_RULE = "SMA"
IST_OFFSET = 19_800


def on(cfg) -> bool:
    return bool(getattr(cfg, "use_index", False))


def rule(cfg) -> str:
    value = str(getattr(cfg, "index_rule", None) or DEFAULT_RULE).upper()
    return value if value in RULES else DEFAULT_RULE


def day_move(tape: pd.DataFrame | None) -> float | None:
    """The index's move in % from the first open of the latest session in `tape` to its last price."""
    if tape is None or getattr(tape, "empty", True) or not {"ts", "open", "close"} <= set(tape.columns):
        return None
    ts = tape["ts"].astype("int64")
    today = tape[(ts + IST_OFFSET) // 86_400 == (int(ts.iloc[-1]) + IST_OFFSET) // 86_400]
    first = float(today["open"].iloc[0]) if len(today) else 0.0
    return (float(today["close"].iloc[-1]) / first - 1) * 100 if first > 0 else None


def sma_side(tape: pd.DataFrame | None, cfg, minutes: int) -> tuple[str | None, float | None]:
    """(LONG / SHORT / None, signed gap %) of the index's SMA fast vs slow on its last closed `minutes` candle."""
    if tape is None or getattr(tape, "empty", True) or "close" not in tape:
        return None, None
    bars = resample(tape, max(1, int(minutes or 1)))
    closed = bars.iloc[:-1]
    fast, slow = int(getattr(cfg, "sma_fast", 9) or 9), int(getattr(cfg, "sma_slow", 21) or 21)
    if len(closed) < slow:
        return None, None
    close = pd.to_numeric(closed["close"], errors="coerce")
    f, s = float(close.tail(fast).mean()), float(close.tail(slow).mean())
    if not s or f == s or pd.isna(f) or pd.isna(s):
        return None, None
    return ("LONG" if f > s else "SHORT"), (f - s) / s * 100


def read(tape: pd.DataFrame | None, cfg, minutes: int) -> dict:
    """What the check reads now: the day's move and the SMA side (either can be None when not known yet)."""
    side, gap = sma_side(tape, cfg, minutes)
    return {"day_pct": day_move(tape), "sma_side": side, "sma_gap_pct": gap, "minutes": max(1, int(minutes or 1))}


def entry_reason(tape: pd.DataFrame | None, direction: str, cfg, minutes: int) -> str | None:
    """Why the market refuses this entry, or None when Nifty goes the trade's way."""
    got = read(tape, cfg, minutes)
    which = rule(cfg)
    want_up = direction == "LONG"
    word = "buy" if want_up else "sell"
    if which in ("DAY", "BOTH"):
        move = got["day_pct"]
        if move is None:
            return f"{NAME} check: {NAME} is not loaded yet"
        if (move > 0) != want_up or move == 0:
            return f"{NAME} check: {NAME} is {move:+.2f}% on the day, against a {word}"
    if which in ("SMA", "BOTH"):
        side = got["sma_side"]
        if side is None:
            return f"{NAME} check: {NAME}'s averages are not formed yet on {got['minutes']}-min candles"
        if side != direction:
            return (
                f"{NAME} check: {NAME}'s SMA fast is {'below' if want_up else 'above'} its SMA slow "
                f"on {got['minutes']}-min candles ({got['sma_gap_pct']:+.2f}%), against a {word}"
            )
    return None


def wanted(settings: dict | None) -> bool:
    """Does a run's saved settings (the bot's, or any stock's own) switch the check on? (replays load Nifty then)"""
    if not isinstance(settings, dict):
        return False
    if settings.get("use_index"):
        return True
    own = settings.get("stock_settings")
    return isinstance(own, dict) and any(isinstance(v, dict) and v.get("use_index") for v in own.values())
