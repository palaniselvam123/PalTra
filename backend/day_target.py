"""Day high / low target (day_target.py; owner's request: take the profit where the stock turned before).

`day_target` (off by default; per bot and per stock, PAPER and LIVE, replays too). Two levels count, whichever the
price reaches first:

  * today's high / low made before the entry (1-minute candles that began before the entry minute), and
  * yesterday's high / low (the last session before today in the stock's 1-minute tape).

A long closes at the nearest high above the entry, a short at the nearest low below it, only for a level at least
`day_target_min_pct` (0.2, 0-10) % beyond the entry, so the trade is in profit when it closes. `day_target_early_pct`
(0, 0-2) closes that % before the level (a touch that just misses still counts). Worked out once per trade from the
candles (a restart finds the same level), checked on every price by `StrategyEngine._watch_day_target`, closed
through `_exit_now` (`DAY_LEVEL_TARGET`; a LIVE stop is cancelled first, a LIVE position restored without its stop id
is left to that stop). The other stops and targets stay on: whichever comes first closes.

**Ignore early highs** (`day_target_skip_min`, 0 = off, 0-180; owner's DATAPATTNS case: a 09:30 buy closed 30 s later at
the opening run's own 09:28 top, 13 points away, then the stock ran 200 points): today's high / low counts only from
candles that began at least that many minutes after 09:15, so the first minutes' run on a gap day is not taken for a
level. Yesterday's high / low is unchanged. Pure helpers.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")
EXIT_REASON = "DAY_LEVEL_TARGET"
DEFAULT_MIN_PCT = 0.2
MAX_MIN_PCT = 10.0
MAX_EARLY_PCT = 2.0
MAX_SKIP_MIN = 180
OPEN = dt.time(9, 15)


def on(cfg) -> bool:
    return bool(getattr(cfg, "day_target", False))


def _pct(cfg, name: str, default: float, top: float) -> float:
    try:
        raw = getattr(cfg, name, None)
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return default
    if value != value:
        return default
    return max(0.0, min(top, value))


def min_pct(cfg) -> float:
    return _pct(cfg, "day_target_min_pct", DEFAULT_MIN_PCT, MAX_MIN_PCT)


def early_pct(cfg) -> float:
    return _pct(cfg, "day_target_early_pct", 0.0, MAX_EARLY_PCT)


def skip_min(cfg) -> int:
    try:
        n = int(float(getattr(cfg, "day_target_skip_min", 0) or 0))
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_SKIP_MIN, n))


def levels(tape: pd.DataFrame | None, entry_ts: float, skip: int = 0) -> dict | None:
    """{"today_high", "today_low", "prev_high", "prev_low", "prev_day"} (any may be None) from 1-minute candles
    (ts / high / low) that began before the entry minute; today's only from candles that began `skip` minutes or more
    after 09:15. None without candles."""
    if tape is None or getattr(tape, "empty", True) or not {"ts", "high", "low"} <= set(tape.columns):
        return None
    ts = pd.to_numeric(tape["ts"], errors="coerce")
    minute = int(entry_ts) // 60 * 60
    before = tape[ts < minute]
    if before.empty:
        return None
    days = pd.to_datetime(pd.to_numeric(before["ts"]), unit="s", utc=True).dt.tz_convert(IST).dt.date
    today = dt.datetime.fromtimestamp(int(entry_ts), IST).date()
    out = {"today_high": None, "today_low": None, "prev_high": None, "prev_low": None, "prev_day": None}
    now_rows = before[days == today]
    if skip > 0 and not now_rows.empty:
        start = dt.datetime.combine(today, OPEN, IST) + dt.timedelta(minutes=skip)
        now_rows = now_rows[pd.to_numeric(now_rows["ts"]) >= start.timestamp()]
    if not now_rows.empty:
        out["today_high"] = float(pd.to_numeric(now_rows["high"]).max())
        out["today_low"] = float(pd.to_numeric(now_rows["low"]).min())
    earlier = sorted({d for d in days if d < today})
    if earlier:
        prev = before[days == earlier[-1]]
        out["prev_high"] = float(pd.to_numeric(prev["high"]).max())
        out["prev_low"] = float(pd.to_numeric(prev["low"]).min())
        out["prev_day"] = earlier[-1].isoformat()
    return out


def target_for(direction: str, entry: float, lv: dict | None, cfg) -> tuple[str, float, float] | None:
    """(name, level, price that closes the trade) for the nearest level beyond the entry by at least the minimum, or
    None. Names: "today's high" / "yesterday's high" (lows for a short)."""
    if lv is None or not entry > 0:
        return None
    floor = min_pct(cfg) / 100.0
    early = early_pct(cfg) / 100.0
    if direction == "LONG":
        found = [(n, lv[k]) for n, k in (("today's high", "today_high"), ("yesterday's high", "prev_high"))
                 if lv.get(k) is not None and lv[k] >= entry * (1 + floor) and lv[k] > entry]
        if not found:
            return None
        name, level = min(found, key=lambda x: x[1])
        return name, level, max(entry, level * (1 - early))
    found = [(n, lv[k]) for n, k in (("today's low", "today_low"), ("yesterday's low", "prev_low"))
             if lv.get(k) is not None and lv[k] <= entry * (1 - floor) and lv[k] < entry]
    if not found:
        return None
    name, level = max(found, key=lambda x: x[1])
    return name, level, min(entry, level * (1 + early))


def reached(direction: str, price: float, trigger: float) -> bool:
    return price >= trigger if direction == "LONG" else price <= trigger
