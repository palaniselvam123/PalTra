"""Live SMA re-check at execution (live_sma.py). PAPER only: the replays fill locally (no Groww order path)."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

import live_sma
from tests.test_entry_candle import JUMP, _fall, _replay
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)

CFG = SimpleNamespace(live_sma_check=True, sma_fast=3, sma_slow=5)


def _frame(closes):
    """Closed candles, then the forming one (its close is replaced by the live price)."""
    return pd.DataFrame({"close": closes + [closes[-1]]})


def test_live_gap_uses_the_live_price_for_the_forming_candle():
    frame = _frame([100.0, 100.0, 100.0, 101.0, 102.0])
    # SMA 3 over 101, 102, live 90 = 97.67; SMA 5 over 100, 100, 101, 102, 90 = 98.6
    assert live_sma.live_gap(frame, 90.0, 3, 5) == pytest.approx((97.6667 - 98.6) / 98.6 * 100, abs=1e-3)
    assert live_sma.live_gap(frame, None, 3, 5) is None
    assert live_sma.live_gap(pd.DataFrame({"close": [1.0, 2.0]}), 2.0, 3, 5) is None  # too few candles


def test_refusal_follows_the_live_side_and_the_minimum_gap():
    frame = _frame([100.0, 100.0, 100.0, 101.0, 102.0])
    assert live_sma.refusal(frame, 103.0, "LONG", CFG) is None
    assert "not above" in live_sma.refusal(frame, 90.0, "LONG", CFG)
    assert live_sma.refusal(frame, 90.0, "SHORT", CFG) is None
    strict = SimpleNamespace(**{**vars(CFG), "live_sma_min_gap": 5.0})
    assert "by at least 5%" in live_sma.refusal(frame, 103.0, "LONG", strict)
    assert live_sma.refusal(frame, 90.0, "LONG", SimpleNamespace()) is None  # off by default
    assert live_sma.min_gap(SimpleNamespace(live_sma_min_gap=99)) == live_sma.MAX_GAP


@pytest.mark.asyncio
async def test_off_by_default_changes_nothing(db):  # noqa: F811
    frame = _fall()
    key = lambda t: (t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"])  # noqa: E731
    assert [key(t) for t in await _replay(db, frame)] == [key(t) for t in await _replay(db, frame, live_sma_check=False)]


@pytest.mark.asyncio
async def test_an_order_at_once_is_refused_when_the_live_smas_do_not_agree(db):  # noqa: F811
    frame = _fall()
    plain = [t for t in await _replay(db, frame) if t["entry_time"] >= JUMP]
    agreed = [t for t in await _replay(db, frame, live_sma_check=True) if t["entry_time"] >= JUMP]
    refused = await _replay(db, frame, live_sma_check=True, live_sma_min_gap=5.0)
    assert plain and agreed  # a steady fall: the live SMAs agree with the sell
    assert not refused  # nothing ever has SMA 9 5% beyond SMA 21 on the live price


@pytest.mark.asyncio
async def test_a_waiting_order_is_dropped_when_the_live_smas_do_not_agree(db):  # noqa: F811
    frame = _fall()
    waited = [t for t in await _replay(db, frame, entry_candle_confirm=True, entry_candle_pct=0.01) if t["entry_time"] >= JUMP]
    dropped = await _replay(
        db, frame, entry_candle_confirm=True, entry_candle_pct=0.01, live_sma_check=True, live_sma_min_gap=5.0
    )
    assert waited and not dropped


def test_api_saves_the_live_check(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["live_sma_check"] is False and got["live_sma_min_gap"] == 0
        ok = client.put("/api/bots/2/config", json={"live_sma_check": True, "live_sma_min_gap": 0.03}).json()
        assert ok["live_sma_check"] is True and ok["live_sma_min_gap"] == 0.03
        assert client.put("/api/bots/2/config", json={"live_sma_min_gap": 9}).status_code == 422
    database.reset_engine()
