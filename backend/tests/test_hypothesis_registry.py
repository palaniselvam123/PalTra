"""Registry tests.

The registry's job is to make pre-registration real, so the tests that matter
are the ones proving a recorded rule cannot quietly become a different rule.
"""
from __future__ import annotations

import pytest

from app.research.hypothesis_registry import STATUSES, Hypothesis, HypothesisRegistry


@pytest.fixture()
def reg(tmp_path):
    return HypothesisRegistry(tmp_path / "research.db")


def h(hid="H999", version="v1", status="PROPOSED", rule=None) -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hid, version=version, name="Test", description="d",
        status=status, rule_definition=rule or {"entry_long": "close > open"},
    )


class TestRegistration:
    def test_register_and_read_back(self, reg):
        reg.register(h())
        got = reg.get("H999", "v1")
        assert got is not None and got.name == "Test" and got.status == "PROPOSED"

    def test_rule_definition_survives_a_round_trip(self, reg):
        rule = {"entry_long": "x > y", "params": {"n": 50, "depth": [0.3, 0.6]}}
        reg.register(h(rule=rule))
        assert reg.get("H999", "v1").rule_definition == rule

    def test_unknown_status_is_rejected(self, reg):
        with pytest.raises(ValueError):
            reg.register(h(status="LOOKS_GOOD"))

    def test_missing_hypothesis_reads_as_none(self, reg):
        assert reg.get("NOPE", "v1") is None

    def test_all_documented_statuses_are_accepted(self, reg):
        for i, s in enumerate(STATUSES):
            reg.register(h(hid=f"H{i:03d}", status=s))
        assert len(reg.list()) == len(STATUSES)


class TestVersioning:
    def test_a_new_version_does_not_overwrite_the_old_one(self, reg):
        """The central guarantee: a revised rule is v2, and v1's verdict
        survives. Without this, 'we rejected it' can be silently rewritten."""
        reg.register(h(version="v1", status="REJECTED", rule={"depth": "30-50%"}))
        reg.register(h(version="v2", status="PROPOSED", rule={"depth": "50-70%"}))

        v1, v2 = reg.get("H999", "v1"), reg.get("H999", "v2")
        assert v1.status == "REJECTED" and v1.rule_definition == {"depth": "30-50%"}
        assert v2.status == "PROPOSED" and v2.rule_definition == {"depth": "50-70%"}
        assert len(reg.list()) == 2

    def test_rule_changed_detects_an_edited_pre_registration(self, reg):
        reg.register(h(rule={"depth": "30-50%"}))
        assert reg.rule_changed("H999", "v1", {"depth": "50-70%"}) is True
        assert reg.rule_changed("H999", "v1", {"depth": "30-50%"}) is False

    def test_rule_changed_is_false_for_an_unregistered_version(self, reg):
        assert reg.rule_changed("H999", "v9", {"a": 1}) is False

    def test_created_at_is_preserved_across_updates(self, reg):
        reg.register(h())
        created = reg.get("H999", "v1").created_at
        reg.set_status("H999", "v1", "TESTING")
        assert reg.get("H999", "v1").created_at == created


class TestResults:
    def test_recording_a_result_requires_and_stores_a_dataset(self, reg):
        """A number without the data that produced it is not a result."""
        reg.register(h())
        reg.record_result("H999", "v1", "research_5m_v1", {"ratio": [0.8]}, "REJECTED")
        got = reg.get("H999", "v1")
        assert got.dataset_id == "research_5m_v1"
        assert got.result_summary == {"ratio": [0.8]}
        assert got.status == "REJECTED"

    def test_recording_against_an_unregistered_hypothesis_raises(self, reg):
        with pytest.raises(KeyError):
            reg.record_result("NOPE", "v1", "d", {}, "REJECTED")

    def test_status_change_appends_notes_rather_than_replacing(self, reg):
        reg.register(h())
        reg.set_status("H999", "v1", "TESTING", "started run")
        reg.set_status("H999", "v1", "REJECTED", "failed gate 1")
        notes = reg.get("H999", "v1").notes
        assert "started run" in notes and "failed gate 1" in notes

    def test_list_filters_by_status(self, reg):
        reg.register(h(hid="H001", status="REJECTED"))
        reg.register(h(hid="H003", status="PROPOSED"))
        assert [x.hypothesis_id for x in reg.list(status="REJECTED")] == ["H001"]


class TestSeed:
    def test_seed_records_the_two_rejections_and_the_proposal(self, tmp_path, monkeypatch):
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()

        assert r.get("H001", "v1").status == "REJECTED"
        assert r.get("H002", "v1").status == "REJECTED"
        assert r.get("H003", "v1").status == "PROPOSED"

    def test_seeded_rejections_carry_the_evidence(self, tmp_path, monkeypatch):
        """A rejection with no numbers behind it is an opinion."""
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()

        for hid in ("H001", "H002"):
            summary = r.get(hid, "v1").result_summary
            assert summary["dataset_id" if "dataset_id" in summary else "signals"]
            assert len(summary["edge_vs_same_bar"]) == 3
            assert r.get(hid, "v1").dataset_id == "research_5m_v1"

    def test_h003_carries_no_parameters_yet(self, tmp_path, monkeypatch):
        """PROPOSED must not smuggle in thresholds — committing them is what
        moving to DEFINED means."""
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()

        rule = r.get("H003", "v1").rule_definition
        assert "proposed only" in rule["state"]

    def test_seeding_twice_does_not_duplicate(self, tmp_path, monkeypatch):
        import app.research.seed_hypotheses as seed_mod

        r = HypothesisRegistry(tmp_path / "research.db")
        monkeypatch.setattr(seed_mod, "registry", r)
        seed_mod.seed()
        seed_mod.seed()
        assert len(r.list()) == 3
