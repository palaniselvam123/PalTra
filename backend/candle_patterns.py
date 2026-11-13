"""Candle-pattern entries for the SMA terminal (``entry_mode = "PATTERN"``).

Instead of an SMA cross, each time a candle of ``pattern_tf`` minutes (1, 3 or
5, built from the 1-minute tape from 09:15) closes, the pattern on it is read.
A bullish pattern buys at the start of the next candle, a bearish one sells
short; either way the trade is closed at the end of that candle
(``CANDLE_END``), then the next candle is judged afresh.

``entry_mode = "COLOUR"`` (`colour_call`) rides the candle colour on the same
``pattern_tf`` candles instead: a green candle buys, a red one sells, the trade
is held while the candles keep its colour and the first candle of the other
colour closes it (``COLOUR_TURN``) and opens the other way. ``colour_count`` (1-5) asks for
that many closed candles of a colour in a row before entering or turning, and
``colour_min_body`` (% of the price, 0 = off) makes a candle whose body is smaller than that
count as no colour (held, like a doji). The smallest body can be set per colour candle size
(``colour_body_by_tf``, "5:0.05,15:0.15" in %; a size not listed uses ``colour_min_body``), or in
ATRs instead (``colour_body_unit = "ATR"``, ``colour_body_atr`` × the Wilder ATR 14 of the colour
candles themselves, so one number fits every candle size and stock). Each candle of the run can also
have its own smallest body (``colour_body_seq``, "0.1,0.5" oldest first, in the unit picked; a blank
place uses the usual one): with 2 in a row and "0.1,0.5" × ATR, a small then a big candle turns the
trade, a big then a small one does not.

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
import heikin_ashi

IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN_MIN = 9 * 60 + 15
PATTERN_TFS = (1, 3, 5, 10, 15, 30, 60)
COLOUR_COUNT_MAX = 5
COLOUR_BODY_MAX = 2.0
COLOUR_BODY_ATR_MAX = 5.0
COLOUR_BODY_UNITS = ("PCT", "ATR")
COLOUR_ATR_PERIOD = 14
PATTERN_SETS = ("STRONG", "ALL")
ENTRY_MODES = ("SMA", "PATTERN", "COLOUR", "NTH_CANDLE")

DEFAULTS = {
    "entry_mode": "SMA",
    "pattern_tf": 1,
    "pattern_trend": False,
    "pattern_set": "STRONG",
    "pattern_min_edge": 1.5,
    "colour_count": 1,
    "colour_min_body": 0.0,
    "colour_body_unit": "PCT",
    "colour_body_by_tf": "",
    "colour_body_atr": 0.0,
    "colour_body_seq": "",
    "colour_inside_minutes": 0,
    "colour_inside_body": 0.0,
    # A separate rule for closing a trade (colour_exit.py).
    "colour_exit_on": False,
    "colour_exit_count": 2,
    "colour_exit_body": 0.2,
    "colour_exit_inside": False,
    # Colour entries wait for a pullback (colour_pull.py).
    "colour_pull": "OFF",
    "colour_pull_candles": 1,
    "colour_pull_market": False,
}

# Inside candles (colour_inside_minutes): sizes read inside one colour candle.
INSIDE_SIZES = (1, 2, 3, 5, 10)
INSIDE_BODY_MAX = 2.0

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


def uses_colour(cfg) -> bool:
    """Ride the candle colour (``entry_mode = "COLOUR"``), see `colour_call`."""
    return str(setting(cfg, "entry_mode")).upper() == "COLOUR"


def uses_nth(cfg) -> bool:
    """The day's Nth candle colour (``entry_mode = "NTH_CANDLE"``, nth_candle.py)."""
    return str(setting(cfg, "entry_mode")).upper() == "NTH_CANDLE"


def per_candle(cfg) -> bool:
    """Entries judged on a candle's close (patterns, colour, the day's Nth candle), not on the SMA cross:
    the order goes at once, so the pullback / candle-extreme / entry-candle waits are skipped."""
    return uses_patterns(cfg) or uses_colour(cfg) or uses_nth(cfg)


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


def colour_count(cfg) -> int:
    """Candles of one colour in a row needed to enter or turn (1-5)."""
    try:
        return min(max(int(setting(cfg, "colour_count") or 1), 1), COLOUR_COUNT_MAX)
    except (TypeError, ValueError):
        return 1


def colour_min_body(cfg) -> float:
    """Smallest body, in % of the close, that counts as a colour (0 = any)."""
    try:
        return min(max(float(setting(cfg, "colour_min_body") or 0.0), 0.0), COLOUR_BODY_MAX)
    except (TypeError, ValueError):
        return 0.0


def colour_body_unit(cfg) -> str:
    """How the smallest body is set: PCT (% of the price, per candle size) or ATR (× the colour candles' ATR)."""
    value = str(setting(cfg, "colour_body_unit") or "PCT").upper()
    return value if value in COLOUR_BODY_UNITS else "PCT"


def parse_body_by_tf(text) -> dict[int, float]:
    """ "5:0.05,15:0.15" → {5: 0.05, 15: 0.15}; sizes outside PATTERN_TFS and bad parts are dropped."""
    out: dict[int, float] = {}
    for part in str(text or "").replace(";", ",").split(","):
        if ":" not in part:
            continue
        size, _, value = part.partition(":")
        try:
            tf, pct = int(size.strip()), float(value.strip())
        except ValueError:
            continue
        if tf in PATTERN_TFS and 0 <= pct <= COLOUR_BODY_MAX:
            out[tf] = pct
    return out


def clean_body_by_tf(text) -> str:
    """The saved form of `colour_body_by_tf` (sizes in order, zeros kept so a size can be switched off)."""
    parsed = parse_body_by_tf(text)
    return ",".join(f"{tf}:{parsed[tf]:g}" for tf in sorted(parsed))


def colour_min_body_pct(cfg, tf: int) -> float:
    """Smallest body in % for this colour candle size: its own value, else `colour_min_body`."""
    own = parse_body_by_tf(setting(cfg, "colour_body_by_tf"))
    if int(tf) in own:
        return own[int(tf)]
    return colour_min_body(cfg)


def colour_body_atr(cfg) -> float:
    """Smallest body in ATRs of the colour candles (0 = any)."""
    try:
        return min(max(float(setting(cfg, "colour_body_atr") or 0.0), 0.0), COLOUR_BODY_ATR_MAX)
    except (TypeError, ValueError):
        return 0.0


def parse_body_seq(text) -> list[float | None]:
    """ "0.1,0.5" → [0.1, 0.5]: the smallest body of each candle of the run, oldest first (``colour_body_seq``).
    A blank place is None (that candle uses the usual smallest body); at most COLOUR_COUNT_MAX places."""
    out: list[float | None] = []
    for part in str(text or "").replace(";", ",").split(",")[:COLOUR_COUNT_MAX]:
        part = part.strip()
        try:
            value = float(part) if part else None
        except ValueError:
            value = None
        out.append(value if value is None or 0 <= value <= max(COLOUR_BODY_MAX, COLOUR_BODY_ATR_MAX) else None)
    while out and out[-1] is None:
        out.pop()
    return out


def clean_body_seq(text) -> str:
    """The saved form of `colour_body_seq` ("0.1,,0.5": a blank place keeps the usual smallest body)."""
    return ",".join("" if v is None else f"{v:g}" for v in parse_body_seq(text))


def _atr_series(rows: list[dict], period: int = COLOUR_ATR_PERIOD) -> list[float | None]:
    """Wilder ATR of the colour candles (real high / low / close), None until two candles exist."""
    out: list[float | None] = []
    trs: list[float] = []
    atr: float | None = None
    for i, r in enumerate(rows):
        hi, lo = float(r["high"]), float(r["low"])
        tr = hi - lo if i == 0 else max(hi - lo, abs(hi - float(rows[i - 1]["close"])), abs(lo - float(rows[i - 1]["close"])))
        trs.append(tr)
        if len(trs) < period:
            atr = sum(trs) / len(trs)
        elif len(trs) == period:
            atr = sum(trs) / period
        else:
            atr = (atr * (period - 1) + tr) / period  # type: ignore[operator]
        out.append(atr if i >= 1 else None)
    return out


def _colour(open_: float, close: float, min_body: float) -> str | None:
    """"G", "R" or None (a doji, or a body under `min_body`, in price units)."""
    body = close - open_
    if body == 0 or (min_body > 0 and abs(body) < min_body):
        return None
    return "G" if body > 0 else "R"


def inside_minutes(cfg, tf: int) -> int:
    """The inside-candle size (``colour_inside_minutes``) when it is on and fits the colour candle, else 0."""
    try:
        n = int(setting(cfg, "colour_inside_minutes") or 0)
    except (TypeError, ValueError):
        return 0
    return n if n in INSIDE_SIZES and n < int(tf) and int(tf) % n == 0 else 0


def inside_body_pct(cfg) -> float:
    try:
        return max(0.0, min(INSIDE_BODY_MAX, float(setting(cfg, "colour_inside_body") or 0.0)))
    except (TypeError, ValueError):
        return 0.0


def inside_colour(closed: pd.DataFrame, last_ts: int, tf: int, size: int, cfg) -> str | None:
    """'G' / 'R' when every `size`-minute candle inside the colour candle that starts at `last_ts` closed that colour
    (Heikin Ashi colours with ``ha_colours``), else None (mixed, a doji, or not all of them there)."""
    rows = tf_candles(closed, size)
    if not rows:
        return None
    end = int(last_ts) + int(tf) * 60
    if heikin_ashi.on(cfg):
        ha = heikin_ashi.view(pd.DataFrame(rows), cfg)
        pairs = [(int(t), float(o), float(c)) for t, o, c in zip(ha["ts"], ha["open"], ha["close"])]
    else:
        pairs = [(int(r["ts"]), float(r["open"]), float(r["close"])) for r in rows]
    inside = [(o, c) for t, o, c in pairs if int(last_ts) <= t < end]
    if len(inside) != int(tf) // int(size):
        return None
    if all(c > o for o, c in inside):
        return "G"
    if all(c < o for o, c in inside):
        return "R"
    return None


def colour_call(frame: pd.DataFrame, cfg) -> PatternCall:
    """Ride the candle colour (``entry_mode = "COLOUR"``): the side the newest closed candles ask for.

    Each time a candle of ``pattern_tf`` minutes closes, the last ``colour_count`` closed candles
    are read: all green asks for a long, all red for a short. The engine holds a trade while the
    candles keep its colour, and once that many candles of the other colour have closed in a row
    it closes it and opens the other way at the start of the next candle. A doji (close = open),
    a candle whose body is under ``colour_min_body`` % of its close, or a mixed run asks for
    nothing: the trade is held. `frame` is the enriched 1-minute tape with the forming candle last.
    """
    tf = int(setting(cfg, "pattern_tf"))
    need = colour_count(cfg)
    closed = frame.iloc[:-1] if frame is not None and len(frame) >= 2 else None
    if closed is None or closed.empty:
        return PatternCall(None, "", "waiting for candles")
    if tf > 1 and not closes_bucket(int(closed["ts"].iloc[-1]), tf):
        return PatternCall(None, "", f"waiting for the {tf}-minute candle to close")
    rows = tf_candles(closed, tf)
    if not rows:
        return PatternCall(None, "", f"waiting for a closed {tf}-minute candle")
    if heikin_ashi.on(cfg):
        # Heikin Ashi colours (heikin_ashi.py): the colour of the smoothed candles.
        ha = heikin_ashi.view(pd.DataFrame(rows), cfg)
        pairs = list(zip(ha["open"].astype(float), ha["close"].astype(float)))
    else:
        pairs = [(float(r["open"]), float(r["close"])) for r in rows]
    size = inside_minutes(cfg, tf)
    if size and pairs:
        # Inside candles agree (colour_inside_minutes): every smaller candle inside the newest colour candle is one
        # colour, so that candle alone is enough (no run of `need`) with its own smallest body (colour_inside_body).
        # The inside candles set the colour (with Heikin Ashi a 15-minute HA candle can still read red after a fall while
        # every smaller HA candle inside it is green); the real colour candle must have closed that way too.
        own = inside_colour(closed, int(rows[-1]["ts"]), tf, size, cfg)
        o, c = float(rows[-1]["open"]), float(rows[-1]["close"])
        if own is not None and ((c > o) if own == "G" else (c < o)):
            floor = float(rows[-1]["close"]) * inside_body_pct(cfg) / 100
            word = "green" if own == "G" else "red"
            if floor <= 0 or abs(c - o) >= floor:
                side = "LONG" if own == "G" else "SHORT"
                return PatternCall(
                    side, f"{word.capitalize()} candle", f"{word} {tf}-minute candle, every {size}-minute candle inside {word}"
                )
            return PatternCall(
                None, "Small candle",
                f"{word} {tf}-minute candle (all {size}-minute inside {word}) body {abs(c - o):.2f} under "
                f"{inside_body_pct(cfg):g}% ({floor:.2f}) — holding",
            )
    if len(pairs) < need:
        return PatternCall(None, "", f"waiting for {need} closed {tf}-minute candles")
    # The smallest body in price units for each of the last `need` candles.
    # Each candle of the run can have its own smallest body (colour_body_seq, oldest first, in the unit picked).
    seq = parse_body_seq(setting(cfg, "colour_body_seq"))[:need]
    seq = seq + [None] * (need - len(seq))
    atr_mode = colour_body_unit(cfg) == "ATR"
    if atr_mode:
        k = colour_body_atr(cfg)
        wants_atr = k > 0 or any(v for v in seq)
        atrs = (_atr_series(rows) if wants_atr else [None] * len(rows))[-need:]
        ks = [min(v, COLOUR_BODY_ATR_MAX) if v is not None else k for v in seq]
        floors = [(kk * a if a else 0.0) for kk, a in zip(ks, atrs)]
        words = [f"{kk:g}× ATR" for kk in ks]
    else:
        pct = colour_min_body_pct(cfg, tf)
        pcts = [min(v, COLOUR_BODY_MAX) if v is not None else pct for v in seq]
        floors = [float(r["close"]) * p / 100 for r, p in zip(rows[-need:], pcts)]
        words = [f"{p:g}%" for p in pcts]
    colours = [_colour(o, c, f) for (o, c), f in zip(pairs[-need:], floors)]
    last = colours[-1]
    place = (lambda i: f"candle {i + 1} of {need}") if need > 1 and any(v is not None for v in seq) else (lambda i: f"{tf}-minute candle")
    if last is None:
        o, c = pairs[-1]
        if c == o:
            return PatternCall(None, "Doji", f"doji {tf}-minute candle — holding")
        return PatternCall(
            None, "Small candle", f"{place(need - 1)} body {abs(c - o):.2f} under {words[-1]} ({floors[-1]:.2f}) — holding"
        )
    word = "green" if last == "G" else "red"
    if any(v is not None for v in seq):
        # A candle of the right colour that is under its own place's size: say which one.
        for i, ((o, c), col) in enumerate(zip(pairs[-need:], colours)):
            right_way = (c > o) if last == "G" else (c < o)
            if col is None and right_way:
                return PatternCall(
                    None, "Small candle", f"{place(i)} body {abs(c - o):.2f} under its {words[i]} ({floors[i]:.2f}) — holding"
                )
    if any(col != last for col in colours):
        run = 0
        for col in reversed(colours):
            if col != last:
                break
            run += 1
        return PatternCall(None, "", f"{run} of {need} {word} {tf}-minute candles in a row — holding")
    side = "LONG" if last == "G" else "SHORT"
    if need == 1:
        return PatternCall(side, f"{word.capitalize()} candle", f"{word} {tf}-minute candle")
    return PatternCall(side, f"{need} {word} candles", f"{need} {word} {tf}-minute candles in a row")
