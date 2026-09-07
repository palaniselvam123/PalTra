"""Persisted risk / virtual-money settings.

`RiskManager` is constructed from in-memory defaults. Saving in Settings only
wrote that object, so a restart silently restored ₹1,00,000 / 2% / 5 trades.
This module is the missing disk: load at startup, save on every edit, and
credit extra virtual money without resetting the other knobs.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import select

from app import state
from app.core.risk_manager import RiskConfig
from app.models.database import RiskSettings, async_session


def apply_row(config: RiskConfig, row: RiskSettings) -> None:
    config.account_capital = float(row.account_capital)
    config.risk_per_trade_pct = float(row.risk_per_trade_pct)
    config.daily_max_loss_pct = float(row.daily_max_loss_pct)
    config.daily_profit_target_pct = float(getattr(row, "daily_profit_target_pct", 0.0) or 0.0)
    config.max_trades_per_day = int(row.max_trades_per_day)
    config.max_spread_pct = float(row.max_spread_pct)
    config.max_leverage = float(getattr(row, "max_leverage", 5.0) or 5.0)
    config.min_edge_multiple = float(getattr(row, "min_edge_multiple", 1.5) or 1.5)
    try:
        hh, mm = (row.square_off_time_ist or "15:30").split(":")
        config.square_off_time_ist = dt.time(int(hh), int(mm))
    except ValueError:
        config.square_off_time_ist = dt.time(15, 30)


def snapshot(config: RiskConfig) -> dict:
    return {
        "account_capital": config.account_capital,
        "risk_per_trade_pct": config.risk_per_trade_pct,
        "daily_max_loss_pct": config.daily_max_loss_pct,
        "daily_profit_target_pct": config.daily_profit_target_pct,
        "max_trades_per_day": config.max_trades_per_day,
        "max_spread_pct": config.max_spread_pct,
        "max_leverage": config.max_leverage,
        "min_edge_multiple": config.min_edge_multiple,
        "square_off_time_ist": config.square_off_time_ist.strftime("%H:%M"),
    }


async def load_into_manager() -> bool:
    """Copy the saved row onto the live risk manager. Returns False if none."""
    async with async_session() as session:
        row = (await session.execute(select(RiskSettings).where(RiskSettings.id == 1))).scalar_one_or_none()
        if row is None:
            return False
        apply_row(state.risk_manager.config, row)
        return True


async def save_from_manager() -> None:
    cfg = state.risk_manager.config
    values = snapshot(cfg)
    async with async_session() as session:
        row = await session.get(RiskSettings, 1)
        if row is None:
            row = RiskSettings(id=1, **values)
            session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await session.commit()


async def add_virtual_money(amount: float) -> float:
    """Credits virtual capital. Other risk knobs are left as they were saved."""
    if amount <= 0:
        raise ValueError("Load amount must be positive")
    state.risk_manager.config.account_capital = round(
        state.risk_manager.config.account_capital + amount, 2
    )
    await save_from_manager()
    return state.risk_manager.config.account_capital
