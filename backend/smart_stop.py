"""Smart stop (`stop_type = "SMART"`, PAPER and LIVE, per bot and per stock, replays too).

Owner's request: one switch, no numbers to guess. Every stock gets its own stop room, learned from how that
stock itself has been moving:

  1. At the entry, the stock's own 1-minute candles of the last `smart_days` sessions (3) are read, up to the
     entry minute. For a buy, every dip from a new high is measured (the high, then the lowest low before the
     price makes a new high again). Only dips that *came back* to a new high within `RECOVER_MINUTES` count:
     they are the wiggles the trend lived through. A short reads the bounces from new lows the same way.
  2. The room is the `smart_pct` (85th) percentile of those dips, in % of the price: most of the stock's normal
     pullbacks fit inside it. It is kept between `smart_min_pct` (0.3 %) and `smart_max_pct` (2 %), so it is never
     a stop the noise hits, nor a stop so far away that a real turn costs a big loss. With fewer than
     `MIN_SAMPLES` dips (a new listing, little history) the ATR stop's distance is used, kept inside the same bounds.
  3. The stop starts `room` from the fill and then follows the best price since the entry at `room` behind it,
     tighter only (it never moves back). Once the trade has run one room in its favour the stop is at least the
     entry plus the round-trip charges (no loss left). With `smart_follow` on (default) the room behind the best
     price shrinks as the profit grows, by `FOLLOW_STEP` (1/8) of the room for each room of profit past the first,
     down to `FOLLOW_FLOOR` (half): the further a trend has run, the more of its profit is kept.

Each stock's room is its own (worked out from its own candles at its own entry); nothing is shared between stocks.
The stop is moved on every price like the trailing stop, but only when it gains at least `MOVE_SHARE` of the room
(or one tick), so a LIVE Groww stop is not modified on every tick. LIVE moves go through `_move_stop`
(`modify_sl`, 2 s apart; a refused modify keeps the old stop; a position restored without its stop id stays at
its saved stop). A restart works the room out again from the same candles up to the entry minute, and the stop
resumes from its saved level. Hit on the price like any stop: `SMART_SL_HIT` (in LIVE Groww's stop fills it).
"""
from __future__ import annotations

import datetime as dt
import math

import pandas as pd

STOP_TYPE = "SMART"
HIT_REASON = "SMART_SL_HIT"

DEFAULT_PCT = 85.0
DEFAULT_DAYS = 3
DEFAULT_MIN_PCT = 0.3
DEFAULT_MAX_PCT = 2.0

RECOVER_MINUTES = 60  # a dip that takes longer than this to come back to a new extreme is not a "normal" wiggle
MIN_DIP_PCT = 0.03  # dips under this (a tick or two) are left out: they say nothing about the room a trade needs
MIN_SAMPLES = 5
FOLLOW_STEP = 0.125
FOLLOW_FLOOR = 0.5
MOVE_SHARE = 0.1

_IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def stop_on(cfg) -> bool:
    """The smart stop is this entry's stop (only while the stop itself is on)."""
    if not bool(getattr(cfg, "use_stop", True)):
        return False
    return str(getattr(cfg, "stop_type", "ATR") or "ATR").upper() == STOP_TYPE


def _num(cfg, key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(getattr(cfg, key, default))
    except (TypeError, ValueError):
        return default
    if value != value:  # NaN
        return default
    return max(lo, min(hi, value))


def _flag(value) -> bool:
    return True if value is None else bool(value)


def settings(cfg) -> dict:
    """The smart stop's numbers for this bot / stock (defaults when not set)."""
    lo = _num(cfg, "smart_min_pct", DEFAULT_MIN_PCT, 0.05, 10.0)
    hi = _num(cfg, "smart_max_pct", DEFAULT_MAX_PCT, 0.1, 10.0)
    return {
        "pct": _num(cfg, "smart_pct", DEFAULT_PCT, 50.0, 99.0),
        "days": int(_num(cfg, "smart_days", DEFAULT_DAYS, 1, 10)),
        "min_pct": min(lo, hi),
        "max_pct": max(lo, hi),
        "follow": _flag(getattr(cfg, "smart_follow", True)),
    }


def _sessions(tape: pd.DataFrame, before_ts: int | None, days: int) -> list[pd.DataFrame]:
    """The last `days` IST sessions of the 1-minute tape, candles that opened before `before_ts` only."""
    if tape is None or len(tape) == 0 or "ts" not in tape.columns:
        return []
    frame = tape
    if before_ts is not None:
        frame = frame[frame["ts"].astype("int64") < int(before_ts)]
    if len(frame) == 0:
        return []
    day = pd.to_datetime(frame["ts"].astype("int64"), unit="s", utc=True).dt.tz_convert(_IST).dt.date
    keys = list(dict.fromkeys(day.tolist()))[-days:]
    return [frame[day == key] for key in keys]


def dips(tape: pd.DataFrame | None, direction: str, before_ts: int | None = None, days: int = DEFAULT_DAYS) -> list[float]:
    """Pullbacks (in % of the extreme) the stock lived through: for a long, each dip from a new high that came
    back to a new high within RECOVER_MINUTES; for a short, each bounce from a new low that came back below it."""
    out: list[float] = []
    long = (direction or "LONG").upper() == "LONG"
    for session in _sessions(tape, before_ts, days):
        if len(session) < 3:
            continue
        highs = session["high"].astype(float).to_numpy()
        lows = session["low"].astype(float).to_numpy()
        stamps = session["ts"].astype("int64").to_numpy()
        ext = highs[0] if long else lows[0]
        ext_at = stamps[0]
        worst = None
        for hi, lo, at in zip(highs[1:], lows[1:], stamps[1:]):
            if not (hi == hi and lo == lo):
                continue
            new_extreme = hi > ext if long else lo < ext
            if new_extreme:
                if worst is not None and ext > 0:
                    depth = (ext - worst) / ext * 100 if long else (worst - ext) / ext * 100
                    if depth >= MIN_DIP_PCT and (at - ext_at) <= RECOVER_MINUTES * 60:
                        out.append(float(depth))
                ext = hi if long else lo
                ext_at = at
                worst = None
            else:
                against = lo if long else hi
                worst = against if worst is None else (min(worst, against) if long else max(worst, against))
    return out


def _percentile(values: list[float], pct: float) -> float:
    data = sorted(values)
    if not data:
        return 0.0
    k = (len(data) - 1) * pct / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return data[int(k)]
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def room(
    tape: pd.DataFrame | None,
    direction: str,
    price: float,
    cfg,
    before_ts: int | None = None,
    fallback: float | None = None,
) -> dict:
    """The stop room for one trade, in ₹ a share and in %, with how it was found.

    `fallback` is the ATR stop's distance in ₹, used when the candles hold too few dips."""
    s = settings(cfg)
    samples = dips(tape, direction, before_ts=before_ts, days=s["days"])
    if len(samples) >= MIN_SAMPLES:
        pct = _percentile(samples, s["pct"])
        source = "dips"
    elif fallback is not None and fallback > 0 and price > 0:
        pct = fallback / price * 100
        source = "atr"
    else:
        pct = s["min_pct"]
        source = "floor"
    clamped = max(s["min_pct"], min(s["max_pct"], pct))
    return {
        "pct": round(clamped, 4),
        "rs": price * clamped / 100 if price > 0 else 0.0,
        "raw_pct": round(pct, 4),
        "samples": len(samples),
        "source": source,
        "clamped": "min" if clamped > pct else ("max" if clamped < pct else ""),
    }


def follow_room(room_rs: float, profit: float, follow: bool) -> float:
    """How far behind the best price the stop sits for a trade `profit` ₹ a share in its favour."""
    if room_rs <= 0:
        return 0.0
    if not follow or profit <= room_rs:
        return room_rs
    runs = profit / room_rs - 1.0
    return room_rs * max(FOLLOW_FLOOR, 1.0 - FOLLOW_STEP * runs)


def want(direction: str, entry: float, best: float, room_rs: float, breakeven: float, follow: bool) -> float:
    """Where the stop belongs now (before rounding and before 'tighter only')."""
    long = (direction or "LONG").upper() == "LONG"
    profit = (best - entry) if long else (entry - best)
    behind = follow_room(room_rs, max(0.0, profit), follow)
    level = best - behind if long else best + behind
    if profit >= room_rs > 0:
        # A full room in its favour: no loss left (entry plus the round-trip charges).
        level = max(level, breakeven) if long else min(level, breakeven)
    return level


def worth_moving(old: float, new: float, room_rs: float, tick: float = 0.05) -> bool:
    """Only move a stop when it gains enough to matter (a LIVE stop is modified on Groww each time)."""
    return abs(new - old) >= max(tick, room_rs * MOVE_SHARE)


def describe(info: dict | None) -> str:
    """A short line for the signal line / blotter: '0.62 % room (85th of 41 dips)'."""
    if not info:
        return ""
    if info.get("source") == "dips":
        text = f"{info['pct']:.2f}% room ({int(info.get('samples') or 0)} dips read)"
    elif info.get("source") == "atr":
        text = f"{info['pct']:.2f}% room (too few dips: ATR stop distance)"
    else:
        text = f"{info['pct']:.2f}% room (no candles: the smallest room)"
    if info.get("clamped") == "min":
        text += ", raised to the smallest room"
    elif info.get("clamped") == "max":
        text += ", cut to the largest room"
    return text
