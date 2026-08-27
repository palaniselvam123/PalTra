from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app import state
from app.services.broadcaster import broadcaster
from app.services.execution import EntryRejected, place_paper_entry
from app.services.safety import reset_kill_switch as _reset_kill_switch, trigger_kill_switch
from app.services.trade_ledger import (
    close_and_settle,
    get_account_summary,
    get_trade_history,
    get_trade_summary,
)

router = APIRouter(prefix="/api/orders", tags=["orders"])


class PlaceOrderRequest(BaseModel):
    symbol: str
    side: str  # BUY | SELL
    entry_price: float
    stop_loss: float
    target: float


class ModeRequest(BaseModel):
    mode: str  # paper | live


@router.get("/mode")
async def get_mode():
    return {"mode": state.mode, "kill_switch_active": state.kill_switch_active}


@router.post("/mode")
async def set_mode(body: ModeRequest):
    if body.mode not in ("paper", "live"):
        raise HTTPException(400, "mode must be 'paper' or 'live'")
    if state.kill_switch_active:
        raise HTTPException(423, "Kill switch is active — restart the platform to change modes.")
    state.mode = body.mode
    await broadcaster.publish("log", {"level": "WARN", "message": f"Trading mode switched to {body.mode.upper()}"})
    return {"mode": state.mode}


@router.post("/place")
async def place_order(body: PlaceOrderRequest):
    """Manual order entry. Goes through the exact same risk-gated path the
    bot uses — see app/services/execution.py.
    """
    try:
        entry = await place_paper_entry(
            symbol=body.symbol,
            side=body.side,
            entry_price=body.entry_price,
            stop_loss=body.stop_loss,
            target=body.target,
            source="MANUAL",
        )
    except EntryRejected as exc:
        raise HTTPException(exc.status_code, exc.reason) from exc

    return {
        "order": {"broker_order_id": entry.order_id, "status": "FILLED", "filled_price": entry.filled_price},
        "fill": {
            "order_id": entry.order_id,
            "symbol": entry.symbol,
            "side": entry.side,
            "quantity": entry.quantity,
            "filled_price": entry.filled_price,
            "charges": entry.charges,
        },
    }


class RiskConfigRequest(BaseModel):
    account_capital: float
    risk_per_trade_pct: float
    daily_max_loss_pct: float
    daily_profit_target_pct: float = 0.0
    max_trades_per_day: int
    max_spread_pct: float
    max_leverage: float = 5.0
    min_edge_multiple: float = 1.5


@router.get("/risk-config")
async def get_risk_config():
    c = state.risk_manager.config
    return {
        "account_capital": c.account_capital,
        "risk_per_trade_pct": c.risk_per_trade_pct,
        "daily_max_loss_pct": c.daily_max_loss_pct,
        "daily_profit_target_pct": c.daily_profit_target_pct,
        "max_trades_per_day": c.max_trades_per_day,
        "max_spread_pct": c.max_spread_pct,
        "max_leverage": c.max_leverage,
        "min_edge_multiple": c.min_edge_multiple,
        "max_position_value": round(c.account_capital * c.max_leverage, 2),
        "square_off_time_ist": c.square_off_time_ist.strftime("%H:%M"),
        # Rupee equivalents so the UI never makes the user do the percentage math.
        "risk_per_trade_value": round(c.account_capital * c.risk_per_trade_pct / 100, 2),
        "daily_max_loss_value": round(c.daily_max_loss_value, 2),
        "daily_profit_target_value": round(c.daily_profit_target_value, 2),
        "state": {
            "trades_taken": state.risk_manager.state.trades_taken,
            "realized_pnl": state.risk_manager.state.realized_pnl,
            "locked": state.risk_manager.state.locked,
            "lock_reason": state.risk_manager.state.lock_reason,
            "lock_kind": state.risk_manager.state.lock_kind,
        },
    }


@router.post("/risk-config")
async def set_risk_config(body: RiskConfigRequest):
    c = state.risk_manager.config
    c.account_capital = body.account_capital
    c.risk_per_trade_pct = body.risk_per_trade_pct
    c.daily_max_loss_pct = body.daily_max_loss_pct
    c.daily_profit_target_pct = body.daily_profit_target_pct
    c.max_trades_per_day = body.max_trades_per_day
    c.max_spread_pct = body.max_spread_pct
    c.max_leverage = body.max_leverage
    c.min_edge_multiple = body.min_edge_multiple
    return {"ok": True}


@router.get("/positions")
async def get_positions():
    return [p.__dict__ for p in state.paper_engine.positions.values()]


@router.post("/close/{symbol}")
async def close_position(symbol: str):
    result, breaker = await close_and_settle(symbol, "MANUAL CLOSE")
    if result is None:
        raise HTTPException(404, f"No open position in {symbol} (or no live quote yet).")

    if breaker is not None:
        await trigger_kill_switch(breaker.reason)

    return {"symbol": symbol, "pnl": result.pnl}


@router.get("/summary")
async def order_summary():
    return await get_trade_summary()


@router.get("/account")
async def account_summary():
    """Virtual wallet: balance, equity, exposure."""
    return await get_account_summary()


@router.get("/history")
async def order_history():
    return await get_trade_history()


@router.post("/kill-switch")
async def kill_switch():
    """Manual emergency kill switch: cancels intent to trade and squares off
    every open paper (or, once live orders are enabled, live) position at
    market.
    """
    await trigger_kill_switch("Manual kill switch activated by user")
    return {"active": True}


@router.post("/kill-switch/reset")
async def reset_kill_switch():
    await _reset_kill_switch()
    return {"active": False}
