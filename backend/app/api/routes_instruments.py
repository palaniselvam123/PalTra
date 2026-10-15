from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from app.services.groww_links import groww_page
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


_NSE_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9&_-]{0,19}")


@router.get("/groww/{symbol}")
async def open_on_groww(symbol: str):
    """Sends the browser to the stock's page on groww.in (the Scalp page's stock links)."""
    name = symbol.strip().upper()
    if not _NSE_SYMBOL.fullmatch(name):
        raise HTTPException(400, f"{symbol!r} is not an NSE trading symbol.")
    return RedirectResponse(await groww_page(name), status_code=302)
