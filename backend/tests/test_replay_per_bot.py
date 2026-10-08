"""Each SMA bot has its own replay player, so all four can replay at once.

Practice only: replays fill locally and never send an order.
"""
from __future__ import annotations

import datetime as dt

import pytest


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/r.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database

    database.reset_engine()
    database.init_db()
    yield database
    database.reset_engine()


def _open_row(database, run_id: int, symbol: str):
    from models import ReplayRun, TradeLog

    now = dt.datetime(2026, 9, 29, 10, 0)
    with database.session_factory()() as s:
        if s.get(ReplayRun, run_id) is None:
            s.add(ReplayRun(id=run_id, created_at=now, start_date="2026-09-29", end_date="2026-09-29",
                            symbols=symbol, settings="{}", status="RUNNING", days_total=1, days_done=0))
        s.add(TradeLog(date="2026-09-29", symbol=symbol, direction="LONG", qty=1, entry_time=now, entry_price=100.0,
                       ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=99.0, mode="REPLAY", run_id=run_id, book_seq=1))
        s.commit()


@pytest.mark.asyncio
async def test_stopping_one_bots_replay_leaves_anothers_trades_and_run_alone(db):
    from models import ReplayRun, TradeLog
    from replay import ReplaySession

    _open_row(db, 1, "TCS")
    _open_row(db, 2, "INFY")
    one = ReplaySession()
    one.run_id, one.status = 1, "PLAYING"
    await one.stop()
    with db.session_factory()() as s:
        tcs = s.query(TradeLog).filter_by(symbol="TCS").one()
        infy = s.query(TradeLog).filter_by(symbol="INFY").one()
        assert tcs.exit_reason == "REPLAY_STOPPED"
        assert infy.exit_time is None  # the other bot's replay is still playing
        assert s.get(ReplayRun, 1).status == "STOPPED"
        assert s.get(ReplayRun, 2).status == "RUNNING"


def test_each_bot_has_its_own_replay_and_the_routes_pick_it(db, monkeypatch):
    from fastapi.testclient import TestClient
    import main

    assert set(main.replays) == {1, 2, 3, 4}
    assert main.replay is main.replays[1]
    assert len({id(s) for s in main.replays.values()}) == 4

    class Eng:
        status = "PAUSED"
        halt_reason = ""
        replay_symbols = ["TCS"]

        def release_manual_panic(self): ...
        def release_trade_cap(self, *_): ...
        def hold_for_next_cross(self, *_): ...

    eng3 = Eng()
    with TestClient(main.app) as client:
        assert set(client.get("/api/replay/all").json()) == {"1", "2", "3", "4"}
        assert client.get("/api/replay?bot=3").json()["status"] == "IDLE"
        monkeypatch.setattr(main.replays[3], "engine", eng3)
        # Bot 3's replay engine answers bot 3's buttons; bot 1 has none playing.
        assert client.post("/api/replay/bot/start?bot=3").json()["bot_status"] == "RUNNING"
        assert eng3.status == "RUNNING"
        assert client.post("/api/replay/bot/start").status_code == 409
        assert client.get("/api/replay?bot=7").status_code == 422
