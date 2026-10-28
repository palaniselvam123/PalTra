"""Price level check (level_check.py): enter only beyond a Bollinger band or a pivot level."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd

import level_check
from strategy_engine import _entry_block

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _frame(prev_day, today):
    """1-minute rows: yesterday's closes, then today's, plus one forming candle."""
    rows, t = [], dt.datetime(2026, 10, 27, 9, 15, tzinfo=IST)
    for c in prev_day:
        rows.append({"ts": int(t.timestamp()), "open": c, "high": c + 0.5, "low": c - 0.5, "close": c})
        t += dt.timedelta(minutes=1)
    t = dt.datetime(2026, 10, 28, 9, 15, tzinfo=IST)
    for c in today + [today[-1]]:
        rows.append({"ts": int(t.timestamp()), "open": c, "high": c + 0.5, "low": c - 0.5, "close": c})
        t += dt.timedelta(minutes=1)
    return pd.DataFrame(rows)


YESTERDAY = [100.0] * 30  # H 100.5, L 99.5, C 100 -> P 100, R1 100.5, S1 99.5


def test_pivot_r1_for_a_buy_and_s1_for_a_sell():
    cfg = SimpleNamespace(use_level=True, level_line="PIVOT_R1")
    above = _frame(YESTERDAY, [100.2, 100.8])
    assert level_check.entry_reason(above, "LONG", cfg) is None
    below = _frame(YESTERDAY, [100.2, 100.3])
    assert "not above R1 100.50" in level_check.entry_reason(below, "LONG", cfg)
    assert level_check.entry_reason(_frame(YESTERDAY, [99.0]), "SHORT", cfg) is None
    assert "not below S1 99.50" in level_check.entry_reason(below, "SHORT", cfg)
    p = SimpleNamespace(use_level=True, level_line="PIVOT_P")
    assert level_check.entry_reason(below, "LONG", p) is None  # 100.3 above P 100


def test_bollinger_middle_and_upper():
    rising = [100 + i * 0.05 for i in range(40)]
    mid = SimpleNamespace(use_level=True, level_line="BB_MIDDLE", bb_period=20, bb_std=2.0)
    frame = _frame(YESTERDAY, rising)
    assert level_check.entry_reason(frame, "LONG", mid) is None  # a steady rise closes above its 20-candle mean
    assert "not below the Bollinger middle band" in level_check.entry_reason(frame, "SHORT", mid)
    upper = SimpleNamespace(use_level=True, level_line="BB_UPPER", bb_period=20, bb_std=2.0)
    assert "not above the upper Bollinger band" in level_check.entry_reason(frame, "LONG", upper)
    jump = _frame(YESTERDAY, [100.0] * 25 + [103.0])
    assert level_check.entry_reason(jump, "LONG", upper) is None


def test_not_ready_refuses_and_entry_block_reads_it_only_when_ticked():
    cfg = SimpleNamespace(use_level=True, level_line="PIVOT_R1")
    only_today = _frame([], [100.0, 101.0]).iloc[0:]
    only_today = only_today[only_today["ts"] >= int(dt.datetime(2026, 10, 28, tzinfo=IST).timestamp())]
    assert "not ready" in level_check.entry_reason(only_today, "LONG", cfg)
    below = _frame(YESTERDAY, [100.2, 100.3])
    assert "Price level" in (_entry_block(below, "LONG", cfg) or "")
    assert _entry_block(below, "LONG", SimpleNamespace(use_level=False, level_line="PIVOT_R1")) is None
    assert level_check.line_of(SimpleNamespace(level_line="nonsense")) == "BB_MIDDLE"


def test_api_saves_the_level_check(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["use_level"] is False and got["level_line"] == "BB_MIDDLE"
        ok = client.put("/api/bots/2/config", json={"use_level": True, "level_line": "PIVOT_R1"}).json()
        assert ok["use_level"] is True and ok["level_line"] == "PIVOT_R1"
        assert client.put("/api/bots/2/config", json={"level_line": "R9"}).status_code == 422
    database.reset_engine()
