"""H004 v1 pre-registration tests.

Nothing here runs the experiment. These pin the definitions so that when the
forward test is eventually run, it runs the rule that was registered — and so
that a later edit to the module cannot drift away from the registry entry
without a test failing.
"""
from __future__ import annotations

import datetime as dt
import random

import pytest

from app.research.cross_sectional import (
    ATR_PERIOD, HORIZONS, LOOKBACK_BARS, MIN_SECTOR_SIZE, SECTOR_OF, SECTORS, TercileBounds,
    assign_bucket, eligible_sectors, future_sector_relative_return, h004_score,
    permute_within_sector, research_universe, sector_median_loo, sector_of,
    sector_relative_return, within_sector_terciles,
)
from app.services.indicators import OHLCV
from app.services.observation_window import SessionIndex

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


class TestSectorMembership:
    def test_every_symbol_maps_to_exactly_one_sector(self):
        seen: dict[str, str] = {}
        for sec, names in SECTORS.items():
            for s in names.split():
                assert s not in seen, f"{s} appears in both {seen.get(s)} and {sec}"
                seen[s] = sec
        assert len(seen) == 125

    def test_membership_is_deterministic(self):
        assert sector_of("TCS") == sector_of("TCS") == "IT"
        assert sector_of("HDFCBANK") == "BANK"

    def test_unknown_symbol_has_no_sector(self):
        assert sector_of("NOTASYMBOL") is None

    def test_sector_map_contains_no_return_data(self):
        """The map is reference information; anything numeric would be a smell."""
        for names in SECTORS.values():
            for s in names.split():
                assert not any(ch.isdigit() for ch in s) or s in {"M&M", "BAJAJ-AUTO"}


class TestSmallSectorExclusion:
    def test_the_three_undersized_sectors_are_excluded(self):
        eligible = eligible_sectors()
        for sec in ("TELECOM", "REALTY", "CONGLOM"):
            assert len(SECTORS[sec].split()) < MIN_SECTOR_SIZE
            assert sec not in eligible

    def test_thirteen_sectors_qualify(self):
        assert len(eligible_sectors()) == 13

    def test_research_universe_is_118(self):
        assert len(research_universe()) == 118

    def test_excluded_symbols_are_absent_from_the_universe(self):
        universe = set(research_universe())
        for s in ("BHARTIARTL", "DLF", "ADANIENT", "ADANIPORTS", "OBEROIRLTY"):
            assert s not in universe

    def test_eligibility_respects_symbols_actually_present(self):
        """A sector large in the map but thin at this timestamp must not qualify."""
        present = set(SECTORS["IT"].split()[:3]) | set(SECTORS["BANK"].split())
        eligible = eligible_sectors(present)
        assert "IT" not in eligible and "BANK" in eligible


class TestLeaveOneOutMedian:
    @staticmethod
    def bank(values: list[float]) -> dict[str, float]:
        return dict(zip(SECTORS["BANK"].split(), values))

    def test_excludes_the_symbol_itself(self):
        rets = self.bank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
        members = SECTORS["BANK"].split()
        # peers of the 1st stock are 2..12, median 7
        assert sector_median_loo(rets, members[0]) == 7

    def test_the_median_stock_does_not_get_a_zero_relative_return(self):
        """The degeneracy leave-one-out exists to remove: with a plain median
        the middle stock is its own benchmark."""
        rets = self.bank([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12])
        rels = [sector_relative_return(rets, s) for s in SECTORS["BANK"].split()]
        assert all(r is not None for r in rels)
        assert sum(1 for r in rels if r == 0) == 0

    def test_returns_none_for_a_sector_below_the_minimum(self):
        rets = dict(zip(SECTORS["REALTY"].split(), [1.0, 2.0]))
        assert sector_median_loo(rets, "DLF") is None
        assert sector_relative_return(rets, "DLF") is None

    def test_returns_none_when_too_few_peers_are_present(self):
        members = SECTORS["BANK"].split()[:4]          # only 4 of 12 present
        rets = dict(zip(members, [1.0, 2.0, 3.0, 4.0]))
        assert sector_median_loo(rets, members[0]) is None

    def test_exactly_the_minimum_is_allowed(self):
        members = SECTORS["CEMENT"].split()            # exactly 5
        rets = dict(zip(members, [1.0, 2.0, 3.0, 4.0, 5.0]))
        assert sector_median_loo(rets, members[0]) == 3.5   # median of 2,3,4,5

    def test_only_same_sector_peers_are_used(self):
        rets = {**dict(zip(SECTORS["BANK"].split(), [1.0] * 12)),
                **dict(zip(SECTORS["IT"].split(), [99.0] * 8))}
        assert sector_median_loo(rets, "HDFCBANK") == 1.0


class TestScore:
    @staticmethod
    def rets(bank_values: list[float]) -> dict[str, float]:
        return dict(zip(SECTORS["BANK"].split(), bank_values))

    def test_score_is_relative_return_over_relative_atr(self):
        rets = self.rets([0.05] + [0.01] * 11)
        members = SECTORS["BANK"].split()
        # peers are all 0.01, so the relative return is 0.04
        assert h004_score(rets, members[0], atr_over_close=0.002) == pytest.approx(20.0)

    def test_a_stock_moving_with_its_sector_scores_zero(self):
        rets = self.rets([0.02] * 12)
        assert h004_score(rets, SECTORS["BANK"].split()[0], 0.002) == 0.0

    def test_volatility_normalisation_makes_stocks_comparable(self):
        """Two stocks with the same relative move but different volatility must
        not score the same — that is the whole point of the denominator."""
        rets = self.rets([0.05] + [0.01] * 11)
        s = SECTORS["BANK"].split()[0]
        assert h004_score(rets, s, 0.001) > h004_score(rets, s, 0.004)

    def test_zero_or_missing_denominator_yields_none(self):
        rets = self.rets([0.05] + [0.01] * 11)
        s = SECTORS["BANK"].split()[0]
        assert h004_score(rets, s, 0.0) is None
        assert h004_score(rets, s, -0.001) is None

    def test_score_is_none_for_an_undersized_sector(self):
        rets = dict(zip(SECTORS["REALTY"].split(), [0.05, 0.01]))
        assert h004_score(rets, "DLF", 0.002) is None

    def test_deterministic(self):
        rets = self.rets([0.05] + [0.01] * 11)
        s = SECTORS["BANK"].split()[0]
        assert h004_score(rets, s, 0.002) == h004_score(rets, s, 0.002)


class TestOutcome:
    def test_outcome_uses_the_same_leave_one_out_construction_as_the_score(self):
        """If the score and outcome used different baselines, a result could be
        an artefact of the mismatch rather than a relationship."""
        fut = dict(zip(SECTORS["BANK"].split(), [0.03] + [0.01] * 11))
        members = SECTORS["BANK"].split()
        assert future_sector_relative_return(fut, members[0]) == pytest.approx(0.02)
        assert future_sector_relative_return(fut, members[0]) == sector_relative_return(fut, members[0])

    def test_outcome_is_none_for_an_undersized_sector(self):
        fut = dict(zip(SECTORS["CONGLOM"].split(), [0.03, 0.01]))
        assert future_sector_relative_return(fut, "ADANIENT") is None


class TestWithinSectorTerciles:
    def test_boundaries_are_computed_per_sector(self):
        bounds = within_sector_terciles({
            "BANK": [float(i) for i in range(12)],
            "IT": [float(i) * 10 for i in range(9)],
        })
        assert bounds["BANK"].low != bounds["IT"].low
        assert bounds["IT"].high > bounds["BANK"].high

    def test_a_stock_is_bucketed_against_its_own_sector(self):
        """A score of 20 is high in BANK and low in IT — the point of cutting
        within sector rather than globally."""
        bounds = within_sector_terciles({
            "BANK": [float(i) for i in range(12)],
            "IT": [float(i) * 10 for i in range(9)],
        })
        assert assign_bucket(bounds, "HDFCBANK", 20.0) == "high"
        assert assign_bucket(bounds, "TCS", 20.0) == "low"

    def test_buckets_split_roughly_evenly(self):
        vals = [float(i) for i in range(120)]
        b = within_sector_terciles({"BANK": vals})["BANK"]
        counts = {"low": 0, "mid": 0, "high": 0}
        for v in vals:
            counts[b.bucket(v)] += 1
        assert all(30 <= c <= 50 for c in counts.values()), counts

    def test_bucket_is_none_for_an_unknown_or_unbucketed_sector(self):
        bounds = within_sector_terciles({"BANK": [float(i) for i in range(12)]})
        assert assign_bucket(bounds, "TCS", 1.0) is None
        assert assign_bucket(bounds, "NOTASYMBOL", 1.0) is None

    def test_boundaries_are_frozen_values_not_recomputed(self):
        b = TercileBounds("BANK", low=-1.0, high=1.0)
        assert b.bucket(-2) == "low" and b.bucket(0) == "mid" and b.bucket(5) == "high"


class TestPermutation:
    @staticmethod
    def scores() -> dict[str, float]:
        out = {}
        for i, s in enumerate(SECTORS["BANK"].split()):
            out[s] = float(i)
        for i, s in enumerate(SECTORS["IT"].split()):
            out[s] = 100.0 + i
        return out

    def test_scores_never_cross_a_sector_boundary(self):
        """The essential property: a whole-cross-section shuffle would let a
        purely sector-driven result beat the null."""
        original = self.scores()
        for seed in range(20):
            out = permute_within_sector(original, random.Random(seed))
            for s, v in out.items():
                assert SECTOR_OF[s] == SECTOR_OF[
                    next(k for k, ov in original.items() if ov == v)
                ]

    def test_the_multiset_of_scores_within_a_sector_is_preserved(self):
        original = self.scores()
        out = permute_within_sector(original, random.Random(1))
        for sec in ("BANK", "IT"):
            members = [s for s in original if SECTOR_OF[s] == sec]
            assert sorted(original[s] for s in members) == sorted(out[s] for s in members)

    def test_membership_and_universe_are_preserved(self):
        original = self.scores()
        out = permute_within_sector(original, random.Random(2))
        assert set(out) == set(original)

    def test_permutation_actually_moves_scores(self):
        """Guards the tests above passing on an identity function."""
        original = self.scores()
        changed = any(permute_within_sector(original, random.Random(s)) != original
                      for s in range(10))
        assert changed

    def test_deterministic_for_a_given_seed(self):
        original = self.scores()
        assert permute_within_sector(original, random.Random(7)) == permute_within_sector(
            original, random.Random(7)
        )


class TestNoLookAheadAndEligibility:
    @staticmethod
    def session(day: str, bars: int = 75) -> list[OHLCV]:
        base = int(dt.datetime.strptime(day, "%Y-%m-%d")
                   .replace(hour=9, minute=15, tzinfo=IST).timestamp())
        return [OHLCV(base + i * 300, 100.0, 101.0, 99.0, 100.0 + i * 0.01, 1000)
                for i in range(bars)]

    def test_score_reads_only_the_returns_it_is_given(self):
        """The score is a pure function of same-timestamp inputs; there is no
        path by which a later bar could reach it."""
        import inspect

        from app.research import cross_sectional

        src = inspect.getsource(cross_sectional)
        for banned in ("t+1", "t + 1", "future_return_i(t", "shift(-"):
            assert banned not in src.replace("future_sector_relative_return", "")

    def test_forward_window_eligibility_is_shared_with_every_other_module(self):
        candles = self.session("2026-06-01")
        index = SessionIndex(candles)
        for h in HORIZONS:
            assert index.is_forward_window_valid(30, h)
            assert not index.is_forward_window_valid(len(candles) - 1, h)

    def test_last_bars_of_session_are_ineligible_at_each_horizon(self):
        candles = self.session("2026-06-01")
        index = SessionIndex(candles)
        for h in HORIZONS:
            assert not index.is_forward_window_valid(len(candles) - h, h)
            assert index.is_forward_window_valid(len(candles) - 1 - h, h)

    def test_window_cannot_cross_into_the_next_session(self):
        candles = self.session("2026-06-01") + self.session("2026-06-02")
        index = SessionIndex(candles)
        assert not index.is_forward_window_valid(74, 6)
        assert index.is_forward_window_valid(75 + 30, 6)


class TestFrozenRegistryDefinition:
    """The registry is the pre-registration; the module is what will run. If they
    disagree, a recorded result would describe a rule nobody executed."""

    @staticmethod
    def rule(tmp_path, monkeypatch) -> dict:
        from app.research.hypothesis_registry import HypothesisRegistry
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        return r.get("H004", "v1").rule_definition

    def test_h004_is_registered_as_defined(self, tmp_path, monkeypatch):
        from app.research.hypothesis_registry import HypothesisRegistry
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        assert r.get("H004", "v1").status == "DEFINED"

    def test_registered_parameters_match_the_implementation(self, tmp_path, monkeypatch):
        rule = self.rule(tmp_path, monkeypatch)
        assert rule["lookback_bars"] == LOOKBACK_BARS
        assert rule["atr_period"] == ATR_PERIOD
        assert rule["min_sector_size"] == MIN_SECTOR_SIZE
        assert rule["horizons_bars"] == list(HORIZONS)
        assert rule["universe"]["research_universe"] == len(research_universe())

    def test_registered_exclusions_match_the_implementation(self, tmp_path, monkeypatch):
        rule = self.rule(tmp_path, monkeypatch)
        eligible = eligible_sectors()
        for entry in rule["universe"]["excluded_sectors"]:
            assert entry.split("(")[0] not in eligible

    def test_seeding_does_not_overwrite_a_recorded_verdict(self, tmp_path, monkeypatch):
        """The bug this guards: adding H004 reset H003 v1 from REJECTED back to
        DEFINED, silently reverting a recorded result."""
        from app.research.hypothesis_registry import HypothesisRegistry
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        r.record_result("H003", "v1", "research_5m_v1", {"verdict": "rejected"}, "REJECTED")

        seed_mod.seed()          # a later seed run must not undo that
        assert r.get("H003", "v1").status == "REJECTED"
        assert r.get("H003", "v1").result_summary is not None
