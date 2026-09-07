"""Technical-analysis gates the bot actually trades.

A golden cross into RSI 80 is the same class of error as EMA2/EMA3: it looks
like a signal and loses money. These tests pin the overbought reject so a
future tweak cannot silently start buying exhaustion again.
"""
from __future__ import annotations

from app.services.indicators import OHLCV, rsi
from app.services.scanner_engine import StrategyEngine, StrategyParams


def _bar(i: int, close: float, volume: int = 2000) -> OHLCV:
    return OHLCV(
        ts=i * 300,
        open=close * 0.999,
        high=close * 1.002,
        low=close * 0.998,
        close=close,
        volume=volume,
    )


def overbought_golden_cross() -> list[OHLCV]:
    """Uptrend, a sharp dip, then a bounce that recrosses while RSI is ≥ 70."""
    bars: list[OHLCV] = []
    price = 100.0
    for i in range(40):
        price *= 1.025
        bars.append(_bar(i, price, volume=8000))
    for i in range(40, 43):
        price *= 0.95
        bars.append(_bar(i, price, volume=3000))
    for i in range(43, 46):
        price *= 1.08
        bars.append(_bar(i, price, volume=9000))
    return bars


def test_trading_defaults_are_the_strict_intraday_set():
    p = StrategyParams.trading_defaults()
    assert p.fast_period == 9 and p.slow_period == 21
    assert p.adx_filter and p.volume_filter and p.trend_filter and p.rsi_filter
    assert p.rsi_overbought == 70
    assert p.trend_period == 50


def test_rsi_filter_rejects_an_overbought_buy():
    bars = overbought_golden_cross()
    open_engine = StrategyEngine(StrategyParams(fast_period=3, slow_period=8))
    gated = StrategyEngine(
        StrategyParams(fast_period=3, slow_period=8, rsi_filter=True, rsi_overbought=70)
    )
    open_buys = [s for s in _all_signals(open_engine, bars) if s.side == "BUY"]
    assert open_buys, "fixture must produce at least one ungated golden cross"

    overbought_buys = [
        s
        for s in open_buys
        if (rsi(bars)[_index_of(bars, s.candle_ts)] or 0) >= 70
    ]
    assert overbought_buys, "fixture must include a buy that is already stretched"

    gated_buys = [s for s in _all_signals(gated, bars) if s.side == "BUY"]
    gated_ts = {s.candle_ts for s in gated_buys}
    for s in overbought_buys:
        assert s.candle_ts not in gated_ts


def test_rsi_filter_does_not_block_a_death_cross_exit():
    """An exit has to fire even when RSI is not oversold — that is the point."""
    bars = overbought_golden_cross()
    # After the spike, drop hard so a death cross exists.
    price = bars[-1].close
    extra = list(bars)
    for i in range(len(bars), len(bars) + 25):
        price *= 0.97
        extra.append(_bar(i, price, volume=8000))

    engine = StrategyEngine(
        StrategyParams(fast_period=3, slow_period=8, rsi_filter=True, rsi_overbought=70)
    )
    sells = [s for s in _all_signals(engine, extra) if s.side == "SELL"]
    assert sells, "death-cross exits must still fire with the RSI buy-filter on"


def _all_signals(engine: StrategyEngine, bars: list[OHLCV]):
    ctx = engine.precompute(bars)
    out = []
    for i in range(len(bars)):
        sig = engine.evaluate_at("X", "5m", bars, i, ctx)
        if sig is not None:
            out.append(sig)
    return out


def _index_of(bars: list[OHLCV], ts: int) -> int:
    for i, b in enumerate(bars):
        if b.ts == ts:
            return i
    raise AssertionError(f"no bar with ts {ts}")
