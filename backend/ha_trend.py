"""Heikin Ashi trend read for the Trend quality scan (owner's request: with Heikin Ashi candles picked, judge the trend
from the candles themselves, never from SMA crosses).

Read-only, no order path; nothing in the strategy reads it. `chop_scan.analyze(..., ha=True)` and the auto-arm replay
(`auto_arm.strong_read(..., ha=True)`) call it on Heikin Ashi bars (`chop_scan.ha_bars`) and put the answers in the same
row fields the SMA read fills, so the card, the filters, the rank, the alerts, auto-arm and the pick backtests follow:

    colour      each closed HA candle is green (close > open), red, or a doji (body under DOJI_SHARE of its range)
    flip        the colour changed between two non-doji candles (a doji never breaks or starts a run): the Heikin
                Ashi "cross". `crosses_today` / `last_cross_*` / Score count today's flips
    run         the current colour run across days (dojis inside it bridged): its start, its candles and its move
                (HA close now vs the HA open of its first candle, signed) and the widest move so far in it
                (the card's "trend age" and "fading": `run_*_by_minutes`, `gap_pct_by_minutes`, `gap_peak_pct_by_minutes`)

Strong trend: seven checks on the run (`checks`), side = the run's colour:

    streak      at least RUN_MIN closed candles of one colour
    nowick      at least 2 of the last 3 (fewer in a short run: all) have no wick against the trend (a green candle
                with no lower wick, a red one with no upper wick): the classic strong Heikin Ashi candle
    body        the last body is at least BODY_KEEP of the run's average body (the run is not running out of steam)
    structure   the last candle's low is above the low two candles back (bullish) / high below (bearish)
    vwap        the real last price on the trend's side of today's VWAP
    move        the run has moved at least MOVE_ATR x the ATR of these candles
    clean       no doji in the last 3 candles

The first three and VWAP are required (REQUIRED) and at least STRONG_MIN_PASSED of the seven must pass.

Phases: today's candles split by colour runs: a run of at least MIN_RUN candles is a BULL / BEAR phase, anything shorter
is CHOP (`trend_phases.summary` shapes the fields). Fresh trend: the colour turned within FRESH_CANDLES candles, the
new colour held, the last candle has no wick against it, the real price is beyond VWAP and today's volume is
FRESH_MIN_RVOL x usual (`trend_strength._opening_rvol`).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import trend_phases
import trend_strength

HA_MODE = "HEIKIN_ASHI"
DOJI_SHARE = 0.10   # a body under 10 % of the candle's range is a doji (no colour)
WICK_SHARE = 0.05   # a wick under 5 % of the range counts as no wick
RUN_MIN = 3
BODY_KEEP = 0.8
MOVE_ATR = 1.0
STRONG_MIN_PASSED = 6
REQUIRED = ("streak", "nowick", "vwap")
CHECKS = ("streak", "nowick", "body", "structure", "vwap", "move", "clean")
MIN_RUN = 5          # a phase: at least 5 candles of one colour
FRESH_CANDLES = 3
# Score: a trending day flips little; a choppy one flips often or in short runs.
CHOP_MIN_FLIPS = 5
CHOP_SHORT_RUN = 3   # flips on average fewer than 3 candles apart = choppy
TREND_MAX_FLIPS = 2
TREND_RUN_CANDLES = 4
LONG_RUN_MIN = 60

NOTES = {
    "streak": f"At least {RUN_MIN} Heikin Ashi candles of the trend's colour in a row",
    "nowick": "Most of the last candles have no wick against the trend (green with no lower wick / red with no upper wick)",
    "body": "The last body is not much smaller than the run's average (the trend is not tiring)",
    "structure": "Higher lows (green run) / lower highs (red run) over the last candles",
    "vwap": "The real price is on the trend's side of today's VWAP",
    "move": f"The run has moved at least {MOVE_ATR:g} ATR",
    "clean": "No doji (tiny body) in the last 3 candles",
}


def _arr(frame: pd.DataFrame, col: str) -> np.ndarray:
    return pd.to_numeric(frame[col], errors="coerce").to_numpy(dtype="float64")


def colours(bars: pd.DataFrame) -> np.ndarray:
    """+1 green, -1 red, 0 doji (or no data) for each candle."""
    if bars is None or len(bars) == 0:
        return np.zeros(0, dtype="int64")
    o, c, h, lo = _arr(bars, "open"), _arr(bars, "close"), _arr(bars, "high"), _arr(bars, "low")
    body = c - o
    rng = np.where(np.isfinite(h - lo) & (h - lo > 0), h - lo, np.nan)
    doji = ~np.isfinite(body) | (np.abs(body) < DOJI_SHARE * np.nan_to_num(rng, nan=np.inf)) | (body == 0)
    out = np.sign(np.nan_to_num(body)).astype("int64")
    out[doji] = 0
    return out


def _filled(col: np.ndarray) -> np.ndarray:
    """Each doji takes the colour of the last coloured candle before it (0 before any colour)."""
    out = col.copy()
    last = 0
    for i, v in enumerate(out):
        if v:
            last = v
        else:
            out[i] = last
    return out


def flips(bars: pd.DataFrame, today: int) -> list[dict]:
    """Today's colour changes (between non-doji candles): [{ts, close, side}] like `chop_scan._crosses`."""
    col = colours(bars)
    if len(col) < 2:
        return []
    ts = bars["ts"].astype("int64").to_numpy()
    close = _arr(bars, "close")
    out, last = [], 0
    for i, v in enumerate(col):
        if not v:
            continue
        if last and v != last and (int(ts[i]) + 19_800) // 86_400 == today:
            out.append({"ts": int(ts[i]), "close": float(close[i]), "side": "BULLISH" if v > 0 else "BEARISH"})
        last = v
    return out


def side(bars: pd.DataFrame) -> str | None:
    col = _filled(colours(bars))
    return None if not len(col) or col[-1] == 0 else ("BULLISH" if col[-1] > 0 else "BEARISH")


def _run_start(filled: np.ndarray) -> int:
    other = (filled != filled[-1]).nonzero()[0]
    return int(other[-1]) + 1 if len(other) else 0


def run_read(bars: pd.DataFrame):
    """(start ts, candles, signed move % now, widest |move| % so far) of the current colour run, across days."""
    if bars is None or len(bars) == 0:
        return None, None, None, None
    filled = _filled(colours(bars))
    if not len(filled) or filled[-1] == 0:
        return None, None, None, None
    first = _run_start(filled)
    o, c = _arr(bars, "open"), _arr(bars, "close")
    base = o[first]
    if not np.isfinite(base) or base == 0:
        return None, None, None, None
    moves = (c[first:] / base - 1) * 100
    moves = moves[np.isfinite(moves)]
    if not len(moves):
        return None, None, None, None
    ts = bars["ts"].astype("int64").to_numpy()
    return int(ts[first]), int(len(filled) - first), round(float(moves[-1]), 4), round(float(np.abs(moves).max()), 4)


def score(hits: list[dict], minutes_since: float | None, avg_gap: float | None, minutes: int) -> str:
    """TRENDING / MIXED / CHOPPY from today's colour flips (the Heikin Ashi counterpart of `chop_scan.score_for`)."""
    n = len(hits)
    if n >= CHOP_MIN_FLIPS or (n >= 2 and avg_gap is not None and avg_gap < CHOP_SHORT_RUN * minutes):
        return "CHOPPY"
    long_ago = max(LONG_RUN_MIN, TREND_RUN_CANDLES * minutes)
    if n == 0 or (n <= TREND_MAX_FLIPS and minutes_since is not None and minutes_since >= long_ago):
        return "TRENDING"
    return "MIXED"


def checks(bars: pd.DataFrame, vwap: float | None, price: float | None) -> dict:
    """The strong-trend read in `trend_strength.read`'s shape, judged on the Heikin Ashi candles."""
    out = {"trend": None, "trend_side": None, "trend_score": 0, "trend_checks": {}, "trend_mode": HA_MODE,
           "trend_notes": dict(NOTES), "sma_gap_pct": None, "vwap": round(vwap, 2) if vwap is not None else None,
           "rvol": None, "trend_days": 0}
    if bars is None or len(bars) < RUN_MIN:
        return out
    col = colours(bars)
    filled = _filled(col)
    if filled[-1] == 0:
        return out
    up = filled[-1] > 0
    sign = 1 if up else -1
    first = _run_start(filled)
    run = len(filled) - first
    o, c, h, lo = _arr(bars, "open"), _arr(bars, "close"), _arr(bars, "high"), _arr(bars, "low")
    rng = np.maximum(h - lo, 1e-9)
    against = (np.minimum(o, c) - lo) if up else (h - np.maximum(o, c))  # the wick against the trend
    last_n = list(range(max(first, len(c) - 3), len(c)))
    nowick = [against[i] <= WICK_SHARE * rng[i] for i in last_n]
    bodies = np.abs(c[first:] - o[first:])
    atr = None
    if "atr_14" in bars:
        a = pd.to_numeric(bars["atr_14"], errors="coerce").iloc[-1]
        atr = float(a) if pd.notna(a) and a > 0 else None
    move = abs(c[-1] - o[first])
    k = len(c) - 1
    found = {
        "streak": run >= RUN_MIN,
        "nowick": len(nowick) > 0 and sum(nowick) >= (2 if len(nowick) >= 3 else len(nowick)),
        "body": len(bodies) > 0 and float(bodies.mean()) > 0 and abs(c[k] - o[k]) >= BODY_KEEP * float(bodies.mean()),
        "structure": k >= 2 and ((lo[k] > lo[k - 2]) if up else (h[k] < h[k - 2])),
        "vwap": vwap is not None and price is not None and (price - vwap) * sign > 0,
        "move": atr is not None and move >= MOVE_ATR * atr,
        "clean": bool((col[-3:] != 0).all()) if len(col) >= 3 else False,
    }
    found = {key: bool(found[key]) for key in CHECKS}
    passed = sum(found.values())
    strong = all(found[key] for key in REQUIRED) and passed >= STRONG_MIN_PASSED
    run_move = (c[-1] / o[first] - 1) * 100 if o[first] else None
    out.update({
        "trend": ("STRONG_BULLISH" if up else "STRONG_BEARISH") if strong else None,
        "trend_side": "BULLISH" if up else "BEARISH",
        "trend_score": round(passed / len(CHECKS) * 100),
        "trend_checks": found,
        "sma_gap_pct": round(float(run_move), 4) if run_move is not None and np.isfinite(run_move) else None,
    })
    return out


def phases(bars: pd.DataFrame, today_start: int) -> list[dict]:
    """Today's phases by colour runs, in `trend_phases.phases`' shape: a run of MIN_RUN+ candles is BULL / BEAR."""
    if bars is None or len(bars) == 0:
        return []
    filled = _filled(colours(bars))
    ts = bars["ts"].astype("int64").to_numpy()
    close = _arr(bars, "close")
    labels = [trend_phases.CHOP] * len(filled)
    i = 0
    while i < len(filled):
        j = i
        while j < len(filled) and filled[j] == filled[i]:
            j += 1
        if filled[i] != 0 and j - i >= MIN_RUN:
            for x in range(i, j):
                labels[x] = trend_phases.BULL if filled[i] > 0 else trend_phases.BEAR
        i = j
    out: list[dict] = []
    i = int(np.searchsorted(ts, today_start))
    while i < len(labels):
        j = i
        while j < len(labels) and labels[j] == labels[i]:
            j += 1
        base = close[i - 1] if i > 0 else close[i]
        out.append({"side": labels[i], "start_ts": int(ts[i]), "end_ts": int(ts[j - 1]), "candles": j - i,
                    "move_pct": round((close[j - 1] / base - 1) * 100, 2) if base else None})
        i = j
    return out


def summary(found: list[dict]) -> dict:
    """`trend_phases.summary`, with "too early" meaning fewer than MIN_RUN of today's candles have closed."""
    out = trend_phases.summary(found)
    out["phase_early"] = bool(found) and found[-1]["side"] == trend_phases.CHOP and sum(p["candles"] for p in found) < MIN_RUN
    return out


def fresh(bars: pd.DataFrame, vwap: float | None, now: dt.datetime, tape: pd.DataFrame | None,
          price: float | None) -> dict:
    """A Heikin Ashi trend that has only just started (`trend_strength.fresh`'s shape)."""
    out = {"fresh": None, "fresh_checks": {}, "fresh_rvol": None}
    if bars is None or len(bars) < FRESH_CANDLES + 2:
        return out
    col = colours(bars)
    filled = _filled(col)
    if filled[-1] == 0:
        return out
    up = filled[-1] > 0
    sign = 1 if up else -1
    first = _run_start(filled)
    run = len(filled) - first
    o, c, h, lo = (_arr(bars, k) for k in ("open", "close", "high", "low"))
    k = len(c) - 1
    against = (min(o[k], c[k]) - lo[k]) if up else (h[k] - max(o[k], c[k]))
    found = {
        "turn": first > 0 and run <= FRESH_CANDLES and col[k] == filled[-1],
        "nowick": against <= WICK_SHARE * max(h[k] - lo[k], 1e-9),
        "vwap": vwap is not None and price is not None and (price - vwap) * sign > 0,
    }
    rvol = None
    if all(found.values()):
        rvol, days = trend_strength._opening_rvol(tape, now)
        found["volume"] = rvol is not None and days >= trend_strength.VOLUME_MIN_SESSIONS and rvol >= trend_strength.FRESH_MIN_RVOL
    else:
        found["volume"] = False
    out["fresh_checks"] = {key: bool(v) for key, v in found.items()}
    out["fresh_rvol"] = round(rvol, 2) if rvol is not None else None
    if all(found.values()):
        out["fresh"] = "BULLISH" if up else "BEARISH"
    return out
