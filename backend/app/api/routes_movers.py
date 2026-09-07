"""Morning movers API: the recorded price history, the rankings, and alerts.

Read endpoints answer from the durable snapshot table, so they work identically
during the session and after it has closed — which is the point of recording in
the first place.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.core.market_clock import IST, ist_now, session_state
from app.research import price_history
from app.research.snapshots import ist_date, snapshot_store
from app.services.alert_notifier import alert_notifier
from app.services.broadcaster import broadcaster
from app.services.market_data import market_data
from app.services.market_recorder import market_recorder
from app.services.movers import (
    FAST_PCT_PER_MIN, MIN_MOVE_PCT, SPEED_WINDOW_MIN, alert_ledger, fast_movers,
    format_alert, morning_cutoff, peak_fast_movers, ranked_movers, split_gainers_losers,
)

router = APIRouter(prefix="/api/movers", tags=["movers"])


def _company_name(symbol: str) -> str:
    """The tradable name behind a ticker, or "" when it is not in the master.

    A blank is returned rather than the symbol itself: the caller shows the
    symbol regardless, and echoing it as a name would make an unknown
    instrument look like a resolved one.
    """
    try:
        from app.services.instruments import instrument_master

        instrument_master.ensure_loaded()
        inst = instrument_master.get(symbol)
        return inst.name if inst else ""
    except Exception:  # noqa: BLE001 — a name is decoration, never a reason to fail
        return ""


def _mover_dict(m) -> dict:
    return {
        "symbol": m.symbol,
        "name": _company_name(m.symbol),
        "open_price": round(m.open_price, 2),
        "last_price": round(m.last_price, 2),
        "pct_from_open": round(m.pct_from_open, 3),
        "high_price": round(m.high_price, 2),
        "low_price": round(m.low_price, 2),
        "last_ts": m.last_ts,
        "last_time_ist": m.last_time_ist,
        "points": m.points,
        "first_time_ist": m.first_time_ist,
        "baseline_is_session_open": m.baseline_is_session_open,
    }


def _resolve_day(day: str | None) -> dt.date:
    if not day:
        return ist_date(int(ist_now().timestamp()))
    try:
        return dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(400, f"day must be YYYY-MM-DD, got {day!r}")


def _resolve_as_of(day: dt.date, at: str | None) -> dt.datetime | None:
    if not at:
        return None
    try:
        hh, mm = (int(x) for x in at.split(":")[:2])
    except ValueError:
        raise HTTPException(400, f"at must be HH:MM, got {at!r}")
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


# ---- recorder control ----------------------------------------------------


@router.get("/recorder")
async def recorder_status():
    return market_recorder.status()


@router.post("/recorder/start")
async def recorder_start():
    return await market_recorder.start()


@router.post("/recorder/stop")
async def recorder_stop():
    return await market_recorder.stop()


@router.post("/recorder/flush")
async def recorder_flush():
    """Write the current in-memory minute bars immediately, without waiting."""
    written = market_recorder.record_once()
    return {"written": written, "store": snapshot_store.stats(market_data.source.value)}


# ---- rankings ------------------------------------------------------------


@router.get("")
async def movers(
    day: str | None = Query(None, description="YYYY-MM-DD; defaults to today IST"),
    at: str | None = Query(None, description="HH:MM IST; window end — ranks as it stood then"),
    since: str | None = Query(None, description="HH:MM IST; window start, default the 09:15 open"),
    top: int = Query(15, ge=1, le=200),
    min_price: float | None = Query(None, ge=0, description="Ignore stocks priced below this"),
    max_price: float | None = Query(None, ge=0, description="Ignore stocks priced above this"),
    min_pct: float | None = Query(None, description="Ignore moves below this % (signed)"),
    max_pct: float | None = Query(None, description="Ignore moves above this % (signed)"),
    source: str | None = Query(None, description="live | simulated; defaults to the active feed"),
):
    d = _resolve_day(day)
    src = source or market_data.source.value
    as_of = _resolve_as_of(d, at)
    frm = _resolve_as_of(d, since)
    all_movers, origin = price_history.movers(d, src, as_of, frm)
    tracked_total = len(all_movers)

    # Filters run on the ranking, never on the store: the totals below are the
    # totals AFTER filtering, and `symbols_tracked` keeps the unfiltered count
    # so "25 of 38" cannot be mistaken for the size of the universe.
    def keep(m) -> bool:
        if min_price is not None and m.last_price < min_price:
            return False
        if max_price is not None and m.last_price > max_price:
            return False
        if min_pct is not None and m.pct_from_open < min_pct:
            return False
        if max_pct is not None and m.pct_from_open > max_pct:
            return False
        return True

    filtered = [m for m in all_movers if keep(m)]
    split = split_gainers_losers(filtered, top)
    return {
        "day": d.isoformat(),
        "as_of": at,
        "since": since,
        "window_start_ist": (frm.strftime("%H:%M") if frm else "09:15"),
        "window_end_ist": (as_of.strftime("%H:%M") if as_of else "15:30"),
        "source": src,
        "origin": origin,
        "resolution_min": all_movers[0].resolution_min if all_movers else None,
        "session": session_state(),
        "symbols_tracked": tracked_total,
        "symbols_after_filter": len(filtered),
        "filters": {
            "min_price": min_price, "max_price": max_price,
            "min_pct": min_pct, "max_pct": max_pct,
        },
        "price_range": (
            {"min": min(m.last_price for m in all_movers), "max": max(m.last_price for m in all_movers)}
            if all_movers else None
        ),
        "pct_range": (
            {"min": min(m.pct_from_open for m in all_movers), "max": max(m.pct_from_open for m in all_movers)}
            if all_movers else None
        ),
        "baseline_is_session_open": all(m.baseline_is_session_open for m in filtered) if filtered else True,
        "requested_top": top,
        "gainers_total": split["gainers_total"],
        "losers_total": split["losers_total"],
        "unchanged_total": split["unchanged_total"],
        "live_coverage": price_history.live_coverage(d, src),
        "empty_reason": (
            price_history.why_empty(d, src, as_of)
            if not all_movers
            else (
                f"{tracked_total} symbols have prices for this window, but none pass the "
                f"current price/percentage filters."
                if not filtered
                else None
            )
        ),
        "gainers": [_mover_dict(m) for m in split["gainers"]],
        "losers": [_mover_dict(m) for m in split["losers"]],
        "recorder": {"running": market_recorder.running, "last_run_at": market_recorder.status()["last_run_at"]},
    }


@router.get("/morning")
async def morning(
    day: str | None = Query(None),
    top: int = Query(15, ge=1, le=200),
    source: str | None = Query(None),
):
    """The ranking as it stood at the end of the morning window."""
    d = _resolve_day(day)
    src = source or market_data.source.value
    cutoff = morning_cutoff(d)
    all_movers, origin = price_history.movers(d, src, cutoff)
    split = split_gainers_losers(all_movers, top)
    return {
        "day": d.isoformat(),
        "cutoff_ist": cutoff.strftime("%H:%M"),
        "source": src,
        "origin": origin,
        "resolution_min": all_movers[0].resolution_min if all_movers else None,
        "symbols_tracked": tracked_total,
        "symbols_after_filter": len(filtered),
        "filters": {
            "min_price": min_price, "max_price": max_price,
            "min_pct": min_pct, "max_pct": max_pct,
        },
        "price_range": (
            {"min": min(m.last_price for m in all_movers), "max": max(m.last_price for m in all_movers)}
            if all_movers else None
        ),
        "pct_range": (
            {"min": min(m.pct_from_open for m in all_movers), "max": max(m.pct_from_open for m in all_movers)}
            if all_movers else None
        ),
        "requested_top": top,
        "gainers_total": split["gainers_total"],
        "losers_total": split["losers_total"],
        "unchanged_total": split["unchanged_total"],
        "gainers": [_mover_dict(m) for m in split["gainers"]],
        "losers": [_mover_dict(m) for m in split["losers"]],
    }


@router.get("/fast")
async def fast(
    day: str | None = Query(None),
    at: str | None = Query(None),
    window_min: int = Query(SPEED_WINDOW_MIN, ge=2, le=60),
    min_speed: float = Query(FAST_PCT_PER_MIN, ge=0.0),
    min_move: float = Query(MIN_MOVE_PCT, ge=0.0),
    source: str | None = Query(None),
    peak: bool = Query(False, description="Scan the whole session for each symbol's fastest window"),
    until: str | None = Query(None, description="HH:MM IST; bound the peak scan, e.g. 11:00"),
):
    d = _resolve_day(day)
    src = source or market_data.source.value
    if peak:
        movers_fast = peak_fast_movers(
            d, src, _resolve_as_of(d, until), window_min, min_speed, min_move
        )
    else:
        movers_fast = fast_movers(
            d, src, _resolve_as_of(d, at), window_min, min_speed, min_move
        )
    return {
        "day": d.isoformat(),
        "source": src,
        "peak": peak,
        "until": until,
        "window_min": window_min,
        "min_speed_pct_per_min": min_speed,
        "min_move_pct": min_move,
        "count": len(movers_fast),
        "movers": [{**f.as_dict(), "name": _company_name(f.symbol)} for f in movers_fast],
    }


# ---- price at a point in time -------------------------------------------


@router.get("/price-at")
async def price_at(
    symbol: str = Query(..., description="e.g. RELIANCE"),
    at: str = Query(..., description="HH:MM IST"),
    day: str | None = Query(None),
    source: str | None = Query(None),
    fetch: bool = Query(True, description="Fetch the day from Groww when it is not stored"),
):
    """What a stock was trading at, at a given minute of a given day.

    Falls back to the broker when neither record has the day, so a question
    about an unrecorded morning is answered rather than refused.
    """
    d = _resolve_day(day)
    src = source or market_data.source.value
    when = _resolve_as_of(d, at)
    point, note = await price_history.resolve_price(symbol.upper(), when, src, allow_fetch=fetch)
    if point is None:
        return {"found": False, "source": src, **note}
    return {"found": True, "day": d.isoformat(), "asked_for": at, "source": src,
            "fetched_now": bool(note.get("fetched")),
            "name": _company_name(point.symbol), **point.as_dict()}


@router.get("/series")
async def series(
    symbol: str = Query(...),
    day: str | None = Query(None),
    source: str | None = Query(None),
):
    d = _resolve_day(day)
    src = source or market_data.source.value
    points = snapshot_store.session_series(symbol.upper(), d, src)
    return {
        "symbol": symbol.upper(),
        "day": d.isoformat(),
        "source": src,
        "points": [
            {"ts": p.ts, "time_ist": p.time_ist, "price": round(p.price, 2),
             "pct_from_open": round(p.pct_from_open, 3) if p.pct_from_open is not None else None}
            for p in points
        ],
    }


@router.get("/days")
async def days(source: str | None = Query(None)):
    src = source or market_data.source.value
    return {
        "source": src,
        "days": [d.isoformat() for d in snapshot_store.recorded_days(src)],
        "stats": snapshot_store.stats(src),
        "available": price_history.available_days(src),
    }


# ---- alerts --------------------------------------------------------------


class AlertRequest(BaseModel):
    day: str | None = None
    window_min: int = SPEED_WINDOW_MIN
    min_speed: float = FAST_PCT_PER_MIN
    min_move: float = MIN_MOVE_PCT
    dry_run: bool = True


@router.post("/alerts/scan")
async def scan_and_alert(req: AlertRequest):
    """Find fast movers and notify about the ones not already alerted today.

    `dry_run` defaults to True: this returns what WOULD be sent without sending
    it, so the thresholds can be judged before anyone's phone is involved.
    """
    d = _resolve_day(req.day)
    src = market_data.source.value
    candidates = fast_movers(d, src, None, req.window_min, req.min_speed, req.min_move)

    fresh = [f for f in candidates if alert_ledger.should_send(f.symbol, f.direction, d)] \
        if not req.dry_run else \
        [f for f in candidates if (f.symbol, f.direction) not in alert_ledger.sent]

    results = []
    for f in fresh:
        message = format_alert(f)
        entry = {"symbol": f.symbol, "direction": f.direction, "message": message}
        if req.dry_run:
            entry["delivery"] = "DRY_RUN — nothing sent"
        else:
            outcome = await alert_notifier.send(message)
            entry["delivery"] = {
                "ok": outcome.ok,
                "provider": outcome.provider,
                "classification": outcome.classification,
                "delivery_confirmed": outcome.delivery_confirmed,
                "error": outcome.error,
                "skipped_reason": outcome.skipped_reason,
            }
            # In-app push regardless of WhatsApp: the browser channel works even
            # when the provider credential does not.
            await broadcaster.publish(
                "mover_alert",
                {"symbol": f.symbol, "direction": f.direction, **f.as_dict()},
            )
        results.append(entry)

    return {
        "day": d.isoformat(),
        "source": src,
        "dry_run": req.dry_run,
        "candidates": len(candidates),
        "alerted": len(results),
        "already_alerted_today": len(alert_ledger.sent),
        "results": results,
    }


@router.post("/alerts/reset")
async def reset_alerts():
    alert_ledger.reset()
    return {"reset": True}


# ---- tracked universe ----------------------------------------------------


@router.get("/universe")
async def universe():
    """Which symbols the live feed is currently polling, and so recording."""
    from app.research.cross_sectional import SECTOR_OF

    tracked = sorted(market_data.symbols)
    return {
        "tracked": tracked,
        "count": len(tracked),
        "research_universe_size": len(SECTOR_OF),
        "note": (
            "The recorder captures whatever the live feed polls. Widening the universe "
            "adds names to the same batched quote call rather than issuing extra requests."
        ),
    }


@router.post("/universe/widen")
async def widen_universe():
    """Track the full verified research universe, not just the bot's 20 names.

    'Which of all the stocks are going up' needs more than the strategy's own
    watchlist. The live feed batches its quote call, so the extra names cost one
    larger request rather than many more requests.
    """
    from app.research.cross_sectional import SECTOR_OF

    added = [s for s in sorted(SECTOR_OF) if market_data.add_symbol(s)]
    return {
        "added": added,
        "added_count": len(added),
        "tracked_now": len(market_data.symbols),
    }


# ---- fetching a whole day for the universe -------------------------------

# Progress for the day-fetch job. One at a time: 125 symbols against a rate
# limit the live quote loop shares is not something to run concurrently.
_fetch_job: dict = {"running": False, "day": None, "interval": None, "done": 0,
                    "total": 0, "stored": 0, "failures": [], "error": None}


class FetchDayRequest(BaseModel):
    day: str | None = None
    interval: str = "5m"


@router.get("/fetch-day/status")
async def fetch_day_status():
    return dict(_fetch_job)


@router.post("/fetch-day")
async def fetch_day(req: FetchDayRequest):
    """Pull a whole day for every tracked symbol, so the rankings have a universe.

    The interactive lookup fetches one symbol at a time, which is right when
    someone asks about one stock and wrong for a gainers table: a 125-name
    ranking needs 125 days of history, and firing those off one lookup at a
    time would take as long and report nothing while it ran.
    """
    import asyncio

    from app.research import ingestion, price_fetch
    from app.research.cross_sectional import SECTOR_OF
    from app.research.store import store as research_store

    if _fetch_job["running"]:
        raise HTTPException(409, "A day fetch is already running.")

    d = _resolve_day(req.day)
    blocked = price_fetch.fetch_blocked_reason("UNIVERSE", d, "live")
    if blocked:
        raise HTTPException(400, blocked)

    from app.research.universe_extra import movers_universe

    symbols = sorted(movers_universe() | set(research_store.symbols(req.interval, "live")))

    async def run():
        _fetch_job.update(running=True, day=d.isoformat(), interval=req.interval,
                          done=0, total=len(symbols), stored=0, failures=[], error=None)
        try:
            for symbol in symbols:
                try:
                    report = await ingestion.backfill(
                        [symbol], req.interval, d, d, price_fetch.broker_client(), "live", research_store
                    )
                    _fetch_job["stored"] += report.candles_fetched
                except Exception as exc:  # noqa: BLE001
                    _fetch_job["failures"].append(f"{symbol}: {str(exc)[:120]}")
                _fetch_job["done"] += 1
        except Exception as exc:  # noqa: BLE001
            _fetch_job["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            _fetch_job["running"] = False

    asyncio.create_task(run())
    return {"started": True, "day": d.isoformat(), "interval": req.interval,
            "symbols": len(symbols),
            "note": "Runs in the background; poll /api/movers/fetch-day/status."}
