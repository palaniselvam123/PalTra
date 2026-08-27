"""Process-wide shared state: current trading mode, the risk manager, the
paper engine, and the latest quote cache fed by the tick stream. Kept in one
module (not a class per route file) so main.py's background tasks and every
router see the same objects.
"""
from __future__ import annotations

from app.core.risk_manager import DailyRiskState, RiskConfig, RiskManager
from app.services.paper_engine import PaperEngine

mode: str = "paper"  # "paper" | "live" — Paper is the hardcoded startup default
kill_switch_active: bool = False

risk_manager = RiskManager(RiskConfig(), DailyRiskState())

# Two independent virtual wallets, deliberately not sharing an engine:
#   AUTO   — the dashboard/strategy account, risk-gated and 1%-sized
#   MANUAL — the discretionary trading desk, where you pick the quantity
# Separate engines mean a position in one cannot be closed or double-counted
# by the other, and each keeps its own balance and P&L history.
paper_engine = PaperEngine()
manual_engine = PaperEngine()
manual_capital: float = 100_000.0

ACCOUNT_AUTO = "AUTO"
ACCOUNT_MANUAL = "MANUAL"


def engine_for(account: str) -> PaperEngine:
    return manual_engine if account == ACCOUNT_MANUAL else paper_engine


def capital_for(account: str) -> float:
    return manual_capital if account == ACCOUNT_MANUAL else risk_manager.config.account_capital

latest_quotes: dict[str, dict] = {}  # symbol -> {ltp, bid, ask, volume}
