"""Scan lists: NSE index constituents (app/services/index_lists.py), the Scalp universe route and the
Trend quality auto re-scan's lists. No network: downloads are faked."""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services import index_lists

CSV = "﻿Company Name,Industry,Symbol,Series,ISIN Code\nReliance Industries Ltd.,Oil,RELIANCE,EQ,INE002A01018\nHDFC Bank Ltd.,Fin,hdfcbank,EQ,INE040A01034\n,,,,\nHDFC Bank Ltd.,Fin,HDFCBANK,EQ,X\n"


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setenv("INDEX_CACHE_DIR", str(tmp_path))
    index_lists._memory.clear()
    yield
    index_lists._memory.clear()


def test_parse_reads_the_symbol_column():
    assert index_lists.parse(CSV) == ["RELIANCE", "HDFCBANK"]


def test_members_cache_and_fall_back_to_the_saved_copy(monkeypatch):
    calls = []

    def ok(key):
        calls.append(key)
        return CSV

    monkeypatch.setattr(index_lists, "_download", ok)
    assert index_lists.members("nifty50") == ["RELIANCE", "HDFCBANK"]
    assert index_lists.members("nifty50") == ["RELIANCE", "HDFCBANK"]
    assert calls == ["nifty50"]  # second from memory

    def blocked(key):
        raise OSError("403 Forbidden")

    index_lists._memory.clear()
    monkeypatch.setattr(index_lists, "_download", blocked)
    assert index_lists.members("nifty50") == ["RELIANCE", "HDFCBANK"]  # the saved file
    with pytest.raises(RuntimeError, match="Nifty 100"):
        index_lists.members("nifty100")  # never saved
    with pytest.raises(ValueError):
        index_lists.members("sensex")


def test_universe_route_joins_the_ticked_lists(monkeypatch):
    from app.api import routes_scalp

    monkeypatch.setattr(routes_scalp.active_scanner, "universe_symbols", lambda: ["SBIN", "RELIANCE"])
    monkeypatch.setattr(index_lists, "_download", lambda key: CSV)
    app = FastAPI()
    app.include_router(routes_scalp.router)
    client = TestClient(app)
    out = client.get("/api/scalp/universe").json()
    assert out["symbols"] == ["SBIN", "RELIANCE"] and out["counts"] == {"fno": 2}
    out = client.get("/api/scalp/universe?lists=fno,nifty50").json()
    assert out["symbols"] == ["SBIN", "RELIANCE", "HDFCBANK"] and out["counts"] == {"fno": 2, "nifty50": 2}
    assert client.get("/api/scalp/universe?lists=nifty50").json()["symbols"] == ["RELIANCE", "HDFCBANK"]
    assert client.get("/api/scalp/universe?lists=sensex").status_code == 400

    def blocked(key):
        raise OSError("403")

    index_lists._memory.clear()
    monkeypatch.setattr(index_lists, "_download", blocked)
    out = client.get("/api/scalp/universe?lists=fno,nifty100").json()
    assert out["symbols"] == ["SBIN", "RELIANCE"] and "nifty100" in out["errors"] and out["error"] is None


def test_auto_rescan_keeps_only_known_lists():
    import chop_auto

    s = chop_auto._clean({"lists": ["Nifty50", "armed", "bogus", "nifty50"]})
    assert s.lists == ["nifty50", "armed"]
    assert chop_auto._clean({"lists": "fno"}).lists == []
    assert chop_auto._clean({}).lists == []


def test_auto_symbols_join_armed_and_index_lists(monkeypatch):
    import main
    from app.api import routes_scalp

    async def armed(armed_only, lists=None):
        return ["TCS"] if armed_only and not lists else await real(armed_only, lists)

    real = main._auto_symbols
    monkeypatch.setattr(main, "_auto_symbols", armed)

    async def universe(lists="fno"):
        assert lists == "nifty50"
        return {"symbols": ["RELIANCE", "TCS"], "error": None}

    monkeypatch.setattr(routes_scalp, "fno_universe", universe)
    assert asyncio.run(main._auto_symbols(False, ["armed", "nifty50"])) == ["TCS", "RELIANCE"]
