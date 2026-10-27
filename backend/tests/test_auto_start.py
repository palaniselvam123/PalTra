"""Auto-start for PAPER bots (auto_start.py)."""
from types import SimpleNamespace

import pytest

import auto_start


class FakeEngine:
    def __init__(self, bot_id=1, status="STOPPED", auto=True, mode="PAPER"):
        self.bot_id = bot_id
        self.status = status
        self.last_signal = ""
        self._cfg_cache = SimpleNamespace(auto_start=auto, trading_mode=mode)

    def load_config(self):
        return self._cfg_cache


async def _start(eng):
    eng.status = "RUNNING"


def test_wants_only_paper_with_the_switch_on():
    assert auto_start.wants(SimpleNamespace(auto_start=True, trading_mode="PAPER"))
    assert auto_start.wants(SimpleNamespace(auto_start=True, trading_mode=None))
    assert not auto_start.wants(SimpleNamespace(auto_start=True, trading_mode="LIVE"))
    assert not auto_start.wants(SimpleNamespace(auto_start=False, trading_mode="PAPER"))
    assert not auto_start.wants(SimpleNamespace(auto_start=None, trading_mode="PAPER"))
    assert not auto_start.wants(None)


@pytest.mark.asyncio
async def test_starts_only_stopped_paper_bots_with_it_on():
    bots = [
        FakeEngine(1),
        FakeEngine(2, auto=False),
        FakeEngine(3, mode="LIVE"),
        FakeEngine(4, status="PAUSED"),
        FakeEngine(5, status="HALTED"),
        FakeEngine(6, status="DAY_COMPLETED"),
        FakeEngine(7, status="RUNNING"),
    ]
    started = await auto_start.pass_once(bots, _start)
    assert started == [1]
    assert [b.status for b in bots] == ["RUNNING", "STOPPED", "STOPPED", "PAUSED", "HALTED", "DAY_COMPLETED", "RUNNING"]
    assert "Auto-started" in bots[0].last_signal


@pytest.mark.asyncio
async def test_a_refused_start_is_left_alone():
    from fastapi import HTTPException

    async def refuse(eng):
        raise HTTPException(423, "Halted for the day")

    eng = FakeEngine(2)
    assert await auto_start.pass_once([eng], refuse) == []
    assert eng.status == "STOPPED"


def test_config_route_saves_and_reports_auto_start(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        assert client.get("/api/bots/2/config").json()["auto_start"] is False
        ok = client.put("/api/bots/2/config", json={"auto_start": True})
        assert ok.status_code == 200 and ok.json()["auto_start"] is True
        assert "auto_start" in main.COPY_SKIP
    database.reset_engine()
