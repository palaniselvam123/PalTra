"""09:30 morning checklist (morning_scan.py): is this stock a real morning runner or sideways chop?

Read-only, no order path: the Scalp page's "09:30 Morning" tab. At 09:30 IST a stock has
only three closed 5-minute candles today (09:15, 09:20, 09:25), too few for the Trend
quality checks (12 candles, sloping slow SMA). This reads five things that can be judged
on those three candles, each for a long and for a short:

    orb      Opening range break: the last closed 1-minute close at or after 09:30 is above
             the 09:15-09:30 high (long) or below its low (short). Inside the range = sideways.
    rvol     Relative volume: shares traded 09:15-09:30 today at least RVOL_MIN times the
             average for the same 15 minutes on up to LOOKBACK_SESSIONS earlier sessions
             (Groww's running session total at the 09:29 candle). Needs MIN_SESSIONS of them.
    vwap     VWAP purity: all three candles on one side of today's VWAP. Each candle closes
             beyond the VWAP at its close; the 09:20 and 09:25 candles also open beyond the
             VWAP as it stood when they opened, and no 1-minute wick inside them (low for a
             long, high for a short) touches the VWAP line as it stood at that minute. The
             09:15 candle is judged on its close only: the VWAP starts inside it.
    gap      SMA gap: (SMA fast - SMA slow) / SMA slow x 100 on the last closed 5-minute
             candle at least +GAP_MIN_PCT (long) or at most -GAP_MIN_PCT (short). The SMAs
             run on the earlier sessions' candles too, so they are formed at the open.
    candles  Candle bias: at least 2 of the 3 candles green (long) / red (short), the 09:25
             candle closes beyond the 09:15 candle's close and beyond the SMA fast.

`score` is the better side's count of five; RUNNER_MIN (4) or more is a runner, worth
arming; less stays in the chop zone. Before 09:30 the window is not complete and the row
is `ready = False`. Information only: nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

import pandas as pd

from candles import resample
from cross_scan import CALL_GAP_SEC, CONCURRENCY, GIVE_UP_AFTER, cache_key, clean_symbols
from groww_client import IST, market_is_open
from indicators import enrich, session_vwap_series, sma_gap_pct

CANDLE_MIN = 5
WINDOW_START = dt.time(9, 15)
WINDOW_END = dt.time(9, 30)
WINDOW_LAST_MINUTE = dt.time(9, 29)
RVOL_MIN = 1.8
GAP_MIN_PCT = 0.10
LOOKBACK_SESSIONS = 10
MIN_SESSIONS = 2
RUNNER_MIN = 4
CHECKS = ("orb", "rvol", "vwap", "gap", "candles")
# Calendar days of 1-minute candles to download: the long one gives the earlier sessions'
# opening volume (about ten sessions, kept for the day), the short one the SMA warm-up.
LONG_DAYS = 15
SHORT_DAYS = 4

log = logging.getLogger("sma.morningscan")

LONG = "LONG"
SHORT = "SHORT"

# (symbol, IST date) -> opening volumes of earlier sessions (newest first). Filled by the first
# scan of the day, so later scans download only SHORT_DAYS. Lives in the process that reads.
_BASELINE: dict[tuple[str, dt.date], list[float]] = {}


@dataclass
class MorningRow:
    symbol: str
    ready: bool  # the 09:15-09:30 window has closed
    side: str | None  # LONG / SHORT: the side with more checks passed
    score: int  # checks passed on that side, 0-5
    runner: bool  # score >= RUNNER_MIN
    checks: dict = field(default_factory=dict)  # check -> bool, for `side`
    notes: dict = field(default_factory=dict)  # check -> one line on what was read
    long_score: int = 0
    short_score: int = 0
    or_high: float | None = None
    or_low: float | None = None
    price: float | None = None  # last closed 1-minute close used for the break
    price_ts: int | None = None
    rvol: float | None = None
    rvol_sessions: int = 0
    open_volume: float | None = None
    gap_pct: float | None = None
    gap_ts: int | None = None
    vwap: float | None = None
    greens: int = 0
    reds: int = 0
    ltp: float | None = None
    day_move_pct: float | None = None

    def as_dict(self) -> dict:
        out = asdict(self)
        for key in ("rvol", "gap_pct", "day_move_pct"):
            if out[key] is not None:
                out[key] = round(out[key], 4)
        return out


def _ist(ts) -> pd.Series:
    return pd.to_datetime(pd.Series(ts).astype("int64"), unit="s", utc=True).dt.tz_convert(IST)


def _today(now: dt.datetime) -> dt.date:
    return (now if now.tzinfo else now.replace(tzinfo=IST)).astimezone(IST).date()


def download_start(symbol: str, now: dt.datetime) -> dt.datetime:
    """Where this stock's download starts: SHORT_DAYS once today's baseline is known, else LONG_DAYS."""
    days = SHORT_DAYS if (symbol, _today(now)) in _BASELINE else LONG_DAYS
    return (now - dt.timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)


def opening_volumes(frame_1m: pd.DataFrame, before: dt.date) -> list[float]:
    """Each session's shares traded 09:15-09:30 (running total at the 09:29 candle), newest first."""
    if frame_1m is None or len(frame_1m) == 0 or "volume" not in frame_1m.columns:
        return []
    df = frame_1m.sort_values("ts")
    local = _ist(df["ts"].to_numpy())
    out: list[tuple[dt.date, float]] = []
    vol = pd.to_numeric(df["volume"], errors="coerce").to_numpy()
    days = local.dt.date.to_numpy()
    times = local.dt.time.to_numpy()
    for day in sorted({d for d in days if d < before}, reverse=True):
        mask = (days == day) & (times == WINDOW_LAST_MINUTE)
        vals = vol[mask]
        if len(vals) and pd.notna(vals[-1]) and float(vals[-1]) > 0:
            out.append((day, float(vals[-1])))
    return [v for _, v in out[:LOOKBACK_SESSIONS]]


def _baseline(symbol: str, frame_1m: pd.DataFrame, today: dt.date) -> list[float]:
    for key in [k for k in _BASELINE if k[1] != today]:
        _BASELINE.pop(key, None)
    key = (symbol, today)
    if key not in _BASELINE:
        _BASELINE[key] = opening_volumes(frame_1m, today)
    return _BASELINE[key]


def _vwap_at(vwap: pd.Series, ts: pd.Series, before: int) -> float | None:
    """VWAP after the last 1-minute candle that started before `before` (epoch seconds)."""
    picked = vwap[ts < before].dropna()
    return float(picked.iloc[-1]) if len(picked) else None


def _beyond(a: float | None, b: float | None, sign: int) -> bool:
    return a is not None and b is not None and (a - b) * sign > 0


def _clock(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def read(
    frame_1m: pd.DataFrame,
    sma_fast: int,
    sma_slow: int,
    now: dt.datetime,
    symbol: str = "",
    baseline: list[float] | None = None,
) -> MorningRow | None:
    """The five checks for one stock. None without any candle today."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    now = now if now.tzinfo else now.replace(tzinfo=IST)
    now_ts = int(now.timestamp())
    today = _today(now)
    df = frame_1m.sort_values("ts").reset_index(drop=True)
    df = df[df["ts"].astype("int64") + 60 <= now_ts].reset_index(drop=True)  # closed 1-minute candles
    if df.empty:
        return None
    local = _ist(df["ts"].to_numpy())
    is_today = (local.dt.date == today).to_numpy()
    tape = df[is_today].reset_index(drop=True)
    if tape.empty:
        return None
    tape_local = _ist(tape["ts"].to_numpy())
    ltp = float(tape["close"].iloc[-1])
    first_open = float(tape["open"].iloc[0]) if "open" in tape.columns else 0.0
    row = MorningRow(
        symbol=symbol,
        ready=False,
        side=None,
        score=0,
        runner=False,
        ltp=round(ltp, 2),
        day_move_pct=(ltp / first_open - 1) * 100 if first_open > 0 else None,
    )
    # The three 5-minute candles of the window, from today's 1-minute tape.
    bars = resample(tape, CANDLE_MIN)
    bars = bars[bars["ts"].astype("int64") + CANDLE_MIN * 60 <= now_ts].reset_index(drop=True)
    bar_time = _ist(bars["ts"].to_numpy()).dt.time
    window = bars[((bar_time >= WINDOW_START) & (bar_time < WINDOW_END)).to_numpy()].reset_index(drop=True)
    if len(window) < 3:
        row.notes = {name: "Waiting for 09:30: the three opening 5-minute candles are not closed yet" for name in CHECKS}
        return row
    row.ready = True
    or_high, or_low = float(window["high"].max()), float(window["low"].min())
    row.or_high, row.or_low = round(or_high, 2), round(or_low, 2)

    # 1. Opening range break on the last closed 1-minute close at or after 09:30.
    after = tape[(tape_local.dt.time >= WINDOW_END).to_numpy()]
    price = float(after["close"].iloc[-1]) if len(after) else None
    if price is not None:
        row.price, row.price_ts = round(price, 2), int(after["ts"].iloc[-1])

    # 2. Relative volume of the opening 15 minutes.
    open_vol = None
    last_min = tape[(tape_local.dt.time == WINDOW_LAST_MINUTE).to_numpy()]
    if len(last_min) and "volume" in tape.columns:
        v = pd.to_numeric(last_min["volume"], errors="coerce").iloc[-1]
        open_vol = float(v) if pd.notna(v) and float(v) > 0 else None
    earlier = baseline if baseline is not None else opening_volumes(df, today)
    row.open_volume = open_vol
    row.rvol_sessions = len(earlier)
    usual = sum(earlier) / len(earlier) if earlier else None
    if open_vol is not None and usual:
        row.rvol = open_vol / usual
    rvol_ok = row.rvol is not None and row.rvol_sessions >= MIN_SESSIONS and row.rvol >= RVOL_MIN

    # 3. VWAP purity on the three candles.
    vwap = session_vwap_series(tape)
    tape_ts = tape["ts"].astype("int64")
    ends = [int(t) + CANDLE_MIN * 60 for t in window["ts"]]
    starts = [int(t) for t in window["ts"]]
    vwap_end = [_vwap_at(vwap, tape_ts, e) for e in ends]
    vwap_start = [None] + [_vwap_at(vwap, tape_ts, s) for s in starts[1:]]
    row.vwap = round(vwap_end[-1], 2) if vwap_end[-1] is not None else None
    minutes_in = [
        [
            (int(t), float(lo), float(hi))
            for t, lo, hi in zip(tape["ts"], tape["low"], tape["high"])
            if s <= int(t) < e
        ]
        for s, e in zip(starts, ends)
    ]

    # 4. SMA gap on the last closed 5-minute candle (earlier sessions warm the SMAs up).
    all_bars = resample(df, CANDLE_MIN)
    all_bars = all_bars[all_bars["ts"].astype("int64") + CANDLE_MIN * 60 <= now_ts].reset_index(drop=True)
    enriched = enrich(all_bars.drop(columns=["volume"], errors="ignore"), sma_fast, sma_slow)
    gap = None
    fast_at_c3 = None
    if len(enriched):
        last = enriched.iloc[-1]
        gap = sma_gap_pct(last.get("sma_9"), last.get("sma_21"))
        row.gap_ts = int(last["ts"])
        c3 = enriched[enriched["ts"].astype("int64") == int(window["ts"].iloc[2])]
        if len(c3) and pd.notna(c3["sma_9"].iloc[0]):
            fast_at_c3 = float(c3["sma_9"].iloc[0])
    row.gap_pct = gap

    # 5. Candle colours.
    opens = [float(x) for x in window["open"]]
    closes = [float(x) for x in window["close"]]
    row.greens = sum(c > o for o, c in zip(opens, closes))
    row.reds = sum(c < o for o, c in zip(opens, closes))

    def judge(direction: str) -> tuple[dict, dict]:
        sign = 1 if direction == LONG else -1
        up = direction == LONG
        word = "above" if up else "below"
        checks: dict[str, bool] = {}
        notes: dict[str, str] = {}
        level = or_high if up else or_low
        name = "high" if up else "low"
        if price is None:
            checks["orb"] = False
            notes["orb"] = "Waiting for a 1-minute candle to close after 09:30"
        else:
            checks["orb"] = _beyond(price, level, sign)
            notes["orb"] = (
                f"Close {price:.2f} at {_clock(row.price_ts)} {'is' if checks['orb'] else 'is not'} {word} the 09:15-09:30 {name} {level:.2f}"
                + ("" if checks["orb"] else " (inside the opening range)")
            )
        checks["rvol"] = rvol_ok
        if row.rvol_sessions < MIN_SESSIONS:
            notes["rvol"] = f"Opening volume: only {row.rvol_sessions} earlier session(s) to compare (needs {MIN_SESSIONS})"
        elif row.rvol is None:
            notes["rvol"] = "Opening volume not reported by Groww"
        else:
            notes["rvol"] = (
                f"Opening 15-min volume {row.rvol:.2f}× the {row.rvol_sessions}-session average (needs {RVOL_MIN:g}×)"
            )
        # VWAP purity
        bad: str | None = None
        for i in range(3):
            label = _clock(starts[i])
            if vwap_end[i] is None:
                bad = f"VWAP not ready at the {label} candle"
                break
            if not _beyond(closes[i], vwap_end[i], sign):
                bad = f"the {label} candle closed {closes[i]:.2f}, not {word} VWAP {vwap_end[i]:.2f}"
                break
            if i == 0:
                continue
            ref = vwap_start[i]
            if ref is None:
                bad = f"VWAP not ready at the {label} candle"
                break
            if not _beyond(opens[i], ref, sign):
                bad = f"the {label} candle opened {opens[i]:.2f}, not {word} VWAP {ref:.2f}"
                break
            # Each minute's wick against the VWAP line as it stood when that minute traded.
            for t, low, high in minutes_in[i]:
                line = _vwap_at(vwap, tape_ts, t)
                wick = low if up else high
                if line is not None and not _beyond(wick, line, sign):
                    bad = f"the {label} candle's {'low' if up else 'high'} {wick:.2f} at {_clock(t)} touched VWAP {line:.2f}"
                    break
            if bad:
                break
        checks["vwap"] = bad is None
        notes["vwap"] = f"All 3 candles {word} VWAP, no wick touched it" if bad is None else f"VWAP: {bad}"
        # SMA gap
        if gap is None:
            checks["gap"] = False
            notes["gap"] = "SMA gap not ready"
        else:
            checks["gap"] = gap * sign >= GAP_MIN_PCT
            notes["gap"] = (
                f"SMA {sma_fast}/{sma_slow} gap {gap:+.3f}% on the {_clock(row.gap_ts)} candle "
                f"(needs {'≥ +' if up else '≤ −'}{GAP_MIN_PCT:.2f}%)"
            )
        # Candle bias
        same = row.greens if up else row.reds
        colour = "green" if up else "red"
        later = _beyond(closes[2], closes[0], sign)
        past_fast = _beyond(closes[2], fast_at_c3, sign)
        checks["candles"] = same >= 2 and later and past_fast
        parts = [f"{same} of 3 {colour}"]
        parts.append(f"09:25 close {closes[2]:.2f} {'beyond' if later else 'not beyond'} 09:15 close {closes[0]:.2f}")
        parts.append(
            f"{'beyond' if past_fast else 'not beyond'} SMA {sma_fast} {fast_at_c3:.2f}" if fast_at_c3 is not None else "SMA fast not ready"
        )
        notes["candles"] = " · ".join(parts) + " (needs 2 of 3, a later close and past the fast SMA)"
        return checks, notes

    long_checks, long_notes = judge(LONG)
    short_checks, short_notes = judge(SHORT)
    row.long_score = sum(long_checks.values())
    row.short_score = sum(short_checks.values())
    if row.long_score != row.short_score:
        side = LONG if row.long_score > row.short_score else SHORT
    elif gap:
        side = LONG if gap > 0 else SHORT
    else:
        side = LONG if (price if price is not None else ltp) >= (or_high + or_low) / 2 else SHORT
    row.side = side
    row.checks, row.notes = (long_checks, long_notes) if side == LONG else (short_checks, short_notes)
    row.score = row.long_score if side == LONG else row.short_score
    row.runner = row.score >= RUNNER_MIN
    return row


def analyze(frame_1m: pd.DataFrame, sma_fast: int, sma_slow: int, now: dt.datetime, symbol: str = "") -> MorningRow | None:
    """`read` with the earlier sessions' opening volume kept for the rest of the day (see download_start)."""
    if frame_1m is None or getattr(frame_1m, "empty", True):
        return None
    base = _baseline(symbol, frame_1m, _today(now)) if symbol else None
    return read(frame_1m, sma_fast, sma_slow, now, symbol=symbol, baseline=base)


# ---- scanner lifecycle (mirrors chop_scan.ChopScanner) ---------------------------------------

MIN_RESCAN_SEC = 30  # "Scan now" at most twice a minute
Fetch = Callable[[str, dt.datetime, dt.datetime], Awaitable[pd.DataFrame]]
# One stock's whole read (download + checks), e.g. in the scanner process (scan_worker.py).
Job = Callable[[str, dt.datetime, dt.datetime], Awaitable["MorningRow | None"]]


def sort_key(row: MorningRow) -> tuple:
    """Runners first, then the most checks passed, then the strongest opening volume."""
    return (not row.ready, not row.runner, -row.score, -(row.rvol or 0.0), row.symbol)


@dataclass
class Status:
    running: bool = False
    done: int = 0
    total: int = 0
    failed: int = 0
    error: str | None = None
    sma_fast: int = 9
    sma_slow: int = 21
    as_of: str | None = None
    finished_at: str | None = None
    market_open: bool = False
    rows: list[dict] = field(default_factory=list)


class MorningScanner:
    """One pass at a time; the last finished result is kept until the next minute."""

    def __init__(self) -> None:
        self.status = Status()
        self._task: asyncio.Task | None = None
        self._key: tuple | None = None
        self._started_at = 0.0

    def stop(self, reason: str) -> None:
        """Cancel a running pass (the Scalp page's Pause scans). The last finished rows stay."""
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
            "minutes": CANDLE_MIN,
            "sma_fast": s.sma_fast,
            "sma_slow": s.sma_slow,
            "as_of": s.as_of,
            "finished_at": s.finished_at,
            "market_open": s.market_open,
            "rules": {"rvol_min": RVOL_MIN, "gap_min_pct": GAP_MIN_PCT, "runner_min": RUNNER_MIN, "sessions": LOOKBACK_SESSIONS},
            "rows": s.rows,
        }

    def start(
        self,
        symbols: list[str],
        fetch: Fetch,
        *,
        sma_fast: int = 9,
        sma_slow: int = 21,
        force: bool = False,
        now: dt.datetime | None = None,
        job: Job | None = None,
    ) -> dict:
        names = clean_symbols(symbols)
        if not names:
            raise ValueError("No stocks to scan")
        now = now or dt.datetime.now(IST)
        key = (cache_key(now, 1), sma_fast, sma_slow, tuple(names))
        if self.status.running:
            return self.snapshot()
        if self._key == key and self.status.as_of is not None and not force:
            return self.snapshot()
        if time.monotonic() - self._started_at < MIN_RESCAN_SEC and self._key is not None:
            return self.snapshot()
        self._started_at = time.monotonic()
        s = self.status
        s.running, s.done, s.total, s.failed, s.error = True, 0, len(names), 0, None
        s.sma_fast, s.sma_slow = sma_fast, sma_slow
        self._task = asyncio.get_running_loop().create_task(self._run(names, fetch, key, sma_fast, sma_slow, now, job))
        return self.snapshot()

    async def _run(self, names, fetch: Fetch, key: tuple, sma_fast: int, sma_slow: int, now: dt.datetime, job: Job | None) -> None:
        end = now.replace(tzinfo=None)
        rows: list[MorningRow] = []
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
                        # The worker picks its own start (it keeps the day's volume baseline).
                        row = await job(symbol, download_start(symbol, now), end)
                    else:
                        frame = await fetch(symbol, download_start(symbol, now), end)
                        row = analyze(frame, sma_fast, sma_slow, now, symbol=symbol)
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
            log.exception("morning scan failed")
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
