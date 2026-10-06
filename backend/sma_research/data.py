"""Download past 1-minute candles and the bot's saved settings (run on the Fly machine).

Only reads: Groww's candle history API (the same call the chart and Replay
use) and the SMA terminal's settings row. Writes go to ``--out`` only. No
order API is imported or called.

Output (``<out>/``):
  settings.json        the saved BotConfig row (strategy settings; no secrets)
  universe.json        the chosen stocks and how they were ranked
  candles/<SYM>.pkl.gz 1-minute candles, Groww's running-total volume as-is
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger("sma_research")

# Groww serves 1-minute history in pieces; fetch_frame splits into 7-day
# requests and gives each call 60 s, so ask for 21 days at a time.
WINDOW_DAYS = 21
PAUSE_SEC = 0.35  # between Groww requests: the live app shares the token's rate limit


def market_hours(now: dt.datetime | None = None) -> bool:
    """09:00-15:45 IST on a weekday: the live bot needs the machine then."""
    from groww_client import IST

    now = (now or dt.datetime.now(IST)).astimezone(IST)
    return now.weekday() < 5 and dt.time(9, 0) <= now.time() <= dt.time(15, 45)


def saved_settings() -> dict:
    """The terminal's saved settings, read without writing anything."""
    from database import session_factory
    from models import BotConfig

    with session_factory()() as db:
        row = db.get(BotConfig, 1)
        if row is None:
            raise SystemExit("No saved SMA settings found (BotConfig row 1).")
        return {c.name: getattr(row, c.name) for c in BotConfig.__table__.columns}


def _broker():
    from groww_client import GrowwClient

    broker = GrowwClient(mode="PAPER")
    broker.adopt_saved_session()
    if not broker.token:
        raise SystemExit("No Groww session. Log in to Groww on the desk Settings page first.")
    return broker


async def _fetch(broker, symbol: str, start: dt.datetime, end: dt.datetime) -> pd.DataFrame:
    from candle_history import fetch_frame

    parts = []
    cursor = start
    while cursor < end:
        stop = min(end, cursor + dt.timedelta(days=WINDOW_DAYS))
        part = await fetch_frame(broker, symbol, cursor, stop)
        if part is not None and not part.empty:
            parts.append(part)
        cursor = stop
        await asyncio.sleep(PAUSE_SEC)
    if not parts:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    frame = pd.concat(parts).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return frame[["ts", "open", "high", "low", "close", "volume"]]


def _daily_turnover(frame: pd.DataFrame) -> pd.Series:
    """₹ traded per session (close × shares), from the running-total volume."""
    from indicators import derive_minute_volume

    if frame.empty:
        return pd.Series(dtype=float)
    days = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    shares = derive_minute_volume(frame).fillna(0)
    return (frame["close"] * shares).groupby(days.to_numpy()).sum()


async def download(out: Path, *, days: int, top: int, symbols: list[str] | None, rank_days: int = 8) -> dict:
    """Rank the universe by recent ₹ turnover, keep the top N, download their history."""
    from groww_client import IST

    out.mkdir(parents=True, exist_ok=True)
    (out / "candles").mkdir(exist_ok=True)
    (out / "settings.json").write_text(json.dumps(saved_settings(), default=str, indent=1))
    broker = _broker()
    now = dt.datetime.now(IST).replace(tzinfo=None)
    end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now < end:
        end -= dt.timedelta(days=1)

    if symbols:
        chosen = [s.upper() for s in symbols]
        ranking: list[dict] = [{"symbol": s, "turnover_cr": None} for s in chosen]
    else:
        from app.services.instruments import instrument_master

        pool = instrument_master.fno_stocks()
        if not pool:
            raise SystemExit("The F&O stock list is empty (instrument master did not load).")
        ranking = []
        start = end - dt.timedelta(days=rank_days)
        for i, sym in enumerate(pool, 1):
            try:
                frame = await _fetch(broker, sym, start, end)
                turnover = _daily_turnover(frame)
                value = float(turnover.median()) / 1e7 if len(turnover) else 0.0
            except Exception as exc:  # noqa: BLE001
                logger.warning("rank %s: %s", sym, exc)
                value = 0.0
            ranking.append({"symbol": sym, "turnover_cr": round(value, 2)})
            if i % 20 == 0:
                logger.info("ranked %d/%d", i, len(pool))
        ranking.sort(key=lambda r: r["turnover_cr"] or 0, reverse=True)
        chosen = [r["symbol"] for r in ranking[:top] if (r["turnover_cr"] or 0) > 0]

    start = end - dt.timedelta(days=days)
    got: dict[str, int] = {}
    for i, sym in enumerate(chosen, 1):
        path = out / "candles" / f"{sym}.pkl.gz"
        if path.exists():
            got[sym] = len(pd.read_pickle(path))
            continue
        t0 = time.monotonic()
        try:
            frame = await _fetch(broker, sym, start, end)
        except Exception as exc:  # noqa: BLE001
            logger.warning("download %s: %s", sym, exc)
            continue
        if frame.empty:
            continue
        frame.to_pickle(path)
        got[sym] = len(frame)
        logger.info("%d/%d %s: %d candles in %.0fs", i, len(chosen), sym, len(frame), time.monotonic() - t0)

    meta = {
        "downloaded_at": dt.datetime.now(IST).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "requested_days": days,
        "top": top,
        "ranking": ranking,
        "symbols": sorted(got),
        "candles": got,
    }
    (out / "universe.json").write_text(json.dumps(meta, indent=1))
    return meta
