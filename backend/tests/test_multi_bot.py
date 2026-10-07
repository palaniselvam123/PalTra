"""Bots 2-4 next to the main desk: own books, own order clients, one LIVE bot per stock.

No order ever reaches Groww here: the shared desk client's SDK is replaced by
one that fails the test, and every "LIVE" engine uses a stub broker.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from tests.test_research_desk import db  # noqa: F401


def _row(database, cid: int, **values):
    from models import BotConfig

    with database.session_factory()() as s:
        row = s.get(BotConfig, cid)
        for key, value in values.items():
            setattr(row, key, value)
        s.commit()


def _shared():
    from groww_client import GrowwClient

    client = GrowwClient(mode="PAPER")

    def no_sdk():
        raise AssertionError("no Groww SDK call may happen in this test")

    client._require_sdk = no_sdk
    return client


def test_a_bots_mode_never_changes_another_bots_order_path(db):  # noqa: F811
    from bots import BotGrowwClient

    shared = _shared()
    bot = BotGrowwClient(shared)
    bot._require_sdk = shared._require_sdk
    # The desk going LIVE leaves the bot on PAPER, and the other way round.
    shared.set_mode("LIVE")
    assert bot.mode == "PAPER"
    ack = asyncio.run(bot.place_entry("TCS", "BUY", 1, 100.0))
    assert ack.order_id.startswith("PAPER") and ack.status == "FILLED"
    shared.set_mode("PAPER")
    bot.set_mode("LIVE")
    assert shared.mode == "PAPER" and bot.mode == "LIVE"


def test_bot_quotes_come_from_the_shared_client(db):  # noqa: F811
    from bots import BotGrowwClient

    calls = []

    class Shared:
        async def refresh(self, symbol):
            calls.append(symbol)
            return 101.0, None, "GROWW"

        async def refresh_ltps(self, symbols):
            calls.append(tuple(symbols))
            return {}

        def anchor_price(self, symbol, price):
            calls.append((symbol, price))

    from groww_client import GrowwClient

    bot = BotGrowwClient.__new__(BotGrowwClient)
    GrowwClient.__init__(bot)
    bot._shared = Shared()
    assert asyncio.run(bot.refresh("TCS"))[0] == 101.0 and bot.data_source == "GROWW"
    asyncio.run(bot.refresh_ltps(["TCS", "INFY"]))
    assert calls == ["TCS", ("TCS", "INFY")]


def test_each_bot_has_its_own_settings_book_and_trade_ids(db):  # noqa: F811
    from bots import BotEngine, config_id_for
    from models import BotConfig, TradeLog, trade_ref
    from strategy_engine import StrategyEngine

    main = StrategyEngine(broker=_shared())
    bot2 = BotEngine(2, main.broker)
    cfg2 = bot2.load_config()
    assert config_id_for(2) == 3 and cfg2.trading_mode == "PAPER" and cfg2.trade_symbols == ""
    assert cfg2.bot_name == "Bot 2"
    with db.session_factory()() as s:
        assert s.get(BotConfig, 2) is None or s.get(BotConfig, 2).id == 2  # research row untouched
    today = dt.date.today().isoformat()
    with db.session_factory()() as s:
        for bot, sym in ((None, "TCS"), (2, "INFY")):
            s.add(
                TradeLog(date=today, symbol=sym, direction="LONG", qty=1, entry_time=dt.datetime.now(),
                         entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=98.0,
                         mode="PAPER", bot=bot, book_seq=1)
            )
        s.commit()
    main.restore_open_books()
    bot2.restore_open_books()
    assert set(main.positions) == {"TCS"} and set(bot2.positions) == {"INFY"}
    assert trade_ref("PAPER", None, 1, 1, 2) == "P2-1" and trade_ref("LIVE", None, 5, 1, 1) == "N-5"
    assert trade_ref("RESEARCH", None, 3, 1, 2) == "Q-3"


def test_one_live_bot_per_stock(db):  # noqa: F811
    import bots
    from bots import BotEngine, config_id_for
    from strategy_engine import StrategyEngine

    main = StrategyEngine(broker=_shared())
    bot2 = BotEngine(2, main.broker)
    bot2.load_config()
    bots.register(main)
    bots.register(bot2)
    _row(db, 1, trading_mode="LIVE", trade_symbols="TCS,SBIN")
    _row(db, config_id_for(2), trading_mode="PAPER", trade_symbols="TCS")
    # A practice bot may watch the same stock; a LIVE one may not.
    assert bots.live_conflict(2, ["INFY"]) is None
    why = bots.live_conflict(2, ["TCS", "INFY"])
    assert why and "TCS" in why and "Bot 1" in why
    _row(db, 1, bot_name="Scalper")
    assert "Scalper" in bots.live_conflict(2, ["SBIN"])
    # Bot 1 in PAPER claims nothing.
    _row(db, 1, trading_mode="PAPER")
    assert bots.live_conflict(2, ["TCS"]) is None


def test_live_entry_is_refused_at_the_last_moment_on_a_stock_another_live_bot_owns(db):  # noqa: F811
    from groww_client import OrderAck
    from strategy_engine import SlCancelFailed, StrategyEngine

    sent = []

    class Broker:
        mode = "LIVE"

        def set_mode(self, mode, token=None):
            self.mode = mode

        async def place_entry(self, *args):
            sent.append(args)
            return OrderAck("E1", "FILLED", 100.0)

    eng = StrategyEngine(broker=Broker())
    eng.bot_id = 3
    eng.live_guard = lambda bot, symbols: "TCS is already traded LIVE by Bot 1." if "TCS" in symbols else None
    cfg = eng.load_config()
    cfg.trading_mode = "LIVE"
    cfg.symbol = "TCS"
    with pytest.raises(SlCancelFailed, match="already traded LIVE"):
        asyncio.run(eng._open("LONG", 100.0, 1.0, cfg, dt.datetime.now()))
    assert sent == []  # nothing went to the broker


def test_settling_a_live_book_never_closes_another_books_trade(db):  # noqa: F811
    from models import TradeLog
    from strategy_engine import StrategyEngine

    class Broker:
        mode = "LIVE"

        def set_mode(self, mode, token=None):
            self.mode = mode

        async def net_quantity(self, symbol):
            return 0  # Groww is flat on TCS

    eng = StrategyEngine(broker=Broker())
    cfg = eng.load_config()
    cfg.trading_mode = "LIVE"
    today = dt.date.today().isoformat()
    with db.session_factory()() as s:
        for mode, bot in (("RESEARCH", None), ("PAPER", None), ("LIVE", 2), ("LIVE", None)):
            s.add(
                TradeLog(date=today, symbol="TCS", direction="LONG", qty=1, entry_time=dt.datetime.now(),
                         entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger_price=98.0,
                         mode=mode, bot=bot)
            )
        s.commit()
    asyncio.run(eng._settle_exchange_flat(cfg, ["TCS"]))
    with db.session_factory()() as s:
        still_open = {(r.mode, r.bot) for r in s.query(TradeLog).filter(TradeLog.exit_time.is_(None))}
    # Only bot 1's own LIVE row was closed as not on Groww.
    assert still_open == {("RESEARCH", 1), ("PAPER", 1), ("LIVE", 2)}


def test_bots_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        listed = client.get("/api/bots").json()
        assert [b["bot"] for b in listed] == [1, 2, 3, 4] and all(b["mode"] == "PAPER" for b in listed)
        assert client.get("/api/bots/2/state").json()["bot"] == 2
        assert client.get("/api/bots/9/state").status_code == 404
        named = client.put("/api/bots/2/config", json={"bot_name": "Scalper"})
        assert named.status_code == 200 and named.json()["bot_name"] == "Scalper"
        assert client.get("/api/bots").json()[1]["name"] == "Scalper"
        armed = client.post("/api/bots/3/trade-symbols", json={"symbol": "TCS", "armed": True})
        assert armed.status_code == 200 and armed.json()["trade_symbols"] == ["TCS"]
        # Bot 1's settings and Trade list are untouched.
        assert "TCS" not in client.get("/api/config").json()["trade_symbols"]
        assert client.get("/api/bots/3/config").json()["trade_symbols"] == ["TCS"]
        # LIVE still needs the confirmation and a Groww session.
        assert client.post("/api/bots/2/mode", json={"mode": "LIVE"}).status_code == 400
        assert client.post("/api/bots/2/mode", json={"mode": "LIVE", "confirm_live": True}).status_code == 503
        assert client.get("/api/trades/book", params={"mode": "PAPER", "bot": 2}).json()["total"] == 0
        assert client.get("/api/trades/book", params={"mode": "PAPER", "bot": 7}).status_code == 422
    database.reset_engine()
