"""Candlestick pattern detection.

Every pattern here is defined by **geometry plus context**, never geometry
alone. That is not a stylistic choice — it is what the patterns actually mean:

* A **hammer** and a **hanging man** are the *same shape*. The only thing that
  distinguishes them is whether the preceding bars were falling or rising.
  A detector that ignores trend cannot tell them apart, so it would be
  reporting a coin flip with a confident name attached.
* A **doji** mid-range is noise; a doji after an extended run is indecision
  at a possible turning point.

So each detector receives the bars before it and reports the trend it found.
Where a pattern is meaningless without that context, it simply does not fire.

Sizes are measured relative to the candle's own range and to recent average
range (ATR-like), never in absolute rupees — a 2-rupee body is enormous on a
50-rupee stock and invisible on a 3,000-rupee one.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services.indicators import OHLCV

BULLISH = "BULLISH"
BEARISH = "BEARISH"
INDECISION = "INDECISION"

# How many prior bars define "the preceding trend" for context.
TREND_LOOKBACK = 5
# A body at or under this fraction of the candle's range counts as "small".
SMALL_BODY = 0.30
# A doji's body must be at or under this fraction — much stricter than "small".
DOJI_BODY = 0.10
# A shadow must be at least this multiple of the body to count as "long".
LONG_SHADOW = 2.0
# The opposite shadow must be at most this fraction of the range to count as
# "short" — a hammer with two long wicks is a spinning top, not a hammer.
SHORT_SHADOW = 0.15


@dataclass
class PatternHit:
    name: str            # e.g. "HAMMER"
    label: str           # human-readable
    bias: str            # BULLISH | BEARISH | INDECISION
    index: int           # index into the candles list
    ts: int
    close: float
    trend: str           # UP | DOWN | FLAT — the context it was found in
    note: str            # why it qualified, in plain language


# ---- geometry helpers ------------------------------------------------------


def _parts(c: OHLCV) -> tuple[float, float, float, float]:
    """(body, upper_shadow, lower_shadow, full_range)."""
    body = abs(c.close - c.open)
    upper = c.high - max(c.open, c.close)
    lower = min(c.open, c.close) - c.low
    rng = c.high - c.low
    return body, upper, lower, rng


def _avg_range(candles: list[OHLCV], end: int, lookback: int = 14) -> float:
    window = candles[max(0, end - lookback) : end]
    if not window:
        return 0.0
    return sum(c.high - c.low for c in window) / len(window)


def _trend_before(candles: list[OHLCV], index: int, lookback: int = TREND_LOOKBACK) -> str:
    """Direction of the run leading into `index`, from close-to-close drift
    measured against typical bar range so a flat market is not read as a trend.
    """
    start = index - lookback
    if start < 0:
        return "FLAT"
    first, last = candles[start].close, candles[index - 1].close
    move = last - first
    scale = _avg_range(candles, index) or abs(first) * 0.001
    if scale <= 0:
        return "FLAT"
    ratio = move / (scale * lookback)
    if ratio > 0.25:
        return "UP"
    if ratio < -0.25:
        return "DOWN"
    return "FLAT"


def _is_significant(candles: list[OHLCV], index: int) -> bool:
    """Ignores candles whose whole range is tiny versus recent bars. A
    perfectly formed hammer inside a dead, rangeless stretch is an artifact of
    rounding, not a signal.
    """
    rng = candles[index].high - candles[index].low
    avg = _avg_range(candles, index)
    return rng > 0 and (avg <= 0 or rng >= avg * 0.5)


# ---- single-candle patterns ------------------------------------------------


def _hammer_family(candles: list[OHLCV], i: int) -> PatternHit | None:
    """Small body at the top of the range with a long lower shadow.

    The identical shape is a HAMMER after a decline (bullish reversal) and a
    HANGING MAN after an advance (bearish reversal). With no prior trend it is
    neither, and reporting it as either would be inventing meaning.
    """
    c = candles[i]
    body, upper, lower, rng = _parts(c)
    if rng <= 0 or not _is_significant(candles, i):
        return None
    if body > rng * SMALL_BODY:
        return None
    if lower < body * LONG_SHADOW or lower < rng * 0.5:
        return None
    if upper > rng * SHORT_SHADOW:
        return None

    trend = _trend_before(candles, i)
    if trend == "DOWN":
        return PatternHit(
            "HAMMER", "Hammer", BULLISH, i, c.ts, c.close, trend,
            "small body with a long lower wick after a decline — sellers pushed down and were rejected",
        )
    if trend == "UP":
        return PatternHit(
            "HANGING_MAN", "Hanging Man", BEARISH, i, c.ts, c.close, trend,
            "same shape as a hammer but after an advance — selling pressure appearing inside an uptrend",
        )
    return None


def _inverted_family(candles: list[OHLCV], i: int) -> PatternHit | None:
    """Mirror of the hammer family: long upper shadow, small body at the low."""
    c = candles[i]
    body, upper, lower, rng = _parts(c)
    if rng <= 0 or not _is_significant(candles, i):
        return None
    if body > rng * SMALL_BODY:
        return None
    if upper < body * LONG_SHADOW or upper < rng * 0.5:
        return None
    if lower > rng * SHORT_SHADOW:
        return None

    trend = _trend_before(candles, i)
    if trend == "UP":
        return PatternHit(
            "SHOOTING_STAR", "Shooting Star", BEARISH, i, c.ts, c.close, trend,
            "long upper wick after an advance — buyers pushed up and were rejected",
        )
    if trend == "DOWN":
        return PatternHit(
            "INVERTED_HAMMER", "Inverted Hammer", BULLISH, i, c.ts, c.close, trend,
            "long upper wick after a decline — a first attempt by buyers, needs confirmation",
        )
    return None


def _doji(candles: list[OHLCV], i: int) -> PatternHit | None:
    """Open and close nearly equal: the bar closed where it opened.

    Reported as INDECISION rather than a direction. A doji does not say which
    way price will go — it says the current side lost control, which is why it
    is more useful as an exit/caution flag than an entry trigger.
    """
    c = candles[i]
    body, upper, lower, rng = _parts(c)
    if rng <= 0 or not _is_significant(candles, i):
        return None
    if body > rng * DOJI_BODY:
        return None

    trend = _trend_before(candles, i)
    if upper > rng * 0.6 and lower < rng * 0.2:
        return PatternHit("GRAVESTONE_DOJI", "Gravestone Doji", BEARISH, i, c.ts, c.close, trend,
                          "opened and closed at the low after being pushed up and rejected")
    if lower > rng * 0.6 and upper < rng * 0.2:
        return PatternHit("DRAGONFLY_DOJI", "Dragonfly Doji", BULLISH, i, c.ts, c.close, trend,
                          "opened and closed at the high after being pushed down and rejected")
    return PatternHit("DOJI", "Doji", INDECISION, i, c.ts, c.close, trend,
                      "closed where it opened — neither side kept control")


def _marubozu(candles: list[OHLCV], i: int) -> PatternHit | None:
    """Body fills almost the entire range: one side controlled the whole bar."""
    c = candles[i]
    body, upper, lower, rng = _parts(c)
    if rng <= 0 or not _is_significant(candles, i):
        return None
    if body < rng * 0.9 or upper > rng * 0.05 or lower > rng * 0.05:
        return None
    trend = _trend_before(candles, i)
    if c.close > c.open:
        return PatternHit("BULLISH_MARUBOZU", "Bullish Marubozu", BULLISH, i, c.ts, c.close, trend,
                          "opened at the low and closed at the high — buyers controlled the entire bar")
    return PatternHit("BEARISH_MARUBOZU", "Bearish Marubozu", BEARISH, i, c.ts, c.close, trend,
                      "opened at the high and closed at the low — sellers controlled the entire bar")


# ---- two-candle patterns ---------------------------------------------------


def _engulfing(candles: list[OHLCV], i: int) -> PatternHit | None:
    """This bar's body completely covers the previous bar's body, in the
    opposite direction. Requires a prior trend to be a *reversal* rather than
    just a bigger bar.
    """
    if i < 1:
        return None
    c, p = candles[i], candles[i - 1]
    if not _is_significant(candles, i):
        return None

    body, _, _, rng = _parts(c)
    prev_body = abs(p.close - p.open)
    if prev_body <= 0 or body <= prev_body:
        return None

    trend = _trend_before(candles, i)
    bull = c.close > c.open and p.close < p.open
    bear = c.close < c.open and p.close > p.open

    if bull and c.close >= p.open and c.open <= p.close and trend == "DOWN":
        return PatternHit("BULLISH_ENGULFING", "Bullish Engulfing", BULLISH, i, c.ts, c.close, trend,
                          "an up bar swallowed the previous down bar after a decline")
    if bear and c.close <= p.open and c.open >= p.close and trend == "UP":
        return PatternHit("BEARISH_ENGULFING", "Bearish Engulfing", BEARISH, i, c.ts, c.close, trend,
                          "a down bar swallowed the previous up bar after an advance")
    return None


# ---- dispatch --------------------------------------------------------------

DETECTORS = (_engulfing, _hammer_family, _inverted_family, _marubozu, _doji)

ALL_PATTERNS = [
    "HAMMER", "HANGING_MAN", "INVERTED_HAMMER", "SHOOTING_STAR",
    "DOJI", "DRAGONFLY_DOJI", "GRAVESTONE_DOJI",
    "BULLISH_ENGULFING", "BEARISH_ENGULFING",
    "BULLISH_MARUBOZU", "BEARISH_MARUBOZU",
]

MIN_BARS = TREND_LOOKBACK + 2


def detect_at(candles: list[OHLCV], index: int) -> PatternHit | None:
    """First matching pattern at `index`, most specific detector first.

    Only one is reported per bar. A bar that is technically both a doji and an
    engulfing is really one event, and emitting both would double-count it in
    any filter built on top.
    """
    if index < 0 or index >= len(candles) or index < MIN_BARS:
        return None
    for detector in DETECTORS:
        hit = detector(candles, index)
        if hit is not None:
            return hit
    return None


def detect_all(candles: list[OHLCV], limit: int | None = None) -> list[PatternHit]:
    """Scans the whole series. Used for chart markers."""
    hits = [h for h in (detect_at(candles, i) for i in range(len(candles))) if h is not None]
    return hits[-limit:] if limit else hits


def latest(candles: list[OHLCV]) -> PatternHit | None:
    """Pattern on the final bar, or None. Callers wanting candle-close-only
    behaviour must pass completed bars.
    """
    return detect_at(candles, len(candles) - 1) if candles else None


# ---- always-available plain description ------------------------------------


def describe(candles: list[OHLCV], index: int) -> str:
    """Plain description of any candle, named pattern or not.

    Most bars are not a hammer or an engulfing — they are just "a small red
    body with a long upper wick". Reporting those as a blank dash tells the
    reader nothing and looks like missing data. This always says something
    true about the bar's shape, so "no named pattern" still comes with a
    description rather than silence.
    """
    if index < 0 or index >= len(candles):
        return "no candle data"
    c = candles[index]
    body, upper, lower, rng = _parts(c)
    if rng <= 0:
        return "flat bar — no range at all"

    if c.close > c.open:
        colour, who = "green", "buyers"
    elif c.close < c.open:
        colour, who = "red", "sellers"
    else:
        colour, who = "flat", "neither side"

    body_frac = body / rng
    if body_frac >= 0.7:
        size = "large body"
    elif body_frac >= 0.35:
        size = "medium body"
    elif body_frac >= 0.1:
        size = "small body"
    else:
        size = "almost no body"

    wicks = []
    if upper >= rng * 0.35:
        wicks.append("long upper wick")
    if lower >= rng * 0.35:
        wicks.append("long lower wick")
    shape = f"{colour} candle, {size}"
    if wicks:
        shape += " with a " + " and a ".join(wicks)

    # A short reading of what the shape implies, without predicting anything.
    # Wicks are checked BEFORE body size: a tiny body with a long upper wick is
    # a rejection, and calling it "closed where it opened" would describe the
    # least informative half of the candle.
    long_upper = upper >= rng * 0.35
    long_lower = lower >= rng * 0.35
    if body_frac >= 0.7:
        reading = f"{who} controlled most of the bar"
    elif long_upper and long_lower:
        reading = "price swung both ways and settled in the middle — indecision"
    elif long_upper:
        reading = "price was pushed up and then rejected"
    elif long_lower:
        reading = "price was pushed down and then rejected"
    elif body_frac < 0.1:
        reading = "it closed almost exactly where it opened — neither side gained ground"
    else:
        reading = f"a routine bar with {who} slightly ahead"

    return f"{shape} — {reading}"
