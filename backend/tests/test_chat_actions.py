"""The chat assistant proposes Trade list and stop changes; it never applies them."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.services.chat_actions import TOOLS, parse_proposal, proposals_from


def _call(name: str, **args) -> dict:
    return {"type": "function_call", "name": name, "arguments": json.dumps(args), "call_id": "c1"}


def test_only_trade_list_and_stop_tools_exist():
    names = {tool["name"] for tool in TOOLS}
    assert names == {"propose_trade_list_change", "propose_stop_change"}
    blob = json.dumps(TOOLS).lower()
    for forbidden in ("confirm_live", "place_order", "kill", "panic", "trading_mode", "force"):
        assert forbidden not in blob


def test_trade_list_symbols_are_cleaned_and_checked():
    p, why = parse_proposal("propose_trade_list_change", json.dumps({"bot": 2, "arm": [" sbin", "SBIN", "m&m"]}))
    assert why is None and p == {"kind": "trade_list", "bot": 2, "arm": ["SBIN", "M&M"], "unarm": [], "reason": ""}

    p, why = parse_proposal("propose_trade_list_change", json.dumps({"bot": 1, "arm": ["SBIN; DROP TABLE"]}))
    assert p is None and "not valid" in why

    p, why = parse_proposal("propose_trade_list_change", json.dumps({"bot": 1, "arm": ["SBIN"], "unarm": ["SBIN"]}))
    assert p is None and "both" in why

    p, why = parse_proposal("propose_trade_list_change", json.dumps({"bot": 9, "arm": ["SBIN"]}))
    assert p is None


def test_stop_change_keeps_only_given_fields_and_the_terminal_bounds():
    p, why = parse_proposal(
        "propose_stop_change",
        json.dumps({"bot": 1, "symbol": "reliance", "stop_type": "TSL", "tsl_mode": "PERCENT", "tsl_sl_pct": 1}),
    )
    assert why is None
    assert p == {
        "kind": "stop", "bot": 1, "symbol": "RELIANCE",
        "changes": {"stop_type": "TSL", "tsl_mode": "PERCENT", "tsl_sl_pct": 1.0}, "reason": "",
    }

    p, _ = parse_proposal("propose_stop_change", json.dumps({"bot": 1, "symbol": "ALL", "atr_multiplier": 2}))
    assert p["symbol"] is None and p["changes"] == {"atr_multiplier": 2.0}

    p, why = parse_proposal("propose_stop_change", json.dumps({"bot": 1, "tsl_sl_pct": 80}))
    assert p is None and "not valid" in why

    p, why = parse_proposal("propose_stop_change", json.dumps({"bot": 1, "trading_mode": "LIVE"}))
    assert p is None


def test_unknown_or_broken_calls_are_dropped():
    found, dropped = proposals_from(
        {
            "output": [
                _call("place_order", symbol="SBIN"),
                {"type": "function_call", "name": "propose_stop_change", "arguments": "{not json"},
                _call("propose_stop_change", bot=1, use_stop=False),
            ]
        }
    )
    assert found == [{"kind": "stop", "bot": 1, "symbol": None, "changes": {"use_stop": False}, "reason": ""}]
    assert len(dropped) == 2


@pytest.mark.asyncio
async def test_answer_returns_proposals_and_saves_nothing(monkeypatch):
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
    monkeypatch.setattr(chat_advisor, "sma_facts", lambda: {"available": True})
    monkeypatch.setattr("app.services.price_questions.lookup_async", no_price)
    sent: dict = {}
    posts: list[str] = []

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers, json):  # noqa: A002
            posts.append(url)
            sent["body"] = json
            return SimpleNamespace(
                status_code=200,
                json=lambda: {
                    "status": "completed",
                    "output": [_call("propose_trade_list_change", bot=2, arm=["SBIN"], reason="you asked")],
                },
            )

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    res = await chat_advisor.answer("arm SBIN on bot 2", [], "/terminal", None)
    assert res["proposals"] == [{"kind": "trade_list", "bot": 2, "arm": ["SBIN"], "unarm": [], "reason": "you asked"}]
    assert "Apply" in res["reply"]
    assert posts == ["https://api.openai.com/v1/responses"]
    assert {t["name"] for t in sent["body"]["tools"]} == {"propose_trade_list_change", "propose_stop_change"}
