"""52-week high (high52.py): a breakout entry check for the SMA bots and a scan for the Scalp page.

Groww's daily candles (`interval_in_minutes` 1440) give each stock's highest high and lowest low over the
last 52 weeks. Two things read them, both read-only; nothing here places an order:

**Entry check** (`use_high52`, off by default; per bot and per stock, PAPER and LIVE, replays too; entries only),
wired through `strategy_engine._entry_block` like the other checks (check name `high52`, chart ✕ "52w high"):

* `high52_side`: `LONG` (default) – a buy needs the closed signal candle to close at the 52-week high, a sell is
  refused (the bot only buys breakouts); `SHORT` – a sell needs a close at the 52-week low, a buy is refused;
  `BOTH` – each side judged on its own level.
* `high52_pct` (0 by default, 0-20): 0 = the close must be at or above the prior 52-week high (a fresh breakout);
  N = a close within N % under the high counts too (and within N % above the low for a sell).

The levels are the highest high / lowest low of the daily candles of the 52 weeks *before* the signal candle's
day (`levels_from`), so a breakout on the day itself counts. They are read once per stock and day: the live
engine asks for them in the background (`ensure`, on the bots' own Groww threads, `groww_pressure.run_bot_call`)
and a replay preloads them for its days (`preload`, one download per stock). Until they are loaded the check
refuses the entry ("52-week high not loaded yet"), like the other checks when a line is not ready.

**Scan** (`High52Scanner`, the Scalp page's "52-week high" tab, `POST /api/high52-scan/start`): every stock's
distance from its 52-week high and low, whether it broke the high (or low) today, the day's move and the
year's move, nearest the high first. Mirrors `MorningScanner` (one pass at a time, the scanner process via
`scan_worker.high52_job`, stopped by Pause scans).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from cross_scan import CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, cache_key, clean_symbols
from groww_client import IST, _is_auth_error, _parse_candles, market_is_open

log = logging.getLogger("sma.high52")

WEEKS = 52
LOOKBACK_DAYS = WEEKS * 7
SIDES = ("LONG", "SHORT", "BOTH")
DEFAULT_SIDE = "LONG"
MAX_PCT = 20.0
DAILY_MINUTES = 1440
_REQUEST_TIMEOUT_SEC = 20
# A failed download is tried again after this long, not on every tick.
RETRY_SEC = 300
CHECK = "high52"


# ---- settings ------------------------------------------------------------------------------


def on(cfg) -> bool:
    return bool(getattr(cfg, "use_high52", False))


def side_of(cfg) -> str:
    value = str(getattr(cfg, "high52_side", None) or DEFAULT_SIDE).upper()
    return value if value in SIDES else DEFAULT_SIDE


def pct_of(cfg) -> float:
    try:
        value = float(getattr(cfg, "high52_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return min(max(value, 0.0), MAX_PCT)


# ---- levels ---------------------------------------------------------------------------------


@dataclass
class Levels:
    symbol: str
    day: str  # the day the levels are for (ISO); candles before it only
    high: float
    high_date: str
    low: float
    low_date: str
    sessions: int
    first_close: float | None = None  # the window's first close (the year's move)
    prev_close: float | None = None  # the close before `day`

    def as_dict(self) -> dict:
        return asdict(self)


def _dates(frame: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(frame["ts"].astype("int64"), unit="s", utc=True).dt.tz_convert(IST).dt.date


def levels_from(daily: pd.DataFrame, day: dt.date, symbol: str = "") -> Levels | None:
    """The 52-week high / low of the daily candles before `day` (none = too few candles)."""
    if daily is None or getattr(daily, "empty", True):
        return None
    dates = _dates(daily)
    since = day - dt.timedelta(days=LOOKBACK_DAYS)
    keep = (dates >= since) & (dates < day)
    window = daily[keep.to_numpy()]
    if window.empty:
        return None
    wd = dates[keep.to_numpy()]
    hi = window["high"].astype(float)
    lo = window["low"].astype(float)
    hi_i = int(hi.to_numpy().argmax())
    lo_i = int(lo.to_numpy().argmin())
    closes = window["close"].astype(float)
    return Levels(
        symbol=symbol,
        day=day.isoformat(),
        high=float(hi.iloc[hi_i]),
        high_date=wd.iloc[hi_i].isoformat(),
        low=float(lo.iloc[lo_i]),
        low_date=wd.iloc[lo_i].isoformat(),
        sessions=int(len(window)),
        first_close=float(closes.iloc[0]),
        prev_close=float(closes.iloc[-1]),
    )


# (symbol, day) -> Levels, filled by `ensure` / `preload`; a replay fills its own days.
_CACHE: dict[tuple[str, dt.date], Levels] = {}
_INFLIGHT: set[tuple[str, dt.date]] = set()
_FAILED: dict[tuple[str, dt.date], float] = {}


def cached(symbol: str, day: dt.date) -> Levels | None:
    return _CACHE.get((symbol.upper(), day))


def remember(levels: Levels, day: dt.date) -> None:
    _CACHE[(levels.symbol.upper(), day)] = levels


def clear() -> None:
    _CACHE.clear()
    _INFLIGHT.clear()
    _FAILED.clear()


# ---- Groww daily candles ---------------------------------------------------------------------


def download_daily(sdk, symbol: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """Daily candles start..end (naive IST datetimes) from Groww, oldest first. One request per 400 days."""
    frames = []
    cursor = start
    while cursor < end:
        stop = min(cursor + dt.timedelta(days=400), end)
        raw = sdk.get_historical_candle_data(
            trading_symbol=symbol,
            exchange="NSE",
            segment="CASH",
            start_time=cursor.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=stop.strftime("%Y-%m-%d %H:%M:%S"),
            interval_in_minutes=DAILY_MINUTES,
            timeout=_REQUEST_TIMEOUT_SEC,
        )
        part = _parse_candles(raw, limit=None)
        if not part.empty:
            frames.append(part)
        cursor = stop
    if not frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    return pd.concat(frames).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


def window_start(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day - dt.timedelta(days=LOOKBACK_DAYS + 7), dt.time(0, 0))


async def fetch_daily(broker, symbol: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """Daily candles off the event loop, with the broker's saved session (the scans' in-process path)."""
    from candle_history import HistoryError

    if not broker.token:
        broker.adopt_saved_session()
    if not broker.token:
        raise HistoryError(getattr(broker, "no_login_text", "Past candles come from Groww. Log in to Groww on the desk Settings page first."))

    async def attempt() -> pd.DataFrame:
        sdk = broker._require_sdk()
        return await asyncio.wait_for(asyncio.to_thread(download_daily, sdk, symbol, start, end), timeout=60)

    try:
        return await attempt()
    except HistoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_auth_error(exc) and broker._recover_from_auth_failure():
            return await attempt()
        detail = str(exc).strip() or type(exc).__name__
        raise HistoryError(f"Groww did not send daily candles: {detail}"[:240]) from exc


async def preload(broker, symbol: str, days: list[dt.date]) -> bool:
    """One download per stock: the levels for every day in `days` (a replay's run). True when loaded."""
    symbol = symbol.upper()
    wanted = [d for d in days if (symbol, d) not in _CACHE]
    if not wanted:
        return True
    try:
        frame = await fetch_daily(broker, symbol, window_start(min(wanted)), dt.datetime.combine(max(wanted), dt.time(23, 59)))
    except Exception as exc:  # noqa: BLE001
        log.warning("52-week levels for %s: %s", symbol, exc)
        return False
    for d in wanted:
        found = levels_from(frame, d, symbol)
        if found is not None:
            remember(found, d)
    return True


async def _load_live(broker, symbol: str, day: dt.date) -> None:
    import groww_pressure

    key = (symbol, day)
    try:
        if not broker.token:
            broker.adopt_saved_session()
        if not broker.token:
            _FAILED[key] = time.monotonic()
            return
        sdk = broker._require_sdk()
        frame = await groww_pressure.run_bot_call(
            download_daily, sdk, symbol, window_start(day), dt.datetime.combine(day, dt.time(23, 59))
        )
        found = levels_from(frame, day, symbol)
        if found is None:
            _FAILED[key] = time.monotonic()
            return
        remember(found, day)
    except Exception as exc:  # noqa: BLE001
        log.warning("52-week levels for %s: %s", symbol, exc)
        _FAILED[key] = time.monotonic()
    finally:
        _INFLIGHT.discard(key)


def ensure(broker, symbols: list[str], day: dt.date) -> None:
    """Start a background download of today's levels for each stock that has none (the live engine's tick)."""
    if not symbols or getattr(broker, "_require_sdk", None) is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    for name in symbols:
        symbol = name.upper()
        key = (symbol, day)
        if key in _CACHE or key in _INFLIGHT:
            continue
        failed = _FAILED.get(key)
        if failed is not None and time.monotonic() - failed < RETRY_SEC:
            continue
        _INFLIGHT.add(key)
        loop.create_task(_load_live(broker, symbol, day))


# ---- entry check -----------------------------------------------------------------------------


def _signal_day(frame: pd.DataFrame) -> dt.date | None:
    closed = frame.iloc[:-1] if frame is not None else None
    if closed is None or getattr(closed, "empty", True) or "ts" not in closed.columns:
        return None
    return dt.datetime.fromtimestamp(int(closed["ts"].iloc[-1]), IST).date()


def _symbol_of(cfg, symbol: str | None) -> str:
    return (symbol or getattr(cfg, "symbol", None) or "").upper()


def entry_reason(frame: pd.DataFrame, direction: str, cfg, symbol: str | None = None) -> str | None:
    """Why the 52-week check refuses this entry, or None when the close is at the level."""
    side = side_of(cfg)
    if side == "LONG" and direction == "SHORT":
        return "52-week high check: sells are off (only breakouts above the 52-week high are bought)"
    if side == "SHORT" and direction == "LONG":
        return "52-week high check: buys are off (only breakdowns below the 52-week low are sold)"
    name = _symbol_of(cfg, symbol)
    day = _signal_day(frame)
    if not name or day is None:
        return "52-week high not loaded yet (needs Groww daily candles)"
    lv = cached(name, day)
    if lv is None:
        return "52-week high not loaded yet (needs Groww daily candles)"
    close = float(frame.iloc[:-1]["close"].iloc[-1])
    pct = pct_of(cfg)
    if direction == "LONG":
        floor = lv.high * (1 - pct / 100)
        if close < floor:
            under = (lv.high - close) / lv.high * 100
            need = "a close at or above it" if pct <= 0 else f"within {pct:g}% of it"
            return f"52-week high: {close:.2f} is {under:.2f}% under the 52-week high {lv.high:.2f} ({lv.high_date}), needs {need}"
        return None
    cap = lv.low * (1 + pct / 100)
    if close > cap:
        over = (close - lv.low) / lv.low * 100
        need = "a close at or below it" if pct <= 0 else f"within {pct:g}% of it"
        return f"52-week low: {close:.2f} is {over:.2f}% above the 52-week low {lv.low:.2f} ({lv.low_date}), needs {need}"
    return None


def passed_text(frame: pd.DataFrame, direction: str, cfg, symbol: str | None = None) -> str:
    name = _symbol_of(cfg, symbol)
    day = _signal_day(frame)
    lv = cached(name, day) if name and day else None
    if lv is None:
        return "52-week high"
    close = float(frame.iloc[:-1]["close"].iloc[-1])
    if direction == "LONG":
        return f"52-week high: {close:.2f} at the 52-week high {lv.high:.2f} ({lv.high_date})"
    return f"52-week low: {close:.2f} at the 52-week low {lv.low:.2f} ({lv.low_date})"


# ---- scan ------------------------------------------------------------------------------------


@dataclass
class High52Row:
    symbol: str
    ltp: float | None  # today's last daily close (the running candle while the market is open)
    high52: float | None
    high52_date: str | None
    low52: float | None
    low52_date: str | None
    dist_high_pct: float | None  # how far under the 52-week high, % (0 = at it, negative = above it today)
    dist_low_pct: float | None  # how far above the 52-week low, %
    broke_high: bool  # today's high reached the prior 52-week high
    broke_low: bool
    days_since_high: int | None  # trading sessions since the high
    day_move_pct: float | None  # today vs the previous close
    year_move_pct: float | None  # today vs the window's first close
    sessions: int
    ts: int | None  # the last daily candle's time

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("dist_high_pct", "dist_low_pct", "day_move_pct", "year_move_pct"):
            if out[key] is not None:
                out[key] = round(out[key], 3)
        return out


def analyze(daily: pd.DataFrame, now: dt.datetime, symbol: str = "") -> High52Row | None:
    """One stock's 52-week reading from its daily candles up to now."""
    if daily is None or getattr(daily, "empty", True):
        return None
    today = now.astimezone(IST).date() if now.tzinfo else now.date()
    dates = _dates(daily)
    last_date = dates.iloc[-1]
    # The levels are the 52 weeks before the last candle's day, so today's own candle is judged against them.
    lv = levels_from(daily, last_date, symbol)
    if lv is None:
        return None
    last = daily.iloc[-1]
    close = float(last["close"])
    high_today = float(last["high"])
    low_today = float(last["low"])
    prev = lv.prev_close
    after_high = dates[dates > dt.date.fromisoformat(lv.high_date)]
    return High52Row(
        symbol=symbol,
        ltp=close,
        high52=lv.high,
        high52_date=lv.high_date,
        low52=lv.low,
        low52_date=lv.low_date,
        dist_high_pct=(lv.high - close) / lv.high * 100 if lv.high else None,
        dist_low_pct=(close - lv.low) / lv.low * 100 if lv.low else None,
        broke_high=bool(high_today >= lv.high),
        broke_low=bool(low_today <= lv.low),
        days_since_high=int(len(after_high)),
        day_move_pct=(close - prev) / prev * 100 if prev else None,
        year_move_pct=(close - lv.first_close) / lv.first_close * 100 if lv.first_close else None,
        sessions=lv.sessions,
        ts=int(last["ts"]),
    )


def sort_key(row: High52Row) -> tuple:
    """Breakouts first, then nearest the 52-week high."""
    return (not row.broke_high, row.dist_high_pct if row.dist_high_pct is not None else 1e9, row.symbol)


MIN_RESCAN_SEC = 30
Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]
Job = Callable[[str, dt.datetime, dt.datetime], Awaitable["High52Row | None"]]


@dataclass
class Status:
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    as_of: str | None = None
    finished_at: str | None = None
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class High52Scanner:
    """One pass at a time; the last finished result is kept."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._key: tuple | None = None
        self._started_at = 0.0

    def stop(self, reason: str) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        if self.status.running:
            self.status.running = False
            self.status.error = reason
            self._key = None

    def snapshot(self) -> dict:
        s = self.status
        return {
            "running": s.running,
            "done": s.done,
            "total": s.total,
            "failed": s.failed,
            "error": s.error,
            "weeks": WEEKS,
            "as_of": s.as_of,
            "finished_at": s.finished_at,
            "market_open": s.market_open,
            "rows": s.rows,
        }

    def start(self, symbols: list[str], fetch: Fetch, *, force: bool = False, now: dt.datetime | None = None, job: Job | None = None) -> dict:
        names = clean_symbols(symbols)
        if not names:
            raise ValueError("No stocks to scan")
        now = now or dt.datetime.now(IST)
        key = (cache_key(now, 5), tuple(names))
        if self.status.running:
            return self.snapshot()
        if self._key == key and self.status.as_of is not None and not force:
            return self.snapshot()
        if time.monotonic() - self._started_at < MIN_RESCAN_SEC and self._key is not None:
            return self.snapshot()
        self._started_at = time.monotonic()
        s = self.status
        s.running, s.done, s.total, s.failed, s.error = True, 0, len(names), 0, None
        self._task = asyncio.get_running_loop().create_task(self._run(names, fetch, key, now, job))
        return self.snapshot()

    async def _run(self, names, fetch: Fetch, key: tuple, now: dt.datetime, job: Job | None) -> None:
        end = now.replace(tzinfo=None)
        start = window_start(now.date())
        rows: list[High52Row] = []
        errors: dict[str, int] = {}
        ok = 0
        gate = asyncio.Semaphore(CONCURRENCY)
        abort = asyncio.Event()

        async def one(symbol: str, delay: float) -> None:
            nonlocal ok
            await asyncio.sleep(delay)
            if abort.is_set():
                return
            async with gate:
                if abort.is_set():
                    return
                try:
                    if job is not None:
                        row = await job(symbol, start, end)
                    else:
                        row = analyze(await fetch(symbol, start, end), now, symbol=symbol)
                    ok += 1
                    if row is not None:
                        rows.append(row)
                except Exception as exc:  # noqa: BLE001
                    text = (str(exc).strip() or type(exc).__name__)[:200]
                    errors[text] = errors.get(text, 0) + 1
                    self.status.failed += 1
                    if ok == 0 and self.status.failed >= GIVE_UP_AFTER:
                        abort.set()
                finally:
                    self.status.done += 1

        try:
            await asyncio.gather(*(one(name, i * CALL_GAP_SEC) for i, name in enumerate(names)))
        except Exception as exc:  # noqa: BLE001
            log.exception("52-week scan failed")
            errors[str(exc)[:200] or type(exc).__name__] = 1
        rows.sort(key=sort_key)
        s = self.status
        s.as_of = now.isoformat(timespec="seconds")
        s.market_open = market_is_open(now)
        s.rows = [r.as_dict() for r in rows]
        if ok == 0 and errors:
            s.error = max(errors, key=errors.get)
            self._key = None
        else:
            s.error = None if not errors else f"{s.failed} stock(s) could not be read ({max(errors, key=errors.get)})"
            self._key = key
        s.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        s.running = False
