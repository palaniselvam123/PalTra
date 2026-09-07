"""Zero-tolerance risk engine. Every order — paper or live — must pass
`RiskManager.validate_order()` before it is dispatched. Any failed check
blocks the order; the circuit breaker additionally locks the whole platform
for the rest of the trading day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum

from app.core.market_clock import ist_now


class RiskCheckResult(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass
class RiskDecision:
    result: RiskCheckResult
    reason: str = ""
    sized_quantity: int | None = None


@dataclass
class RiskConfig:
    account_capital: float = 100_000.0
    risk_per_trade_pct: float = 1.0        # 1% rule
    daily_max_loss_pct: float = 2.0        # loss circuit breaker
    daily_profit_target_pct: float = 0.0   # 0 disables the profit lock
    max_trades_per_day: int = 5
    max_spread_pct: float = 0.15
    square_off_time_ist: dt.time = field(default_factory=lambda: dt.time(15, 30))
    # Ceiling on position value as a multiple of capital. The 1% rule alone
    # sizes purely on stop distance and never asks what the position costs, so
    # a razor-thin stop produced 27x leverage on a 1 lakh account — an order no
    # broker would accept. Real MIS equity leverage is around 5x.
    max_leverage: float = 5.0
    # A target must beat round-trip costs by this multiple to be worth taking.
    # At 1.0 a winning trade merely breaks even, so the default leaves margin.
    min_edge_multiple: float = 1.5

    @property
    def daily_max_loss_value(self) -> float:
        """The loss limit in rupees — negative."""
        return -abs(self.account_capital * (self.daily_max_loss_pct / 100))

    @property
    def daily_profit_target_value(self) -> float:
        """The profit target in rupees. Zero means no target is set."""
        return abs(self.account_capital * (self.daily_profit_target_pct / 100))


@dataclass
class DailyRiskState:
    trade_date: dt.date = field(default_factory=lambda: ist_now().date())
    trades_taken: int = 0
    realized_pnl: float = 0.0
    locked: bool = False
    lock_reason: str = ""
    # Why trading stopped: "LOSS_LIMIT" (bad) or "PROFIT_TARGET" (good).
    # Both halt the day, but they mean opposite things to the user.
    lock_kind: str = ""

    def reset_if_new_day(self) -> None:
        today = ist_now().date()
        if self.trade_date != today:
            self.trade_date = today
            self.trades_taken = 0
            self.realized_pnl = 0.0
            self.locked = False
            self.lock_reason = ""
            self.lock_kind = ""


class RiskManager:
    """Stateful, per-process risk gate. Backed by DailyRiskState which should
    be persisted (see models.database.RiskState) so a restart doesn't reset
    the day's loss/trade counters.
    """

    def __init__(self, config: RiskConfig, state: DailyRiskState | None = None):
        self.config = config
        self.state = state or DailyRiskState()

    def position_size(self, entry_price: float, stop_loss_price: float) -> int:
        """The 1% rule, bounded by what the account can actually carry.

        Risking a fixed rupee amount over the stop distance answers "how many
        shares before I lose my budget", but that formula contains no price
        term at all — it has no idea what the position costs. A 27-paisa stop
        on a 1 lakh account asks for 3,703 shares, which at 731 rupees is 27
        lakh of exposure. So the risk-based size is capped by the leverage
        limit, and the smaller of the two wins.
        """
        risk_amount = self.config.account_capital * (self.config.risk_per_trade_pct / 100)
        per_share_risk = abs(entry_price - stop_loss_price)
        if per_share_risk <= 0 or entry_price <= 0:
            return 0

        by_risk = int(risk_amount // per_share_risk)
        max_position_value = self.config.account_capital * self.config.max_leverage
        by_exposure = int(max_position_value // entry_price)
        return max(min(by_risk, by_exposure), 0)

    def exposure_capped(self, entry_price: float, stop_loss_price: float) -> bool:
        """True when the leverage ceiling — not the risk rule — set the size.
        Worth surfacing: it means the trade carries less than the configured
        risk, because the stop is unusually tight for this price.
        """
        risk_amount = self.config.account_capital * (self.config.risk_per_trade_pct / 100)
        per_share_risk = abs(entry_price - stop_loss_price)
        if per_share_risk <= 0 or entry_price <= 0:
            return False
        by_risk = int(risk_amount // per_share_risk)
        by_exposure = int((self.config.account_capital * self.config.max_leverage) // entry_price)
        return by_exposure < by_risk

    def min_edge_check(
        self, entry_price: float, target_price: float, round_trip_cost_per_share: float
    ) -> tuple[bool, str]:
        """Rejects a target that cannot clear its own costs.

        A trade whose target is nearer than the slippage and charges it will
        pay books a loss even when the target is hit exactly — which is not a
        risk to be managed but an arithmetic certainty to be refused.
        """
        target_distance = abs(target_price - entry_price)
        required = round_trip_cost_per_share * self.config.min_edge_multiple
        if target_distance < required:
            return False, (
                f"Target is only ₹{target_distance:.2f} away but round-trip costs are "
                f"₹{round_trip_cost_per_share:.2f}/share — the trade loses money even if the target is hit "
                f"exactly. Needs ₹{required:.2f} ({self.config.min_edge_multiple}x costs) to be worth taking."
            )
        return True, ""

    def is_past_square_off(self, now: dt.time | None = None) -> bool:
        now = now or ist_now().time()
        return now >= self.config.square_off_time_ist

    def spread_pct(self, bid: float, ask: float) -> float:
        if bid <= 0 or ask <= 0:
            return 100.0
        mid = (bid + ask) / 2
        return ((ask - bid) / mid) * 100

    def validate_order(
        self,
        *,
        entry_price: float,
        stop_loss_price: float,
        bid: float,
        ask: float,
        enforce_session_cutoff: bool = True,
        fixed_quantity: int | None = None,
    ) -> RiskDecision:
        """`enforce_session_cutoff` is switched off only when running against
        the synthetic feed, where there is no real NSE session for the cut-off
        rule to refer to. On live market data it is always enforced.
        """
        self.state.reset_if_new_day()

        if self.state.locked:
            return RiskDecision(RiskCheckResult.REJECTED, self.state.lock_reason)

        if enforce_session_cutoff and self.is_past_square_off():
            cutoff = self.config.square_off_time_ist.strftime("%H:%M")
            return RiskDecision(RiskCheckResult.REJECTED, f"Past {cutoff} IST hard cut-off — no new entries")

        if self.state.trades_taken >= self.config.max_trades_per_day:
            return RiskDecision(
                RiskCheckResult.REJECTED,
                f"Daily trade cap reached ({self.config.max_trades_per_day})",
            )

        spread = self.spread_pct(bid, ask)
        if spread > self.config.max_spread_pct:
            return RiskDecision(
                RiskCheckResult.REJECTED,
                f"Spread {spread:.3f}% exceeds guard threshold {self.config.max_spread_pct}%",
            )

        # `fixed_quantity` replaces the 1% sizing step ONLY. The kill switch,
        # day lock, session cut-off, trade cap, spread guard, loss limit and
        # profit target all still apply, and an explicit size is still held to
        # the leverage ceiling. Sizing is the one rule a deliberate allocation
        # strategy has to override; the rest exist to stop the day running
        # away, and nothing gets to bypass those.
        if fixed_quantity is not None:
            qty = int(fixed_quantity)
            if qty <= 0:
                return RiskDecision(RiskCheckResult.REJECTED, "Allocation produced a zero-share order")
            max_shares = int((self.config.account_capital * self.config.max_leverage) // entry_price)
            if qty > max_shares:
                return RiskDecision(
                    RiskCheckResult.REJECTED,
                    f"{qty} shares at {entry_price} exceeds the "
                    f"{self.config.max_leverage}x leverage ceiling ({max_shares} max)",
                )
        else:
            qty = self.position_size(entry_price, stop_loss_price)
            if qty <= 0:
                return RiskDecision(RiskCheckResult.REJECTED, "Computed position size is zero (entry == stop-loss?)")

        if self.state.realized_pnl <= self.config.daily_max_loss_value:
            self._trip_loss_limit()
            return RiskDecision(RiskCheckResult.REJECTED, self.state.lock_reason)

        target = self.config.daily_profit_target_value
        if target > 0 and self.state.realized_pnl >= target:
            self._trip_profit_target()
            return RiskDecision(RiskCheckResult.REJECTED, self.state.lock_reason)

        return RiskDecision(RiskCheckResult.APPROVED, sized_quantity=qty)

    def record_fill(self) -> None:
        self.state.trades_taken += 1

    def record_realized_pnl(self, pnl_delta: float) -> RiskDecision | None:
        """Call after every position close. Returns a decision if this close
        ends the trading day — either by breaching the loss limit or by
        reaching the profit target. Either way the caller must square off
        what is left and stop taking entries.
        """
        self.state.reset_if_new_day()
        self.state.realized_pnl += pnl_delta

        if self.state.locked:
            return None

        if self.state.realized_pnl <= self.config.daily_max_loss_value:
            self._trip_loss_limit()
            return RiskDecision(RiskCheckResult.REJECTED, self.state.lock_reason)

        target = self.config.daily_profit_target_value
        if target > 0 and self.state.realized_pnl >= target:
            self._trip_profit_target()
            return RiskDecision(RiskCheckResult.REJECTED, self.state.lock_reason)

        return None

    def reconcile_lock(self) -> bool:
        """Re-check a day lock against the CURRENT limits.

        The lock is a latch: once tripped it stays set until the next trading
        day. That is right while the limits are fixed, but it means raising
        `daily_max_loss_pct` after a breach leaves the platform locked against
        a threshold that no longer exists — the operator changes the setting,
        nothing happens, and the bot refuses to start with a message quoting
        the old number.

        So after a limits change, a lock whose condition is no longer true is
        released. A lock that still breaches the new limit stays exactly where
        it is. Returns True if the lock was released.
        """
        if not self.state.locked:
            return False

        if self.state.lock_kind == "LOSS_LIMIT":
            if self.state.realized_pnl > self.config.daily_max_loss_value:
                self._clear_lock()
                return True
        elif self.state.lock_kind == "PROFIT_TARGET":
            target = self.config.daily_profit_target_value
            if target <= 0 or self.state.realized_pnl < target:
                self._clear_lock()
                return True
        return False

    def _clear_lock(self) -> None:
        self.state.locked = False
        self.state.lock_reason = ""
        self.state.lock_kind = ""

    def release_lock(self) -> None:
        """Operator override — clears the day lock regardless of P&L.

        Deliberately separate from `reconcile_lock`: this one discards the
        safety stop on the operator's say-so, so the caller is expected to
        make that explicit to the user rather than doing it silently.
        """
        self._clear_lock()

    def _trip_loss_limit(self) -> None:
        self.state.locked = True
        self.state.lock_kind = "LOSS_LIMIT"
        self.state.lock_reason = (
            f"Daily loss limit hit: ₹{self.state.realized_pnl:.2f} breached the "
            f"₹{self.config.daily_max_loss_value:.0f} cap "
            f"({self.config.daily_max_loss_pct}% of capital). Trading is locked for the rest of the day."
        )

    def _trip_profit_target(self) -> None:
        self.state.locked = True
        self.state.lock_kind = "PROFIT_TARGET"
        self.state.lock_reason = (
            f"Daily profit target reached: ₹{self.state.realized_pnl:.2f} "
            f"met the ₹{self.config.daily_profit_target_value:.0f} goal "
            f"({self.config.daily_profit_target_pct}% of capital). Stopping to lock in the day's gain."
        )

    # Kept for callers that referred to the old name.
    _trip_circuit_breaker = _trip_loss_limit
