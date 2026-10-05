"""Scalp monitor: which streaming stocks are good to scalp right now.

A scalp needs three things at once, and this reads each from data the desk
already has (1-minute candles from `candle_store`, bid/ask from the feed):

* **Room to move** — 1-minute ATR as a % of price. A stock that moves 0.03% a
  minute cannot pay for charges on a quick in-and-out.
* **Cheap to get in and out** — the bid/ask spread as a % of price, and enough
  rupees traded today that an order does not move the price.
* **Something happening now** — the last closed minute's volume against the
  20 before it, and how far price went in the last 1 and 5 minutes.

The score is a plain weighted sum of those readings, each capped, so a row can
always be explained from its own columns. It is a ranking aid, not a signal:
nothing here places or changes an order.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import asdict, dataclass

from app.core.market_clock import IST
from app.services.indicators import OHLCV

ATR_PERIOD = 14
VOLUME_LOOKBACK = 20
MIN_BARS = ATR_PERIOD + 2  # below this the readings are not meaningful

# Defaults for "scalp-ready". Chosen to be legible, not fitted.
DEFAULT_MIN_ATR_PCT = 0.08     # ~0.08% a minute
DEFAULT_MAX_SPREAD_PCT = 0.05  # 5 paise on ₹100
DEFAULT_MIN_VALUE_CR = 5.0     # ₹5 crore traded today

# Score weights (sum to 100). Each part is capped at its weight.
W_MOVE, W_VOLUME, W_MOMENTUM, W_LIQUIDITY = 35, 25, 20, 20
ATR_FULL_PCT = 0.30            # ATR% that earns the full movement score
VOLUME_FULL_RATIO = 3.0        # volume spike that earns the full volume score
MOMENTUM_FULL_PCT = 1.0        # 5-minute move that earns the full momentum score
VALUE_FULL_CR = 50.0           # ₹ crore traded that earns the full liquidity score


@dataclass
class ScalpRow:
    symbol: str
    ltp: float | None
    bars: int
    change_pct: float | None        # vs today's first 1-minute open
    atr_pct: float | None           # Wilder ATR(14) on closed 1m bars, % of price
    spread_pct: float | None        # (ask - bid) / ltp, %
    volume_ratio: float | None      # last closed bar / mean of the 20 before it
    value_cr: float | None          # Σ close × volume today, ₹ crore
    move_1m_pct: float | None
    move_5m_pct: float | None
    vwap: float | None
    vwap_dist_pct: float | None
    range_pct: float | None         # today's high-low as % of today's open
    bias: str                       # LONG / SHORT / NONE
    score: float
    ready: bool
    reasons: list[str]              # why it is not ready (empty when ready)
    as_of: int | None               # last closed bar open time (epoch s)

    def as_dict(self) -> dict:
        return asdict(self)


def _wilder_atr(bars: list[OHLCV], period: int = ATR_PERIOD) -> float | None:
    if len(bars) < period + 1:
        return None
    trs = []
    for prev, bar in zip(bars, bars[1:]):
        trs.append(max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close)))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def _ist_day(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=IST).date()


def _pct(a: float, b: float) -> float | None:
    return (a - b) / b * 100 if b else None


def _cap(value: float | None, full: float, weight: float) -> float:
    if value is None or full <= 0:
        return 0.0
    return weight * max(0.0, min(abs(value) / full, 1.0))


def score_row(
    symbol: str,
    bars: list[OHLCV],
    quote: dict | None,
    *,
    min_atr_pct: float = DEFAULT_MIN_ATR_PCT,
    max_spread_pct: float = DEFAULT_MAX_SPREAD_PCT,
    min_value_cr: float = DEFAULT_MIN_VALUE_CR,
) -> ScalpRow:
    """One stock's scalp readings. `bars` are 1-minute candles, oldest first;
    the last one is the forming minute and is used only for the live price."""
    quote = quote or {}
    closed = bars[:-1] if len(bars) > 1 else []
    ltp = float(quote.get("ltp") or 0) or (bars[-1].close if bars else None)
    reasons: list[str] = []

    atr_pct = None
    if closed and ltp:
        atr = _wilder_atr(closed[-(ATR_PERIOD * 4):])
        atr_pct = atr / ltp * 100 if atr is not None else None

    spread_pct = None
    bid, ask = float(quote.get("bid") or 0), float(quote.get("ask") or 0)
    if ltp and bid > 0 and ask >= bid:
        spread_pct = (ask - bid) / ltp * 100

    volume_ratio = None
    if len(closed) > VOLUME_LOOKBACK:
        prior = [b.volume for b in closed[-(VOLUME_LOOKBACK + 1):-1]]
        avg = sum(prior) / len(prior)
        if avg > 0:
            volume_ratio = closed[-1].volume / avg

    today = [b for b in bars if _ist_day(b.ts) == _ist_day(bars[-1].ts)] if bars else []
    today_closed = today[:-1]
    value_cr = vwap = vwap_dist = change = range_pct = None
    if today_closed:
        traded = sum(b.close * b.volume for b in today_closed)
        volume = sum(b.volume for b in today_closed)
        value_cr = traded / 1e7
        if volume > 0:
            vwap = sum((b.high + b.low + b.close) / 3 * b.volume for b in today_closed) / volume
            vwap_dist = _pct(ltp, vwap) if ltp else None
    if today and ltp:
        change = _pct(ltp, today[0].open)
        hi = max(b.high for b in today)
        lo = min(b.low for b in today)
        range_pct = (hi - lo) / today[0].open * 100 if today[0].open else None

    move_1m = _pct(ltp, closed[-1].close) if closed and ltp else None
    move_5m = _pct(ltp, closed[-5].close) if len(closed) >= 5 and ltp else None

    if len(bars) < MIN_BARS:
        reasons.append(f"warming up ({len(bars)}/{MIN_BARS} one-minute candles)")
    if atr_pct is not None and atr_pct < min_atr_pct:
        reasons.append(f"ATR {atr_pct:.3f}% < {min_atr_pct:g}%")
    if spread_pct is None:
        reasons.append("no bid/ask yet")
    elif spread_pct > max_spread_pct:
        reasons.append(f"spread {spread_pct:.3f}% > {max_spread_pct:g}%")
    if value_cr is not None and value_cr < min_value_cr:
        reasons.append(f"₹{value_cr:.1f} cr traded < ₹{min_value_cr:g} cr")
    elif value_cr is None:
        reasons.append("no volume today yet")

    score = (
        _cap(atr_pct, ATR_FULL_PCT, W_MOVE)
        + _cap(volume_ratio, VOLUME_FULL_RATIO, W_VOLUME)
        + _cap(move_5m, MOMENTUM_FULL_PCT, W_MOMENTUM)
        + _cap(value_cr, VALUE_FULL_CR, W_LIQUIDITY)
    )
    # A wide spread eats a scalp's edge, so it costs score in proportion.
    if spread_pct is not None and max_spread_pct > 0 and spread_pct > max_spread_pct:
        score *= max(0.0, 1 - (spread_pct - max_spread_pct) / (max_spread_pct * 4))

    bias = "NONE"
    if vwap_dist is not None and move_5m is not None:
        if vwap_dist > 0 and move_5m > 0:
            bias = "LONG"
        elif vwap_dist < 0 and move_5m < 0:
            bias = "SHORT"

    return ScalpRow(
        symbol=symbol,
        ltp=round(ltp, 2) if ltp else None,
        bars=len(bars),
        change_pct=change,
        atr_pct=atr_pct,
        spread_pct=spread_pct,
        volume_ratio=volume_ratio,
        value_cr=value_cr,
        move_1m_pct=move_1m,
        move_5m_pct=move_5m,
        vwap=round(vwap, 2) if vwap else None,
        vwap_dist_pct=vwap_dist,
        range_pct=range_pct,
        bias=bias,
        score=round(score, 1),
        ready=not reasons,
        reasons=reasons,
        as_of=closed[-1].ts if closed else None,
    )


def alert_text(row: ScalpRow, mode_note: str = "") -> str:
    """Telegram text for a stock that just became scalp-ready. No orders."""
    parts = [
        "PalTra scalp watch",
        f"{row.symbol} {row.ltp:,.2f} · score {row.score:.0f}" if row.ltp else f"{row.symbol} · score {row.score:.0f}",
        f"ATR {row.atr_pct:.2f}%/min · spread {row.spread_pct:.3f}%"
        if row.atr_pct is not None and row.spread_pct is not None
        else "",
        f"Volume {row.volume_ratio:.1f}× · 5m {row.move_5m_pct:+.2f}%"
        if row.volume_ratio is not None and row.move_5m_pct is not None
        else "",
        f"Bias {row.bias} · {row.vwap_dist_pct:+.2f}% from VWAP" if row.vwap_dist_pct is not None else "",
        "Watch only — no order placed" + (f" · {mode_note}" if mode_note else ""),
    ]
    return "\n".join(p for p in parts if p)
