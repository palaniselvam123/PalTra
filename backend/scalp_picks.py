"""Scalp-pick backtest: what the Scalp page would have picked on past days.

For each replayed day, at a chosen time (say 09:45), every stock in the
universe is scored with the same `score_row` the live Scalp page uses, from
Groww's 1-minute candles up to that minute. The top N scalp-ready stocks are
that day's picks, and only they are traded by the SMA bot for the rest of
the day.

Honest limits, so the result is not read as more than it is:
* Groww keeps no past bid/ask, so the spread check is skipped (not passed).
* The pick minute's own candle is not used beyond its open price: the score
  sees only what was known at that minute.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from app.services.indicators import OHLCV
from app.services.scalp_monitor import DEFAULT_MIN_ATR_PCT, DEFAULT_MIN_VALUE_CR, score_row
from groww_client import IST
from indicators import derive_minute_volume

MAX_UNIVERSE = 60
MAX_TOP_N = 10


@dataclass
class PickRule:
    pick_time: dt.time = dt.time(9, 45)
    top_n: int = 3
    min_atr_pct: float = DEFAULT_MIN_ATR_PCT
    min_value_cr: float = DEFAULT_MIN_VALUE_CR
    require_bias: bool = True

    def as_dict(self) -> dict:
        return {
            "pick_time": self.pick_time.strftime("%H:%M"),
            "top_n": self.top_n,
            "min_atr_pct": self.min_atr_pct,
            "min_value_cr": self.min_value_cr,
            "require_bias": self.require_bias,
        }


def bars_known_at(frame: pd.DataFrame, day: dt.date, at: dt.time) -> list[OHLCV]:
    """The day's candles before `at`, after the previous day's, as the live
    page would have held them; then the `at` minute reduced to its open.

    Volume is per minute (Groww's frame carries the running session total).
    """
    if frame is None or frame.empty:
        return []
    cut = int(dt.datetime.combine(day, at, tzinfo=IST).timestamp())
    since = int(dt.datetime.combine(day - dt.timedelta(days=5), dt.time(0, 0), tzinfo=IST).timestamp())
    part = frame[(frame["ts"] >= since) & (frame["ts"] <= cut)].copy()
    if part.empty:
        return []
    days = pd.to_datetime(part["ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    if not (days == day).any():
        return []
    # The day before the pick day, and the pick day itself.
    prior = sorted(d for d in days.unique() if d < day)
    keep = days == day
    if prior:
        keep |= days == prior[-1]
    part = part[keep.to_numpy()]
    minute = derive_minute_volume(part).fillna(0)
    bars = [
        OHLCV(
            ts=int(r.ts),
            open=float(r.open),
            high=float(r.high),
            low=float(r.low),
            close=float(r.close),
            volume=int(v),
        )
        for r, v in zip(part.itertuples(index=False), minute.to_numpy())
    ]
    last = bars[-1]
    if last.ts == cut:
        # Only the price at the pick minute is known, not how that minute ends.
        bars[-1] = OHLCV(ts=last.ts, open=last.open, high=last.open, low=last.open, close=last.open, volume=0)
    else:
        # No candle at the pick minute: the last closed price stands in for it.
        bars.append(OHLCV(ts=cut, open=last.close, high=last.close, low=last.close, close=last.close, volume=0))
    return bars


def picks_for_day(frames: dict[str, pd.DataFrame], day: dt.date, rule: PickRule) -> list[dict]:
    """The day's picks, best first: scalp-ready (spread not checked), with a
    LONG/SHORT bias when the rule asks for one, top N by score."""
    scored = []
    for symbol, frame in frames.items():
        bars = bars_known_at(frame, day, rule.pick_time)
        if len(bars) < 2:
            continue
        row = score_row(
            symbol,
            bars,
            {"ltp": bars[-1].close},
            min_atr_pct=rule.min_atr_pct,
            min_value_cr=rule.min_value_cr,
            require_spread=False,
        )
        if not row.ready:
            continue
        if rule.require_bias and row.bias == "NONE":
            continue
        scored.append(row)
    scored.sort(key=lambda r: r.score, reverse=True)
    return [
        {
            "symbol": r.symbol,
            "score": r.score,
            "bias": r.bias,
            "atr_pct": round(r.atr_pct, 4) if r.atr_pct is not None else None,
            "value_cr": round(r.value_cr, 2) if r.value_cr is not None else None,
            "move_5m_pct": round(r.move_5m_pct, 3) if r.move_5m_pct is not None else None,
        }
        for r in scored[: rule.top_n]
    ]
