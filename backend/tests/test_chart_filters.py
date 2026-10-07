"""The chart shows what the entry filters see: VWAP, RSI and refused crosses."""
from __future__ import annotations

import datetime as dt
import math
from types import SimpleNamespace

import pandas as pd
import pytest

from groww_client import IST
from indicators import _session_vwap, enrich, rsi_wilder, session_vwap_series
from strategy_engine import _entry_block, candle_rows, chart_filters, filter_blocks


def _frame(days=(dt.date(2026, 9, 28), dt.date(2026, 9, 29)), bars=200) -> pd.DataFrame:
    rows = []
    for d, day in enumerate(days):
        t0 = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        cum = 0
        for i in range(bars):
            mid = 1000 + 8 * math.sin((i + 7 * d) / 9.0) + 2 * math.sin(i / 2.3)
            o = mid - 0.4
            c = mid + (0.6 if math.cos((i + 7 * d) / 9.0) >= 0 else -0.6)
            cum += 500 + (i * 37) % 900
            rows.append(
                {
                    "ts": int((t0 + dt.timedelta(minutes=i)).timestamp()),
                    "open": o,
                    "high": max(o, c) + 0.5,
                    "low": min(o, c) - 0.5,
                    "close": c,
                    "volume": cum,
                }
            )
    return enrich(pd.DataFrame(rows))


def _cfg(**over):
    base = dict(
        use_vwap=False, use_volume=False, use_density=False, use_rsi=False, use_adx_filter=False,
        adx_threshold=20, volume_min_ratio=1.0, density_min_pct=50.0,
        rsi_long_min=40.0, rsi_long_max=70.0, rsi_short_min=30.0, rsi_short_max=60.0,
        use_stop=True, stop_type="ATR",
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_the_vwap_line_is_the_vwap_the_filter_reads_on_every_candle():
    frame = _frame()
    series = session_vwap_series(frame)
    for i in range(5, len(frame), 17):
        expected = _session_vwap(frame.iloc[: i + 1])
        assert series.iloc[i] == pytest.approx(expected)
    # It restarts with each session.
    second = frame.index[pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date == dt.date(2026, 9, 29)][0]
    # The earlier session does not count: same value from that session's candles alone.
    assert series.iloc[second + 5] == pytest.approx(_session_vwap(frame.iloc[second : second + 6]))


def test_candles_carry_vwap_and_rsi():
    frame = _frame()
    rows = candle_rows(frame)
    rsi = rsi_wilder(frame["close"], 14)
    assert len(rows) == len(frame)
    assert rows[100]["rsi14"] == pytest.approx(float(rsi.iloc[100]))
    assert rows[100]["vwap"] is not None
    assert rows[0]["rsi14"] is None  # not ready yet


def test_candles_carry_the_shares_traded_in_each_minute():
    frame = _frame()
    rows = candle_rows(frame)
    # Groww's volume is a running session total; the chart gets each minute's own.
    assert rows[1]["volume"] == pytest.approx(frame["volume"].iloc[1] - frame["volume"].iloc[0])
    assert rows[50]["volume"] == pytest.approx(500 + (50 * 37) % 900)
    # The first minute of each session has no earlier total, so it is unknown, not 0.
    assert rows[0]["volume"] is None
    assert rows[200]["volume"] is None


def test_no_filter_on_means_no_refused_crosses():
    assert filter_blocks(_frame(), _cfg()) == []


def test_each_refused_cross_has_the_bots_own_reason():
    frame = _frame()
    cfg = _cfg(use_rsi=True, rsi_long_min=55.0, rsi_long_max=60.0, rsi_short_min=40.0, rsi_short_max=45.0)
    blocks = filter_blocks(frame, cfg)
    assert blocks, "narrow RSI bands should refuse some crosses"
    by_ts = {int(ts): i for i, ts in enumerate(frame["ts"])}
    for mark in blocks:
        i = by_ts[mark["time"]]
        assert mark["reason"] == _entry_block(frame.iloc[: i + 2], mark["direction"], cfg)
        assert mark["label"].startswith("RSI ")
    # A cross the filter lets through is not listed.
    loose = filter_blocks(frame, _cfg(use_rsi=True, rsi_long_min=0, rsi_long_max=100, rsi_short_min=0, rsi_short_max=100))
    assert loose == []


def test_the_adx_gate_is_shown_too():
    blocks = filter_blocks(_frame(), _cfg(use_adx_filter=True, adx_threshold=99))
    assert blocks and all(b["label"].startswith("ADX") for b in blocks)


def test_chart_filters_say_which_lines_to_draw():
    assert chart_filters(_cfg(use_vwap=True))["use_vwap"] is True
    assert chart_filters(_cfg())["atr_stop"] is True
    assert chart_filters(_cfg(stop_type="TSL"))["atr_stop"] is False
    assert chart_filters(_cfg(use_stop=False))["atr_stop"] is False


# ---- the chart reuses its work between polls --------------------------------

def _chart_engine(frame, cfg):
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    engine = StrategyEngine(broker=_FakeBroker())
    engine.candles = frame
    engine._cfg_cache = cfg
    return engine


def _uncached(frame, cfg, limit):
    """What chart_payload sent before the cache: every row and cross recomputed."""
    rows = candle_rows(frame)[-limit:]
    for key in ("sma9", "sma21", "atr14", "vwap", "rsi14", "volume"):
        rows[-1][key] = None
    first = rows[0]["time"]
    return rows, [b for b in filter_blocks(frame, cfg) if b["time"] >= first]


@pytest.mark.parametrize("limit", [240, 2500])
def test_cached_chart_is_the_same_as_recomputing(limit, monkeypatch):
    monkeypatch.setattr("strategy_engine.session_factory", _no_db)
    frame = _frame()
    cfg = _cfg(use_rsi=True, use_adx_filter=True, rsi_long_min=55.0, rsi_long_max=60.0, symbol="X", atr_multiplier=1.5, trading_mode="PAPER")
    engine = _chart_engine(frame, cfg)
    first = engine.chart_payload(limit)
    rows, blocked = _uncached(frame, cfg, limit)
    assert first["candles"] == rows and first["blocked"] == blocked and blocked
    # A poll before the next close reuses the work; only the forming bar moves.
    calls = []
    monkeypatch.setattr("strategy_engine.filter_blocks", lambda *a: calls.append(1) or [])
    moved = frame.copy()
    moved.loc[moved.index[-1], "close"] += 3.0
    engine.candles = moved
    again = engine.chart_payload(limit)
    assert calls == [] and again["blocked"] == blocked
    assert again["candles"][-1]["close"] == pytest.approx(rows[-1]["close"] + 3.0)
    # A new closed candle, or new filter settings, recompute.
    engine.candles = moved.iloc[:-1]
    engine.chart_payload(limit)
    assert calls == [1]
    engine._cfg_cache = _cfg(use_vwap=True, symbol="X", atr_multiplier=1.5, trading_mode="PAPER")
    engine.chart_payload(limit)
    assert calls == [1, 1]


class _NoRows:
    def query(self, *_a, **_k):
        return self

    filter = order_by = limit = query

    def all(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _no_db():
    return lambda: _NoRows()


def test_chart_draws_another_watched_stock_without_moving_the_focus(monkeypatch):
    monkeypatch.setattr("strategy_engine.session_factory", _no_db)
    frame = _frame()
    cfg = _cfg(symbol="X", atr_multiplier=1.5, trading_mode="PAPER", sma_fast=9, sma_slow=21, atr_period=14)
    engine = _chart_engine(frame, cfg)
    other = frame.copy()
    other["close"] = other["close"] + 50.0
    other["open"] = other["open"] + 50.0
    other["high"] = other["high"] + 50.0
    other["low"] = other["low"] + 50.0
    engine._frames["Y"] = other
    focus = engine.chart_payload(240)
    side = engine.chart_payload(240, "y")
    assert focus["symbol"] == "X" and side["symbol"] == "Y"
    assert side["candles"][-2]["close"] == pytest.approx(focus["candles"][-2]["close"] + 50.0)
    assert side["candles"][-2]["sma9"] is not None  # enriched on its own
    # The focus is untouched, and each stock keeps its own cached rows.
    assert engine.chart_payload(240)["candles"] == focus["candles"]
    assert engine.chart_payload(240, "X")["candles"] == focus["candles"]
    assert engine.chart_payload(240, "NOPE")["candles"] == []
