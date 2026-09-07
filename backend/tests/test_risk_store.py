"""Risk knobs must round-trip through the persisted row.

Settings used to write RAM only, so a restart restored ₹1,00,000 / 2% / 5
trades and looked like the page had 'reset'. apply_row / snapshot are the
pure mapping; the SQLite write is exercised by saving those fields.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from app.core.risk_manager import RiskConfig
from app.services.risk_store import apply_row, snapshot


def test_snapshot_then_apply_restores_capital_loss_and_trade_cap():
    cfg = RiskConfig(
        account_capital=250_000,
        risk_per_trade_pct=0.8,
        daily_max_loss_pct=3.5,
        daily_profit_target_pct=4.0,
        max_trades_per_day=9,
        max_spread_pct=0.2,
        max_leverage=4.0,
        min_edge_multiple=1.8,
        square_off_time_ist=dt.time(15, 20),
    )
    row = SimpleNamespace(**snapshot(cfg))
    fresh = RiskConfig()
    apply_row(fresh, row)  # type: ignore[arg-type]
    assert fresh.account_capital == 250_000
    assert fresh.daily_max_loss_pct == 3.5
    assert fresh.max_trades_per_day == 9
    assert fresh.risk_per_trade_pct == 0.8
    assert fresh.daily_profit_target_pct == 4.0
    assert fresh.max_leverage == 4.0
    assert fresh.square_off_time_ist == dt.time(15, 20)


def test_defaults_are_not_the_saved_values():
    """Guard: if this equals the saved fixture, the persist test is tautological."""
    defaults = RiskConfig()
    assert defaults.account_capital == 100_000
    assert defaults.daily_max_loss_pct == 2.0
    assert defaults.max_trades_per_day == 5
