"""Scalp monitor: each reading is computed from the candles shown, the score
can be explained from its columns, and alerts only watch. No order is placed."""
from __future__ import annotations

import datetime as dt

import pytest

from app.services.indicators import OHLCV
from app.services.scalp_monitor import (
    MIN_BARS,
    _wilder_atr,
    alert_text,
    score_row,
)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
T0 = int(dt.datetime(2026, 10, 5, 9, 15, tzinfo=IST).timestamp())


def _bars(closes: list[float], volumes: list[int] | None = None, span: float = 0.4, start: int = T0) -> list[OHLCV]:
    out = []
    prev = closes[0]
    for i, close in enumerate(closes):
        out.append(
            OHLCV(
                ts=start + i * 60,
                open=prev,
                high=max(prev, close) + span / 2,
                low=min(prev, close) - span / 2,
                close=close,
                volume=(volumes[i] if volumes else 10_000),
            )
        )
        prev = close
    return out


def _quote(ltp: float, spread: float = 0.04) -> dict:
    return {"ltp": ltp, "bid": ltp - spread / 2, "ask": ltp + spread / 2, "volume": 0}


def test_a_short_history_is_warming_up_not_ready():
    row = score_row("TCS", _bars([100.0] * 5), _quote(100.0))
    assert not row.ready
    assert any("warming up" in r for r in row.reasons)
    assert row.bars == 5 < MIN_BARS


def test_readings_come_from_the_candles():
    closes = [100 + (i % 2) * 0.5 for i in range(40)]
    vols = [10_000] * 39 + [40_000]
    bars = _bars(closes, vols)
    ltp = bars[-1].close
    row = score_row("TCS", bars, _quote(ltp))
    closed = bars[:-1]
    assert row.atr_pct == pytest.approx(_wilder_atr(closed[-56:]) / ltp * 100)
    assert row.spread_pct == pytest.approx(0.04 / ltp * 100)
    # Last closed bar (10k) against the 20 before it (10k): 1×.
    assert row.volume_ratio == pytest.approx(1.0)
    assert row.move_5m_pct == pytest.approx((ltp - closed[-5].close) / closed[-5].close * 100)
    traded = sum(b.close * b.volume for b in closed)
    assert row.value_cr == pytest.approx(traded / 1e7)
    assert row.as_of == closed[-1].ts


def test_a_liquid_moving_tight_stock_is_ready_and_scores_high():
    # Trending, wide-ranging bars, heavy volume, a 1-paise spread.
    closes = [500 + i * 0.6 for i in range(60)]
    vols = [200_000] * 58 + [700_000, 200_000]
    row = score_row("RELIANCE", _bars(closes, vols, span=1.5), _quote(closes[-1], spread=0.05))
    assert row.ready, row.reasons
    assert row.bias == "LONG"
    assert row.score >= 70


def test_a_wide_spread_is_not_ready_and_costs_score():
    closes = [500 + i * 0.6 for i in range(60)]
    bars = _bars(closes, [200_000] * 60, span=1.5)
    tight = score_row("X", bars, _quote(closes[-1], spread=0.05))
    wide = score_row("X", bars, _quote(closes[-1], spread=2.0))
    assert not wide.ready and any("spread" in r for r in wide.reasons)
    assert wide.score < tight.score


def test_thin_trading_and_a_dull_stock_say_why():
    row = score_row("SLEEPY", _bars([50.0] * 40, [100] * 40, span=0.02), _quote(50.0, spread=0.01))
    assert not row.ready
    joined = " ".join(row.reasons)
    assert "ATR" in joined and "cr traded" in joined


def test_only_todays_candles_count_for_vwap_and_value():
    yesterday = _bars([90.0] * 30, [1_000_000] * 30, start=T0 - 86_400)
    today = _bars([100.0] * 30, [10_000] * 30)
    row = score_row("TCS", yesterday + today, _quote(100.0))
    assert row.value_cr == pytest.approx(sum(b.close * b.volume for b in today[:-1]) / 1e7)
    assert row.vwap == pytest.approx(100.0, abs=0.5)
    assert row.change_pct == pytest.approx(0.0)


def test_alert_text_is_watch_only():
    closes = [500 + i * 0.6 for i in range(60)]
    row = score_row("RELIANCE", _bars(closes, [200_000] * 60, span=1.5), _quote(closes[-1], 0.05))
    text = alert_text(row)
    assert text.startswith("PalTra scalp watch") and "RELIANCE" in text
    assert "no order placed" in text


def test_due_alerts_respect_score_and_cooldown(monkeypatch):
    from app.api import routes_scalp

    mon = routes_scalp._Monitor()
    mon.alert_min_score = 50
    mon.alert_cooldown_min = 30
    closes = [500 + i * 0.6 for i in range(60)]
    good = score_row("A", _bars(closes, [200_000] * 60, span=1.5), _quote(closes[-1], 0.05))
    dull = score_row("B", _bars([50.0] * 40, [100] * 40, span=0.02), _quote(50.0, 0.01))
    assert [r.symbol for r in mon.due_alerts([good, dull])] == ["A"]
    mon.last_sent["A"] = routes_scalp.ist_now()
    assert mon.due_alerts([good]) == []


@pytest.mark.asyncio
async def test_no_alert_on_simulated_prices(monkeypatch):
    from app.api import routes_scalp
    from app.services.market_data import DataSource

    sent = []

    async def fake_send(message):
        sent.append(message)

    mon = routes_scalp._Monitor()
    monkeypatch.setattr(routes_scalp.market_data, "source", DataSource.SIMULATED)
    monkeypatch.setattr(routes_scalp.alert_notifier, "send", fake_send)
    assert await mon.alert_once() == []
    assert sent == []


def test_the_monitor_endpoint_ranks_the_streaming_stocks(monkeypatch):
    from fastapi.testclient import TestClient

    from app import state
    from app.api import routes_scalp
    from app.main import app

    closes = [500 + i * 0.6 for i in range(60)]
    series = {
        "GOOD": _bars(closes, [200_000] * 60, span=1.5),
        "DULL": _bars([50.0] * 40, [100] * 40, span=0.02),
    }
    monkeypatch.setattr(routes_scalp.market_data, "symbols", ["DULL", "GOOD"])
    monkeypatch.setattr(routes_scalp.candle_store, "get", lambda s, *_a, **_k: series[s])
    monkeypatch.setitem(state.latest_quotes, "GOOD", _quote(closes[-1], 0.05))
    monkeypatch.setitem(state.latest_quotes, "DULL", _quote(50.0, 0.01))
    body = TestClient(app).get("/api/scalp/monitor").json()
    assert [r["symbol"] for r in body["rows"]] == ["GOOD", "DULL"]
    assert body["ready"] == 1 and body["universe"] == 2
    assert body["alerts"]["enabled"] is False
    only = TestClient(app).get("/api/scalp/monitor", params={"only_ready": True}).json()
    assert [r["symbol"] for r in only["rows"]] == ["GOOD"]
