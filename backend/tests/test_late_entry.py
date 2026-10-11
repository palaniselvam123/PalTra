"""Late entry (late_entry.py): a cross the VWAP filter refused trades once the filter agrees.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import late_entry
from tests.test_gap_mode import _day, _settings, db  # noqa: F401  (db is a fixture)

DAY = dt.date(2026, 9, 22)


def _frame() -> pd.DataFrame:
    flat = lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02)  # noqa: E731

    def v_shape(i, p):
        if i < 60:  # 09:15-10:14 a steady fall: VWAP sits well above the price
            return p - 0.25
        return p + 0.15  # then a steady rise: the bullish cross comes below VWAP

    return pd.DataFrame(_day(dt.date(2026, 9, 21), flat) + _day(DAY, v_shape))


async def _longs(database, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(_frame(), "SIMV", [DAY], _settings(database, use_vwap=True, **over))
    return [t for t in trades if t["direction"] == "LONG"]


@pytest.mark.asyncio
async def test_a_cross_refused_below_vwap_is_bought_once_price_is_back_above_vwap(db):  # noqa: F811
    assert await _longs(db) == []  # the VWAP filter refuses the only bullish cross
    late = await _longs(db, late_entry_min=180)
    assert len(late) == 1
    entry = late[0]["entry_time"]
    assert entry > dt.datetime(2026, 9, 22, 10, 30)  # well after the cross, once above VWAP


@pytest.mark.asyncio
async def test_a_short_window_gives_up_before_the_filter_agrees(db):  # noqa: F811
    assert await _longs(db, late_entry_min=5) == []


def test_judge_and_refusal_rules():
    t0 = 1_790_000_000
    assert late_entry.judge("LONG", t0, t0 + 600, 0.05, 30)[0] == "try"
    assert late_entry.judge("LONG", t0, t0 + 600, -0.01, 30)[0] == "drop"  # crossed back
    assert late_entry.judge("SHORT", t0, t0 + 600, 0.01, 30)[0] == "drop"
    assert late_entry.judge("LONG", t0, t0 + 1860, 0.05, 30)[0] == "drop"  # window over
    assert late_entry.refused_by_filter("BULLISH", "BULLISH ignored — VWAP: 990.00 is below 992.00")
    assert late_entry.refused_by_filter("BEARISH", "closed on BEARISH — RSI 25.0 outside 30-60")
    assert not late_entry.refused_by_filter("BULLISH", "BULLISH ignored — no new entries after 15:00 IST")
    assert not late_entry.refused_by_filter("BULLISH", "entry blocked — trade cap")
    assert not late_entry.refused_by_filter("BULLISH", "opened LONG")


def test_api_saves_late_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "late_entry_min" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["late_entry_min"] == 0
        ok = client.put("/api/config", json={"late_entry_min": 30})
        assert ok.status_code == 200 and ok.json()["late_entry_min"] == 30
        assert client.put("/api/config", json={"late_entry_min": 999}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"late_entry_min": 15})
        assert own.status_code == 200 and own.json()["late_entry_min"] == 15
    database.reset_engine()
