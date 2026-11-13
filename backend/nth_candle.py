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

* ``nth_side_rule`` — which way: ``NTH`` (that candle's colour, default), ``MAJORITY`` (the way most of
  candles 1..N went, a tie = no order) or ``AGREE`` (that candle only when most agree); "most" by
  ``nth_majority_by`` ``COUNT`` (green vs red) or ``SIZE`` (bodies added up). With MAJORITY the body
  floor is read on the winning side's move (the Nth candle when it agrees, else the net body).
* ``nth_dir_rule`` — what makes a candle up / down (``candle_moves``): ``COLOUR`` (green / red, default),
  ``CLOSES`` (its close vs the previous candle's close; candle 1 against the session before) or ``BOTH``
  (green and a higher close / red and a lower close; anything else counts like a doji). The body floor
  then reads that move (with CLOSES the change from the previous close).

Entry filters (VWAP, RSI, …), the stop, the flip, market hours, the cut-off, the caps and one
LIVE bot per stock all apply through ``StrategyEngine.apply_signal`` like any entry. With
``nth_skip_checks`` on, the order goes at once through ``StrategyEngine.direct_order`` instead: no entry
check, ADX or flip is read; market hours, the cut-off, the Trade list, the caps, the loss lock and one
LIVE bot per stock still apply. With
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
    "nth_side_rule": "NTH",
    "nth_majority_by": "COUNT",
    "nth_skip_checks": False,
    "nth_dir_rule": "COLOUR",
}
SIDE_RULES = ("NTH", "MAJORITY", "AGREE")
MAJORITY_BY = ("COUNT", "SIZE")
#: What makes a candle "up" or "down": COLOUR = green / red (close vs its own open); CLOSES = the close vs the previous
#: candle's close (candle 1 against the last close of the session before); BOTH = green AND a higher close (red AND a
#: lower close). A candle that is neither counts like a doji.
DIR_RULES = ("COLOUR", "CLOSES", "BOTH")


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


def side_rule(cfg) -> str:
    """How the side is picked: NTH = the Nth candle's colour; MAJORITY = the way most of candles 1..N went
    (a tie = no order); AGREE = the Nth candle's colour, only when most of candles 1..N agree with it."""
    value = str(setting(cfg, "nth_side_rule") or "NTH").upper()
    return value if value in SIDE_RULES else "NTH"


def majority_by(cfg) -> str:
    """How "most" is counted: COUNT = green vs red candles; SIZE = the bodies added up (signed, in rupees)."""
    value = str(setting(cfg, "nth_majority_by") or "COUNT").upper()
    return value if value in MAJORITY_BY else "COUNT"


def dir_rule(cfg) -> str:
    """What makes a candle up / down (DIR_RULES): its colour, its close vs the previous close, or both."""
    value = str(setting(cfg, "nth_dir_rule") or "COLOUR").upper()
    return value if value in DIR_RULES else "COLOUR"


def candle_moves(opens, closes, prev_close=None, rule: str = "COLOUR") -> list[float]:
    """Each candle's signed move under `rule` (> 0 up, < 0 down, 0 neither): COLOUR = close − open; CLOSES = close −
    the previous candle's close (`prev_close` for the first one; unknown = 0); BOTH = the body when the close also moved
    the same way against the previous close, else 0."""
    out: list[float] = []
    prev = None if prev_close is None else float(prev_close)
    for o, c in zip(opens, closes):
        o, c = float(o), float(c)
        body = c - o
        change = None if prev is None else c - prev
        if rule == "CLOSES":
            move = 0.0 if change is None else change
        elif rule == "BOTH":
            move = body if change is not None and body * change > 0 else 0.0
        else:
            move = body
        out.append(move)
        prev = c
    return out


def dir_words(rule: str) -> tuple[str, str]:
    """How an up / down candle is called under `rule`."""
    if rule == "CLOSES":
        return "closed higher", "closed lower"
    if rule == "BOTH":
        return "green and closed higher", "red and closed lower"
    return "green", "red"


def skip_checks(cfg) -> bool:
    """Enter at once on the call: no entry check (VWAP, RSI, ADX, …) is read (StrategyEngine.direct_order)."""
    return bool(setting(cfg, "nth_skip_checks"))


def majority(opens, closes, by: str = "COUNT", rule: str = "COLOUR", prev_close=None) -> tuple[str | None, int, int, float]:
    """The side most of these candles went: (LONG / SHORT / None for a tie, ups, downs, net move in rupees), each
    candle read by `rule` (candle_moves)."""
    bodies = candle_moves(opens, closes, prev_close, rule)
    greens = sum(1 for b in bodies if b > 0)
    reds = sum(1 for b in bodies if b < 0)
    net = sum(bodies)
    score = (greens - reds) if by == "COUNT" else net
    return ("LONG" if score > 0 else "SHORT" if score < 0 else None), greens, reds, net


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
    if "ts" in closed.columns:
        # The same candle by time of day, not by position: the oldest day of a live frame (now − 5 days) or of a
        # replay frame starts mid-session, so its "Nth candle" was a later one carrying a much bigger running
        # total, and volume vs usual read too low (1.5× on a day that was really 2×).
        ts = pd.to_numeric(closed["ts"], errors="coerce")
        tod = (ts + 19800) % 86400
        want = tod.loc[mine.index[n - 1]]
        for day in sorted({d for d in days if d < today}):
            hit = vols[(days == day) & (tod == want)]
            if len(hit) and not pd.isna(hit.iloc[-1]) and float(hit.iloc[-1]) > 0:
                usual.append(float(hit.iloc[-1]))
    else:
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
    earlier = closed[days < today]
    prev_close = float(earlier["close"].iloc[-1]) if not earlier.empty else None
    session = closed[days == today].reset_index(drop=True)
    if heikin_ashi.on(cfg):
        session = heikin_ashi.view(session, cfg)
    opens = session["open"].astype(float).tolist()
    closes = session["close"].astype(float).tolist()
    close = closes[-1]
    rule = side_rule(cfg)
    how = dir_rule(cfg)
    up_word, down_word = dir_words(how)
    moves = candle_moves(opens[:n], closes[:n], prev_close, how)
    most, greens, reds, net = majority(opens[:n], closes[:n], majority_by(cfg), how, prev_close)
    tally = f"candles 1-{n}: {greens} {up_word}, {reds} {down_word}" if majority_by(cfg) == "COUNT" else f"candles 1-{n} add up to {net:+.2f}"
    if rule == "MAJORITY":
        if most is None:
            return Call(None, today, f"{tally} — no majority, no order today")
        # The move the body floor reads: the Nth candle when it went the majority's way (counting), else the net body.
        sign = 1 if most == "LONG" else -1
        nth_body = moves[-1]
        move = abs(nth_body) if majority_by(cfg) == "COUNT" and nth_body * sign > 0 else abs(net)
        body = move * sign
    else:
        body = moves[-1]
        if body == 0:
            what = "a doji" if how == "COLOUR" else f"neither {up_word} nor {down_word}"
            return Call(None, today, f"{label} is {what} — no order today")
        if rule == "AGREE" and most != ("LONG" if body > 0 else "SHORT"):
            return Call(None, today, f"{label} {up_word if body > 0 else down_word} but {tally} — against the majority, no order today")
    if body == 0:
        return Call(None, today, f"{tally} — no move, no order today")
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
    side = "LONG" if body > 0 else "SHORT"
    vol = f", volume {rvol:g}× usual" if rvol is not None else ""
    if rule == "MAJORITY":
        return Call(side, today, f"most of the first {n} {size}-minute candles {'up' if side == 'LONG' else 'down'} ({tally}{vol})", rvol, body_pct)
    colour = up_word if body > 0 else down_word
    agree = f", {tally}" if rule == "AGREE" else ""
    what = f"{colour} {label} (body" if how == "COLOUR" else f"{label} {colour} (move"
    return Call(side, today, f"{what} {body_pct:.2f}%{agree}{vol})", rvol, body_pct)
