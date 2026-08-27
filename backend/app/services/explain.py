"""Turns a technical signal into language a non-analyst can act on.

"EMA9 crossed below EMA21 (2270.70 vs 2270.74)" is precise and useless to
someone who does not already know what an EMA is. Worse, it hides the most
important thing about that specific example: the two lines were 4 paise apart,
which is a coin-flip crossing, not a trend change.

Everything here is generated from numbers already computed — nothing is
invented, and no claim is made about what price will do next.
"""
from __future__ import annotations

from app.services.indicators import OHLCV
from app.services.patterns import PatternHit

# Below this separation (as a % of price) the two averages are effectively on
# top of each other and the "cross" may reverse on the very next bar.
MARGINAL_GAP_PCT = 0.05


def _ma_words(kind: str, period: int) -> str:
    label = "average price of the last" if kind.upper() == "SMA" else "recent-weighted average of the last"
    return f"the {label} {period} candles"


def explain_signal(
    *,
    side: str,
    fast_type: str,
    fast_period: int,
    slow_type: str,
    slow_period: int,
    fast_value: float,
    slow_value: float,
    price: float,
    timeframe: str,
    volume_ratio: float | None,
    candle: PatternHit | None,
    candle_desc: str,
    confirmed_by: PatternHit | None,
) -> list[str]:
    """A short list of plain sentences. Each is independently true and stands
    on its own, so the UI can show one or all of them.
    """
    lines: list[str] = []

    direction = "risen above" if side == "BUY" else "fallen below"
    mood = "picking up" if side == "BUY" else "cooling off"
    lines.append(
        f"{_ma_words(fast_type, fast_period).capitalize()} has {direction} "
        f"{_ma_words(slow_type, slow_period)}. In plain terms: prices over the last few "
        f"{timeframe} candles are {mood} faster than the longer trend."
    )

    gap = abs(fast_value - slow_value)
    gap_pct = (gap / price * 100) if price else 0.0
    if gap_pct < MARGINAL_GAP_PCT:
        lines.append(
            f"This is a very narrow cross — the two averages are only ₹{gap:.2f} apart "
            f"({gap_pct:.3f}% of price). A gap this small can flip back on the next candle, "
            "so treat it as weak on its own."
        )
    else:
        lines.append(f"The two averages separated by ₹{gap:.2f} ({gap_pct:.2f}% of price).")

    if candle is not None:
        if candle.bias == "INDECISION":
            lines.append(
                f"The candle itself was a {candle.label} — {candle.note}. That is hesitation, "
                "not agreement with the signal."
            )
        else:
            agrees = (candle.bias == "BULLISH" and side == "BUY") or (
                candle.bias == "BEARISH" and side == "SELL"
            )
            verdict = "which points the same way as this signal" if agrees else (
                "which points the OPPOSITE way to this signal — a reason for caution"
            )
            lines.append(f"The candle itself was a {candle.label} ({candle.note}), {verdict}.")
    else:
        # No named pattern is not "nothing happened" — the bar still had a
        # shape, and describing it is more useful than a blank.
        lines.append(
            f"The candle itself: {candle_desc}. That is not one of the classic named patterns, "
            "so it adds no extra confirmation either way."
        )

    if confirmed_by is not None and confirmed_by is not candle:
        lines.append(f"A {confirmed_by.label} appeared nearby and agreed with the direction.")

    if volume_ratio is not None:
        if volume_ratio >= 1.5:
            lines.append(
                f"Trading volume was {volume_ratio:.1f}x its recent average — more participants than usual, "
                "which makes the move more credible."
            )
        else:
            lines.append(f"Volume was {volume_ratio:.1f}x its recent average — nothing unusual.")

    return lines


def candle_summary(candle: PatternHit | None) -> str:
    """One-line description of the signal bar, for a compact table cell."""
    if candle is None:
        return "no notable candle shape"
    return f"{candle.label} ({candle.bias.lower()})"
