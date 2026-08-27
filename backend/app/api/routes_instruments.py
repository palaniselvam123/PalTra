from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.services.instruments import instrument_master

router = APIRouter(prefix="/api/instruments", tags=["instruments"])


@router.get("/search")
async def search(
    q: str = Query(..., min_length=1),
    limit: int = Query(25, ge=1, le=100),
    equity_only: bool = Query(True),
):
    try:
        results = instrument_master.search(q, limit=limit, equity_only=equity_only)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Could not load the instrument list: {exc}") from exc
    return [
        {
            "symbol": i.symbol,
            "name": i.name,
            "series": i.series,
            "intraday_allowed": i.intraday_allowed,
        }
        for i in results
    ]


@router.get("/stats")
async def stats():
    return instrument_master.stats()
