"""Historical ingestion for the research store.

Flow: work out which days are missing, fetch only those, validate, store, then
record coverage so the same history is never downloaded twice.

**Isolation.** This borrows the live broker session (a logged-in API client)
but writes nowhere near the live path — not to `candle_store`, not to
`trading.db`, and it never touches feed state. It is deliberately not wired
into the tick loop or the scanner. The only shared resource is the broker
connection itself, which is why the pacing below exists.

**Pacing.** `GrowwClient` has no retry or rate limiting of its own (verified:
no sleep/retry/backoff in the client). A research backfill issues far more
historical calls than the live app ever does, so the throttle and backoff live
here rather than in the client, where they would alter live behaviour.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass, field

from app.core.market_clock import IST
from app.research.store import Candle, ResearchStore, ist_date, store as default_store
from app.services.candle_store import INTERVALS

# Seconds between historical calls. The live app polls quotes every 2s; a
# backfill running alongside it must stay well clear of that budget.
REQUEST_SPACING_SEC = 1.2
MAX_RETRIES = 4
BACKOFF_BASE_SEC = 2.0

# Days per API call. Groww refuses a window wider than a per-interval maximum
# with "Invalid interval value" — measured with `probe_max_history`, not
# guessed: at 5m a 15-day window succeeds and 20 days is refused. Kept a little
# under each measured ceiling so a boundary day cannot tip a request over.
CHUNK_DAYS = {"1m": 3, "5m": 14, "15m": 30, "1h": 90, "1d": 300}


@dataclass
class IngestionReport:
    symbols_requested: int = 0
    symbols_succeeded: int = 0
    days_requested: int = 0
    candles_fetched: int = 0
    candles_new: int = 0
    skipped_already_have: int = 0
    failures: list[str] = field(default_factory=list)
    rejected_rows: int = 0

    def as_dict(self) -> dict:
        return {
            "symbols_requested": self.symbols_requested,
            "symbols_succeeded": self.symbols_succeeded,
            "days_requested": self.days_requested,
            "candles_fetched": self.candles_fetched,
            "candles_new": self.candles_new,
            "skipped_already_have": self.skipped_already_have,
            "rejected_rows": self.rejected_rows,
            "failures": self.failures[:20],
        }


def _trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    """Weekdays in range. Holidays are not known in advance — they are
    discovered by fetching and finding the day genuinely empty, which is then
    recorded as EMPTY so it is never requested again."""
    out, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += dt.timedelta(days=1)
    return out


def missing_days(
    store: ResearchStore, symbol: str, interval: str, source: str, start: dt.date, end: dt.date
) -> list[dt.date]:
    """Trading days in range with no coverage record yet."""
    have = store.covered_days(symbol, interval, source)
    return [d for d in _trading_days(start, end) if d not in have]


def _chunk(days: list[dt.date], size: int) -> list[tuple[dt.date, dt.date]]:
    """Group consecutive missing days into fetch windows."""
    if not days:
        return []
    days = sorted(days)
    out, chunk_start, prev = [], days[0], days[0]
    for d in days[1:]:
        if (d - chunk_start).days >= size:
            out.append((chunk_start, prev))
            chunk_start = d
        prev = d
    out.append((chunk_start, prev))
    return out


async def _fetch_window(
    client, symbol: str, interval: str, start: dt.date, end: dt.date
) -> list[tuple[int, float, float, float, float, int]]:
    """One historical call for an explicit window, with backoff.

    Uses `get_candles_window` rather than `get_candles(days=...)`: the latter
    only measures back from now, so requesting an old window means requesting a
    very wide one, which the API refuses outright past its per-interval cap.
    """
    minutes = INTERVALS[interval] // 60
    # Request whole days in IST and filter after — the API takes naive local
    # datetimes, and a boundary bar is cheaper to drop than to miss.
    start_dt = dt.datetime.combine(start, dt.time(0, 0))
    end_dt = dt.datetime.combine(end, dt.time(23, 59, 59))
    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES):
        try:
            rows = await client.get_candles_window(symbol, minutes, start_dt, end_dt)
            return [r for r in rows if start <= ist_date(r[0]) <= end]
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            await asyncio.sleep(BACKOFF_BASE_SEC * (2**attempt))
    raise RuntimeError(f"{symbol} {start}..{end}: {last_error}")


def _to_candles(symbol: str, interval: str, source: str, rows: list) -> tuple[list[Candle], int]:
    """Convert raw rows, rejecting any that fail the OHLC invariant.

    Rejections are counted and returned rather than silently dropped — a feed
    that starts emitting impossible bars is a fact the caller needs to see, not
    something to quietly filter away.
    """
    out, rejected = [], 0
    for ts, o, h, l, c, v in rows:
        if not (l <= h and l <= o <= h and l <= c <= h) or v < 0:
            rejected += 1
            continue
        out.append(Candle(symbol, interval, int(ts), float(o), float(h), float(l), float(c), int(v), source))
    return out, rejected


async def backfill(
    symbols: list[str],
    interval: str,
    start_date: dt.date,
    end_date: dt.date,
    client,
    source: str = "live",
    store: ResearchStore | None = None,
    progress=None,
) -> IngestionReport:
    """Fetch and store missing history for `symbols` over a date range.

    Safe to interrupt and re-run: coverage is recorded per day as it lands, so
    a second run resumes rather than restarting.
    """
    st = store or default_store
    report = IngestionReport(symbols_requested=len(symbols))
    chunk_size = CHUNK_DAYS.get(interval, 25)

    for symbol in symbols:
        need = missing_days(st, symbol, interval, source, start_date, end_date)
        already = len(_trading_days(start_date, end_date)) - len(need)
        report.skipped_already_have += already
        if not need:
            report.symbols_succeeded += 1
            if progress:
                progress(f"{symbol}: already complete")
            continue

        report.days_requested += len(need)
        symbol_ok = True
        for win_start, win_end in _chunk(need, chunk_size):
            try:
                rows = await _fetch_window(client, symbol, interval, win_start, win_end)
            except Exception as exc:  # noqa: BLE001
                symbol_ok = False
                report.failures.append(str(exc))
                for d in need:
                    if win_start <= d <= win_end:
                        st.mark_coverage(symbol, interval, source, d, 0, "FAILED", str(exc)[:200])
                continue

            candles, rejected = _to_candles(symbol, interval, source, rows)
            report.rejected_rows += rejected
            report.candles_fetched += len(candles)
            report.candles_new += st.upsert(candles)

            by_day: dict[dt.date, int] = {}
            for c in candles:
                by_day[ist_date(c.ts)] = by_day.get(ist_date(c.ts), 0) + 1
            # Every requested day in the window gets a record. A day with no
            # candles is marked EMPTY (a holiday, or pre-listing) so it is
            # never requested again — that distinction is the whole point of
            # the coverage table.
            for d in need:
                if win_start <= d <= win_end:
                    n = by_day.get(d, 0)
                    st.mark_coverage(symbol, interval, source, d, n, "OK" if n else "EMPTY")

            if progress:
                progress(f"{symbol}: {win_start}..{win_end} -> {len(candles)} candles")
            await asyncio.sleep(REQUEST_SPACING_SEC)

        if symbol_ok:
            report.symbols_succeeded += 1

    return report


async def update(
    symbols: list[str], interval: str, client, source: str = "live",
    store: ResearchStore | None = None, lookback_days: int = 7, progress=None,
) -> IngestionReport:
    """Top up to today. Re-checks a short recent window so a day that was
    partially captured while the market was still open gets completed."""
    st = store or default_store
    today = dt.datetime.now(IST).date()
    recent = today - dt.timedelta(days=lookback_days)
    with st._conn() as conn:  # noqa: SLF001 — same package
        conn.execute(
            "DELETE FROM research_coverage WHERE interval=? AND source=? AND day >= ?",
            (interval, source, recent.isoformat()),
        )
    return await backfill(symbols, interval, recent, today, client, source, st, progress)


async def probe_max_history(client, symbol: str, interval: str = "5m", ladder=None, widths=None) -> dict:
    """Measure what the data source actually allows, in two dimensions.

    The application's own limits (`BACKFILL_DAYS`, `MAX_BARS`) say nothing
    about the API, and the API has two separate constraints that are easy to
    confuse: how WIDE a single request may be, and how FAR BACK data exists at
    all. A request that fails could mean either, so they are probed separately.
    """
    minutes = INTERVALS[interval] // 60
    now = dt.datetime.now()

    width: list[dict] = []
    for days in widths or (5, 10, 12, 15, 20, 30, 60, 90):
        try:
            rows = await client.get_candles_window(
                symbol, minutes, now - dt.timedelta(days=days), now
            )
            width.append({"window_days": days, "rows": len(rows),
                          "first": ist_date(rows[0][0]).isoformat() if rows else None})
        except Exception as exc:  # noqa: BLE001
            width.append({"window_days": days, "error": str(exc)[:160]})
        await asyncio.sleep(REQUEST_SPACING_SEC)

    # How far back does history exist? Ask with a window known to be legal,
    # slid progressively further into the past.
    widest = max((w["window_days"] for w in width if "rows" in w and w["rows"]), default=10)
    depth: list[dict] = []
    for days_back in ladder or (30, 60, 75, 90, 120, 180, 365):
        end = now - dt.timedelta(days=days_back)
        start = end - dt.timedelta(days=widest)
        try:
            rows = await client.get_candles_window(symbol, minutes, start, end)
            depth.append({
                "days_back": days_back,
                "window": [start.date().isoformat(), end.date().isoformat()],
                "rows": len(rows),
                "first": ist_date(rows[0][0]).isoformat() if rows else None,
            })
        except Exception as exc:  # noqa: BLE001
            depth.append({"days_back": days_back, "error": str(exc)[:160]})
        await asyncio.sleep(REQUEST_SPACING_SEC)

    return {"symbol": symbol, "interval": interval,
            "max_request_window_days": widest, "width_probes": width, "depth_probes": depth}
