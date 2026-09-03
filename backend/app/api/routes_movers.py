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
from app.research.snapshots import ist_date, snapshot_store
from app.services.alert_notifier import alert_notifier
from app.services.broadcaster import broadcaster
from app.services.market_data import market_data
from app.services.market_recorder import market_recorder
from app.services.movers import (
    FAST_PCT_PER_MIN, MIN_MOVE_PCT, SPEED_WINDOW_MIN, alert_ledger, fast_movers,
    format_alert, morning_cutoff, ranked_movers, split_gainers_losers,
)

router = APIRouter(prefix="/api/movers", tags=["movers"])


def _mover_dict(m) -> dict:
    return {
        "symbol": m.symbol,
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
    at: str | None = Query(None, description="HH:MM IST; ranks as it stood at that moment"),
    top: int = Query(15, ge=1, le=100),
    source: str | None = Query(None, description="live | simulated; defaults to the active feed"),
):
    d = _resolve_day(day)
    src = source or market_data.source.value
    as_of = _resolve_as_of(d, at)
    all_movers = ranked_movers(d, src, as_of)
    split = split_gainers_losers(all_movers, top)
    return {
        "day": d.isoformat(),
        "as_of": at,
        "source": src,
        "session": session_state(),
        "symbols_tracked": len(all_movers),
        "baseline_is_session_open": all(m.baseline_is_session_open for m in all_movers) if all_movers else True,
        "gainers": [_mover_dict(m) for m in split["gainers"]],
        "losers": [_mover_dict(m) for m in split["losers"]],
        "recorder": {"running": market_recorder.running, "last_run_at": market_recorder.status()["last_run_at"]},
    }


@router.get("/morning")
async def morning(
    day: str | None = Query(None),
    top: int = Query(15, ge=1, le=100),
    source: str | None = Query(None),
):
    """The ranking as it stood at the end of the morning window."""
    d = _resolve_day(day)
    src = source or market_data.source.value
    cutoff = morning_cutoff(d)
    all_movers = ranked_movers(d, src, cutoff)
    split = split_gainers_losers(all_movers, top)
    return {
        "day": d.isoformat(),
        "cutoff_ist": cutoff.strftime("%H:%M"),
        "source": src,
        "symbols_tracked": len(all_movers),
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
):
    d = _resolve_day(day)
    src = source or market_data.source.value
    movers_fast = fast_movers(
        d, src, _resolve_as_of(d, at), window_min, min_speed, min_move
    )
    return {
        "day": d.isoformat(),
        "source": src,
        "window_min": window_min,
        "min_speed_pct_per_min": min_speed,
        "min_move_pct": min_move,
        "count": len(movers_fast),
        "movers": [f.as_dict() for f in movers_fast],
    }


# ---- price at a point in time -------------------------------------------


@router.get("/price-at")
async def price_at(
    symbol: str = Query(..., description="e.g. RELIANCE"),
    at: str = Query(..., description="HH:MM IST"),
    day: str | None = Query(None),
    source: str | None = Query(None),
):
    """What a stock was trading at, at a given minute of a given day."""
    d = _resolve_day(day)
    src = source or market_data.source.value
    when = _resolve_as_of(d, at)
    point = snapshot_store.price_at(symbol.upper(), when, src)
    if point is None:
        return {
            "found": False,
            "symbol": symbol.upper(),
            "day": d.isoformat(),
            "asked_for": at,
            "source": src,
            "reason": (
                "No price was recorded for that symbol within 15 minutes before that time. "
                "Either the recorder was not running, or the symbol is not in the tracked universe."
            ),
        }
    return {
        "found": True,
        "symbol": point.symbol,
        "day": d.isoformat(),
        "asked_for": at,
        "recorded_at_ist": point.time_ist,
        "price": round(point.price, 2),
        "open_price": round(point.open_price, 2) if point.open_price else None,
        "pct_from_open": round(point.pct_from_open, 3) if point.pct_from_open is not None else None,
        "source": src,
    }


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
