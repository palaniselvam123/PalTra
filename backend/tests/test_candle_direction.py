"""Candle direction entry filter: the candles before an entry move the trade's way."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from indicators import _direction_reason, entry_filter_reason
from strategy_engine import _entry_block, _short_block_label, entry_checks


def _frame(rows: list[tuple[float, float]]) -> pd.DataFrame:
    """(open, close) per closed candle, plus a forming candle at the end."""
    out = []
    for i, (o, c) in enumerate(rows + [(rows[-1][1], rows[-1][1])]):
        out.append({"ts": 1_790_000_000 + 60 * i, "open": o, "high": max(o, c) + 0.1, "low": min(o, c) - 0.1,
                    "close": c, "volume": 1000 * (i + 1)})
    return pd.DataFrame(out)


RISING = [(99.8, 100.0), (100.0, 100.4), (100.4, 100.9)]
FALLING = [(100.9, 100.6), (100.6, 100.2), (100.2, 99.8)]
MIXED = [(100.0, 100.5), (100.5, 100.3), (100.3, 100.8)]  # up, down, up


def test_closes_rule_needs_each_close_beyond_the_last():
    closed = _frame(RISING).iloc[:-1]
    assert _direction_reason(closed, "LONG", 2, "CLOSES") == []
    assert "not all falling" in _direction_reason(closed, "SHORT", 2, "CLOSES")[0]
    mixed = _frame(MIXED).iloc[:-1]
    # Only the last close rose; two in a row did not.
    assert _direction_reason(mixed, "LONG", 1, "CLOSES") == []
    reason = _direction_reason(mixed, "LONG", 2, "CLOSES")[0]
    assert "100.50 → 100.30 → 100.80" in reason


def test_colour_rule_reads_green_and_red_candles():
    assert _direction_reason(_frame(FALLING).iloc[:-1], "SHORT", 3, "COLOUR") == []
    reason = _direction_reason(_frame(MIXED).iloc[:-1], "LONG", 3, "COLOUR")[0]
    assert "not all green" in reason and "(GRG)" in reason


def test_both_rule_needs_both():
    # Green candles that gap down: colour passes, closes do not.
    gapping = [(100.0, 100.2), (99.0, 99.5), (98.0, 98.4)]
    closed = _frame(gapping).iloc[:-1]
    assert _direction_reason(closed, "LONG", 2, "COLOUR") == []
    assert len(_direction_reason(closed, "LONG", 2, "BOTH")) == 1


def test_too_few_candles_waits():
    assert "needs more candles" in _direction_reason(_frame(RISING).iloc[:-1], "LONG", 5, "CLOSES")[0]


def test_off_by_default_and_wired_through_the_engine_check():
    frame = _frame(MIXED)
    assert entry_filter_reason(frame, "LONG") is None
    cfg = SimpleNamespace(use_candle_dir=True, candle_dir_count=2, candle_dir_rule="CLOSES")
    block = _entry_block(frame, "LONG", cfg)
    assert block and block.startswith("Candle direction")
    assert _short_block_label(block) == "Candles"
    assert _entry_block(_frame(RISING), "LONG", cfg) is None
    lines = entry_checks(_frame(RISING), "LONG", cfg)
    assert lines and lines[0].startswith("✅ Candle direction: last 2 closes rising")


def test_api_saves_the_filter_and_a_stock_can_set_its_own(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import SNAPSHOT_FIELDS, STOCK_FIELDS

    assert {"use_candle_dir", "candle_dir_count", "candle_dir_rule"} <= set(STOCK_FIELDS) & set(SNAPSHOT_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_candle_dir"] is False and first["candle_dir_count"] == 2 and first["candle_dir_rule"] == "CLOSES"
        ok = client.put("/api/config", json={"use_candle_dir": True, "candle_dir_count": 3, "candle_dir_rule": "BOTH"})
        assert ok.status_code == 200, ok.text
        assert ok.json()["candle_dir_rule"] == "BOTH" and ok.json()["candle_dir_count"] == 3
        assert client.put("/api/config", json={"candle_dir_rule": "SIDEWAYS"}).status_code == 422
        assert client.put("/api/config", json={"candle_dir_count": 0}).status_code == 422
        own = client.put("/api/config/stock/TCS", json={"candle_dir_count": 1})
        assert own.status_code == 200 and own.json()["own"] == {"candle_dir_count": 1}
    database.reset_engine()


# ---- through the real engine (replay engine, local fills) -------------------------------------
import datetime as dt  # noqa: E402

import pytest  # noqa: E402

from tests.test_gap_mode import _trades, db  # noqa: E402,F401


@pytest.mark.asyncio
async def test_choppy_crosses_are_skipped_and_the_trend_still_trades(db):  # noqa: F811
    plain = await _trades(db)
    checked = await _trades(db, use_candle_dir=True, candle_dir_count=3, candle_dir_rule="CLOSES")
    trend = dt.datetime(2026, 9, 22, 10, 10)
    # 09:15-10:14 alternates up/down closes: three rising closes in a row never happen.
    assert any(t["entry_time"] < trend for t in plain)
    assert not any(t["entry_time"] < trend for t in checked)
    # The cross at the very start of the rise has no three rising closes behind it yet, so a plain
    # cross is refused. Gap mode reads the same check later, when the widening gap fires the order,
    # by which time the closes have risen: it buys the trend and still skips the chop.
    gap = await _trades(db, use_candle_dir=True, candle_dir_count=3, use_gap_mode=True)
    assert any(t["direction"] == "LONG" and t["entry_time"] >= trend for t in gap)
    assert not any(t["entry_time"] < trend for t in gap)
