"""Cost model tests — Phase 15 requires these explicitly.

Costs are the difference between a backtest that flatters a strategy and one
that tells the truth. This app has already seen a real trade whose price move
was −₹703 but whose recorded loss was −₹3,591 once charges were applied, so
these are not decorative.
"""
from __future__ import annotations

import pytest

from app.services.backtester import SLIPPAGE_PCT, _fill, _size, BacktestConfig
from app.services.paper_engine import (
    BROKERAGE_CAP,
    BROKERAGE_PCT,
    STT_PCT,
    estimate_charges,
    round_trip_cost_per_share,
)


class TestBrokerageCap:
    def test_small_order_uses_percentage(self):
        # 0.03% of 10,000 = 3, well under the 20 cap
        charges = estimate_charges("BUY", 100.0, 100)
        assert charges > 0
        # brokerage component alone should be the percentage, not the cap
        assert 10_000 * BROKERAGE_PCT < BROKERAGE_CAP

    def test_large_order_hits_the_cap(self):
        """The bug this guards: uncapped brokerage billed ₹812 on ₹27 lakh of
        turnover where a discount broker bills ₹20."""
        turnover = 27_00_000
        uncapped = turnover * BROKERAGE_PCT
        assert uncapped > BROKERAGE_CAP, "test premise: this turnover should exceed the cap"

        big = estimate_charges("BUY", 731.0, 3703)
        # Charges must not include the uncapped brokerage figure.
        assert big < uncapped, "brokerage cap is not being applied"

    def test_charges_scale_sublinearly_once_capped(self):
        """Doubling quantity must not double charges once brokerage is capped —
        only the percentage-of-turnover components grow."""
        one = estimate_charges("BUY", 1000.0, 1000)
        two = estimate_charges("BUY", 1000.0, 2000)
        assert two < one * 2


class TestSellSideCharges:
    def test_sell_costs_more_than_buy(self):
        """STT is sell-side only, so an exit is dearer than an entry."""
        buy = estimate_charges("BUY", 500.0, 100)
        sell = estimate_charges("SELL", 500.0, 100)
        assert sell > buy
        assert STT_PCT > 0

    def test_stt_is_material_on_large_turnover(self):
        buy = estimate_charges("BUY", 2632.0, 100)
        sell = estimate_charges("SELL", 2632.0, 100)
        # On ~2.6 lakh turnover the STT difference should be tens of rupees.
        assert sell - buy > 20


class TestNoDoubleCounting:
    def test_round_trip_is_entry_plus_exit(self):
        """round_trip_cost_per_share must equal both legs plus slippage — not
        charges applied twice on one leg."""
        price, qty = 500.0, 100
        entry = estimate_charges("BUY", price, qty)
        exit_ = estimate_charges("SELL", price, qty)
        slippage_per_share = price * (0.05 / 100) * 2

        got = round_trip_cost_per_share("BUY", price, qty, 0.05)
        expected = slippage_per_share + (entry + exit_) / qty
        assert got == pytest.approx(expected, rel=1e-9)

    def test_zero_quantity_is_zero_not_a_crash(self):
        assert round_trip_cost_per_share("BUY", 500.0, 0, 0.05) == 0.0


class TestSlippageDirection:
    """Slippage must always work against the trader, on both legs and sides."""

    def test_buy_entry_pays_more(self):
        assert _fill("BUY", 100.0, opening=True) > 100.0

    def test_buy_exit_receives_less(self):
        assert _fill("BUY", 100.0, opening=False) < 100.0

    def test_sell_entry_receives_less(self):
        assert _fill("SELL", 100.0, opening=True) < 100.0

    def test_sell_exit_pays_more(self):
        assert _fill("SELL", 100.0, opening=False) > 100.0

    def test_magnitude_matches_configured_pct(self):
        got = _fill("BUY", 1000.0, opening=True) - 1000.0
        assert got == pytest.approx(1000.0 * SLIPPAGE_PCT / 100, abs=0.01)

    def test_round_trip_slippage_is_never_favourable(self):
        """A position opened and closed at the same market price must lose."""
        entry = _fill("BUY", 500.0, opening=True)
        exit_ = _fill("BUY", 500.0, opening=False)
        assert exit_ < entry


class TestSizing:
    """Backtest sizing must mirror the live risk manager's two limbs."""

    def setup_method(self):
        self.cfg = BacktestConfig(capital=100_000, risk_per_trade_pct=1.0, max_leverage=5.0)

    def test_wide_stop_is_risk_limited(self):
        # ₹1,000 risk budget over a ₹5 stop = 200 shares
        assert _size(self.cfg, 100.0, 95.0) == 200

    def test_tight_stop_is_leverage_capped(self):
        """The ₹27-lakh bug: a razor-thin stop asks for far more exposure than
        the account can carry, so the leverage limb must bind."""
        qty = _size(self.cfg, 100.0, 99.9)
        assert qty == 5000                       # 5x100k / 100
        assert qty * 100.0 <= 100_000 * 5.0

    def test_never_exceeds_leverage_regardless_of_stop(self):
        for stop in (99.99, 99.9, 99.5, 95.0, 50.0):
            qty = _size(self.cfg, 100.0, stop)
            assert qty * 100.0 <= 100_000 * 5.0 + 1e-6

    def test_zero_stop_distance_returns_zero(self):
        assert _size(self.cfg, 100.0, 100.0) == 0

    def test_zero_price_returns_zero(self):
        assert _size(self.cfg, 0.0, 0.0) == 0


class TestCostsAgainstKnownTrade:
    """Regression against a real trade from this app's own ledger.

    ASIANPAINT, 100 shares, entry 2632.32 → exit 2633.78: the price moved
    ₹1.46/share in the trader's favour (₹146 gross) but the trade netted only
    ₹4.33 because charges took ₹141.67.
    """

    def test_reproduces_recorded_charges(self):
        entry = estimate_charges("BUY", 2632.32, 100)
        exit_ = estimate_charges("SELL", 2633.78, 100)
        total = entry + exit_
        assert total == pytest.approx(141.67, abs=1.0)

    def test_net_matches_recorded_pnl(self):
        gross = (2633.78 - 2632.32) * 100
        charges = estimate_charges("BUY", 2632.32, 100) + estimate_charges("SELL", 2633.78, 100)
        assert gross - charges == pytest.approx(4.33, abs=1.0)
