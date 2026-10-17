"""Strong trend read (trend_strength.py): is this stock in a clean, one-way trend?

Read-only, no order path. Used by the Chop scan (`chop_scan.analyze`) to tag
each stock `STRONG_BULLISH` / `STRONG_BEARISH` and score it out of seven.

Two modes, chosen by the time of the read (IST):

**Morning mode (before 10:00).** Judged on today's own candles since 09:15 only,
so the first 15–60 minutes are not read against yesterday's closes:

    stack     price beyond the fast SMA, fast beyond the slow SMA (price > fast > slow up)
    vwap      price and the fast SMA on the trend side of today's VWAP
    slope     the fast SMA moved with the trend since the first candle today (2+ candles)
    fan       |SMA gap| at least MORNING_FAN_MIN_PCT % on the trend side
    hold      closes on the trend side of the fast SMA: all of them up to 09:30 (3 of 3),
              then at least HOLD_MIN_SHARE of today's closes
    run       a fresh SMA cross today is allowed; with no cross the lines must already be
              on the trend side. Yesterday's crosses never count, so this never penalises
              an early-morning cross
    volume    today's cumulative volume since 09:15 is at least VOLUME_MIN_RVOL times the
              average for the same minutes on earlier sessions (up to 10 days in the tape).
              Needs at least 2 earlier sessions; with fewer it does not pass

Morning labels show at least STRONG_MORNING_MIN of the seven: `Strong`.

**Midday mode (10:00 onwards).** The mature 12-candle rules:

    stack, vwap, slope, fan, hold, structure, run   as described below

`stack`, `vwap` and `slope` are required in midday; the label is given when those
pass and at least STRONG_MIN_PASSED of the seven pass. `score` is the share of the
seven checks that passed (0-100). Nothing in the strategy reads it.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from indicators import derive_minute_volume, sma_gap_pct

IST = ZoneInfo("Asia/Kolkata")

STRONG_BULLISH = "STRONG_BULLISH"
STRONG_BEARISH = "STRONG_BEARISH"

MORNING_END = dt.time(10, 0)  # before this, the morning rules
MIDDAY = "MIDDAY"
MORNING = "MORNING"

# Midday (the mature rules)
LOOKBACK = 12  # closed candles judged for hold / structure / run
SLOPE_BARS = 3
FAN_MIN_PCT = 0.10
HOLD_MIN_SHARE = 0.75
STRONG_MIN_PASSED = 6
REQUIRED = ("stack", "vwap", "slope")
CHECKS = ("stack", "vwap", "slope", "fan", "hold", "structure", "run")

# Morning
MORNING_FAN_MIN_PCT = 0.10
MORNING_HOLD_ALL_UNTIL = 3  # candles: 09:15, 09:20, 09:25 (all must close on the trend side by 09:30)
STRONG_MORNING_MIN = 5  # 5 of 7 is "Strong" before 10:00
VOLUME_MIN_RVOL = 1.5
VOLUME_LOOKBACK_SESSIONS = 10
VOLUME_MIN_SESSIONS = 2
MORNING_CHECKS = ("stack", "vwap", "slope", "fan", "hold", "run", "volume")

NOTES_MIDDAY = {
    "stack": "Price, fast SMA and slow SMA stacked one way",
    "vwap": "Price and slow SMA on the trend side of VWAP",
    "slope": "Both SMAs moved with the trend over 3 candles",
    "fan": "SMA gap at least 0.10% and not narrowed by a third",
    "hold": "Most of the last 12 closes on the trend side of the fast SMA",
    "structure": "Lower highs and lower lows (higher for bullish) over the last 12 candles",
    "run": "No SMA cross in the last 12 candles",
}


def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def _ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(int(ts), IST).date()


def _ist_time(ts: int) -> dt.time:
    return dt.datetime.fromtimestamp(int(ts), IST).time()


def read(closed: pd.DataFrame, vwap: float | None, now: dt.datetime | None = None, tape: pd.DataFrame | None = None) -> dict:
    """Trend read of enriched closed candles (`sma_9`, `sma_21`, OHLC).

    `now` picks the mode (before 10:00 IST: morning). `tape` is the 1-minute candles
    (with the running session volume) for the morning volume check. Never raises on short data.
    Without `now` the midday rules apply, as before.
    """
    if now is not None and _ist_time(int(now.timestamp())) < MORNING_END:
        return _morning(closed, vwap, now, tape)
    return _midday(closed, vwap)


def _empty(mode: str) -> dict:
    return {
        "trend": None,
        "trend_side": None,
        "trend_score": 0,
        "trend_checks": {},
        "trend_mode": mode,
        "trend_notes": {},
        "sma_gap_pct": None,
        "vwap": None,
        "rvol": None,
        "trend_days": 0,
    }


def _morning(closed: pd.DataFrame, vwap: float | None, now: dt.datetime, tape: pd.DataFrame | None) -> dict:
    empty = _empty(MORNING)
    if closed is None or len(closed) == 0:
        return empty
    today_date = now.astimezone(IST).date() if now.tzinfo else now.replace(tzinfo=IST).date()
    dates = [_ist_date(ts) for ts in closed["ts"]]
    mask = [d == today_date for d in dates]
    today = closed[mask]
    n = len(today)
    if n == 0:
        return empty
    last = today.iloc[-1]
    close, fast, slow = _f(last.get("close")), _f(last.get("sma_9")), _f(last.get("sma_21"))
    gap = sma_gap_pct(fast, slow) if fast is not None and slow is not None else None
    if close is None or gap is None or gap == 0:
        return {**empty, "vwap": round(vwap, 2) if vwap is not None else None}
    up = gap > 0
    sign = 1 if up else -1

    def beyond(a, b) -> bool:
        return a is not None and b is not None and (a - b) * sign > 0

    fasts = [_f(v) for v in today["sma_9"]]
    closes = [_f(v) for v in today["close"]]
    slows = [_f(v) for v in today["sma_21"]]
    # Fast SMA slope since the first candle today (or the last few if more candles than that).
    slope_ok = False
    if n >= 2:
        base = fasts[max(0, n - 1 - SLOPE_BARS)]
        slope_ok = beyond(fasts[-1], base)
    # Closes on the trend side of the fast SMA, today only.
    side_closes = [beyond(c, f) for c, f in zip(closes, fasts)]
    share = sum(side_closes) / n
    hold_ok = (sum(side_closes) == n) if n <= MORNING_HOLD_ALL_UNTIL else share >= HOLD_MIN_SHARE
    # Crosses today between consecutive candles (yesterday's never count).
    diffs = [(f - s) if f is not None and s is not None else None for f, s in zip(fasts, slows)]
    crossed_today = any(
        diffs[i] is not None and diffs[i - 1] is not None and (diffs[i] > 0) != (diffs[i - 1] > 0)
        for i in range(1, n)
    )
    on_side_all_morning = all(d is not None and d * sign > 0 for d in diffs)
    run_ok = crossed_today or on_side_all_morning
    # Volume since 09:15 against the same minutes on earlier sessions.
    rvol, days = _opening_rvol(tape, now)
    volume_ok = rvol is not None and rvol >= VOLUME_MIN_RVOL and days >= VOLUME_MIN_SESSIONS
    checks = {
        "stack": beyond(close, fast) and beyond(fast, slow),
        "vwap": vwap is not None and beyond(close, vwap) and beyond(fast, vwap),
        "slope": slope_ok,
        "fan": abs(gap) >= MORNING_FAN_MIN_PCT and beyond(fast, slow),
        "hold": hold_ok,
        "run": run_ok,
        "volume": volume_ok,
    }
    passed = sum(checks.values())
    side = "BULLISH" if up else "BEARISH"
    word = "above" if up else "below"
    notes = {
        "stack": "Price above fast SMA above slow SMA" if up else "Price below fast SMA below slow SMA",
        "vwap": f"Price and fast SMA {word} VWAP",
        "slope": "Fast SMA moving with the trend since the open" if n >= 2 else "Needs 2 candles today",
        "fan": f"SMA gap at least {MORNING_FAN_MIN_PCT:g}% on the trend side",
        "hold": (
            f"{sum(side_closes)} of {n} closes on the trend side today"
            + (" (needs all of them until 09:30)" if n <= MORNING_HOLD_ALL_UNTIL else " (needs 75%)")
        ),
        "run": "Fresh cross today is allowed" if crossed_today else "Lines on the trend side since the open",
        "volume": (
            f"Volume {rvol:.1f}× the usual for this time (needs {VOLUME_MIN_RVOL:g}×, {days} earlier sessions)"
            if rvol is not None and days >= VOLUME_MIN_SESSIONS
            else "Volume: not enough earlier sessions to compare"
        ),
    }
    strong = passed >= STRONG_MORNING_MIN
    return {
        "trend": (STRONG_BULLISH if up else STRONG_BEARISH) if strong else None,
        "trend_side": side,
        "trend_score": round(passed / len(MORNING_CHECKS) * 100),
        "trend_checks": checks,
        "trend_mode": MORNING,
        "trend_notes": notes,
        "sma_gap_pct": round(gap, 4),
        "vwap": round(vwap, 2) if vwap is not None else None,
        "rvol": round(rvol, 2) if rvol is not None else None,
        "trend_days": days,
    }


def _opening_rvol(tape: pd.DataFrame | None, now: dt.datetime) -> tuple[float | None, int]:
    """Today's volume since 09:15 up to `now`, against the same minutes on earlier sessions.

    Returns (ratio, earlier sessions used). The ratio is None when today has no volume yet.
    """
    if tape is None or len(tape) == 0 or "volume" not in tape.columns or "ts" not in tape.columns:
        return None, 0
    df = tape.sort_values("ts")
    minute_vol = derive_minute_volume(df)
    ts = df["ts"].astype("int64")
    local = pd.to_datetime(ts, unit="s", utc=True).dt.tz_convert(IST)
    times = local.dt.time
    today_date = now.astimezone(IST).date() if now.tzinfo else now.replace(tzinfo=IST).date()
    now_time = (now.astimezone(IST) if now.tzinfo else now.replace(tzinfo=IST)).time()
    in_span = (times >= dt.time(9, 15)) & (times <= now_time)
    sessions = local.dt.date
    today_sum = minute_vol[in_span & (sessions == today_date)].dropna()
    if today_sum.empty:
        return None, 0
    earlier_totals: list[float] = []
    for day in sorted({d for d in sessions if d < today_date}, reverse=True)[:VOLUME_LOOKBACK_SESSIONS]:
        mins = minute_vol[in_span & (sessions == day)].dropna()
        if len(mins):
            earlier_totals.append(float(mins.sum()))
    if not earlier_totals:
        return None, 0
    usual = sum(earlier_totals) / len(earlier_totals)
    if usual <= 0:
        return None, len(earlier_totals)
    return float(today_sum.sum()) / usual, len(earlier_totals)


def _midday(closed: pd.DataFrame, vwap: float | None) -> dict:
    empty = _empty(MIDDAY)
    if closed is None or len(closed) < LOOKBACK + SLOPE_BARS:
        return empty
    last = closed.iloc[-1]
    close, fast, slow = _f(last.get("close")), _f(last.get("sma_9")), _f(last.get("sma_21"))
    gap = sma_gap_pct(fast, slow) if fast is not None and slow is not None else None
    if close is None or gap is None or gap == 0:
        return empty
    up = gap > 0
    sign = 1 if up else -1

    def beyond(a: float | None, b: float | None) -> bool:
        return a is not None and b is not None and (a - b) * sign > 0

    back = closed.iloc[-1 - SLOPE_BARS]
    fast_then, slow_then = _f(back.get("sma_9")), _f(back.get("sma_21"))
    gap_then = sma_gap_pct(fast_then, slow_then) if fast_then is not None and slow_then is not None else None
    recent = closed.iloc[-LOOKBACK:]
    side_closes = [
        beyond(_f(c), _f(f)) for c, f in zip(recent["close"], recent["sma_9"])
    ]
    gaps = [sma_gap_pct(f, s) for f, s in zip(closed["sma_9"].iloc[-LOOKBACK - 1 :], closed["sma_21"].iloc[-LOOKBACK - 1 :])]
    signs = [g > 0 for g in gaps if g is not None and g != 0]
    half = LOOKBACK // 2
    older, newer = recent.iloc[:half], recent.iloc[half:]
    if up:
        structure = newer["high"].max() > older["high"].max() and newer["low"].min() > older["low"].min()
    else:
        structure = newer["high"].max() < older["high"].max() and newer["low"].min() < older["low"].min()
    checks = {
        "stack": beyond(close, fast) and beyond(fast, slow),
        "vwap": vwap is not None and beyond(close, vwap) and beyond(slow, vwap),
        "slope": beyond(fast, fast_then) and beyond(slow, slow_then),
        "fan": abs(gap) >= FAN_MIN_PCT and gap_then is not None and abs(gap) >= abs(gap_then) * 2 / 3,
        "hold": sum(side_closes) >= HOLD_MIN_SHARE * len(side_closes),
        "structure": bool(structure),
        "run": len(signs) >= LOOKBACK and all(s == up for s in signs),
    }
    passed = sum(checks.values())
    strong = all(checks[k] for k in REQUIRED) and passed >= STRONG_MIN_PASSED
    side = "BULLISH" if up else "BEARISH"
    return {
        "trend": (STRONG_BULLISH if up else STRONG_BEARISH) if strong else None,
        "trend_side": side,
        "trend_score": round(passed / len(CHECKS) * 100),
        "trend_checks": checks,
        "trend_mode": MIDDAY,
        "trend_notes": dict(NOTES_MIDDAY),
        "sma_gap_pct": round(gap, 4),
        "vwap": round(vwap, 2) if vwap is not None else None,
        "rvol": None,
        "trend_days": 0,
    }
