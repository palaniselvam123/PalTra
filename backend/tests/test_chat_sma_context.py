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
