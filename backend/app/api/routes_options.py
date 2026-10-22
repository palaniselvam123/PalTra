"""Options page: Groww option chain and a practice (paper) options book.

Read-only on Groww: expiries and the option chain. Orders here are practice
fills in `OptionPaperTrade`; nothing on this router calls Groww's order API.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import get_session
from app.services import options
from app.services.options import OptionsError, Ticket

router = APIRouter(prefix="/api/options", tags=["options"])


@router.get("/underlyings")
async def underlyings():
    """Indices first, then every stock with options on Groww's instrument list."""
    stocks: list[str] = []
    error = None
    try:
        from app.services.instruments import instrument_master

        found = await asyncio.to_thread(instrument_master.option_underlyings)
        stocks = [s for s in found if s not in options.INDICES]
        if not stocks:
            stocks = await asyncio.to_thread(instrument_master.fno_stocks)
    except Exception as exc:  # noqa: BLE001 - the indices still work
        error = f"Could not load the stock list: {exc}"
    return {"indices": list(options.INDICES), "stocks": stocks, "error": error}


@router.get("/expiries")
async def expiries(underlying: str = Query(..., min_length=1, max_length=20)):
    return await options.expiries(underlying)


@router.get("/chain")
async def chain(
    underlying: str = Query(..., min_length=1, max_length=20),
    expiry: str = Query(..., min_length=10, max_length=10),
    force: bool = False,
):
    try:
        return await options.chain(underlying, expiry, force=force)
    except OptionsError as exc:
        raise HTTPException(400, str(exc)) from exc


class OrderBody(BaseModel):
    underlying: str = Field(min_length=1, max_length=20)
    expiry: str = Field(min_length=10, max_length=10)
    strike: float = Field(gt=0)
    option_type: str
    side: str
    lots: int = Field(ge=1, le=100)


@router.get("/book")
async def get_book(session: AsyncSession = Depends(get_session)):
    return await options.book(session)


@router.post("/order")
async def practice_order(body: OrderBody, session: AsyncSession = Depends(get_session)):
    """A practice fill at the chain's last price. Never a Groww order."""
    try:
        row = await options.place(session, Ticket(**body.model_dump()))
    except OptionsError as exc:
        raise HTTPException(409, str(exc)) from exc
    return options._row_out(row, row.entry_price)


@router.post("/book/{trade_id}/close")
async def close_practice(trade_id: int, session: AsyncSession = Depends(get_session)):
    try:
        row = await options.close(session, trade_id)
    except OptionsError as exc:
        raise HTTPException(409, str(exc)) from exc
    return options._row_out(row)
