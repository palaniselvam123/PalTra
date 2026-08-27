"""Dataset validation and the quality report.

Research conclusions inherit the defects of the data underneath them, and the
defects that matter here are quiet ones: a duplicated bar inflates a sample, a
silently missing hour hides a gap the strategy would have traded through, an
OHLC row where `low > open` means the feed is lying about something.

Two rules this module follows:

* **Nothing is deleted.** Problems are recorded and reported. A validator that
  quietly drops suspicious rows produces a clean-looking dataset whose cleanup
  no one can audit, and the row it dropped might have been the real one.
* **Absence is not automatically a defect.** NSE is closed at weekends and on
  trading holidays, and the app has no holiday calendar
  (`market_clock.is_trading_day` is a weekday check by its own admission).
  Rather than hardcode a list that silently goes stale, holidays are inferred
  from the data: a weekday on which EVERY symbol has zero candles is a market
  holiday, not 50 simultaneous per-symbol gaps. That inference needs a
  cross-section, so it is done over the whole dataset rather than per symbol.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from app.core.market_clock import MARKET_CLOSE, MARKET_OPEN
from app.research.store import ResearchStore, ist_date
from app.services.candle_store import INTERVALS
from app.services.indicators import OHLCV


@dataclass
class Issue:
    kind: str
    symbol: str
    detail: str
    ts: int | None = None

    def as_dict(self) -> dict:
        d = {"kind": self.kind, "symbol": self.symbol, "detail": self.detail}
        if self.ts is not None:
            d["ts"] = self.ts
        return d


@dataclass
class ValidationResult:
    symbols: int = 0
    total_candles: int = 0
    trading_days: int = 0
    start: str | None = None
    end: str | None = None
    interval: str = ""
    duplicates: int = 0
    missing_intervals: int = 0
    invalid_ohlc: int = 0
    invalid_volume: int = 0
    timestamp_issues: int = 0
    holidays_detected: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.invalid_ohlc or self.duplicates or self.timestamp_issues or self.invalid_volume:
            return "FAILED"
        if self.missing_intervals:
            return "WARNING"
        if self.total_candles == 0:
            return "FAILED"
        return "READY"

    def as_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "interval": self.interval,
            "start": self.start,
            "end": self.end,
            "trading_days": self.trading_days,
            "total_candles": self.total_candles,
            "duplicates": self.duplicates,
            "missing_intervals": self.missing_intervals,
            "invalid_ohlc": self.invalid_ohlc,
            "invalid_volume": self.invalid_volume,
            "timestamp_issues": self.timestamp_issues,
            "holidays_detected": self.holidays_detected,
            "status": self.status,
            # Capped: a systematically broken feed would otherwise produce a
            # report too large to read, and the counts above carry the scale.
            "sample_issues": [i.as_dict() for i in self.issues[:50]],
        }


def expected_bars_per_session(interval: str) -> int:
    """How many bars a full NSE session contains at this interval."""
    seconds = INTERVALS[interval]
    open_dt = dt.datetime.combine(dt.date(2024, 1, 1), MARKET_OPEN)
    close_dt = dt.datetime.combine(dt.date(2024, 1, 1), MARKET_CLOSE)
    return max(int((close_dt - open_dt).total_seconds() // seconds), 1)


def _check_series(symbol: str, bars: list[OHLCV], interval: str) -> tuple[list[Issue], dict]:
    """Per-symbol checks that need no cross-sectional context."""
    issues: list[Issue] = []
    counts = {"duplicates": 0, "invalid_ohlc": 0, "invalid_volume": 0, "timestamp_issues": 0}

    seen: set[int] = set()
    prev_ts: int | None = None
    for b in bars:
        if b.ts in seen:
            counts["duplicates"] += 1
            issues.append(Issue("duplicate", symbol, f"repeated timestamp {b.ts}", b.ts))
        seen.add(b.ts)

        if prev_ts is not None and b.ts <= prev_ts:
            counts["timestamp_issues"] += 1
            issues.append(Issue("out_of_order", symbol, f"{b.ts} follows {prev_ts}", b.ts))
        prev_ts = b.ts

        # A bar is a summary of a period: the low must be the floor and the
        # high the ceiling of everything that happened inside it.
        if not (b.low <= b.high and b.low <= b.open <= b.high and b.low <= b.close <= b.high):
            counts["invalid_ohlc"] += 1
            issues.append(
                Issue("invalid_ohlc", symbol, f"o={b.open} h={b.high} l={b.low} c={b.close}", b.ts)
            )

        if b.volume < 0:
            counts["invalid_volume"] += 1
            issues.append(Issue("invalid_volume", symbol, f"negative volume {b.volume}", b.ts))

        if b.ts % INTERVALS[interval] != 0 and interval != "1d":
            counts["timestamp_issues"] += 1
            issues.append(Issue("unaligned_timestamp", symbol, f"{b.ts} not on a {interval} boundary", b.ts))

    return issues, counts


def validate(
    data: dict[str, list[OHLCV]],
    interval: str,
    expected_bars: int | None = None,
    missing_tolerance: float = 0.9,
) -> ValidationResult:
    """Validate a loaded dataset.

    `missing_tolerance` is the fraction of a full session below which a day is
    reported as having missing intervals. It is not 1.0 because a real feed
    routinely omits a bar in which nothing traded, and calling every such bar a
    defect would bury the gaps that matter.
    """
    res = ValidationResult(interval=interval, symbols=len(data))
    if not data:
        return res

    full_session = expected_bars or expected_bars_per_session(interval)

    # Cross-section first: which weekdays did the whole market sit out?
    days_by_symbol: dict[str, set[dt.date]] = {}
    all_days: set[dt.date] = set()
    for sym, bars in data.items():
        d = {ist_date(b.ts) for b in bars}
        days_by_symbol[sym] = d
        all_days |= d

    if all_days:
        span_start, span_end = min(all_days), max(all_days)
        res.start, res.end = span_start.isoformat(), span_end.isoformat()
        cursor = span_start
        holidays: list[dt.date] = []
        while cursor <= span_end:
            if cursor.weekday() < 5 and cursor not in all_days:
                holidays.append(cursor)
            cursor += dt.timedelta(days=1)
        res.holidays_detected = [h.isoformat() for h in holidays]
    res.trading_days = len(all_days)

    for sym, bars in data.items():
        res.total_candles += len(bars)
        issues, counts = _check_series(sym, bars, interval)
        res.issues += issues
        res.duplicates += counts["duplicates"]
        res.invalid_ohlc += counts["invalid_ohlc"]
        res.invalid_volume += counts["invalid_volume"]
        res.timestamp_issues += counts["timestamp_issues"]

        # Missing bars, counted only on days this symbol actually traded, and
        # only against the session — overnight and weekend gaps are not gaps.
        per_day: dict[dt.date, int] = defaultdict(int)
        for b in bars:
            per_day[ist_date(b.ts)] += 1
        for day, n in per_day.items():
            if n < full_session * missing_tolerance:
                short_by = full_session - n
                res.missing_intervals += short_by
                res.issues.append(
                    Issue("missing_bars", sym, f"{day}: {n} of ~{full_session} bars (short {short_by})")
                )

    return res


def format_report(res: ValidationResult) -> str:
    """Human-readable quality report."""
    lines = [
        "DATASET QUALITY REPORT",
        "",
        f"  Symbols:              {res.symbols}",
        f"  Period:               {res.start} -> {res.end}",
        f"  Interval:             {res.interval}",
        f"  Trading days:         {res.trading_days}",
        f"  Total candles:        {res.total_candles}",
        "",
        f"  Duplicate candles:    {res.duplicates}",
        f"  Missing intervals:    {res.missing_intervals}",
        f"  Invalid OHLC rows:    {res.invalid_ohlc}",
        f"  Invalid volume rows:  {res.invalid_volume}",
        f"  Timestamp issues:     {res.timestamp_issues}",
        f"  Holidays detected:    {len(res.holidays_detected)}"
        + (f"  {', '.join(res.holidays_detected)}" if res.holidays_detected else ""),
        "",
        f"  STATUS: {res.status}",
    ]
    if res.issues:
        lines += ["", "  first issues:"]
        lines += [f"    {i.kind:20} {i.symbol:12} {i.detail}" for i in res.issues[:10]]
        if len(res.issues) > 10:
            lines.append(f"    ... and {len(res.issues) - 10} more")
    return "\n".join(lines)


def validate_store(
    store: ResearchStore, interval: str, source: str = "live", symbols: list[str] | None = None
) -> ValidationResult:
    syms = symbols or store.symbols(interval, source)
    return validate(store.read_many(syms, interval, source), interval)
