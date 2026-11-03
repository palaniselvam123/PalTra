"""9:45 market-regime checkpoint (market_regime.py).

Off by default (`market_regime.json` next to the SMA database, `MARKET_REGIME_FILE`).
When it is on, the desk reads an equal-weight basket at 09:45, 11:15 and 13:30 IST
and keeps that reading for the rest of the session:

    TREND_DAY                  score 5-6   full quantity
    SELECTIVE_STOCK_SPECIFIC   score 3-4   half quantity, and only a stock whose
                                            relative volume and 5-minute ATR clear
                                            the floors
    SIDEWAYS_CHOP              score 0-2   SMA-cross and breakout entries are paused

Groww's cash candles do not include the Nifty index, so the index series is the
equal-weight of fifteen liquid names (five banks, five IT, five other
heavyweights). Breadth is how many of those names are up or down, and the two
sectors are the bank group and the IT group. It is a proxy, not the official
Nifty or the NSE advance/decline line.

The six checks are the 09:45 list: outside yesterday's range or a narrow CPR,
breadth, the two sectors moving with the index, the last four 5-minute closes
on one side of VWAP, an opening gap under 0.65%, and ADX at least 22 with the
choppiness index at most 55. Before a gated entry, a choppiness index above
61.8 on that basket or on the stock's own 5-minute candles refuses it.

Gated entries are the moving-average and breakout ones (SMA cross, gap mode,
join, late, counter, breakout, SMA 9 turn, always-in). News, a hand-off, Force,
a fast-move hand-off, and candle pattern or colour entries are left alone.
Replays and the research desk are not attached. Nothing here places an order.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from candles import bucket_start
from groww_client import IST, market_is_open
from indicators import adx_wilder, atr_wilder, derive_minute_volume, true_range

log = logging.getLogger("sma.market_regime")

POLL_SEC = 15.0
CHECKPOINTS = ("09:45", "11:15", "13:30")
ADX_PERIOD = 14
CHOP_PERIOD = 14
ADX_MIN = 22.0
CHOP_MAX = 55.0
CHOP_HARD = 61.8
CPR_NARROW = 0.25
AD_HIGH = 1.8
AD_LOW = 0.55
SECTOR_MOVE = 0.25
GAP_MAX = 0.65
VWAP_BARS = 4
TREND_SCORE = 5
SELECTIVE_SCORE = 3
RVOL_MIN = 1.5
ATR_PCT_MIN = 0.25
MIN_NAMES = 10
MIN_SECTOR = 3
MIN_RVOL_SESSIONS = 2
FAILS_BEFORE_GIVE_UP = 3
CANDLE_MIN = 5

BANKS = ("HDFCBANK", "ICICIBANK", "SBIN", "KOTAKBANK", "AXISBANK")
IT = ("TCS", "INFY", "HCLTECH", "WIPRO", "TECHM")
OTHERS = ("RELIANCE", "LT", "ITC", "BHARTIARTL", "HINDUNILVR")
BASKET = BANKS + IT + OTHERS

# Moving-average and breakout entries. A ":PULLBACK" / ":EXTREME_LIMIT" suffix is ignored.
GATED = frozenset({
    "SMA_CROSS",
    "GAP_MODE",
    "JOIN_TREND",
    "ALWAYS_IN",
    "ALWAYS_IN_REVERSE",
    "LATE_ENTRY",
    "EARLY_REVERSAL",
    "COUNTER_REENTRY",
    "BREAKOUT_ARRIVAL",
    "SMA9_TURN",
})

REGIMES = ("TREND_DAY", "SELECTIVE_STOCK_SPECIFIC", "SIDEWAYS_CHOP")

_MEMORY: dict = {"reading": None}


@dataclass
class Settings:
    on: bool = False
    rvol_min: float = RVOL_MIN
    atr_pct_min: float = ATR_PCT_MIN


@dataclass
class MarketRegimeOutput:
    score: int
    max_score: int
    regime: str
    position_size_multiplier: float
    adx_value: float | None
    chop_value: float | None
    cpr_width_pct: float | None
    breakdown: dict[str, bool]
    notes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def entry_base(why: str) -> str:
    return (why or "SMA_CROSS").split(":", 1)[0].upper()


def as_ist(now: dt.datetime) -> dt.datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=IST)
    return now.astimezone(IST)


def route(score: int) -> tuple[str, float]:
    """Score to regime and the quantity multiplier (1, 1/2, or 0)."""
    if score >= TREND_SCORE:
        return "TREND_DAY", 1.0
    if score >= SELECTIVE_SCORE:
        return "SELECTIVE_STOCK_SPECIFIC", 0.5
    return "SIDEWAYS_CHOP", 0.0


def checkpoint_due(now: dt.datetime, fired: set[str]) -> str | None:
    """The latest checkpoint time that has passed today and has not been recorded."""
    clock = as_ist(now)
    if clock.time() > dt.time(15, 30):
        return None
    passed = [slot for slot in CHECKPOINTS if clock.time() >= _hhmm(slot)]
    if not passed:
        return None
    slot = passed[-1]
    return None if slot in fired else slot


def _hhmm(slot: str) -> dt.time:
    hour, minute = slot.split(":")
    return dt.time(int(hour), int(minute))


def settings_path() -> Path:
    explicit = os.environ.get("MARKET_REGIME_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :]).with_name("market_regime.json")
    return Path("market_regime.json")


def _raw() -> dict:
    try:
        data = json.loads(settings_path().read_text())
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        log.exception("could not read the market regime file")
        return {}
    return data if isinstance(data, dict) else {}


def _write(data: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def clean(raw: dict | None) -> Settings:
    raw = raw or {}
    s = Settings()
    s.on = bool(raw.get("on", s.on))
    try:
        s.rvol_min = min(10.0, max(0.5, float(raw.get("rvol_min", s.rvol_min))))
    except (TypeError, ValueError):
        pass
    try:
        s.atr_pct_min = min(5.0, max(0.05, float(raw.get("atr_pct_min", s.atr_pct_min))))
    except (TypeError, ValueError):
        pass
    return s


def load() -> Settings:
    return clean(_raw())


def save(updates: dict) -> Settings:
    data = _raw()
    s = clean({**asdict(load()), **{k: v for k, v in (updates or {}).items() if v is not None}})
    data.update(asdict(s))
    _write(data)
    return s


def reset_state() -> None:
    """Forget the in-memory reading. Tests point the file elsewhere and call this."""
    _MEMORY["reading"] = None


def fired_slots(now: dt.datetime) -> set[str]:
    data = _raw()
    if data.get("fired_day") != as_ist(now).date().isoformat():
        return set()
    return {str(slot) for slot in data.get("fired") or []}


def mark_fired(now: dt.datetime, slot: str) -> None:
    data = _raw()
    day = as_ist(now).date().isoformat()
    slots = set(data.get("fired") or []) if data.get("fired_day") == day else set()
    slots.add(slot)
    data["fired_day"] = day
    data["fired"] = sorted(slots)
    _write(data)


def publish(reading: dict) -> dict:
    """Keep a good reading for today when a later pass cannot build one."""
    prev = _MEMORY["reading"] if _MEMORY["reading"] is not None else _raw().get("reading")
    if (
        reading.get("error")
        and isinstance(prev, dict)
        and prev.get("day") == reading.get("day")
        and not prev.get("error")
        and prev.get("regime")
    ):
        reading = {**prev, "last_error": reading.get("error"), "last_error_at": reading.get("at")}
    _MEMORY["reading"] = reading
    data = _raw()
    data["reading"] = reading
    _write(data)
    return reading


def current(now: dt.datetime) -> dict | None:
    """Today's reading, when it has a regime. A failed pass does not count."""
    reading = _MEMORY["reading"]
    if reading is None:
        stored = _raw().get("reading")
        reading = stored if isinstance(stored, dict) else None
        _MEMORY["reading"] = reading
    if not isinstance(reading, dict) or reading.get("error") or not reading.get("regime"):
        return None
    if reading.get("day") != as_ist(now).date().isoformat():
        return None
    return reading


def basket_labels() -> dict:
    return {"banks": list(BANKS), "it": list(IT), "other": list(OTHERS)}


def rules() -> dict:
    return {
        "trend_at": TREND_SCORE,
        "selective_at": SELECTIVE_SCORE,
        "adx_min": ADX_MIN,
        "chop_max": CHOP_MAX,
        "chop_hard": CHOP_HARD,
        "cpr_narrow": CPR_NARROW,
        "gap_max": GAP_MAX,
        "ad_high": AD_HIGH,
        "ad_low": AD_LOW,
        "sector_move": SECTOR_MOVE,
        "vwap_bars": VWAP_BARS,
    }


# --- indicators -------------------------------------------------------------


def calculate_vwap(df: pd.DataFrame) -> pd.Series:
    """Intraday VWAP. `minute_volume` is the shares in each bar; else `volume` is."""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    column = "minute_volume" if "minute_volume" in df.columns else "volume"
    vol = pd.to_numeric(df[column], errors="coerce").fillna(0.0) if column in df.columns else 0.0
    cum_vol = vol.cumsum().replace(0, np.nan)
    return (typical * vol).cumsum() / cum_vol


def choppiness(df: pd.DataFrame, period: int = CHOP_PERIOD) -> float | None:
    """Choppiness index, 0-100. Above 61.8 is sideways; below 38.2 is a strong trend."""
    if df is None or len(df) < period + 1 or not {"high", "low", "close"}.issubset(df.columns):
        return None
    tr = true_range(df)
    span = tr.rolling(period).sum()
    width = (df["high"].rolling(period).max() - df["low"].rolling(period).min()).replace(0, np.nan)
    values = 100 * np.log10(span / width) / np.log10(period)
    last = values.iloc[-1]
    if not np.isfinite(last):
        return None
    return round(float(last), 2)


def adx_value(df: pd.DataFrame, period: int = ADX_PERIOD) -> float | None:
    """Wilder ADX, the same smoother the bots use."""
    if df is None or len(df) < period + 1 or not {"high", "low", "close"}.issubset(df.columns):
        return None
    last = adx_wilder(df, period).iloc[-1]
    if not np.isfinite(last):
        return None
    return round(float(last), 2)


def cpr_width_pct(prev_high: float, prev_low: float, prev_close: float) -> float | None:
    """Central pivot range width as a percent of the pivot. Narrow is under 0.25%."""
    try:
        high, low, close = float(prev_high), float(prev_low), float(prev_close)
    except (TypeError, ValueError):
        return None
    if not all(np.isfinite(v) for v in (high, low, close)):
        return None
    pivot = (high + low + close) / 3.0
    if pivot == 0:
        return None
    bc = (high + low) / 2.0
    tc = (pivot - bc) + pivot
    return round(abs(tc - bc) / abs(pivot) * 100.0, 3)


def evaluate(
    df_5m_history: pd.DataFrame,
    df_5m_today: pd.DataFrame,
    prev_day_hlc: dict,
    advances: int,
    declines: int,
    sector_changes_pct: list[float],
) -> MarketRegimeOutput:
    """The six 09:45 checks. `df_5m_today` is today's closed 5-minute bars only."""
    if df_5m_today is None or len(df_5m_today) == 0:
        raise ValueError("no closed 5-minute bars today")
    current_price = float(df_5m_today["close"].iloc[-1])
    today_open = float(df_5m_today["open"].iloc[0])
    prev_high = float(prev_day_hlc["high"])
    prev_low = float(prev_day_hlc["low"])
    prev_close = float(prev_day_hlc["close"])

    cpr = cpr_width_pct(prev_high, prev_low, prev_close)
    outside = current_price > prev_high or current_price < prev_low or (cpr is not None and cpr < CPR_NARROW)
    where = (
        "above yesterday's high"
        if current_price > prev_high
        else "below yesterday's low"
        if current_price < prev_low
        else "inside yesterday's range"
    )
    cpr_note = "CPR not ready" if cpr is None else f"CPR {cpr:.3f}% ({'narrow' if cpr < CPR_NARROW else 'wide'})"
    outside_note = f"{where}; {cpr_note}"

    declines_safe = max(int(declines), 1)
    ad_ratio = int(advances) / declines_safe
    breadth = ad_ratio >= AD_HIGH or ad_ratio <= AD_LOW
    breadth_note = f"advances {int(advances)}, declines {int(declines)}, ratio {ad_ratio:.2f}"

    index_change = (current_price - prev_close) / prev_close * 100.0 if prev_close else 0.0
    sectors = [float(s) for s in sector_changes_pct]
    if not sectors:
        confluence = False
        sector_note = "no sector moves"
    elif index_change >= 0:
        confluence = all(s > SECTOR_MOVE for s in sectors)
        sector_note = f"index {index_change:+.2f}%; sectors {', '.join(f'{s:+.2f}%' for s in sectors)}"
    else:
        confluence = all(s < -SECTOR_MOVE for s in sectors)
        sector_note = f"index {index_change:+.2f}%; sectors {', '.join(f'{s:+.2f}%' for s in sectors)}"

    separated = _vwap_separated(df_5m_today)
    vwap_note = f"last {min(VWAP_BARS, len(df_5m_today))} closes {'hold one side of VWAP' if separated else 'cross VWAP'}"

    gap_pct = abs(today_open - prev_close) / abs(prev_close) * 100.0 if prev_close else 0.0
    moderate = gap_pct < GAP_MAX
    gap_note = f"opening gap {gap_pct:.2f}%"

    adx = adx_value(df_5m_history)
    chop = choppiness(df_5m_history)
    quant = adx is not None and chop is not None and adx >= ADX_MIN and chop <= CHOP_MAX
    quant_note = f"ADX {adx if adx is not None else '—'}, chop {chop if chop is not None else '—'}"

    checks = {
        "outside_prev_range_or_narrow_cpr": bool(outside),
        "breadth_aligned": bool(breadth),
        "sector_confluence": bool(confluence),
        "vwap_separation": bool(separated),
        "moderate_opening_gap": bool(moderate),
        "adx_and_chop_confirm": bool(quant),
    }
    notes = {
        "outside_prev_range_or_narrow_cpr": outside_note,
        "breadth_aligned": breadth_note,
        "sector_confluence": sector_note,
        "vwap_separation": vwap_note,
        "moderate_opening_gap": gap_note,
        "adx_and_chop_confirm": quant_note,
    }
    score = sum(1 for passed in checks.values() if passed)
    regime, multiplier = route(score)
    return MarketRegimeOutput(
        score=score,
        max_score=len(checks),
        regime=regime,
        position_size_multiplier=multiplier,
        adx_value=adx,
        chop_value=chop,
        cpr_width_pct=cpr,
        breakdown=checks,
        notes=notes,
    )


def _vwap_separated(df: pd.DataFrame) -> bool:
    if df is None or len(df) < VWAP_BARS:
        return False
    vwap = calculate_vwap(df)
    closes = df["close"].iloc[-VWAP_BARS:]
    recent = vwap.iloc[-VWAP_BARS:]
    if closes.isna().any() or recent.isna().any():
        return False
    return bool((closes > recent).all() or (closes < recent).all())


# --- basket -----------------------------------------------------------------


def _epoch(now: dt.datetime) -> int:
    return int(as_ist(now).timestamp())


def _closed_minutes(df: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    """1-minute bars whose minute has ended, oldest first, with a shares-per-bar column."""
    if df is None or len(df) == 0 or "ts" not in df.columns:
        return pd.DataFrame()
    work = df.copy()
    work["ts"] = pd.to_numeric(work["ts"], errors="coerce")
    work = work.dropna(subset=["ts"]).sort_values("ts")
    work = work[work["ts"] + 60 <= _epoch(now)]
    if work.empty:
        return work
    if "minute_volume" in work.columns:
        work["minute_volume"] = pd.to_numeric(work["minute_volume"], errors="coerce")
    else:
        work["minute_volume"] = derive_minute_volume(work).to_numpy()
    return work


def _to_5m(df: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    """Closed 5-minute bars from 09:15 IST. Volume is the shares inside the bar."""
    if df is None or len(df) == 0:
        return pd.DataFrame()
    work = df.copy()
    work["_b"] = [bucket_start(int(ts), CANDLE_MIN) for ts in work["ts"]]
    end = _epoch(now)
    rows = []
    for start, group in work.groupby("_b", sort=True):
        if int(start) + CANDLE_MIN * 60 > end:
            continue
        vol = pd.to_numeric(group["minute_volume"], errors="coerce").fillna(0.0).sum()
        rows.append(
            {
                "ts": int(start),
                "open": float(group["open"].iloc[0]),
                "high": float(pd.to_numeric(group["high"], errors="coerce").max()),
                "low": float(pd.to_numeric(group["low"], errors="coerce").min()),
                "close": float(group["close"].iloc[-1]),
                "volume": float(vol),
            }
        )
    return pd.DataFrame(rows)


def _dates(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts.astype("int64"), unit="s", utc=True).dt.tz_convert(IST).dt.date


def _day_change(df: pd.DataFrame, today: dt.date) -> float | None:
    dates = _dates(df["ts"])
    prev_days = sorted({d for d in dates if d < today})
    if not prev_days or not (dates == today).any():
        return None
    prev_close = float(df.loc[dates == prev_days[-1], "close"].iloc[-1])
    last = float(df.loc[dates == today, "close"].iloc[-1])
    if prev_close == 0:
        return None
    return (last - prev_close) / prev_close * 100.0


def from_frames(frames: dict[str, pd.DataFrame], now: dt.datetime) -> tuple[MarketRegimeOutput, list[str]]:
    """Build today's checkpoint from each name's 1-minute candles (yesterday included)."""
    clock = as_ist(now)
    today = clock.date()
    prepared: dict[str, pd.DataFrame] = {}
    for symbol, raw in frames.items():
        name = (symbol or "").upper()
        closed = _closed_minutes(raw, clock)
        if closed.empty or not {"open", "high", "low", "close"}.issubset(closed.columns):
            continue
        dates = set(_dates(closed["ts"]))
        if today in dates and any(d < today for d in dates):
            prepared[name] = closed
    if len(prepared) < MIN_NAMES:
        raise ValueError(f"{len(prepared)} names have both yesterday and today; {MIN_NAMES} are needed")

    parts = []
    for name, df in prepared.items():
        part = df[["ts", "open", "high", "low", "close", "minute_volume"]].copy()
        part["symbol"] = name
        parts.append(part)
    stacked = pd.concat(parts, ignore_index=True)
    stacked["ts"] = stacked["ts"].astype("int64")
    counts = stacked.groupby("ts")["symbol"].nunique()
    keep = counts[counts >= MIN_NAMES].index
    stacked = stacked[stacked["ts"].isin(keep)]
    if stacked.empty:
        raise ValueError("the names do not share enough minutes to build a basket")
    index = (
        stacked.groupby("ts", sort=True)
        .agg(
            open=("open", "mean"),
            high=("high", "mean"),
            low=("low", "mean"),
            close=("close", "mean"),
            minute_volume=("minute_volume", "sum"),
        )
        .reset_index()
    )
    dates = _dates(index["ts"])
    prev_days = sorted({d for d in dates if d < today})
    if not prev_days or not (dates == today).any():
        raise ValueError("the basket has no previous session, so the CPR and the gap cannot be read")
    prev = index.loc[dates == prev_days[-1]]
    prev_hlc = {"high": float(prev["high"].max()), "low": float(prev["low"].min()), "close": float(prev["close"].iloc[-1])}
    five = _to_5m(index, clock)
    five_dates = _dates(five["ts"]) if len(five) else pd.Series(dtype=object)
    today_5m = five.loc[five_dates == today] if len(five) else five
    if len(today_5m) < VWAP_BARS:
        raise ValueError(f"only {len(today_5m)} closed 5-minute bars today; {VWAP_BARS} are needed")
    history = five.loc[five_dates.isin((prev_days[-1], today))] if len(five) else five

    changes = {name: _day_change(df, today) for name, df in prepared.items()}
    changes = {name: value for name, value in changes.items() if value is not None}
    advances = sum(1 for value in changes.values() if value > 0)
    declines = sum(1 for value in changes.values() if value < 0)

    def sector(names: tuple[str, ...]) -> float | None:
        got = [changes[name] for name in names if name in changes]
        if len(got) < MIN_SECTOR:
            return None
        return float(np.mean(got))

    sectors = [value for value in (sector(BANKS), sector(IT)) if value is not None]
    output = evaluate(history, today_5m, prev_hlc, advances, declines, sectors)
    return output, sorted(prepared)


def _stamp(now: dt.datetime, slot: str, error: str = "", output: MarketRegimeOutput | None = None, symbols: list[str] | None = None, symbol_errors: list[str] | None = None) -> dict:
    clock = as_ist(now)
    reading = {
        "day": clock.date().isoformat(),
        "at": clock.isoformat(timespec="seconds"),
        "checkpoint": slot,
        "error": error,
        "symbols": symbols or [],
        "symbol_errors": (symbol_errors or [])[:8],
        "proxy": "Equal-weight basket of 5 banks, 5 IT names and 5 other heavyweights. Not the official Nifty.",
    }
    if output is not None:
        reading.update(output.as_dict())
    return reading


async def collect(fetch, now: dt.datetime, slot: str, symbols: tuple[str, ...] = BASKET, pause: float = 0.35) -> dict:
    """Download the basket and score it. `fetch(symbol, start, end)` returns 1-minute candles."""
    clock = as_ist(now)
    start = (clock - dt.timedelta(days=4)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    end = clock.replace(tzinfo=None)
    frames: dict[str, pd.DataFrame] = {}
    errors: list[str] = []
    for index, symbol in enumerate(symbols):
        if index and pause:
            await asyncio.sleep(pause)
        try:
            frames[symbol] = await fetch(symbol, start, end)
        except Exception as exc:  # noqa: BLE001 - one name must not sink the basket
            errors.append(f"{symbol}: {exc}")
    try:
        output, used = from_frames(frames, clock)
    except ValueError as exc:
        text = str(exc)
        if errors:
            text = f"{text}. {errors[0]}"
        return _stamp(clock, slot, error=text, symbol_errors=errors)
    return _stamp(clock, slot, output=output, symbols=used, symbol_errors=errors)


# --- stock filter and the entry gate ----------------------------------------


def _tape_5m(tape: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    closed = _closed_minutes(tape, now)
    return _to_5m(closed, now) if len(closed) else pd.DataFrame()


def chop_from_tape(tape: pd.DataFrame | None, now: dt.datetime) -> float | None:
    if tape is None or len(getattr(tape, "index", [])) == 0:
        return None
    return choppiness(_tape_5m(tape, now))


def relative_volume(tape: pd.DataFrame | None, now: dt.datetime) -> float | None:
    """Shares from 09:15 to the last closed minute, over the average of that clock time on earlier days."""
    if tape is None:
        return None
    closed = _closed_minutes(tape, now)
    if closed.empty:
        return None
    dates = _dates(closed["ts"])
    times = pd.to_datetime(closed["ts"].astype("int64"), unit="s", utc=True).dt.tz_convert(IST).dt.time
    today = as_ist(now).date()
    today_rows = closed.loc[dates == today]
    if today_rows.empty:
        return None
    last_time = times.loc[today_rows.index[-1]]
    open_t = dt.time(9, 15)

    def shares(day) -> float | None:
        mask = (dates == day) & (times >= open_t) & (times <= last_time)
        vol = pd.to_numeric(closed.loc[mask, "minute_volume"], errors="coerce").dropna()
        if vol.empty:
            return None
        return float(vol.sum())

    today_shares = shares(today)
    earlier = []
    for day in sorted({d for d in dates if d < today}, reverse=True):
        got = shares(day)
        if got is not None and got > 0:
            earlier.append(got)
        if len(earlier) >= 10:
            break
    if today_shares is None or today_shares <= 0 or len(earlier) < MIN_RVOL_SESSIONS:
        return None
    return round(today_shares / (sum(earlier) / len(earlier)), 2)


def atr_percent(tape: pd.DataFrame | None, now: dt.datetime, period: int = ADX_PERIOD) -> float | None:
    """Wilder ATR on the closed 5-minute bars, as a percent of the last close."""
    five = _tape_5m(tape, now) if tape is not None else pd.DataFrame()
    if len(five) < period + 1:
        return None
    value = atr_wilder(five, period).iloc[-1]
    close = float(five["close"].iloc[-1])
    if not np.isfinite(value) or close == 0:
        return None
    return round(float(value) / close * 100.0, 3)


def selective_reason(tape: pd.DataFrame | None, now: dt.datetime, settings: Settings) -> str | None:
    """Why a selective session will not take this stock. None when it qualifies."""
    rvol = relative_volume(tape, now)
    if rvol is None:
        return "relative volume is not ready (needs two earlier sessions)"
    if rvol < settings.rvol_min:
        return f"relative volume {rvol:.2f}× is below {settings.rvol_min:g}×"
    atr = atr_percent(tape, now)
    if atr is None:
        return "5-minute ATR is not ready"
    if atr < settings.atr_pct_min:
        return f"5-minute ATR {atr:.2f}% of price is below {settings.atr_pct_min:g}%"
    return None


def _engine_now(engine) -> dt.datetime:
    clock = getattr(engine, "_now", None)
    now = clock() if clock is not None else dt.datetime.now(IST)
    return as_ist(now)


def block_entry(engine, symbol: str, why: str) -> str | None:
    """Why this gated entry stops, or None to let it through. Off, or no reading today: no block."""
    settings = load()
    if not settings.on or entry_base(why) not in GATED:
        return None
    now = _engine_now(engine)
    reading = current(now)
    if reading is None:
        return None
    chop = reading.get("chop_value")
    if chop is not None and float(chop) > CHOP_HARD:
        return f"basket chop {float(chop):.1f} is above {CHOP_HARD:g}"
    name = (symbol or "").upper()
    tapes = getattr(engine, "_tapes", {}) or {}
    tape = tapes.get(name)
    stock_chop = chop_from_tape(tape, now)
    if stock_chop is not None and stock_chop > CHOP_HARD:
        return f"{name} 5-minute chop {stock_chop:.1f} is above {CHOP_HARD:g}"
    regime = reading.get("regime")
    if regime == "SIDEWAYS_CHOP":
        return f"sideways session (score {reading.get('score')}/{reading.get('max_score')}); trend entries are paused"
    if regime == "SELECTIVE_STOCK_SPECIFIC":
        return selective_reason(tape, now, settings)
    return None


def scaled_qty(engine, why: str, qty: int) -> int:
    """Half quantity on a selective day. A paused day is refused before the order, so quantity stays put."""
    base = int(qty)
    if not load().on or entry_base(why) not in GATED:
        return base
    reading = current(_engine_now(engine))
    if reading is None:
        return base
    mult = float(reading.get("position_size_multiplier") or 0)
    if mult >= 1 or mult <= 0:
        return base
    return max(1, int(base * mult + 0.5))


def attach(engines) -> None:
    """Point the live bots at the gate. Replays and the research desk are not in `engines`."""
    for engine in engines:
        engine.regime_block = block_entry
        engine.regime_qty = scaled_qty


def alert_text(reading: dict) -> str:
    slot = reading.get("checkpoint") or ""
    if reading.get("error") and not reading.get("regime"):
        return f"Market regime {slot}: the basket could not be read. {reading['error']}"
    mult = float(reading.get("position_size_multiplier") or 0)
    size = "full size" if mult >= 1 else "half size" if mult > 0 else "trend entries paused"
    regime = str(reading.get("regime") or "").replace("_", " ").title()
    adx = reading.get("adx_value")
    chop = reading.get("chop_value")
    cpr = reading.get("cpr_width_pct")
    lines = [
        f"Market regime {slot}: {regime} {reading.get('score')}/{reading.get('max_score')}, {size}.",
        f"ADX {adx if adx is not None else '—'}, chop {chop if chop is not None else '—'}, CPR {cpr if cpr is not None else '—'}%.",
    ]
    missed = [name for name, ok in (reading.get("breakdown") or {}).items() if not ok]
    if missed:
        lines.append("Not met: " + ", ".join(missed) + ".")
    lines.append("Basket proxy, not the official Nifty. News, Force and candle-colour entries are unchanged.")
    if not load().on:
        lines.append("The switch is off, so order size is unchanged.")
    return "\n".join(lines)


def view(now: dt.datetime | None = None) -> dict:
    now = as_ist(now or dt.datetime.now(IST))
    settings = load()
    stored = _MEMORY["reading"]
    if stored is None:
        raw = _raw().get("reading")
        stored = raw if isinstance(raw, dict) else None
        _MEMORY["reading"] = stored
    # Yesterday's call is not today's. A failed pass today is still shown, so the card can say why.
    today = stored if isinstance(stored, dict) and stored.get("day") == now.date().isoformat() else None
    return {
        "on": settings.on,
        "rvol_min": settings.rvol_min,
        "atr_pct_min": settings.atr_pct_min,
        "checkpoints": list(CHECKPOINTS),
        "rules": rules(),
        "basket": basket_labels(),
        "reading": today,
        "applies": bool(settings.on and current(now)),
        "running": watcher.busy,
        "gated": sorted(GATED),
    }


class Watcher:
    """One pass at each checkpoint while the switch is on and the market is open."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.busy = False
        self._fails: dict[str, int] = {}

    async def evaluate(self, fetch, send, now: dt.datetime, slot: str, mark: bool) -> dict | None:
        if self._lock.locked():
            return None
        async with self._lock:
            return await self._evaluate(fetch, send, now, slot, mark)

    async def _evaluate(self, fetch, send, now: dt.datetime, slot: str, mark: bool) -> dict:
        self.busy = True
        try:
            reading = await collect(fetch, now, slot)
            stored = publish(reading)
            key = f"{as_ist(now).date().isoformat()} {slot}"
            failed = bool(reading.get("error") and not reading.get("regime"))
            if failed:
                self._fails[key] = self._fails.get(key, 0) + 1
                if mark and self._fails[key] >= FAILS_BEFORE_GIVE_UP:
                    mark_fired(now, slot)
            else:
                self._fails.pop(key, None)
                if mark and slot in CHECKPOINTS:
                    mark_fired(now, slot)
            if send is not None and (not failed or (mark and self._fails.get(key, 0) >= FAILS_BEFORE_GIVE_UP)):
                try:
                    await send(alert_text(stored if not failed else reading))
                except Exception:  # noqa: BLE001
                    log.exception("market regime alert failed")
            return stored
        finally:
            self.busy = False

    async def run(self, fetch, send, now_fn) -> None:
        while True:
            try:
                settings = load()
                now = as_ist(now_fn())
                if settings.on and market_is_open(now) and not self.busy:
                    slot = checkpoint_due(now, fired_slots(now))
                    if slot is not None:
                        await self.evaluate(fetch, send, now, slot, mark=True)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("market regime pass")
            await asyncio.sleep(POLL_SEC)

    def start(self, fetch, send, now_fn) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run(fetch, send, now_fn))

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


watcher = Watcher()
