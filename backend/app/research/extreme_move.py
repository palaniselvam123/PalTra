"""Candidate extreme-move *event* — input-only, not a hypothesis.

Research question (not yet a registered claim):

    After an unusually large short-term directional displacement, is the
    subsequent move more often opposite than continuing?

This module identifies the event and defines what a later fade measurement
would mean. It does not generate BUY/SELL, does not register H006, and does
not inspect the hold-out.

The event threshold is the one already frozen on the predictor side of the
tradeability census:

    extreme_score(t) = |close[t] - close[t-1]| / ATR(14)[t-1]
    event            iff extreme_score(t) >= 1.0
    flat close-to-close is excluded

Do not search 0.5 / 0.75 / 1.25 / 1.5 / 2.0. The cut is not a result.
"""
from __future__ import annotations

from app.research.tradeability import (
    DEV_END, DEV_START, LARGE_MOVE_ATR, MKT_REL_ATR_P25, MKT_REL_ATR_P75,
    assert_development_window, atr_series_for, bar_move_in_atr, is_large_move,
)
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex

# Identical to tradeability.LARGE_MOVE_ATR. Re-exported so a later reader does
# not invent a second cut.
EXTREME_CUT = LARGE_MOVE_ATR  # 1.0
ATR_PERIOD = 14

# Proposed outcome horizons for a *future* experiment. Not run in this audit.
# Rationale is in FADE_HORIZON_RATIONALE; do not pick after seeing results.
FADE_HORIZONS = (1, 3, 6, 12)
FADE_PRIMARY_HORIZON = 6
FADE_HORIZON_RATIONALE = (
    "1 bar (5m): immediate bounce; the shortest causal step after the event. "
    "3 bars (15m): short fade, still inside one 30-minute session bucket. "
    "6 bars (30m): primary — one existing session bucket, the same width the "
    "research framework already treats as a time-of-day cell, and long enough "
    "that a single 5m bounce is not the whole story. "
    "12 bars (60m): secondary diagnostic; more close-of-session attrition. "
    "24/48 are not proposed: those were tradeability 'is there any movement' "
    "horizons, not a fade window, and they drop most of the afternoon."
)

# Episode separation uses the shared framework default (12 bars). The event
# itself is one close-to-close, but ATR(14) is a long smoother, so events
# closer than 12 bars share almost the same scale.
EPISODE_SEPARATION_BARS = 12

# Trailing same-session OK bars used for a causal volume comparison.
VOLUME_LOOKBACK_BARS = 12
VOLUME_MIN_PRIOR = 6


def extreme_score(candles: list[OHLCV], atr_series: list[float | None], i: int) -> float | None:
    """|close[t] − close[t-1]| / ATR[t-1]. None when ATR[t-1] is missing."""
    signed = bar_move_in_atr(candles, atr_series, i)
    if signed is None:
        return None
    return abs(signed)


def event_direction(candles: list[OHLCV], i: int) -> int | None:
    """+1 if close[t] > close[t-1], −1 if close[t] < close[t-1], else None.

    Flat is excluded. Nothing after t is read. close[t-1] may be the previous
    session's last close when t is the session open — that is a property of
    the stated definition and is flagged separately.
    """
    if i < 1 or i >= len(candles):
        return None
    delta = candles[i].close - candles[i - 1].close
    if delta > 0:
        return 1
    if delta < 0:
        return -1
    return None


def is_extreme_event(candles: list[OHLCV], atr_series: list[float | None], i: int) -> bool:
    """True only when the score is defined, the move is not flat, and ≥ cut."""
    if event_direction(candles, i) is None:
        return False
    flagged = is_large_move(candles, atr_series, i)
    return bool(flagged)


def same_session_predecessor(index: SessionIndex, i: int) -> bool:
    """True when close[t-1] sits in the same session as bar t."""
    if i < 1:
        return False
    return index.session_start(i) <= i - 1


def session_range_position(
    candles: list[OHLCV], index: SessionIndex, i: int
) -> float | None:
    """(close[t] − session_low) / session_range, through bar t only.

    After a large up bar this is typically near 1: the event *places* price
    near the printed high. That is the mechanical-room selection effect.
    """
    if i < 0 or i >= len(candles):
        return None
    start = index.session_start(i)
    window = candles[start : i + 1]
    hi = max(c.high for c in window)
    lo = min(c.low for c in window)
    if hi <= lo:
        return None
    return (candles[i].close - lo) / (hi - lo)


def opposite_session_room(
    candles: list[OHLCV], index: SessionIndex, i: int, direction: int
) -> float | None:
    """Share of the printed session range that sits opposite `direction`.

    For an up event this is the fraction of the range below close (often large).
    Known at t. It is not a forward return.
    """
    pos = session_range_position(candles, index, i)
    if pos is None:
        return None
    if direction > 0:
        return pos
    if direction < 0:
        return 1.0 - pos
    return None


def signed_future_move(future_move: float, direction: int) -> float:
    """future_move × initial-event direction.

    Negative means the subsequent displacement was opposite the extreme move
    (reversion in the research sense). Positive means continuation.

    This is a definition, not an entry rule. Cost is not applied here.
    """
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")
    return future_move * direction


def is_reversion(future_move: float, direction: int) -> bool | None:
    """True when the subsequent close-to-close is opposite the extreme move.

    Flat future_move is neither reversion nor continuation.
    """
    if direction not in (1, -1):
        raise ValueError("direction must be +1 or -1")
    if future_move == 0:
        return None
    return (future_move * direction) < 0


def shuffle_directions(directions: list[int], rng) -> list[int]:
    """Reassign +1/−1 while preserving the counts.

    Control A and, on this event population, Control B: keep the same bars,
    the same timestamps, and the same long/short balance; destroy only the
    pairing between the observed extreme-move sign and the subsequent move.

    Null: conditional on a 1-ATR event bar existing, the sign of that event
    carries no information about the sign of the later displacement.
    """
    out = list(directions)
    rng.shuffle(out)
    return out


def trailing_ok_volume(
    volumes: list[int],
    unknown: set[int],
    timestamps: list[int],
    session_start: int,
    i: int,
    lookback: int = VOLUME_LOOKBACK_BARS,
    min_prior: int = VOLUME_MIN_PRIOR,
) -> tuple[int | None, float | None]:
    """Event bar_volume and its ratio to the trailing same-session OK median.

    The event bar itself is not in the baseline. UNKNOWN bars are skipped.
    Bars after t are never read. Returns (event_volume, rvol) or (None, None)
    when the event bar is UNKNOWN / non-positive or the baseline is too thin.
    """
    if i < 0 or i >= len(volumes):
        return None, None
    ts = timestamps[i]
    if ts in unknown or volumes[i] <= 0:
        return None, None
    start = max(session_start, i - lookback)
    prior = [
        volumes[j]
        for j in range(start, i)
        if timestamps[j] not in unknown and volumes[j] > 0
    ]
    if len(prior) < min_prior:
        return volumes[i], None
    mid = sorted(prior)
    n = len(mid)
    med = mid[n // 2] if n % 2 else (mid[n // 2 - 1] + mid[n // 2]) / 2.0
    if med <= 0:
        return volumes[i], None
    return volumes[i], volumes[i] / med


__all__ = [
    "ATR_PERIOD",
    "DEV_END",
    "DEV_START",
    "EPISODE_SEPARATION_BARS",
    "EXTREME_CUT",
    "FADE_HORIZONS",
    "FADE_HORIZON_RATIONALE",
    "FADE_PRIMARY_HORIZON",
    "LARGE_MOVE_ATR",
    "MKT_REL_ATR_P25",
    "MKT_REL_ATR_P75",
    "VOLUME_LOOKBACK_BARS",
    "VOLUME_MIN_PRIOR",
    "assert_development_window",
    "atr_series_for",
    "bar_move_in_atr",
    "event_direction",
    "extreme_score",
    "is_extreme_event",
    "is_reversion",
    "opposite_session_room",
    "same_session_predecessor",
    "session_range_position",
    "shuffle_directions",
    "signed_future_move",
    "trailing_ok_volume",
]
