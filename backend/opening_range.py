"""Opening range (opening_range.py): four checks on the first 15 minutes, each its own tick box.

Read on the bot's own closed candles of today, once the 09:15–09:30 bars are closed
(the window is always 09:15–09:30 IST whatever the bot's candle size). All four
are off by default and judged per bot and per stock, in PAPER and LIVE.

* **Volume** (`or_volume`): the 09:15–09:30 volume is at least `or_volume_ratio`
  times the average volume of the same minutes on the previous sessions' data kept
  in the frame; the frame only has today plus the lookback, so it compares with the
  average per-minute volume of the 20 minutes before 09:15 (the same arithmetic
  `minute_volume_stats` uses). Missing volume refuses the buy or sell.
* **VWAP** (`or_vwap`): all candles of the window closed on the trade's side of
  session VWAP (above for a buy, below for a sell).
* **SMA spread** (`or_spread`): the SMA fast and slow are pulling apart: the gap in
  % of the slow line is at least `or_spread_pct` on the last closed candle and
  wider than it was on the candle before (the same signed gap the bot already reads).
* **Break** (`or_break`): on the first candle that closes at or after 09:30, the
  close is above the window high (buy) or below the window low (sell).

`entry_reason` returns why a check refuses, or None when every ticked check agrees.
A check that is not ready (no window yet, no VWAP, no volume) refuses rather than
passes. Nothing here places an order on its own; the engine decides.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

from indicators import derive_minute_volume, session_vwap_series

IST = ZoneInfo("Asia/Kolkata")

WINDOW_START = dt.time(9, 15)
WINDOW_END = dt.time(9, 30)
DEFAULT_VOLUME_RATIO = 2.0
DEFAULT_SPREAD_PCT = 0.10
LOOKBACK = 20

CHECKS = ("or_volume", "or_vwap", "or_spread", "or_break")


def _num(value, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return default if pd.isna(out) else out


def ratio(cfg) -> float:
    return max(0.0, _num(getattr(cfg, "or_volume_ratio", None), DEFAULT_VOLUME_RATIO))


def spread_pct(cfg) -> float:
    return max(0.0, _num(getattr(cfg, "or_spread_pct", None), DEFAULT_SPREAD_PCT))


def on(cfg, name: str) -> bool:
    return bool(getattr(cfg, f"use_{name}", False))


def _today(closed: pd.DataFrame) -> pd.DataFrame:
    """The session of the last closed candle (IST): the day the window belongs to.

    Read from the candles, not the wall clock, so a replay of a past day gives the same answer.
    """
    if closed.empty:
        return closed
    last = dt.datetime.fromtimestamp(int(closed["ts"].iloc[-1]), IST).date()
    days = pd.to_datetime(closed["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    return closed[days == last]


def _window(today: pd.DataFrame) -> pd.DataFrame:
    """Candles that start inside 09:15–09:30 IST."""
    times = pd.to_datetime(today["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.time
    return today[(times >= WINDOW_START) & (times < WINDOW_END)]


def _window_ready(today: pd.DataFrame) -> bool:
    """The window is complete once a candle at or after 09:30 has closed."""
    if today.empty:
        return False
    times = pd.to_datetime(today["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.time
    return bool((times >= WINDOW_END).any())


def _window_minutes(closed: pd.DataFrame) -> pd.DataFrame:
    """Every closed candle that starts inside 09:15–09:30 IST, with its IST session date and minute volume."""
    ist = pd.to_datetime(closed["ts"], unit="s", utc=True).dt.tz_convert(IST)
    mask = (ist.dt.time >= WINDOW_START) & (ist.dt.time < WINDOW_END)
    if "minute_volume" in closed.columns:
        vol = pd.to_numeric(closed["minute_volume"], errors="coerce")
    else:
        vol = derive_minute_volume(closed)
    out = pd.DataFrame({"session": ist.dt.date, "vol": vol})
    return out[mask]


def _volume_refusal(closed: pd.DataFrame, today: pd.DataFrame, window: pd.DataFrame, cfg) -> str | None:
    """Today's opening volume per minute against the usual opening volume per minute (earlier sessions in the frame)."""
    need = ratio(cfg)
    if not _window_ready(today) or window.empty:
        return "opening range not complete"
    minutes = _window_minutes(closed)
    if minutes.empty:
        return "opening volume not available"
    today_day = minutes["session"].iloc[-1]
    now = minutes[minutes["session"] == today_day]["vol"].dropna()
    earlier = minutes[minutes["session"] < today_day].dropna(subset=["vol"])
    if now.empty:
        return "opening volume not available"
    per_session = earlier.groupby("session")["vol"].mean().iloc[-LOOKBACK:]
    if per_session.empty:
        return "opening volume: no earlier session to compare"
    usual = float(per_session.mean())
    today_per_minute = float(now.mean())
    if usual <= 0:
        return "opening volume: no earlier volume to compare"
    if today_per_minute < need * usual:
        return f"opening volume {today_per_minute / usual:.1f}× the usual (needs {need:g}×)"
    return None


def _vwap_refusal(closed: pd.DataFrame, today: pd.DataFrame, window: pd.DataFrame, direction: str) -> str | None:
    if window.empty or not _window_ready(today):
        return "opening range not complete"
    vwap = session_vwap_series(closed).loc[window.index]
    if vwap.isna().any():
        return "opening VWAP not ready"
    close = pd.to_numeric(window["close"], errors="coerce")
    if direction == "LONG":
        bad = close <= vwap
        side = "above"
    else:
        bad = close >= vwap
        side = "below"
    if bool(bad.any()):
        return f"opening candles were not all {side} VWAP"
    return None


def _spread_refusal(closed: pd.DataFrame, direction: str, cfg) -> str | None:
    if len(closed) < 2:
        return "SMA spread not ready"
    fast, slow = closed["sma_9"], closed["sma_21"]
    if pd.isna(fast.iloc[-1]) or pd.isna(slow.iloc[-1]) or pd.isna(fast.iloc[-2]) or pd.isna(slow.iloc[-2]):
        return "SMA spread not ready"
    gap_now = (float(fast.iloc[-1]) - float(slow.iloc[-1])) / float(slow.iloc[-1]) * 100
    gap_before = (float(fast.iloc[-2]) - float(slow.iloc[-2])) / float(slow.iloc[-2]) * 100
    need = spread_pct(cfg)
    signed = gap_now if direction == "LONG" else -gap_now
    wider = abs(gap_now) > abs(gap_before) and (gap_now > 0) == (direction == "LONG")
    if signed < need or not wider:
        return f"SMA gap {gap_now:+.3f}% is not pulling apart (needs {need:g}% on the {'buy' if direction == 'LONG' else 'sell'} side)"
    return None


def _break_refusal(today: pd.DataFrame, window: pd.DataFrame, direction: str) -> str | None:
    if window.empty or not _window_ready(today):
        return "opening range not complete"
    after = today[pd.to_datetime(today["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.time >= WINDOW_END]
    if after.empty:
        return "waiting for the 09:30 candle to close"
    close = float(after["close"].iloc[-1])
    high, low = float(window["high"].max()), float(window["low"].min())
    if direction == "LONG" and close <= high:
        return f"no break above the 09:15–09:30 high {high:.2f} (close {close:.2f})"
    if direction == "SHORT" and close >= low:
        return f"no break below the 09:15–09:30 low {low:.2f} (close {close:.2f})"
    return None


def entry_reason(frame: pd.DataFrame, direction: str, cfg, only: str | None = None) -> str | None:
    """Why the ticked opening-range checks refuse this entry, or None when they all agree.

    `only` reads one check (for the chart's per-check message), as `_entry_block` does.
    """
    if frame is None or len(frame) < 2:
        return "opening range not ready"
    closed = frame.iloc[:-1]
    today = _today(closed)
    window = _window(today)

    def wanted(name: str) -> bool:
        return on(cfg, name) and (only is None or only == name)

    reasons: list[str] = []
    if wanted("or_volume"):
        reasons.append(_volume_refusal(closed, today, window, cfg) or "")
    if wanted("or_vwap"):
        reasons.append(_vwap_refusal(closed, today, window, direction) or "")
    if wanted("or_spread"):
        reasons.append(_spread_refusal(closed, direction, cfg) or "")
    if wanted("or_break"):
        reasons.append(_break_refusal(today, window, direction) or "")
    reasons = [r for r in reasons if r]
    return "; ".join(reasons) if reasons else None


def any_on(cfg) -> bool:
    return any(on(cfg, name) for name in CHECKS)


def passed_text(frame: pd.DataFrame, direction: str, cfg, name: str) -> str:
    """The reading behind an opening-range check that agrees, for the chart's check line."""
    closed = frame.iloc[:-1]
    today = _today(closed)
    window = _window(today)
    if name == "or_break" and not window.empty:
        high, low = float(window["high"].max()), float(window["low"].min())
        level = high if direction == "LONG" else low
        return f"Opening range: broke the 09:15–09:30 {'high' if direction == 'LONG' else 'low'} {level:.2f}"
    if name == "or_vwap" and not window.empty:
        return f"Opening range: all candles {'above' if direction == 'LONG' else 'below'} VWAP"
    if name == "or_volume":
        return f"Opening range: volume at least {ratio(cfg):g}× the usual"
    return f"Opening range: {name.removeprefix('or_')} agrees"
