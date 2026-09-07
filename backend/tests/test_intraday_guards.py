"""Guards that stop the scanner/gainers bot from trading noise as if it were a signal.

Today's book is the spec: EMA2/EMA3 on a forming bar, leftover buying-power
dumped into one name, and a 20%/35% bracket on a same-day square-off produced
a −₹40k session of 40-second round-trips. These tests pin the arithmetic that
must not regress.
"""
from __future__ import annotations

import datetime as dt

from app.strategies.gainer_momentum import (
    GainerConfig,
    cap_to_risk,
    even_slot_budget,
    ma_pair_is_noise,
    past_last_entry,
    plan_entry,
)


class TestEvenSlotBudget:
    def test_does_not_dump_leftover_into_last_slot(self):
        """Four tiny names leave almost the whole book. The last slot must
        still only get an even 1/N share, not the leftover 4.9 lakh.
        """
        leftover = even_slot_budget(
            balance=100_000,
            leverage=5.0,
            allocation_pct=100.0,
            deployed=10_000,
            max_positions=5,
            open_count=4,
        )
        remaining = 100_000 * 5.0 - 10_000
        assert remaining == 490_000
        assert leftover == 100_000
        assert leftover < remaining

    def test_first_name_cannot_take_the_whole_book(self):
        slot = even_slot_budget(
            balance=100_000,
            leverage=5.0,
            allocation_pct=50.0,
            deployed=0.0,
            max_positions=3,
            open_count=0,
        )
        total = 100_000 * 5.0 * 0.5  # 2.5 lakh
        assert slot == total / 3
        assert slot < total

    def test_full_book_is_zero(self):
        assert (
            even_slot_budget(
                balance=100_000,
                leverage=5.0,
                allocation_pct=50.0,
                deployed=250_000,
                max_positions=3,
                open_count=3,
            )
            == 0.0
        )


class TestMaPair:
    def test_ema2_ema3_is_noise(self):
        assert ma_pair_is_noise(2, 3)

    def test_ema9_ema21_is_tradable(self):
        assert not ma_pair_is_noise(9, 21)

    def test_gap_of_four_is_still_noise(self):
        assert ma_pair_is_noise(9, 13)


class TestLastEntry:
    def test_blocks_the_last_45_minutes(self):
        square = dt.time(15, 30)
        assert past_last_entry(dt.time(15, 27), square, 45) is True
        assert past_last_entry(dt.time(14, 44), square, 45) is False
        assert past_last_entry(dt.time(14, 45), square, 45) is True


class TestRiskCap:
    def test_allocation_cannot_outrun_the_one_percent_rule(self):
        cfg = GainerConfig(stop_loss_pct=1.2, target_pct=2.4, min_order_value=1.0)
        # 5 lakh budget at ₹318 would buy ~1,500 shares without a cap.
        signal = plan_entry(symbol="LALITHAA", price=318.0, pct_from_open=0.0, budget=500_000, config=cfg)
        assert signal is not None
        assert signal.quantity > 200
        # 1% of 1 lakh over a 1.2% stop: 1000 / (318 * 0.012) ≈ 262
        capped = cap_to_risk(signal, risk_qty=262)
        assert capped is not None
        assert capped.quantity == 262
        assert capped.allocation_value == 262 * 318.0

    def test_intraday_stop_is_inside_a_typical_session_range(self):
        cfg = GainerConfig()
        signal = plan_entry(symbol="HDFCBANK", price=710.0, pct_from_open=1.0, budget=80_000, config=cfg)
        assert signal is not None
        stop_pct = (signal.entry - signal.stop_loss) / signal.entry * 100
        tgt_pct = (signal.target - signal.entry) / signal.entry * 100
        assert 1.0 <= stop_pct <= 1.5
        assert 2.0 <= tgt_pct <= 2.8
