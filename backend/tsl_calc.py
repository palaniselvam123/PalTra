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
import math
import statistics

import pandas as pd

import smart_stop

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


# ---------------------------------------------------------------------------------------------------------------
# One stop / trail / target for every stock a bot trades (the Settings "All armed stocks" calculator).
# A fixed ₹ stop means a different thing on a ₹100 stock and a ₹5,000 one, so this reads each stock's own ATR on
# the bot's candle and shows what one common % (or one set of ATR multiples) means for each of them.

TIGHT_ATR = 1.0  # a common % stop under 1 ATR is inside the stock's normal wiggle: likely stopped out by noise
WIDE_ATR = 3.0  # over 3 ATR the loss when it is hit is far bigger than the stock needs
PCT_STEP = 0.05


def breakeven_after(stop: float, trail: float) -> float | None:
    """How far the price must go your way before the trailing stop reaches the entry (no loss left).

    The stop moves one trail step for each full step the price gains, so it needs ceil(stop / trail) steps."""
    if not stop or not trail or trail <= 0 or stop <= 0:
        return None
    steps = math.ceil(stop / trail - 1e-9)
    return round(steps * trail, 4)


def _round_pct(value: float) -> float:
    return max(PCT_STEP, round(round(value / PCT_STEP) * PCT_STEP, 2))


def _fit(stop_rs: float, atr: float | None) -> tuple[float | None, str]:
    if not atr or atr <= 0:
        return None, "unknown"
    mult = stop_rs / atr
    if mult < TIGHT_ATR:
        return round(mult, 2), "tight"
    if mult > WIDE_ATR:
        return round(mult, 2), "wide"
    return round(mult, 2), "fits"


def basket(
    tapes: dict,
    minutes: int,
    atr_period: int = 14,
    stop_mult: float = DEFAULT_STOP,
    trail_mult: float = DEFAULT_TRAIL,
    target_mult: float = DEFAULT_TARGET,
    cfg=None,
) -> dict:
    """Per stock: its ATR on `minutes` candles and the multiples in ₹ and %; plus one common % for all of them
    (the median of the stocks' own %) and what that common % means in ₹ and in ATRs for each stock."""
    rows: list[dict] = []
    for symbol, tape in tapes.items():
        name = (symbol or "").upper()
        if tape is None or getattr(tape, "empty", True) or len(tape) < 3:
            rows.append({"symbol": name, "price": None, "atr": None, "note": "No candles for this stock yet."})
            continue
        price = float(tape["close"].iloc[-1])
        read = _size_read(tape, int(minutes), int(atr_period))
        atr = read.get("atr") if read else None
        row: dict = {"symbol": name, "price": price, "atr": atr, "candles": read.get("candles") if read else 0}
        # The smart stop's room for a buy and a sell today (smart_stop.py): this stock's own recovered dips.
        for side, key in (("LONG", "smart_long"), ("SHORT", "smart_short")):
            info = smart_stop.room(tape, side, price, cfg if cfg is not None else object())
            row[key] = {"pct": info["pct"], "rs": round_price(name, info["rs"]), "samples": info["samples"], "source": info["source"]}
        if not atr or price <= 0:
            row["note"] = f"Not enough {minutes}-minute candles for an ATR {atr_period} yet."
            rows.append(row)
            continue
        stop = round_price(name, atr * stop_mult)
        trail = round_price(name, atr * trail_mult)
        target = round_price(name, atr * target_mult) if target_mult > 0 else 0.0
        be = breakeven_after(stop, trail)
        row.update(
            {
                "atr_pct": round(atr / price * 100, 3),
                "stop": stop,
                "trail": trail,
                "target": target,
                "stop_pct": round(stop / price * 100, 3),
                "trail_pct": round(trail / price * 100, 3),
                "target_pct": round(target / price * 100, 3) if target else 0.0,
                "breakeven_after": be,
                "breakeven_after_pct": round(be / price * 100, 3) if be else None,
            }
        )
        rows.append(row)
    sized = [r for r in rows if r.get("stop")]
    percent = None
    if sized:
        stop_pct = _round_pct(statistics.median(r["stop_pct"] for r in sized))
        trail_pct = _round_pct(statistics.median(r["trail_pct"] for r in sized))
        target_pct = _round_pct(statistics.median(r["target_pct"] for r in sized)) if target_mult > 0 else 0.0
        per_stock = []
        for r in sized:
            stop_rs = round_price(r["symbol"], r["price"] * stop_pct / 100)
            trail_rs = round_price(r["symbol"], r["price"] * trail_pct / 100)
            target_rs = round_price(r["symbol"], r["price"] * target_pct / 100) if target_pct else 0.0
            mult, fit = _fit(stop_rs, r["atr"])
            per_stock.append(
                {
                    "symbol": r["symbol"],
                    "price": r["price"],
                    "stop": stop_rs,
                    "trail": trail_rs,
                    "target": target_rs,
                    "stop_atr": mult,
                    "fit": fit,
                    "breakeven_after": breakeven_after(stop_rs, trail_rs),
                }
            )
        percent = {
            "stop_pct": stop_pct,
            "trail_pct": trail_pct,
            "target_pct": target_pct,
            "breakeven_after_pct": breakeven_after(stop_pct, trail_pct),
            "per_stock": per_stock,
            "tight": [p["symbol"] for p in per_stock if p["fit"] == "tight"],
            "wide": [p["symbol"] for p in per_stock if p["fit"] == "wide"],
        }
    return {
        "minutes": int(minutes),
        "atr_period": int(atr_period),
        "multiples": {"stop": stop_mult, "trail": trail_mult, "target": target_mult},
        "stocks": rows,
        "sized": len(sized),
        "percent": percent,
    }
