"""Chop scan (chop_scan.py): how choppy or trending each stock has looked today.

Read-only, no order path. For each stock in the list, the same 1-minute Groww
candles the Cross scan already downloads are resampled to the user's chosen
candle (1/2/3/5/10/15 min from 09:15 IST), enriched with SMA fast / slow
(`indicators.enrich` on the main desk's SMA periods: the same arithmetic the
bot uses), and today's **closed** candles are walked to count the SMA fast /
slow crosses so far. Nothing here predicts the next move: it describes what
the stock has done this session.

Each row carries:
    `crosses_today`           — SMA fast / slow crosses on today's closed candles.
    `minutes_since_last_cross`— minutes between the start of the last cross candle
                                and now (None when there has been none today).
    `avg_minutes_between`     — average gap, in minutes, between today's crosses.
    `avg_move_pct`            — average absolute price change (%) between two
                                consecutive crosses (close-to-close).
    `score`                   — plain-English bucket: `TRENDING`, `MIXED` or `CHOPPY`.
    plus the stock's today activity (volume, 10-minute volume, signed speed),
    from `cross_scan.activity` so the rules match row-for-row.

Scoring (deliberately simple so the owner can read the rules):
    TRENDING — 0 or 1 crosses so far AND, when there was a cross, the last one
               was more than LONG_RUN_MIN minutes ago.
    CHOPPY   — CHOP_MIN_CROSSES or more today, OR a non-trivial average gap
               that is below CHOP_SHORT_GAP_MIN minutes.
    MIXED    — anything else.

The scanner's lifecycle mirrors `cross_scan.CrossScanner` and reuses the same
`fetch(symbol, start, end)` callable: a scan starts only when the frontend
presses Scan now, one pass at a time, up to CROSS_SCAN_CONCURRENCY downloads
in parallel (default 1) and failing stops after `GIVE_UP_AFTER` errors without
a success.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field, replace
from typing import Awaitable, Callable

import pandas as pd

from cross_scan import (
    CONCURRENCY,
    CALL_GAP_SEC,
    GIVE_UP_AFTER,
    MIN_RESCAN_SEC,
    SCAN_MINUTES,
    WARMUP_DAYS,
    activity,
    cache_key,
    clean_symbols,
    closed_bars,
    SMA_MAX,
    cut_window,
    download_days,
    window_days,
    window_start,
)
from groww_client import IST, market_is_open
from indicators import _session_vwap, derive_minute_volume, enrich, sma_gap_pct
import heikin_ashi
import ha_trend
import trend_phases
import trend_strength
import index_check
import pivots

SESSION_OPEN_SEC = 9 * 3600 + 15 * 60  # 09:15 IST, seconds after IST midnight

log = logging.getLogger("sma.chopscan")

TRENDING = "TRENDING"
MIXED = "MIXED"
CHOPPY = "CHOPPY"

# Quiet-runner alert. When a stock had crosses earlier today and has then stayed
# quiet for `quiet_min` minutes, the scanner marks it a "quiet runner" (possible
# strong trend forming) and sends one Telegram per stock per day. Information
# only: nothing in the strategy reads it; the person decides.
QUIET_MIN_DEFAULT = 60
# Keyed by IST date so a brand-new day sends the alert again: stock -> when its Telegram went (IST).
_ALERTED_TODAY: dict[dt.date, dict[str, str]] = {}
# Stock -> when a scan first read it as a quiet runner today (IST), for the table's "First alert".
_FIRST_QUIET: dict[dt.date, dict[str, str]] = {}


def _today_only(book: dict[dt.date, dict[str, str]], now: dt.datetime) -> dict[str, str]:
    today = now.astimezone(IST).date()
    for day in list(book):
        if day != today:
            book.pop(day, None)
    return book.setdefault(today, {})


def _note_first_quiet(symbol: str, now: dt.datetime) -> str:
    """The first time today this stock read as quiet (kept for the rest of the day)."""
    return _today_only(_FIRST_QUIET, now).setdefault(symbol, now.astimezone(IST).isoformat(timespec="seconds"))


def _already_alerted(symbol: str, now: dt.datetime) -> bool:
    today = now.astimezone(IST).date()
    _ALERTED_TODAY.setdefault(today, {})
    # Keep only today's set, so the dict stays bounded across days.
    for day in list(_ALERTED_TODAY):
        if day != today:
            _ALERTED_TODAY.pop(day, None)
    return symbol in _ALERTED_TODAY[today]


def _mark_alerted(symbol: str, now: dt.datetime) -> None:
    today = now.astimezone(IST).date()
    _ALERTED_TODAY.setdefault(today, {}).setdefault(symbol, now.astimezone(IST).isoformat(timespec="seconds"))


def in_price_range(ltp: float | None, price_min: float = 0.0, price_max: float = 0.0) -> bool:
    """True when the last price is inside [price_min, price_max]; 0 means no bound. Unknown price passes only with no bounds."""
    if not price_min and not price_max:
        return True
    if ltp is None:
        return False
    return (not price_min or ltp >= price_min) and (not price_max or ltp <= price_max)


def quiet_runner_text(symbol: str, minutes: float, side: str | None, ltp: float | None) -> str:
    bias = "Bullish ↑" if side == "BULLISH" else "Bearish ↓" if side == "BEARISH" else "trend forming"
    price = f" at {ltp:.2f}" if ltp is not None else ""
    hrs = int(minutes // 60)
    mins = int(minutes - hrs * 60)
    quiet = f"{hrs}h {mins}m" if hrs else f"{mins}m"
    return (
        f"QUIET RUNNER · {symbol}{price} has had no SMA cross for {quiet} after crossing earlier today. "
        f"May be a strong {bias}. Open Trend quality to review."
    )

SUMMARY_NAMES = 15  # stocks named in a summary alert before "and N more"


def quiet_runners_text(rows: list) -> str:
    """One alert for several quiet runners: the longest-quiet first, then a count of the rest."""
    ranked = sorted(rows, key=lambda r: -(r.minutes_since_last_cross or 0.0))
    names = [
        f"{r.symbol}{' ↑' if r.last_cross_direction == 'BULLISH' else ' ↓' if r.last_cross_direction == 'BEARISH' else ''}"
        for r in ranked[:SUMMARY_NAMES]
    ]
    more = len(ranked) - len(names)
    tail = f" and {more} more" if more > 0 else ""
    return (
        f"QUIET RUNNERS · {len(ranked)} stocks have had no SMA cross for a while after crossing earlier today: "
        f"{', '.join(names)}{tail}. Open Trend quality to review."
    )

# Thresholds. Picked on the plain-language reading of the owner's example: one
# cross-only morning that then runs for 60+ minutes is TRENDING; a stock that
# crosses four or more times in a day, or whose crosses come on average less
# than 15 minutes apart, is CHOPPY. Everything else reads MIXED.
LONG_RUN_MIN = 60
CHOP_MIN_CROSSES = 4
CHOP_SHORT_GAP_MIN = 15

Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]
# One stock's whole read (download + analysis), e.g. in the scanner process (scan_worker.py).
Job = Callable[[str, dt.datetime, dt.datetime], Awaitable["ChopRow | None"]]


@dataclass
class ChopRow:
    symbol: str
    minutes: int  # the candle size the row was read on
    crosses_today: int
    minutes_since_last_cross: float | None
    avg_minutes_between: float | None
    avg_move_pct: float | None  # abs % move between consecutive crosses, on average
    last_cross_direction: str | None  # "BULLISH" or "BEARISH" from the last cross's new side
    score: str
    quiet_runner: bool  # had crosses earlier today and has stayed quiet for `quiet_min` minutes
    ltp: float | None
    candle_ts: int  # start of the last closed candle (epoch seconds)
    volume: float | None = None
    volume_window: float | None = None
    move_pct: float | None = None
    speed_pct_per_min: float | None = None
    window_min: int = 10
    # Strong trend read (`trend_strength.read`): STRONG_BULLISH / STRONG_BEARISH or None.
    trend: str | None = None
    trend_side: str | None = None
    trend_score: int = 0
    trend_checks: dict = field(default_factory=dict)
    sma_gap_pct: float | None = None
    vwap: float | None = None
    # Morning mode (before 10:00 IST): which rules were used, a note per check, today's relative volume
    # and how many earlier sessions it was compared with (trend_strength.py).
    trend_mode: str = "MIDDAY"
    trend_notes: dict = field(default_factory=dict)
    rvol: float | None = None
    trend_days: int = 0
    day_move_pct: float | None = None  # last close vs today's first open
    # Fresh trend (`trend_strength.fresh`): BULLISH / BEARISH when the SMAs crossed in the last 3 candles with the price
    # and fast SMA beyond VWAP, the gap widening and volume 1.5x usual; else None. `fresh_checks` says which held.
    fresh: str | None = None
    fresh_checks: dict = field(default_factory=dict)
    fresh_rvol: float | None = None
    # Today's closed-candle SMA crosses on every candle size (1, 2, 3, 5, 10, 15 min), from the
    # same 1-minute download: the "No cross today on" filter. None = not enough candles yet.
    crosses_by_minutes: dict = field(default_factory=dict)
    # The side the fast SMA is on for each size ("BULLISH" above the slow SMA, "BEARISH" below).
    side_by_minutes: dict = field(default_factory=dict)
    # Start of the last cross candle today per size (epoch seconds) and its direction; None without one.
    last_cross_ts_by_minutes: dict = field(default_factory=dict)
    last_cross_side_by_minutes: dict = field(default_factory=dict)
    # The strong-trend read (`trend_strength.read`) on every candle size: STRONG_BULLISH /
    # STRONG_BEARISH, or None (no strong trend, or too few candles). The "Trend on" filter.
    trend_by_minutes: dict = field(default_factory=dict)
    # How long the current SMA side has run on every size (`_run_read`, all days in the download): the
    # start of its first candle (epoch seconds), how many closed candles, the signed SMA gap % now and the
    # widest |gap| % in that run (a gap well under its widest = the trend is fading). None = too few candles.
    run_since_ts_by_minutes: dict = field(default_factory=dict)
    run_candles_by_minutes: dict = field(default_factory=dict)
    gap_pct_by_minutes: dict = field(default_factory=dict)
    gap_peak_pct_by_minutes: dict = field(default_factory=dict)
    # Three SMAs (`three_read`, only when the scan was given a third, longer SMA): `stack3` BULLISH (fast above slow
    # above third) / BEARISH (the mirror) / None (not lined up, or the third SMA not formed), the signed gaps in %
    # (fast vs slow, slow vs third), for how many closed candles the lines have been stacked, and for how many candles
    # in a row BOTH gaps have widened (0 = not widening now).
    # Against the market (the pass reads Nifty once): Nifty's move today in % and this stock's day move minus it
    # (positive = stronger than Nifty). None when Nifty could not be read.
    index_move_pct: float | None = None
    rs_pct: float | None = None
    # Central Pivot Range from the previous session (pivots.levels): its width in % of the pivot (narrow = a trending
    # day is more likely, wide = sideways), where the last price sits (ABOVE / INSIDE / BELOW), and yesterday's high / low
    # with whether the price is beyond them (ABOVE the high / BELOW the low / INSIDE).
    cpr_width_pct: float | None = None
    cpr_side: str | None = None
    cpr_high: float | None = None
    cpr_low: float | None = None
    prev_high: float | None = None
    prev_low: float | None = None
    prev_side: str | None = None
    sma_third: int = 0
    stack3: str | None = None
    gap_fast_slow_pct: float | None = None
    gap_slow_third_pct: float | None = None
    stack3_candles: int = 0
    widen_candles: int = 0
    # When a scan first read this stock as a quiet runner today, and when its Telegram went (IST).
    first_quiet_at: str | None = None
    alerted_at: str | None = None
    # Trend phases today (`trend_phases`): the phase now (BULL / BEAR / CHOP), since when, its move,
    # and every phase today oldest first ({side, start_ts, end_ts, candles, move_pct}).
    phase: str | None = None
    phase_since_ts: int | None = None
    phase_move_pct: float | None = None
    phase_candles: int = 0
    # Minutes after 09:15 left out of today's trend read (`analyze(skip=)`; 0 = none).
    trend_skip_min: int = 0
    phases: list = field(default_factory=list)
    strong_phases: int = 0
    # CHOP only because fewer than trend_phases.MIN_RUN candles of today have closed ("not enough candles yet").
    phase_early: bool = False

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("minutes_since_last_cross", "avg_minutes_between", "avg_move_pct", "move_pct", "speed_pct_per_min", "day_move_pct"):
            if out[key] is not None:
                out[key] = round(out[key], 4)
        return out


def _ist_day(ts: int) -> int:
    """IST day index (midnight to midnight), from a UTC epoch second."""
    return (ts + 19_800) // 86_400


def _crosses(closed: pd.DataFrame, today: int) -> list[dict]:
    """Return each closed candle on day `today` where the SMA fast / slow gap flipped sign.

    The gap is signed `(fast - slow) / slow x 100`; a flip from negative to
    positive is a bullish cross, the other way round is bearish. Zeros do not
    count as a cross. A candle without a gap (SMAs not formed yet) is skipped:
    the next one is compared with the last candle that had one. Whole columns at
    once (the scan reads every stock on six candle sizes).
    """
    if closed is None or len(closed) < 2 or "sma_9" not in closed or "sma_21" not in closed:
        return []
    fast = pd.to_numeric(closed["sma_9"], errors="coerce")
    slow = pd.to_numeric(closed["sma_21"], errors="coerce")
    gap = ((fast - slow) / slow * 100.0).where(slow != 0)
    valid = gap.notna()
    g = gap[valid]
    prev = g.shift(1)
    ts = closed["ts"][valid].astype("int64")
    flip = prev.notna() & (prev != 0) & (g != 0) & ((prev < 0) != (g < 0)) & ((ts + 19_800) // 86_400 == today)
    if not flip.any():
        return []
    close = pd.to_numeric(closed["close"][valid], errors="coerce").fillna(0.0)
    return [
        {"ts": int(t), "close": float(c), "side": "BULLISH" if v > 0 else "BEARISH"}
        for t, c, v in zip(ts[flip], close[flip], g[flip])
    ]


NORMAL = "NORMAL"
HEIKIN_ASHI = "HA"
# Real candles read the Heikin Ashi way (owner's request): colour runs and flips of the real candles, no SMA cross.
COLOUR = "COLOUR"
CANDLE_KINDS = (NORMAL, COLOUR, HEIKIN_ASHI)
COLOUR_MODE = "COLOUR"  # trend_mode of a colour read on real candles (ha_trend.HA_MODE on Heikin Ashi ones)


def candle_kind(value) -> str:
    """NORMAL / COLOUR / HA from a request or saved meta (anything else, and a missing one, is NORMAL)."""
    text = str(value or "").upper()
    return text if text in CANDLE_KINDS else NORMAL


def read_flags(candles) -> tuple[bool, bool]:
    """(Heikin Ashi candles, read by colour) for a candle kind; True / False stand for HA / NORMAL (older callers)."""
    if candles is True:
        return True, True
    kind = candle_kind(candles) if candles not in (None, False) else NORMAL
    return kind == HEIKIN_ASHI, kind != NORMAL


def ha_bars(bars: pd.DataFrame) -> pd.DataFrame:
    """`bars` as Heikin Ashi candles (heikin_ashi.candles): HA open / close, high and low widened to hold them.
    Everything read from the result (SMAs, crosses, the strong-trend checks, phases) is then the Heikin Ashi read;
    prices shown to the person (last price, VWAP, day move) come from the real 1-minute tape."""
    if bars is None or len(bars) == 0 or not {"open", "high", "low", "close"} <= set(bars.columns):
        return bars
    ha_open, ha_close = heikin_ashi.candles(bars)
    out = bars.copy()
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    out["open"], out["close"] = ha_open, ha_close
    out["high"] = pd.concat([high, ha_open, ha_close], axis=1).max(axis=1)
    out["low"] = pd.concat([low, ha_open, ha_close], axis=1).min(axis=1)
    return out


MAX_SKIP_CANDLES = 12
WIDEN_MAX = 30  # candles the three-SMA read looks back for the stack's age and the widening run


def day_levels(frame_1m: pd.DataFrame, ltp: float | None) -> dict:
    """The row's CPR and previous-day reads (pivots.levels on the latest session in the download). {} when unknown."""
    try:
        day = dt.datetime.fromtimestamp(int(frame_1m["ts"].iloc[-1]), IST).date()
        lv = pivots.levels(frame_1m, day)
    except Exception:  # noqa: BLE001
        lv = None
    if lv is None or ltp is None:
        return {}

    def where(low: float, high: float) -> str:
        return "ABOVE" if ltp > high else "BELOW" if ltp < low else "INSIDE"

    return {
        "cpr_width_pct": lv["cpr_width_pct"], "cpr_high": lv["cpr_high"], "cpr_low": lv["cpr_low"],
        "cpr_side": where(lv["cpr_low"], lv["cpr_high"]),
        "prev_high": lv["pdh"], "prev_low": lv["pdl"], "prev_side": where(lv["pdl"], lv["pdh"]),
    }


def with_index(rows: list, index_move: float | None) -> None:
    """Set each row's move against the market: its day move minus Nifty's (in place)."""
    if index_move is None:
        return
    for row in rows:
        row.index_move_pct = round(float(index_move), 4)
        if row.day_move_pct is not None:
            row.rs_pct = round(float(row.day_move_pct) - float(index_move), 4)


def pick_third(third, slow: int) -> int:
    """The scan's third (longest) SMA: 0 = off, else longer than the slow SMA and at most SMA_MAX.
    ValueError in plain words."""
    try:
        third = int(third or 0)
    except (TypeError, ValueError):
        raise ValueError("The third SMA must be a whole number.") from None
    if not third:
        return 0
    if third <= int(slow) or third > SMA_MAX:
        raise ValueError(f"The third SMA must be longer than the slow SMA ({int(slow)}) and at most {SMA_MAX}.")
    return third


def three_read(bars: pd.DataFrame, fast: int, slow: int, third: int) -> dict:
    """Three SMAs on closed candles: are they stacked one way, and are both gaps widening?

    `stack3` BULLISH = SMA fast > SMA slow > SMA third on the last closed candle, BEARISH the mirror, None otherwise
    (or the third SMA is not formed yet). The gaps are signed %: (fast - slow) / slow and (slow - third) / third.
    `stack3_candles` = closed candles in a row the lines have been stacked that way; `widen_candles` = closed candles in
    a row, ending at the last one, on which BOTH gaps were wider than on the candle before while stacked (0 = one of
    them is flat or narrowing now). Both are counted up to WIDEN_MAX. Never raises on short data."""
    out = {"sma_third": int(third or 0), "stack3": None, "gap_fast_slow_pct": None, "gap_slow_third_pct": None,
           "stack3_candles": 0, "widen_candles": 0}
    if not third or bars is None or len(bars) < int(third) or "close" not in bars:
        return out
    close = pd.to_numeric(bars["close"], errors="coerce")
    f = close.rolling(int(fast)).mean()
    m = close.rolling(int(slow)).mean()
    t = close.rolling(int(third)).mean()
    g1 = ((f - m) / m * 100.0).where(m != 0).iloc[-(WIDEN_MAX + 1):].tolist()
    g2 = ((m - t) / t * 100.0).where(t != 0).iloc[-(WIDEN_MAX + 1):].tolist()
    a, b = g1[-1], g2[-1]
    if pd.isna(a) or pd.isna(b):
        return out
    out["gap_fast_slow_pct"], out["gap_slow_third_pct"] = round(float(a), 4), round(float(b), 4)
    if not ((a > 0 and b > 0) or (a < 0 and b < 0)):
        return out
    sign = 1 if a > 0 else -1
    out["stack3"] = "BULLISH" if sign > 0 else "BEARISH"

    def stacked(i: int) -> bool:
        return not pd.isna(g1[i]) and not pd.isna(g2[i]) and g1[i] * sign > 0 and g2[i] * sign > 0

    n = 0
    for i in range(len(g1) - 1, -1, -1):
        if not stacked(i):
            break
        n += 1
    out["stack3_candles"] = n
    w = 0
    for i in range(len(g1) - 1, 0, -1):
        if not (stacked(i) and not pd.isna(g1[i - 1]) and not pd.isna(g2[i - 1])
                and abs(g1[i]) > abs(g1[i - 1]) and abs(g2[i]) > abs(g2[i - 1])):
            break
        w += 1
    out["widen_candles"] = w
    return out


def skip_minutes(skip_candles, minutes: int) -> int:
    """The first N candles of the scan's size as minutes after 09:15 (0 = off)."""
    try:
        n = int(skip_candles or 0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(MAX_SKIP_CANDLES, n)) * int(minutes or 1)


def analyze(frame_1m: pd.DataFrame, sma_fast: int, sma_slow: int, minutes: int, now: dt.datetime, symbol: str = "", quiet_min: int = QUIET_MIN_DEFAULT, ha: bool = False, colour: bool = False, skip: int = 0, wide: pd.DataFrame | None = None, third: int = 0) -> ChopRow | None:
    """One stock's chop/trend read. None when the tape isn't long enough yet. `ha`: read Heikin Ashi candles.

    `wide`: a longer download of the same stock (`scan_frames`), read only for the other candle sizes of the row whose
    slow SMA needs more days than `frame_1m` holds (30-minute and 1-hour candles), each cut to its own window.
    `colour`: read the real candles by colour (runs and flips, ha_trend.py) instead of the SMA cross; Heikin Ashi
    candles are always read by colour.

    `skip` (minutes after 09:15, 0 = off; owner's request: a stock read "down" at 10:00 only because of the first three
    big candles, then went sideways): today's candles that began before 09:15 + skip are left out of the trend read.
    Crosses / colour flips, phases, the strong-trend checks (their candles; morning mode reads today's candles after the
    skip), the fresh-trend read and the VWAP start after them; the SMAs are still worked out on every candle, so they
    carry some of the opening move for a while. The day move, last price and activity stay real.

    `third` (0 = off): a third, longer SMA (owner's request: 9 / 21 / 50 with the gaps widening). The row then carries
    `three_read` on the scan's candles (from `wide` when the third SMA needs more days than `frame_1m` holds). The
    other reads are unchanged, and a colour read (no SMAs) leaves it out."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    bars = closed_bars(frame_1m, minutes, now)
    if len(bars) < max(int(sma_slow), 2) + 1:
        return None
    real_close = float(bars["close"].iloc[-1])  # before any Heikin Ashi change: the price VWAP is compared with
    if ha:
        bars = ha_bars(bars)
    enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow)
    # "Today" is the session of the last closed candle: on a holiday, a weekend or before the first candle of the day
    # has closed, the last session is read (owner's case: on the 24 Nov holiday every stock read "Trending" with 0
    # crosses, because the calendar day had no candles). Minutes since a cross then count to that session's last candle.
    today = _ist_day(int(enriched.iloc[-1]["ts"]))
    clock = now if today == _ist_day(int(now.timestamp())) else dt.datetime.fromtimestamp(
        int(enriched.iloc[-1]["ts"]) + int(minutes) * 60, IST)
    by_colour = bool(ha or colour)
    # Heikin Ashi: the trend is read from the candles (colour runs, flips, wicks; ha_trend.py), never the SMA cross.
    hits = ha_trend.flips(enriched, today) if by_colour else _crosses(enriched, today)
    if skip:
        cut = today * 86_400 - 19_800 + SESSION_OPEN_SEC + int(skip) * 60
        hits = [h for h in hits if int(h["ts"]) >= cut]
    ltp = float(frame_1m["close"].iloc[-1]) if len(frame_1m) else None
    minutes_since: float | None = None
    avg_gap: float | None = None
    avg_move: float | None = None
    last_side: str | None = None
    if hits:
        last_ts = hits[-1]["ts"]
        minutes_since = max(0.0, (int(clock.timestamp()) - last_ts) / 60)
        last_side = hits[-1]["side"]
        if len(hits) >= 2:
            gaps = [(hits[i]["ts"] - hits[i - 1]["ts"]) / 60 for i in range(1, len(hits))]
            moves = [
                abs(hits[i]["close"] / hits[i - 1]["close"] - 1) * 100
                for i in range(1, len(hits))
                if hits[i - 1]["close"]
            ]
            avg_gap = sum(gaps) / len(gaps) if gaps else None
            avg_move = sum(moves) / len(moves) if moves else None
    score = ha_trend.score(hits, minutes_since, avg_gap, minutes) if by_colour else score_for(len(hits), minutes_since, avg_gap)
    quiet = bool(hits) and minutes_since is not None and minutes_since >= max(1, int(quiet_min or 0))
    base = ChopRow(
        symbol=symbol,
        minutes=minutes,
        crosses_today=len(hits),
        minutes_since_last_cross=minutes_since,
        avg_minutes_between=avg_gap,
        avg_move_pct=avg_move,
        last_cross_direction=last_side,
        score=score,
        quiet_runner=quiet,
        ltp=round(ltp, 2) if ltp is not None else None,
        candle_ts=int(enriched.iloc[-1]["ts"]),
    )
    today_start = today * 86_400 - 19_800 + SESSION_OPEN_SEC
    skip_from = today_start + max(0, int(skip or 0)) * 60
    vwap = _vwap_today(frame_1m, now, skip_from if skip else None)
    view = _skipped(enriched, today_start, skip_from) if skip else enriched
    if skip and len(view) < 2:
        view = enriched  # nothing after the skip yet: read as usual
    if by_colour:
        trend = ha_trend.checks(view, vwap, real_close)
        if not ha:
            trend = {**trend, "trend_mode": COLOUR_MODE}
        fresh = ha_trend.fresh(view, vwap, now, frame_1m, real_close)
        phase = ha_trend.summary(ha_trend.phases(enriched, skip_from))
    else:
        trend = trend_strength.read(view, vwap, now=now, tape=frame_1m)
        fresh = trend_strength.fresh(view, vwap, now, frame_1m, price=real_close)
        phase = trend_phases.summary(trend_phases.phases(enriched, skip_from))
    if skip:
        kept, note = _moved_since_skip(enriched, today_start, skip_from, trend.get("trend"))
        notes = {**(trend.get("trend_notes") or {}), "skip": note} if note else trend.get("trend_notes")
        trend = {**trend, "trend": kept, "trend_skip_min": int(skip), **({"trend_notes": notes} if notes else {})}
    own_days = window_days(minutes, sma_slow)

    def tape_for(m: int) -> pd.DataFrame:
        days = window_days(m, sma_slow)
        return frame_1m if wide is None or days <= own_days else cut_window(wide, now, days)

    by_size = {m: (*_cross_read(hits, enriched, by_colour), trend["trend"]) if m == minutes
               else _crosses_on(tape_for(m), m, now, sma_fast, sma_slow, today, vwap, ha, by_colour, skip_from if skip else None)
               for m in SCAN_MINUTES}
    three: dict = {}
    if third and not by_colour:
        need = window_days(minutes, third)
        if wide is not None and need > own_days:
            bars3 = closed_bars(cut_window(wide, now, need), minutes, now)
        else:
            bars3 = bars
        three = three_read(bars3, sma_fast, sma_slow, third)
    return replace(
        base,
        **day_levels(frame_1m, ltp),
        **three,
        **activity(frame_1m),
        **trend,
        **fresh,
        **phase,
        day_move_pct=_day_move(frame_1m),
        crosses_by_minutes={m: r[0] for m, r in by_size.items()},
        side_by_minutes={m: r[1] for m, r in by_size.items()},
        last_cross_ts_by_minutes={m: r[2] for m, r in by_size.items()},
        last_cross_side_by_minutes={m: r[3] for m, r in by_size.items()},
        run_since_ts_by_minutes={m: r[4] for m, r in by_size.items()},
        run_candles_by_minutes={m: r[5] for m, r in by_size.items()},
        gap_pct_by_minutes={m: r[6] for m, r in by_size.items()},
        gap_peak_pct_by_minutes={m: r[7] for m, r in by_size.items()},
        trend_by_minutes={m: r[8] for m, r in by_size.items()},
    )


def scan_download_days(minutes: int, sma_slow: int, third: int = 0) -> int:
    """Days a Trend quality pass downloads: the usual ones, or more when a third SMA needs them on the scan's candle."""
    days = download_days(minutes, sma_slow, SCAN_MINUTES)
    return max(days, window_days(minutes, third)) if third else days


def scan_frames(frame_1m: pd.DataFrame, minutes: int, sma_slow: int, now: dt.datetime) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(the frame `analyze` reads, the whole download as its `wide`) for one stock of a pass.

    A pass downloads `download_days`, which can be longer than its own candle needs so that a row's 30-minute and
    1-hour reads have their SMAs; the scan's own read keeps its own window, so its numbers do not move with that."""
    return cut_window(frame_1m, now, window_days(minutes, sma_slow)), frame_1m


def _side(enriched: pd.DataFrame) -> str | None:
    gap = sma_gap_pct(enriched.iloc[-1].get("sma_9"), enriched.iloc[-1].get("sma_21")) if len(enriched) else None
    return None if gap is None or gap == 0 else ("BULLISH" if gap > 0 else "BEARISH")


def _crosses_on(frame_1m: pd.DataFrame, minutes: int, now: dt.datetime, sma_fast: int, sma_slow: int, today: int,
                vwap: float | None = None, ha: bool = False, colour: bool = False, skip_from: int | None = None):
    """(crosses today, side, last cross ts, last cross side, run since ts, run candles, gap %, widest gap %,
    strong trend) on `minutes`-long candles; Nones when too short. `colour`: read by colour (Heikin Ashi always is).
    `today` is the session the main read used (the last closed candle's day)."""
    bars = closed_bars(frame_1m, minutes, now)
    if len(bars) < max(int(sma_slow), 2) + 1:
        return (None,) * 9
    if ha:
        bars = ha_bars(bars)
    enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow)
    view = enriched
    if skip_from is not None:
        view = _skipped(enriched, today * 86_400 - 19_800 + SESSION_OPEN_SEC, skip_from)
        if len(view) < 2:
            view = enriched

    def after(hits: list[dict]) -> list[dict]:
        return [h for h in hits if skip_from is None or int(h["ts"]) >= skip_from]

    if ha or colour:
        price = float(frame_1m["close"].iloc[-1]) if len(frame_1m) else None
        strong = ha_trend.checks(view, vwap, price)["trend"]
        if skip_from is not None:
            strong = _moved_since_skip(enriched, today * 86_400 - 19_800 + SESSION_OPEN_SEC, skip_from, strong)[0]
        return (*_cross_read(after(ha_trend.flips(enriched, today)), enriched, True), strong)
    strong = trend_strength.read(view, vwap, now=now, tape=frame_1m)["trend"]
    if skip_from is not None:
        strong = _moved_since_skip(enriched, today * 86_400 - 19_800 + SESSION_OPEN_SEC, skip_from, strong)[0]
    return (*_cross_read(after(_crosses(enriched, today)), enriched), strong)


SKIP_MOVE_ATR = 1.0  # with the skip on, a strong trend must have moved this many ATRs its way since the skipped candles


def _moved_since_skip(enriched: pd.DataFrame, today_start: int, skip_from: int, strong: str | None) -> tuple[str | None, str | None]:
    """(strong trend kept or None, note). With today's first candles skipped, a strong trend also needs the price to
    have moved at least SKIP_MOVE_ATR × ATR 14 its way from the close of the last skipped candle (a sideways day after
    a big opening move is not a trend). Nothing to judge (no skipped candle closed today, no ATR) keeps it."""
    if not strong or enriched is None or len(enriched) == 0:
        return strong, None
    ts = enriched["ts"].astype("int64")
    before = enriched[(ts >= today_start) & (ts < skip_from)]
    if before.empty or len(enriched[ts >= skip_from]) == 0:
        return strong, None
    base = float(before["close"].iloc[-1])
    now = float(enriched["close"].iloc[-1])
    atr = pd.to_numeric(enriched.get("atr_14"), errors="coerce").iloc[-1] if "atr_14" in enriched else float("nan")
    if not atr == atr or atr <= 0:
        return strong, None
    sign = 1.0 if "BULL" in str(strong) else -1.0
    moved = (now - base) * sign / float(atr)
    if moved >= SKIP_MOVE_ATR:
        return strong, f"Moved {moved:.1f} ATR its way since the skipped opening candles"
    return None, f"Only {moved:+.1f} ATR its way since the skipped opening candles (needs {SKIP_MOVE_ATR:g}): not a trend"


def _skipped(enriched: pd.DataFrame, today_start: int, skip_from: int) -> pd.DataFrame:
    """`enriched` without today's candles that began before `skip_from` (earlier days stay, as the checks' history)."""
    ts = enriched["ts"].astype("int64")
    return enriched[(ts < today_start) | (ts >= skip_from)].reset_index(drop=True)


def _cross_read(hits: list[dict], enriched: pd.DataFrame, ha: bool = False):
    """Heikin Ashi (`ha`): the side is the run's colour and the run / gap are its candles and move (ha_trend.run_read)."""
    last = hits[-1] if hits else None
    return (len(hits), ha_trend.side(enriched) if ha else _side(enriched), (last["ts"] if last else None),
            (last["side"] if last else None), *(ha_trend.run_read(enriched) if ha else _run_read(enriched)))


def _run_read(enriched: pd.DataFrame):
    """(start ts, closed candles, signed gap % now, widest |gap| % in the run) of the current SMA side.

    The run is the unbroken stretch of closed candles whose signed gap `(fast - slow) / slow x 100` has the
    same sign as the last one, across days (the download's earlier sessions too, so a trend from yesterday
    counts; it can only be as long as the download). Candles without a gap or with a zero gap are skipped.
    """
    if enriched is None or len(enriched) == 0 or "sma_9" not in enriched or "sma_21" not in enriched:
        return None, None, None, None
    fast = pd.to_numeric(enriched["sma_9"], errors="coerce")
    slow = pd.to_numeric(enriched["sma_21"], errors="coerce")
    gap = ((fast - slow) / slow * 100.0).where(slow != 0)
    keep = gap.notna() & (gap != 0)
    g = gap[keep].reset_index(drop=True)
    if g.empty:
        return None, None, None, None
    ts = enriched["ts"][keep].astype("int64").reset_index(drop=True)
    up = g > 0
    other = (up != up.iloc[-1]).to_numpy().nonzero()[0]
    first = int(other[-1]) + 1 if len(other) else 0
    run = g.iloc[first:]
    return int(ts.iloc[first]), int(len(run)), round(float(g.iloc[-1]), 4), round(float(run.abs().max()), 4)


def _today_1m(frame_1m: pd.DataFrame) -> pd.DataFrame:
    last_ts = int(frame_1m["ts"].iloc[-1])
    return frame_1m[(frame_1m["ts"].astype("int64") + 19_800) // 86_400 == (last_ts + 19_800) // 86_400]


def _vwap_today(frame_1m: pd.DataFrame, now: dt.datetime, start_ts: int | None = None) -> float | None:
    """Today's VWAP on the closed 1-minute tape (the bot's own VWAP arithmetic); from `start_ts` on when given."""
    try:
        bars = closed_bars(frame_1m, 1, now)
        if start_ts is not None and len(bars):
            if "minute_volume" not in bars.columns:
                # Per-minute volume from the running total on the whole tape first, so the first kept minute is not
                # weighted with the whole skipped morning.
                bars = bars.assign(minute_volume=derive_minute_volume(bars))
            ts = bars["ts"].astype("int64")
            last_day = (int(ts.iloc[-1]) + 19_800) // 86_400
            kept = bars[(ts >= start_ts) | ((ts + 19_800) // 86_400 != last_day)]
            if len(kept) and (int(kept["ts"].iloc[-1]) + 19_800) // 86_400 == last_day:
                bars = kept
        return _session_vwap(bars)
    except Exception:  # noqa: BLE001
        return None


def _day_move(frame_1m: pd.DataFrame) -> float | None:
    try:
        today = _today_1m(frame_1m.sort_values("ts"))
        first = float(today["open"].iloc[0]) if len(today) and "open" in today.columns else 0.0
        return (float(today["close"].iloc[-1]) / first - 1) * 100 if first > 0 else None
    except Exception:  # noqa: BLE001
        return None


def score_for(crosses: int, minutes_since: float | None, avg_gap: float | None) -> str:
    """The one place the TRENDING / MIXED / CHOPPY rule lives, so the tests pin it."""
    if crosses >= CHOP_MIN_CROSSES:
        return CHOPPY
    if crosses >= 2 and avg_gap is not None and avg_gap < CHOP_SHORT_GAP_MIN:
        return CHOPPY
    if crosses <= 1 and (crosses == 0 or (minutes_since is not None and minutes_since > LONG_RUN_MIN)):
        return TRENDING
    return MIXED


@dataclass
class Status:
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    minutes: int = 5
    sma_fast: int = 9
    sma_slow: int = 21
    sma_third: int = 0  # a third, longer SMA for the three-SMA read (0 = off)
    quiet_min: int = QUIET_MIN_DEFAULT
    candles: str = NORMAL  # NORMAL | COLOUR (real candles read by colour) | HA (Heikin Ashi)
    skip_candles: int = 0  # today's first N candles of the scan's size left out of the trend read
    as_of: str | None = None
    finished_at: str | None = None  # wall-clock time (IST) the last pass ended
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class ChopScanner:
    """One scan at a time; the last finished result is kept until the next candle closes."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._key: tuple | None = None
        self._started_at = 0.0
        # Called with (snapshot, meta) when a pass finishes with something read (scan_history).
        self.on_done: Callable[[dict, dict], None] | None = None
        self._meta: dict = {}
        # Counts the passes started, so a caller can tell its own pass from one already running (scan_arm).
        self.passes = 0

    def stop(self, reason: str) -> None:
        """Cancel a running pass (the Scalp page's Pause scans). The last finished rows stay."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.status.running:
            self.status.running = False
            self.status.error = reason
            self._key = None

    def snapshot(self) -> dict:
        s = self.status
        return {
            "running": s.running,
            "done": s.done,
            "total": s.total,
            "failed": s.failed,
            "error": s.error,
            "minutes": s.minutes,
            "finished_at": s.finished_at,
            "sma_fast": s.sma_fast,
            "sma_slow": s.sma_slow,
            "sma_third": s.sma_third,
            "quiet_min": s.quiet_min,
            "candles": s.candles,
            "skip_candles": s.skip_candles,
            "as_of": s.as_of,
            "market_open": s.market_open,
            "rows": s.rows,
        }

    def start(
        self,
        symbols: list[str],
        fetch: Fetch,
        *,
        minutes: int = 5,
        sma_fast: int = 9,
        sma_slow: int = 21,
        quiet_min: int = QUIET_MIN_DEFAULT,
        alert_on_quiet: bool = False,
        alert_cb: Callable[[str], None] | None = None,
        force: bool = False,
        now: dt.datetime | None = None,
        job: Job | None = None,
        meta: dict | None = None,
        price_min: float = 0.0,
        price_max: float = 0.0,
        candles: str = NORMAL,
        skip_candles: int = 0,
        sma_third: int = 0,
    ) -> dict:
        sma_third = pick_third(sma_third, sma_slow)
        skip_candles = max(0, min(MAX_SKIP_CANDLES, int(skip_candles or 0)))
        if candles not in CANDLE_KINDS:
            raise ValueError("Candles must be NORMAL, COLOUR (real candles read by colour) or HA (Heikin Ashi)")
        if minutes not in SCAN_MINUTES:
            raise ValueError(f"Candle size must be one of {', '.join(str(m) for m in SCAN_MINUTES)} minutes")
        names = clean_symbols(symbols)
        if not names:
            raise ValueError("No stocks to scan")
        now = now or dt.datetime.now(IST)
        key = (cache_key(now, minutes), minutes, sma_fast, sma_slow, tuple(names), candles, skip_candles, sma_third)
        if self.status.running:
            return self.snapshot()
        fresh = self._key == key and self.status.as_of is not None
        if fresh and not force:
            return self.snapshot()
        if time.monotonic() - self._started_at < MIN_RESCAN_SEC and self._key is not None:
            return self.snapshot()
        self._started_at = time.monotonic()
        self.passes += 1
        self._meta = dict(meta or {})
        self.status.running = True
        self.status.done = 0
        self.status.total = len(names)
        self.status.failed = 0
        self.status.error = None
        self.status.minutes, self.status.sma_fast, self.status.sma_slow = minutes, sma_fast, sma_slow
        self.status.quiet_min = max(1, int(quiet_min or QUIET_MIN_DEFAULT))
        self.status.candles = candles
        self.status.skip_candles = skip_candles
        self.status.sma_third = sma_third
        self._task = asyncio.get_running_loop().create_task(
            self._run(
                names, fetch, key, minutes, sma_fast, sma_slow, now,
                alert_on_quiet=alert_on_quiet, alert_cb=alert_cb, job=job,
                price_min=price_min, price_max=price_max, ha=candles == HEIKIN_ASHI, colour=candles == COLOUR,
                skip=skip_minutes(skip_candles, minutes), third=sma_third,
            )
        )
        return self.snapshot()

    async def _run(
        self,
        names: list[str],
        fetch: Fetch,
        key: tuple,
        minutes: int,
        sma_fast: int,
        sma_slow: int,
        now: dt.datetime,
        *,
        alert_on_quiet: bool = False,
        alert_cb: Callable[[str], None] | None = None,
        job: Job | None = None,
        price_min: float = 0.0,
        price_max: float = 0.0,
        ha: bool = False,
        colour: bool = False,
        skip: int = 0,
        third: int = 0,
    ) -> None:
        start = window_start(now, scan_download_days(minutes, sma_slow, third))
        end = now.replace(tzinfo=None)
        rows: list[ChopRow] = []
        errors: dict[str, int] = {}
        ok = 0
        gate = asyncio.Semaphore(CONCURRENCY)
        abort = asyncio.Event()

        async def one(symbol: str, delay: float) -> None:
            nonlocal ok
            await asyncio.sleep(delay)
            if abort.is_set():
                return
            async with gate:
                if abort.is_set():
                    return
                try:
                    if job is not None:
                        row = await job(symbol, start, end)
                    else:
                        frame, wide = scan_frames(await fetch(symbol, start, end), minutes, sma_slow, now)
                        row = analyze(frame, sma_fast, sma_slow, minutes, now, symbol=symbol, quiet_min=self.status.quiet_min, ha=ha, colour=colour, skip=skip, wide=wide, third=third)
                    ok += 1
                    if row is not None:
                        rows.append(row)
                except Exception as exc:  # noqa: BLE001
                    text = (str(exc).strip() or type(exc).__name__)[:200]
                    errors[text] = errors.get(text, 0) + 1
                    self.status.failed += 1
                    if ok == 0 and self.status.failed >= GIVE_UP_AFTER:
                        abort.set()
                finally:
                    self.status.done += 1

        try:
            await asyncio.gather(*(one(name, i * CALL_GAP_SEC) for i, name in enumerate(names)))
        except Exception as exc:  # noqa: BLE001
            log.exception("chop scan failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        if rows:
            # Nifty once per pass, for each row's move against the market. A failed read leaves the column empty.
            try:
                index_tape = await fetch(index_check.INDEX, window_start(now, WARMUP_DAYS), end)
                with_index(rows, index_check.day_move(index_tape))
            except Exception:  # noqa: BLE001
                log.info("chop scan: Nifty could not be read for the vs-Nifty column")
        # The most trending first, then mixed, then choppy. Inside a bucket:
        # TRENDING keeps the longest run on top; CHOPPY keeps the most crosses on top.
        bucket = {TRENDING: 0, MIXED: 1, CHOPPY: 2}
        rows.sort(
            key=lambda r: (
                bucket.get(r.score, 3),
                -(r.minutes_since_last_cross or 0.0) if r.score == TRENDING else -r.crosses_today if r.score == CHOPPY else 0,
            )
        )
        self.status.as_of = now.isoformat(timespec="seconds")
        self.status.market_open = market_is_open(now)
        # One alert per quiet runner per day. Nothing is traded here.
        if alert_on_quiet and alert_cb is not None and market_is_open(now):
            # Only stocks inside the chosen price range are alerted (0 = no bound).
            fresh = [
                row for row in rows
                if row.quiet_runner and in_price_range(row.ltp, price_min, price_max) and not _already_alerted(row.symbol, now)
            ]
            if fresh:
                # One message per scan: a low "quiet for" setting can flag hundreds of stocks.
                text = (
                    quiet_runner_text(fresh[0].symbol, fresh[0].minutes_since_last_cross or 0.0, fresh[0].last_cross_direction, fresh[0].ltp)
                    if len(fresh) == 1
                    else quiet_runners_text(fresh)
                )
                try:
                    alert_cb(text)
                except Exception:  # noqa: BLE001
                    log.exception("could not send the quiet-runner alert")
                else:
                    for row in fresh:
                        _mark_alerted(row.symbol, now)
        # First time today each stock read as quiet (market hours only), and when its Telegram went.
        first_quiet = _today_only(_FIRST_QUIET, now)
        alerted = _today_only(_ALERTED_TODAY, now)
        for row in rows:
            if row.quiet_runner and market_is_open(now):
                _note_first_quiet(row.symbol, now)
            row.first_quiet_at = first_quiet.get(row.symbol)
            row.alerted_at = alerted.get(row.symbol)
        self.status.rows = [r.as_dict() for r in rows]
        if ok == 0 and errors:
            self.status.error = max(errors, key=errors.get)
            self._key = None
        else:
            self.status.error = None if not errors else f"{self.status.failed} stock(s) could not be read ({max(errors, key=errors.get)})"
            self._key = key
        self.status.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        self.status.running = False
        if self.on_done is not None and ok > 0:
            try:
                self.on_done(self.snapshot(), self._meta)
            except Exception:  # noqa: BLE001
                log.exception("could not hand the finished scan to history")
