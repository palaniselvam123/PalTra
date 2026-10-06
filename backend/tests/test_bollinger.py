"""Bollinger entry filter: skip a stretched cross and a squeeze. Off by default."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

from groww_client import IST
from indicators import bollinger, entry_filter_reason
from strategy_engine import STOCK_FIELDS, _entry_block, _short_block_label, entry_checks


def _frame(closes: list[float]) -> pd.DataFrame:
    """1-minute candles on the given closes; the last row plays the forming bar."""
    t0 = int(dt.datetime(2026, 10, 6, 9, 15, tzinfo=IST).timestamp())
    rows, cum = [], 0
    for i, c in enumerate(closes):
        cum += 1000
        rows.append({"ts": t0 + 60 * i, "open": c, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": cum})
    return pd.DataFrame(rows)


def _cfg(**over):
    base = dict(
        use_vwap=False, use_volume=False, use_density=False, use_rsi=False, use_adx_filter=False,
        use_bollinger=True, bb_period=20, bb_std=2.0, bb_min_width_pct=0.15,
    )
    base.update(over)
    return SimpleNamespace(**base)


# A choppy base around 100 (bands about 1.6% wide), then the last closed bar.
BASE = [100 + (0.4 if i % 2 else -0.4) for i in range(30)]


def test_bands_are_the_20_sma_and_two_population_sd():
    closes = pd.Series([float(x) for x in range(1, 41)])
    mid, upper, lower = bollinger(closes, 20, 2.0)
    window = closes.iloc[-20:]
    assert mid.iloc[-1] == pytest.approx(window.mean())
    assert upper.iloc[-1] == pytest.approx(window.mean() + 2 * window.std(ddof=0))
    assert lower.iloc[-1] == pytest.approx(window.mean() - 2 * window.std(ddof=0))
    assert pd.isna(mid.iloc[18])  # not ready before 20 closes


def test_off_by_default():
    frame = _frame(BASE + [110.0, 110.0])
    assert entry_filter_reason(frame, "LONG") is None


def test_a_buy_that_closed_above_the_upper_band_is_stretched():
    frame = _frame(BASE + [102.5, 102.5])  # closed bar far above the band; last row is forming
    reason = _entry_block(frame, "LONG", _cfg())
    assert reason and "above the upper band" in reason
    # The same bar is fine for a sell: it is not below the lower band.
    assert _entry_block(frame, "SHORT", _cfg()) is None


def test_a_sell_below_the_lower_band_is_stretched_and_inside_passes():
    low = _frame(BASE + [97.5, 97.5])
    assert "below the lower band" in _entry_block(low, "SHORT", _cfg())
    inside = _frame(BASE + [100.2, 100.2])
    assert _entry_block(inside, "LONG", _cfg()) is None
    assert _entry_block(inside, "SHORT", _cfg()) is None


def test_a_squeeze_is_refused_and_zero_turns_that_check_off():
    flat = _frame([100 + (0.01 if i % 2 else -0.01) for i in range(30)] + [100.0, 100.0])
    reason = _entry_block(flat, "LONG", _cfg())
    assert reason and reason.startswith("Bollinger squeeze")
    assert _short_block_label(reason) == "BB squeeze"
    assert _entry_block(flat, "LONG", _cfg(bb_min_width_pct=0)) is None


def test_not_enough_candles_says_so():
    assert "needs 20 candles" in _entry_block(_frame([100.0] * 10), "LONG", _cfg())


def test_checks_line_names_the_band_reading():
    inside = _frame(BASE + [100.2, 100.2])
    lines = entry_checks(inside, "LONG", _cfg())
    assert any(line.startswith("✅ Bollinger:") and "wide" in line for line in lines)
    out = entry_checks(_frame(BASE + [102.5, 102.5]), "LONG", _cfg())
    assert any(line.startswith("❌ Bollinger:") for line in out)


def test_each_stock_can_set_its_own_bollinger():
    assert {"use_bollinger", "bb_period", "bb_std", "bb_min_width_pct"} <= set(STOCK_FIELDS)


def test_api_saves_the_filter_and_a_zero_squeeze(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_bollinger"] is False and first["bb_min_width_pct"] == 0.15
        ok = client.put("/api/config", json={"use_bollinger": True, "bb_period": 30, "bb_min_width_pct": 0})
        assert ok.status_code == 200
        body = ok.json()
        assert body["use_bollinger"] is True and body["bb_period"] == 30 and body["bb_min_width_pct"] == 0
        assert client.put("/api/config", json={"bb_period": 2}).status_code == 422
    database.reset_engine()
