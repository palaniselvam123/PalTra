"""Tests for the trade-list source badge and the bulk copy endpoint.

Nothing here places an order; it only arms stocks on the Trade list and copies
them between bots. Mode stays PAPER.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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


def _arm(client, symbol, bot=1, source=None):
    url = "/api/trade-symbols" if bot == 1 else f"/api/bots/{bot}/trade-symbols"
    body = {"symbol": symbol, "armed": True}
    if source:
        body["source"] = source
    return client.post(url, json=body)


def test_arming_a_stock_with_a_source_shows_the_source_in_the_config(api):
    r = _arm(api, "TCS", source="Cross scan")
    assert r.status_code == 200
    cfg = r.json()
    assert "TCS" in cfg["trade_symbols"]
    assert cfg["trade_sources"].get("TCS") == "Cross scan"


def test_disarming_a_stock_clears_its_source(api):
    _arm(api, "INFY", source="Movers")
    r = api.post("/api/trade-symbols", json={"symbol": "INFY", "armed": False})
    assert r.status_code == 200 and "INFY" not in r.json()["trade_symbols"]
    assert "INFY" not in r.json()["trade_sources"]


def test_copy_requires_different_bots_and_nonempty_symbols(api):
    assert api.post("/api/trade-symbols/copy", json={"from_bot": 1, "to_bot": 1, "symbols": ["TCS"]}).status_code == 400
    assert api.post("/api/trade-symbols/copy", json={"from_bot": 1, "to_bot": 2, "symbols": []}).status_code == 400


def test_copy_adds_stocks_to_the_target_bot_keeps_them_on_the_source(api):
    _arm(api, "TCS", source="Manual")
    _arm(api, "INFY", source="Cross scan")
    r = api.post(
        "/api/trade-symbols/copy",
        json={"from_bot": 1, "to_bot": 2, "symbols": ["TCS", "INFY", "SBIN"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert sorted(body["added"]) == ["INFY", "TCS"]
    # SBIN was not on bot 1, so it is skipped with a reason.
    assert any(s["symbol"] == "SBIN" for s in body["skipped"])
    # The source bot keeps its stocks.
    bot1 = api.get("/api/config").json()
    assert set(bot1["trade_symbols"]) >= {"TCS", "INFY"}
    assert bot1["trade_sources"].get("TCS") == "Manual"
    # The destination bot shows the original source note (not the "Copied from" label).
    bot2 = api.get("/api/bots/2/config").json()
    assert set(bot2["trade_symbols"]) >= {"TCS", "INFY"}
    assert bot2["trade_sources"].get("TCS") == "Manual"
    assert bot2["trade_sources"].get("INFY") == "Cross scan"


def test_copy_skips_stocks_already_on_the_target(api):
    _arm(api, "TCS")
    _arm(api, "TCS", bot=2)  # already there
    r = api.post("/api/trade-symbols/copy", json={"from_bot": 1, "to_bot": 2, "symbols": ["TCS"]})
    assert r.status_code == 200
    body = r.json()
    assert body["added"] == []
    assert any(s["symbol"] == "TCS" and "already" in s["why"] for s in body["skipped"])


def test_copy_falls_back_to_a_copied_from_label_when_the_source_bot_had_no_tag(api):
    # Older stock with no source entry.
    _arm(api, "RELIANCE")
    r = api.post("/api/trade-symbols/copy", json={"from_bot": 1, "to_bot": 3, "symbols": ["RELIANCE"]})
    assert r.status_code == 200 and r.json()["added"] == ["RELIANCE"]
    bot3 = api.get("/api/bots/3/config").json()
    assert "Copied from bot 1" in bot3["trade_sources"].get("RELIANCE", "")
