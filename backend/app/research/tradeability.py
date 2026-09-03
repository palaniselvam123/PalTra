"""Market-tradeability descriptors — not a trading strategy.

Research question: under what observable intraday conditions is there enough
price movement to matter next to realistic round-trip costs?

This module does not generate BUY/SELL signals, does not register a hypothesis,
and does not inspect the hold-out. It wraps the existing cost model and defines
input-only regime labels plus descriptive movement ratios.

Three quantities that must not be conflated:

    available movement   — how far price actually travelled (abs return, range)
    capturable movement  — the part a trader could have taken (MFE is an
                           optimistic bound; a real exit is smaller)
    realized trading P&L — after costs, slippage, fills, and a decision rule

COST_COVERAGE_RATIO = available_movement / round_trip_cost

That ratio is not a probability, not expected profit, and not a claim that the
move was tradable. A value of 3 means the observed move was three times the
cost floor; it does not mean anyone captured 3× cost.
"""
from __future__ import annotations

import datetime as dt

from app.services.hypothesis_lab import SLIPPAGE_ROUND_TRIP_PCT, cost_floor_pct
from app.services.indicators import OHLCV, atr
from app.services.observation_window import SessionIndex

# Development window for this study. Validation and hold-out stay unread.
DEV_START = dt.date(2026, 6, 1)
DEV_END = dt.date(2026, 7, 23)
VAL_START = dt.date(2026, 7, 24)
VAL_END = dt.date(2026, 8, 10)

HORIZONS = (1, 3, 6, 12, 24, 48)
LOOKBACK_BARS = 12          # contemporaneous return window; same as H004's input lookback
ATR_PERIOD = 14
LARGE_MOVE_ATR = 1.0        # a-priori: close-to-close of bar t ≥ 1× ATR[t-1]
COST_MULTIPLES = (1, 2, 3, 5)
POSITION_VALUE = 100_000    # matches hypothesis_lab.cost_floor_pct default
REGIME_LOW_PCT = 25
REGIME_HIGH_PCT = 75

# Frozen development-only quartiles of timestamp-level median ATR(14)/close.
# Source: tradeability census on 2026-06-01..2026-07-23, predictor side only.
# Must not be re-estimated against forward movement or P&L.
MKT_REL_ATR_P25 = 0.001802
MKT_REL_ATR_P75 = 0.002430


def assert_development_window(start: dt.date, end: dt.date) -> None:
    """Refuse any window that would touch validation or the hold-out."""
    if start < DEV_START or end > DEV_END:
        raise ValueError(
            "tradeability study is development-only; validation and hold-out are unread"
        )


def cost_constants() -> dict:
    """What this layer uses. Charges themselves live in paper_engine; the only
    function called here is `cost_floor_pct`, which already wraps them.
    """
    return {
        "slippage_round_trip_pct": SLIPPAGE_ROUND_TRIP_PCT,
        "slippage_pct_per_leg": SLIPPAGE_ROUND_TRIP_PCT / 2.0,
        "position_value_inr": POSITION_VALUE,
        "floor_function": "app.services.hypothesis_lab.cost_floor_pct",
    }


def round_trip_cost_audit(price: float, position_value: float = POSITION_VALUE) -> dict:
    """Worked example of the existing floor at one price.

    Charge rupees are not re-derived here. `charges_pct` is the floor minus the
    lab's round-trip slippage term — the same split `cost_floor_pct` uses.
    """
    qty = max(int(position_value / price), 1)
    floor = cost_floor_pct(price, position_value)
    return {
        "price": price,
        "qty": qty,
        "turnover": price * qty,
        "charges_pct": floor - SLIPPAGE_ROUND_TRIP_PCT,
        "slippage_round_trip_pct": SLIPPAGE_ROUND_TRIP_PCT,
        "total_floor_pct": floor,
    }


def cost_coverage_ratio(movement_pct: float, cost_pct: float) -> float | None:
    """Available movement divided by round-trip cost.

    Not a probability, not expected profit, not capturable movement.
    """
    if movement_pct is None or cost_pct is None or cost_pct <= 0:
        return None
    return movement_pct / cost_pct


def classify_vol_regime(value: float, p25: float, p75: float) -> str:
    """LOW / NORMAL / HIGH from pre-frozen predictor-side quartiles.

    Cuts must be computed on the development distribution of the causal
    variable, never on forward returns. Ties: ≤ p25 is LOW, ≥ p75 is HIGH.
    """
    if value <= p25:
        return "LOW"
    if value >= p75:
        return "HIGH"
    return "NORMAL"


def rel_atr_at(candles: list[OHLCV], atr_series: list[float | None], i: int) -> float | None:
    """ATR(14)[t] / close[t]. Available at the close of bar t.

    `indicators.atr` at index t incorporates true range through bar t (Wilder),
    so this does not read any bar after t.
    """
    if i < 0 or i >= len(candles):
        return None
    a = atr_series[i]
    close = candles[i].close
    if a is None or a <= 0 or close <= 0:
        return None
    return a / close


def same_session_lookback_ok(index: SessionIndex, i: int, lookback: int = LOOKBACK_BARS) -> bool:
    """True when bars [i-lookback, i] all sit in bar i's session."""
    if i < lookback:
        return False
    return index.session_start(i) <= i - lookback


def contemporaneous_return_pct(
    candles: list[OHLCV], index: SessionIndex, i: int, lookback: int = LOOKBACK_BARS
) -> float | None:
    """close[t]/close[t-lookback] − 1 in percent. Same session; no future bars."""
    if not same_session_lookback_ok(index, i, lookback):
        return None
    prev = candles[i - lookback].close
    if prev <= 0:
        return None
    return (candles[i].close / prev - 1.0) * 100


def session_range_over_atr(
    candles: list[OHLCV], index: SessionIndex, atr_series: list[float | None], i: int
) -> float | None:
    """(session high − session low) through bar t, divided by ATR[t].

    The range includes bar t. Nothing after t is read.
    """
    a = atr_series[i] if 0 <= i < len(atr_series) else None
    if a is None or a <= 0:
        return None
    start = index.session_start(i)
    window = candles[start : i + 1]
    span = max(c.high for c in window) - min(c.low for c in window)
    return span / a


def gap_over_atr(
    candles: list[OHLCV], index: SessionIndex, atr_series: list[float | None], i: int
) -> float | None:
    """|session open − previous session close| / ATR at the session's first bar.

    Defined only on the first bar of a session that has a prior bar. The previous
    close is known before the open.
    """
    if i < 1 or i != index.session_start(i):
        return None
    a = atr_series[i]
    if a is None or a <= 0:
        return None
    return abs(candles[i].open - candles[i - 1].close) / a


def bar_move_in_atr(candles: list[OHLCV], atr_series: list[float | None], i: int) -> float | None:
    """(close[t] − close[t-1]) / ATR[t-1]. Scale does not include bar t's range."""
    if i < 1:
        return None
    a = atr_series[i - 1]
    if a is None or a <= 0:
        return None
    return (candles[i].close - candles[i - 1].close) / a


def is_large_move(candles: list[OHLCV], atr_series: list[float | None], i: int) -> bool | None:
    """A-priori large bar: |close-to-close| ≥ LARGE_MOVE_ATR × ATR[t-1]."""
    m = bar_move_in_atr(candles, atr_series, i)
    if m is None:
        return None
    return abs(m) >= LARGE_MOVE_ATR


def unsigned_window_metrics(
    entry: float, exit_close: float, window_high: float, window_low: float
) -> dict[str, float]:
    """Descriptive movement over a completed forward window. No side is assumed.

    upside_exc  — MFE of a hypothetical long (clipped at 0)
    downside_exc — MFE of a hypothetical short / MAE of a long (clipped at 0)
    These are available-path bounds, not capturable P&L.
    """
    if entry <= 0:
        raise ValueError("entry must be positive")
    signed = (exit_close / entry - 1.0) * 100
    return {
        "signed_pct": signed,
        "abs_pct": abs(signed),
        "upside_exc_pct": max(0.0, window_high - entry) / entry * 100,
        "downside_exc_pct": max(0.0, entry - window_low) / entry * 100,
        "range_pct": (window_high - window_low) / entry * 100,
    }


def forward_window_ok(index: SessionIndex, i: int, horizon: int) -> bool:
    return index.is_forward_window_valid(i, horizon)


def coverage_hits(movement_pct: float, cost_pct: float, multiples: tuple[int, ...] = COST_MULTIPLES) -> dict[int, bool]:
    ratio = cost_coverage_ratio(movement_pct, cost_pct)
    if ratio is None:
        return {m: False for m in multiples}
    return {m: ratio >= m for m in multiples}


COVERAGE_BINS = ("<1x", "1-2x", "2-3x", "3-5x", ">5x")


def coverage_bin(ratio: float | None) -> str | None:
    """Put a coverage ratio into a descriptive bin. Not a trading label.

    Edges are left-closed except the first: <1, [1,2), [2,3), [3,5), ≥5.
    The last bin is labelled ">5x" in the report sense of "five times or more."
    """
    if ratio is None:
        return None
    if ratio < 1:
        return "<1x"
    if ratio < 2:
        return "1-2x"
    if ratio < 3:
        return "2-3x"
    if ratio < 5:
        return "3-5x"
    return ">5x"


def atr_series_for(candles: list[OHLCV], period: int = ATR_PERIOD) -> list[float | None]:
    return atr(candles, period)


# ---- approved one-off gate experiment (not a live strategy) ----

GATE_HIGH_CUT = MKT_REL_ATR_P75   # 0.002430; do not retune
GATE_PRIMARY_HORIZON = 6
GATE_SECONDARY_HORIZONS = (3, 12)


def gate_label(mkt_rel_atr: float, high_cut: float = GATE_HIGH_CUT) -> str:
    """HIGH vs STANDBY. Not BUY/SELL. Cut is the frozen development p75."""
    return "HIGH" if mkt_rel_atr >= high_cut else "STANDBY"


def shuffle_labels_within_buckets(
    labels: dict[int, str], buckets: dict[int, int], rng
) -> dict[int, str]:
    """Reassign HIGH/STANDBY inside each session bucket, preserving counts.

    Timestamps in different buckets never exchange labels. That is the TOD-
    preserving null: the clock's HIGH rate stays fixed, only which stamps
    inside a bucket are HIGH is shuffled.
    """
    by_bucket: dict[int, list[int]] = {}
    for ts, lab in labels.items():
        by_bucket.setdefault(buckets[ts], []).append(ts)
    out: dict[int, str] = {}
    for group in by_bucket.values():
        labs = [labels[ts] for ts in group]
        rng.shuffle(labs)
        for ts, lab in zip(group, labs):
            out[ts] = lab
    return out


def comparable_day_effects(
    cells: dict[tuple, tuple[list[float], list[float]]],
) -> dict:
    """Mean within-bucket (median HIGH − median STANDBY) per day.

    `cells` keys are (day, bucket). Values are (high_coverages, standby_coverages).
    A cell is comparable only when both lists are non-empty. Days with no
    comparable cell are omitted — they do not contribute a morning-vs-afternoon
    contrast.
    """
    by_day: dict = {}
    for (day, _bucket), (high, standby) in cells.items():
        if not high or not standby:
            continue
        diff = _median(high) - _median(standby)
        by_day.setdefault(day, []).append(diff)
    return {day: sum(ds) / len(ds) for day, ds in by_day.items() if ds}


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    if n == 0:
        raise ValueError("median of empty")
    mid = n // 2
    if n % 2:
        return float(s[mid])
    return (s[mid - 1] + s[mid]) / 2.0
