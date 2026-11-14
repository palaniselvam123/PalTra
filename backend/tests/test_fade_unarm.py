"""Un-arm a stock once its trend fades (fade_unarm.py)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

import fade_unarm
from tests.test_sma_atr_terminal import IST, engine  # noqa: F401  (engine is a fixture)

CHOP = {"minutes": 15, "score": "MIXED", "crosses": 2, "phase": "CHOP", "phase_early": False}
TREND = {"minutes": 15, "score": "TRENDING", "crosses": 0, "phase": "BULL", "phase_early": False}
CHOPPY_SCORE = {"minutes": 15, "score": "CHOPPY", "crosses": 5, "phase": "BULL", "phase_early": False}


def test_what_counts_as_faded():
    assert fade_unarm.faded(CHOP, "PHASE")
    assert fade_unarm.faded(TREND, "PHASE") is None
    assert fade_unarm.faded(CHOPPY_SCORE, "PHASE") is None
    assert fade_unarm.faded(CHOPPY_SCORE, "SCORE")
    assert fade_unarm.faded(CHOP, "SCORE") is None
    assert fade_unarm.faded(CHOP, "EITHER") and fade_unarm.faded(CHOPPY_SCORE, "EITHER")
    # Too few candles today: "choppy" only means not readable yet, never a fade.
    assert fade_unarm.faded({**CHOP, "phase_early": True}, "EITHER") is None
    assert fade_unarm.faded(None, "EITHER") is None


def test_settings_are_bounded():
    assert not fade_unarm.on(SimpleNamespace())
    assert fade_unarm.rule(SimpleNamespace(fade_unarm_rule="bogus")) == "PHASE"
    assert fade_unarm.after_min(SimpleNamespace(fade_unarm_min=1)) == fade_unarm.MIN_MIN
    assert fade_unarm.after_min(SimpleNamespace(fade_unarm_min=999)) == fade_unarm.MAX_MIN
    assert fade_unarm.after_min(SimpleNamespace()) == 30


def _arm(names: list[str]) -> None:
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = ",".join(names)
        db.commit()


def _armed() -> list[str]:
    import database
    from models import BotConfig
    from strategy_engine import trade_names

    with database.session_factory()() as db:
        return trade_names(db.get(BotConfig, 1))


@pytest.mark.asyncio
async def test_unarmed_after_reading_choppy_for_the_set_minutes(engine, monkeypatch):  # noqa: F811
    import stock_regime

    _arm(["SBIN", "TCS"])
    reads = {"SBIN": CHOP, "TCS": TREND}
    monkeypatch.setattr(stock_regime, "cached", lambda owner, symbol, *a, **k: reads[symbol])
    engine.status = "RUNNING"
    cfg = engine.load_config()
    cfg.fade_unarm, cfg.fade_unarm_min = True, 30
    start = dt.datetime(2026, 11, 13, 10, 0, tzinfo=IST)

    async def at(minutes: int, symbol: str) -> bool:
        now = start + dt.timedelta(minutes=minutes)
        engine._now = lambda: now
        return await engine._watch_fade(symbol, cfg)

    assert not await at(0, "SBIN")
    assert not await at(15, "SBIN")
    assert not await at(15, "TCS")  # still trending: never un-armed
    assert await at(30, "SBIN")
    assert _armed() == ["TCS"]
    assert "trend faded" in engine._signals["SBIN"]


@pytest.mark.asyncio
async def test_a_trend_coming_back_restarts_the_timer_and_a_held_stock_waits(engine, monkeypatch):  # noqa: F811
    import stock_regime

    _arm(["SBIN"])
    reads = {"SBIN": CHOP}
    monkeypatch.setattr(stock_regime, "cached", lambda owner, symbol, *a, **k: reads[symbol])
    engine.status = "RUNNING"
    cfg = engine.load_config()
    cfg.fade_unarm, cfg.fade_unarm_min = True, 30
    start = dt.datetime(2026, 11, 13, 10, 0, tzinfo=IST)

    async def at(minutes: int) -> bool:
        now = start + dt.timedelta(minutes=minutes)
        engine._now = lambda: now
        return await engine._watch_fade("SBIN", cfg)

    assert not await at(0)
    reads["SBIN"] = TREND
    assert not await at(15)  # the trend came back: the choppy stretch starts over
    reads["SBIN"] = CHOP
    assert not await at(30)
    assert not await at(45)
    engine.positions["SBIN"] = object()  # an open trade is never cut
    assert not await at(60)
    assert "once this trade closes" in engine._signals["SBIN"]
    assert _armed() == ["SBIN"]
    del engine.positions["SBIN"]
    assert await at(75)
    assert _armed() == []


@pytest.mark.asyncio
async def test_off_by_default(engine, monkeypatch):  # noqa: F811
    import stock_regime

    _arm(["SBIN"])
    monkeypatch.setattr(stock_regime, "cached", lambda *a, **k: CHOP)
    engine.status = "RUNNING"
    cfg = engine.load_config()
    engine._now = lambda: dt.datetime(2026, 11, 13, 15, 0, tzinfo=IST)
    assert not await engine._watch_fade("SBIN", cfg)
    assert _armed() == ["SBIN"]


def test_a_replay_only_drops_the_stock_from_its_run():
    from replay import ReplayEngine

    fake = SimpleNamespace(replay_symbols=["SBIN", "TCS"])
    text = ReplayEngine._fade_unarm(fake, "sbin")
    assert fake.replay_symbols == ["TCS"]
    assert "replay" in text


def test_api_saves_the_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"fade_unarm", "fade_unarm_rule", "fade_unarm_min"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["fade_unarm"] is False and got["fade_unarm_rule"] == "PHASE" and got["fade_unarm_min"] == 30
        body = client.put("/api/config", json={"fade_unarm": True, "fade_unarm_rule": "EITHER", "fade_unarm_min": 45}).json()
        assert body["fade_unarm"] is True and body["fade_unarm_rule"] == "EITHER" and body["fade_unarm_min"] == 45
        assert client.put("/api/config", json={"fade_unarm_rule": "SOON"}).status_code == 422
        assert client.put("/api/config", json={"fade_unarm_min": 2}).status_code == 422
    database.reset_engine()
