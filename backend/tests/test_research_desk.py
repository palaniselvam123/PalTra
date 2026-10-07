"""Research desk: a paper-only second bot next to the live one.

Never sends an order: the live client here is a stub whose order methods fail
the test if they are ever called.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

IST = ZoneInfo("Asia/Kolkata")
NOW = dt.datetime(2026, 10, 6, 10, 30, tzinfo=IST)


class LiveClientStub:
    """Stands in for the live desk's Groww client. Quotes only."""

    def __init__(self):
        self.quotes = 0

    async def refresh(self, symbol: str):
        self.quotes += 1
        ts = int(NOW.replace(second=0).timestamp())
        frame = pd.DataFrame(
            [{"ts": ts - 60, "open": 1000, "high": 1001, "low": 999, "close": 1000, "volume": 1000},
             {"ts": ts, "open": 1000, "high": 1001, "low": 999, "close": 1000, "volume": 2000}]
        )
        return 1000.0, frame, "GROWW"

    async def _never(self, *args, **kwargs):  # noqa: ARG002
        raise AssertionError("the research desk must never reach the Groww order API")

    place_entry = place_exit = place_sl = modify_sl = cancel_order = _never


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/research.db")
    sent: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", sent.append)
    import database

    database.reset_engine()
    database.init_db()
    database.alerts = sent
    yield database
    database.reset_engine()


def _live_row(database, **values):
    from models import BotConfig

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        for key, value in values.items():
            setattr(row, key, value)
        s.commit()


def _research(stub):
    from research import ResearchEngine

    eng = ResearchEngine(stub.refresh)
    cfg = eng.load_config()
    return eng, cfg


def test_research_settings_start_as_a_copy_of_live_with_nothing_armed(db):
    from models import BotConfig

    _live_row(db, symbol="TCS", trade_symbols="TCS,INFY", atr_multiplier=2.5, trading_mode="LIVE")
    eng, cfg = _research(LiveClientStub())
    assert cfg.trading_mode == "RESEARCH"
    assert cfg.atr_multiplier == 2.5 and cfg.symbol == "TCS"
    assert cfg.trade_symbols == ""
    with db.session_factory()() as s:
        # The book tag is never written back; the stored row stays PAPER.
        assert s.get(BotConfig, 2).trading_mode == "PAPER"
        assert s.get(BotConfig, 1).trading_mode == "LIVE"


@pytest.mark.asyncio
async def test_research_fills_locally_even_while_the_live_desk_is_live(db):
    from models import TradeLog, trade_ref

    _live_row(db, trading_mode="LIVE", trade_symbols="TCS")
    stub = LiveClientStub()
    eng, cfg = _research(stub)
    with db.session_factory()() as s:
        from models import BotConfig

        row = s.get(BotConfig, 2)
        row.symbol = row.trade_symbols = "RELIANCE"
        row.qty = 5
        s.commit()
    cfg = eng.load_config()
    eng._focus = "RELIANCE"
    eng._ltps["RELIANCE"] = 1000.0
    await eng._open("LONG", 1000.0, 2.0, cfg, NOW)
    pos = eng.positions["RELIANCE"]
    assert pos.mode == "RESEARCH"
    await eng._square_off("MANUAL_CLOSE")
    assert "RELIANCE" not in eng.positions
    with db.session_factory()() as s:
        rows = s.query(TradeLog).all()
    assert [(r.mode, trade_ref(r.mode, r.run_id, r.book_seq)) for r in rows] == [("RESEARCH", "Q-1")]
    assert rows[0].exit_price is not None
    assert db.alerts == []  # research sends no Telegram/WhatsApp alerts


@pytest.mark.asyncio
async def test_quotes_come_from_the_live_client(db):
    stub = LiveClientStub()
    eng, _cfg = _research(stub)
    ltp, frame, source = await eng.broker.refresh("TCS")
    assert ltp == 1000.0 and source == "GROWW" and stub.quotes == 1
    # Only the quote function is kept, never the client and its order methods.
    assert not any(value is stub for value in vars(eng.broker).values())


@pytest.mark.asyncio
async def test_books_do_not_mix(db):
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    _live_row(db, symbol="TCS", trade_symbols="TCS", qty=1)
    live = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    live_cfg = live.load_config()
    live._focus = "TCS"
    live._ltps["TCS"] = 3000.0
    await live._open("LONG", 3000.0, 2.0, live_cfg, NOW)

    eng, _ = _research(LiveClientStub())
    with db.session_factory()() as s:
        from models import BotConfig

        row = s.get(BotConfig, 2)
        row.symbol = row.trade_symbols = "INFY"
        s.commit()
    cfg = eng.load_config()
    eng._focus = "INFY"
    eng._ltps["INFY"] = 1500.0
    await eng._open("SHORT", 1500.0, 2.0, cfg, NOW)

    # The live panic square-off closes the live book only.
    await live.kill("test panic")
    assert "TCS" not in live.positions and "INFY" in eng.positions

    # After a restart each engine picks up only its own open rows.
    fresh_live = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    fresh_live.restore_open_books()
    fresh_research, _ = _research(LiveClientStub())
    fresh_research.restore_open_books()
    assert set(fresh_live.positions) == set()
    assert set(fresh_research.positions) == {"INFY"}

    # KPIs: the closed live trade is in PAPER, nothing of it in RESEARCH.
    live._session_date = eng._session_date = NOW.date().isoformat()
    assert live._kpis("PAPER")["trades"] == 1
    assert eng._kpis("RESEARCH")["trades"] == 0


def test_api_keeps_the_desks_apart(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        live_before = client.get("/api/config").json()
        research = client.get("/api/research/config").json()
        assert research["trading_mode"] == "RESEARCH"
        assert research["trade_symbols"] == []

        saved = client.put("/api/research/config", json={"atr_multiplier": 3.3, "use_vwap": True})
        assert saved.status_code == 200 and saved.json()["trading_mode"] == "RESEARCH"
        armed = client.post("/api/research/trade-symbols", json={"symbol": "SBIN", "armed": True})
        assert armed.status_code == 200 and armed.json()["trade_symbols"] == ["SBIN"]

        live_after = client.get("/api/config").json()
        assert live_after["atr_multiplier"] == live_before["atr_multiplier"]
        assert live_after["trade_symbols"] == live_before["trade_symbols"]
        assert live_after["trading_mode"] == "PAPER"

        # Up to 10 armed stocks on the research desk.
        for name in ("A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9"):
            assert client.post("/api/research/trade-symbols", json={"symbol": name, "armed": True}).status_code == 200
        over = client.post("/api/research/trade-symbols", json={"symbol": "B1", "armed": True})
        assert over.status_code == 409 and "10 stocks" in over.json()["detail"]

        # Starting the research bot leaves the live bot alone, and the other way round.
        assert client.post("/api/research/bot/start").json()["bot_status"] == "RUNNING"
        assert client.get("/api/state").json()["bot_status"] != "RUNNING"
        state = client.get("/api/research/state").json()
        assert state["desk"] == "research" and state["mode"] == "RESEARCH"
        client.post("/api/research/bot/pause")

        # There is no research mode switch: it cannot be put in LIVE.
        assert client.post("/api/research/mode", json={"mode": "LIVE", "confirm_live": False}).status_code in (404, 405)
        assert "RESEARCH" in client.get("/api/trades/counts").json()
        assert client.get("/api/trades/book", params={"mode": "RESEARCH"}).status_code == 200
    database.reset_engine()


def test_past_candle_markers_keep_each_desk_to_its_own_trades():
    from types import SimpleNamespace

    from candle_history import _one_book

    rows = [SimpleNamespace(mode=m, run_id=None, entry_time=None, id=i) for i, m in enumerate(("PAPER", "LIVE", "RESEARCH"))]
    assert [r.mode for r in _one_book(rows, 0, 10, None)] == ["PAPER", "LIVE"]
    assert [r.mode for r in _one_book(rows, 0, 10, None, "RESEARCH")] == ["RESEARCH"]
