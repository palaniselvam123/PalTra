from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import state
from app.services import manual_desk
from app.services.manual_desk import ManualOrderRejected
from app.services.trade_ledger import get_trade_history, get_trade_summary

router = APIRouter(prefix="/api/manual", tags=["manual"])


class PlaceRequest(BaseModel):
    symbol: str
    side: str  # BUY | SELL
    quantity: int = Field(ge=1)
    stop_loss: float | None = None
    target: float | None = None


class PreviewRequest(BaseModel):
    symbol: str
    side: str
    quantity: int = Field(ge=1)


class CapitalRequest(BaseModel):
    starting_capital: float = Field(gt=0)


@router.get("/watchlist")
async def watchlist():
    return manual_desk.watchlist()


@router.get("/account")
async def account():
    return await manual_desk.margin_snapshot()


@router.get("/positions")
async def positions():
    return manual_desk.positions()


@router.get("/summary")
async def summary():
    return await get_trade_summary(account=state.ACCOUNT_MANUAL)


@router.get("/history")
async def history(limit: int = 100):
    # Voided positions are shown here — they were real orders, and dropping the
    # row would hide that a position was opened at all.
    return await get_trade_history(limit=limit, account=state.ACCOUNT_MANUAL, include_cancelled=True)


@router.post("/preview")
async def preview(body: PreviewRequest):
    try:
        return manual_desk.preview(body.symbol.upper(), body.side.upper(), body.quantity)
    except ManualOrderRejected as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc


@router.post("/place")
async def place(body: PlaceRequest):
    try:
        fill = await manual_desk.place(
            symbol=body.symbol.upper(),
            side=body.side.upper(),
            quantity=body.quantity,
            stop_loss=body.stop_loss,
            target=body.target,
        )
    except ManualOrderRejected as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc
    return {
        "order_id": fill.order_id,
        "symbol": fill.symbol,
        "side": fill.side,
        "quantity": fill.quantity,
        "filled_price": fill.filled_price,
        "charges": fill.charges,
        "trade_id": fill.trade_id,
    }


@router.post("/close/{symbol}")
async def close(symbol: str):
    try:
        return await manual_desk.close(symbol.upper())
    except ManualOrderRejected as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc


@router.post("/square-off-all")
async def square_off_all():
    return {"closed": await manual_desk.square_off_all()}


@router.post("/capital")
async def set_capital(body: CapitalRequest):
    """Resets the desk's starting capital. Balance is starting capital plus
    realised P&L, so this shifts the baseline the desk is measured against —
    it does not erase trade history.
    """
    state.manual_capital = body.starting_capital
    return await manual_desk.margin_snapshot()
