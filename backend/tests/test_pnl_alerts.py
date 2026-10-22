"""All-bots P&L alerts (pnl_alerts.py): fake engines, a fake delivery and the API. Alerts only."""
from __future__ import annotations

import datetime as dt

import pytest

import pnl_alerts


class FakeEngine:
    def __init__(self, bot_id: int, closed: float, opened: float = 0.0, charges: float = 0.0):
        self.bot_id = bot_id
        self.closed, self.opened, self.charges = closed, opened, charges
        self.positions = {"SBIN": object()} if opened else {}
        self.status = "RUNNING"
        self.killed: list[str] = []

    def load_config(self):
        return type("C", (), {"trading_mode": "PAPER"})()

    def _kpis(self, mode):  # noqa: ARG002
        return {"actual_gross": self.closed, "net": self.closed - self.charges}

    def _open_pnl(self, name):  # noqa: ARG002
        return {"gross": self.opened, "net": self.opened - self.charges}

    async def kill(self, reason: str) -> None:  # never called: alerts only
        self.killed.append(reason)


def cfg(**kw):
    base = {"pnl_alert_profit": 0, "pnl_alert_loss": 0, "pnl_alert_telegram": True, "pnl_alert_call": True, "pnl_alert_basis": "GROSS"}
    base.update(kw)
    return type("C", (), base)()


def recorder():
    sent: list = []

    async def deliver(text, telegram, call):
        sent.append((text, telegram, call))

    return sent, pnl_alerts.PnlAlerts(deliver=deliver)


def test_settings_and_due():
    conf = pnl_alerts.settings(cfg(pnl_alert_profit="5000", pnl_alert_loss=-3, pnl_alert_basis="net", pnl_alert_call=False))
    assert conf == {"profit": 5000.0, "loss": 0.0, "telegram": True, "call": False, "basis": "NET"}
    assert pnl_alerts.settings(type("C", (), {})())["telegram"] is True  # older rows: on
    assert pnl_alerts.due(5000, 5000, 2000, set()) == ["profit"]
    assert pnl_alerts.due(-2000, 5000, 2000, set()) == ["loss"]
    assert pnl_alerts.due(4999, 5000, 2000, set()) == []
    assert pnl_alerts.due(6000, 5000, 2000, {"profit"}) == []
    assert pnl_alerts.due(1e6, 0, 0, set()) == []


@pytest.mark.asyncio
async def test_profit_level_alerts_once_a_day_and_never_trades():
    sent, watch = recorder()
    bots = [FakeEngine(1, 3000), FakeEngine(2, 1500, opened=600), FakeEngine(3, 0), FakeEngine(4, -100)]
    day = dt.date(2026, 10, 22)
    assert await watch.check(bots, cfg(pnl_alert_profit=5000, pnl_alert_loss=2000), day) == ["profit"]
    text, telegram, call = sent[0]
    assert "+₹5,000.00" in text and "Bot 2 +₹2,100.00" in text and "before charges" in text and telegram and call
    assert await watch.check(bots, cfg(pnl_alert_profit=5000, pnl_alert_loss=2000), day) == []  # once today
    assert await watch.check(bots, cfg(pnl_alert_profit=5000), day + dt.timedelta(days=1)) == ["profit"]  # next day again
    assert all(not b.killed for b in bots) and all(b.status == "RUNNING" for b in bots)


@pytest.mark.asyncio
async def test_loss_level_on_net_and_the_switches():
    sent, watch = recorder()
    bots = [FakeEngine(1, -1500, charges=300), FakeEngine(2, -100, opened=-200, charges=100)]
    day = dt.date(2026, 10, 22)
    # Before charges: -1,800, not yet at -2,000.
    assert await watch.check(bots, cfg(pnl_alert_loss=2000), day) == []
    # After charges: -1,800 - 300 - 100 - 100 = -2,300.
    assert await watch.check(bots, cfg(pnl_alert_loss=2000, pnl_alert_basis="NET", pnl_alert_call=False), day) == ["loss"]
    assert "after charges" in sent[-1][0] and "-₹2,000.00" in sent[-1][0] and sent[-1][2] is False
    # Both ways off: nothing is checked or sent.
    _, quiet = recorder()
    assert await quiet.check(bots, cfg(pnl_alert_loss=10, pnl_alert_telegram=False, pnl_alert_call=False), day) == []


def test_api_saves_levels_and_tests_need_a_way(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        got = client.get("/api/bots/pnl-alerts").json()
        assert got["profit"] == 0 and got["loss"] == 0 and got["telegram"] and got["call"] and got["basis"] == "GROSS"
        assert client.put("/api/bots/pnl-alerts", json={"profit": -1, "loss": 0}).status_code == 422
        saved = client.put(
            "/api/bots/pnl-alerts", json={"profit": 5000, "loss": 2500, "telegram": True, "call": False, "basis": "NET"}
        ).json()
        assert (saved["profit"], saved["loss"], saved["call"], saved["basis"]) == (5000, 2500, False, "NET")
        assert saved["total"] == 0 and saved["sent"] == {}
        client.put("/api/bots/pnl-alerts", json={"profit": 5000, "loss": 0, "telegram": False, "call": False})
        assert client.post("/api/bots/pnl-alerts/test").status_code == 409
        # Copying one bot's settings to another never carries the desk-wide alert levels.
        from main import COPY_SKIP

        assert {"pnl_alert_profit", "pnl_alert_loss", "pnl_alert_call"} <= COPY_SKIP
    database.reset_engine()
