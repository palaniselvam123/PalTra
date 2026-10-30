"""SMA 9 deceleration guard (decel_guard.py): exit when the fast SMA bends, before it turns.

Off by default (`decel_exit`), per bot and per stock, PAPER and LIVE (and replays). Read once per
closed candle after the entry by StrategyEngine._watch_slope_exit, on the bot's own candle or on the
slope candle (`slope_minutes`, e.g. 1-minute candles under a 15-minute bot), like the other SMA 9 exits.

The slope is measured in ATR, so one set of numbers fits a ₹100 stock and a ₹5,000 one, a wild
morning and a quiet afternoon:

    slope[t]  = (SMA9[t] - SMA9[t - k]) / (k x ATR14[t])        ATR per candle, k = `decel_lookback`
    dslope[t] = slope[t] - slope[t - 1]                          the bend (second difference)

both signed with the trade (+ = with it: rising for a long, falling for a short). `peak` is the
steepest signed slope so far, counted from the first candle closed after the entry (`decel_peak_from`
= ENTRY, default) or from the start of the current run (RUN: the first candle of the stretch, back from
the entry and inside the same IST session, on which the slope has pointed the trade's way), so a
trade entered late in a run can be warned on its first candle. The trade closes as `SMA9_DECEL` on the
first candle closed after the entry where

  1. armed:    peak >= `decel_arm_atr` (the run was a real one, not a drift), and
  2. faded:    slope <= peak x (1 - `decel_drop_pct` / 100) (the line has lost that share of its
               steepness: the visual flattening), and
  3. braking:  dslope <= -`decel_min_atr` on each of the last `decel_candles` candles (it is still
               bending, not a single wobble), and
  4. confirm:  with `decel_confirm` = CLOSE, the candle closed through the SMA 9 against the trade by at
               least `decel_pierce_atr` x ATR (a long's close under the line, a short's above); OFF skips it.

The slope does not have to turn negative, so the exit usually comes one or two candles before the
zero-slope flip (SMA9_TURN / SMA9_VTURN) and the 9 / 21 cross. Closed candles only: every input is a
backward rolling window or a shift, and the forming candle is never read (no look-ahead).
`early_warning` is the same rule over a whole frame (vectorised) for research and tests; the engine
calls it on the closed frame and reads the last row.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

EXIT_REASON = "SMA9_DECEL"
CONFIRMS = ("OFF", "CLOSE")
PEAK_FROM = ("ENTRY", "RUN")

DEFAULT_LOOKBACK = 2
DEFAULT_ARM_ATR = 0.10
DEFAULT_DROP_PCT = 50.0
DEFAULT_MIN_ATR = 0.02
DEFAULT_CANDLES = 2
DEFAULT_CONFIRM = "CLOSE"
DEFAULT_PIERCE_ATR = 0.0
DEFAULT_PEAK_FROM = "ENTRY"
ATR_PERIOD = 14


def on(cfg) -> bool:
    return bool(getattr(cfg, "decel_exit", False))


def _num(cfg, name: str, default: float, low: float, high: float) -> float:
    raw = getattr(cfg, name, None)
    try:
        value = float(default if raw is None else raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def settings(cfg) -> dict:
    """The guard's numbers, clamped to their allowed ranges."""
    confirm = str(getattr(cfg, "decel_confirm", None) or DEFAULT_CONFIRM).upper()
    peak_from = str(getattr(cfg, "decel_peak_from", None) or DEFAULT_PEAK_FROM).upper()
    return {
        "lookback": int(_num(cfg, "decel_lookback", DEFAULT_LOOKBACK, 1, 10)),
        "arm_atr": _num(cfg, "decel_arm_atr", DEFAULT_ARM_ATR, 0.0, 5.0),
        "drop_pct": _num(cfg, "decel_drop_pct", DEFAULT_DROP_PCT, 0.0, 100.0),
        "min_atr": _num(cfg, "decel_min_atr", DEFAULT_MIN_ATR, 0.0, 5.0),
        "candles": int(_num(cfg, "decel_candles", DEFAULT_CANDLES, 1, 5)),
        "confirm": confirm if confirm in CONFIRMS else DEFAULT_CONFIRM,
        "pierce_atr": _num(cfg, "decel_pierce_atr", DEFAULT_PIERCE_ATR, 0.0, 5.0),
        "peak_from": peak_from if peak_from in PEAK_FROM else DEFAULT_PEAK_FROM,
    }


def wilder_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> pd.Series:
    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    close = pd.to_numeric(df["close"], errors="coerce")
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def slopes(df: pd.DataFrame, fast: int = 9, lookback: int = DEFAULT_LOOKBACK) -> pd.DataFrame:
    """SMA fast, ATR(14), slope in ATR per candle and its change, per closed candle."""
    close = pd.to_numeric(df["close"], errors="coerce")
    sma = close.rolling(int(fast)).mean()
    atr = wilder_atr(df)
    k = max(1, int(lookback))
    slope = (sma - sma.shift(k)) / (k * atr.replace(0.0, np.nan))
    return pd.DataFrame({"sma9": sma, "atr": atr, "slope_atr": slope, "dslope_atr": slope.diff()}, index=df.index)


def run_start(df: pd.DataFrame, signed: pd.Series, entry_pos: int) -> int:
    """First row of the run the trade was entered in: walking back from the entry, the rows whose signed
    slope is still with the trade, inside the entry's IST session. `entry_pos` when there is none."""
    start = int(entry_pos)
    days = None
    if "ts" in df.columns:
        days = (pd.to_numeric(df["ts"], errors="coerce") + 19800) // 86400  # IST calendar day
    vals = signed.to_numpy()
    day = days.iloc[min(start, len(df) - 1)] if days is not None and len(df) else None
    i = start - 1
    while i >= 0 and np.isfinite(vals[i]) and vals[i] > 0 and (day is None or days.iloc[i] == day):
        start = i
        i -= 1
    return start


def early_warning(df: pd.DataFrame, direction: str, entry_pos: int, cfg=None, fast: int = 9) -> pd.DataFrame:
    """`slopes` plus the signed reads and `early_warning_exit` for a trade entered before row `entry_pos`.

    Rows at or after `entry_pos` are the candles closed after the entry. `df` must hold closed candles only.
    """
    s = settings(cfg)
    out = slopes(df, fast, s["lookback"])
    sign = 1.0 if direction == "LONG" else -1.0
    signed = out["slope_atr"] * sign
    bend = out["dslope_atr"] * sign
    rows = np.arange(len(out))
    after = pd.Series(rows >= int(entry_pos), index=out.index)
    first = run_start(df, signed, int(entry_pos)) if s["peak_from"] == "RUN" else int(entry_pos)
    peak = signed.where(pd.Series(rows >= first, index=out.index)).cummax()
    armed = peak >= s["arm_atr"]
    faded = signed <= peak * (1.0 - s["drop_pct"] / 100.0)
    braking = bend.rolling(s["candles"]).max() <= -s["min_atr"]
    if s["confirm"] == "CLOSE":
        close = pd.to_numeric(df["close"], errors="coerce")
        through = (close - out["sma9"]) * sign <= -s["pierce_atr"] * out["atr"]
    else:
        through = pd.Series(True, index=out.index)
    out["signed_slope"] = signed
    out["peak_slope"] = peak
    out["early_warning_exit"] = (after & armed & faded & braking & through).fillna(False).astype(bool)
    return out


def judge(closed: pd.DataFrame, direction: str, entry_pos: int, cfg, fast: int = 9) -> tuple[str, str] | None:
    """(SMA9_DECEL, note) when the last closed candle raises the early warning, else None."""
    if closed is None or len(closed) < int(fast) + ATR_PERIOD or entry_pos >= len(closed):
        return None
    out = early_warning(closed, direction, entry_pos, cfg, fast)
    last = out.iloc[-1]
    if not bool(last["early_warning_exit"]):
        return None
    peak = float(last["peak_slope"])
    now = float(last["signed_slope"])
    return EXIT_REASON, (
        f"SMA 9 decelerating: slope {now:+.3f} ATR/candle from a peak of {peak:+.3f} "
        f"(bend {float(last['dslope_atr']) * (1 if direction == 'LONG' else -1):+.3f})"
    )
