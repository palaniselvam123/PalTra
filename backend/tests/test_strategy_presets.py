"""Saved strategies and the history of saves (strategy_presets.py)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import strategy_presets as sp


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "strategy_presets.json"
    monkeypatch.setenv("STRATEGY_PRESETS_FILE", str(path))
    return path


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from main import app

    with TestClient(app) as client:
        yield client
    database.reset_engine()
    get_settings.cache_clear()


def test_strategy_only_keeps_update_fields_and_drops_bookkeeping():
    allowed = {"qty", "atr_multiplier", "trade_symbols", "trading_mode", "use_vwap"}
    skip = frozenset({"trade_symbols", "trading_mode"})
    got = sp.strategy_only({"qty": 5, "atr_multiplier": 1.5, "trade_symbols": ["TCS"], "trading_mode": "LIVE", "junk": 1, "use_vwap": True}, allowed, skip)
    assert got == {"qty": 5, "atr_multiplier": 1.5, "use_vwap": True}


def test_a_preset_is_saved_listed_replaced_by_name_and_deleted(store):
    sp.save_preset("Scalp A", "1", {"qty": 10, "atr_multiplier": 1.5})
    sp.save_preset("Trend", "3", {"qty": 2, "atr_multiplier": 2.5})
    out = sp.listing("1")
    assert [p["name"] for p in out["presets"]] == ["Trend", "Scalp A"]  # newest first
    assert out["presets"][1]["settings"] == {"qty": 10, "atr_multiplier": 1.5} and out["presets"][1]["desk"] == "1"
    # The same name on the same desk is replaced in place, not duplicated.
    sp.save_preset("Scalp A", "1", {"qty": 20, "atr_multiplier": 1.5})
    names = [(p["name"], p["desk"]) for p in sp.listing("1")["presets"]]
    assert names.count(("Scalp A", "1")) == 1
    assert next(p for p in sp.listing("1")["presets"] if p["name"] == "Scalp A")["settings"]["qty"] == 20
    pid = next(p["id"] for p in sp.listing("1")["presets"] if p["name"] == "Trend")
    assert [p["name"] for p in sp.delete_preset(pid, "1")["presets"]] == ["Scalp A"]
    assert json.loads(store.read_text())["presets"][0]["name"] == "Scalp A"


def test_history_keeps_each_save_newest_first_and_skips_an_unchanged_one(store):
    sp.remember("2", {"qty": 1})
    sp.remember("2", {"qty": 1})  # nothing changed: not repeated
    sp.remember("2", {"qty": 3})
    sp.remember("research", {"qty": 9})
    hist = sp.listing("2")["history"]
    assert [h["settings"]["qty"] for h in hist] == [3, 1]
    assert sp.listing("research")["history"][0]["settings"] == {"qty": 9}
    assert sp.listing("1")["history"] == []


def test_history_is_capped_per_desk(store):
    for i in range(sp.MAX_HISTORY + 5):
        sp.remember("1", {"qty": i})
    hist = sp.listing("1")["history"]
    assert len(hist) == sp.MAX_HISTORY and hist[0]["settings"]["qty"] == sp.MAX_HISTORY + 4


def test_a_broken_file_reads_as_empty(store):
    store.write_text("{not json")
    assert sp.listing("1") == {"desk": "1", "presets": [], "history": []}


def test_desk_keys_are_the_five_bots_and_research():
    assert sp.desk_key(3) == "3" and sp.desk_key("research") == "research" and sp.desk_key("x") == "1"


def test_a_save_through_the_api_lands_in_the_history(api, monkeypatch, tmp_path):
    monkeypatch.setenv("STRATEGY_PRESETS_FILE", str(tmp_path / "p.json"))
    before = api.get("/api/config").json()
    r = api.put("/api/config", json={"atr_multiplier": 1.75})
    assert r.status_code == 200, r.text
    hist = api.get("/api/config/presets?desk=1").json()["history"]
    assert hist and hist[0]["settings"]["atr_multiplier"] == 1.75
    assert "trade_symbols" not in hist[0]["settings"] and "trading_mode" not in hist[0]["settings"]
    # A preset through the API keeps only strategy fields.
    r = api.post("/api/config/presets", json={"name": "My scalp", "desk": "1", "settings": {**before, "trade_symbols": ["X"], "atr_multiplier": 2}})
    assert r.status_code == 200, r.text
    preset = r.json()["presets"][0]
    assert preset["name"] == "My scalp" and preset["settings"]["atr_multiplier"] == 2 and "trade_symbols" not in preset["settings"]
    assert api.post("/api/config/presets", json={"name": "", "desk": "1", "settings": {"atr_multiplier": 2}}).status_code == 400
    assert api.delete(f"/api/config/presets/{preset['id']}?desk=1").json()["presets"] == []
