"""Strong-trend pick backtest (read-only, practice money; the Scalp page's Backtest tab, "Strong-trend picks").

The owner's daily routine, automated: open the Trend quality history at a time (10:00, 11:00…), keep the strong bullish /
bearish stocks, take the top 10-20 and backtest them. Here, for each past day of a replay range, at `pick_time` every
stock in the universe is read exactly as the Trend quality card would have read it then (`chop_scan.analyze` on the
1-minute candles closed before that minute, on `minutes` candles with the main desk's SMA periods), the strong-trend ones
in `direction` are ranked (trend score, then the move since the open, then the SMA gap) and the top `top_n` become that
day's picks. `replay.ReplaySession` (the Scalp-pick mode, `rule`) then trades only those with the picked bot's settings
from the pick minute to the square-off: nothing known after the pick time is used.

**Daily automatic run** (`AutoSettings`, `trend_picks_auto.json` next to the SMA DB, `TREND_PICKS_AUTO_FILE`, off by
default): after `run_at` (15:45) on each weekday, `run` starts that day's backtest of the picks by itself (once per day,
`last_day`), on the chosen bot's replay player when it is free, at full speed; the result lands in Backtests like any run.

Nothing here places an order or changes a bot's settings or Trade list.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import pandas as pd

log = logging.getLogger("sma.trend_picks")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KIND = "TREND"
DIRECTIONS = ("BULLISH", "BEARISH", "BOTH")
SIZES = (1, 3, 5, 10, 15)
MAX_TOP_N = 20
MAX_UNIVERSE = 250
CHECK_SEC = 60.0


@dataclass
class TrendPickRule:
    pick_time: dt.time = dt.time(10, 0)
    top_n: int = 10
    direction: str = "BOTH"
    minutes: int = 5
    sma_fast: int = 9
    sma_slow: int = 21
    kind: str = KIND

    def as_dict(self) -> dict:
        return {
            "kind": KIND,
            "pick_time": self.pick_time.strftime("%H:%M"),
            "top_n": self.top_n,
            "direction": self.direction,
            "minutes": self.minutes,
            "sma_fast": self.sma_fast,
            "sma_slow": self.sma_slow,
        }


def _known_at(frame: pd.DataFrame, day: dt.date, at: dt.time) -> pd.DataFrame:
    """The 1-minute candles that had closed before `at` on `day` (and the earlier days the SMAs warm up on)."""
    cut = int(dt.datetime.combine(day, at, tzinfo=IST).timestamp())
    return frame[frame["ts"].astype("int64") < cut].reset_index(drop=True)


def read_at(frame: pd.DataFrame, day: dt.date, rule: TrendPickRule, symbol: str = "") -> dict | None:
    """One stock's Trend quality read as it stood at the pick time, or None (no candles that day / too few)."""
    from chop_scan import analyze

    part = _known_at(frame, day, rule.pick_time)
    if part.empty:
        return None
    last_day = pd.to_datetime(part["ts"].iloc[-1], unit="s", utc=True).tz_convert("Asia/Kolkata").date()
    if last_day != day:
        return None
    now = dt.datetime.combine(day, rule.pick_time, tzinfo=IST)
    row = analyze(part, rule.sma_fast, rule.sma_slow, rule.minutes, now, symbol=symbol)
    return None if row is None else row.as_dict()


def _wanted(trend: str | None, direction: str) -> bool:
    if trend not in ("STRONG_BULLISH", "STRONG_BEARISH"):
        return False
    if direction == "BULLISH":
        return trend == "STRONG_BULLISH"
    if direction == "BEARISH":
        return trend == "STRONG_BEARISH"
    return True


def picks_for_day(frames: dict[str, pd.DataFrame], day: dt.date, rule: TrendPickRule) -> list[dict]:
    """The day's strong-trend picks at the pick time, best first."""
    rows = []
    for symbol, frame in frames.items():
        try:
            row = read_at(frame, day, rule, symbol=symbol)
        except Exception:  # noqa: BLE001 - one bad tape never stops the day's pick
            log.exception("trend pick read failed for %s", symbol)
            continue
        if row is None or not _wanted(row.get("trend"), rule.direction):
            continue
        rows.append(row)

    def key(r: dict):
        move = abs(float(r.get("day_move_pct") or 0.0))
        gap = abs(float(r.get("sma_gap_pct") or 0.0))
        return (-int(r.get("trend_score") or 0), -move, -gap, r.get("symbol") or "")

    rows.sort(key=key)
    return [
        {
            "symbol": r["symbol"],
            "trend": r.get("trend"),
            "side": "LONG" if r.get("trend") == "STRONG_BULLISH" else "SHORT",
            "trend_score": r.get("trend_score"),
            "day_move_pct": round(float(r["day_move_pct"]), 3) if r.get("day_move_pct") is not None else None,
            "ltp": r.get("ltp"),
        }
        for r in rows[: rule.top_n]
    ]


# ---- daily automatic run ------------------------------------------------------------------------


@dataclass
class AutoSettings:
    on: bool = False
    run_at: str = "15:45"
    pick_time: str = "10:00"
    direction: str = "BOTH"
    top_n: int = 10
    minutes: int = 5
    bot: int = 1
    lists: list = field(default_factory=lambda: ["fno"])
    last_day: str | None = None
    last_run_id: int | None = None
    last_note: str | None = None


def _path() -> Path:
    explicit = os.environ.get("TREND_PICKS_AUTO_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("trend_picks_auto.json")
    return Path("trend_picks_auto.json")


def _hhmm(raw, low: dt.time, high: dt.time) -> str | None:
    try:
        h, m = str(raw).strip().split(":")
        t = dt.time(int(h), int(m))
    except (ValueError, TypeError):
        return None
    return t.strftime("%H:%M") if low <= t <= high else None


def load() -> AutoSettings:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    s = AutoSettings()
    for name in asdict(s):
        if name in raw:
            setattr(s, name, raw[name])
    s.run_at = _hhmm(s.run_at, dt.time(15, 31), dt.time(23, 59)) or "15:45"
    s.pick_time = _hhmm(s.pick_time, dt.time(9, 20), dt.time(14, 30)) or "10:00"
    s.direction = s.direction if s.direction in DIRECTIONS else "BOTH"
    s.top_n = max(1, min(MAX_TOP_N, int(s.top_n or 10)))
    s.minutes = int(s.minutes) if int(s.minutes or 0) in SIZES else 5
    s.bot = max(1, min(4, int(s.bot or 1)))
    s.lists = [str(x) for x in (s.lists or ["fno"])] or ["fno"]
    s.on = bool(s.on)
    return s


def save(s: AutoSettings) -> AutoSettings:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(s)))
    tmp.replace(path)
    return load()


def update(**changes) -> AutoSettings:
    """Raises ValueError on a bad value."""
    s = load()
    if "run_at" in changes and changes["run_at"] is not None:
        if _hhmm(changes["run_at"], dt.time(15, 31), dt.time(23, 59)) is None:
            raise ValueError("The daily run must be after the close: HH:MM between 15:31 and 23:59 IST.")
    if "pick_time" in changes and changes["pick_time"] is not None:
        if _hhmm(changes["pick_time"], dt.time(9, 20), dt.time(14, 30)) is None:
            raise ValueError("Pick a time between 09:20 and 14:30 IST.")
    if changes.get("direction") is not None and changes["direction"] not in DIRECTIONS:
        raise ValueError("direction must be BULLISH, BEARISH or BOTH.")
    for name, value in changes.items():
        if value is not None and hasattr(s, name) and name not in ("last_day", "last_run_id", "last_note"):
            setattr(s, name, value)
    return save(s)


def due(now: dt.datetime, s: AutoSettings) -> bool:
    local = now.astimezone(IST)
    if not s.on or local.weekday() >= 5 or s.last_day == local.date().isoformat():
        return False
    h, m = s.run_at.split(":")
    return local.time() >= dt.time(int(h), int(m))


async def run(start_fn, now_fn=lambda: dt.datetime.now(IST), poll: float = CHECK_SEC) -> None:
    """Loop: once a weekday after `run_at`, start that day's strong-trend pick backtest. Never raises.

    `start_fn(settings, day)` starts it and returns (run_id or None, note)."""
    while True:
        try:
            s = load()
            now = now_fn()
            if due(now, s):
                day = now.astimezone(IST).date()
                run_id, note = await start_fn(s, day)
                if run_id is not None or note:
                    s = load()
                    # A refusal that will not change today (no candles, a holiday) still counts as today's go.
                    s.last_day, s.last_run_id, s.last_note = day.isoformat(), run_id, note
                    save(s)
                    log.info("daily strong-trend pick backtest: %s", note)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("daily strong-trend pick backtest failed")
        await asyncio.sleep(poll)
