"""Executed buy / sell volume, estimated (owner's SOLARINDS case: buyers held 79 % of the *waiting* orders while
sellers kept hitting their bids and a strong red candle formed).

Groww's API has no trade-by-trade list and never says which side started a trade, so the shares bought and sold at
market are estimated, one 1-minute candle at a time:

- **ticks** (`src` "T"): the per-second prices the bots record (tick_store.py, kept TICK_KEEP_DAYS) inside that
  minute, read with the tick rule: a price above the one before is a buy, below a sell, unchanged keeps the last
  side. The minute's volume is split by the share of buy ticks.
- **candle** (`src` "C"): no seconds recorded (a stock no bot watched, a day older than the tick store, the
  simulator): the candle's shape, as the cumulative delta reads it (cum_delta.py): buy share = ½ + ½ × (close −
  open) ÷ (high − low), ½ with no range.

The minute's volume is Groww's running session total differenced (indicators.derive_minute_volume). Display and one
optional entry check only (pressure_exit.flip_side's `flow`); nothing here orders.
"""
from __future__ import annotations

import time

import pandas as pd

from indicators import derive_minute_volume
import tick_store

# Finished minutes are kept, so a chart refresh only reads the seconds of the minute still forming. A minute is
# finished once a later minute is on the tape and FINAL_AFTER_SEC have passed.
_CACHE: dict[tuple[str, int], dict | None] = {}
_CACHE_MAX = 60_000
# A minute is final once its seconds have been flushed (tick_store.FLUSH_EVERY_SEC) after its end.
FINAL_AFTER_SEC = 70


def _tick_share(seconds: list[list[float]], before: float | None) -> float | None:
    """Buy share by the tick rule over one minute's [[ts, price], ...], or None when the price never moved."""
    up = down = 0
    last = before
    side = 0
    for _, price in seconds:
        if last is not None:
            if price > last:
                side = 1
            elif price < last:
                side = -1
            if side > 0:
                up += 1
            elif side < 0:
                down += 1
        last = price
    if up + down == 0:
        return None
    return up / (up + down)


def candle_share(o: float, h: float, low: float, c: float) -> float:
    """Buy share from the candle's shape (the cumulative delta's arithmetic)."""
    span = float(h) - float(low)
    if span <= 0:
        return 0.5
    return min(1.0, max(0.0, 0.5 + 0.5 * (float(c) - float(o)) / span))


def minutes(symbol: str, tape: pd.DataFrame | None, start: float, end: float) -> list[dict]:
    """[{"time", "buy", "sell", "src"}] for each 1-minute candle of `tape` with start <= ts < end and a known volume.

    `tape` is the 1-minute frame (ts, open, high, low, close, running volume or minute_volume)."""
    if tape is None or len(tape) == 0 or "ts" not in tape.columns:
        return []
    name = (symbol or "").upper()
    ts_all = pd.to_numeric(tape["ts"], errors="coerce").to_numpy()
    keep = (ts_all >= start) & (ts_all < end)
    if not keep.any():
        return []
    times = ts_all[keep].astype("int64").tolist()
    # Finished minutes come from the cache: a chart refresh (once a second in a replay) only works on new ones.
    todo = [t for t in times if (name, t) not in _CACHE]
    out: dict[int, dict | None] = {}
    if todo:
        if "minute_volume" in tape.columns:
            vols = pd.to_numeric(tape["minute_volume"], errors="coerce")
        else:
            vols = derive_minute_volume(tape)
        want = set(todo)
        pick = keep & pd.Series(ts_all).isin(want).to_numpy()
        rows = tape[pick]
        vol_of = dict(zip(ts_all[pick].astype("int64").tolist(), vols.to_numpy()[pick].tolist()))
        closes = dict(zip(ts_all.astype("int64").tolist(), pd.to_numeric(tape["close"], errors="coerce").tolist()))
        try:
            seconds = tick_store.between(name, min(todo) - 60, max(todo) + 60)
        except Exception:  # noqa: BLE001
            seconds = []
        by_minute: dict[int, list[list[float]]] = {}
        for sec, price in seconds:
            by_minute.setdefault(int(sec) - int(sec) % 60, []).append([sec, price])
        now = time.time()
        last_ts = int(max(ts_all[~pd.isna(ts_all)])) if len(ts_all) else 0
        for t, o, h, low, c in zip(
            rows["ts"].astype("int64").tolist(), rows["open"].tolist(), rows["high"].tolist(),
            rows["low"].tolist(), rows["close"].tolist(),
        ):
            vol = vol_of.get(t)
            point = None
            if vol is not None and pd.notna(vol) and float(vol) > 0:
                before = closes.get(t - 60)
                before = float(before) if before is not None and pd.notna(before) else None
                got = _tick_share(by_minute.get(t - t % 60, []), before) if seconds else None
                src = "T"
                if got is None:
                    got, src = candle_share(o, h, low, c), "C"
                buy = round(float(vol) * got)
                point = {"time": t, "buy": int(buy), "sell": int(round(float(vol)) - buy), "src": src}
            out[t] = point
            # Final only once a later minute is on the tape as well as by the clock: a replay's tape ends in the minute
            # still forming (part of its volume, a price on its way), which the wall clock always calls finished, so
            # it used to be kept with those partial numbers (often no volume yet = a blank) for good.
            if t < last_ts and t + FINAL_AFTER_SEC < now:
                if len(_CACHE) > _CACHE_MAX:
                    _CACHE.clear()
                _CACHE[(name, t)] = point
    result = []
    for t in times:
        point = out[t] if t in out else _CACHE.get((name, t))
        if point is not None:
            result.append(point)
    return result


def share(points: list[dict], side: str) -> float | None:
    """That side's % of the executed volume over the points (None without volume)."""
    buy = sum(p["buy"] for p in points)
    sell = sum(p["sell"] for p in points)
    if buy + sell <= 0:
        return None
    pct = 100.0 * buy / (buy + sell)
    return round(pct if side == "LONG" else 100.0 - pct, 1)


def source_word(points: list[dict]) -> str:
    """"ticks" when every minute was read from recorded seconds, "candles" when none was, else "ticks + candles"."""
    kinds = {p["src"] for p in points}
    if kinds == {"T"}:
        return "ticks"
    if kinds == {"C"}:
        return "candles"
    return "ticks + candles"


def reset() -> None:
    _CACHE.clear()
