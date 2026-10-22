"""Arm log (arm_log.py) and "replay as armed". PAPER only; no order path."""
from __future__ import annotations

import datetime as dt

import pytest

import arm_log
from tests.test_gap_mode import _frame, _settings, db  # noqa: F401  (db is a fixture)

IST = arm_log.IST
DAY = dt.date(2026, 9, 22)


def _set_list(database, config_id: int, names: list[str]) -> None:
    from models import BotConfig

    with database.session_factory()() as s:
        row = s.get(BotConfig, config_id)
        row.trade_symbols = ",".join(names)
        s.commit()


def _log(database, config_id: int, symbol: str, action: str, at: dt.datetime) -> None:
    from models import ArmLog

    with database.session_factory()() as s:
        s.add(ArmLog(at=at, config_id=config_id, symbol=symbol, action=action, mode="PAPER"))
        s.commit()


def test_trade_list_changes_are_logged(db):  # noqa: F811
    from models import ArmLog

    _set_list(db, 1, ["SBIN", "INFY"])
    _set_list(db, 1, ["INFY", "TCS"])
    with db.session_factory()() as s:
        got = [(r.symbol, r.action) for r in s.query(ArmLog).filter(ArmLog.config_id == 1).order_by(ArmLog.id)]
    assert ("SBIN", "ARM") in got and ("INFY", "ARM") in got
    assert ("TCS", "ARM") in got and ("SBIN", "UNARM") in got
    assert got.count(("INFY", "ARM")) == 1  # kept armed: no new row
    # The baseline adds a start only for armed stocks whose last row is not an arm.
    assert arm_log.baseline() >= 0
    before = arm_log.baseline()
    assert before == 0


def test_windows_follow_the_log(db):  # noqa: F811
    _log(db, 1, "SBIN", "ARMED", dt.datetime(2026, 9, 21, 15, 0))  # armed the day before, still armed
    _log(db, 1, "INFY", "ARM", dt.datetime(2026, 9, 22, 10, 30))
    _log(db, 1, "INFY", "UNARM", dt.datetime(2026, 9, 22, 12, 0))
    _log(db, 1, "INFY", "ARM", dt.datetime(2026, 9, 22, 13, 0))
    _log(db, 1, "TCS", "ARM", dt.datetime(2026, 9, 21, 10, 0))
    _log(db, 1, "TCS", "UNARM", dt.datetime(2026, 9, 21, 11, 0))  # not armed on DAY
    spans = arm_log.windows(1, DAY)
    ts = lambda h, m: int(dt.datetime(2026, 9, 22, h, m, tzinfo=IST).timestamp())  # noqa: E731
    midnight = ts(0, 0)
    assert spans["SBIN"] == [(midnight, midnight + 86_400)]
    assert spans["INFY"] == [(ts(10, 30), ts(12, 0)), (ts(13, 0), midnight + 86_400)]
    assert "TCS" not in spans
    assert arm_log.armed_at(spans["INFY"], ts(11, 0)) and not arm_log.armed_at(spans["INFY"], ts(12, 30))
    assert arm_log.desk_of(1) == "1" and arm_log.desk_of(2) == "research" and arm_log.desk_of(4) == "3"
    assert arm_log.config_id_of("research") == 2 and arm_log.config_id_of(3) == 4


@pytest.mark.asyncio
async def test_replay_as_armed_trades_a_stock_only_after_it_was_armed(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    # Plain replay: the gap-mode test frame trades this stock from the morning.
    plain = await replay_symbol(_frame(), "SIMA", [DAY], _settings(db))
    assert any(t["entry_time"] < dt.datetime(2026, 9, 22, 11, 0) for t in plain)
    # Armed only at 11:00 live: nothing opens before that, the later cross still trades.
    _log(db, 1, "SIMA", "ARM", dt.datetime(2026, 9, 22, 11, 0))
    spans = arm_log.windows(1, DAY)
    armed = await replay_symbol(_frame(), "SIMA", [DAY], _settings(db), arm_windows=spans)
    assert armed and all(t["entry_time"] >= dt.datetime(2026, 9, 22, 11, 0) for t in armed)


def test_api_shows_the_day(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.post("/api/trade-symbols", json={"symbol": "SBIN", "armed": True}).status_code == 200
        today = dt.datetime.now(IST).date().isoformat()
        got = client.get(f"/api/arm-log?desk=1&day={today}").json()
        assert got["day"] == today
        assert any(r["symbol"] == "SBIN" for r in got["rows"])
        assert any(s["symbol"] == "SBIN" for s in got["stocks"])
        assert client.get("/api/arm-log?desk=9").status_code == 422
    database.reset_engine()
