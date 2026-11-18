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
closed by it. The day's saved Most active scans count too (`_rows`): a desk whose scan runs on another server, and a
replay of a day with saved scans, judge the same reads the chart's buyers / sellers strip draws.
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


def _saved(symbol: str, day: dt.date) -> list[tuple[float, float]]:
    try:
        import scan_history

        return list(scan_history.active_shares(day.isoformat()).get(symbol, ()))
    except Exception:  # noqa: BLE001
        return []


def _rows(symbol: str, now_ts: float) -> list[tuple[float, float]]:
    """The stock's reads up to `now_ts` on that IST day: the day's saved Most active scans (this server's own and the
    copies other servers send, so a replay of a saved day and a desk whose scan runs elsewhere see them too) joined
    with the live reads noted here (same read time = one read), oldest first."""
    symbol = (symbol or "").upper()
    day = dt.datetime.fromtimestamp(now_ts, IST).date()
    merged = {int(t): s for t, s in _saved(symbol, day) if t <= now_ts}
    for t, s in _READS.get(symbol, ()):
        if t <= now_ts and dt.datetime.fromtimestamp(t, IST).date() == day:
            merged[int(t)] = s
    return sorted((float(t), s) for t, s in merged.items())


def side_share(share: float, direction: str) -> float:
    """The trade's own side's share in % (buyers for a long, sellers for a short)."""
    return round((share if direction == "LONG" else 1.0 - share) * 100.0, 1)


def judge(symbol: str, direction: str, entry_ts: float, now_ts: float, level: float) -> tuple[bool, str]:
    """(close?, plain note) for an open trade. Reads at or before now only (a replay's past clock sees none)."""
    rows = _rows(symbol, now_ts)
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


def over_counter(cfg) -> bool:
    """`pressure_over_counter`: the buyers / sellers reading overrules the counter-candle exit (needs the fade exit)."""
    return on(cfg) and bool(getattr(cfg, "pressure_over_counter", False))


def holds(symbol: str, direction: str, entry_ts: float, now_ts: float, level: float) -> tuple[bool, str]:
    """(hold the counter-candle exit?, plain note). Held only while a Most active read taken since the entry, at most
    MAX_AGE_SEC old, still shows the trade's own side above the level; with no such read the counter-candle exit works
    as usual, so a paused scan never leaves a trade without it. At or below the level the fade exit closes it."""
    rows = [(t, s) for t, s in _rows(symbol, now_ts) if t >= entry_ts]
    if not rows:
        return False, "no Most active read since the entry"
    last_t, last_s = rows[-1]
    if now_ts - last_t > MAX_AGE_SEC:
        return False, f"last Most active read is {int((now_ts - last_t) // 60)} min old"
    who = "buyers" if direction == "LONG" else "sellers"
    share = side_share(last_s, direction)
    if share <= level:
        return False, f"{who} {share:g}% (at or below {level:g}%)"
    at = dt.datetime.fromtimestamp(last_t, IST).strftime("%H:%M")
    return True, f"{who} still {share:g}% at {at} (above {level:g}%)"


# --- Entries for Most active stocks (`pressure_entry`, off by default; `pressure_entry_pct` 65, 30-95) -------------------
# Owner's case: a stock armed while buyers (sellers) held most of the waiting orders was only traded on the bot's next
# signal, by when the demand had gone. CHECK: every entry of such a stock needs the order's own side (buyers for a buy,
# sellers for a sell) still at least that % in a fresh read. AT_ARM: the same check, and the stock is also entered at
# once on the side that holds it (once a day per stock, `StrategyEngine._pressure_entry_pass`).
ENTRY_MODES = ("OFF", "CHECK", "AT_ARM")
ENTRY_REASON = "MOST_ACTIVE"
DEFAULT_ENTRY_PCT = 65.0


def entry_mode(cfg) -> str:
    value = str(getattr(cfg, "pressure_entry", None) or "OFF").upper()
    return value if value in ENTRY_MODES else "OFF"


def entry_pct(cfg) -> float:
    value = getattr(cfg, "pressure_entry_pct", None)
    try:
        value = float(DEFAULT_ENTRY_PCT if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_ENTRY_PCT
    return min(MAX_PCT, max(MIN_PCT, value))


def fresh_read(symbol: str, now_ts: float) -> tuple[float, float] | None:
    """(read time, buyers' share 0..1) of the newest read at most MAX_AGE_SEC old, else None."""
    rows = _rows(symbol, now_ts)
    if not rows or now_ts - rows[-1][0] > MAX_AGE_SEC:
        return None
    return rows[-1]


def entry_refusal(symbol: str, direction: str, now_ts: float, level: float) -> str | None:
    """None when the order's own side still holds at least `level` %, else the plain reason for the refusal."""
    read = fresh_read(symbol, now_ts)
    if read is None:
        return f"no Most active read in the last {MAX_AGE_SEC // 60} min (Most active entry check)"
    who = "buyers" if direction == "LONG" else "sellers"
    share = side_share(read[1], direction)
    if share < level:
        at = dt.datetime.fromtimestamp(read[0], IST).strftime("%H:%M")
        return f"{who} {share:g}% at {at}, under {level:g}% (Most active entry check)"
    return None


def entry_side(symbol: str, now_ts: float, level: float) -> tuple[str, float, str] | None:
    """(LONG / SHORT, that side's %, HH:MM of the read) when buyers or sellers hold at least `level` % now, else None."""
    read = fresh_read(symbol, now_ts)
    if read is None:
        return None
    at = dt.datetime.fromtimestamp(read[0], IST).strftime("%H:%M")
    for side in ("LONG", "SHORT"):
        share = side_share(read[1], side)
        if share >= level:
            return side, share, at
    return None


# --- Buyers / sellers overrule the candle colour (`pressure_flip`, off by default; `pressure_flip_pct` 65, 55-95) --------
# Owner's case (SOLARINDS, 11 Nov): a candle-colour bot bought a Most active stock on a green candle while sellers held 71 %
# of the waiting orders. With this on, for Most active stocks in candle-colour mode, a colour that points one way while
# the other side holds at least that % in a fresh read is turned to their side (`StrategyEngine._colour_minute`).
FLIP_REASON = "PRESSURE_FLIP"
DEFAULT_FLIP_PCT = 65.0
MIN_FLIP_PCT = 55.0


def flip_on(cfg) -> bool:
    return bool(getattr(cfg, "pressure_flip", False))


def flip_pct(cfg) -> float:
    value = getattr(cfg, "pressure_flip_pct", None)
    try:
        value = float(DEFAULT_FLIP_PCT if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULT_FLIP_PCT
    return min(MAX_PCT, max(MIN_FLIP_PCT, value))


def exit_flow_pct(cfg) -> float:
    """The executed-volume share the other side needs before the fade exit closes (trade_flow.py), 0 = not checked."""
    value = getattr(cfg, "pressure_exit_flow", None)
    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return 0.0 if value <= 0 else min(MAX_PCT, max(50.0, value))


def exit_flow_window(cfg) -> int:
    """Minutes of executed volume the fade exit's check reads (1-60), 0 = the exit candle just closed."""
    try:
        value = int(getattr(cfg, "pressure_exit_flow_window", 3) if getattr(cfg, "pressure_exit_flow_window", None) is not None else 3)
    except (TypeError, ValueError):
        value = 3
    return value if 0 <= value <= 60 else 3


def flip_flow_pct(cfg) -> float:
    """The executed-volume share the other side needs too (trade_flow.py), 0 = not checked."""
    value = getattr(cfg, "pressure_flip_flow", None)
    try:
        value = float(value or 0.0)
    except (TypeError, ValueError):
        value = 0.0
    return 0.0 if value <= 0 else min(MAX_PCT, max(MIN_PCT, value))


def flip_side(symbol: str, side: str, now_ts: float, level: float,
              close: float | None = None, vwap: float | None = None,
              flow: tuple[float | None, str] | None = None, flow_level: float = 0.0) -> tuple[str, str] | None:
    """(the other side, plain note) when the other side holds at least `level` % in a fresh read AND the price
    agrees: the last closed candle's close under today's VWAP to sell, over it to buy (owner's case: buyers read
    79 % while sellers were hitting the bids under the VWAP; the waiting orders alone are not proof). An unknown
    close or VWAP never flips. Else None: the colour decides."""
    read = fresh_read(symbol, now_ts)
    if read is None:
        return None
    other = "SHORT" if side == "LONG" else "LONG"
    share = side_share(read[1], other)
    if share < level:
        return None
    if close is None or vwap is None or not (close < vwap if other == "SHORT" else close > vwap):
        return None
    done = ""
    if flow_level > 0:
        # Executed volume too (trade_flow.py): the other side's share of the colour candle's estimated volume.
        # `flow` = (the LONG side's % of it, how it was read); unknown never flips.
        if flow is None or flow[0] is None:
            return None
        executed = flow[0] if other == "LONG" else round(100.0 - flow[0], 1)
        if executed < flow_level:
            return None
        done = f", {executed:g}% of the executed volume ({flow[1]})"
    at = dt.datetime.fromtimestamp(read[0], IST).strftime("%H:%M")
    who = "sellers" if other == "SHORT" else "buyers"
    where = "under" if other == "SHORT" else "over"
    return other, f"{who} {share:g}% (read {at}){done} and the price {where} VWAP {vwap:.2f} overrule the candle colour"


def reset() -> None:
    _READS.clear()
    _EXTRA.clear()
