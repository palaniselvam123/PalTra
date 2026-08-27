"""Entry diagnostics — under exactly what conditions does this signal work?

An aggregate MFE/MAE of 0.64 says the entries are poor on average. It does not
say *why*, and the plausible causes point at different fixes:

* every entry is bad            → the signal itself carries no information
* only low-ADX entries are bad  → market selection, not entry logic
* only repeated crossovers      → whipsaw; take the first cross only
* only entries near resistance  → location; needs a price-room check

Segmenting the same population by context separates these. Each row reports
its own sample size because a promising ratio over 11 trades is noise, and
labelling it as such is the difference between evidence and a story.

Nothing here predicts. It measures what already happened, so later components
(movement potential, ranking) can be built on observed conditions rather than
assumed ones.
"""
from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass, field

from app.core.market_clock import IST
from app.services.backtester import ForwardOutcome, measure_forward
from app.services.indicators import OHLCV, adx, ema, vwap
from app.services.scanner_engine import StrategyEngine, StrategyParams

# Below this many samples a segment is reported but flagged as unreliable.
MIN_RELIABLE_SAMPLE = 20
HORIZON_BARS = 12


@dataclass
class Observation:
    """One signal, its context at the moment it fired, and what followed."""

    symbol: str
    side: str
    ts: int
    entry_price: float
    forward: ForwardOutcome

    adx: float | None
    vwap_distance_pct: float | None      # + = above VWAP
    rvol: float | None                   # volume vs trailing average
    ema_gap_pct: float | None            # separation of the two averages
    prior_move_pct: float                # move in the signal's direction, prior 9 bars
    room_to_high_pct: float | None       # to the session high so far
    room_to_low_pct: float | None
    crossover_index: int                 # 1st, 2nd, 3rd... crossover that day
    time_bucket: str

    @property
    def favourable(self) -> bool:
        return self.forward.mfe > self.forward.mae


def _time_bucket(ts: int) -> str:
    t = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).time()
    if t < dt.time(9, 45):
        return "1 open"
    if t < dt.time(11, 30):
        return "2 morning"
    if t < dt.time(13, 30):
        return "3 midday"
    if t < dt.time(14, 45):
        return "4 afternoon"
    return "5 close"


def _ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def collect(
    symbol: str,
    candles: list[OHLCV],
    params: StrategyParams,
    horizon_bars: int = HORIZON_BARS,
) -> list[Observation]:
    """Every signal this strategy would have produced, with context attached."""
    engine = StrategyEngine(params)
    ctx = engine.precompute(candles)
    warmup = max(params.warmup_bars(), 30)
    if len(candles) < warmup + horizon_bars:
        return []

    adx_series = adx(candles, 14)["adx"]
    vwap_series = vwap(candles)
    fast_series = ema(candles, params.fast_period)
    slow_series = ema(candles, params.slow_period)

    out: list[Observation] = []
    crossings_today = 0
    current_day = _ist_date(candles[warmup].ts)

    for i in range(warmup, len(candles) - 2):
        day = _ist_date(candles[i].ts)
        if day != current_day:
            current_day, crossings_today = day, 0

        signal = engine.evaluate_at(symbol, "diag", candles, i, ctx)
        if signal is None:
            continue
        crossings_today += 1

        entry = candles[i].close
        if entry <= 0:
            continue

        fwd = measure_forward(
            candles,
            i,
            signal.side,
            entry,
            risk_per_share=entry * 0.003,
            bar_seconds=(candles[1].ts - candles[0].ts) if len(candles) > 1 else 300,
            horizon_bars=horizon_bars,
        )
        if fwd.mfe == 0 and fwd.mae == 0:
            continue  # no forward data (end of series / session)

        back = candles[max(0, i - 9)].close
        prior = ((entry - back) if signal.side == "BUY" else (back - entry)) / entry * 100

        # Session extremes so far — "room" is measured against what price has
        # already proved it can reach today, not an arbitrary lookback.
        today_bars = [c for c in candles[max(0, i - 80) : i + 1] if _ist_date(c.ts) == day]
        hi = max((c.high for c in today_bars), default=entry)
        lo = min((c.low for c in today_bars), default=entry)

        vol_window = [c.volume for c in candles[max(0, i - 20) : i]]
        avg_vol = statistics.fmean(vol_window) if vol_window else 0
        f, s = fast_series[i], slow_series[i]

        out.append(
            Observation(
                symbol=symbol,
                side=signal.side,
                ts=candles[i].ts,
                entry_price=entry,
                forward=fwd,
                adx=adx_series[i],
                vwap_distance_pct=round((entry - vwap_series[i]) / entry * 100, 3)
                if vwap_series[i]
                else None,
                rvol=round(candles[i].volume / avg_vol, 2) if avg_vol > 0 else None,
                ema_gap_pct=round(abs(f - s) / entry * 100, 4) if (f and s) else None,
                prior_move_pct=round(prior, 3),
                room_to_high_pct=round((hi - entry) / entry * 100, 3),
                room_to_low_pct=round((entry - lo) / entry * 100, 3),
                crossover_index=crossings_today,
                time_bucket=_time_bucket(candles[i].ts),
            )
        )
    return out


# ---- segmentation ----------------------------------------------------------


def _row(name: str, group: list[Observation]) -> dict:
    mfe = statistics.fmean([o.forward.mfe_pct for o in group])
    mae = statistics.fmean([o.forward.mae_pct for o in group])
    return {
        "segment": name,
        "n": len(group),
        "avg_mfe_pct": round(mfe, 3),
        "avg_mae_pct": round(mae, 3),
        "mfe_mae_ratio": round(mfe / mae, 2) if mae > 0 else None,
        "favourable_pct": round(sum(o.favourable for o in group) / len(group) * 100, 1),
        "reached_1r_pct": round(sum(o.forward.reached_1r for o in group) / len(group) * 100, 1),
        # Flagged rather than hidden: a strong-looking ratio on a handful of
        # samples is the easiest way to fool yourself here.
        "reliable": len(group) >= MIN_RELIABLE_SAMPLE,
    }


def segment(observations: list[Observation], dimension: str) -> list[dict]:
    """Group observations by a named dimension and report outcomes per bucket."""
    buckets: dict[str, list[Observation]] = {}

    def put(key: str, o: Observation) -> None:
        buckets.setdefault(key, []).append(o)

    for o in observations:
        if dimension == "adx":
            a = o.adx
            key = "unknown" if a is None else (
                "1 <15" if a < 15 else "2 15-20" if a < 20 else "3 20-25" if a < 25 else "4 >25"
            )
        elif dimension == "vwap":
            v = o.vwap_distance_pct
            key = "unknown" if v is None else ("above VWAP" if v > 0 else "below VWAP")
        elif dimension == "vwap_aligned":
            v = o.vwap_distance_pct
            if v is None:
                key = "unknown"
            else:
                aligned = (v > 0 and o.side == "BUY") or (v < 0 and o.side == "SELL")
                key = "aligned with VWAP" if aligned else "against VWAP"
        elif dimension == "rvol":
            r = o.rvol
            key = "unknown" if r is None else (
                "1 <0.8x" if r < 0.8 else "2 0.8-1.2x" if r < 1.2 else "3 1.2-2x" if r < 2 else "4 >2x"
            )
        elif dimension == "ema_gap":
            g = o.ema_gap_pct
            key = "unknown" if g is None else (
                "1 razor <0.02%" if g < 0.02 else "2 0.02-0.05%" if g < 0.05 else "3 >0.05%"
            )
        elif dimension == "prior_move":
            p = o.prior_move_pct
            key = "1 early <0.1%" if p < 0.1 else "2 mid 0.1-0.3%" if p < 0.3 else "3 late >0.3%"
        elif dimension == "crossover_index":
            key = "1 first of day" if o.crossover_index == 1 else (
                "2 second" if o.crossover_index == 2 else "3 third+"
            )
        elif dimension == "time":
            key = o.time_bucket
        elif dimension == "side":
            key = o.side
        elif dimension == "room":
            # Room in the direction of the trade, vs the session extreme.
            r = o.room_to_high_pct if o.side == "BUY" else o.room_to_low_pct
            key = "unknown" if r is None else (
                "1 <0.1% room" if r < 0.1 else "2 0.1-0.4%" if r < 0.4 else "3 >0.4% room"
            )
        else:
            key = "all"
        put(key, o)

    return [_row(k, v) for k, v in sorted(buckets.items())]


DIMENSIONS = [
    "side", "adx", "vwap_aligned", "vwap", "rvol",
    "ema_gap", "prior_move", "crossover_index", "room", "time",
]


def full_report(observations: list[Observation]) -> dict:
    if not observations:
        return {"observations": 0}
    overall = _row("ALL", observations)
    return {
        "observations": len(observations),
        "overall": overall,
        "segments": {d: segment(observations, d) for d in DIMENSIONS},
    }
