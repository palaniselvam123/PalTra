"""Trend breakout handoff (breakout.py): the chop bot hands a stock back to the trend bot.

The reverse of recovery.py's handoff. Off by default (`breakout_handoff`), per bot and
per stock, PAPER and LIVE; meant for the chop bot (Bot 2), and switched on for every
stock recovery.py hands to Bot 2 (`recovery.HANDOFF_SETTINGS`).

On each closed candle of an armed stock (the bot's own candle, e.g. 5 minutes), a
**breakout** is:

    tall    height (high - low) >= `breakout_atr_mult` (1.8) x ATR
    and one of
    gap     |SMA fast - SMA slow| / SMA slow x 100 >= `breakout_gap_pct` (0.20) %
    volume  the candle's volume >= `breakout_volume_mult` (1.5) x the average of
            the previous 20 candles' volume (missing volume never counts)

On a breakout an open trade on that stock is cut at once (`BREAKOUT_STOP`), and the
stock leaves this bot's Trade list and is armed on Bot 3 (`TREND_BOT`) with Bot 3's
own settings. In LIVE the move waits until Groww shows no shares. A PAPER source never
arms a LIVE Bot 3, and the one-LIVE-bot-per-stock rule applies.

**Bot 3 enters on the breakout's side** (green candle: buy, red: sell) without waiting
for a new cross (`Arrival`, `StrategyEngine.queue_breakout_entry`): on its next pass it
runs the entry through `apply_signal` with every check of its own (filters, ADX, the
cut-off, the trade cap, one LIVE bot per stock), and never orders at the breakout
candle's high or low. The order waits as a tall-candle pullback (tall_pullback.py) at
the breakout candle's SMA fast or half-way level, whichever is nearer the close, for
Bot 3's `tall_expiry_candles` (2) candles; nothing rests on Groww's book. A flipped
stock orders at once, as in tall_pullback.py. Only live bots do this
(`bots.register` sets `breakout_arrival`); a replay moves the stock but plays one bot.

The candle already on the tape when the stock is armed is not read, so a stock just
handed over is not sent straight back on the candle that moved it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from indicators import minute_volume_stats

BREAKOUT_EXIT = "BREAKOUT_STOP"
TREND_BOT = 3

DEFAULT_ATR_MULT = 1.8
DEFAULT_GAP_PCT = 0.20
DEFAULT_VOLUME_MULT = 1.5
VOLUME_LOOKBACK = 20


def _num(cfg, name: str, default: float, low: float, high: float) -> float:
    raw = getattr(cfg, name, None)
    try:
        value = float(raw if raw is not None else default)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def on(cfg) -> bool:
    return bool(getattr(cfg, "breakout_handoff", False))


def atr_mult(cfg) -> float:
    return _num(cfg, "breakout_atr_mult", DEFAULT_ATR_MULT, 0.5, 10.0)


def gap_pct(cfg) -> float:
    return _num(cfg, "breakout_gap_pct", DEFAULT_GAP_PCT, 0.0, 5.0)


def volume_mult(cfg) -> float:
    return _num(cfg, "breakout_volume_mult", DEFAULT_VOLUME_MULT, 1.0, 10.0)


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) or not math.isfinite(out) else out


def check(bar, atr, sma_fast, sma_slow, volume, avg_volume, cfg) -> tuple[bool, str]:
    """(breakout?, note) for one closed candle. Missing numbers never make a breakout."""
    high, low = _f(bar.get("high")), _f(bar.get("low"))
    atr, fast, slow = _f(atr), _f(sma_fast), _f(sma_slow)
    if high is None or low is None or atr is None or atr <= 0:
        return False, "no candle range or ATR yet"
    height = high - low
    tall = height >= atr_mult(cfg) * atr
    gap = abs(fast - slow) / slow * 100 if fast is not None and slow not in (None, 0) else None
    wide = gap is not None and gap >= gap_pct(cfg)
    volume, avg_volume = _f(volume), _f(avg_volume)
    surge = volume is not None and avg_volume is not None and avg_volume > 0 and volume >= volume_mult(cfg) * avg_volume
    parts = [f"candle {height / atr:.1f}x ATR"]
    if gap is not None:
        parts.append(f"SMA gap {gap:.2f}%")
    if surge:
        parts.append(f"volume {volume / avg_volume:.1f}x average")
    note = ", ".join(parts)
    return tall and (wide or surge), note


@dataclass
class Arrival:
    """A stock handed to Bot 3 by a breakout: enter on its side at the pullback level."""

    direction: str  # LONG | SHORT
    limit: float
    made_at: float  # the handoff's time (epoch seconds, the engine's clock); dropped once stale


ARRIVAL_TTL_SEC = 15 * 60


def side(bar) -> str | None:
    """The breakout's side: a green candle buys, a red one sells."""
    o, c = _f(bar.get("open")), _f(bar.get("close"))
    if o is None or c is None or c == o:
        return None
    return "LONG" if c > o else "SHORT"


def arrive(to_bot: int, symbol: str, arrival: Arrival) -> str:
    """Give the live target bot its breakout entry. Never raises."""
    try:
        import bots

        engine = bots.engines().get(int(to_bot))
        if engine is None:
            return f"Bot {to_bot} is not running"
        engine.queue_breakout_entry(symbol, arrival)
        side_word = "buy" if arrival.direction == "LONG" else "sell"
        return f"Bot {to_bot} will {side_word} on a pullback to {arrival.limit:.2f}"
    except Exception as exc:  # noqa: BLE001
        return f"Bot {to_bot} entry not queued: {exc}"


def read(enriched: pd.DataFrame, cfg) -> tuple[bool, str]:
    """Judge the last closed candle (iloc[-2]) of an enriched frame (forming bar last)."""
    if enriched is None or len(enriched) < 3:
        return False, "too few candles"
    closed = enriched.iloc[:-1]
    bar = closed.iloc[-1]
    volume, average, _ratio = minute_volume_stats(closed, VOLUME_LOOKBACK)
    return check(bar, bar.get("atr_14"), bar.get("sma_9"), bar.get("sma_21"), volume, average, cfg)
