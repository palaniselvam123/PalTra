"""Most active stocks: where the money is trading on NSE right now.

The scalp monitor ranks only the stocks the desk already streams. This scans
the whole F&O stock list (about 200 of the most traded NSE names) with one
Groww quote per stock and ranks them by what the crowd is doing:

* **₹ traded today** — volume × average price. The plainest measure of where
  traders are.
* **Volume vs usual** — today's volume so far against the 20-day average
  scaled to the share of the session gone. 2× means twice the usual crowd.
* **Buy vs sell quantity** — Groww's total pending buy and sell quantity in
  the order book. More buyers waiting than sellers leans up, and the reverse.
* **vs average price** — the day's average traded price is the session VWAP:
  above it, today's buyers are in profit.

Market data only. Nothing here places, changes or cancels an order.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
from dataclasses import asdict, dataclass

from app.core.market_clock import IST, ist_now, is_market_open

log = logging.getLogger("active_stocks")

SCAN_EVERY_SEC = 300          # one pass every 5 minutes while the market is open
MIN_MANUAL_GAP_SEC = 60       # "Scan now" at most once a minute
CONCURRENCY = 3               # parallel quote calls, gentle on Groww's rate limit
CALL_GAP_SEC = 0.12
MAX_UNIVERSE = 250
AVG_DAYS = 20
SESSION_OPEN = dt.time(9, 15)
SESSION_MINUTES = 375         # 09:15–15:30


@dataclass
class ActiveRow:
    symbol: str
    ltp: float
    change_pct: float | None        # vs previous close
    volume: int
    value_cr: float                 # volume × average price, ₹ crore
    avg_price: float | None         # Groww's average traded price today (session VWAP)
    vwap_dist_pct: float | None     # ltp vs average price
    rvol: float | None              # volume so far / usual volume by this time
    buy_qty: int | None             # total pending buy quantity
    sell_qty: int | None            # total pending sell quantity
    buy_share: float | None         # buy_qty / (buy_qty + sell_qty), 0..1
    range_pct: float | None         # today's high-low as % of open
    bias: str                       # LONG / SHORT / NONE

    def as_dict(self) -> dict:
        return asdict(self)


def _num(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None  # NaN -> None


def _pick(data: dict, *keys):
    for key in keys:
        if isinstance(data, dict) and data.get(key) not in (None, ""):
            return data[key]
    return None


def session_fraction(now: dt.datetime) -> float:
    """Share of the 09:15–15:30 session gone, floored so the first minutes
    do not divide by almost nothing."""
    start = dt.datetime.combine(now.date(), SESSION_OPEN, tzinfo=now.tzinfo)
    gone = (now - start).total_seconds() / 60
    return max(0.05, min(1.0, gone / SESSION_MINUTES))


def row_from_quote(symbol: str, data: dict, avg_daily_volume: float | None, fraction: float) -> ActiveRow | None:
    """One ranked row from a Groww quote payload. None without a price or volume."""
    ltp = _num(_pick(data, "last_price", "ltp"))
    volume = _num(_pick(data, "volume", "day_volume", "total_traded_volume"))
    if not ltp or ltp <= 0 or volume is None or volume <= 0:
        return None
    ohlc = data.get("ohlc") if isinstance(data.get("ohlc"), dict) else {}
    prev_close = _num(_pick(ohlc, "close"))
    day_open = _num(_pick(ohlc, "open"))
    high, low = _num(_pick(ohlc, "high")), _num(_pick(ohlc, "low"))
    avg_price = _num(_pick(data, "average_price", "avg_price", "atp"))

    change = _num(_pick(data, "day_change_perc", "day_change_percentage"))
    if change is None and prev_close:
        change = (ltp - prev_close) / prev_close * 100
    vwap_dist = (ltp - avg_price) / avg_price * 100 if avg_price else None
    traded_at = avg_price or ltp
    rvol = None
    if avg_daily_volume and avg_daily_volume > 0:
        rvol = volume / (avg_daily_volume * fraction)

    buy = _num(_pick(data, "total_buy_quantity", "total_bid_quantity"))
    sell = _num(_pick(data, "total_sell_quantity", "total_ask_quantity"))
    share = buy / (buy + sell) if buy is not None and sell is not None and buy + sell > 0 else None
    range_pct = (high - low) / day_open * 100 if high and low and day_open else None

    bias = "NONE"
    if vwap_dist is not None and change is not None:
        if vwap_dist > 0 and change > 0:
            bias = "LONG"
        elif vwap_dist < 0 and change < 0:
            bias = "SHORT"

    return ActiveRow(
        symbol=symbol,
        ltp=round(ltp, 2),
        change_pct=change,
        volume=int(volume),
        value_cr=volume * traded_at / 1e7,
        avg_price=round(avg_price, 2) if avg_price else None,
        vwap_dist_pct=vwap_dist,
        rvol=rvol,
        buy_qty=int(buy) if buy is not None else None,
        sell_qty=int(sell) if sell is not None else None,
        buy_share=share,
        range_pct=range_pct,
        bias=bias,
    )


SORTS = {
    "value": lambda r: r.value_cr,
    "rvol": lambda r: r.rvol or 0.0,
    "change": lambda r: abs(r.change_pct or 0.0),
    "pressure": lambda r: abs((r.buy_share or 0.5) - 0.5),
}


class ActiveScanner:
    """Holds the last scan and runs the next one. Memory only."""

    def __init__(self) -> None:
        self.rows: list[ActiveRow] = []
        self.as_of: dt.datetime | None = None
        self.universe: int = 0
        self.failed: int = 0
        self.error: str | None = None
        self.running = False
        self._avg_volume: dict[str, float] = {}
        self._avg_day: dt.date | None = None
        self._last_start: dt.datetime | None = None
        self._task: asyncio.Task | None = None
        self.manual_task: asyncio.Task | None = None

    def universe_symbols(self) -> list[str]:
        from app.services.instruments import instrument_master

        try:
            names = instrument_master.fno_stocks()
        except Exception as exc:  # noqa: BLE001 — the list is a public CSV; a network blip must not break the page
            self.error = f"Could not load the F&O stock list: {exc}"
            return []
        return names[:MAX_UNIVERSE]

    async def _average_volumes(self, client, symbols: list[str], today: dt.date) -> None:
        """20-day average daily volume, fetched once a day per stock."""
        if self._avg_day != today:
            self._avg_volume = {}
            self._avg_day = today
        missing = [s for s in symbols if s not in self._avg_volume]
        if not missing:
            return
        before = dt.datetime.combine(today - dt.timedelta(days=1), dt.time(23, 59))
        sem = asyncio.Semaphore(CONCURRENCY)

        async def one(symbol: str) -> None:
            async with sem:
                try:
                    vols = await client.get_daily_volumes(symbol, AVG_DAYS, end=before)
                except Exception:  # noqa: BLE001 — no average just means no "vs usual" for that stock
                    vols = []
                good = [v for v in vols if v and v > 0]
                self._avg_volume[symbol] = sum(good) / len(good) if good else 0.0
                await asyncio.sleep(CALL_GAP_SEC)

        await asyncio.gather(*(one(s) for s in missing))

    async def scan(self, client, symbols: list[str] | None = None, now: dt.datetime | None = None) -> None:
        if self.running:
            return
        self.running = True
        self._last_start = ist_now()
        try:
            names = symbols if symbols is not None else self.universe_symbols()
            if not names:
                self.error = self.error or "No F&O stock list to scan."
                return
            now = now or ist_now()
            await self._average_volumes(client, names, now.date())
            fraction = session_fraction(now)
            sem = asyncio.Semaphore(CONCURRENCY)
            rows: list[ActiveRow] = []
            failed = 0
            errors: list[str] = []

            async def one(symbol: str) -> None:
                nonlocal failed
                async with sem:
                    try:
                        data = await client.get_quote_payload(symbol)
                    except Exception as exc:  # noqa: BLE001
                        failed += 1
                        errors.append(str(exc))
                        return
                    finally:
                        await asyncio.sleep(CALL_GAP_SEC)
                    row = row_from_quote(symbol, data, self._avg_volume.get(symbol), fraction)
                    if row is not None:
                        rows.append(row)

            await asyncio.gather(*(one(s) for s in names))
            self.rows = sorted(rows, key=SORTS["value"], reverse=True)
            self.universe = len(names)
            self.failed = failed
            self.as_of = now
            self.error = None if rows else (errors[0] if errors else "Groww sent no volume for these stocks yet.")
        finally:
            self.running = False

    def can_scan_now(self) -> bool:
        if self.running:
            return False
        if self._last_start is None:
            return True
        return (ist_now() - self._last_start).total_seconds() >= MIN_MANUAL_GAP_SEC

    def start_background(self, get_client) -> None:
        """Rescan every few minutes while the market is open and a Groww session exists."""
        if self._task is not None and not self._task.done():
            return

        async def loop() -> None:
            while True:
                try:
                    client = get_client()
                    if client is not None and is_market_open():
                        await self.scan(client)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — never let the loop die
                    self.error = f"{type(exc).__name__}: {exc}"
                    log.warning("active scan failed: %s", exc)
                await asyncio.sleep(SCAN_EVERY_SEC)

        self._task = asyncio.create_task(loop())

    def stop_background(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def snapshot(self, sort: str = "value", top: int = 50, bias: str | None = None, min_value_cr: float = 0.0) -> dict:
        key = SORTS.get(sort, SORTS["value"])
        rows = [r for r in self.rows if r.value_cr >= min_value_cr]
        if bias in ("LONG", "SHORT"):
            rows = [r for r in rows if r.bias == bias]
        rows = sorted(rows, key=key, reverse=True)[:top]
        return {
            "as_of": self.as_of.astimezone(IST).isoformat() if self.as_of else None,
            "universe": self.universe,
            "scanned": len(self.rows),
            "failed": self.failed,
            "running": self.running,
            "error": self.error,
            "sort": sort if sort in SORTS else "value",
            "rows": [r.as_dict() for r in rows],
        }


active_scanner = ActiveScanner()
