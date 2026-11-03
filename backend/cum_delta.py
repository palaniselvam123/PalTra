"""Cumulative delta (CVD) check and divergence exit (cum_delta.py), on the bot's own closed candles.

Groww gives 1-minute candles with a running volume total, never which shares traded at the ask
(buyers) and which at the bid (sellers), so true order-flow delta cannot be built. Each candle's
delta is estimated from its body's share of its range (owner's choice):

    delta      = candle volume × (close − open) ÷ (high − low)      (0 for a candle with no range)
    cumulative = running sum of delta from the first candle of each IST session (reset daily)

A strong green candle counts as mostly buying, a doji as about none. A candle without a known
volume (Groww's first minute of a session) adds 0.

Both parts are off by default, per bot and per stock, PAPER and LIVE, replays too.

* **Entry** (`use_cvd`): a buy only while today's cumulative delta has risen over the last
  `cvd_lookback` (5) closed candles (since the open when fewer have closed today), a sell only
  while it has fallen. Wired through `_entry_block` like the other filters (check name `cvd`).
* **Divergence exit** (`cvd_div_exit`): on a candle closed after the entry, a long closes when
  that candle's high is above every high of the `cvd_div_lookback` (10) candles before it today
  while its cumulative delta is below the delta at the earlier highest high (price made a new high,
  buyers did not); a short mirrors it with lows. Exit reason `CVD_DIVERGENCE`.

Nothing here places an order.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators import derive_minute_volume

DEFAULT_LOOKBACK = 5
DEFAULT_DIV_LOOKBACK = 10
MAX_LOOKBACK = 50
MIN_DIV_LOOKBACK = 3
MAX_DIV_LOOKBACK = 100
EXIT_REASON = "CVD_DIVERGENCE"


def _int(cfg, name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(getattr(cfg, name, None) or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def lookback(cfg) -> int:
    return _int(cfg, "cvd_lookback", DEFAULT_LOOKBACK, 1, MAX_LOOKBACK)


def div_lookback(cfg) -> int:
    return _int(cfg, "cvd_div_lookback", DEFAULT_DIV_LOOKBACK, MIN_DIV_LOOKBACK, MAX_DIV_LOOKBACK)


def entry_on(cfg) -> bool:
    return bool(getattr(cfg, "use_cvd", False))


def exit_on(cfg) -> bool:
    return bool(getattr(cfg, "cvd_div_exit", False))


def _sessions(frame: pd.DataFrame) -> pd.Series:
    ts = pd.to_numeric(frame["ts"], errors="coerce")
    return pd.to_datetime(ts, unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date


def deltas(frame: pd.DataFrame) -> pd.Series:
    """Each candle's estimated delta (shares, signed). Missing volume or a flat candle gives 0."""
    if "minute_volume" in frame.columns:
        vol = pd.to_numeric(frame["minute_volume"], errors="coerce")
    else:
        vol = derive_minute_volume(frame)
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    body = pd.to_numeric(frame["close"], errors="coerce") - pd.to_numeric(frame["open"], errors="coerce")
    span = high - low
    share = (body / span.where(span > 0)).clip(-1.0, 1.0)
    out = (vol.clip(lower=0) * share).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    return out.astype("float64")


def series(frame: pd.DataFrame) -> pd.Series:
    """Cumulative delta, reset at each IST session's first candle."""
    if frame is None or len(frame) == 0:
        return pd.Series(dtype="float64")
    d = deltas(frame)
    return d.groupby(_sessions(frame).values).cumsum().astype("float64")


def _today(closed: pd.DataFrame) -> pd.DataFrame:
    days = _sessions(closed)
    return closed[days.values == days.iloc[-1]]


def _fmt(value: float) -> str:
    return f"{value:+,.0f}"


def entry_reason(frame: pd.DataFrame, direction: str, cfg) -> str | None:
    """Why the CVD check refuses this entry, or None when it agrees. The forming candle is never read."""
    closed = frame.iloc[:-1] if frame is not None else None
    if closed is None or len(closed) == 0:
        return "Cumulative delta is not ready (no closed candle yet)"
    today = _today(closed)
    cvd = series(today)
    n = lookback(cfg)
    now = float(cvd.iloc[-1])
    # Since the open (0) when fewer than n candles have closed today.
    before = float(cvd.iloc[-1 - n]) if len(cvd) > n else 0.0
    change = now - before
    span = f"{n} candles" if len(cvd) > n else "the open"
    if direction == "LONG" and change <= 0:
        return f"Cumulative delta {_fmt(now)} has not risen since {span} ({_fmt(change)})"
    if direction == "SHORT" and change >= 0:
        return f"Cumulative delta {_fmt(now)} has not fallen since {span} ({_fmt(change)})"
    return None


def passed_text(frame: pd.DataFrame, direction: str, cfg) -> str:
    today = _today(frame.iloc[:-1])
    cvd = series(today)
    n = lookback(cfg)
    now = float(cvd.iloc[-1])
    before = float(cvd.iloc[-1 - n]) if len(cvd) > n else 0.0
    word = "rose" if direction == "LONG" else "fell"
    span = f"last {n} candles" if len(cvd) > n else "since the open"
    return f"Cumulative delta {word} {_fmt(now - before)} ({span}, now {_fmt(now)})"


def divergence(closed: pd.DataFrame, side: str, cfg) -> tuple[bool, str]:
    """Did the last closed candle make a new high (long) / low (short) without the delta following?"""
    if closed is None or len(closed) < 2:
        return False, ""
    today = _today(closed)
    n = div_lookback(cfg)
    if len(today) < 2:
        return False, ""
    cvd = series(today)
    window = today.iloc[-1 - n:-1]
    win_cvd = cvd.iloc[-1 - n:-1]
    bar = today.iloc[-1]
    now = float(cvd.iloc[-1])
    if side == "LONG":
        highs = pd.to_numeric(window["high"], errors="coerce")
        at = int(np.nanargmax(highs.values))
        prior, prior_cvd = float(highs.iloc[at]), float(win_cvd.iloc[at])
        if float(bar["high"]) > prior and now < prior_cvd:
            return True, (
                f"new high {float(bar['high']):.2f} above {prior:.2f} but cumulative delta "
                f"{_fmt(now)} is below {_fmt(prior_cvd)} at that high"
            )
    elif side == "SHORT":
        lows = pd.to_numeric(window["low"], errors="coerce")
        at = int(np.nanargmin(lows.values))
        prior, prior_cvd = float(lows.iloc[at]), float(win_cvd.iloc[at])
        if float(bar["low"]) < prior and now > prior_cvd:
            return True, (
                f"new low {float(bar['low']):.2f} below {prior:.2f} but cumulative delta "
                f"{_fmt(now)} is above {_fmt(prior_cvd)} at that low"
            )
    return False, ""
