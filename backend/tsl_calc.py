"""Trailing stop and target sized from the candle (tsl_calc.py; read-only, no order).

"How wide should the stop and target be on a 5-minute candle?" The honest answer is "as wide as the stock
moves on that candle", so this reads the stock's own 1-minute tape, groups it into each candle size the bot
can trade (1, 2, 3, 5, 10, 15 minutes), takes the Wilder ATR of that size (the same `indicators.enrich` ATR
the bot's stop uses) and the average candle range, and turns multiples of the ATR into a trailing stop
distance, a trail step and a target, in ₹ (rounded to the stock's tick) and in % of the price. The person
picks the multiples; the Settings page can copy a row into the bot's or the stock's own TSL settings. The
bot never reads this on its own: nothing changes until Save.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

import indicators
from candles import resample
from tick_sizes import round_price

SIZES = (1, 2, 3, 5, 10, 15)
#: Default multiples of the candle's ATR: stop 1.5× (one noisy candle rarely reaches it), trail 0.5× (the stop
#: follows in half-candle steps), target 3× (two to one on the stop).
DEFAULT_STOP = 1.5
DEFAULT_TRAIL = 0.5
DEFAULT_TARGET = 3.0
RANGE_CANDLES = 20


def _size_read(tape: pd.DataFrame, minutes: int, atr_period: int) -> dict | None:
    frame = resample(tape, minutes) if minutes > 1 else tape
    if frame is None or len(frame) < 3:
        return None
    closed = frame.iloc[:-1].copy()  # the last candle is still forming
    if len(closed) < atr_period + 1:
        return {"minutes": minutes, "candles": int(len(closed)), "atr": None, "avg_range": None}
    rich = indicators.enrich(closed, atr_period=atr_period)
    atr = rich["atr_14"].iloc[-1]
    ranges = (closed["high"].astype(float) - closed["low"].astype(float)).tail(RANGE_CANDLES)
    return {
        "minutes": minutes,
        "candles": int(len(closed)),
        "atr": None if pd.isna(atr) else float(atr),
        "avg_range": float(ranges.mean()) if len(ranges) else None,
    }


def suggest(
    symbol: str,
    tape: pd.DataFrame | None,
    sizes=SIZES,
    atr_period: int = 14,
    stop_mult: float = DEFAULT_STOP,
    trail_mult: float = DEFAULT_TRAIL,
    target_mult: float = DEFAULT_TARGET,
) -> dict:
    """Per candle size: ATR, average range and the stop / trail / target those multiples give."""
    name = (symbol or "").upper()
    if tape is None or getattr(tape, "empty", True) or len(tape) < 3:
        return {"symbol": name, "price": None, "sizes": [], "error": "No candles for this stock yet."}
    price = float(tape["close"].iloc[-1])
    out: list[dict] = []
    for minutes in sizes:
        read = _size_read(tape, int(minutes), int(atr_period))
        if read is None:
            continue
        atr = read["atr"]
        if atr and price > 0:
            stop = round_price(name, atr * stop_mult)
            trail = round_price(name, atr * trail_mult)
            target = round_price(name, atr * target_mult) if target_mult > 0 else 0.0
            read.update(
                {
                    "stop": stop,
                    "trail": trail,
                    "target": target,
                    "stop_pct": round(stop / price * 100, 3),
                    "trail_pct": round(trail / price * 100, 3),
                    "target_pct": round(target / price * 100, 3) if target else 0.0,
                    "atr_pct": round(atr / price * 100, 3),
                }
            )
        out.append(read)
    last_ts = int(tape["ts"].iloc[-1]) if "ts" in tape.columns else None
    return {
        "symbol": name,
        "price": price,
        "as_of": dt.datetime.fromtimestamp(last_ts, dt.timezone.utc).isoformat() if last_ts else None,
        "atr_period": int(atr_period),
        "multiples": {"stop": stop_mult, "trail": trail_mult, "target": target_mult},
        "sizes": out,
    }
