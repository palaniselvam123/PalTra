"""SMA cross exit switch (cross_exit, on by default).

Off: an opposite cross neither closes nor reverses the open trade; crosses only
open trades while flat. The stop / target, gap fade, Bollinger exit, candle end
and the square-off still close it; with none of them on, the trade runs to the
square-off. Replays on local fills only (no Groww order path).
"""
from __future__ import annotations

import pytest

from tests.test_gap_mode import _trades, db  # noqa: F401


@pytest.mark.asyncio
async def test_on_by_default_an_opposite_cross_closes_and_reverses(db):  # noqa: F811
    trades = await _trades(db)
    assert any(t["exit_reason"] == "MA_CROSS" for t in trades)
    # Stop-and-reverse: a new trade opens where the last one closed on a cross.
    crossed = [t for t in trades if t["exit_reason"] == "MA_CROSS"]
    assert any(any(n["entry_time"] == c["exit_time"] for n in trades) for c in crossed)


@pytest.mark.asyncio
async def test_off_with_no_other_exit_the_first_trade_runs_to_square_off(db):  # noqa: F811
    plain = await _trades(db)
    held = await _trades(db, cross_exit=False)  # _settings turns the stop off
    assert len(held) == 1 < len(plain)
    only = held[0]
    assert only["exit_reason"] == "EOD_SQUARE_OFF"
    # Same first entry as with the cross exit on: crosses still open trades while flat.
    assert (only["entry_time"], only["direction"]) == (plain[0]["entry_time"], plain[0]["direction"])


@pytest.mark.asyncio
async def test_off_other_exits_still_close_and_a_later_cross_opens_again(db):  # noqa: F811
    gap = dict(use_gap_mode=True, gap_entry_long=0.05, gap_exit_long=0.02, gap_entry_short=-0.05, gap_exit_short=-0.02)
    trades = await _trades(db, cross_exit=False, **gap)
    assert trades and not any(t["exit_reason"] == "MA_CROSS" for t in trades)
    assert any(t["exit_reason"] == "GAP_FADE" for t in trades)
    # After a gap-fade exit the bot is flat again, so a later cross can open a new trade.
    assert len(trades) >= 2


def test_api_saves_the_switch_and_a_stock_can_hold_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "cross_exit" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["cross_exit"] is True
        assert client.put("/api/config", json={"cross_exit": False}).json()["cross_exit"] is False
        assert client.put("/api/config", json={"cross_exit": True}).json()["cross_exit"] is True
        own = client.put("/api/config/stock/TCS", json={"cross_exit": False})
        assert own.status_code == 200 and own.json()["own"] == {"cross_exit": False}
        assert client.put("/api/bots/2/config", json={"cross_exit": False}).json()["cross_exit"] is False
    database.reset_engine()
