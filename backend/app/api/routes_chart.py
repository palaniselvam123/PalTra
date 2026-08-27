"""Charting API: OHLCV history, indicators, and trade markers.

Indicators are computed here rather than in the browser on purpose. The entry
gate rejects breakouts on a Python ADX; if the chart drew its own JavaScript
ADX the two would eventually disagree, and the chart would be quietly
misrepresenting why a trade did or didn't happen.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.models.database import Trade, async_session
from app.services import indicators as ind
from app.services import patterns as candle_patterns
from app.services.backfill import ensure_backfilled
from app.services.candle_store import INTERVALS, candle_store
from app.services.market_data import market_data

router = APIRouter(prefix="/api/chart", tags=["chart"])

@router.get("/candles")
async def candles(
    symbol: str = Query(...),
    interval: str = Query("5m"),
    limit: int = Query(500, ge=10, le=1500),
    indicators: str = Query("", description="Comma-separated, e.g. ema_fast,vwap,rsi"),
):
    symbol = symbol.upper()
    if interval not in INTERVALS:
        raise HTTPException(400, f"interval must be one of {list(INTERVALS)}")

    source = market_data.source.value
    note = await ensure_backfilled(symbol, interval)
    bars = candle_store.get(symbol, interval, source, limit)

    requested = [n for n in indicators.split(",") if n.strip()]
    computed = ind.compute(bars, requested) if bars and requested else {}

    return {
        "symbol": symbol,
        "interval": interval,
        "candles": [
            {"time": b.ts, "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
            for b in bars
        ],
        "indicators": computed,
        "note": note,
        "source": market_data.source.value,
    }


@router.get("/markers")
async def markers(symbol: str = Query(...), limit: int = Query(200, ge=1, le=1000)):
    """Entry and exit points for this symbol, so the chart shows where trades
    actually happened rather than leaving you to correlate a table by eye.
    """
    symbol = symbol.upper()
    async with async_session() as session:
        rows = (
            await session.execute(
                select(Trade).where(Trade.symbol == symbol).order_by(Trade.id.desc()).limit(limit)
            )
        ).scalars().all()

    out = []
    for t in rows:
        if t.status == "CANCELLED":
            continue  # voided: no real outcome to plot
        if t.opened_at:
            out.append(
                {
                    "time": int(t.opened_at.timestamp()),
                    "kind": "ENTRY",
                    "side": t.side,
                    "price": t.entry_price,
                    "quantity": t.quantity,
                    "account": t.account or "AUTO",
                    "source": t.source or "MANUAL",
                    "trade_id": t.id,
                    "pnl": None,
                }
            )
        if t.status == "CLOSED" and t.closed_at and t.exit_price is not None:
            out.append(
                {
                    "time": int(t.closed_at.timestamp()),
                    "kind": "EXIT",
                    "side": "SELL" if t.side == "BUY" else "BUY",
                    "price": t.exit_price,
                    "quantity": t.quantity,
                    "account": t.account or "AUTO",
                    "source": t.source or "MANUAL",
                    "trade_id": t.id,
                    "pnl": t.pnl,
                    "exit_reason": t.exit_reason,
                }
            )
    out.sort(key=lambda m: m["time"])
    return out


@router.get("/patterns")
async def chart_patterns(
    symbol: str = Query(...),
    interval: str = Query("5m"),
    limit: int = Query(400, ge=10, le=1500),
):
    """Candlestick patterns detected across the visible bars, for chart
    markers. The forming bar is excluded — its shape is still changing, so
    labelling it would show a pattern that can disappear on the next tick.
    """
    symbol = symbol.upper()
    if interval not in INTERVALS:
        raise HTTPException(400, f"interval must be one of {list(INTERVALS)}")

    source = market_data.source.value
    note = await ensure_backfilled(symbol, interval)
    bars = candle_store.get(symbol, interval, source, limit)
    closed = bars[:-1] if bars else []

    hits = candle_patterns.detect_all(closed)
    return {
        "symbol": symbol,
        "interval": interval,
        "note": note,
        "patterns": [
            {
                "time": h.ts,
                "name": h.name,
                "label": h.label,
                "bias": h.bias,
                "close": round(h.close, 2),
                "trend": h.trend,
                "note": h.note,
            }
            for h in hits
        ],
    }
