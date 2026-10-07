"""Past 1-minute candles from Groww for the terminal chart's From/To view.

Read-only: this only downloads candles and reads the trade log. It never
places, changes, or cancels an order, and it does not touch the bot's own
candle cache.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import re
from types import SimpleNamespace

import pandas as pd

from database import session_factory
from groww_client import IST, _is_auth_error, _parse_candles
from indicators import enrich
from models import ReplayRun, TradeLog, trade_ref
from strategy_engine import candle_rows, chart_filters, filter_blocks, settings_for

# Groww serves 1-minute candles at most 7 days per request.
CHUNK_DAYS = 7
MAX_RANGE_DAYS = 30
# Bars before `start` so SMA 21 and ATR 14 are already formed on the first
# bar shown. Four calendar days covers a weekend plus a holiday.
WARMUP_DAYS = 4
_REQUEST_TIMEOUT_SEC = 15
_TOTAL_TIMEOUT_SEC = 60
# Candle sizes the chart offers, in minutes. Bigger bars are built from the
# 1-minute download, aligned to the 09:15 open like NSE charts.
INTERVALS = (1, 5, 15, 30, 60)
_SESSION_OPEN_MIN = 9 * 60 + 15
_SYMBOL = re.compile(r"^[A-Z0-9&_-]{1,20}$")
_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d")


class HistoryError(ValueError):
    """A request the chart should show back to the person as is."""


def parse_ist(text: str, *, end_of_day: bool = False) -> dt.datetime:
    """Read an IST wall-clock time such as 2026-10-01T09:15 (naive, IST)."""
    raw = (text or "").strip()
    for fmt in _FORMATS:
        try:
            value = dt.datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            value = value.replace(hour=23, minute=59)
        return value
    raise HistoryError(f"Could not read the time {raw!r}. Use YYYY-MM-DD HH:MM (IST).")


def check_range(symbol: str, start: dt.datetime, end: dt.datetime, now: dt.datetime) -> tuple[str, dt.datetime, dt.datetime]:
    name = (symbol or "").strip().upper()
    if not _SYMBOL.match(name):
        raise HistoryError("Pick a stock first.")
    end = min(end, now)
    if start >= end:
        raise HistoryError("From must be before To.")
    if end - start > dt.timedelta(days=MAX_RANGE_DAYS):
        raise HistoryError(f"Pick {MAX_RANGE_DAYS} days or less.")
    return name, start, end


def _download(sdk, symbol: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    frames = []
    cursor = start
    while cursor < end:
        stop = min(cursor + dt.timedelta(days=CHUNK_DAYS), end)
        raw = sdk.get_historical_candle_data(
            trading_symbol=symbol,
            exchange="NSE",
            segment="CASH",
            start_time=cursor.strftime("%Y-%m-%d %H:%M:%S"),
            end_time=stop.strftime("%Y-%m-%d %H:%M:%S"),
            interval_in_minutes=1,
            timeout=_REQUEST_TIMEOUT_SEC,
        )
        part = _parse_candles(raw, limit=None)
        if not part.empty:
            frames.append(part)
        cursor = stop
    if not frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    return pd.concat(frames).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)


async def fetch_frame(broker, symbol: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    """Download [start, end] in 7-day pieces off the event loop."""
    if not broker.token:
        broker.adopt_saved_session()
    if not broker.token:
        raise HistoryError("Past candles come from Groww. Log in to Groww on the desk Settings page first.")

    async def attempt() -> pd.DataFrame:
        sdk = broker._require_sdk()
        return await asyncio.wait_for(
            asyncio.to_thread(_download, sdk, symbol, start, end), timeout=_TOTAL_TIMEOUT_SEC
        )

    try:
        return await attempt()
    except HistoryError:
        raise
    except Exception as exc:  # noqa: BLE001
        if _is_auth_error(exc) and broker._recover_from_auth_failure():
            return await attempt()
        detail = str(exc).strip() or type(exc).__name__
        raise HistoryError(f"Groww did not send candles: {detail}"[:240]) from exc


def resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Merge 1-minute bars into `minutes`-long bars that start at 09:15 IST + k·minutes."""
    if minutes <= 1 or frame.empty:
        return frame
    ts = frame["ts"].astype("int64")
    ist_min = ((ts + 19_800) % 86_400) // 60
    offset = (ist_min - _SESSION_OPEN_MIN) % minutes
    bucket = ts - ts % 60 - offset * 60
    # Groww's `volume` is a running session total, so a bigger bar keeps the
    # total at its last minute. enrich() then differences it into the shares
    # traded in each bar; summing running totals counted them many times over.
    grouped = frame.assign(bucket=bucket).groupby("bucket", sort=True)
    out = pd.DataFrame(
        {
            "ts": grouped["ts"].first().index.astype("int64"),
            "open": grouped["open"].first().to_numpy(),
            "high": grouped["high"].max().to_numpy(),
            "low": grouped["low"].min().to_numpy(),
            "close": grouped["close"].last().to_numpy(),
            "volume": grouped["volume"].last().to_numpy(),
        }
    )
    return out.reset_index(drop=True)


def _epoch(value: dt.datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return int(value.timestamp())


def _one_book(rows: list, first: int, last: int, run_id: int | None, book: str | None = None) -> list:
    """The trades to mark: one book, never every replay of the same day.

    With `run_id`: that replay run only. Otherwise the practice/real trades
    plus the most recent replay run that traded inside the shown range, so
    replaying a day several times does not stack its markers.
    """
    if run_id is not None:
        return [r for r in rows if r.run_id == run_id]
    if book is not None:
        # One desk's own book only (the research desk's RESEARCH trades).
        return [r for r in rows if (r.mode or "PAPER").upper() == book]
    own = [r for r in rows if (r.mode or "PAPER").upper() in ("PAPER", "LIVE")]
    shown = [
        r
        for r in rows
        if (r.mode or "").upper() == "REPLAY" and r.entry_time is not None and first <= _epoch(r.entry_time) <= last
    ]
    if not shown:
        return own
    latest = max(shown, key=lambda r: (r.run_id is not None, r.run_id or 0, r.id))
    return own + [r for r in rows if (r.mode or "").upper() == "REPLAY" and r.run_id == latest.run_id]


def _markers(
    symbol: str, first: int, last: int, span: int = 60, run_id: int | None = None, book: str | None = None
) -> list[dict]:
    """Entries and exits on this stock inside the shown bars."""
    out: list[dict] = []
    with session_factory()() as db:
        rows = db.query(TradeLog).filter(TradeLog.symbol == symbol).order_by(TradeLog.id).all()
    for row in _one_book(rows, first, last + span - 1, run_id, book):
        ref = trade_ref(row.mode, row.run_id, row.book_seq, row.id)
        if row.entry_time is not None:
            t = _epoch(row.entry_time)
            if first <= t <= last + span - 1:
                out.append(
                    {
                        "time": t,
                        "direction": row.direction,
                        "price": row.entry_price,
                        "kind": "ENTRY",
                        # Lets the chart colour the entry by the trade's result.
                        "net_pnl": row.net_pnl if row.net_pnl is not None else row.gross_pnl,
                        "trade_ref": ref,
                        "open": row.exit_time is None,
                    }
                )
        if row.exit_time is not None and row.exit_price is not None:
            t = _epoch(row.exit_time)
            if first <= t <= last + span - 1:
                out.append(
                    {
                        "time": t,
                        "direction": row.direction,
                        "price": row.exit_price,
                        "kind": "EXIT",
                        "net_pnl": row.net_pnl if row.net_pnl is not None else row.gross_pnl,
                        "reason": row.exit_reason,
                        "trade_ref": ref,
                        "open": False,
                    }
                )
    return sorted(out, key=lambda m: m["time"])


def _finite(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def build_payload(
    frame: pd.DataFrame,
    symbol: str,
    start: dt.datetime,
    end: dt.datetime,
    cfg,
    interval: int = 1,
    run_id: int | None = None,
    book: str | None = None,
) -> dict:
    sma_fast = getattr(cfg, "sma_fast", 9) or 9
    sma_slow = getattr(cfg, "sma_slow", 21) or 21
    atr_period = getattr(cfg, "atr_period", 14) or 14
    candles: list[dict] = []
    blocked: list[dict] = []
    if not frame.empty:
        enriched = enrich(resample(frame, interval), sma_fast, sma_slow, atr_period)
        lo, hi = _epoch(start), _epoch(end)
        # Keep the bar that holds From even when it opened a little earlier.
        lo -= lo % 60
        lo -= ((((lo + 19_800) % 86_400) // 60) - _SESSION_OPEN_MIN) % interval * 60
        keep = (enriched["ts"] >= lo) & (enriched["ts"] <= hi)
        candles = [row for row, inside in zip(candle_rows(enriched), keep) if inside]
        # Refused crosses are judged on 1-minute candles, as the bot trades them.
        minute = enrich(frame, sma_fast, sma_slow, atr_period) if interval != 1 else enriched
        blocked = [b for b in filter_blocks(minute, cfg) if lo <= b["time"] <= hi]
    markers = _markers(symbol, candles[0]["time"], candles[-1]["time"], interval * 60, run_id, book) if candles else []
    return {
        "symbol": symbol,
        "interval": interval,
        "from": start.strftime("%Y-%m-%d %H:%M"),
        "to": end.strftime("%Y-%m-%d %H:%M"),
        "candles": candles,
        "markers": markers,
        "entry_price": None,
        "sl_trigger": None,
        "atr_multiplier": float(getattr(cfg, "atr_multiplier", 1.5) or 1.5),
        "blocked": blocked,
        "filters": chart_filters(cfg),
    }


async def load_history(
    broker,
    symbol: str,
    start_text: str,
    end_text: str,
    cfg,
    interval: int = 1,
    run_id: int | None = None,
    book: str | None = None,
) -> dict:
    if interval not in INTERVALS:
        raise HistoryError(f"Candle size must be one of {', '.join(str(i) for i in INTERVALS)} minutes.")
    now = dt.datetime.now(IST).replace(tzinfo=None, second=0, microsecond=0)
    start = parse_ist(start_text)
    end = parse_ist(end_text, end_of_day=True)
    symbol, start, end = check_range(symbol, start, end, now)
    # 21 bars of 30 or 60 minutes need more than four days before From.
    warmup = WARMUP_DAYS if interval <= 15 else 10
    frame = await fetch_frame(broker, symbol, start - dt.timedelta(days=warmup), end)
    # The stock's own settings (from the run's snapshot for a replay run).
    settings = settings_for(run_settings(cfg, run_id), symbol)
    return build_payload(frame, symbol, start, end, settings, interval, run_id, book)


def run_settings(cfg, run_id: int | None):
    """A replay run is judged with the settings it ran with, not today's."""
    if run_id is None:
        return cfg
    with session_factory()() as db:
        run = db.get(ReplayRun, run_id)
        raw = run.settings if run is not None else None
    try:
        snapshot = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        snapshot = {}
    if not isinstance(snapshot, dict) or not snapshot:
        return cfg
    base = {name: getattr(cfg, name) for name in dir(cfg) if not name.startswith("_") and not callable(getattr(cfg, name, None))}
    # A run from before per-stock settings used the shared ones for every stock.
    snapshot.setdefault("stock_settings", {})
    return SimpleNamespace(**{**base, **{k: v for k, v in snapshot.items() if v is not None}})
