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

from candles import resample
from indicators import atr_wilder

STOP_TYPE = "SMART"
HIT_REASON = "SMART_SL_HIT"

DEFAULT_PCT = 85.0
DEFAULT_DAYS = 3
DEFAULT_MIN_PCT = 0.3
DEFAULT_MAX_PCT = 2.0

RECOVER_MINUTES = 60  # a dip that takes longer than this to come back to a new extreme is not a "normal" wiggle
RECOVER_CANDLES = 12  # ... or, on longer candles, longer than this many of them
ATR_FLOOR = 1.0  # the room is never under this many ATRs of the candles the bot trades on (one candle's normal swing)
ATR_PERIOD = 14
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
        "live": _flag(getattr(cfg, "smart_live", True)),
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


def _candles(tape: pd.DataFrame | None, before_ts: int | None, days: int, minutes: int) -> list[pd.DataFrame]:
    """The last `days` sessions in candles of `minutes` (grouped from 09:15), only candles closed before `before_ts`."""
    out = []
    for session in _sessions(tape, before_ts, days):
        frame = resample(session, minutes) if minutes > 1 else session
        if frame is None or len(frame) == 0:
            continue
        if before_ts is not None and minutes > 1:
            # A candle still forming at the entry minute is not read.
            frame = frame[frame["ts"].astype("int64") + minutes * 60 <= int(before_ts)]
        out.append(frame)
    return out


def candle_atr(tape: pd.DataFrame | None, before_ts: int | None, days: int, minutes: int) -> float | None:
    """The Wilder ATR of the candles the bot trades on, the last one closed before the entry."""
    frames = [f for f in _candles(tape, before_ts, max(days, 2), minutes) if len(f)]
    if not frames:
        return None
    joined = pd.concat(frames, ignore_index=True)
    if len(joined) < ATR_PERIOD + 1:
        return None
    try:
        value = float(atr_wilder(joined, ATR_PERIOD).iloc[-1])
    except Exception:  # noqa: BLE001
        return None
    return value if value == value and value > 0 else None


def dips(
    tape: pd.DataFrame | None, direction: str, before_ts: int | None = None, days: int = DEFAULT_DAYS, minutes: int = 1
) -> list[float]:
    """Pullbacks (in % of the extreme) the stock lived through on candles of `minutes`: for a long, each dip from a
    new high that came back to a new high in time; for a short, each bounce from a new low that came back below it."""
    out: list[float] = []
    long = (direction or "LONG").upper() == "LONG"
    window = max(RECOVER_MINUTES, RECOVER_CANDLES * max(1, int(minutes))) * 60
    for session in _candles(tape, before_ts, days, max(1, int(minutes))):
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
                    if depth >= MIN_DIP_PCT and (at - ext_at) <= window:
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
    minutes: int = 1,
) -> dict:
    """The stop room for one trade, in ₹ a share and in %, with how it was found.

    Read on the candles the bot trades on (`minutes`): a 15-minute strategy needs the room of 15-minute swings, not
    of 1-minute wiggles. Never under ATR_FLOOR × the ATR of those candles. `fallback` (₹) is used with too few dips."""
    s = settings(cfg)
    minutes = max(1, int(minutes or 1))
    samples = dips(tape, direction, before_ts=before_ts, days=s["days"], minutes=minutes)
    atr = candle_atr(tape, before_ts, s["days"], minutes)
    atr_pct = atr / price * 100 if atr and price > 0 else None
    if len(samples) >= MIN_SAMPLES:
        pct = _percentile(samples, s["pct"])
        source = "dips"
    elif atr_pct is not None:
        pct = 1.5 * atr_pct
        source = "atr"
    elif fallback is not None and fallback > 0 and price > 0:
        pct = fallback / price * 100
        source = "atr"
    else:
        pct = s["min_pct"]
        source = "floor"
    floor_hit = False
    if atr_pct is not None and pct < ATR_FLOOR * atr_pct:
        pct = ATR_FLOOR * atr_pct
        floor_hit = True
    clamped = max(s["min_pct"], min(s["max_pct"], pct))
    return {
        "minutes": minutes,
        "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
        "atr_floor": floor_hit,
        "pct": round(clamped, 4),
        "rs": price * clamped / 100 if price > 0 else 0.0,
        "raw_pct": round(pct, 4),
        "samples": len(samples),
        "source": source,
        "clamped": "min" if clamped > pct else ("max" if clamped < pct else ""),
    }


RESIZE_NOTE_SHARE = 0.1  # a re-sized room is written to the trade's stop log when it moved at least this share


def resized(old: float | None, new: float) -> bool:
    """The room changed enough to note on the trade."""
    if not old or old <= 0:
        return True
    return abs(new - old) / old >= RESIZE_NOTE_SHARE


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
    size = f"{int(info.get('minutes') or 1)}-min"
    if info.get("source") == "dips":
        text = f"{info['pct']:.2f}% room ({int(info.get('samples') or 0)} dips on {size} candles)"
    elif info.get("source") == "atr":
        text = f"{info['pct']:.2f}% room (too few dips: 1.5 × the {size} ATR)"
    else:
        text = f"{info['pct']:.2f}% room (no candles: the smallest room)"
    if info.get("atr_floor") and not info.get("clamped"):
        text += f", raised to one {size} candle's swing (1 ATR)"
    if info.get("clamped") == "min":
        text += ", raised to the smallest room"
    elif info.get("clamped") == "max":
        text += ", cut to the largest room"
    return text
