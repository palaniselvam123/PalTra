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
    LONG/SHORT bias when the rule asks for one, top N by score. A strong-trend rule
    (trend_picks.TrendPickRule) picks by the Trend quality read instead."""
    if getattr(rule, "kind", None) == "TREND":
        import trend_picks

        return trend_picks.picks_for_day(frames, day, rule)
    if getattr(rule, "kind", None) == "AUTOARM":
        import auto_arm

        return auto_arm.picks_for_day(frames, day, rule)
    if getattr(rule, "kind", None) == "ACTIVE":
        import active_picks

        return active_picks.picks_for_day(frames, day, rule)
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


def _hhmm(value, default: dt.time) -> dt.time:
    try:
        hh, mm = str(value).split(":")[:2]
        return dt.time(int(hh), int(mm))
    except (TypeError, ValueError):
        return default


def rule_from_dict(data: dict, sma_fast: int = 9, sma_slow: int = 21):
    """The pick rule a run saved (`scalp_pick` in its settings), rebuilt so a cut-short run can be resumed.

    `kind` picks the class: TREND (trend_picks), ACTIVE (active_picks), AUTOARM (auto_arm, with the Most active hits
    the run saved), none = the Scalp page's own scalp picks. The SMA periods come from the run's saved settings."""
    kind = str(data.get("kind") or "").upper()
    if kind == "TREND":
        import trend_picks

        return trend_picks.TrendPickRule(
            pick_time=_hhmm(data.get("pick_time"), dt.time(10, 0)), top_n=int(data.get("top_n") or 10),
            direction=str(data.get("direction") or "BOTH"), minutes=int(data.get("minutes") or 5),
            sma_fast=int(data.get("sma_fast") or sma_fast), sma_slow=int(data.get("sma_slow") or sma_slow),
        )
    if kind == "ACTIVE":
        import active_picks

        return active_picks.ActivePickRule(
            pick_time=_hhmm(data.get("pick_time"), dt.time(10, 0)), top_n=int(data.get("top_n") or 10),
            rank=str(data.get("rank") or "rvol"), bias=str(data.get("bias") or "BOTH"),
            min_value_cr=float(data.get("min_value_cr") or 0.0),
        )
    if kind == "AUTOARM":
        import auto_arm

        hits = data.get("active_hits") if isinstance(data.get("active_hits"), dict) else {}
        return auto_arm.AutoArmRule(
            source=str(data.get("source") or "BOTH"), first=_hhmm(data.get("first"), dt.time(9, 30)),
            last=_hhmm(data.get("last"), dt.time(14, 30)), every_min=int(data.get("every_min") or 5),
            minutes=int(data.get("minutes") or 5), sma_fast=sma_fast, sma_slow=sma_slow,
            min_checks=int(data.get("min_checks") or 6), side=str(data.get("side") or "BOTH"),
            pct=float(data.get("pct") or 70.0), pressure_side=str(data.get("pressure_side") or "BOTH"),
            min_value_cr=float(data.get("min_value_cr") or 0.0), max_day=int(data.get("max_day") or 10),
            price_min=float(data.get("price_min") or 0.0), price_max=float(data.get("price_max") or 0.0),
            active_hits={str(k): [tuple(h) for h in v] for k, v in hits.items() if isinstance(v, list)},
        )
    return PickRule(
        pick_time=_hhmm(data.get("pick_time"), dt.time(9, 45)), top_n=int(data.get("top_n") or 3),
        min_atr_pct=float(data.get("min_atr_pct") if data.get("min_atr_pct") is not None else DEFAULT_MIN_ATR_PCT),
        min_value_cr=float(data.get("min_value_cr") if data.get("min_value_cr") is not None else DEFAULT_MIN_VALUE_CR),
        require_bias=bool(data.get("require_bias", True)),
    )


def resumable(data: dict | None) -> bool:
    """A pick run can be resumed when it saved the stocks it picks from (runs started after this was added)."""
    return isinstance(data, dict) and bool(data.get("universe_symbols"))
