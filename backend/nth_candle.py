"""Second candle entries for the SMA terminal (``entry_mode = "NTH_CANDLE"``).

The owner's Most active routine as a bot entry: read the day's Nth candle (``nth_candle_no``,
default the 2nd; ``nth_candle_minutes`` 5-minute candles by default), buy on green / sell on red at
its close (the order goes at the start of the next candle), once per stock per day. Nothing else
opens a trade in this mode: no SMA cross, no pattern. The bot's own stop, trailing stop, target
and the ticked exits run the trade, and the square-off closes what is left. The same rule the
Scalp page's "Second candle" test measures (second_candle.py), so a test there can be traded here.

In this mode the bot's own candle is the second-candle size (candles.candle_minutes), so the ATR,
the exits and the chart all read those candles, and the Nth candle is simply the Nth closed candle
of the session in the bot's frame.

Checks, all settings (per bot and per stock):

* ``nth_min_body_pct`` — a candle whose body |close − open| is under this % of its close is skipped
  (0 = any body; a doji is always skipped).
* ``nth_min_rvol`` — volume vs usual at that candle's close must be at least this (0 = off):
  Groww's running session volume at the Nth candle against the same candle of the earlier
  sessions in the frame. Unknown (no earlier session, no volume) refuses while the check is on.

Entry filters (VWAP, RSI, …), the stop, the flip, market hours, the cut-off, the caps and one
LIVE bot per stock all apply through ``StrategyEngine.apply_signal`` like any entry. With
Heikin Ashi colours on, the Heikin Ashi colour is read.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

import heikin_ashi

IST = ZoneInfo("Asia/Kolkata")
MODE = "NTH_CANDLE"
ENTRY_REASON = "NTH_CANDLE"
CANDLE_NO_MAX = 6
MINUTES = (1, 3, 5, 10, 15)
BODY_MAX = 2.0
RVOL_MAX = 10.0

DEFAULTS = {
    "nth_candle_no": 2,
    "nth_candle_minutes": 5,
    "nth_min_body_pct": 0.0,
    "nth_min_rvol": 0.0,
}


def setting(cfg, name: str):
    value = getattr(cfg, name, None) if not isinstance(cfg, dict) else cfg.get(name)
    return DEFAULTS[name] if value is None else value


def on(cfg) -> bool:
    mode = getattr(cfg, "entry_mode", None) if not isinstance(cfg, dict) else cfg.get("entry_mode")
    return str(mode or "SMA").upper() == MODE


def candle_no(cfg) -> int:
    try:
        return min(max(int(setting(cfg, "nth_candle_no") or 2), 1), CANDLE_NO_MAX)
    except (TypeError, ValueError):
        return 2


def minutes(cfg) -> int:
    try:
        value = int(setting(cfg, "nth_candle_minutes") or 5)
    except (TypeError, ValueError):
        return 5
    return value if value in MINUTES else 5


def min_body_pct(cfg) -> float:
    try:
        return min(max(float(setting(cfg, "nth_min_body_pct") or 0.0), 0.0), BODY_MAX)
    except (TypeError, ValueError):
        return 0.0


def min_rvol(cfg) -> float:
    try:
        return min(max(float(setting(cfg, "nth_min_rvol") or 0.0), 0.0), RVOL_MAX)
    except (TypeError, ValueError):
        return 0.0


def ordinal(n: int) -> str:
    return {1: "1st", 2: "2nd", 3: "3rd"}.get(int(n), f"{int(n)}th")


@dataclass
class Call:
    side: str | None  # "LONG" | "SHORT" | None
    day: dt.date | None  # the session the Nth candle belongs to (None while waiting)
    note: str
    rvol: float | None = None
    body_pct: float | None = None


def _session_days(closed: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(closed["ts"].astype("int64"), unit="s", utc=True).dt.tz_convert(IST).dt.date


def rvol_at(closed: pd.DataFrame, days: pd.Series, today: dt.date, n: int) -> float | None:
    """Volume vs usual at the close of today's Nth candle: Groww's running session volume there
    against the mean of the same candle on the earlier sessions in the frame (None = unknown)."""
    if "volume" not in closed.columns:
        return None
    vols = pd.to_numeric(closed["volume"], errors="coerce")
    mine = vols[days == today]
    if len(mine) < n or pd.isna(mine.iloc[n - 1]) or float(mine.iloc[n - 1]) <= 0:
        return None
    usual = []
    for day in sorted({d for d in days if d < today}):
        part = vols[days == day]
        if len(part) >= n and not pd.isna(part.iloc[n - 1]) and float(part.iloc[n - 1]) > 0:
            usual.append(float(part.iloc[n - 1]))
    if not usual:
        return None
    return round(float(mine.iloc[n - 1]) / (sum(usual) / len(usual)), 2)


def call(frame: pd.DataFrame | None, cfg, done_for: dt.date | None = None) -> Call:
    """The order the day's Nth closed candle asks for, read once when it has just closed.

    `frame` is the bot's candle frame (nth_candle_minutes candles) with the forming candle last.
    `done_for` is the day already acted on, so the same candle never orders twice.
    """
    n = candle_no(cfg)
    size = minutes(cfg)
    closed = frame.iloc[:-1] if frame is not None and len(frame) >= 2 else None
    if closed is None or closed.empty or "ts" not in closed.columns:
        return Call(None, None, "waiting for candles")
    days = _session_days(closed)
    today = days.iloc[-1]
    count = int((days == today).sum())
    label = f"{ordinal(n)} {size}-minute candle"
    if done_for == today:
        return Call(None, today, f"the {label} was read today — no more orders until tomorrow")
    if count < n:
        return Call(None, today, f"waiting for the {label} to close ({count} of {n} closed)")
    if count > n:
        return Call(None, today, f"the {label} closed earlier — no order after it")
    candle = closed.iloc[-1]
    if heikin_ashi.on(cfg):
        ha = heikin_ashi.view(closed[days == today].reset_index(drop=True), cfg)
        open_, close = float(ha["open"].iloc[-1]), float(ha["close"].iloc[-1])
    else:
        open_, close = float(candle["open"]), float(candle["close"])
    body = close - open_
    if body == 0:
        return Call(None, today, f"{label} is a doji — no order today")
    body_pct = round(abs(body) / close * 100, 4) if close > 0 else 0.0
    floor = min_body_pct(cfg)
    if floor > 0 and body_pct < floor:
        return Call(None, today, f"{label} body {body_pct:.3f}% is under {floor:g}% — no order today", body_pct=body_pct)
    rvol = rvol_at(closed, days, today, n)
    need = min_rvol(cfg)
    if need > 0:
        if rvol is None:
            return Call(None, today, f"{label}: volume vs usual not known (no earlier session) — no order today", body_pct=body_pct)
        if rvol < need:
            return Call(None, today, f"{label}: volume {rvol:g}× usual is under {need:g}× — no order today", rvol, body_pct)
    colour = "green" if body > 0 else "red"
    side = "LONG" if body > 0 else "SHORT"
    vol = f", volume {rvol:g}× usual" if rvol is not None else ""
    return Call(side, today, f"{colour} {label} (body {body_pct:.2f}%{vol})", rvol, body_pct)
