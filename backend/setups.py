"""Technical setups as entries (setups.py).

Off by default (`setup_entry` = ""), per bot and per stock, PAPER and LIVE, replays too. Five classic intraday setups,
each read on the bot's own closed candles (the forming candle is never a signal); tick one or more (`setup_entry`, a CSV
of `KEYS`):

* `ORB` – **opening-range breakout**: the first candle that closes beyond the high (buy) or low (sell) of the day's
  first `setup_orb_minutes` (5-60, default 15) minutes from 09:15. Each side once a day.
* `VWAP_PULL` – **VWAP pullback**: in an up-trend (SMA fast above slow, at least `PULL_HELD` 4 of the 5 candles before
  closed above the VWAP) a candle dips to within `TOUCH_ATR` (0.25) ATR of today's VWAP and closes green back above it;
  the mirror sells. No volume check (a pullback is quiet by nature).
* `SQUEEZE` – **Bollinger squeeze breakout**: the bands (`bb_period` / `bb_std`) were among the narrowest quarter of
  the last `setup_squeeze_candles` (10-100, default 30) candles and this candle is the first to close outside them.
* `BREAKOUT` – **N-candle high / low breakout**: the first close above the highest high (below the lowest low) of the
  `setup_break_candles` (5-100, default 20) candles before it.
* `INSIDE` – **inside-bar breakout**: a candle that stayed wholly inside the one before it (the mother candle, at least
  `MOTHER_ATR` 0.75 ATR tall), then a close beyond the mother candle's high or low.

Two confirmations for the four breakouts (not the pullback): **volume** (`setup_vol_mult`, default 1.5, 0 = not
checked): the breakout candle's volume is at least that many times the average of the 20 candles before it, missing
volume refuses; and **with the day's trend** (`setup_trend`, on by default): a buy only above today's VWAP, a sell only
below. `setup_side` `BOTH` (default) | `LONG` | `SHORT` keeps one side. Two ticked setups that point opposite ways on
the same candle give no order.

Asked once per closed candle of a flat armed stock (`StrategyEngine._setup_entry_pass`). The order goes through
`apply_signal` (entry reason `SETUP_<KEY>`), so every ticked entry check, the stop, flip, market hours, the cut-off,
caps, the wait after an exit and one LIVE bot per stock apply. Exits are the bot's own.

`setup_only` (off by default): only these setups open trades; the bot's own signals (the SMA cross, a turn, join …)
open nothing on the stock (a cross still closes a trade, it does not reverse). Force, news and hand-off orders pass.

Nothing here places an order itself.
"""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

import trend_trade
from indicators import bollinger, derive_minute_volume, session_vwap_series

IST = ZoneInfo("Asia/Kolkata")

KEYS = ("ORB", "VWAP_PULL", "SQUEEZE", "BREAKOUT", "INSIDE")
NAMES = {
    "ORB": "opening-range breakout",
    "VWAP_PULL": "VWAP pullback",
    "SQUEEZE": "Bollinger squeeze breakout",
    "BREAKOUT": "high / low breakout",
    "INSIDE": "inside-bar breakout",
}
REASON_PREFIX = "SETUP_"
ENTRY_REASONS = tuple(REASON_PREFIX + key for key in KEYS)
SIDES = ("BOTH", "LONG", "SHORT")

SESSION_OPEN = dt.time(9, 15)
ORB_DEFAULT, ORB_MIN, ORB_MAX = 15, 5, 60
BREAK_DEFAULT, BREAK_MIN, BREAK_MAX = 20, 5, 100
SQUEEZE_DEFAULT, SQUEEZE_MIN, SQUEEZE_MAX = 30, 10, 100
VOL_DEFAULT, VOL_MAX = 1.5, 10.0
VOL_LOOKBACK = 20  # candles before the breakout candle its volume is set against
VOL_MIN_CANDLES = 5
SQUEEZE_SHARE = 0.25  # the bands count as squeezed in the narrowest quarter of the look-back
TOUCH_ATR = 0.25  # a pullback "reaches" the VWAP within this many ATRs of it
PULL_LOOK, PULL_HELD = 5, 4  # candles before the pullback candle, and how many must have closed beyond the VWAP
MOTHER_ATR = 0.75  # the mother candle of an inside bar is at least this tall, in ATRs


def clean(value) -> str:
    """The ticked setups as a CSV in `KEYS` order; unknown names dropped."""
    if isinstance(value, (list, tuple, set)):
        parts = [str(v) for v in value]
    else:
        parts = str(value or "").replace(";", ",").split(",")
    picked = {p.strip().upper() for p in parts if p and p.strip()}
    return ",".join(key for key in KEYS if key in picked)


def picked(cfg) -> tuple[str, ...]:
    text = clean(getattr(cfg, "setup_entry", "") or "")
    return tuple(text.split(",")) if text else ()


def enabled(cfg) -> bool:
    return bool(picked(cfg))


def only(cfg) -> bool:
    return enabled(cfg) and bool(getattr(cfg, "setup_only", False))


def side_rule(cfg) -> str:
    value = str(getattr(cfg, "setup_side", "BOTH") or "BOTH").upper()
    return value if value in SIDES else "BOTH"


def with_trend(cfg) -> bool:
    value = getattr(cfg, "setup_trend", True)
    return True if value is None else bool(value)


def _int(cfg, name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(getattr(cfg, name, default) or default)
    except (TypeError, ValueError):
        value = default
    return max(low, min(high, value))


def orb_minutes(cfg) -> int:
    return _int(cfg, "setup_orb_minutes", ORB_DEFAULT, ORB_MIN, ORB_MAX)


def break_candles(cfg) -> int:
    return _int(cfg, "setup_break_candles", BREAK_DEFAULT, BREAK_MIN, BREAK_MAX)


def squeeze_candles(cfg) -> int:
    return _int(cfg, "setup_squeeze_candles", SQUEEZE_DEFAULT, SQUEEZE_MIN, SQUEEZE_MAX)


def vol_mult(cfg) -> float:
    raw = getattr(cfg, "setup_vol_mult", VOL_DEFAULT)
    try:
        value = VOL_DEFAULT if raw is None else float(raw)
    except (TypeError, ValueError):
        value = VOL_DEFAULT
    return max(0.0, min(VOL_MAX, value))


def longest(cfg) -> int:
    """Closed candles the ticked setups need before they can read (for how much history to keep); 0 when off."""
    need = 0
    for key in picked(cfg):
        if key == "SQUEEZE":
            need = max(need, _int(cfg, "bb_period", 20, 2, 200) + squeeze_candles(cfg) + 2)
        elif key == "BREAKOUT":
            need = max(need, break_candles(cfg) + 2)
        else:
            need = max(need, VOL_LOOKBACK + 2)
    return need


# ------------------------------------------------------------------ helpers
def _f(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(out) else out


def _ist(closed: pd.DataFrame) -> pd.Series:
    return pd.to_datetime(closed["ts"], unit="s", utc=True).dt.tz_convert(IST)


def _volumes(closed: pd.DataFrame) -> pd.Series:
    if "minute_volume" in closed.columns:
        return pd.to_numeric(closed["minute_volume"], errors="coerce")
    return derive_minute_volume(closed)


def _atr(closed: pd.DataFrame) -> float | None:
    """The last closed candle's ATR (the enriched `atr_14`), else the average range of the last 14 candles."""
    if "atr_14" in closed.columns:
        value = _f(closed["atr_14"].iloc[-1])
        if value is not None and value > 0:
            return value
    tail = closed.tail(14)
    value = _f((tail["high"] - tail["low"]).mean())
    return value if value is not None and value > 0 else None


def volume_refusal(closed: pd.DataFrame, cfg) -> str | None:
    """Why the breakout candle's volume does not confirm, or None (also None when the check is off)."""
    need = vol_mult(cfg)
    if need <= 0:
        return None
    vol = _volumes(closed)
    last = _f(vol.iloc[-1])
    before = vol.iloc[-(VOL_LOOKBACK + 1):-1].dropna()
    if last is None or len(before) < VOL_MIN_CANDLES:
        return "volume not known"
    usual = _f(before.mean())
    if usual is None or usual <= 0:
        return "volume not known"
    if last < need * usual:
        return f"volume {last / usual:.1f}x the usual (needs {need:g}x)"
    return None


def trend_refusal(closed: pd.DataFrame, side: str, cfg) -> str | None:
    """Why the day's trend (price vs today's VWAP) does not agree with `side`, or None (also when off)."""
    if not with_trend(cfg):
        return None
    vwap = _f(session_vwap_series(closed).iloc[-1])
    close = _f(closed["close"].iloc[-1])
    if vwap is None or close is None:
        return "today's VWAP not known"
    if side == "LONG" and close <= vwap:
        return f"price {close:.2f} is under today's VWAP {vwap:.2f}"
    if side == "SHORT" and close >= vwap:
        return f"price {close:.2f} is over today's VWAP {vwap:.2f}"
    return None


# ------------------------------------------------------------------ the setups
# Each returns (side or None, a key that names this one event, what it saw in plain words).
def _orb(closed: pd.DataFrame, cfg) -> tuple[str | None, str, str]:
    minutes = orb_minutes(cfg)
    ist = _ist(closed)
    day = ist.iloc[-1].date()
    today = closed[(ist.dt.date == day).to_numpy()]
    times = _ist(today).dt.time
    end = (dt.datetime.combine(day, SESSION_OPEN) + dt.timedelta(minutes=minutes)).time()
    window = today[((times >= SESSION_OPEN) & (times < end)).to_numpy()]
    after = today[(times >= end).to_numpy()]
    name = f"first {minutes} min"
    if window.empty or after.empty:
        return None, "", f"the {name} range is not complete yet"
    high, low = float(window["high"].max()), float(window["low"].min())
    close = float(after["close"].iloc[-1])
    # The close before this candle: inside the range (or the window's own last close) for a first breakout.
    prev = float(after["close"].iloc[-2]) if len(after) > 1 else float(window["close"].iloc[-1])
    if close > high and prev <= high:
        return "LONG", f"ORB:LONG:{day}", f"closed {close:.2f} above the {name} high {high:.2f}"
    if close < low and prev >= low:
        return "SHORT", f"ORB:SHORT:{day}", f"closed {close:.2f} below the {name} low {low:.2f}"
    return None, "", f"no fresh close beyond the {name} range {low:.2f}-{high:.2f}"


def _vwap_pull(closed: pd.DataFrame, cfg) -> tuple[str | None, str, str]:
    ist = _ist(closed)
    day = ist.iloc[-1].date()
    today = closed[(ist.dt.date == day).to_numpy()]
    if len(today) < PULL_LOOK + 1:
        return None, "", "too few candles today for a VWAP pullback"
    vwap = session_vwap_series(closed).loc[today.index]
    last_vwap, atr = _f(vwap.iloc[-1]), _atr(closed)
    fast, slow = _f(today.get("sma_9", pd.Series([None])).iloc[-1]), _f(today.get("sma_21", pd.Series([None])).iloc[-1])
    if last_vwap is None or atr is None or fast is None or slow is None:
        return None, "", "VWAP, ATR or the SMAs are not formed yet"
    bar = today.iloc[-1]
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    before_close, before_vwap = today["close"].iloc[-(PULL_LOOK + 1):-1], vwap.iloc[-(PULL_LOOK + 1):-1]
    key = f"VWAP_PULL:{int(bar['ts'])}"
    reach = TOUCH_ATR * atr
    if fast > slow and int((before_close > before_vwap).sum()) >= PULL_HELD:
        if l <= last_vwap + reach and c > last_vwap and c > o:
            return "LONG", key, f"up-trend dipped to the VWAP {last_vwap:.2f} (low {l:.2f}) and closed green at {c:.2f}"
        return None, "", "up-trend above the VWAP, no pullback to it on this candle"
    if fast < slow and int((before_close < before_vwap).sum()) >= PULL_HELD:
        if h >= last_vwap - reach and c < last_vwap and c < o:
            return "SHORT", key, f"down-trend rose to the VWAP {last_vwap:.2f} (high {h:.2f}) and closed red at {c:.2f}"
        return None, "", "down-trend below the VWAP, no pullback to it on this candle"
    return None, "", "no steady trend on one side of the VWAP"


def _squeeze(closed: pd.DataFrame, cfg) -> tuple[str | None, str, str]:
    period = _int(cfg, "bb_period", 20, 2, 200)
    try:
        k = float(getattr(cfg, "bb_std", 2.0) or 2.0)
    except (TypeError, ValueError):
        k = 2.0
    look = squeeze_candles(cfg)
    if len(closed) < period + look + 1:
        return None, "", "too few candles for a Bollinger squeeze"
    mid, upper, lower = bollinger(closed["close"], period, k)
    width = ((upper - lower) / mid).iloc[-(look + 1):-1]  # the look-back, ending on the candle before this one
    if width.isna().any():
        return None, "", "Bollinger bands are not formed yet"
    was, level = float(width.iloc[-1]), float(width.quantile(SQUEEZE_SHARE))
    if was > level:
        return None, "", f"no squeeze: bands {was * 100:.2f}% wide, the narrow quarter is under {level * 100:.2f}%"
    c, prev = float(closed["close"].iloc[-1]), float(closed["close"].iloc[-2])
    up, lo = float(upper.iloc[-1]), float(lower.iloc[-1])
    key = f"SQUEEZE:{int(closed['ts'].iloc[-1])}"
    seen = f"after a squeeze (bands {was * 100:.2f}% wide)"
    if c > up and prev <= float(upper.iloc[-2]):
        return "LONG", key, f"closed {c:.2f} above the upper band {up:.2f} {seen}"
    if c < lo and prev >= float(lower.iloc[-2]):
        return "SHORT", key, f"closed {c:.2f} below the lower band {lo:.2f} {seen}"
    return None, "", "squeezed, no close outside the bands yet"


def _breakout(closed: pd.DataFrame, cfg) -> tuple[str | None, str, str]:
    n = break_candles(cfg)
    if len(closed) < n + 2:
        return None, "", f"too few candles for a {n}-candle breakout"
    before = closed.iloc[-(n + 1):-1]
    high, low = float(before["high"].max()), float(before["low"].min())
    c, prev = float(closed["close"].iloc[-1]), float(closed["close"].iloc[-2])
    key = f"BREAKOUT:{int(closed['ts'].iloc[-1])}"
    if c > high and prev <= high:
        return "LONG", key, f"closed {c:.2f} above the {n}-candle high {high:.2f}"
    if c < low and prev >= low:
        return "SHORT", key, f"closed {c:.2f} below the {n}-candle low {low:.2f}"
    return None, "", f"inside the {n}-candle range {low:.2f}-{high:.2f}"


def _inside(closed: pd.DataFrame, cfg) -> tuple[str | None, str, str]:
    if len(closed) < 3:
        return None, "", "too few candles for an inside bar"
    mother, inner, bar = closed.iloc[-3], closed.iloc[-2], closed.iloc[-1]
    m_high, m_low = float(mother["high"]), float(mother["low"])
    if not (float(inner["high"]) <= m_high and float(inner["low"]) >= m_low):
        return None, "", "no inside bar before this candle"
    atr = _atr(closed)
    if atr is None or (m_high - m_low) < MOTHER_ATR * atr:
        return None, "", "inside bar, but its mother candle is too small to matter"
    c = float(bar["close"])
    key = f"INSIDE:{int(bar['ts'])}"
    if c > m_high:
        return "LONG", key, f"closed {c:.2f} above the mother candle's high {m_high:.2f} after an inside bar"
    if c < m_low:
        return "SHORT", key, f"closed {c:.2f} below the mother candle's low {m_low:.2f} after an inside bar"
    return None, "", "inside bar, no close beyond the mother candle yet"


_READERS = {"ORB": _orb, "VWAP_PULL": _vwap_pull, "SQUEEZE": _squeeze, "BREAKOUT": _breakout, "INSIDE": _inside}
_NO_CONFIRM = ("VWAP_PULL",)  # its own rule already reads the VWAP, and a pullback is quiet


def read_one(key: str, closed: pd.DataFrame, cfg) -> tuple[str | None, str, str]:
    """One setup on the closed candles: (side or None, event key, note). Confirmations and the side rule applied."""
    need = {"ts", "open", "high", "low", "close"}
    if closed is None or len(closed) < 3 or not need.issubset(closed.columns):
        return None, "", "too few candles"
    side, event, note = _READERS[key](closed, cfg)
    if side is None:
        return None, "", note
    rule = side_rule(cfg)
    if rule != "BOTH" and side != rule:
        return None, "", f"{note}, but only {'buys' if rule == 'LONG' else 'sells'} are taken"
    if key not in _NO_CONFIRM:
        refusal = volume_refusal(closed, cfg) or trend_refusal(closed, side, cfg)
        if refusal:
            return None, "", f"{note}, but {refusal}"
    return side, event, note


def call(frame: pd.DataFrame | None, cfg) -> tuple[str | None, str | None, str | None, str]:
    """(LONG / SHORT / None, the setup's key, its event key, why in plain words) on `frame`'s closed candles.

    `frame` is the bot's enriched candles with the forming candle last (never read)."""
    keys = picked(cfg)
    if not keys:
        return None, None, None, "no setup is ticked"
    if frame is None or len(frame) < 4:
        return None, None, None, "too few candles"
    closed = frame.iloc[:-1]
    hits: list[tuple[str, str, str, str]] = []
    misses: list[str] = []
    for key in keys:
        side, event, note = read_one(key, closed, cfg)
        if side is None:
            misses.append(f"{NAMES[key]}: {note}")
        else:
            hits.append((side, key, event, note))
    if not hits:
        return None, None, None, "; ".join(misses)
    if len({h[0] for h in hits}) > 1:
        names = ", ".join(f"{NAMES[h[1]]} {'buy' if h[0] == 'LONG' else 'sell'}" for h in hits)
        return None, None, None, f"setups disagree on this candle ({names}): no order"
    side, key, event, note = hits[0]
    also = "".join(f"; also {NAMES[h[1]]}" for h in hits[1:])
    return side, key, event, f"{NAMES[key]}: {note}{also}"


def blocks(cfg, why: str | None) -> str | None:
    """Why the bot's own signal may not open a trade here (`setup_only`), or None."""
    if not only(cfg):
        return None
    reason = str(why or "SMA_CROSS").split(":", 1)[0].split("@", 1)[0]
    if reason.startswith(REASON_PREFIX) or reason.startswith(trend_trade.PASS_REASONS):
        return None
    return "only the ticked technical setups open trades on this stock"
