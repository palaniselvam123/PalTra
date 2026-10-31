"""The chat assistant reads the SMA terminal and the page the user is on."""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from app.core.market_clock import IST
from app.services import sma_context
from app.services.sma_context import screen_facts, sma_facts


def test_without_the_terminal_in_process_only_the_guide_is_given(monkeypatch):
    monkeypatch.setattr(sma_context, "_terminal", lambda: None)
    facts = sma_facts()
    assert facts["available"] is False
    assert "BB_TARGET" in facts["strategy_guide"]["exit_reasons"]
    assert "SMA" in facts["strategy_guide"]["entry"]


def test_reads_settings_and_todays_trades_but_not_backtests(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    import main as sma_main
    from models import BotConfig, TradeLog
    from strategy_engine import StrategyEngine

    today = dt.datetime.now(IST).date().isoformat()
    with database.session_factory()() as db:
        cfg = db.get(BotConfig, 1)
        cfg.trade_symbols = "RELIANCE,TCS"
        cfg.bb_exit = "BAND"
        for i, (mode, reason, net) in enumerate(
            [("PAPER", "MA_CROSS", 120.0), ("PAPER", "BB_TARGET", 80.5), ("REPLAY", "MA_CROSS", 999.0)]
        ):
            db.add(
                TradeLog(
                    date=today, symbol="RELIANCE", direction="LONG", qty=10,
                    entry_time=dt.datetime(2026, 10, 6, 10, i), entry_price=1000.0, ma_cross_price=1000.0,
                    atr_at_entry=2.0, sl_trigger_price=997.0, exit_time=dt.datetime(2026, 10, 6, 11, i),
                    exit_price=1013.0, exit_reason=reason, gross_pnl=net + 30, brokerage_and_taxes=30.0,
                    net_pnl=net, mode=mode,
                )
            )
        db.commit()

    engine = StrategyEngine()
    fake = SimpleNamespace(engine=engine, _config_dict=sma_main._config_dict)
    monkeypatch.setattr(sma_context, "_terminal", lambda: fake)
    facts = sma_facts()
    database.reset_engine()

    assert facts["available"] is True and "error" not in facts
    assert facts["armed_stocks"] == ["RELIANCE", "TCS"]
    assert facts["settings"]["bb_exit"] == "BAND"
    assert facts["trades_today_count"] == 2  # the REPLAY backtest trade is not a real day's trade
    assert {t["exit_reason"] for t in facts["trades_today"]} == {"MA_CROSS", "BB_TARGET"}
    paper_day = next(d for d in facts["recent_days"] if d["mode"] == "PAPER")
    assert paper_day == {"date": today, "mode": "PAPER", "trades": 2, "wins": 2, "net": 200.5, "charges": 60.0}
    json.dumps(facts, default=str)  # goes to the model as JSON


def test_screen_is_capped():
    assert screen_facts(None, None) is None
    small = screen_facts("/terminal", {"bot_status": "RUNNING"})
    assert small == {"page": "/terminal", "data": {"bot_status": "RUNNING"}}
    big = screen_facts("/terminal", {"rows": ["x" * 100] * 1000})
    assert big["data_truncated"] is True and len(big["data_json_prefix"]) == sma_context.MAX_SCREEN_CHARS


@pytest.mark.asyncio
async def test_question_is_sent_with_the_sma_facts_and_the_screen(monkeypatch):
    from app.services import chat_advisor

    async def config():
        return SimpleNamespace(model="gpt-test")

    async def key(required: bool = True):
        return "sk-test"

    async def context():
        return {"trades": [], "console_log_newest_first": [], "open_positions": [], "bot": {"status": "STOPPED"}}

    async def no_price(_message):
        return None

    monkeypatch.setattr(chat_advisor.ai_advisor, "config", config)
    monkeypatch.setattr(chat_advisor.ai_advisor, "_api_key", key)
    monkeypatch.setattr(chat_advisor, "build_context", context)
    monkeypatch.setattr(chat_advisor, "sma_facts", lambda: {"available": True, "trades_today_count": 3})
    monkeypatch.setattr("app.services.price_questions.lookup_async", no_price)
    sent: dict = {}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):  # noqa: A002
            sent["url"], sent["body"] = url, json
            return SimpleNamespace(
                status_code=200,
                json=lambda: {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "3 trades today."}]}]},
            )

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    res = await chat_advisor.answer(
        "How many trades today?", [], "/terminal", {"view": "SMA terminal", "trades_today_count": 3}
    )
    assert res["reply"] == "3 trades today."
    assert res["facts_summary"]["sma_trades_today"] == 3 and res["facts_summary"]["screen"] is True
    prompt = sent["body"]["input"][-1]["content"]
    facts = json.loads(prompt.split("FACTS (the only information you have):\n", 1)[1].split("\n\nQuestion:")[0])
    assert facts["sma_terminal"]["trades_today_count"] == 3
    assert facts["screen"] == {"page": "/terminal", "data": {"view": "SMA terminal", "trades_today_count": 3}}
    assert "SMA terminal" in sent["body"]["instructions"]


def test_a_live_terminal_reaches_the_assistant_as_real_money(tmp_path, monkeypatch):
    """The ORB desk's "paper only" note once made the assistant call a LIVE terminal paper."""
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    import main as sma_main
    from models import BotConfig
    from strategy_engine import StrategyEngine

    # Only the stored mode changes; nothing here talks to Groww.
    with database.session_factory()() as db:
        db.get(BotConfig, 1).trading_mode = "LIVE"
        db.commit()
    fake = SimpleNamespace(engine=StrategyEngine(), _config_dict=sma_main._config_dict)
    monkeypatch.setattr(sma_context, "_terminal", lambda: fake)
    facts = sma_facts()
    with database.session_factory()() as db:
        db.get(BotConfig, 1).trading_mode = "PAPER"
        db.commit()
    paper = sma_facts()
    database.reset_engine()

    assert facts["execution_mode"] == "LIVE" and facts["real_money"] is True
    assert "REAL money" in facts["execution_note"]
    assert paper["execution_mode"] == "PAPER" and paper["real_money"] is False


def test_orb_mechanics_say_they_are_not_the_sma_terminal():
    from app.services import chat_advisor

    mech = chat_advisor._mechanics()
    assert "ORB desk only" in mech["applies_to"] and "sma_terminal.execution_mode" in mech["applies_to"]
    assert "REAL MONEY" in chat_advisor.SYSTEM_PROMPT
    assert "never call it paper" in chat_advisor.SYSTEM_PROMPT


def test_every_bot_and_todays_cross_timeline_reach_the_assistant(tmp_path, monkeypatch):
    """"Why did SBIN not trade / reverse at 13:00?": the crosses of the day with what the bot did on each."""
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    import pandas as pd

    import main as sma_main
    from models import BotConfig, TradeLog
    from strategy_engine import StrategyEngine

    today = dt.datetime.now(IST).date()
    with database.session_factory()() as db:
        cfg = db.get(BotConfig, 1)
        cfg.trade_symbols = "SBIN"
        cfg.entry_cutoff_time = "13:30"
        cfg.use_vwap = False
        yesterday = (today - dt.timedelta(days=1)).isoformat()
        db.add(
            TradeLog(
                date=yesterday, symbol="SBIN", direction="SHORT", qty=10, entry_time=dt.datetime(2026, 10, 30, 10, 5),
                entry_price=800.0, ma_cross_price=800.0, atr_at_entry=2.0, sl_trigger_price=805.0,
                exit_time=dt.datetime(2026, 10, 30, 11, 0), exit_price=790.0, exit_reason="TSL_HIT", gross_pnl=100.0,
                brokerage_and_taxes=10.0, net_pnl=90.0, mode="PAPER", entry_reason="SMA_CROSS", bot=1,
                strategy=json.dumps({"stop_type": "TSL", "tsl_sl_points": 5.0, "use_vwap": True}),
            )
        )
        db.add(
            TradeLog(
                date=today.isoformat(), symbol="SBIN", direction="LONG", qty=10,
                entry_time=dt.datetime(today.year, today.month, today.day, 12, 1), entry_price=801.0, ma_cross_price=801.0,
                atr_at_entry=2.0, sl_trigger_price=795.0, exit_time=dt.datetime(today.year, today.month, today.day, 14, 1),
                exit_price=803.0, exit_reason="MA_CROSS", gross_pnl=20.0, brokerage_and_taxes=5.0, net_pnl=15.0, mode="PAPER",
                entry_reason="SMA_CROSS", bot=1,
            )
        )
        db.commit()

    engine = StrategyEngine()

    def at(h, m):
        return int(dt.datetime(today.year, today.month, today.day, h, m, tzinfo=IST).timestamp())

    # 11:59 close: fast crosses above slow (traded at 12:01); 13:59 close: crosses below (after the cut-off: closes only); 14:30 forming.
    rows = [
        {"ts": at(11, 58), "close": 800.0, "sma_9": 799.0, "sma_21": 800.0},
        {"ts": at(11, 59), "close": 801.0, "sma_9": 800.5, "sma_21": 800.0},
        {"ts": at(12, 0), "close": 802.0, "sma_9": 801.0, "sma_21": 800.2},
        {"ts": at(13, 59), "close": 798.0, "sma_9": 799.0, "sma_21": 800.0},
        {"ts": at(14, 0), "close": 797.0, "sma_9": 798.0, "sma_21": 799.8},
        {"ts": at(14, 30), "close": 797.0, "sma_9": 798.0, "sma_21": 799.8},
    ]
    engine._frames["SBIN"] = pd.DataFrame(rows)
    engine._signals["SBIN"] = "SBIN SELL ignored — no new entries after 13:30"
    fake = SimpleNamespace(
        engine=engine, _config_dict=sma_main._config_dict, ConfigUpdate=sma_main.ConfigUpdate,
        strategy_defaults=sma_main.strategy_defaults, bot_engines={}, research_engine=None,
    )
    monkeypatch.setattr(sma_context, "_terminal", lambda: fake)
    facts = sma_facts()
    database.reset_engine()

    assert "error" not in facts and "bots_error" not in facts
    bot1 = facts["bots"][0]
    assert bot1["bot"] == "1" and bot1["mode"] == "PAPER" and bot1["armed_stocks"] == ["SBIN"]
    assert bot1["signal_lines"]["SBIN"].startswith("SBIN SELL ignored")
    stock = bot1["stocks_today"][0]
    assert stock["symbol"] == "SBIN" and stock["rules_in_force"]["entry_cutoff_time"] == "13:30"
    crosses = stock["crosses_today"]
    assert [c["signal"] for c in crosses] == ["BUY", "SELL"]
    assert "entered trade" in crosses[0]["what_happened"]
    assert "after the entry cut-off 13:30" in crosses[1]["what_happened"] and "closed trade" in crosses[1]["what_happened"]
    # Yesterday's trade and the strategy it used.
    past = facts["recent_trades"]
    assert past and past[0]["symbol"] == "SBIN" and past[0]["entry_reason"] == "SMA_CROSS" and past[0]["strategy_id"] == "S1"
    assert facts["strategies_used"]["S1"]["stop_type"] == "TSL"
    # The catalog names the fields the chat may change, with their bounds, never the bookkeeping ones.
    cat = facts["settings_catalog"]
    assert cat["atr_multiplier"]["le"] == 10 and cat["stop_type"]["choices"] == ["ATR", "SMA_GAP", "TSL", "SUPERTREND", "VWAP"]
    assert "trading_mode" not in cat and "trade_symbols" not in cat and "help" in cat["use_vwap"]
    json.dumps(facts, default=str)
