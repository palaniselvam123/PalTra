"""Quarterly results: calendar, filed results, each company's numbers and verdict (app/services/results.py).

Read-only: nothing here places, closes or changes an order, and nothing in the strategy reads it.
"""
from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services import results

router = APIRouter(prefix="/api/results", tags=["results"])
_SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,25}$")


def _clean(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _SYMBOL.match(s):
        raise HTTPException(400, f"Not a stock symbol: {symbol!r}")
    return s


@router.get("/calendar")
async def calendar(days: int = Query(7, ge=1, le=30)) -> dict:
    return await results.calendar(days)


@router.get("/filed")
async def filed(limit: int = Query(200, ge=1, le=1000)) -> dict:
    return await results.filed(limit)


@router.get("/stock/{symbol}")
async def stock(symbol: str, force: bool = False) -> dict:
    return await results.stock(_clean(symbol), force=force)


class Many(BaseModel):
    symbols: list[str] = Field(default_factory=list, max_length=results.MAX_ANALYZE)
    force: bool = False


@router.post("/analyze")
async def analyze(body: Many) -> dict:
    symbols = [_clean(s) for s in body.symbols if s.strip()]
    if not symbols:
        raise HTTPException(400, "No stocks given")
    return {"rows": await results.many(symbols, force=body.force)}


@router.get("/probe")
async def probe(symbol: str = "ALKEM") -> dict:
    return await results.probe(_clean(symbol))
