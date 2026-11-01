"""The chat assistant reads only what a question needs (and everything with "Read everything")."""
from __future__ import annotations

import datetime as dt
import json
from types import SimpleNamespace

import pytest

from app.core.market_clock import IST
from app.services import sma_context
from app.services.chat_scope import Scope, classify
from app.services.sma_context import screen_facts, sma_facts


def test_classify_names_the_topics_bots_and_words():
    s = classify("How many trades today, and what were they?", "/terminal", {"view": "SMA terminal"})
    assert "today" in s.topics and "change" not in s.topics and not s.tools and s.page_bot == "1" and not s.orb

    s = classify("Why did SBIN not reverse on the 1 pm cross?", "/terminal", {"view": "SMA terminal", "bot": 3})
    assert "crosses" in s.topics and "SBIN" in s.words and s.page_bot == "3" and not s.tools

    s = classify("Turn on the VWAP check on bot 2")
    assert {"change", "settings"} <= s.topics and s.tools and s.bots == {"2"}

    # A question is not an order: "use" in "how do I use" asks for the guide, not the tools.
    s = classify("How do I use the Bollinger exit well?")
    assert "howto" in s.topics and "change" not in s.topics and not s.tools
    s = classify("Can you turn on VWAP on bot 3?")
    assert "change" in s.topics and s.tools and s.bots == {"3"}

    s = classify("What strategy did yesterday's trades use?")
    assert "past" in s.topics and "change" not in s.topics

    s = classify("Which stocks are armed on all bots?")
    assert s.all_bots and not s.bots

    s = classify("Why is trading locked right now?", "/", None)
    assert s.orb  # a desk page, nothing about the SMA bots
    s = classify("Why isn't bot 2 buying anything?", "/", None)
    assert not s.orb and s.bots == {"2"}

    # A follow-up keeps the subject of the question before it.
    s = classify("Show the arithmetic for that trade", "/terminal", None, history=[{"role": "user", "content": "What did yesterday's SBIN trade use?"}, {"role": "assistant", "content": "..."}])
    assert "past" in s.topics and "SBIN" in s.words

    s = classify("hello", "/terminal", None)
    assert s.fallback and s.topics == {"today", "crosses"}

    full = classify("anything", full=True)
    assert full.full and full.wants("past") and full.tools and full.orb


def test_screen_drops_the_settings_block_unless_asked_about_them():
    screen = {"view": "SMA terminal", "settings": {"qty": 10, "use_vwap": True}, "todays_trades": [{"symbol": "SBIN"}], "mode": "PAPER"}
    trimmed = screen_facts("/terminal", screen, classify("Why did SBIN not trade at 1 pm?"))
    assert "left out" in trimmed["data"]["settings"] and "todays_trades" not in trimmed["data"] and trimmed["data"]["mode"] == "PAPER"
    kept = screen_facts("/terminal", screen, classify("Which strategy am I using?"))
    assert kept["data"]["settings"] == {"qty": 10, "use_vwap": True}
    everything = screen_facts("/terminal", screen, Scope(full=True))
    assert everything["data"] == screen


def _terminal_with_sbin(tmp_path, monkeypatch):
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
        cfg.trade_symbols = "SBIN,TCS"
        yesterday = (today - dt.timedelta(days=1)).isoformat()
        for sym in ("SBIN", "TCS"):
            db.add(
                TradeLog(
                    date=yesterday, symbol=sym, direction="SHORT", qty=10, entry_time=dt.datetime(2026, 10, 30, 10, 5),
                    entry_price=800.0, ma_cross_price=800.0, atr_at_entry=2.0, sl_trigger_price=805.0,
                    exit_time=dt.datetime(2026, 10, 30, 11, 0), exit_price=790.0, exit_reason="TSL_HIT", gross_pnl=100.0,
                    brokerage_and_taxes=10.0, net_pnl=90.0, mode="PAPER", entry_reason="SMA_CROSS", bot=1,
                    strategy=json.dumps({"stop_type": "TSL", "tsl_sl_points": 5.0}),
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

    rows = [
        {"ts": at(11, 58), "close": 800.0, "sma_9": 799.0, "sma_21": 800.0},
        {"ts": at(11, 59), "close": 801.0, "sma_9": 800.5, "sma_21": 800.0},
        {"ts": at(12, 0), "close": 802.0, "sma_9": 801.0, "sma_21": 800.2},
        {"ts": at(13, 59), "close": 798.0, "sma_9": 799.0, "sma_21": 800.0},
        {"ts": at(14, 0), "close": 797.0, "sma_9": 798.0, "sma_21": 799.8},
        {"ts": at(14, 30), "close": 797.0, "sma_9": 798.0, "sma_21": 799.8},
    ]
    engine._frames["SBIN"] = pd.DataFrame(rows)
    engine._frames["TCS"] = pd.DataFrame(rows)
    fake = SimpleNamespace(
        engine=engine, _config_dict=sma_main._config_dict, ConfigUpdate=sma_main.ConfigUpdate,
        strategy_defaults=sma_main.strategy_defaults, bot_engines={}, research_engine=None,
    )
    monkeypatch.setattr(sma_context, "_terminal", lambda: fake)
    return database


def test_facts_carry_only_what_the_question_needs(tmp_path, monkeypatch):
    database = _terminal_with_sbin(tmp_path, monkeypatch)
    try:
        today = sma_facts(classify("How many trades today, and what were they?", "/terminal", {"view": "SMA terminal"}))
        assert today["trades_today_count"] == 1 and today["trades_today"][0]["symbol"] == "SBIN" and today["today_kpis"]
        for absent in ("strategy_guide", "settings", "settings_catalog", "recent_trades", "strategies_used", "recent_backtests"):
            assert absent not in today, absent
        assert "stocks_today" not in today["bots"][0] and "settings_differing_from_defaults" in today["bots"][0]
        assert today["sections"] == ["today"]

        why = sma_facts(classify("Why did SBIN not reverse on the 1 pm cross?", "/terminal", {"view": "SMA terminal"}))
        assert why["stocks_asked_about"] == ["SBIN"]
        stocks = why["bots"][0]["stocks_today"]
        assert [s["symbol"] for s in stocks] == ["SBIN"] and [c["signal"] for c in stocks[0]["crosses_today"]] == ["BUY", "SELL"]
        assert "recent_trades" not in why and "settings_catalog" not in why and "strategy_guide" not in why

        broad = sma_facts(classify("Why isn't the bot buying anything?", "/terminal", {"view": "SMA terminal"}))
        assert sorted(s["symbol"] for s in broad["bots"][0]["stocks_today"]) == ["SBIN", "TCS"]

        past = sma_facts(classify("What strategy did yesterday's TCS trade use?"))
        assert [t["symbol"] for t in past["recent_trades"]] == ["TCS"] and past["strategies_used"]["S1"]["stop_type"] == "TSL"
        assert "stocks_today" not in past["bots"][0] and "settings_catalog" not in past

        change = sma_facts(classify("Turn on the VWAP check on bot 1"))
        assert "use_vwap" in change["settings_catalog"] and change["settings"] and change["strategy_guide"]
        assert "recent_trades" not in change

        everything = sma_facts(Scope(full=True))
        assert everything["settings"] and everything["settings_catalog"] and everything["recent_trades"] and everything["strategy_guide"]
        assert len(everything["recent_trades"]) == 2 and everything["bots"][0]["settings"]
        assert sorted(s["symbol"] for s in everything["bots"][0]["stocks_today"]) == ["SBIN", "TCS"]
        assert len(json.dumps(today, default=str)) < len(json.dumps(everything, default=str)) / 3
    finally:
        database.reset_engine()


@pytest.mark.asyncio
async def test_tools_and_the_orb_desk_go_only_when_asked_for(monkeypatch):
    from app.services import chat_advisor

    async def config():
        return SimpleNamespace(model="gpt-test")

    async def key(required: bool = True):
        return "sk-test"

    async def no_price(_message):
        return None

    built: list = []

    async def context(scope=None):
        built.append(scope)
        return {"trades": [], "console_log_newest_first": [], "open_positions": [], "bot": {"status": "STOPPED"}, "orb": scope.orb}

    monkeypatch.setattr(chat_advisor.ai_advisor, "config", config)
    monkeypatch.setattr(chat_advisor.ai_advisor, "_api_key", key)
    monkeypatch.setattr(chat_advisor, "build_context", context)
    monkeypatch.setattr(chat_advisor, "sma_facts", lambda scope=None: {"available": True, "trades_today_count": 1, "sections": ["today"], "stocks_asked_about": []})
    monkeypatch.setattr("app.services.price_questions.lookup_async", no_price)
    sent: list[dict] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):  # noqa: A002
            sent.append(json)
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "status": "completed",
                    "usage": {"input_tokens": 1234, "output_tokens": 56, "total_tokens": 1290},
                    "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
                },
            )

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    res = await chat_advisor.answer("How many trades today?", [], "/terminal", {"view": "SMA terminal"})
    assert "tools" not in sent[-1] and built[-1].orb is False
    summary = res["facts_summary"]
    assert summary["usage"] == {"input_tokens": 1234, "output_tokens": 56, "total_tokens": 1290}
    assert summary["scope"]["full"] is False and "today" in summary["scope"]["topics"] and summary["sections"] == ["today"]
    assert summary["facts_chars"] > 0

    await chat_advisor.answer("Turn on VWAP on bot 2", [], "/terminal", {"view": "SMA terminal"})
    assert sent[-1]["tools"]

    res = await chat_advisor.answer("How many trades today?", [], "/terminal", {"view": "SMA terminal"}, full=True)
    assert sent[-1]["tools"] and built[-1].full and built[-1].orb and res["facts_summary"]["scope"]["full"] is True
