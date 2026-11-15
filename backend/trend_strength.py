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

**Midday mode (10:00 onwards).** Only today's candles, and only the current run: the candles since the SMAs took
this side today (at least RUN_MIN, at most LOOKBACK). Yesterday's trend is not today's, and a trend is judged from
where it began, not on a fixed hour of candles (the old 12-candle window called a trend an hour after it began,
three hours on 15-minute candles, and kept the label after the price had stopped):

    stack      price beyond the fast SMA, fast beyond the slow SMA
    vwap       price and the fast SMA on the trend side of today's VWAP
    slope      both SMAs moved with the trend over SLOPE_BARS candles
    fan        |SMA gap| at least FAN_MIN_PCT % and not narrowed by a third
    hold       at least HOLD_MIN_SHARE of the run's closes beyond the fast SMA
    structure  higher highs and lows (lower for bearish) across the run's two halves, and a new high (low)
               within the last LIVE_BARS candles: the move is still going
    run        the SMAs on this side for RUN_MIN+ candles today, the last EFFICIENCY_BARS closes at least
               MIN_EFFICIENCY straight, and no single candle making more than ONE_CANDLE_SHARE of the move

`stack`, `vwap`, `slope` and `run` are required in midday; the label is given when those pass and at least
STRONG_MIN_PASSED of the seven pass. No label before MIN_TODAY closed candles today (MORNING_MIN_TODAY before 10:00,
where `stack`, `slope` (with a new high / low in the last LIVE_BARS candles) and `run` (not one candle) are required).
`score` is the share of the seven checks that passed (0-100). The bots' strong-trend hand-off (`breakout.strong`)
reads these checks; nothing else in the strategy does.
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
LOOKBACK = 12  # at most this many closed candles of the current run are judged for hold / structure / run
MIN_TODAY = 4  # closed candles needed today before any label: one or two candles are never a trend
MORNING_MIN_TODAY = 3  # before 10:00 (09:30 on 5-minute candles): the first three candles, each checked
RUN_MIN = 4  # the SMAs must have been on the trend's side for this many closed candles today
LIVE_BARS = 3  # a new high (low for a downtrend) within this many candles = the move is still going
ONE_CANDLE_SHARE = 0.6  # one candle making more than this share of the whole move = a spike, not a trend
EFFICIENCY_BARS = 6  # the last candles the straight-line share is read on (trend_phases' window)
MIN_EFFICIENCY = 0.5  # |net move| / path walked: under this the candles went back and forth
SLOPE_BARS = 3
FAN_MIN_PCT = 0.10
HOLD_MIN_SHARE = 0.75
STRONG_MIN_PASSED = 6
REQUIRED = ("stack", "vwap", "slope", "run")
CHECKS = ("stack", "vwap", "slope", "fan", "hold", "structure", "run")

# Morning
MORNING_FAN_MIN_PCT = 0.10
MORNING_HOLD_ALL_UNTIL = 3  # candles: 09:15, 09:20, 09:25 (all must close on the trend side by 09:30)
STRONG_MORNING_MIN = 5  # 5 of 7 is "Strong" before 10:00
MORNING_REQUIRED = ("stack", "slope", "run")
VOLUME_MIN_RVOL = 1.5
VOLUME_LOOKBACK_SESSIONS = 10
VOLUME_MIN_SESSIONS = 2
MORNING_CHECKS = ("stack", "vwap", "slope", "fan", "hold", "run", "volume")

NOTES_MIDDAY = {
    "stack": "Price, fast SMA and slow SMA stacked one way",
    "vwap": "Price and fast SMA on the trend side of VWAP",
    "slope": "Both SMAs moved with the trend over 3 candles",
    "fan": "SMA gap at least 0.10% and not narrowed by a third",
    "hold": "Most closes of this run on the trend side of the fast SMA",
    "structure": "Higher highs and lows (lower for bearish) in this run, the newest within the last 3 candles",
    "run": "At least 4 candles old today, a steady move, not one big candle",
}


def _today_part(closed: pd.DataFrame, day: dt.date | None = None) -> pd.DataFrame:
    """The candles of `day` (default: the last candle's IST day). A frame without `ts` is taken whole."""
    if "ts" not in closed.columns or len(closed) == 0:
        return closed
    if day is None:
        day = _ist_date(closed["ts"].iloc[-1])
    return closed[[_ist_date(ts) == day for ts in closed["ts"]]]


def _run_len(part: pd.DataFrame, sign: int) -> int:
    """How many candles at the end of `part` have the fast SMA on the trend's side of the slow one."""
    count = 0
    for f, s in zip(reversed(list(part["sma_9"])), reversed(list(part["sma_21"]))):
        f, s = _f(f), _f(s)
        if f is None or s is None or (f - s) * sign <= 0:
            break
        count += 1
    return count


def _move_read(win: pd.DataFrame, sign: int) -> dict:
    """Is the move in `win` a steady one that is still going? All on closed candles, signed the trend's way.

    live       a new high (low) within the last LIVE_BARS candles
    spike      one candle made more than ONE_CANDLE_SHARE of the whole move
    efficiency |net| / path over the last EFFICIENCY_BARS closes (1 = a straight line)
    net        the move from the window's first open to its last close, the trend's way
    """
    n = len(win)
    opens = [_f(v) for v in win["open"]]
    closes = [_f(v) for v in win["close"]]
    if n == 0 or any(v is None for v in opens + closes):
        return {"live": False, "spike": True, "efficiency": 0.0, "net": 0.0}
    net = (closes[-1] - opens[0]) * sign
    biggest = max((c - o) * sign for o, c in zip(opens, closes))
    edge = win["high"] if sign > 0 else win["low"]
    tail = edge.iloc[-min(LIVE_BARS, n):]
    live = bool(tail.max() >= edge.max()) if sign > 0 else bool(tail.min() <= edge.min())
    last = closes[-(EFFICIENCY_BARS + 1):]
    path = sum(abs(b - a) for a, b in zip(last, last[1:]))
    eff = abs(last[-1] - last[0]) / path if path > 0 else 0.0
    return {"live": live, "spike": net <= 0 or biggest > net * ONE_CANDLE_SHARE, "efficiency": eff, "net": net}


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
    if n < MORNING_MIN_TODAY:
        return {**empty, "trend_side": "BULLISH" if gap > 0 else "BEARISH", "sma_gap_pct": round(gap, 4),
                "vwap": round(vwap, 2) if vwap is not None else None,
                "trend_notes": {"early": f"Too early: {n} of {MORNING_MIN_TODAY} closed candles today"}}
    up = gap > 0
    sign = 1 if up else -1

    def beyond(a, b) -> bool:
        return a is not None and b is not None and (a - b) * sign > 0

    fasts = [_f(v) for v in today["sma_9"]]
    closes = [_f(v) for v in today["close"]]
    slows = [_f(v) for v in today["sma_21"]]
    # Fast SMA slope since the first candle today (or the last few if more candles than that).
    slope_ok = False
    move = _move_read(today.iloc[-LOOKBACK:], sign)
    if n >= 2:
        base = fasts[max(0, n - 1 - SLOPE_BARS)]
        # Still going: the fast SMA keeps rising for a while after the price has stopped, so a new high is asked too.
        slope_ok = beyond(fasts[-1], base) and move["live"]
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
    run_ok = (crossed_today or on_side_all_morning) and not move["spike"]
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
        "slope": "Fast SMA moving with the trend and a new " + ("high" if up else "low") + " in the last 3 candles",
        "fan": f"SMA gap at least {MORNING_FAN_MIN_PCT:g}% on the trend side",
        "hold": (
            f"{sum(side_closes)} of {n} closes on the trend side today"
            + (" (needs all of them until 09:30)" if n <= MORNING_HOLD_ALL_UNTIL else " (needs 75%)")
        ),
        "run": ("One candle made most of the move: a spike, not a trend" if move["spike"]
                else "Fresh cross today is allowed" if crossed_today else "Lines on the trend side since the open"),
        "volume": (
            f"Volume {rvol:.1f}× the usual for this time (needs {VOLUME_MIN_RVOL:g}×, {days} earlier sessions)"
            if rvol is not None and days >= VOLUME_MIN_SESSIONS
            else "Volume: not enough earlier sessions to compare"
        ),
    }
    strong = all(checks[k] for k in MORNING_REQUIRED) and passed >= STRONG_MORNING_MIN
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


FRESH_CANDLES = 3  # the SMAs crossed within this many closed candles
FRESH_MIN_RVOL = 1.5
FRESH_CHECKS = ("cross", "vwap", "gap", "volume")


def fresh(closed: pd.DataFrame, vwap: float | None, now: dt.datetime, tape: pd.DataFrame | None,
          price: float | None = None, colour: bool = False) -> dict:
    """A trend that has only just started: the "fresh trend" flag of the Trend quality card.

    The strong-trend checks confirm a trend once it is old; this reads its first candles. All must hold on the
    closed candles (`sma_9`, `sma_21`):

        cross    the SMAs crossed within the last FRESH_CANDLES closed candles and have stayed that way
        vwap     the price (`price`, the real last close; else the candle's close) and the fast SMA are on the
                 trend's side of today's VWAP
        gap      the SMA gap is wider than on the candle before
        volume   today's volume since 09:15 is at least FRESH_MIN_RVOL x the same minutes of the earlier sessions
                 (`_opening_rvol`; needs VOLUME_MIN_SESSIONS)
        colour   only with `colour` (Heikin Ashi candles): the last candle is the trend's colour

    Returns {fresh: BULLISH / BEARISH / None, fresh_checks, fresh_rvol}. Information only; never raises on short data.
    """
    out = {"fresh": None, "fresh_checks": {}, "fresh_rvol": None}
    if closed is None or len(closed) < FRESH_CANDLES + 2 or "sma_9" not in closed or "sma_21" not in closed:
        return out
    tail = closed.iloc[-(FRESH_CANDLES + 1):]
    gaps = [sma_gap_pct(_f(a), _f(b)) if _f(a) is not None and _f(b) is not None else None
            for a, b in zip(tail["sma_9"], tail["sma_21"])]
    if any(g is None for g in gaps) or gaps[-1] == 0:
        return out
    up = gaps[-1] > 0
    sign = 1 if up else -1
    last = closed.iloc[-1]
    close = price if price is not None else _f(last.get("close"))
    fast = _f(last.get("sma_9"))
    checks = {
        # The other side (or flat) somewhere in the window before the last candle = the cross is that recent.
        "cross": any(g * sign <= 0 for g in gaps[:-1]),
        "vwap": vwap is not None and close is not None and fast is not None and (close - vwap) * sign > 0 and (fast - vwap) * sign > 0,
        "gap": abs(gaps[-1]) > abs(gaps[-2]),
    }
    if colour:
        o, c = _f(last.get("open")), _f(last.get("close"))
        checks["colour"] = o is not None and c is not None and (c - o) * sign > 0
    rvol = None
    if all(checks.values()):  # the volume read walks the whole tape: only when the rest agrees
        rvol, days = _opening_rvol(tape, now)
        checks["volume"] = rvol is not None and days >= VOLUME_MIN_SESSIONS and rvol >= FRESH_MIN_RVOL
    else:
        checks["volume"] = False
    out["fresh_checks"] = checks
    out["fresh_rvol"] = round(rvol, 2) if rvol is not None else None
    if all(checks.values()):
        out["fresh"] = "BULLISH" if up else "BEARISH"
    return out


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
    # Only today's candles, and only the current run (since the SMAs took this side): yesterday's trend is not
    # today's, and a trend is judged from where it began, not on a fixed hour of candles.
    today = _today_part(closed)
    if len(today) < MIN_TODAY:
        return {**empty, "trend_side": "BULLISH" if up else "BEARISH", "sma_gap_pct": round(gap, 4),
                "vwap": round(vwap, 2) if vwap is not None else None,
                "trend_notes": {"early": f"Too early: {len(today)} of {MIN_TODAY} closed candles today"}}
    age = _run_len(today, sign)
    recent = today.iloc[-min(LOOKBACK, max(age, RUN_MIN)):]
    side_closes = [
        beyond(_f(c), _f(f)) for c, f in zip(recent["close"], recent["sma_9"])
    ]
    move = _move_read(recent, sign)
    half = len(recent) // 2
    older, newer = recent.iloc[:half], recent.iloc[half:]
    if up:
        structure = newer["high"].max() > older["high"].max() and newer["low"].min() > older["low"].min()
    else:
        structure = newer["high"].max() < older["high"].max() and newer["low"].min() < older["low"].min()
    checks = {
        "stack": beyond(close, fast) and beyond(fast, slow),
        "vwap": vwap is not None and beyond(close, vwap) and beyond(fast, vwap),
        "slope": beyond(fast, fast_then) and beyond(slow, slow_then),
        "fan": abs(gap) >= FAN_MIN_PCT and gap_then is not None and abs(gap) >= abs(gap_then) * 2 / 3,
        "hold": sum(side_closes) >= HOLD_MIN_SHARE * len(side_closes),
        "structure": bool(structure) and move["live"],
        "run": age >= RUN_MIN and not move["spike"] and move["efficiency"] >= MIN_EFFICIENCY,
    }
    word = "high" if up else "low"
    notes = dict(NOTES_MIDDAY)
    notes["hold"] = f"{sum(side_closes)} of the last {len(side_closes)} closes on the trend side of the fast SMA (needs 75%)"
    notes["structure"] = (
        f"{'Higher' if up else 'Lower'} highs and lows in this run"
        + (f", a new {word} in the last {LIVE_BARS} candles" if move["live"] else f", but no new {word} in the last {LIVE_BARS} candles")
    )
    notes["run"] = (
        f"Only {age} candle(s) on this side today (needs {RUN_MIN})" if age < RUN_MIN
        else "One candle made most of the move: a spike, not a trend" if move["spike"]
        else f"Back and forth: {move['efficiency'] * 100:.0f}% straight over the last {EFFICIENCY_BARS} candles (needs {MIN_EFFICIENCY * 100:.0f}%)"
        if move["efficiency"] < MIN_EFFICIENCY
        else f"{age} candles on this side today, a steady move"
    )
    passed = sum(checks.values())
    strong = all(checks[k] for k in REQUIRED) and passed >= STRONG_MIN_PASSED
    side = "BULLISH" if up else "BEARISH"
    return {
        "trend": (STRONG_BULLISH if up else STRONG_BEARISH) if strong else None,
        "trend_side": side,
        "trend_score": round(passed / len(CHECKS) * 100),
        "trend_checks": checks,
        "trend_mode": MIDDAY,
        "trend_notes": notes,
        "sma_gap_pct": round(gap, 4),
        "vwap": round(vwap, 2) if vwap is not None else None,
        "rvol": None,
        "trend_days": 0,
    }
