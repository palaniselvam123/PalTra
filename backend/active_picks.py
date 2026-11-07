"""Most active pick backtest (practice money; the Scalp page's Most active card, "Backtest" view).

The Trend quality backtest (`trend_picks.py`), done the Most active way: for each past day of a replay range, at
`pick_time` every stock of the universe is read from its 1-minute Groww candles closed before that minute, the way the
Most active card would have read it then (`read_at`): the move from the previous close (`change_pct`), money traded so far
(`value_cr`, ₹ crore), volume against its usual for the same minutes of the earlier sessions in the download (`rvol`,
needs `MIN_SESSIONS`), the distance from today's average price (VWAP, `vwap_dist_pct`), today's range, the speed over the
last `SPEED_MIN` minutes (% per minute) and the bias (LONG when up on the day and above the average price, SHORT when down
and below). Stocks under `min_value_cr` are dropped, `bias` keeps LONG / SHORT / both (`ANY` also keeps NONE), and the
rest are ranked by `rank` (the card's Rank by: `value`, `rvol`, `change`, `vwap`, `speed`, `range`); the top `top_n` are
the day's picks. `replay.ReplaySession` (the Scalp-pick mode) trades only them with the chosen bot's settings from the
pick minute to the square-off.

The live card also ranks by buyers vs sellers waiting (Groww's order book); past order books are not kept, so that one
is left out here. Nothing here places an order or changes a bot's settings or Trade list.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger("sma.active_picks")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KIND = "ACTIVE"
RANKS = ("value", "rvol", "change", "vwap", "speed", "range")
BIASES = ("BOTH", "LONG", "SHORT", "ANY")
MAX_TOP_N = 20
MAX_UNIVERSE = 250
SPEED_MIN = 10
MIN_SESSIONS = 1
SESSION_OPEN = dt.time(9, 15)


@dataclass
class ActivePickRule:
    pick_time: dt.time = dt.time(10, 0)
    top_n: int = 10
    rank: str = "rvol"
    bias: str = "BOTH"
    min_value_cr: float = 5.0
    kind: str = KIND

    def as_dict(self) -> dict:
        return {
            "kind": KIND,
            "pick_time": self.pick_time.strftime("%H:%M"),
            "top_n": self.top_n,
            "rank": self.rank,
            "bias": self.bias,
            "min_value_cr": self.min_value_cr,
        }


def _day_of(ts: pd.Series) -> pd.Series:
    return pd.to_datetime(ts.astype("int64"), unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date


def _cum_at(session: pd.DataFrame, minutes_in: float) -> float | None:
    """Groww's running session volume at `minutes_in` minutes after 09:15 (the last candle closed by then)."""
    if session.empty:
        return None
    start = int(dt.datetime.combine(session["_day"].iloc[0], SESSION_OPEN, tzinfo=IST).timestamp())
    part = session[session["ts"].astype("int64") < start + minutes_in * 60]
    if part.empty:
        return None
    v = pd.to_numeric(part["volume"], errors="coerce").dropna()
    return float(v.iloc[-1]) if not v.empty else None


def read_at(frame: pd.DataFrame, day: dt.date, at: dt.time, symbol: str = "") -> dict | None:
    """One stock as the Most active card would have read it at `at` on `day`, or None (no candles that day)."""
    if frame is None or frame.empty:
        return None
    cut = int(dt.datetime.combine(day, at, tzinfo=IST).timestamp())
    known = frame[frame["ts"].astype("int64") < cut].copy()
    if known.empty:
        return None
    known["_day"] = _day_of(known["ts"])
    today = known[known["_day"] == day]
    if len(today) < 2:
        return None
    earlier = known[known["_day"] < day]
    ltp = float(today["close"].iloc[-1])
    prev_close = float(earlier["close"].iloc[-1]) if not earlier.empty else None
    day_open = float(today["open"].iloc[0])
    base = prev_close or day_open
    change = (ltp - base) / base * 100 if base else None

    # Groww's volume is the running session total; per-minute volume is its difference.
    cum = pd.to_numeric(today["volume"], errors="coerce").ffill().fillna(0.0)
    minute_vol = cum.diff().clip(lower=0).fillna(cum.iloc[0])
    typical = (today["high"] + today["low"] + today["close"]) / 3
    shares = float(cum.iloc[-1])
    vwap = float((typical * minute_vol).sum() / minute_vol.sum()) if minute_vol.sum() > 0 else None
    value_cr = float((typical * minute_vol).sum()) / 1e7 if minute_vol.sum() > 0 else 0.0

    minutes_in = (cut - int(dt.datetime.combine(day, SESSION_OPEN, tzinfo=IST).timestamp())) / 60
    usual = [v for d, g in earlier.groupby("_day") if (v := _cum_at(g, minutes_in))]
    rvol = shares / (sum(usual) / len(usual)) if len(usual) >= MIN_SESSIONS and sum(usual) > 0 else None

    back = today[today["ts"].astype("int64") <= cut - SPEED_MIN * 60]
    then = float(back["close"].iloc[-1]) if not back.empty else day_open
    speed = (ltp - then) / then * 100 / SPEED_MIN if then else None

    vwap_dist = (ltp - vwap) / vwap * 100 if vwap else None
    bias = "NONE"
    if change is not None and vwap_dist is not None:
        if change > 0 and vwap_dist > 0:
            bias = "LONG"
        elif change < 0 and vwap_dist < 0:
            bias = "SHORT"
    hi, lo = float(today["high"].max()), float(today["low"].min())
    return {
        "symbol": symbol,
        "ltp": round(ltp, 2),
        "change_pct": round(change, 3) if change is not None else None,
        "value_cr": round(value_cr, 2),
        "rvol": round(rvol, 2) if rvol is not None else None,
        "vwap_dist_pct": round(vwap_dist, 3) if vwap_dist is not None else None,
        "range_pct": round((hi - lo) / day_open * 100, 3) if day_open else None,
        "speed_pct_min": round(speed, 4) if speed is not None else None,
        "bias": bias,
    }


RANK_KEY = {
    "value": lambda r: r["value_cr"] or 0.0,
    "rvol": lambda r: r["rvol"] or 0.0,
    "change": lambda r: abs(r["change_pct"] or 0.0),
    "vwap": lambda r: abs(r["vwap_dist_pct"] or 0.0),
    "speed": lambda r: abs(r["speed_pct_min"] or 0.0),
    "range": lambda r: r["range_pct"] or 0.0,
}


def _wanted(bias: str, want: str) -> bool:
    if want == "ANY":
        return True
    if want == "BOTH":
        return bias in ("LONG", "SHORT")
    return bias == want


def picks_for_day(frames: dict[str, pd.DataFrame], day: dt.date, rule: ActivePickRule) -> list[dict]:
    """The day's Most active picks at the pick time, best first."""
    rows = []
    for symbol, frame in frames.items():
        try:
            row = read_at(frame, day, rule.pick_time, symbol=symbol)
        except Exception:  # noqa: BLE001 - one bad tape never stops the day's pick
            log.exception("most active pick read failed for %s", symbol)
            continue
        if row is None or row["value_cr"] < rule.min_value_cr or not _wanted(row["bias"], rule.bias):
            continue
        rows.append(row)
    key = RANK_KEY.get(rule.rank, RANK_KEY["rvol"])
    rows.sort(key=lambda r: (-key(r), -(r["value_cr"] or 0), r["symbol"]))
    return [{**r, "side": r["bias"] if r["bias"] != "NONE" else None} for r in rows[: rule.top_n]]
