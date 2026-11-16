"""Buyers / sellers fade exit for Most active stocks (`pressure_exit`, off by default; `pressure_exit_pct` 55, 30-95;
per bot and per stock, PAPER and LIVE).

Owner's case: stocks armed from the Most active buyers / sellers column were held until a candle of the other colour,
so a trade in profit was often closed at a loss after one big opposite candle. With this on, a stock armed from Most
active (its Trade list source says "Most active": the auto-arm or the card's Arm) is also closed once the side that
started it fades: a buy when the buyers' share of the waiting orders falls to `pressure_exit_pct` % or below, a sell
when the sellers' share does. The colour exit, stops and targets stay; whichever comes first closes the trade.

The shares come from the desk's own Most active scan (every finished pass is noted here, `note_scan`, from
`routes_scalp._pressure_alert` → terminal `pressure_note`), so the exit reacts as often as that scan runs (its auto scan
interval). Only reads taken after the entry and at most `MAX_AGE_SEC` old count, and the exit arms only once the side
was above the level (`peak`, from the last read before the entry on): a trade that started without that pressure is not
closed by it. Replays never see live reads (their clock is in the past), so they keep their normal exits.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

EXIT_REASON = "PRESSURE_FADE"
DEFAULT_PCT = 55.0
MIN_PCT = 30.0
MAX_PCT = 95.0
MAX_AGE_SEC = 20 * 60          # a read older than this is not acted on
KEEP_SEC = 8 * 3600            # reads kept per stock
SOURCE_WORD = "most active"

# symbol -> [(epoch seconds, buyers' share 0..1)], oldest first
_READS: dict[str, list[tuple[float, float]]] = {}
# symbol -> {int epoch: (volume vs usual ×, money traded ₹ cr)} for the same live reads (display only)
_EXTRA: dict[str, dict[int, tuple[float | None, float | None]]] = {}


def _opt(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out >= 0 else None


def usable_share(value) -> float | None:
    """A buyers' share (0..1) worth reading, else None. A one-sided order book (no buy orders or no sell orders
    waiting, so exactly 0 or 1) is a bad quote, not a real 100 % (owner's KAYNES case: 0 buy orders read at 09:31 on
    a stock trading ₹500 cr armed it as "sellers 100%"). Saved scans from before this rule still hold such reads."""
    try:
        share = float(value)
    except (TypeError, ValueError):
        return None
    return share if 0.0 < share < 1.0 else None


def on(cfg) -> bool:
    return bool(getattr(cfg, "pressure_exit", False))


def pct(cfg) -> float:
    value = getattr(cfg, "pressure_exit_pct", None)
    try:
        value = float(DEFAULT_PCT if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_PCT
    return min(MAX_PCT, max(MIN_PCT, value))


def from_most_active(source: str | None) -> bool:
    return SOURCE_WORD in str(source or "").lower()


def _ts(text) -> float | None:
    if not text:
        return None
    try:
        when = dt.datetime.fromisoformat(str(text))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return when.timestamp()


def note_scan(snapshot: dict) -> int:
    """Keep each row's buyers' share with the time its order book was read. Returns how many were kept."""
    start = _ts(snapshot.get("as_of"))
    kept = 0
    for row in snapshot.get("rows") or []:
        symbol = str(row.get("symbol") or "").upper()
        share = usable_share(row.get("buy_share"))
        at = _ts(row.get("read_at")) or start
        if not symbol or share is None or at is None:
            continue
        reads = _READS.setdefault(symbol, [])
        if reads and reads[-1][0] >= at:
            continue
        reads.append((at, share))
        extra = _EXTRA.setdefault(symbol, {})
        extra[int(at)] = (_opt(row.get("rvol")), _opt(row.get("value_cr")))
        cutoff = at - KEEP_SEC
        while reads and reads[0][0] < cutoff:
            extra.pop(int(reads[0][0]), None)
            reads.pop(0)
        kept += 1
    return kept


def reads(symbol: str) -> list[tuple[float, float]]:
    return list(_READS.get(symbol.upper(), ()))


def side_share(share: float, direction: str) -> float:
    """The trade's own side's share in % (buyers for a long, sellers for a short)."""
    return round((share if direction == "LONG" else 1.0 - share) * 100.0, 1)


def judge(symbol: str, direction: str, entry_ts: float, now_ts: float, level: float) -> tuple[bool, str]:
    """(close?, plain note) for an open trade. Reads at or before now only (a replay's past clock sees none)."""
    rows = [(t, s) for t, s in _READS.get(symbol.upper(), ()) if t <= now_ts]
    if not rows:
        return False, "no Most active read of this stock yet"
    who = "buyers" if direction == "LONG" else "sellers"
    before = [r for r in rows if r[0] < entry_ts]
    since = [r for r in rows if r[0] >= entry_ts]
    if not since:
        return False, f"no Most active read since the entry yet ({who} exit at {level:g}%)"
    last_t, last_s = since[-1]
    if now_ts - last_t > MAX_AGE_SEC:
        return False, f"last Most active read is {int((now_ts - last_t) // 60)} min old"
    looked = ([before[-1]] if before else []) + since
    peak = max(side_share(s, direction) for _, s in looked)
    now_share = side_share(last_s, direction)
    if peak <= level:
        return False, f"{who} {now_share:g}% — never above {level:g}% since the entry, fade exit not armed"
    if now_share <= level:
        at = dt.datetime.fromtimestamp(last_t, IST).strftime("%H:%M")
        return True, f"{who} faded to {now_share:g}% at {at} (was {peak:g}%, exit at {level:g}% or below)"
    return False, f"{who} {now_share:g}% (exit at {level:g}% or below)"


MAX_SERIES_DAYS = 3


def _point(at: int, share: float, rvol: float | None, value_cr: float | None) -> dict:
    point = {"time": at, "buy_pct": round(share * 100.0, 1)}
    if rvol is not None:
        point["rvol"] = round(rvol, 2)
    if value_cr is not None:
        point["value_cr"] = round(value_cr, 2)
    return point


def series(symbol: str, start_ts: float, end_ts: float) -> list[dict]:
    """The stock's buyers' share between two times for the chart's buyers / sellers strip: the day's saved Most active
    scans (scan_history.active_reads, so past days and replays have it too) joined with the live reads noted here,
    oldest first, one point per read time. Display only.
    [{"time": epoch, "buy_pct": 0..100, "rvol": volume vs usual × (when read), "value_cr": ₹ cr traded (when read)}]"""
    symbol = (symbol or "").upper()
    if not symbol or end_ts < start_ts:
        return []
    found: dict[int, tuple[float, float | None, float | None]] = {}
    last = dt.datetime.fromtimestamp(end_ts, IST).date()
    # A long range reads only its last few days of saved scans.
    day = max(dt.datetime.fromtimestamp(start_ts, IST).date(), last - dt.timedelta(days=MAX_SERIES_DAYS - 1))
    days = 0
    try:
        import scan_history

        while day <= last and days < MAX_SERIES_DAYS:
            for at, share, rvol, value in scan_history.active_reads(day.isoformat()).get(symbol, ()):
                found[int(at)] = (share, rvol, value)
            day += dt.timedelta(days=1)
            days += 1
    except Exception:  # noqa: BLE001
        pass
    extra = _EXTRA.get(symbol, {})
    for at, share in _READS.get(symbol, ()):
        rvol, value = extra.get(int(at), (None, None))
        found[int(at)] = (share, rvol, value)
    return [_point(t, *v) for t, v in sorted(found.items()) if start_ts <= t <= end_ts]


def read_before(symbol: str, at_ts: float) -> dict | None:
    """The latest Most active read of the stock at or before `at_ts` on that IST day (a backtest trade's entry), as a
    `series` point, or None. Display only."""
    points = series(symbol, dt.datetime.combine(dt.datetime.fromtimestamp(at_ts, IST).date(), dt.time(0, 0),
                                                tzinfo=IST).timestamp(), at_ts)
    return points[-1] if points else None


def reset() -> None:
    _READS.clear()
    _EXTRA.clear()
