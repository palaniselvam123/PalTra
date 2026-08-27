"""Aggregates the raw tick stream into fixed-interval OHLC candles.

The ORB strategy is candle-based (5-minute closes), but the feed only gives
ticks, so this sits between them. Volume arrives cumulative-per-day from the
feed, so we store the per-candle delta instead.
"""
from __future__ import annotations

import time
from collections import defaultdict
from dataclasses import dataclass

MAX_HISTORY_PER_SYMBOL = 500


@dataclass
class BuiltCandle:
    symbol: str
    start_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: int


class CandleBuilder:
    def __init__(self, interval_sec: int = 300):
        self.interval_sec = interval_sec
        self._current: dict[str, BuiltCandle] = {}
        self._history: dict[str, list[BuiltCandle]] = defaultdict(list)
        self._last_cum_volume: dict[str, int] = {}

    def reset(self) -> None:
        self._current.clear()
        self._history.clear()
        self._last_cum_volume.clear()

    def set_interval(self, interval_sec: int) -> None:
        if interval_sec != self.interval_sec:
            self.interval_sec = interval_sec
            self.reset()

    def on_tick(self, symbol: str, price: float, cum_volume: int, now: float | None = None) -> BuiltCandle | None:
        """Feed one tick. Returns a candle only at the moment one completes,
        so callers can treat the return value as a "5-min close" event.
        """
        ts = int(now if now is not None else time.time())
        bucket = ts - (ts % self.interval_sec)

        prev_cum = self._last_cum_volume.get(symbol, cum_volume)
        vol_delta = max(0, cum_volume - prev_cum)
        self._last_cum_volume[symbol] = cum_volume

        current = self._current.get(symbol)
        if current is None:
            self._current[symbol] = BuiltCandle(symbol, bucket, price, price, price, price, vol_delta)
            return None

        if current.start_ts == bucket:
            current.high = max(current.high, price)
            current.low = min(current.low, price)
            current.close = price
            current.volume += vol_delta
            return None

        completed = current
        history = self._history[symbol]
        history.append(completed)
        if len(history) > MAX_HISTORY_PER_SYMBOL:
            del history[0 : len(history) - MAX_HISTORY_PER_SYMBOL]
        self._current[symbol] = BuiltCandle(symbol, bucket, price, price, price, price, vol_delta)
        return completed

    def history(self, symbol: str) -> list[BuiltCandle]:
        return list(self._history.get(symbol, []))

    def current(self, symbol: str) -> BuiltCandle | None:
        return self._current.get(symbol)
