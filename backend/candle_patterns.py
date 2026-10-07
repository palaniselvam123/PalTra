"""Candle-pattern entries for the SMA terminal (``entry_mode = "PATTERN"``).

Instead of an SMA cross, each time a candle of ``pattern_tf`` minutes (1, 3 or
5, built from the 1-minute tape from 09:15) closes, the pattern on it is read.
A bullish pattern buys at the start of the next candle, a bearish one sells
short; either way the trade is closed at the end of that candle
(``CANDLE_END``), then the next candle is judged afresh.

The pattern names follow ``frontend/src/lib/candlePatterns.ts`` (the chart's
data table), so what the table calls a candle is what the bot acted on.

Two optional checks, both settings:

* ``pattern_trend`` — a bullish pattern only while SMA fast is above SMA slow
  on the last closed 1-minute candle, a bearish one only below.
* ``pattern_min_edge`` — the candles must usually move more than the trade
  costs: the average range of the last 14 pattern candles, as % of price,
  must be at least this many times the round-trip charges as % of the trade
  (0 turns it off). A one-candle trade pays a full round of charges each time.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from charges import calculate_charges

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN_MIN = 9 * 60 + 15
PATTERN_TFS = (1, 3, 5)
PATTERN_SETS = ("STRONG", "ALL")
ENTRY_MODES = ("SMA", "PATTERN")

DEFAULTS = {
    "entry_mode": "SMA",
    "pattern_tf": 1,
    "pattern_trend": False,
    "pattern_set": "STRONG",
    "pattern_min_edge": 1.5,
}

# Patterns that order. STRONG: the multi-candle and full-body signals; ALL adds
# the single-candle shapes and the softer two-candle ones.
STRONG_BULL = {"Bullish engulfing", "Piercing line", "Morning star", "Three white soldiers", "Bullish marubozu"}
STRONG_BEAR = {"Bearish engulfing", "Dark cloud cover", "Evening star", "Three black crows", "Bearish marubozu"}
SOFT_BULL = {"Hammer", "Inverted hammer", "Bullish harami", "Tweezer bottom", "Dragonfly doji"}
SOFT_BEAR = {"Hanging man", "Shooting star", "Bearish harami", "Tweezer top", "Gravestone doji"}


def setting(cfg, name: str):
    value = getattr(cfg, name, None)
    return DEFAULTS[name] if value is None else value


def uses_patterns(cfg) -> bool:
    return str(setting(cfg, "entry_mode")).upper() == "PATTERN"


# ---- pattern names (port of candlePatterns.ts) ----------------------------------------------------


@dataclass
class _Shape:
    range: float
    body: float
    upper: float
    lower: float
    bull: bool
    bear: bool
    mid: float


def _shape(c: dict) -> _Shape:
    rng = max(c["high"] - c["low"], 0.0)
    return _Shape(
        range=rng,
        body=abs(c["close"] - c["open"]),
        upper=c["high"] - max(c["open"], c["close"]),
        lower=min(c["open"], c["close"]) - c["low"],
        bull=c["close"] > c["open"],
        bear=c["close"] < c["open"],
        mid=(c["open"] + c["close"]) / 2,
    )


def _trend_before(rows: list[dict], i: int) -> int:
    start = max(0, i - 5)
    if i - start < 3:
        return 0
    first = rows[start]["close"]
    last = rows[i - 1]["close"]
    span = max(r["high"] for r in rows[start:i]) - min(r["low"] for r in rows[start:i])
    if span <= 0:
        return 0
    move = (last - first) / span
    return 1 if move > 0.3 else -1 if move < -0.3 else 0


def _average_body(rows: list[dict], i: int) -> float:
    start = max(0, i - 10)
    bodies = [abs(r["close"] - r["open"]) for r in rows[start:i]]
    if not bodies:
        return abs(rows[i]["close"] - rows[i]["open"])
    return sum(bodies) / len(bodies)


def _single(s: _Shape, avg: float, trend: int) -> tuple[str, str] | None:
    if s.range <= 0:
        return "Flat", "neutral"
    share = s.body / s.range
    if share <= 0.1:
        if s.lower >= s.range * 0.6 and s.upper <= s.range * 0.1:
            return "Dragonfly doji", "bullish"
        if s.upper >= s.range * 0.6 and s.lower <= s.range * 0.1:
            return "Gravestone doji", "bearish"
        if s.upper >= s.range * 0.3 and s.lower >= s.range * 0.3:
            return "Long-legged doji", "neutral"
        return "Doji", "neutral"
    if s.lower >= s.body * 2 and s.upper <= s.body * 0.5:
        return ("Hanging man", "bearish") if trend > 0 else ("Hammer", "bullish")
    if s.upper >= s.body * 2 and s.lower <= s.body * 0.5:
        return ("Shooting star", "bearish") if trend > 0 else ("Inverted hammer", "bullish")
    if share >= 0.9 and s.body >= avg:
        return ("Bullish marubozu", "bullish") if s.bull else ("Bearish marubozu", "bearish")
    if share <= 0.3 and s.upper >= s.body and s.lower >= s.body:
        return "Spinning top", "neutral"
    return None


def _double(p: dict, c: dict, ps: _Shape, s: _Shape, trend: int) -> tuple[str, str] | None:
    p_top, p_bot = max(p["open"], p["close"]), min(p["open"], p["close"])
    top, bot = max(c["open"], c["close"]), min(c["open"], c["close"])
    if ps.bear and s.bull and bot <= p_bot and top >= p_top and s.body > ps.body:
        return "Bullish engulfing", "bullish"
    if ps.bull and s.bear and bot <= p_bot and top >= p_top and s.body > ps.body:
        return "Bearish engulfing", "bearish"
    if ps.bear and s.bull and top < p_top and bot > p_bot and ps.body > 0 and s.body < ps.body * 0.6:
        return "Bullish harami", "bullish"
    if ps.bull and s.bear and top < p_top and bot > p_bot and ps.body > 0 and s.body < ps.body * 0.6:
        return "Bearish harami", "bearish"
    if ps.bear and s.bull and c["open"] < p["close"] and c["close"] > ps.mid and c["close"] < p["open"]:
        return "Piercing line", "bullish"
    if ps.bull and s.bear and c["open"] > p["close"] and c["close"] < ps.mid and c["close"] > p["open"]:
        return "Dark cloud cover", "bearish"
    tick = max(ps.range, s.range) * 0.05
    if trend < 0 and ps.bear and s.bull and abs(p["low"] - c["low"]) <= tick:
        return "Tweezer bottom", "bullish"
    if trend > 0 and ps.bull and s.bear and abs(p["high"] - c["high"]) <= tick:
        return "Tweezer top", "bearish"
    return None


def _triple(a: dict, b: dict, c: dict, as_: _Shape, bs: _Shape, cs: _Shape) -> tuple[str, str] | None:
    small_middle = bs.body <= min(as_.body, cs.body) * 0.5
    if as_.bear and small_middle and cs.bull and c["close"] > as_.mid and as_.body > 0:
        return "Morning star", "bullish"
    if as_.bull and small_middle and cs.bear and c["close"] < as_.mid and as_.body > 0:
        return "Evening star", "bearish"

    def strong(s: _Shape) -> bool:
        return s.range > 0 and s.body / s.range >= 0.6

    rising = (
        as_.bull and bs.bull and cs.bull and b["close"] > a["close"] and c["close"] > b["close"]
        and b["open"] > a["open"] and c["open"] > b["open"]
    )
    if rising and strong(as_) and strong(bs) and strong(cs):
        return "Three white soldiers", "bullish"
    falling = (
        as_.bear and bs.bear and cs.bear and b["close"] < a["close"] and c["close"] < b["close"]
        and b["open"] < a["open"] and c["open"] < b["open"]
    )
    if falling and strong(as_) and strong(bs) and strong(cs):
        return "Three black crows", "bearish"
    return None


def candle_pattern(rows: list[dict], i: int) -> tuple[str, str]:
    """(name, bias) of candle `i`: three-candle patterns first, then two, then one."""
    c = rows[i]
    s = _shape(c)
    trend = _trend_before(rows, i)
    if i >= 2:
        hit = _triple(rows[i - 2], rows[i - 1], c, _shape(rows[i - 2]), _shape(rows[i - 1]), s)
        if hit:
            return hit
    if i >= 1:
        hit = _double(rows[i - 1], c, _shape(rows[i - 1]), s, trend)
        if hit:
            return hit
    avg = _average_body(rows, i)
    one = _single(s, avg, trend)
    if one:
        return one
    size = "Long " if avg > 0 and s.body >= avg * 1.5 else "Small " if avg > 0 and s.body <= avg * 0.5 else ""
    word = "bullish" if s.bull else "bearish" if s.bear else "flat"
    name = f"{size}{word}" if size else word.capitalize()
    return name, ("bullish" if s.bull else "bearish" if s.bear else "neutral")


# ---- candles of the chosen size, and the signal ------------------------------------------------


def _minute_of_day(ts: int) -> int:
    t = dt.datetime.fromtimestamp(int(ts), IST)
    return t.hour * 60 + t.minute


def closes_bucket(ts: int, tf: int) -> bool:
    """Whether the 1-minute candle starting at `ts` is the last one of its `tf`-minute candle."""
    return (_minute_of_day(ts) - SESSION_OPEN_MIN + 1) % int(tf) == 0


def tf_candles(closed: pd.DataFrame, tf: int) -> list[dict]:
    """Closed 1-minute candles merged into `tf`-minute candles from 09:15, complete ones only."""
    if closed is None or closed.empty:
        return []
    out: list[dict] = []
    current: dict | None = None
    key = None
    for row in closed[["ts", "open", "high", "low", "close"]].itertuples(index=False):
        ts = int(row.ts)
        t = dt.datetime.fromtimestamp(ts, IST)
        minute = t.hour * 60 + t.minute - SESSION_OPEN_MIN
        bucket = (t.date(), minute // int(tf))
        if bucket != key:
            if current is not None:
                out.append(current)
            key = bucket
            current = {"ts": ts, "open": float(row.open), "high": float(row.high), "low": float(row.low),
                       "close": float(row.close), "n": 1}
        else:
            current["high"] = max(current["high"], float(row.high))
            current["low"] = min(current["low"], float(row.low))
            current["close"] = float(row.close)
            current["n"] += 1
    if current is not None:
        out.append(current)
    # The last bucket counts only once its final minute has closed.
    if out and int(tf) > 1 and not closes_bucket(int(closed["ts"].iloc[-1]), tf):
        out = out[:-1]
    return out


@dataclass
class PatternCall:
    side: str | None  # "LONG" | "SHORT" | None
    pattern: str
    note: str


def breakeven_pct(price: float, qty: int) -> float:
    """Round-trip charges of one trade as % of what it trades."""
    if price <= 0 or qty <= 0:
        return 0.0
    costs = calculate_charges(price, price, int(qty))
    return float(costs["total_charges"]) / (price * qty) * 100.0


def pattern_call(frame: pd.DataFrame, cfg) -> PatternCall:
    """The order the newest closed pattern candle asks for, with the reason.

    `frame` is the enriched 1-minute tape with the forming candle last.
    """
    tf = int(setting(cfg, "pattern_tf"))
    closed = frame.iloc[:-1] if frame is not None and len(frame) >= 2 else None
    if closed is None or closed.empty:
        return PatternCall(None, "", "waiting for candles")
    if tf > 1 and not closes_bucket(int(closed["ts"].iloc[-1]), tf):
        return PatternCall(None, "", f"waiting for the {tf}-minute candle to close")
    rows = tf_candles(closed, tf)
    if len(rows) < 3:
        return PatternCall(None, "", f"waiting for 3 closed {tf}-minute candles")
    name, bias = candle_pattern(rows, len(rows) - 1)
    allowed = (STRONG_BULL | STRONG_BEAR) if str(setting(cfg, "pattern_set")).upper() == "STRONG" else (
        STRONG_BULL | STRONG_BEAR | SOFT_BULL | SOFT_BEAR
    )
    if name not in allowed or bias == "neutral":
        return PatternCall(None, name, f"{name} — not a trading pattern")
    side = "LONG" if bias == "bullish" else "SHORT"
    if bool(setting(cfg, "pattern_trend")):
        bar = closed.iloc[-1]
        fast, slow = bar.get("sma_9"), bar.get("sma_21")
        if fast is None or slow is None or pd.isna(fast) or pd.isna(slow):
            return PatternCall(None, name, f"{name} — SMA trend not ready")
        if side == "LONG" and not fast > slow:
            return PatternCall(None, name, f"{name} — against the SMA trend (SMA {cfg.sma_fast} below {cfg.sma_slow})")
        if side == "SHORT" and not fast < slow:
            return PatternCall(None, name, f"{name} — against the SMA trend (SMA {cfg.sma_fast} above {cfg.sma_slow})")
    edge = float(setting(cfg, "pattern_min_edge") or 0)
    if edge > 0:
        recent = rows[-14:]
        price = recent[-1]["close"]
        avg_range = sum(r["high"] - r["low"] for r in recent) / len(recent)
        range_pct = avg_range / price * 100 if price > 0 else 0.0
        need = breakeven_pct(price, int(getattr(cfg, "qty", 1) or 1)) * edge
        if range_pct < need:
            return PatternCall(
                None,
                name,
                f"{name} — candles too small for the charges ({range_pct:.3f}% range < {need:.3f}% needed)",
            )
    return PatternCall(side, name, f"{name} on the {tf}-minute candle")
