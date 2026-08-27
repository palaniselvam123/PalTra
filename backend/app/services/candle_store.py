"""Multi-timeframe OHLCV history for charting.

`CandleBuilder` already aggregates ticks, but it holds exactly ONE interval —
whatever the bot is configured for — because that is all the strategy needs.
A chart needs several timeframes at once and needs them whether or not the bot
is running, so this keeps its own buckets fed from the same tick loop.

**Series are keyed by data source, and never merged across sources.** An
earlier version keyed only by (symbol, interval), so backfilling real NSE
history into a symbol that had been ticking on the simulated feed produced a
single candle with a synthetic open of 2784 and a real low of 1317 — a 2x
range that never happened. Synthetic and real prices are different series
about different worlds; blending them silently manufactures fiction.
"""
from __future__ import annotations

import time
from collections import defaultdict

from app.services.indicators import OHLCV

# Seconds per bar. Anything the UI offers must exist here.
INTERVALS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}

IST_OFFSET_SEC = 19800  # +05:30
MAX_BARS = 1500  # per symbol per interval per source


def bucket_start(ts: int, interval: str) -> int:
    """Bar-open timestamp for `ts`.

    Daily bars anchor to the IST trading day, not UTC midnight — bucketing a
    day by UTC would split an Indian session across two bars at 05:30 IST,
    right in the middle of the pre-open.
    """
    seconds = INTERVALS[interval]
    if interval == "1d":
        return ((ts + IST_OFFSET_SEC) // seconds) * seconds - IST_OFFSET_SEC
    return ts - (ts % seconds)


class CandleStore:
    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, str], list[OHLCV]] = defaultdict(list)
        self._last_cum_volume: dict[tuple[str, str], int] = {}
        self._backfilled: set[tuple[str, str, str]] = set()

    # ---- ingest ---------------------------------------------------------

    def on_tick(self, symbol: str, price: float, cum_volume: int, source: str, now: float | None = None) -> None:
        ts = int(now if now is not None else time.time())

        # Volume arrives cumulative-per-day, so the per-bar figure is the
        # delta. Tracked per source: the simulated and live feeds keep
        # unrelated counters, and differencing across a source switch would
        # produce one enormous bogus volume bar.
        vol_key = (symbol, source)
        prev_cum = self._last_cum_volume.get(vol_key, cum_volume)
        delta = max(0, cum_volume - prev_cum)
        self._last_cum_volume[vol_key] = cum_volume

        for name in INTERVALS:
            bucket = bucket_start(ts, name)
            bars = self._bars[(symbol, name, source)]
            if bars and bars[-1].ts == bucket:
                bar = bars[-1]
                bar.high = max(bar.high, price)
                bar.low = min(bar.low, price)
                bar.close = price
                bar.volume += delta
            else:
                bars.append(OHLCV(ts=bucket, open=price, high=price, low=price, close=price, volume=delta))
                if len(bars) > MAX_BARS:
                    del bars[0 : len(bars) - MAX_BARS]

    def seed(self, symbol: str, interval: str, source: str, candles: list[OHLCV]) -> None:
        """Installs historical bars for one source's series.

        Locally-built bars win on a timestamp collision — they were built from
        ticks this process actually observed, and only bars from the SAME
        source are ever considered, so this cannot mix price worlds.
        """
        key = (symbol, interval, source)
        live = {b.ts: b for b in self._bars[key]}
        merged = {b.ts: b for b in candles}
        merged.update(live)
        self._bars[key] = [merged[ts] for ts in sorted(merged)][-MAX_BARS:]
        self._backfilled.add(key)

    def needs_backfill(self, symbol: str, interval: str, source: str) -> bool:
        return (symbol, interval, source) not in self._backfilled

    def drop_source(self, source: str) -> int:
        """Discards everything collected under one source. Used when the feed
        switches, so stale synthetic bars cannot linger behind a real chart.
        """
        keys = [k for k in self._bars if k[2] == source]
        for k in keys:
            del self._bars[k]
        self._backfilled -= set(keys)
        for vk in [k for k in self._last_cum_volume if k[1] == source]:
            del self._last_cum_volume[vk]
        return len(keys)

    # ---- read -----------------------------------------------------------

    def get(self, symbol: str, interval: str, source: str, limit: int = 500) -> list[OHLCV]:
        return self._bars[(symbol, interval, source)][-limit:]

    def stats(self) -> dict:
        return {
            "series": len(self._bars),
            "total_bars": sum(len(v) for v in self._bars.values()),
            "backfilled": len(self._backfilled),
        }


candle_store = CandleStore()
