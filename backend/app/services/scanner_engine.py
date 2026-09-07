"""Moving-average crossover detection for the scanner.

Two rules shape everything here:

**Signals fire on candle close, never intra-candle.** A forming bar's close is
just the current tick; a fast MA computed from it wobbles across the slow MA
repeatedly within one bar and would fire a burst of contradictory alerts. The
worker therefore evaluates only *completed* bars, and this module detects the
cross between the last two of them.

**Indicators come from `app.services.indicators`, not a second library.** The
chart, the ORB entry gate and this scanner all read the same EMA. Introducing
pandas-ta here would give the scanner its own subtly different moving average
(different seeding, different warm-up) and the alert would then disagree with
the chart the user checks it against. One implementation, one answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.indicators import OHLCV, adx, ema, rsi, sma
from app.services import patterns as candle_patterns
from app.services.explain import explain_signal

GOLDEN_CROSS = "GOLDEN_CROSS"
DEATH_CROSS = "DEATH_CROSS"
BOTH = "BOTH"


@dataclass
class StrategyParams:
    fast_period: int = 9
    fast_type: str = "EMA"          # EMA | SMA
    slow_period: int = 21
    slow_type: str = "EMA"
    signal_type: str = BOTH         # GOLDEN_CROSS | DEATH_CROSS | BOTH
    trend_filter: bool = False
    trend_period: int = 200
    volume_filter: bool = False
    volume_multiplier: float = 1.5
    volume_lookback: int = 20
    pattern_filter: bool = False
    pattern_lookback: int = 3
    adx_filter: bool = False
    adx_threshold: float = 20.0
    # Block BUY when RSI is already stretched. A golden cross into RSI 80 is
    # chasing an exhausted move; the death-cross exit is left unfiltered so a
    # held long can still get out.
    rsi_filter: bool = False
    rsi_overbought: float = 70.0

    @classmethod
    def trading_defaults(cls) -> "StrategyParams":
        """The set the bot actually trades: EMA 9/21 on closed 5m bars, with
        trend / ADX / volume / RSI gates. Alert configs can be noisier; this
        is the floor an entry has to clear.
        """
        return cls(
            fast_period=9,
            fast_type="EMA",
            slow_period=21,
            slow_type="EMA",
            signal_type=BOTH,
            trend_filter=True,
            trend_period=50,
            volume_filter=True,
            volume_multiplier=1.5,
            volume_lookback=20,
            adx_filter=True,
            adx_threshold=20.0,
            rsi_filter=True,
            rsi_overbought=70.0,
        )

    @property
    def fast_label(self) -> str:
        return f"{self.fast_type}{self.fast_period}"

    @property
    def slow_label(self) -> str:
        return f"{self.slow_type}{self.slow_period}"

    def warmup_bars(self) -> int:
        """Bars needed before any signal is trustworthy. Evaluating earlier
        would compare a seeded MA against a half-formed one.
        """
        need = max(self.fast_period, self.slow_period) + 2
        if self.trend_filter:
            need = max(need, self.trend_period + 2)
        if self.volume_filter:
            need = max(need, self.volume_lookback + 2)
        if self.pattern_filter:
            need = max(need, candle_patterns.MIN_BARS + self.pattern_lookback + 1)
        if self.adx_filter:
            need = max(need, 30)  # Wilder's ADX needs 2*period+1 bars
        if self.rsi_filter:
            need = max(need, 16)  # Wilder RSI needs period+1 closes
        return need


@dataclass
class Signal:
    symbol: str
    timeframe: str
    side: str                       # BUY | SELL
    price: float
    fast_label: str
    slow_label: str
    fast_value: float
    slow_value: float
    candle_ts: int
    volume_ratio: float | None = None
    adx_value: float | None = None
    pattern: str | None = None
    candle_pattern: str | None = None
    candle_desc: str = ""
    candle_ohlc: tuple[float, float, float, float] | None = None
    rsi_value: float | None = None
    plain_english: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def _moving_average(candles: list[OHLCV], kind: str, period: int):
    return sma(candles, period) if kind.upper() == "SMA" else ema(candles, period)


class StrategyEngine:
    """Stateless evaluator. The worker owns "have I already seen this bar";
    keeping that out of here means the engine can be re-run over history for
    backtesting without its answers depending on call order.
    """

    def __init__(self, params: StrategyParams | None = None):
        self.params = params or StrategyParams()

    def precompute(self, candles: list[OHLCV]) -> dict:
        """Every series the decision needs, computed ONCE over the full set.

        `evaluate` recomputing these per bar made a backtest O(n^2) — 1,500
        bars across 8 symbols never finished. Both paths now share this, so a
        historical answer cannot drift from a live one.
        """
        p = self.params
        ctx = {
            "fast": _moving_average(candles, p.fast_type, p.fast_period),
            "slow": _moving_average(candles, p.slow_type, p.slow_period),
            "adx": adx(candles, 14)["adx"] if p.adx_filter else None,
            "trend": _moving_average(candles, "EMA", p.trend_period) if p.trend_filter else None,
            "rsi": rsi(candles, 14) if p.rsi_filter else None,
        }
        return ctx

    def evaluate(self, symbol: str, timeframe: str, closed_candles: list[OHLCV]) -> Signal | None:
        """`closed_candles` must contain COMPLETED bars only — the caller is
        responsible for dropping the forming one.
        """
        if len(closed_candles) < self.params.warmup_bars():
            return None
        ctx = self.precompute(closed_candles)
        return self.evaluate_at(symbol, timeframe, closed_candles, len(closed_candles) - 1, ctx)

    def evaluate_at(
        self, symbol: str, timeframe: str, candles: list[OHLCV], i: int, ctx: dict
    ) -> Signal | None:
        """Decision for bar `i` using precomputed series. `candles[:i+1]` are
        the only bars considered, so there is no look-ahead."""
        p = self.params
        if i < 1 or i >= len(candles) or i + 1 < p.warmup_bars():
            return None

        fast, slow = ctx["fast"], ctx["slow"]

        f_now, f_prev = fast[i], fast[i - 1]
        s_now, s_prev = slow[i], slow[i - 1]
        if None in (f_now, f_prev, s_now, s_prev):
            return None

        crossed_up = f_prev <= s_prev and f_now > s_now
        crossed_down = f_prev >= s_prev and f_now < s_now
        if not (crossed_up or crossed_down):
            return None

        side = "BUY" if crossed_up else "SELL"
        if p.signal_type == GOLDEN_CROSS and side != "BUY":
            return None
        if p.signal_type == DEATH_CROSS and side != "SELL":
            return None

        bar = candles[i]
        reasons = [
            f"{p.fast_label} crossed {'above' if crossed_up else 'below'} {p.slow_label} "
            f"({f_now:.2f} vs {s_now:.2f})"
        ]

        # ---- filters: a failed filter kills the signal entirely ----------
        # Trend strength first: it is the cheapest way to reject a whipsaw, and
        # a crossover inside a flat range is noise regardless of what the other
        # filters say about it.
        adx_value = None
        if p.adx_filter:
            series = ctx["adx"]
            adx_value = series[i] if series else None
            if adx_value is None or adx_value < p.adx_threshold:
                return None
            reasons.append(
                f"ADX {adx_value:.1f} is at or above {p.adx_threshold:.0f} — there is a real trend to follow"
            )

        rsi_value = None
        if p.rsi_filter and side == "BUY":
            series = ctx["rsi"]
            rsi_value = series[i] if series else None
            if rsi_value is None or rsi_value >= p.rsi_overbought:
                return None
            reasons.append(
                f"RSI {rsi_value:.0f} is below {p.rsi_overbought:.0f} — not buying an already-stretched move"
            )

        if p.trend_filter:
            trend = ctx["trend"]
            t_now = trend[i] if trend else None
            if t_now is None:
                return None
            if side == "BUY" and bar.close <= t_now:
                return None
            if side == "SELL" and bar.close >= t_now:
                return None
            reasons.append(
                f"close {bar.close:.2f} {'above' if side == 'BUY' else 'below'} "
                f"EMA{p.trend_period} {t_now:.2f}"
            )

        volume_ratio = None
        if p.volume_filter:
            window = [c.volume for c in candles[max(0, i - p.volume_lookback) : i]]
            avg = sum(window) / len(window) if window else 0
            if avg <= 0:
                # No volume baseline means the filter cannot be judged. Failing
                # closed is right: the user asked for volume confirmation.
                return None
            volume_ratio = bar.volume / avg
            if volume_ratio < p.volume_multiplier:
                return None
            reasons.append(f"volume {volume_ratio:.2f}x the {p.volume_lookback}-bar average")

        # Candle confirmation. INDECISION never confirms — a doji says neither
        # side kept control, which is not agreement with a directional entry.
        pattern_name = None
        confirming_hit = None
        if p.pattern_filter:
            wanted = candle_patterns.BULLISH if side == "BUY" else candle_patterns.BEARISH
            window = max(1, p.pattern_lookback)
            hit = None
            # Search backwards so the most recent confirming candle wins.
            for offset in range(window):
                idx = i - offset
                found = candle_patterns.detect_at(candles, idx)
                if found is not None and found.bias == wanted:
                    hit = found
                    break
            if hit is None:
                return None
            pattern_name = hit.name
            confirming_hit = hit
            bars_ago = i - hit.index
            when = "on the signal bar" if bars_ago == 0 else f"{bars_ago} bar(s) earlier"
            reasons.append(f"confirmed by {hit.label} {when} — {hit.note}")

        # The signal bar's own shape is recorded ALWAYS, not just when the
        # filter is on. It is context a reader needs in order to judge the
        # signal, and hiding it behind a setting would mean the log explains
        # less exactly when the user has done less configuring.
        # Pattern helpers read only backward from the index, so passing the
        # full array is safe and avoids copying the history each bar.
        own_candle = candle_patterns.detect_at(candles, i)
        own_desc = candle_patterns.describe(candles, i)

        plain = explain_signal(
            side=side,
            fast_type=p.fast_type,
            fast_period=p.fast_period,
            slow_type=p.slow_type,
            slow_period=p.slow_period,
            fast_value=f_now,
            slow_value=s_now,
            price=bar.close,
            timeframe=timeframe,
            volume_ratio=volume_ratio,
            candle=own_candle,
            candle_desc=own_desc,
            confirmed_by=confirming_hit,
            rsi_value=rsi_value,
        )

        return Signal(
            symbol=symbol,
            timeframe=timeframe,
            side=side,
            price=round(bar.close, 2),
            fast_label=p.fast_label,
            slow_label=p.slow_label,
            fast_value=round(f_now, 2),
            slow_value=round(s_now, 2),
            candle_ts=bar.ts,
            volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else None,
            adx_value=round(adx_value, 1) if adx_value is not None else None,
            pattern=pattern_name,
            candle_pattern=own_candle.name if own_candle else None,
            candle_desc=own_desc,
            candle_ohlc=(bar.open, bar.high, bar.low, bar.close),
            rsi_value=round(rsi_value, 1) if rsi_value is not None else None,
            plain_english=plain,
            reasons=reasons,
        )
