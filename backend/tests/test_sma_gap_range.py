"""SMA gap range entry filter: separate buy and sell ranges, signed %, off unless ticked."""
from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from indicators import entry_filter_reason, sma_gap_signed
from strategy_engine import STOCK_FIELDS, _entry_block, _filter_note, _short_block_label, entry_checks


def _frame(fast: float, slow: float) -> pd.DataFrame:
    """Three candles; the middle one is the closed cross candle, the last is forming."""
    rows = []
    for i in range(3):
        rows.append({"ts": 1_790_000_000 + 60 * i, "open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
                     "volume": 1000 * (i + 1), "sma_9": fast, "sma_21": slow})
    return pd.DataFrame(rows)


def _cfg(**over):
    base = dict(use_vwap=False, use_volume=False, use_density=False, use_rsi=False, use_bollinger=False,
                use_adx_filter=False, use_gap_long=True, gap_long_min=0.02, gap_long_max=0.5,
                use_gap_short=True, gap_short_min=-0.5, gap_short_max=-0.02)
    base.update(over)
    return SimpleNamespace(**base)


def test_gap_is_signed():
    assert sma_gap_signed(100.1, 100.0) == pytest.approx(0.1)
    assert sma_gap_signed(99.9, 100.0) == pytest.approx(-0.1)
    assert sma_gap_signed(None, 100.0) is None


def test_off_unless_ticked():
    assert entry_filter_reason(_frame(100.001, 100.0), "LONG") is None
    assert _entry_block(_frame(100.001, 100.0), "LONG", _cfg(use_gap_long=False)) is None


def test_buy_range():
    assert _entry_block(_frame(100.1, 100.0), "LONG", _cfg()) is None  # +0.10% inside 0.02..0.5
    reason = _entry_block(_frame(100.005, 100.0), "LONG", _cfg())  # +0.005% too small
    assert reason == "SMA gap +0.005% is outside the buy range 0.02% to 0.5%"
    assert _short_block_label(reason) == "Gap +0.005%"
    assert "outside" in _entry_block(_frame(101.0, 100.0), "LONG", _cfg())  # +1% too wide


def test_sell_range_uses_negative_numbers_and_its_own_tick():
    assert _entry_block(_frame(99.9, 100.0), "SHORT", _cfg()) is None  # -0.10% inside -0.5..-0.02
    assert "sell range" in _entry_block(_frame(99.99, 100.0), "SHORT", _cfg())  # -0.01% too small
    # Only buys ticked: a sell is not checked at all.
    assert _entry_block(_frame(99.99, 100.0), "SHORT", _cfg(use_gap_short=False)) is None
    # Positive numbers are allowed too, e.g. a sell range that straddles zero.
    assert _entry_block(_frame(100.01, 100.0), "SHORT", _cfg(gap_short_min=-0.2, gap_short_max=0.05)) is None


def test_empty_range_says_so():
    reason = _entry_block(_frame(100.1, 100.0), "LONG", _cfg(gap_long_min=0.3, gap_long_max=0.1))
    assert "range is empty" in reason


def test_check_lines_and_note():
    ok = entry_checks(_frame(100.1, 100.0), "LONG", _cfg())
    assert any(line.startswith("✅ SMA gap +0.100%") for line in ok)
    bad = entry_checks(_frame(100.005, 100.0), "LONG", _cfg())
    assert any(line.startswith("❌ SMA gap") for line in bad)
    # Only the side's own tick decides whether the line is shown.
    assert not any("SMA gap" in line for line in entry_checks(_frame(99.9, 100.0), "SHORT", _cfg(use_gap_short=False)))
    assert "SMA gap" in _filter_note(_cfg(use_gap_long=False))
    assert "SMA gap and candle direction are off" in _filter_note(_cfg(use_gap_long=False, use_gap_short=False))


def test_each_stock_can_set_its_own_ranges():
    assert {"use_gap_long", "gap_long_min", "gap_long_max", "use_gap_short", "gap_short_min", "gap_short_max"} <= set(STOCK_FIELDS)


def test_api_saves_signed_ranges_and_refuses_an_empty_one(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_gap_long"] is False and first["use_gap_short"] is False
        assert (first["gap_short_min"], first["gap_short_max"]) == (-0.5, -0.02)
        ok = client.put("/api/config", json={"use_gap_short": True, "gap_short_min": -0.3, "gap_short_max": 0})
        assert ok.status_code == 200, ok.text
        body = ok.json()
        assert body["use_gap_short"] is True and body["gap_short_min"] == -0.3 and body["gap_short_max"] == 0
        bad = client.put("/api/config", json={"gap_long_min": 0.4, "gap_long_max": 0.1})
        assert bad.status_code == 400 and "Buy SMA gap" in bad.json()["detail"]
        assert client.put("/api/config", json={"gap_long_max": 50}).status_code == 422
    database.reset_engine()
