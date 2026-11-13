"""stop_handoff: after a stop-loss exit the stock moves back / to Bot 2 / to Bot 3 (PAPER, stub broker, no orders)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

import stop_handoff


def _cfg(**over):
    return SimpleNamespace(**{"stop_handoff": "OFF", "stop_handoff_loss": True, **over})


def test_target_choices():
    assert stop_handoff.target(_cfg(), 1, None) == (None, "off")
    assert stop_handoff.target(_cfg(stop_handoff="BOT2"), 1, None) == (2, "")
    assert stop_handoff.target(_cfg(stop_handoff="BOT3"), 2, 1) == (3, "")
    assert stop_handoff.target(_cfg(stop_handoff="BOT3"), 3, 1)[0] is None  # already there
    assert stop_handoff.target(_cfg(stop_handoff="BACK"), 3, 1) == (1, "")
    assert stop_handoff.target(_cfg(stop_handoff="BACK"), 1, None)[0] is None  # never moved: stays
    assert stop_handoff.choice(_cfg(stop_handoff="weird")) == "OFF"
    assert stop_handoff.loss_only(SimpleNamespace()) is True
    assert "TARGET_HIT" not in stop_handoff.STOP_REASONS and "TSL_HIT" in stop_handoff.STOP_REASONS


@pytest.mark.asyncio
async def test_a_losing_stop_moves_the_stock_and_a_winning_one_stays(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/s.db")
    monkeypatch.setenv("HANDOFF_RETURN_FILE", str(tmp_path / "handoff_return.json"))
    import database
    import handoff_log
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    asked = []
    eng.handoff = lambda bot, symbol, live, to_bot=2, **_kw: asked.append((bot, symbol, to_bot)) or f"moved to Bot {to_bot} — waits"
    pos = SimpleNamespace(direction="LONG", entry_price=1087.0, mode="PAPER", trade_id=5)
    await eng._stop_handoff(_cfg(stop_handoff="BOT2"), "ACME", pos, 1076.0)  # a loss
    assert asked == [(1, "ACME", 2)]
    assert "moved to Bot 2" in eng._signals["ACME"]
    await eng._stop_handoff(_cfg(stop_handoff="BOT3"), "ACME", pos, 1095.0)  # a trailing stop in profit: stays
    assert asked == [(1, "ACME", 2)]
    await eng._stop_handoff(_cfg(stop_handoff="BOT3", stop_handoff_loss=False), "ACME", pos, 1095.0)
    assert asked[-1] == (1, "ACME", 3)
    await eng._stop_handoff(_cfg(stop_handoff="BACK"), "ACME", pos, 1076.0)  # never moved: stays
    assert len(asked) == 2 and "did not come from another bot" in eng._signals["ACME"]
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).date().isoformat()
    rows = handoff_log.rows(day=today)
    assert [r["kind"] for r in rows] == ["STOP_EXIT", "STOP_EXIT"] and rows[-1]["to_bot"] == 2
    database.reset_engine()
