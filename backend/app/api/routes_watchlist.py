from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import watchlist
from app.services.watchlist import WatchlistRejected
from app.strategies.scanner import NIFTY50_UNIVERSE

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class AddRequest(BaseModel):
    symbol: str


@router.get("")
async def get_watchlist():
    added = await watchlist.list_added()
    return {
        "core": sorted(NIFTY50_UNIVERSE),  # the bot's fixed strategy universe — always streaming
        "added": added,
    }


@router.post("/add")
async def add_symbol(body: AddRequest):
    try:
        return await watchlist.add(body.symbol)
    except WatchlistRejected as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc


@router.delete("/{symbol}")
async def remove_symbol(symbol: str):
    ok = await watchlist.remove(symbol)
    if not ok:
        raise HTTPException(
            404,
            f"'{symbol.upper()}' is not a removable watchlist entry — either it was never added, "
            "or it's one of the bot's core 20 symbols, which always stream.",
        )
    return {"removed": symbol.upper()}
