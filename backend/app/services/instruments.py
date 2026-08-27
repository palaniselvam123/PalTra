"""The instrument master — every NSE symbol Groww knows about, for search.

This is the piece that was missing entirely: every quote method on the SDK
(`get_ltp`, `get_quote`, ...) requires the caller to already know the symbol.
There was no way to discover a symbol, so the "watchlist" was really just a
hand-typed Python list of 20 names — nothing like Groww's own app, where
typing "TATA" searches across the whole exchange.

Groww publishes the full instrument list as a public, unauthenticated CSV
(`GrowwAPI.INSTRUMENT_CSV_URL`) — no broker session needed, so search works
even before you've connected live data. It is ~140k rows because it includes
every option strike and expiry; this module filters that down to what a
retail intraday trader can actually search for: NSE cash-market equities.

Fetched once and cached in memory with a TTL, because it is a large (~20MB)
static file that changes at most daily — polling it per search would be both
slow and pointless.
"""
from __future__ import annotations

import csv
import io
import time
import urllib.request
from dataclasses import dataclass

INSTRUMENT_CSV_URL = "https://growwapi-assets.groww.in/instruments/instrument.csv"
CACHE_TTL_SEC = 24 * 3600
FETCH_TIMEOUT_SEC = 30


@dataclass(frozen=True)
class Instrument:
    symbol: str            # trading_symbol — what every quote/order call needs
    name: str
    isin: str
    intraday_allowed: bool  # Groww's own flag: MIS permitted right now on this name
    series: str             # EQ = mainboard equity; others are SME, ETFs, etc.


class InstrumentMaster:
    def __init__(self) -> None:
        self._by_symbol: dict[str, Instrument] = {}
        self._fetched_at: float = 0.0
        self._error: str | None = None

    def _stale(self) -> bool:
        return not self._by_symbol or (time.monotonic() - self._fetched_at) > CACHE_TTL_SEC

    def ensure_loaded(self) -> None:
        if not self._stale():
            return
        try:
            raw = urllib.request.urlopen(INSTRUMENT_CSV_URL, timeout=FETCH_TIMEOUT_SEC).read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001
            self._error = str(exc)
            # Keep serving whatever was cached before — a network hiccup should
            # not empty out search results that were working a moment ago.
            if self._by_symbol:
                return
            raise

        by_symbol: dict[str, Instrument] = {}
        for row in csv.DictReader(io.StringIO(raw)):
            # NSE cash-market equities only. FNO (options/futures) and
            # COMMODITY together are 130k of the 143k rows and are a different
            # trading instrument entirely — irrelevant to an equity watchlist
            # and would drown out the symbols someone is actually searching for.
            if row.get("exchange") != "NSE" or row.get("segment") != "CASH":
                continue
            symbol = row.get("trading_symbol", "").strip()
            if not symbol:
                continue
            by_symbol[symbol] = Instrument(
                symbol=symbol,
                name=row.get("name", "").strip(),
                isin=row.get("isin", "").strip(),
                intraday_allowed=row.get("is_intraday") == "1",
                series=row.get("series", "").strip(),
            )

        self._by_symbol = by_symbol
        self._fetched_at = time.monotonic()
        self._error = None

    def stats(self) -> dict:
        self.ensure_loaded()
        eq = [i for i in self._by_symbol.values() if i.series == "EQ"]
        return {
            "total_nse_cash": len(self._by_symbol),
            "mainboard_equity": len(eq),
            "intraday_eligible": sum(1 for i in eq if i.intraday_allowed),
            "cached_age_sec": round(time.monotonic() - self._fetched_at, 1) if self._fetched_at else None,
            "error": self._error,
        }

    def get(self, symbol: str) -> Instrument | None:
        self.ensure_loaded()
        return self._by_symbol.get(symbol.upper())

    def search(self, query: str, limit: int = 25, equity_only: bool = True) -> list[Instrument]:
        """Symbol-prefix matches first (typing "TATA" should surface TATAMOTORS
        before some ISIN-adjacent footnote), then substring matches on symbol
        or name, both alphabetical. All in-memory — no network call per
        keystroke, which is what makes this different from hitting a live
        broker endpoint for every character typed.
        """
        self.ensure_loaded()
        q = query.strip().upper()
        if not q:
            return []

        pool = self._by_symbol.values()
        if equity_only:
            pool = (i for i in pool if i.series == "EQ")

        prefix, contains = [], []
        for inst in pool:
            if inst.symbol.startswith(q):
                prefix.append(inst)
            elif q in inst.symbol or q in inst.name.upper():
                contains.append(inst)

        prefix.sort(key=lambda i: i.symbol)
        contains.sort(key=lambda i: i.symbol)
        return (prefix + contains)[:limit]


instrument_master = InstrumentMaster()
