"""Readings for stock selection, from candle data only (no new indicators).

Every reading is taken from data known BEFORE the moment it would be used:

* stock-day, ``pre`` (before the open): the previous sessions only;
* stock-day, ``o30`` (at 09:45): the day's 09:15-09:44 candles plus history;
* trade (at entry): the closed cross candle and the ones before it.

SMA 9/21, ATR(14) and per-minute volume come from ``indicators.enrich``, the
same function the bot uses, over the continuous candle series (as the bot
sees it). The only additions are plain arithmetic on those columns:

* efficiency = |net move| / sum of |minute moves|  (1 = straight line,
  near 0 = back and forth) - the "↑↑↑↑ vs ↑↓↑↓" question;
* crosses = how many times SMA 9 crossed SMA 21 in the window (whipsaw count);
* separation = the median, over the stretches between crosses, of the widest
  |SMA9 - SMA21| / SMA21 % reached - how far the averages typically pull apart;
* gap change at entry = how much the SMA gap moved on the cross candle and
  the 3 before it, in the trade's direction (expanding > 0, shrinking < 0).
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from indicators import enrich

RVOL_SESSIONS = 20  # the app's own convention (volume filter, Most active, Scalp)
OPEN30_END = dt.time(9, 45)
_IST = 19_800


def prepare(frame: pd.DataFrame, sma_fast: int = 9, sma_slow: int = 21, atr_period: int = 14) -> pd.DataFrame:
    """Enriched candles plus day, clock, gap % and cross flags."""
    out = enrich(frame.reset_index(drop=True), sma_fast, sma_slow, atr_period)
    local = out["ts"].astype("int64") + _IST
    out["day"] = pd.to_datetime(local // 86_400, unit="D").dt.date
    out["minute"] = (local % 86_400) // 60
    out["gap_pct"] = (out["sma_9"] - out["sma_21"]) / out["sma_21"] * 100
    sign = np.sign(out["sma_9"] - out["sma_21"])
    out["cross"] = (sign != sign.shift(1)) & sign.notna() & sign.shift(1).notna() & (sign != 0)
    out["mvol"] = out["minute_volume"].fillna(0).clip(lower=0)
    out["turnover"] = out["close"] * out["mvol"]
    out["atr_pct"] = out["atr_14"] / out["close"] * 100
    return out


def _efficiency(part: pd.DataFrame) -> float | None:
    if len(part) < 5:
        return None
    path = part["close"].diff().abs().sum() + abs(part["close"].iloc[0] - part["open"].iloc[0])
    if path <= 0:
        return None
    return float(abs(part["close"].iloc[-1] - part["open"].iloc[0]) / path)


def _separation(part: pd.DataFrame) -> float | None:
    """Median widest |gap %| between consecutive crosses inside the window."""
    gap = part["gap_pct"].abs().to_numpy()
    if len(gap) < 5 or np.isnan(gap).all():
        return None
    seg = part["cross"].cumsum().to_numpy()
    peaks = pd.Series(gap).groupby(seg).max().dropna()
    return float(peaks.median()) if len(peaks) else None


def _window_stats(part: pd.DataFrame) -> dict:
    return {
        "turnover_cr": float(part["turnover"].sum()) / 1e7,
        "atr_pct": float(part["atr_pct"].mean()) if part["atr_pct"].notna().any() else None,
        "efficiency": _efficiency(part),
        "crosses": int(part["cross"].sum()),
        "separation": _separation(part),
        "range_pct": float((part["high"].max() - part["low"].min()) / part["open"].iloc[0] * 100) if len(part) else None,
    }


def stock_days(prepared: pd.DataFrame, symbol: str, days: list[dt.date]) -> list[dict]:
    """One row per (stock, day) with the before-the-open and 09:45 readings."""
    by_day = {d: g for d, g in prepared.groupby("day", sort=True)}
    sessions = sorted(by_day)
    daily_vol = {d: float(by_day[d]["mvol"].sum()) for d in sessions}
    daily_turn = {d: float(by_day[d]["turnover"].sum()) / 1e7 for d in sessions}
    open30_min = OPEN30_END.hour * 60 + OPEN30_END.minute
    open30_vol = {d: float(by_day[d].loc[by_day[d]["minute"] < open30_min, "mvol"].sum()) for d in sessions}
    rows = []
    for day in days:
        if day not in by_day:
            continue
        before = [d for d in sessions if d < day]
        if not before:
            continue
        prev = before[-1]
        hist = before[-RVOL_SESSIONS:]
        prev_hist = [d for d in sessions if d < prev][-RVOL_SESSIONS:]
        row: dict = {"symbol": symbol, "date": day.isoformat()}
        pre = _window_stats(by_day[prev])
        row.update({f"pre_{k}": v for k, v in pre.items()})
        row["pre_turnover_cr_med"] = float(np.median([daily_turn[d] for d in hist]))
        base = np.mean([daily_vol[d] for d in prev_hist]) if len(prev_hist) >= 5 else None
        row["pre_rvol"] = daily_vol[prev] / base if base else None
        today = by_day[day]
        early = today[today["minute"] < open30_min]
        if len(early) >= 20:
            o30 = _window_stats(early)
            row.update({f"o30_{k}": v for k, v in o30.items()})
            row["o30_atr_pct"] = float(early["atr_pct"].iloc[-1]) if pd.notna(early["atr_pct"].iloc[-1]) else None
            base30 = np.mean([open30_vol[d] for d in hist]) if len(hist) >= 5 else None
            row["o30_rvol"] = open30_vol[day] / base30 if base30 else None
            row["o30_abs_gap"] = float(abs(early["gap_pct"].iloc[-1])) if pd.notna(early["gap_pct"].iloc[-1]) else None
        rows.append(row)
    return rows


def trade_features(prepared: pd.DataFrame, trade: dict) -> dict:
    """Readings on the closed cross candle the trade was entered on."""
    entry = trade["entry_time"]
    if entry.tzinfo is None:
        entry = entry.replace(tzinfo=dt.timezone(dt.timedelta(seconds=_IST)))
    minute_ts = int(entry.timestamp()) // 60 * 60
    ts = prepared["ts"].to_numpy()
    i = int(np.searchsorted(ts, minute_ts, side="left")) - 1  # last closed candle before the entry minute
    if i < 4:
        return {}
    sign = 1.0 if trade["direction"] == "LONG" else -1.0
    gap = prepared["gap_pct"].to_numpy()
    mvol = prepared["mvol"].to_numpy()
    prior = mvol[max(0, i - 20) : i]
    out = {
        "cross_bar_ts": int(ts[i]),
        "is_cross_bar": bool(prepared["cross"].iloc[i]),
        "gap_entry": float(sign * gap[i]) if not np.isnan(gap[i]) else None,
        "gap_d1": float(sign * (gap[i] - gap[i - 1])) if not np.isnan(gap[i - 1]) else None,
        "gap_d3": float(sign * (gap[i] - gap[i - 3])) if not np.isnan(gap[i - 3]) else None,
        "atr_pct_entry": float(prepared["atr_pct"].iloc[i]) if pd.notna(prepared["atr_pct"].iloc[i]) else None,
        "vol_ratio_entry": float(mvol[i] / prior.mean()) if len(prior) and prior.mean() > 0 else None,
    }
    exit_t = trade.get("exit_time")
    out["minutes_held"] = (exit_t - trade["entry_time"]).total_seconds() / 60 if exit_t else None
    return out
