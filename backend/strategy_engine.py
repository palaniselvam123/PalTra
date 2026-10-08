"""SMA(9, 21) crossover + 1.5× ATR(14) stop, with stop-and-reverse.

Safety invariants
-----------------
1. Signals read `df.iloc[-3]` vs `df.iloc[-2]` only. The forming bar (`iloc[-1]`)
   is never a signal bar.
2. An `asyncio.Lock` serialises every order. A PENDING/TRANSIT order blocks
   a new one.
3. Before Long↔Short, the resting exchange SL is cancelled and the cancel is
   verified. If the SL already filled, the reverse is aborted.
4. The engine boots in PAPER. LIVE is a confirmed mode on the client.
5. `max_daily_loss` squares off, cancels SL orders, and locks the day.
   `max_trades_per_day` blocks the next entry (a reverse closes flat and locks).
6. A live order is a position only after Groww reports the fill. A pending
   order is cancelled. It must not sit on the book and fill hours later.
   An exit is not sent when Groww does not hold that position.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import time
from dataclasses import dataclass, field
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
from sqlalchemy import and_, func, or_, select, true

from charges import calculate_charges, legs_for
from database import session_factory
from groww_client import IN_FLIGHT, TERMINAL_CANCELLED, TERMINAL_FILLED, GrowwClient, market_is_open
from indicators import (
    _session_vwap,
    bollinger,
    bollinger_exit,
    closed_candle_cross,
    closed_technical_snapshot,
    derive_minute_volume,
    enrich,
    entry_filter_reason,
    format_signal_report,
    minute_volume_stats,
    rsi_wilder,
    session_vwap_series,
    sma_gap_pct,
    sma_gap_signed,
)
from candle_patterns import closes_bucket, pattern_call, setting as pattern_setting, uses_patterns
from gap_mode import Pending, fade_confirmed, judge_exit, judge_pending, uses_gap_mode
from gap_trail import gap_levels, tighten, uses_gap_stop
from tsl import tsl_entry_levels, tsl_settings, tsl_stop, uses_tsl
from models import BotConfig, TradeLog, trade_ref
import paper_wallet
import tick_sizes
import tick_store
from tick_sizes import round_price

logger = logging.getLogger("sma.strategy")

IST = ZoneInfo("Asia/Kolkata")


class OrderBusy(Exception):
    pass


class SlCancelFailed(Exception):
    pass


class ForceRefused(Exception):
    """A manual force-order was refused before a broker request."""


def _intraday_is_shut(message: str) -> bool:
    """Groww has stopped taking intraday orders for the session."""
    text = (message or "").lower()
    return "intraday" in text and (
        "not available" in text or "about to close" in text or "stopped" in text
    )


def _opposite(direction: str) -> str:
    return "SHORT" if direction == "LONG" else "LONG"


def flips_orders(cfg) -> bool:
    """Flip strategy (flip_orders): a buy signal places a sell and a sell signal a buy."""
    return bool(getattr(cfg, "flip_orders", False))


def cross_exits(cfg) -> bool:
    """SMA cross exit (cross_exit, on unless set off): an opposite cross closes the trade."""
    return getattr(cfg, "cross_exit", None) is not False


@dataclass
class OpenPosition:
    direction: str  # LONG | SHORT
    qty: int
    entry_price: float
    ma_cross_price: float
    atr_at_entry: float
    sl_trigger: float
    sl_order_id: str
    entry_order_id: str
    entry_time: dt.datetime
    trade_id: int
    mode: str
    # False when entered with use_stop off. sl_trigger is then only the
    # level a stop would have used, and nothing watches it.
    stop_active: bool = True
    # SMA-gap moving stop (PAPER only): sl_trigger trails each closed candle
    # and the position also exits at `target`.
    trailing: bool = False
    target: float | None = None
    # Groww-style trailing stop (tsl.py), PAPER and LIVE: the stop sits
    # tsl_points from entry and moves tsl_step for each tsl_step the price
    # gains past tsl_best, its most favourable LTP since entry.
    tsl_step: float | None = None
    tsl_points: float | None = None
    tsl_best: float | None = None
    # LIVE: monotonic time of the last stop modify sent to Groww.
    tsl_modified_at: float = 0.0
    # Bollinger exit: the last closed candle read, and whether a close has
    # been on the trade's side of the middle band (arms the MIDDLE exit).
    bb_bar: int | None = None
    bb_armed: bool = False
    # SMA gap mode exit: last closed candle read, its gap, and armed / peak.
    gap_bar: int | None = None
    gap_prev: float | None = None
    gap_state: dict = field(default_factory=dict)
    # Highest and lowest price seen while the trade is open (every tick's
    # LTP, the entry and the exit). Saved as TradeLog.max_high / max_low.
    high: float | None = None
    low: float | None = None
    # Flip strategy: the order went the other way from the signal (a buy
    # signal sold, a sell signal bought). Signal exits follow the signal.
    flipped: bool = False

    @property
    def signal_direction(self) -> str:
        """The side the signal asked for. Crosses, gap fade and Bollinger exits judge this."""
        return _opposite(self.direction) if self.flipped else self.direction

    def note_price(self, price: float) -> None:
        if price <= 0:
            return
        self.high = price if self.high is None else max(self.high, price)
        self.low = price if self.low is None else min(self.low, price)


def _ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def _parse_hhmm(value: str) -> dt.time:
    hh, mm = (value or "15:15").split(":")
    return dt.time(int(hh), int(mm))


MAX_TRADE_SYMBOLS = 24

# The settings that decide a trade. Each trade keeps a copy of them from its
# entry (TradeLog.strategy), and each replay run keeps one for the whole run.
SNAPSHOT_FIELDS = (
    "qty", "sma_fast", "sma_slow", "atr_period", "atr_multiplier", "use_stop",
    "stop_type", "gap_sl_mult", "gap_tp_mult", "gap_min_pct",
    "tsl_sl_points", "tsl_trail_points", "tsl_target_points",
    "use_adx_filter", "adx_threshold", "use_vwap", "use_volume", "volume_min_ratio",
    "use_density", "density_min_pct", "use_rsi", "rsi_long_min", "rsi_long_max",
    "rsi_short_min", "rsi_short_max", "use_bollinger", "bb_period", "bb_std", "bb_min_width_pct",
    "bb_exit", "use_gap_long", "gap_long_min", "gap_long_max", "use_gap_short", "gap_short_min",
    "gap_short_max", "use_gap_mode", "gap_entry_long", "gap_exit_long", "gap_entry_short",
    "gap_exit_short", "gap_giveback_pct", "gap_entry_delay_min", "gap_entry_window_min",
    "gap_fade_confirm_sma", "gap_fade_min_candles", "gap_fade_intrabar",
    "use_candle_dir", "candle_dir_count", "candle_dir_rule", "flip_orders", "cross_exit",
    "entry_mode", "pattern_tf", "pattern_trend", "pattern_set", "pattern_min_edge",
    "max_daily_loss", "entry_cutoff_time", "square_off_time",
)


# The settings one stock may set for itself. The rest of SNAPSHOT_FIELDS
# (daily loss, entry cut-off, square-off) and the mode, trade cap and Trade
# list are for the whole account and always come from the shared row.
STOCK_FIELDS = tuple(
    name for name in SNAPSHOT_FIELDS if name not in ("max_daily_loss", "entry_cutoff_time", "square_off_time")
)


def stock_settings(cfg) -> dict[str, dict]:
    """Every stock's own overrides, {SYMBOL: {field: value}}. Never raises."""
    raw = getattr(cfg, "stock_settings", None)
    if isinstance(raw, dict):
        data = raw
    else:
        try:
            data = json.loads(raw) if raw else {}
        except (TypeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict] = {}
    for symbol, fields in data.items():
        if not isinstance(fields, dict):
            continue
        kept = {k: v for k, v in fields.items() if k in STOCK_FIELDS and v is not None}
        if kept:
            out[str(symbol).upper()] = kept
    return out


def stock_overrides(cfg, symbol: str) -> dict:
    return stock_settings(cfg).get((symbol or "").upper(), {})


def settings_for(cfg, symbol: str):
    """`cfg` with this stock's own settings on top, for any config-like object
    (a BotConfig or a replay run's snapshot). The input is not changed.
    """
    if cfg is None:
        return None
    if isinstance(cfg, BotConfig):
        return _cfg_for(cfg, symbol)
    own = stock_overrides(cfg, symbol)
    if not own:
        return cfg
    base = {name: getattr(cfg, name) for name in dir(cfg) if not name.startswith("_") and not callable(getattr(cfg, name, None))}
    return SimpleNamespace(**{**base, **own})


def settings_snapshot(cfg: BotConfig) -> dict:
    return {name: getattr(cfg, name, None) for name in SNAPSHOT_FIELDS}


# Most trades the blotter loads for one book. /api/trades stays at the newest
# 200 across all books, because the page polls it every few seconds.
BOOK_LIMIT = 20000
BOOKS = ("PAPER", "LIVE", "REPLAY", "RESEARCH")


def bot_rows(bot: int):
    """Rows of one SMA bot. Rows from before the bots existed (NULL) are bot 1's."""
    if int(bot) == 1:
        return or_(TradeLog.bot == 1, TradeLog.bot.is_(None))
    return TradeLog.bot == int(bot)


def _mode_filter(mode: str):
    """Rows of one book. Old rows with no mode belong to the practice book."""
    if mode == "PAPER":
        return or_(TradeLog.mode == "PAPER", TradeLog.mode.is_(None))
    return TradeLog.mode == mode


def pack_strategies(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Send each distinct strategy once and point rows at it.

    Thousands of trades usually share a handful of settings; repeating the
    full snapshot on every row would make the response many times larger.
    """
    strategies: list[dict] = []
    # book() hands rows with the same stored settings the same dict, so the
    # object identity is enough to spot a repeat.
    seen: dict[int, int] = {}
    packed: list[dict] = []
    for row in rows:
        item = dict(row)
        settings = item.pop("strategy", None)
        ref = None
        if settings:
            ref = seen.get(id(settings))
            if ref is None:
                ref = seen[id(settings)] = len(strategies)
                strategies.append(settings)
        item["strategy_ref"] = ref
        packed.append(item)
    return packed, strategies


def _strategy_of(row: TradeLog, parsed: dict[str, dict | None] | None = None) -> dict | None:
    raw = getattr(row, "strategy", None)
    if not raw:
        return None
    if parsed is not None and raw in parsed:
        return parsed[raw]
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = None
    value = value if isinstance(value, dict) else None
    if parsed is not None:
        parsed[raw] = value
    return value


def trade_names(cfg: BotConfig) -> list[str]:
    """Stocks the bot may order. The chart symbol is not implied."""
    raw = getattr(cfg, "trade_symbols", None) or ""
    names: list[str] = []
    for part in str(raw).upper().replace(" ", "").split(","):
        if part and part.isalnum() and part not in names:
            names.append(part)
    return names[:MAX_TRADE_SYMBOLS]


def _cfg_for(cfg: BotConfig, symbol: str) -> BotConfig:
    """This stock's settings: the shared row with the stock's own overrides on top."""
    data = {col.name: getattr(cfg, col.name) for col in BotConfig.__table__.columns}
    data["symbol"] = symbol
    data.update(stock_overrides(cfg, symbol))
    return BotConfig(**data)


def _entry_block(
    frame: pd.DataFrame,
    direction: str,
    cfg: BotConfig,
    price: float | None = None,
    only: str | None = None,
) -> str | None:
    """Checked entry filters only. An unchecked box is not read.

    `only` ("vwap", "volume", "density", "rsi", "bollinger", "gap" or "candle_dir") reads that one filter, so a
    message can say which of several checked filters agrees and which does not.
    """

    def on(name: str) -> bool:
        return bool(getattr(cfg, f"use_{name}", False)) and (only is None or only == name)

    return entry_filter_reason(
        frame,
        direction,
        use_vwap=on("vwap"),
        use_volume=on("volume"),
        use_density=on("density"),
        use_rsi=on("rsi"),
        volume_min_ratio=float(getattr(cfg, "volume_min_ratio", 1.0) or 1.0),
        density_min_pct=float(getattr(cfg, "density_min_pct", 50.0) or 50.0),
        rsi_long_min=float(getattr(cfg, "rsi_long_min", 40.0) or 40.0),
        rsi_long_max=float(getattr(cfg, "rsi_long_max", 70.0) or 70.0),
        rsi_short_min=float(getattr(cfg, "rsi_short_min", 30.0) or 30.0),
        rsi_short_max=float(getattr(cfg, "rsi_short_max", 60.0) or 60.0),
        use_bollinger=on("bollinger"),
        bb_period=int(getattr(cfg, "bb_period", 20) or 20),
        bb_std=float(getattr(cfg, "bb_std", 2.0) or 2.0),
        bb_min_width_pct=_bb_min_width(cfg),
        use_gap_long=bool(getattr(cfg, "use_gap_long", False)) and only in (None, "gap"),
        use_gap_short=bool(getattr(cfg, "use_gap_short", False)) and only in (None, "gap"),
        gap_long_min=_gap_setting(cfg, "gap_long_min"),
        gap_long_max=_gap_setting(cfg, "gap_long_max"),
        gap_short_min=_gap_setting(cfg, "gap_short_min"),
        gap_short_max=_gap_setting(cfg, "gap_short_max"),
        use_candle_dir=on("candle_dir"),
        candle_dir_count=_candle_dir_count(cfg),
        candle_dir_rule=_candle_dir_rule(cfg),
        price=price,
    )


def _candle_dir_count(cfg) -> int:
    value = getattr(cfg, "candle_dir_count", None)
    return 2 if value is None else max(1, int(value))


def _candle_dir_rule(cfg) -> str:
    rule = (getattr(cfg, "candle_dir_rule", None) or "CLOSES").upper()
    return rule if rule in ("CLOSES", "COLOUR", "BOTH") else "CLOSES"


_GAP_DEFAULTS = {"gap_long_min": 0.02, "gap_long_max": 0.5, "gap_short_min": -0.5, "gap_short_max": -0.02}


def _gap_setting(cfg, key: str) -> float:
    """A gap range end; 0 and negative numbers are real settings."""
    value = getattr(cfg, key, None)
    return _GAP_DEFAULTS[key] if value is None else float(value)


def _uses_check(cfg, name: str, direction: str) -> bool:
    """Whether a named entry check is ticked for this side."""
    if name == "gap":
        key = "use_gap_long" if direction == "LONG" else "use_gap_short"
        return bool(getattr(cfg, key, False))
    return bool(getattr(cfg, f"use_{name}", False))


def _bb_min_width(cfg) -> float:
    """The squeeze threshold; 0 is a real setting (squeeze check off)."""
    value = getattr(cfg, "bb_min_width_pct", None)
    return 0.15 if value is None else float(value)


def _short_block_label(reason: str) -> str:
    """A few words for the chart marker, e.g. "RSI 72.3" or "VWAP"."""
    parts = []
    for part in (reason or "").split("; "):
        low = part.lower()
        if low.startswith("vwap"):
            parts.append("VWAP")
        elif low.startswith("rsi"):
            words = part.split()
            parts.append(f"RSI {words[1]}" if len(words) > 1 and words[1][0].isdigit() else "RSI")
        elif low.startswith("volume"):
            parts.append("Vol")
        elif low.startswith("density"):
            parts.append("Density")
        elif low.startswith("bollinger squeeze"):
            parts.append("BB squeeze")
        elif low.startswith("bollinger"):
            parts.append("BB")
        elif low.startswith("sma gap"):
            words = part.split()
            parts.append(f"Gap {words[2]}" if len(words) > 2 and words[2][:1] in "+-" else "Gap")
        elif low.startswith("candle direction"):
            parts.append("Candles")
        elif low.startswith("adx"):
            parts.append(part.split(" is")[0])
        elif part:
            parts.append(part[:16])
    return " · ".join(parts)


def filter_blocks(frame: pd.DataFrame, cfg) -> list[dict]:
    """SMA crosses on closed candles that the entry filters would refuse.

    Each cross is judged exactly as the bot judges one: the frame up to the
    candle after the cross (which plays the forming bar), through
    `_entry_block`, plus the ADX gate. Only refused crosses are returned,
    oldest first, with the full reason and a short label for the chart.
    """
    if cfg is None or frame is None or len(frame) < 3 or not {"sma_9", "sma_21", "ts"}.issubset(frame.columns):
        return []
    use_adx = bool(getattr(cfg, "use_adx_filter", False))
    if not (use_adx or any(bool(getattr(cfg, k, False)) for k in (
        "use_vwap", "use_volume", "use_density", "use_rsi", "use_bollinger", "use_gap_long", "use_gap_short",
        "use_candle_dir",
    ))):
        return []
    fast = frame["sma_9"].to_numpy(dtype=float)
    slow = frame["sma_21"].to_numpy(dtype=float)
    out: list[dict] = []
    for i in range(1, len(frame) - 1):
        a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if any(pd.isna(v) for v in (a0, b0, a1, b1)):
            continue
        if a0 <= b0 and a1 > b1:
            direction = "LONG"
        elif a0 >= b0 and a1 < b1:
            direction = "SHORT"
        else:
            continue
        window = frame.iloc[: i + 2]
        reasons = []
        block = _entry_block(window, direction, cfg)
        if block:
            reasons.append(block)
        if use_adx:
            adx = _finite(frame.iloc[i].get("adx_14"))
            need = float(getattr(cfg, "adx_threshold", 20) or 20)
            if adx is None or adx < need:
                reasons.append(f"ADX {adx:.1f} is below {need:g}" if adx is not None else "ADX is not ready")
        if reasons:
            reason = "; ".join(reasons)
            out.append(
                {
                    "time": int(frame.iloc[i]["ts"]),
                    "direction": direction,
                    "reason": reason,
                    "label": _short_block_label(reason),
                }
            )
    return out


_CHECK_NAMES = (
    ("vwap", "VWAP"), ("volume", "Volume"), ("density", "Density"), ("rsi", "RSI"), ("bollinger", "Bollinger"),
    ("gap", "SMA gap"), ("candle_dir", "Candle direction"),
)


def _check_passed(name: str, frame: pd.DataFrame, direction: str, cfg) -> str:
    """The reading behind a filter that agrees, e.g. "RSI 55.2 (40–70)"."""
    try:
        closed = frame.iloc[:-1]
        bar = closed.iloc[-1]
        if name == "vwap":
            vwap = _session_vwap(closed)
            side = "above" if direction == "LONG" else "below"
            return f"VWAP: {float(bar['close']):.2f} {side} {vwap:.2f}"
        if name == "rsi":
            rsi = float(rsi_wilder(closed["close"], 14).iloc[-1])
            if direction == "LONG":
                lo, hi = getattr(cfg, "rsi_long_min", 40.0) or 40.0, getattr(cfg, "rsi_long_max", 70.0) or 70.0
            else:
                lo, hi = getattr(cfg, "rsi_short_min", 30.0) or 30.0, getattr(cfg, "rsi_short_max", 60.0) or 60.0
            return f"RSI {rsi:.1f} (inside {float(lo):.0f}–{float(hi):.0f})"
        if name == "volume":
            current, average, _got = minute_volume_stats(closed, 20)
            ratio = float(getattr(cfg, "volume_min_ratio", 1.0) or 1.0)
            return f"Volume {current:.0f} ≥ {ratio:g}× avg {average:.0f}"
        if name == "bollinger":
            period = int(getattr(cfg, "bb_period", 20) or 20)
            _mid, upper, lower = bollinger(closed["close"], period, float(getattr(cfg, "bb_std", 2.0) or 2.0))
            u, lo = float(upper.iloc[-1]), float(lower.iloc[-1])
            mid = (u + lo) / 2
            width = (u - lo) / mid * 100 if mid else 0.0
            return f"Bollinger: {float(bar['close']):.2f} inside {lo:.2f}–{u:.2f} (bands {width:.2f}% wide)"
        if name == "gap":
            gap = sma_gap_signed(bar.get("sma_9"), bar.get("sma_21"))
            side = "buy" if direction == "LONG" else "sell"
            lo = _gap_setting(cfg, f"gap_{'long' if direction == 'LONG' else 'short'}_min")
            hi = _gap_setting(cfg, f"gap_{'long' if direction == 'LONG' else 'short'}_max")
            return f"SMA gap {gap:+.3f}% (inside the {side} range {lo:g}% to {hi:g}%)"
        if name == "candle_dir":
            n = _candle_dir_count(cfg)
            rule = _candle_dir_rule(cfg)
            word = "rising" if direction == "LONG" else "falling"
            what = {"CLOSES": f"closes {word}", "COLOUR": "green" if direction == "LONG" else "red",
                    "BOTH": f"closes {word}, {'green' if direction == 'LONG' else 'red'}"}[rule]
            closes = " → ".join(f"{float(c):.2f}" for c in closed["close"].iloc[-(n + 1):])
            return f"Candle direction: last {n} {what} ({closes})"
        if name == "density":
            span = float(bar["high"]) - float(bar["low"])
            body = abs(float(bar["close"]) - float(bar["open"]))
            density = 0.0 if span <= 0 else body / span * 100
            return f"Density {density:.0f}%"
    except Exception:  # noqa: BLE001
        pass
    return dict(_CHECK_NAMES).get(name, name)


def entry_checks(frame: pd.DataFrame, direction: str, cfg) -> list[str]:
    """Each checked entry filter on the last closed candle, read as the bot reads it.

    One line per checked filter: "✅ …" when it lets an entry through, "❌ …"
    with the reason when it would refuse. Unchecked filters are left out.
    Only text for alerts; the order decision stays in `apply_signal`.
    """
    if cfg is None or frame is None or getattr(frame, "empty", True):
        return []
    lines: list[str] = []
    for name, _label in _CHECK_NAMES:
        if not _uses_check(cfg, name, direction):
            continue
        reason = _entry_block(frame, direction, cfg, only=name)
        lines.append(f"❌ {reason}" if reason else f"✅ {_check_passed(name, frame, direction, cfg)}")
    if bool(getattr(cfg, "use_adx_filter", False)):
        need = float(getattr(cfg, "adx_threshold", 20) or 20)
        adx = _finite(frame.iloc[-2].get("adx_14")) if len(frame) >= 2 else None
        if adx is None:
            lines.append("❌ ADX is not ready")
        else:
            lines.append(f"{'✅' if adx >= need else '❌'} ADX {adx:.1f} (needs {need:g})")
    if not lines:
        lines.append("No filters on — the cross alone places the order")
    return lines


def chart_filters(cfg) -> dict:
    """Which filter lines the chart should draw, and the RSI bands."""
    if cfg is None:
        return {}
    return {
        "use_vwap": bool(getattr(cfg, "use_vwap", False)),
        "use_rsi": bool(getattr(cfg, "use_rsi", False)),
        "rsi_long_min": float(getattr(cfg, "rsi_long_min", 40.0) or 40.0),
        "rsi_long_max": float(getattr(cfg, "rsi_long_max", 70.0) or 70.0),
        "rsi_short_min": float(getattr(cfg, "rsi_short_min", 30.0) or 30.0),
        "rsi_short_max": float(getattr(cfg, "rsi_short_max", 60.0) or 60.0),
        "use_bollinger": bool(getattr(cfg, "use_bollinger", False)),
        "bb_period": int(getattr(cfg, "bb_period", 20) or 20),
        "bb_std": float(getattr(cfg, "bb_std", 2.0) or 2.0),
        "bb_exit": (getattr(cfg, "bb_exit", None) or "OFF").upper(),
        "atr_stop": getattr(cfg, "use_stop", True) is not False and (getattr(cfg, "stop_type", "ATR") or "ATR") == "ATR",
    }


# The settings filter_blocks reads. The chart cache is keyed on them.
_FILTER_KEYS = (
    "use_adx_filter", "adx_threshold", "use_vwap", "use_volume", "volume_min_ratio",
    "use_density", "density_min_pct", "use_rsi", "rsi_long_min", "rsi_long_max",
    "rsi_short_min", "rsi_short_max", "use_bollinger", "bb_period", "bb_std", "bb_min_width_pct",
    "use_gap_long", "gap_long_min", "gap_long_max", "use_gap_short", "gap_short_min", "gap_short_max",
    "use_candle_dir", "candle_dir_count", "candle_dir_rule",
)


def _forming_row(bar) -> dict:
    """The forming candle for the chart. Its indicators are left blank so the
    lines stop on the last closed candle and do not repaint."""
    return {
        "time": int(bar["ts"]),
        "open": float(bar["open"]),
        "high": float(bar["high"]),
        "low": float(bar["low"]),
        "close": float(bar["close"]),
        "sma9": None,
        "sma21": None,
        "atr14": None,
        "vwap": None,
        "rsi14": None,
        "volume": None,
    }


def candle_rows(frame: pd.DataFrame) -> list[dict]:
    """Chart candles with SMA, ATR, session VWAP and RSI 14 per candle."""
    if frame is None or frame.empty:
        return []
    vwap = session_vwap_series(frame)
    rsi = rsi_wilder(frame["close"], 14)
    # Shares traded in each minute (Groww's own volume is a running session
    # total). Feeds the chart's volume profile; missing stays None, not 0.
    if "minute_volume" in frame.columns:
        minute_vol = pd.to_numeric(frame["minute_volume"], errors="coerce")
    else:
        minute_vol = derive_minute_volume(frame)
    rows = []
    for i, (_, row) in enumerate(frame.iterrows()):
        rows.append(
            {
                "time": int(row["ts"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "sma9": _finite(row.get("sma_9")),
                "sma21": _finite(row.get("sma_21")),
                "atr14": _finite(row.get("atr_14")),
                "vwap": _finite(vwap.iloc[i]),
                "rsi14": _finite(rsi.iloc[i]),
                "volume": _finite(minute_vol.iloc[i]),
            }
        )
    return rows


class StrategyEngine:
    #: The BotConfig row this engine trades with. The research desk has its own.
    config_id = 1
    #: Which SMA bot this is (TradeLog.bot): 1 the main desk, 2-4 the extra bots.
    #: Research and replay engines stay 1; their mode tag keeps their books apart.
    bot_id = 1
    #: bots.live_conflict once the bots are registered: refuses a LIVE entry on
    #: a stock another LIVE bot owns. None (no check) for a lone engine in tests.
    live_guard = None
    #: Whether this engine's live prices go to the second-by-second record
    #: (tick_store). A replay's prices are made up from minute candles: never.
    records_ticks = True
    #: Whether PAPER entries take margin from the practice wallet (paper_wallet).
    #: Bots 1-4 do; replay and research engines keep their own practice money.
    uses_wallet = True

    def __init__(self, broker: GrowwClient | None = None):
        self.broker = broker or GrowwClient(mode="PAPER")
        self.lock = asyncio.Lock()
        self.inflight: str | None = None  # PENDING | TRANSIT | None
        self.status = "STOPPED"  # STOPPED | RUNNING | PAUSED | DAY_COMPLETED | HALTED
        self.halt_reason = ""
        self.positions: dict[str, OpenPosition] = {}
        self._focus = ""
        self._unbound_position: OpenPosition | None = None
        self._frames: dict[str, pd.DataFrame] = {}
        self._ltps: dict[str, float] = {}
        # Closed-bar timestamp already judged for an entry. A bar is not
        # marked here until its candle is actually on the tape.
        self._judged_bar: dict[str, int] = {}
        # Last closed candle each moving stop/target was recalculated on.
        self._trail_bar: dict[str, int] = {}
        self._signals: dict[str, str] = {}
        # symbol -> timestamp of the closed bar already on the tape when the
        # bot was started or the stock was armed. None means the first bar
        # we see is that bar. A cross on it must not trade.
        self._skip_cross_until: dict[str, int | None] = {}
        # SMA gap mode: a cross waiting for its gap before the order goes.
        self._gap_pending: dict[str, Pending] = {}
        # Heads-up keys already sent, so a near cross does not message every minute.
        self._warned: set[tuple] = set()
        # Latest order refusal per stock (Groww's own words), kept for the
        # screen after the next tick's note replaces it, and when it was sent
        # to Telegram so a refusal repeated every minute alerts once.
        self._rejects: dict[str, tuple[str, dt.datetime]] = {}
        self._reject_sent: dict[tuple[str, str], dt.datetime] = {}
        self.ltp = 0.0
        self.sma9 = None
        self.sma21 = None
        self.atr14 = None
        self.adx14 = None
        self.sma_gap = None
        self.sma_gap_pct = None
        self.vwap = None
        self.minute_volume = None
        self.avg_minute_volume = None
        self.volume_ratio = None
        self.rsi14 = None
        self.data_source = "SIMULATOR"
        self.last_error = ""
        self.last_signal = ""
        self.candles = pd.DataFrame()
        self._stop = False
        self._sleep = asyncio.sleep
        self.realized_net = 0.0
        self.trades_today = 0
        self._session_date = self._now().date().isoformat()
        self._quote_symbol = ""
        self._cfg_cache = None
        self._ticks_retry_at = 0.0
        self._ticks_job = None
        self._realized_mode: str | None = None

    def _now(self) -> dt.datetime:
        """The engine's clock. A replay engine runs on the replayed day instead."""
        return _ist_now()

    def _alert(self, message: str) -> None:
        """Telegram/WhatsApp alert. A replay engine sends none."""
        _schedule_whatsapp(message)

    def _view_frame(self, view: str, frame: pd.DataFrame, cfg: BotConfig) -> tuple[pd.DataFrame, bool]:
        """The chart stock's enriched candles, and whether they were recomputed.

        The display readings (SMA, ATR, ADX, VWAP, RSI) come from the last
        closed candle, so they only change when a candle closes. Recomputing
        every indicator over ~2,500 candles on every tick was most of a
        replay tick's cost. Between closes only the forming candle's prices
        move, so the cached frame is reused with that row updated. Its own
        indicators are never read (the chart blanks them). Orders do not use
        this frame: each armed stock is enriched afresh when its candle closes.
        """
        if frame is None or frame.empty:
            return frame, True
        if len(frame) < 2:
            return enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period), True
        closed = frame.iloc[-2]
        key = (
            view,
            len(frame),
            int(frame["ts"].iloc[0]),
            int(closed["ts"]),
            float(closed["close"]),
            float(closed.get("volume", 0) or 0),
            int(frame["ts"].iloc[-1]),
            int(cfg.sma_fast),
            int(cfg.sma_slow),
            int(cfg.atr_period),
        )
        cached = getattr(self, "_view_cache", None)
        if cached is not None and cached[0] == key:
            base = cached[1]
            cols = [c for c in ("open", "high", "low", "close", "volume") if c in frame.columns and c in base.columns]
            now_vals = frame[cols].iloc[-1].to_numpy()
            if (base[cols].iloc[-1].to_numpy() == now_vals).all():
                return base, False
            # A new frame, never an edit in place: the chart may be reading
            # the previous one from another thread.
            out = base.copy()
            out.iloc[-1, [out.columns.get_loc(c) for c in cols]] = now_vals
            return out, False
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        self._view_cache = (key, enriched)
        return enriched, True

    def _position_key(self) -> str:
        if self._focus:
            return self._focus
        cached = self._cfg_cache
        if cached is not None and cached.symbol:
            return str(cached.symbol).upper()
        return ""

    @property
    def position(self) -> OpenPosition | None:
        key = self._position_key()
        if self._unbound_position is not None and key:
            self.positions[key] = self._unbound_position
            self._unbound_position = None
        if self._unbound_position is not None and not key:
            return self._unbound_position
        return self.positions.get(key)

    @position.setter
    def position(self, value: OpenPosition | None) -> None:
        key = self._position_key()
        if not key:
            self._unbound_position = value
            return
        self._unbound_position = None
        if value is None:
            self.positions.pop(key, None)
        else:
            self.positions[key] = value

    def restore_open_books(self) -> None:
        """A restart must remember a live position or the next cross orders again."""
        with session_factory()() as db:
            rows = db.query(TradeLog).filter(TradeLog.exit_time.is_(None), self._bot_rows()).all()
            # Replay and research rows belong to their own engines, never to this book.
            rows = [row for row in rows if self._restores((row.mode or "PAPER").upper())]
            pending = [
                (
                    str(row.symbol or "").upper(),
                    row.direction,
                    int(row.qty),
                    float(row.entry_price),
                    float(row.ma_cross_price),
                    float(row.atr_at_entry),
                    float(row.sl_trigger_price),
                    row.entry_time,
                    int(row.id),
                    row.mode or "PAPER",
                    row.stop_active is not False,
                    bool(getattr(row, "flipped", False)),
                )
                for row in rows
            ]
        try:
            cfg = self.load_config()
        except Exception:  # noqa: BLE001
            cfg = None
        for symbol, direction, qty, entry, cross, atr, sl, when, trade_id, mode, stop_active, flipped in pending:
            if not symbol or symbol in self.positions:
                continue
            # A practice book on the moving stop keeps trailing from the saved
            # stop; its target comes back on the next closed candle.
            trailing = bool(
                stop_active
                and cfg is not None
                and uses_gap_stop(cfg, live=(mode or "PAPER").upper() == "LIVE")
            )
            tsl_points = tsl_step = None
            target = None
            if stop_active and cfg is not None and not trailing and uses_tsl(cfg):
                # The trailed stop was saved on each move. It never loosens,
                # so trailing resumes from it with the best price reset to entry.
                tsl_points, tsl_step, tgt_points = tsl_settings(cfg)
                _sl, target = tsl_entry_levels(direction, entry, tsl_points, tgt_points)
            if when is not None and when.tzinfo is None:
                when = when.replace(tzinfo=IST)
            self.positions[symbol] = OpenPosition(
                direction=direction,
                qty=qty,
                entry_price=entry,
                ma_cross_price=cross,
                atr_at_entry=atr,
                sl_trigger=sl,
                sl_order_id="",
                entry_order_id="",
                entry_time=when or self._now(),
                trade_id=trade_id,
                mode=mode,
                stop_active=stop_active,
                trailing=trailing,
                target=target,
                tsl_step=tsl_step,
                tsl_points=tsl_points,
                tsl_best=entry if tsl_step else None,
                # Ticks before a restart are not kept; the range restarts at entry.
                high=entry,
                low=entry,
                flipped=flipped,
            )

    def _bot_rows(self):
        """This bot's rows of the trade table (see bot_rows)."""
        return bot_rows(self.bot_id)

    def _restores(self, mode: str) -> bool:
        """Whether an open row of this book is this engine's to pick up after a restart."""
        return mode in ("PAPER", "LIVE")

    def stop(self) -> None:
        self._stop = True

    def load_config(self) -> BotConfig:
        with session_factory()() as db:
            row = db.get(BotConfig, self.config_id)
            if row is None:
                raise RuntimeError("BotConfig missing — init_db() was not called")
            db.expunge(row)
            self._cfg_cache = row
            return row

    def _roll_session(self, now: dt.datetime) -> None:
        day = now.date().isoformat()
        if day != self._session_date:
            self._session_date = day
            self.realized_net = 0.0
            self._realized_mode = None
            self.trades_today = 0
            self._warned.clear()
            self._rejects.clear()
            self._reject_sent.clear()
            self._gap_pending.clear()
            if self.status in ("DAY_COMPLETED", "HALTED"):
                self.status = "STOPPED"
                self.halt_reason = ""

    async def run(self) -> None:
        """1-second loop. Candle logic fires once at second == 1 of each minute."""
        while not self._stop:
            self._refresh_tick_sizes()
            try:
                await self.tick(self._now())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            # A stopped book after the close does not need two quotes a second.
            # That loop was keeping the only CPU busy while the desk waited.
            pause = 5.0 if (not market_is_open(self._now()) and self.status != "RUNNING") else 0.5
            await self._sleep(pause)

    def _refresh_tick_sizes(self) -> None:
        """Load Groww's per-stock tick sizes in a worker thread, once a day.

        The download is large, so the 1-second loop never waits on it. Until
        it lands, prices round to the safe NSE price-band step.
        """
        if os.environ.get("SMA_TICK_SIZES", "").lower() == "off":
            return
        if self._ticks_job is not None and not self._ticks_job.done():
            return
        now_m = time.monotonic()
        if not tick_sizes.is_stale() or now_m < self._ticks_retry_at:
            return
        self._ticks_retry_at = now_m + 600
        self._ticks_job = asyncio.get_running_loop().run_in_executor(None, tick_sizes.load_from_groww)

    async def tick(self, now: dt.datetime) -> None:
        cfg = self.load_config()
        self._roll_session(now)
        if getattr(self, "_realized_mode", None) != (cfg.trading_mode or "PAPER").upper():
            self._refresh_realized((cfg.trading_mode or "PAPER").upper())
        self.broker.set_mode(cfg.trading_mode)
        view = (cfg.symbol or "").upper()
        armed = trade_names(cfg)
        # Armed names come first so a chart symbol cannot crowd one of them
        # out of the quote loop. Open books stay watched after they are disarmed.
        watch = [name for name in dict.fromkeys([*armed, *self.positions.keys(), view]) if name]
        self._forget_unwatched(set(watch))
        self._focus = view
        self._anchor_held_prices()
        await self._drop_positions_groww_does_not_hold(cfg)
        await self._settle_exchange_flat(cfg, armed)
        if self.config_id == 1:
            # The second-by-second switch lives on the live desk's row and covers both desks.
            tick_store.set_enabled(getattr(cfg, "second_ticks", None) is not False)
        seconds = self.records_ticks and tick_store.enabled()
        if seconds:
            # One batched Groww call a second for every watched stock (market
            # hours, Groww session only); refresh() below then serves it.
            prefetch = getattr(self.broker, "refresh_ltps", None)
            if prefetch is not None:
                try:
                    await prefetch(watch)
                except Exception:  # noqa: BLE001
                    pass
        if view != self._quote_symbol:
            self._quote_symbol = view
            cached = self._frames.get(view)
            self.candles = cached if cached is not None else pd.DataFrame()
            self.ltp = self._ltps.get(view, 0.0)
        for symbol in watch:
            if not symbol:
                continue
            try:
                ltp, frame, source = await self.broker.refresh(symbol)
            except Exception as exc:  # noqa: BLE001
                if symbol == view:
                    self.last_error = str(exc)
                    self.data_source = "ERROR"
                continue
            if frame is not None and not frame.empty and len(frame) > 2500:
                frame = frame.iloc[-2500:].reset_index(drop=True)
            self._frames[symbol] = frame
            self._ltps[symbol] = float(ltp)
            if seconds and tick_store.records(source) and market_is_open(now):
                tick_store.add(symbol, now, float(ltp))
            if symbol != view:
                continue
            self.last_error = ""
            self.ltp = float(ltp)
            self.data_source = source
            view_cfg = _cfg_for(cfg, view)
            enriched, fresh = self._view_frame(view, frame, view_cfg)
            self.candles = enriched
            if fresh and not enriched.empty and len(enriched) >= 2:
                # Display values from the last CLOSED bar so the UI does not
                # repaint SMA/ATR with the forming tick. These are the same
                # readings the order path uses.
                closed = enriched.iloc[-2]
                self.sma9 = _finite(closed.get("sma_9"))
                self.sma21 = _finite(closed.get("sma_21"))
                self.atr14 = _finite(closed.get("atr_14"))
                self.adx14 = _finite(closed.get("adx_14"))
                snap = closed_technical_snapshot(enriched)
                self.sma_gap = snap.sma_gap if snap else None
                self.sma_gap_pct = snap.sma_gap_pct if snap else None
                self.vwap = snap.vwap if snap else None
                self.minute_volume = snap.minute_volume if snap else None
                self.avg_minute_volume = snap.average_previous_20_volume if snap else None
                self.volume_ratio = snap.volume_ratio if snap else None
                self.rsi14 = snap.rsi14 if snap else None

        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]

        if self.records_ticks:
            await tick_store.flush()

        # Every open trade remembers its highest and lowest price, paused or not.
        for symbol, pos in self.positions.items():
            pos.note_price(float(self._ltps.get(symbol, 0.0) or 0.0))

        # Practice books close at square-off whether or not the bot is
        # running, and never carry into the next session.
        await self._close_finished_paper_books(now, cfg)

        if self.status != "RUNNING":
            return

        if self._loss_breached(cfg):
            await self._stop_for_loss(cfg)
            return

        for symbol in list(self.positions):
            self._focus = symbol
            self.ltp = self._ltps.get(symbol, 0.0)
            symbol_cfg = _cfg_for(cfg, symbol)
            self._trail_gap_levels(symbol_cfg)
            await self._trail_tsl(symbol_cfg)
            await self._watch_stop(symbol_cfg)
            await self._watch_bollinger(symbol_cfg)
            await self._watch_gap_fade(symbol_cfg)

        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]
        if self.status != "RUNNING":
            return

        for symbol in armed:
            frame = self._frames.get(symbol)
            if frame is None or getattr(frame, "empty", True) or len(frame) < 3:
                continue
            closed_ts = _closed_bar_ts(frame)
            if closed_ts is None:
                continue
            if _candle_is_behind(closed_ts, now):
                # The bar that just closed is not in this frame yet. Judging
                # now would burn the cross, and the next minute would no
                # longer see it.
                self._signals[symbol] = f"{symbol} waiting for the closed candle"
                self.last_signal = self._signals[symbol]
                continue
            if self._judged_bar.get(symbol) == closed_ts:
                continue
            if not self._still_armed(symbol):
                # Taken off the Trade list while this pass fetched quotes.
                continue
            symbol_cfg = _cfg_for(cfg, symbol)
            enriched = enrich(frame, symbol_cfg.sma_fast, symbol_cfg.sma_slow, symbol_cfg.atr_period)
            self._focus = symbol
            self.ltp = self._ltps.get(symbol, self.ltp)
            result = await self.on_minute(now, symbol_cfg, enriched)
            if (result or "").startswith("blocked — order PENDING"):
                continue
            judged = _closed_bar_ts(enriched) or closed_ts
            if judged is not None:
                self._judged_bar[symbol] = judged
            if self.status != "RUNNING":
                break
        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]

    def _market_price(self, symbol: str | None, fallback: float) -> float:
        """Latest quote for this stock, else the given price."""
        ltp = self._ltps.get((symbol or "").upper())
        if ltp is not None and ltp > 0:
            return float(ltp)
        return float(fallback)

    def _still_armed(self, symbol: str) -> bool:
        """Read the Trade list now, not the copy taken at the top of the pass."""
        try:
            return (symbol or "").upper() in trade_names(self.load_config())
        except Exception:  # noqa: BLE001
            return False

    def _forget_unwatched(self, watch: set[str]) -> None:
        """Drop the loop state of stocks that are no longer armed, held, or shown.

        A removed stock is no longer quoted or judged. If it is armed again,
        hold_for_next_cross makes it wait for a fresh cross.
        """
        for table in (self._frames, self._ltps, self._judged_bar, self._signals, self._skip_cross_until, self._gap_pending):
            for symbol in [key for key in table if key not in watch]:
                table.pop(symbol, None)

    def _anchor_held_prices(self) -> None:
        """Give each held stock its own price when Groww has not quoted it."""
        anchor = getattr(self.broker, "anchor_price", None)
        if anchor is None:
            return
        for symbol, pos in list(self.positions.items()):
            if pos is not None:
                anchor(symbol, self._ltps.get(symbol) or pos.entry_price)

    def _exit_price(self, symbol: str, pos: OpenPosition) -> float:
        """That stock's own last price. Never another stock's."""
        ltp = self._ltps.get(symbol)
        if ltp is not None and ltp > 0:
            return float(ltp)
        return float(pos.entry_price)

    async def on_minute(self, now: dt.datetime, cfg: BotConfig, frame: pd.DataFrame) -> str:
        if self.status != "RUNNING":
            return ""
        if self._past_square_off(now, cfg):
            await self._square_off("EOD_SQUARE_OFF")
            if now.time() < _parse_hhmm(cfg.square_off_time):
                # Before the open: yesterday's books are closed, today has
                # not started. Wait for the session instead of ending it.
                self.last_signal = "market closed — no new orders until 09:20 IST"
                return self.last_signal
            if self.status != "DAY_COMPLETED" and not self.positions:
                self.status = "DAY_COMPLETED"
                self.halt_reason = f"Auto square-off at {cfg.square_off_time} IST"
                self._announce_down("DAY_COMPLETED", self.halt_reason)
            return self.halt_reason or "square-off"

        # No new position outside the cash session, in PAPER or LIVE. The
        # practice tape keeps moving on screen, but it does not trade.
        if not market_is_open(now):
            self.last_signal = "market closed — no new orders until 09:20 IST"
            symbol = (cfg.symbol or "").upper()
            if symbol:
                self._signals[symbol] = f"{symbol} {self.last_signal}"
            return self.last_signal

        # Opening-auction buffer.
        if now.time() < dt.time(9, 20):
            self.last_signal = "skipped — opening auction buffer (09:15–09:20)"
            return self.last_signal

        if frame is None or frame.empty:
            return ""
        symbol = (cfg.symbol or "").upper()
        self._focus = symbol
        if uses_patterns(cfg):
            return await self._pattern_minute(symbol, frame, cfg, now)
        self._warn_upcoming(symbol, frame, cfg, now)
        # A cross that was already printed when the bot started, or when this
        # stock was armed, is skipped. The next cross on a newer closed bar
        # is the one that may trade. Being flat does not enter early.
        if self._cross_is_stale(symbol, frame):
            text = f"{symbol} no order — that cross already printed, waiting for a new cross. {_filter_note(cfg)}"
            self._signals[symbol] = text
            self.last_signal = text
            return text
        signal, signal_frame = _signal_on_unjudged_bars(frame, self._judged_bar.get(symbol))
        if uses_gap_mode(cfg):
            return await self._gap_minute(symbol, signal, signal_frame, frame, cfg, now)
        if signal is None or signal_frame is None:
            relation = _sma_side_text(frame)
            if symbol in self.positions:
                text = f"{symbol} holding"
            elif relation:
                text = (
                    f"{symbol} no order — {relation} already, waiting for a new cross. {_filter_note(cfg)}"
                )
            else:
                text = f"{symbol} no order — waiting for an SMA cross. {_filter_note(cfg)}"
            self._signals[symbol] = text
            self.last_signal = text
            return text
        return await self.apply_signal(signal, signal_frame, cfg, now)

    async def _pattern_minute(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Candle-pattern entries (candle_patterns.py), once per closed candle of pattern_tf minutes.

        At the end of each pattern candle the open trade closes (CANDLE_END);
        then a bullish pattern buys and a bearish one sells short at the start
        of the next candle. Market hours, the cut-off, the Trade list, the caps,
        the entry filters, the stop and the flip all apply as for a cross.
        """
        tf = int(pattern_setting(cfg, "pattern_tf"))
        closed_ts = _closed_bar_ts(frame)
        at_end = closed_ts is not None and (tf == 1 or closes_bucket(closed_ts, tf))
        if at_end and symbol in self.positions:
            pos = self.positions[symbol]
            self._focus = symbol
            await self._exit_now(cfg, self._exit_price(symbol, pos), "CANDLE_END")
            if symbol in self.positions:
                text = f"{symbol} could not close at the candle end — {self.last_signal}"
                self._signals[symbol] = text
                return text
        call = pattern_call(frame, cfg)
        if call.side is None:
            text = f"{symbol} no order — {call.note}"
            self._signals[symbol] = text
            self.last_signal = text
            return text
        signal = "BULLISH" if call.side == "LONG" else "BEARISH"
        # No Telegram for a refused pattern: one can print on every candle.
        result = await self.apply_signal(signal, frame, cfg, now, alert=False)
        if result.startswith(("opened", "reversed")):
            result = f"{result} — {call.note}"
            self._signals[symbol] = result
            self.last_signal = result
        return result

    async def _gap_minute(
        self,
        symbol: str,
        signal: str | None,
        signal_frame: pd.DataFrame | None,
        frame: pd.DataFrame,
        cfg: BotConfig,
        now: dt.datetime,
    ) -> str:
        """SMA gap mode, once per closed candle: a cross arms, the gap fires.

        An opposite cross still closes the open trade at once; the reverse
        waits for its own gap like any other entry (see gap_mode.py).
        """
        if signal is not None and signal_frame is not None:
            want = "LONG" if signal == "BULLISH" else "SHORT"
            pos = self.positions.get(symbol)
            # With the SMA cross exit off the trade is held (no close, no armed reverse).
            if pos is not None and pos.signal_direction != want and cross_exits(cfg):
                closed = await self._close_on_cross(signal, signal_frame, cfg, now)
                if symbol in self.positions:
                    return closed  # the close did not go through; do not arm the reverse
            if symbol not in self.positions:
                cross_ts = _closed_bar_ts(signal_frame) or _closed_bar_ts(frame) or 0
                self._gap_pending[symbol] = Pending(want, int(cross_ts))
        pending = self._gap_pending.get(symbol)
        if pending is None or symbol in self.positions:
            self._gap_pending.pop(symbol, None)
            text = (
                f"{symbol} holding"
                if symbol in self.positions
                else f"{symbol} no order — gap mode waits for an SMA cross. {_filter_note(cfg)}"
            )
            self._signals[symbol] = text
            self.last_signal = text
            return text
        bar = frame.iloc[-2]
        gap = sma_gap_signed(bar.get("sma_9"), bar.get("sma_21"))
        action, note = judge_pending(cfg, pending, gap, int(bar["ts"]))
        if action == "enter":
            sig = "BULLISH" if pending.direction == "LONG" else "BEARISH"
            result = await self.apply_signal(sig, frame, cfg, now, alert=not pending.alerted)
            refused = result.startswith(f"{sig} ignored — ") and "no new entries after" not in result
            if refused or result.startswith(("blocked", "skipped")):
                # A ticked filter or ADX said no on this candle: keep waiting,
                # it may agree on a later one. Alert once per armed cross.
                pending.alerted = True
            else:
                self._gap_pending.pop(symbol, None)
            if result.startswith(("opened", "reversed")):
                result = f"{result} — {note}"
                self._signals[symbol] = result
                self.last_signal = result
            return result
        if action == "drop":
            self._gap_pending.pop(symbol, None)
            text = f"{symbol} no order — {note}"
        else:
            text = f"{symbol} {pending.direction} armed — {note}"
        self._signals[symbol] = text
        self.last_signal = text
        return text

    async def _close_on_cross(self, signal: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Gap mode: an opposite cross closes the trade but does not reverse yet."""
        pos = self.position
        if pos is None:
            return ""
        price = round_price(cfg.symbol, float(frame.iloc[-2]["close"]))
        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                self.last_signal = "blocked — order PENDING/TRANSIT"
                return self.last_signal
            self.inflight = "TRANSIT"
            try:
                await self._cancel_sl_verified(pos)
                await self._close_position(pos, self._market_price(cfg.symbol, price), "MA_CROSS", now, cfg)
                self.position = None
                text = f"closed on {signal} — gap mode waits for the gap before the reverse"
            except SlCancelFailed as exc:
                self._note_broker_block(exc)
                text = self.last_signal
            finally:
                self.inflight = None
        self.last_signal = text
        self._signals[(cfg.symbol or "").upper()] = text
        self._log_decision(signal, frame, cfg, now, text)
        if self._loss_breached(cfg):
            await self._stop_for_loss(cfg)
        return text

    async def _watch_gap_fade(self, cfg: BotConfig) -> None:
        """Once per closed candle: close a trade whose SMA gap has faded (gap mode)."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or not uses_gap_mode(cfg) or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart: the exchange stop id is unknown and
            # cannot be cancelled first. The stop and crosses still close it.
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        if closed_ts + 60 <= opened.timestamp():
            return  # that candle closed before the entry
        if pos.gap_bar == closed_ts:
            if bool(getattr(cfg, "gap_fade_intrabar", False)):
                await self._gap_fade_live(cfg, pos, symbol, frame)
            return
        pos.gap_bar = closed_ts
        closes = frame["close"].iloc[:-1].astype(float)
        fast = closes.rolling(int(cfg.sma_fast)).mean()
        slow = closes.rolling(int(cfg.sma_slow)).mean()
        gap = sma_gap_signed(fast.iloc[-1], slow.iloc[-1])
        if gap is None:
            return
        note = judge_exit(cfg, pos.signal_direction, gap, pos.gap_prev, pos.gap_state)
        pos.gap_prev = gap
        if note is None:
            return
        ok, why = fade_confirmed(cfg, pos.signal_direction, float(closes.iloc[-1]), _finite(slow.iloc[-1]), pos.gap_state)
        if not ok:
            # A pullback, not a reversal yet: hold and judge the next closed candle.
            self._signals[symbol] = f"{symbol} holding — {note}, but {why}"
            return
        self._signals[symbol] = f"{symbol} gap exit — {note}"
        await self._exit_now(cfg, self.ltp, "GAP_FADE")

    async def _gap_fade_live(self, cfg: BotConfig, pos: OpenPosition, symbol: str, frame: pd.DataFrame) -> None:
        """gap_fade_intrabar: judge the fade on the live price, about once a second.

        The live price stands in for the forming candle's close, so the gap is
        read "as if this second closed the candle". The closed-candle state
        (armed, widest gap, narrowing run) is only probed on a copy, never
        changed; the next closed candle updates it as usual.
        """
        if pos.gap_prev is None or self.ltp <= 0 or frame is None or len(frame) < 2:
            return
        now_s = self._now().timestamp()
        seen = getattr(self, "_live_fade_at", None)
        if seen is None:
            seen = self._live_fade_at = {}
        if now_s - seen.get(symbol, 0.0) < 1.0:
            return
        seen[symbol] = now_s
        need = int(cfg.sma_slow) + 2
        closes = pd.Series([*frame["close"].iloc[:-1].astype(float).tail(need).tolist(), float(self.ltp)])
        fast = closes.rolling(int(cfg.sma_fast)).mean().iloc[-1]
        slow = closes.rolling(int(cfg.sma_slow)).mean().iloc[-1]
        gap = sma_gap_signed(fast, slow)
        if gap is None:
            return
        probe = dict(pos.gap_state)
        note = judge_exit(cfg, pos.signal_direction, gap, pos.gap_prev, probe)
        if note is None:
            return
        ok, why = fade_confirmed(cfg, pos.signal_direction, float(self.ltp), _finite(slow), probe)
        if not ok:
            self._signals[symbol] = f"{symbol} holding — live {note}, but {why}"
            return
        self._signals[symbol] = f"{symbol} gap exit on the live price — {note}"
        await self._exit_now(cfg, self.ltp, "GAP_FADE")

    def _log_decision(self, signal: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime, decision: str, note: str = "") -> None:
        """Write the closed-candle readings once per entry evaluation.

        A failure here must not change the order. The report is diagnostic.
        """
        try:
            side = "LONG" if signal == "BULLISH" else "SHORT"
            action = "BUY" if side == "LONG" else "SELL"
            snap = closed_technical_snapshot(frame)
            if side == "LONG":
                rsi_min = float(getattr(cfg, "rsi_long_min", 40.0) or 40.0)
                rsi_max = float(getattr(cfg, "rsi_long_max", 70.0) or 70.0)
            else:
                rsi_min = float(getattr(cfg, "rsi_short_min", 30.0) or 30.0)
                rsi_max = float(getattr(cfg, "rsi_short_max", 60.0) or 60.0)
            logger.info(
                "\n%s",
                format_signal_report(
                    symbol=(cfg.symbol or "").upper(),
                    evaluated_at=now.strftime("%Y-%m-%d %H:%M:%S IST"),
                    action=action,
                    snap=snap,
                    use_vwap=bool(getattr(cfg, "use_vwap", False)),
                    use_volume=bool(getattr(cfg, "use_volume", False)),
                    volume_multiple=float(getattr(cfg, "volume_min_ratio", 1.0) or 1.0),
                    use_rsi=bool(getattr(cfg, "use_rsi", False)),
                    rsi_min=rsi_min,
                    rsi_max=rsi_max,
                    decision=decision,
                    note=note,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("signal report failed")

    async def apply_signal(
        self, signal: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime, alert: bool = True
    ) -> str:
        """Stop-and-reverse on a closed-candle cross. Returns a short status."""
        self._focus = (cfg.symbol or "").upper()
        curr = frame.iloc[-2]
        atr = _finite(curr.get("atr_14"))
        if atr is None or atr <= 0:
            text = "skipped — ATR not ready"
            self.last_signal = text
            if self._focus:
                self._signals[self._focus] = text
            self._log_decision(signal, frame, cfg, now, text)
            return text
        cross_price = round_price(cfg.symbol, float(curr["close"]))
        adx = _finite(curr.get("adx_14"))
        adx_blocks_entry = bool(cfg.use_adx_filter) and (adx is None or adx < float(cfg.adx_threshold))
        entry_block = _entry_block(frame, "LONG" if signal == "BULLISH" else "SHORT", cfg)

        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                self.last_signal = "blocked — order PENDING/TRANSIT"
                if self._focus:
                    self._signals[self._focus] = self.last_signal
                self._log_decision(signal, frame, cfg, now, self.last_signal)
                return self.last_signal
            self.inflight = "PENDING"
            try:
                self.inflight = "TRANSIT"
                result = await self._apply_locked(
                    signal=signal,
                    cross_price=cross_price,
                    atr=atr,
                    adx_blocks_entry=adx_blocks_entry,
                    entry_block=entry_block,
                    cfg=cfg,
                    now=now,
                )
                self.last_signal = result
                self._signals[self._focus] = result
                self._log_decision(signal, frame, cfg, now, result)
                if alert:
                    self._alert_refused(signal, frame, cfg, now, result)
                return result
            except SlCancelFailed as exc:
                self._note_broker_block(exc)
                self._log_decision(signal, frame, cfg, now, self.last_signal)
                return self.last_signal
            finally:
                self.inflight = None

    def _alert_refused(self, signal: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime, result: str) -> None:
        """Telegram when a fresh cross is not ordered because a check refused it.

        Each closed-candle cross is judged once, so this sends once per cross.
        It never changes the decision; a failure here is only logged.
        """
        try:
            ignored = result.startswith(f"{signal} ignored — ")
            no_reverse = result.startswith(f"closed on {signal} — ")
            if not (ignored or no_reverse):
                return
            want = "LONG" if signal == "BULLISH" else "SHORT"
            self._alert(
                refused_alert(
                    mode=(cfg.trading_mode or "PAPER").upper(),
                    symbol=(cfg.symbol or "").upper(),
                    signal=signal,
                    reason=result.split(" — ", 1)[1],
                    closed=no_reverse,
                    checks=entry_checks(frame, want, cfg),
                    price=_finite(frame.iloc[-2].get("close")),
                    when=now,
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("refused-cross alert failed")

    async def _apply_locked(
        self,
        *,
        signal: str,
        cross_price: float,
        atr: float,
        adx_blocks_entry: bool,
        cfg: BotConfig,
        now: dt.datetime,
        entry_block: str | None = None,
    ) -> str:
        want = "LONG" if signal == "BULLISH" else "SHORT"
        pos = self.position
        cutoff = _entry_cutoff(cfg)
        past_cutoff = now.time() >= cutoff
        cutoff_text = f"no new entries after {cutoff.strftime('%H:%M')} IST"

        if pos is not None and pos.signal_direction == want:
            return f"already {pos.direction}" + (" (flipped)" if pos.flipped else "")

        if pos is not None and pos.signal_direction != want and not cross_exits(cfg):
            # SMA cross exit is off: the cross neither closes nor reverses the trade.
            return (
                f"holding {pos.direction} — SMA cross exit is off; the stop, target, gap fade, "
                "Bollinger exit or square-off closes it"
            )

        if pos is not None and pos.signal_direction != want:
            # Opposite cross: cancel SL, verify, flatten, then maybe reverse.
            await self._cancel_sl_verified(pos)
            await self._close_position(pos, self._market_price(cfg.symbol, cross_price), "MA_CROSS", now, cfg)
            self.position = None
            if past_cutoff:
                return f"closed on {signal} — {cutoff_text}"
            if adx_blocks_entry:
                return f"closed on {signal} — ADX filter blocked the reverse"
            if entry_block:
                return f"closed on {signal} — {entry_block}"
            if self.trades_today >= int(cfg.max_trades_per_day):
                self._cap_the_day(f"max_trades_per_day ({cfg.max_trades_per_day}) reached")
                return "closed on cross — trade cap locks the day"
            await self._open(want, cross_price, atr, cfg, now)
            return f"reversed to {want}"

        # FLAT
        if past_cutoff:
            return f"{signal} ignored — {cutoff_text}"
        if adx_blocks_entry:
            return f"{signal} ignored — ADX below {cfg.adx_threshold}"
        if entry_block:
            return f"{signal} ignored — {entry_block}"
        if self.trades_today >= int(cfg.max_trades_per_day):
            self._cap_the_day(f"max_trades_per_day ({cfg.max_trades_per_day}) reached")
            return "entry blocked — trade cap"
        await self._open(want, cross_price, atr, cfg, now)
        return f"opened {want}"

    def hold_for_next_cross(self, symbols: list[str]) -> None:
        """Remember the closed bar already on the tape. Do not trade that cross."""
        for symbol in symbols:
            name = (symbol or "").upper()
            if not name:
                continue
            closed = _closed_bar_ts(self._frames.get(name))
            self._skip_cross_until[name] = closed
            if closed is not None:
                self._judged_bar[name] = closed

    def _cross_is_stale(self, symbol: str, frame: pd.DataFrame) -> bool:
        if symbol not in self._skip_cross_until:
            return False
        closed_ts = _closed_bar_ts(frame)
        anchor = self._skip_cross_until[symbol]
        if anchor is None:
            self._skip_cross_until[symbol] = closed_ts
            return True
        if closed_ts is None or closed_ts <= anchor:
            return True
        self._skip_cross_until.pop(symbol, None)
        return False

    async def force_order(self, symbol: str) -> str:
        """Buy or sell from the live SMA side, then leave the bot running.

        This does not wait for a cross and does not require the candle, or
        the session, to be closed. A cross already on the tape still cannot
        fire an extra order on this same bar.

        The entry filters (VWAP, volume, density, RSI, ADX) are not applied:
        the person pressing Force has decided. What a filter would have said
        is still logged with the decision. Market hours, the entry cut-off,
        the Trade list, the halt / loss lock and the trade cap still apply.
        """
        self.release_manual_panic()
        if self.status == "HALTED":
            raise ForceRefused(self.halt_reason or "Halted for the day")
        if self.status == "DAY_COMPLETED":
            raise ForceRefused(self.halt_reason or "Session already squared off")
        name = (symbol or "").upper().strip()
        if not name or not name.isalnum():
            raise ForceRefused("Choose a stock on the chart first")
        if name in self.positions:
            direction = self.positions[name].direction
            raise ForceRefused(f"{name} is already {direction}. Force does not add a second order.")
        cfg = self.load_config()
        clock = self._now()
        if not market_is_open(clock):
            raise ForceRefused("Market is closed. No new order until 09:20 IST.")
        if clock.time() >= _parse_hhmm(cfg.square_off_time):
            raise ForceRefused(f"Past {cfg.square_off_time} IST square-off. No new order.")
        if clock.time() >= _entry_cutoff(cfg):
            raise ForceRefused(f"No new entries after {_entry_cutoff(cfg).strftime('%H:%M')} IST.")
        if name not in trade_names(cfg):
            raise ForceRefused(f"{name} is not on the Trade list. Press Trade on {name} first.")
        frame = self._frames.get(name)
        if frame is None or getattr(frame, "empty", True):
            try:
                ltp, frame, source = await self.broker.refresh(name)
            except Exception as exc:  # noqa: BLE001
                raise ForceRefused(str(exc)) from exc
            if frame is None or getattr(frame, "empty", True):
                raise ForceRefused(f"{name} has no candles yet")
            self._frames[name] = frame
            self._ltps[name] = float(ltp)
            if name == (cfg.symbol or "").upper():
                self.ltp = float(ltp)
                self.last_error = ""
                self.data_source = source
        own = _cfg_for(cfg, name)
        enriched = enrich(frame, own.sma_fast, own.sma_slow, own.atr_period)
        side = _live_ma_side(enriched)
        if side is None:
            raise ForceRefused(f"{name} has no SMA yet")
        price = float(self._ltps.get(name) or enriched.iloc[-1]["close"] or 0)
        if price <= 0:
            raise ForceRefused(f"{name} has no price")
        self._ltps[name] = price
        # No cross here. The reference is the last closed candle, so the
        # report still shows how far the fill was from it.
        reference = _finite(enriched.iloc[-2]["close"]) if len(enriched) >= 2 else None
        reference = round_price(name, reference) if reference else price
        atr = _latest_atr(enriched)
        if atr is None:
            raise ForceRefused(f"{name} ATR is not ready")
        # Filters do not stop a Force order. Keep what they would have said
        # for the decision log, so a forced entry can be reviewed later.
        overridden = _entry_block(enriched, "LONG" if side == "BULLISH" else "SHORT", own, price=price)
        if self.status != "RUNNING":
            self.status = "RUNNING"
            self.halt_reason = ""
            # Other armed stocks keep waiting for a cross that prints after this start.
            self.hold_for_next_cross([item for item in trade_names(cfg) if item != name])
        # The bar we just looked at stays ignored, so this manual fill is not
        # reversed by a cross that was already printed.
        self.hold_for_next_cross([name])
        self._focus = name
        self.ltp = price
        now = self._now()
        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                raise ForceRefused("blocked — order PENDING/TRANSIT")
            self.inflight = "TRANSIT"
            try:
                result = await self._apply_locked(
                    signal=side,
                    cross_price=reference,
                    atr=atr,
                    adx_blocks_entry=False,
                    cfg=_cfg_for(cfg, name),
                    now=now,
                )
            except SlCancelFailed as exc:
                self.last_error = str(exc)
                self._order_refused(name, str(exc))
                raise ForceRefused(str(exc)) from exc
            finally:
                self.inflight = None
        self.last_signal = result
        self._signals[name] = result
        self._log_decision(
            side,
            enriched,
            _cfg_for(cfg, name),
            now,
            result,
            note="Force order. SMA side is read from the forming bar, then the last closed bar. Entry filters are not applied"
            + (f" (they would have refused: {overridden})." if overridden else "."),
        )
        return result

    async def close_symbol(self, symbol: str) -> str:
        """Close one open book. Does not stop the bot or touch the other stocks."""
        name = (symbol or "").upper().strip()
        if not name or name not in self.positions:
            raise ForceRefused(f"{name or 'That stock'} has no open position")
        cfg = self.load_config()
        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                raise ForceRefused("An order is already in progress")
            self.inflight = "TRANSIT"
            saved_focus, saved_ltp = self._focus, self.ltp
            self._focus = name
            try:
                pos = self.position
                if pos is None:
                    raise ForceRefused(f"{name} has no open position")
                try:
                    await self._cancel_sl_verified(pos)
                except SlCancelFailed as exc:
                    self.last_error = str(exc)
                    self._order_refused(name, str(exc))
                    raise ForceRefused(str(exc)) from exc
                if self.position is None:
                    return f"{name} was already flat"
                px = self._exit_price(name, self.position)
                try:
                    await self._close_position(
                        self.position, px, "MANUAL_CLOSE", self._now(), _cfg_for(cfg, name)
                    )
                except SlCancelFailed as exc:
                    self.last_error = str(exc)
                    self._order_refused(name, str(exc))
                    raise ForceRefused(str(exc)) from exc
                self.position = None
                text = f"{name} closed"
                self.last_signal = text
                self._signals[name] = text
                return text
            finally:
                self.inflight = None
                self._focus = saved_focus
                self.ltp = saved_ltp

    async def _read_order(self, order_id: str) -> tuple[str, float | None]:
        fn = getattr(self.broker, "read_order", None)
        try:
            if fn is not None:
                status, price = await fn(order_id)
                return (status or "").upper(), price
            status = await self.broker.get_order_status(order_id)
            return (status or "").upper(), None
        except Exception:  # noqa: BLE001
            return "UNKNOWN", None

    async def _rejection(self, order_id: str, status: str, said: str = "") -> SlCancelFailed:
        """The refusal with Groww's reason when Groww gave one.

        A bare "Groww REJECTED the order" left no way to tell margin from a
        blocked stock or a price band. The reason is read, never acted on.
        """
        reason = (said or "").strip()
        if not reason and order_id:
            fn = getattr(self.broker, "order_remark", None)
            if fn is not None:
                try:
                    reason = (await fn(order_id) or "").strip()
                except Exception:  # noqa: BLE001
                    reason = ""
        text = f"Groww {status} the order" + (f": {reason}" if reason else "") + ". Nothing was booked."
        logger.warning("order %s %s: %s", order_id or "-", status, reason or "no reason given")
        return SlCancelFailed(text)

    async def _require_live_fill(self, ack, cfg: BotConfig):
        """Return only when Groww has filled this order.

        A DAY limit that is still pending is cancelled. Leaving it working is
        how a batch of orders filled hours later, after this screen already
        showed them as done.
        """
        from groww_client import TERMINAL_CANCELLED, TERMINAL_FILLED

        status = (getattr(ack, "status", "") or "").upper()
        order_id = str(getattr(ack, "order_id", "") or "")
        if not order_id or order_id.startswith(("LIVE", "PAPER")):
            raise SlCancelFailed(getattr(ack, "message", "") or "Groww did not accept the order. Nothing was booked.")
        if status in TERMINAL_CANCELLED or status in {"REJECTED", "FAILED", "FAILURE"}:
            raise await self._rejection(order_id, status, getattr(ack, "message", "") or "")
        price = getattr(ack, "fill_price", None)
        if status not in TERMINAL_FILLED:
            for _ in range(6):
                await self._sleep(0.5)
                status, seen = await self._read_order(order_id)
                if seen:
                    price = seen
                if status in TERMINAL_FILLED:
                    break
                if status in TERMINAL_CANCELLED or status in {"REJECTED", "FAILED", "FAILURE"}:
                    raise await self._rejection(order_id, status)
        if status in TERMINAL_FILLED:
            ack.status = status
            if price:
                ack.fill_price = price
            return ack
        try:
            await self.broker.cancel_order(order_id)
        except Exception as exc:  # noqa: BLE001
            status, seen = await self._read_order(order_id)
            if status in TERMINAL_FILLED:
                ack.status = status
                if seen:
                    ack.fill_price = seen
                return ack
            raise SlCancelFailed(f"Could not cancel the unfilled Groww order: {exc}") from exc
        status, seen = await self._read_order(order_id)
        if status in TERMINAL_FILLED:
            ack.status = status
            if seen:
                ack.fill_price = seen
            return ack
        raise SlCancelFailed(
            "That order was still pending at Groww, so it was cancelled. It will not fill later."
        )

    def _book_not_on_groww(self, pos: OpenPosition, now: dt.datetime) -> None:
        """The screen had a position Groww does not. Remove it without an order.

        It was never a fill, so it gives its slot back to the daily cap.
        """
        costs = {"gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0}
        self._finalize_trade(pos.trade_id, pos.entry_price, "NOT_ON_GROWW", now, costs)
        self._uncount_trade(pos.trade_id)

    def _uncount_trade(self, trade_id: int) -> None:
        with session_factory()() as db:
            row = db.get(TradeLog, trade_id)
            day = row.date if row is not None else None
        if trade_id <= self._count_floor():
            return  # already left out of the count by a reset
        if day == self._session_date and self.trades_today > 0:
            self.trades_today -= 1

    async def _drop_positions_groww_does_not_hold(self, cfg: BotConfig) -> None:
        """A local open row whose Groww book is flat is not a live position.

        Sending an exit for it would open the other side. That is the order
        that showed up on Groww hours after this screen still looked fine.
        """
        if (cfg.trading_mode or "").upper() != "LIVE" or self.inflight:
            return
        now_m = time.monotonic()
        if now_m - getattr(self, "_phantom_check_at", 0.0) < 15:
            return
        self._phantom_check_at = now_m
        for symbol in list(self.positions):
            pos = self.positions.get(symbol)
            if pos is None or (pos.mode or "").upper() != "LIVE":
                continue
            opened = pos.entry_time
            if opened is not None and opened.tzinfo is None:
                opened = opened.replace(tzinfo=IST)
            if opened is not None and (self._now() - opened).total_seconds() < 20:
                continue
            net = await self._groww_net(symbol)
            if net != 0:
                continue
            self._focus = symbol
            self._book_not_on_groww(pos, self._now())
            self.position = None
            text = f"{symbol} is not on Groww. Removed it here. No order was sent."
            self.last_signal = text
            self._signals[symbol] = text

    async def _open(self, direction: str, cross_price: float, atr: float, cfg: BotConfig, now: dt.datetime) -> None:
        """Book a position only once the broker confirms the fill.

        A refused, failed, or cancelled entry raises before anything is
        booked or counted, so the stock stays FLAT and the daily trade cap
        is not used up. Shares Groww did fill are always booked, even when
        the stop that follows is refused, so a real position is never lost.

        `direction` is the signal's side. With the flip strategy on, the order
        goes the other way; the stop and target guard that real position.
        """
        flipped = flips_orders(cfg)
        if flipped:
            direction = _opposite(direction)
        side = "BUY" if direction == "LONG" else "SELL"
        live = (cfg.trading_mode or "").upper() == "LIVE"
        guard = self.live_guard
        if live and guard is not None:
            # Last check before real money: another LIVE bot may own this stock.
            why = guard(self.bot_id, [cfg.symbol])
            if why:
                raise SlCancelFailed(why)
        qty = int(cfg.qty)
        # The order goes out at the market price now. cross_price stays the
        # signal candle's close, so entry minus cross is the real fill lag.
        order_price = self._market_price(cfg.symbol, cross_price)
        if self.uses_wallet and (cfg.trading_mode or "PAPER").upper() == "PAPER":
            # Practice wallet: block the margin, borrowing any shortfall. Never stops the order.
            try:
                loan = paper_wallet.cover(cfg.symbol, qty, order_price, bot=self.bot_id)
            except Exception:  # noqa: BLE001
                logger.exception("paper wallet margin check failed for %s", cfg.symbol)
                loan = None
            if loan is not None:
                self.wallet_loan = loan
        try:
            ack = await self.broker.place_entry(cfg.symbol, side, qty, order_price)
        except Exception as exc:  # noqa: BLE001
            raise SlCancelFailed(str(exc) or "Groww did not accept the entry") from exc
        if live:
            try:
                ack = await self._require_live_fill(ack, cfg)
            except SlCancelFailed:
                # A cancelled order can still have part-filled. Groww's book
                # is the truth: book what it holds, otherwise stay FLAT.
                held = await self._held_after_failed_entry(cfg.symbol, direction, qty)
                if not held:
                    raise
                qty = held
        elif (ack.status or "").upper() in ("REJECTED", "FAILED", "FAILURE", "CANCELLED", "CANCELED"):
            raise SlCancelFailed(f"Entry rejected: {ack.message or ack.status}")
        fill = round_price(cfg.symbol, ack.fill_price or order_price)
        sl = self._sl_price(direction, fill, atr, float(cfg.atr_multiplier), cfg.symbol)
        trailing = uses_gap_stop(cfg, live)
        target = None
        if trailing:
            gap_now = self._closed_gap_pct(cfg.symbol, cfg)
            sl_gap, tp_gap = gap_levels(
                direction,
                fill,
                gap_now,
                float(getattr(cfg, "gap_sl_mult", 1.0) or 1.0),
                float(getattr(cfg, "gap_tp_mult", 2.0) or 2.0),
                float(getattr(cfg, "gap_min_pct", 0.2) or 0.0),
            )
            sl = round_price(cfg.symbol, sl_gap)
            target = round_price(cfg.symbol, tp_gap)
        tsl_points = tsl_step = None
        if uses_tsl(cfg):
            tsl_points, tsl_step, tgt_points = tsl_settings(cfg)
            sl_tsl, tp_tsl = tsl_entry_levels(direction, fill, tsl_points, tgt_points)
            sl = round_price(cfg.symbol, sl_tsl)
            target = round_price(cfg.symbol, tp_tsl) if tp_tsl is not None else None
        sl_side = "SELL" if direction == "LONG" else "BUY"
        sl_id = ""
        sl_error = ""
        stop_active = bool(getattr(cfg, "use_stop", True))
        if stop_active:
            try:
                sl_ack = await self.broker.place_sl(cfg.symbol, sl_side, qty, sl)
                sl_id = sl_ack.order_id
            except Exception as exc:  # noqa: BLE001
                sl_error = str(exc) or "Groww refused the stop"
        trade_id = self._insert_open_trade(
            cfg=cfg,
            direction=direction,
            fill=fill,
            cross_price=cross_price,
            atr=atr,
            sl=sl,
            now=now,
            qty=qty,
            stop_active=stop_active,
            flipped=flipped,
        )
        self.trades_today += 1
        self._rejects.pop((cfg.symbol or "").upper(), None)
        self._alert(
            fill_alert(
                mode=(cfg.trading_mode or "PAPER").upper(),
                direction=direction,
                symbol=cfg.symbol,
                qty=qty,
                fill=fill,
                stop=sl if stop_active else None,
                when=now,
                day=self._day_totals(cfg.symbol),
            )
        )
        self.position = OpenPosition(
            direction=direction,
            qty=qty,
            entry_price=fill,
            ma_cross_price=cross_price,
            atr_at_entry=atr,
            sl_trigger=sl,
            sl_order_id=sl_id,
            entry_order_id=ack.order_id,
            entry_time=now,
            trade_id=trade_id,
            mode=cfg.trading_mode,
            stop_active=stop_active,
            trailing=trailing,
            target=target,
            tsl_step=tsl_step,
            tsl_points=tsl_points,
            tsl_best=fill if tsl_step else None,
            high=fill,
            low=fill,
            flipped=flipped,
        )
        if trailing:
            closed_ts = _closed_bar_ts(self._frames.get((cfg.symbol or "").upper()))
            if closed_ts is not None:
                self._trail_bar[(cfg.symbol or "").upper()] = closed_ts
        if sl_error:
            await self._exit_unprotected(cfg, now, sl_error)

    async def _held_after_failed_entry(self, symbol: str, direction: str, qty: int) -> int:
        """Shares Groww holds after an entry this bot treated as failed."""
        net = await self._groww_net(symbol)
        if not net:
            return 0
        if (direction == "LONG" and net > 0) or (direction == "SHORT" and net < 0):
            return min(abs(int(net)), int(qty))
        return 0

    async def _exit_unprotected(self, cfg: BotConfig, now: dt.datetime, why: str) -> None:
        """The entry filled but its stop was refused. Do not hold it naked."""
        pos = self.position
        symbol = (cfg.symbol or "").upper()
        if pos is None:
            return
        try:
            await self._close_position(pos, self._ltps.get(symbol) or pos.entry_price, "SL_REJECTED", now, cfg)
        except SlCancelFailed as exc:
            self.last_error = f"Stop refused ({why}) and the exit failed: {exc}. Close {symbol} by hand."
            self._signals[symbol] = self.last_error
            self._alert(f"PalTra ALERT\n{symbol} has no stop. {self.last_error}")
            return
        self.position = None
        self.last_error = f"Stop refused ({why}). {symbol} was closed at once."
        self._signals[symbol] = f"{symbol} closed — stop was refused"

    def _closed_gap_pct(self, symbol: str, cfg: BotConfig) -> float | None:
        """SMA 9 vs SMA 21 gap % on this stock's last closed candle."""
        frame = self._frames.get((symbol or "").upper())
        if frame is None or getattr(frame, "empty", True) or len(frame) < 2:
            return None
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        closed = enriched.iloc[-2]
        return sma_gap_pct(closed.get("sma_9"), closed.get("sma_21"))

    def _trail_gap_levels(self, cfg: BotConfig) -> None:
        """Once per closed candle: move a practice stop (tighter only) and target."""
        pos = self.position
        if pos is None or not pos.trailing or not pos.stop_active:
            return
        if (pos.mode or "PAPER").upper() == "LIVE":
            return
        symbol = (cfg.symbol or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or self._trail_bar.get(symbol) == closed_ts:
            return
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        closed = enriched.iloc[-2]
        gap = sma_gap_pct(closed.get("sma_9"), closed.get("sma_21"))
        price = _finite(closed.get("close"))
        self._trail_bar[symbol] = closed_ts
        if price is None or price <= 0:
            return
        sl_new, tp_new = gap_levels(
            pos.direction,
            price,
            gap,
            float(getattr(cfg, "gap_sl_mult", 1.0) or 1.0),
            float(getattr(cfg, "gap_tp_mult", 2.0) or 2.0),
            float(getattr(cfg, "gap_min_pct", 0.2) or 0.0),
        )
        moved = round_price(symbol, tighten(pos.direction, pos.sl_trigger, sl_new))
        pos.target = round_price(symbol, tp_new)
        if moved != pos.sl_trigger:
            pos.sl_trigger = moved
            # Saved so a restart keeps the trailed stop, not the entry one.
            with session_factory()() as db:
                row = db.get(TradeLog, pos.trade_id)
                if row is not None and row.exit_time is None:
                    row.sl_trigger_price = moved
                    db.commit()

    async def _trail_tsl(self, cfg: BotConfig) -> None:
        """Every tick: move a TSL stop toward profit in whole ₹ steps.

        LIVE moves the exchange stop with Groww's modify, which keeps the
        position protected throughout. A refused modify leaves the old stop
        in place (still live) and is tried again on a later tick.
        """
        pos = self.position
        if pos is None or not pos.tsl_step or not pos.stop_active or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        best = pos.tsl_best if pos.tsl_best is not None else pos.entry_price
        best = max(best, self.ltp) if pos.direction == "LONG" else min(best, self.ltp)
        pos.tsl_best = best
        proposed = tsl_stop(pos.direction, pos.entry_price, float(pos.tsl_points or 0.0), pos.tsl_step, best)
        moved = round_price(symbol, tighten(pos.direction, pos.sl_trigger, proposed))
        if moved == pos.sl_trigger:
            return
        if (pos.mode or "PAPER").upper() == "LIVE":
            if not pos.sl_order_id:
                # Restored after a restart: the exchange stop id is unknown,
                # so it stays where it is.
                return
            if time.monotonic() - pos.tsl_modified_at < 2.0:
                return
            pos.tsl_modified_at = time.monotonic()
            modify = getattr(self.broker, "modify_sl", None)
            if modify is None:
                return
            side = "SELL" if pos.direction == "LONG" else "BUY"
            try:
                await modify(pos.sl_order_id, symbol, side, pos.qty, moved)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"Trailing stop not moved ({exc}). The stop at {pos.sl_trigger} is still live."
                return
        pos.sl_trigger = moved
        # Saved so a restart keeps the trailed stop, not the entry one.
        with session_factory()() as db:
            row = db.get(TradeLog, pos.trade_id)
            if row is not None and row.exit_time is None:
                row.sl_trigger_price = moved
                db.commit()

    def _sl_price(self, direction: str, entry: float, atr: float, mult: float, symbol: str = "") -> float:
        symbol = symbol or self._focus
        if direction == "LONG":
            return round_price(symbol, entry - mult * atr)
        return round_price(symbol, entry + mult * atr)

    async def _cancel_sl_verified(self, pos: OpenPosition) -> None:
        """Orphan-SL prevention: cancel, then confirm it is not still working."""
        if not pos.sl_order_id:
            return
        await self.broker.cancel_order(pos.sl_order_id)
        for _ in range(8):
            status = (await self.broker.get_order_status(pos.sl_order_id)).upper()
            if status in TERMINAL_CANCELLED or status == "":
                pos.sl_order_id = ""
                return
            if status in TERMINAL_FILLED:
                raise SlCancelFailed(
                    f"SL {pos.sl_order_id} already filled ({status}) — reverse blocked"
                )
            await self._sleep(0.05)
        raise SlCancelFailed(f"SL {pos.sl_order_id} cancel was not confirmed — reverse blocked")

    async def _groww_net(self, symbol: str) -> int | None:
        fn = getattr(self.broker, "net_quantity", None)
        if fn is None:
            return None
        try:
            return await fn(symbol)
        except Exception:  # noqa: BLE001
            return None

    async def _settle_exchange_flat(self, cfg: BotConfig, symbols: list[str]) -> None:
        """Book an open terminal trade once Groww's MIS book for it is flat.

        A restart forgets the in-memory position. The exchange stop can already
        have sold the shares. Leaving the row open is what kept the screen on
        LONG after Groww showed the stop filled.
        """
        if (cfg.trading_mode or "").upper() != "LIVE":
            return
        now_m = time.monotonic()
        if now_m - getattr(self, "_flat_check_at", 0.0) < 15:
            return
        self._flat_check_at = now_m
        for symbol in symbols:
            if symbol in self.positions:
                continue
            if await self._groww_net(symbol) != 0:
                continue
            with session_factory()() as db:
                # Only this bot's LIVE rows: Groww's net position says nothing
                # about a practice or research trade on the same stock.
                rows = (
                    db.query(TradeLog)
                    .filter(
                        TradeLog.exit_time.is_(None),
                        TradeLog.symbol == symbol,
                        TradeLog.mode == "LIVE",
                        self._bot_rows(),
                    )
                    .all()
                )
                pending = [
                    (
                        int(row.id),
                        float(row.sl_trigger_price or row.entry_price),
                        row.direction,
                        float(row.entry_price),
                        int(row.qty),
                    )
                    for row in rows
                ]
            for trade_id, _px, _direction, entry, _qty in pending:
                # Groww is flat and this process is not holding the book.
                # Close the row here. Do not send an order: that order would
                # open the other side.
                costs = {"gross_pnl": 0.0, "total_charges": 0.0, "net_pnl": 0.0}
                self._finalize_trade(trade_id, entry, "NOT_ON_GROWW", self._now(), costs)
                self._uncount_trade(trade_id)
                self.last_signal = "Not on Groww — removed here, no order sent"

    async def _watch_stop(self, cfg: BotConfig) -> None:
        pos = self.position
        if pos is None or self.status != "RUNNING":
            return
        # Entered with no stop: nothing to watch. Square-off and an opposite
        # cross still close the position. The position's own flag decides,
        # not today's checkbox, so toggling it does not invent a stop.
        if not pos.stop_active and not pos.sl_order_id:
            return
        hit = False
        fill_price = self.ltp
        symbol = (cfg.symbol or self._focus or "").upper()
        if (cfg.trading_mode or "").upper() == "LIVE" and not pos.sl_order_id:
            # Restored after a restart: the exchange still holds the stop id.
            # A flat book means that stop filled. Any other read must not exit.
            net = await self._groww_net(symbol)
            if net != 0:
                return
            hit = True
            fill_price = pos.sl_trigger or self.ltp
        elif (cfg.trading_mode or "").upper() == "LIVE" and pos.sl_order_id:
            status = (await self.broker.get_order_status(pos.sl_order_id)).upper()
            if status in TERMINAL_FILLED:
                hit = True
                # Exchange fill price if the adapter stored one; else the trigger.
                fill_price = pos.sl_trigger
            elif status not in IN_FLIGHT:
                # Groww reports a filled stop as EXECUTED. If that word was
                # missed, a flat MIS book is the same fact: the shares are gone.
                opened = pos.entry_time
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=IST)
                age = (self._now() - opened).total_seconds()
                if age >= 20 and await self._groww_net(cfg.symbol) == 0:
                    hit = True
                    fill_price = pos.sl_trigger or self.ltp
        elif pos.stop_active:
            if pos.direction == "LONG" and self.ltp <= pos.sl_trigger:
                hit = True
                fill_price = self.ltp
            elif pos.direction == "SHORT" and self.ltp >= pos.sl_trigger:
                hit = True
                fill_price = self.ltp
        reason = "GAP_SL_HIT" if pos.trailing else ("TSL_HIT" if pos.tsl_step else "ATR_SL_HIT")
        if not hit and pos.target is not None and self.ltp > 0:
            if (pos.direction == "LONG" and self.ltp >= pos.target) or (
                pos.direction == "SHORT" and self.ltp <= pos.target
            ):
                hit = True
                fill_price = self.ltp
                reason = "TARGET_HIT"
        if not hit:
            return
        await self._exit_now(cfg, fill_price, reason)

    async def _watch_bollinger(self, cfg: BotConfig) -> None:
        """Once per closed candle: exit on the Bollinger band (bb_exit)."""
        pos = self.position
        mode = (getattr(cfg, "bb_exit", None) or "OFF").upper()
        if pos is None or self.status != "RUNNING" or mode == "OFF" or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.bb_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart: the exchange stop id is unknown, so it
            # cannot be cancelled first. An exit now could leave that stop to
            # fill later and open a reverse. The stop and crosses still close it.
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        if closed_ts + 60 <= opened.timestamp():
            # That candle closed before the entry (it is the cross candle).
            return
        pos.bb_bar = closed_ts
        closes = frame["close"].iloc[:-1]
        reason, note, pos.bb_armed = bollinger_exit(
            closes,
            pos.signal_direction,
            mode,
            int(getattr(cfg, "bb_period", 20) or 20),
            float(getattr(cfg, "bb_std", 2.0) or 2.0),
            pos.bb_armed,
        )
        if reason is None:
            return
        self._signals[symbol] = f"{symbol} Bollinger exit — {note}"
        await self._exit_now(cfg, self.ltp, reason)

    async def _exit_now(self, cfg: BotConfig, fill_price: float, reason: str) -> None:
        """Close the focused position for a stop, target or Bollinger exit."""
        async with self.lock:
            if self.position is None or self.inflight:
                return
            self.inflight = "TRANSIT"
            try:
                if reason in _TAKEN_EXITS and self.position.sl_order_id:
                    # The stop is still working on Groww. Cancel it first so
                    # it cannot fill after the exit and open a reverse.
                    try:
                        await self._cancel_sl_verified(self.position)
                    except SlCancelFailed:
                        # It filled as we cancelled: the stop closed the trade.
                        reason = "TSL_HIT" if self.position.tsl_step else "ATR_SL_HIT"
                        fill_price = self.position.sl_trigger or fill_price
                # SL already fired (or paper touch). Do not place a reverse.
                self.position.sl_order_id = ""
                await self._close_position(self.position, fill_price, reason, self._now(), cfg)
                self.position = None
                self.last_signal = {
                    "TARGET_HIT": "target hit — flat",
                    "GAP_SL_HIT": "moving stop hit — flat",
                    "TSL_HIT": "trailing stop hit — flat",
                    "BB_TARGET": "Bollinger band target — flat",
                    "BB_MIDDLE": "Bollinger middle band exit — flat",
                    "GAP_FADE": "SMA gap faded — flat",
                    "CANDLE_END": "candle closed — flat",
                }.get(reason, "ATR stop hit — flat")
            finally:
                self.inflight = None
        if self._loss_breached(cfg):
            await self._stop_for_loss(cfg)

    async def _square_off(self, reason: str, symbols: list[str] | None = None) -> None:
        async with self.lock:
            if self.inflight:
                return
            self.inflight = "TRANSIT"
            saved_focus, saved_ltp = self._focus, self.ltp
            try:
                cfg = self.load_config()
                for symbol in list(self.positions):
                    if symbols is not None and symbol not in symbols:
                        continue
                    self._focus = symbol
                    if self.position is None:
                        continue
                    try:
                        await self._cancel_sl_verified(self.position)
                    except SlCancelFailed:
                        # SL may have filled as we tried to cancel — book whatever
                        # position is left at LTP if we still have one.
                        pass
                    if self.position is None:
                        continue
                    px = self._exit_price(symbol, self.position)
                    self.ltp = px
                    try:
                        await self._close_position(
                            self.position, px, reason, self._now(), _cfg_for(cfg, symbol)
                        )
                    except SlCancelFailed as exc:
                        self.last_error = str(exc)
                        self.last_signal = f"blocked — {exc}"
                        self._signals[symbol] = self.last_signal
                        self._order_refused(symbol, str(exc))
                        if _intraday_is_shut(str(exc)):
                            self.status = "DAY_COMPLETED"
                            self.halt_reason = str(exc)
                            self._announce_down("DAY_COMPLETED", self.halt_reason)
                            return
                        continue
                    self.position = None
            finally:
                self.inflight = None
                self._focus, self.ltp = saved_focus, saved_ltp

    async def kill(self, reason: str) -> None:
        self.halt_reason = reason
        self.status = "HALTED"
        await self._square_off("KILL_SWITCH")
        self._announce_down("HALTED", reason)

    async def _close_position(
        self,
        pos: OpenPosition,
        exit_price: float,
        reason: str,
        now: dt.datetime,
        cfg: BotConfig,
    ) -> None:
        exit_side = "SELL" if pos.direction == "LONG" else "BUY"
        px = round_price(cfg.symbol, float(exit_price))
        live = (cfg.trading_mode or "").upper() == "LIVE"
        # LIVE ATR hits are filled by the exchange SL; don't send a second exit.
        send_exit = not (live and reason in ("ATR_SL_HIT", "TSL_HIT"))
        if send_exit and live:
            net = await self._groww_net(cfg.symbol)
            if net is None:
                raise SlCancelFailed(
                    f"Could not read {cfg.symbol} on Groww, so no exit was sent"
                )
            if (pos.direction == "LONG" and net <= 0) or (pos.direction == "SHORT" and net >= 0):
                if net == 0:
                    self._book_not_on_groww(pos, now)
                    return
                raise SlCancelFailed(
                    f"Groww's {cfg.symbol} position does not match this {pos.direction}. No order was sent."
                )
            qty = min(pos.qty, abs(int(net)))
            try:
                ack = await self.broker.place_exit(cfg.symbol, exit_side, qty, px)
            except Exception as exc:  # noqa: BLE001
                raise SlCancelFailed(str(exc) or "Groww did not accept the exit") from exc
            try:
                ack = await self._require_live_fill(ack, cfg)
            except SlCancelFailed:
                # The exit may have filled while it was being cancelled.
                net_after = await self._groww_net(cfg.symbol)
                if net_after != 0:
                    raise
                ack = None
            if ack is not None and ack.fill_price:
                px = round_price(cfg.symbol, ack.fill_price)
        elif send_exit:
            ack = await self.broker.place_exit(cfg.symbol, exit_side, pos.qty, px)
            if ack.fill_price:
                px = round_price(cfg.symbol, ack.fill_price)
        buy, sell = legs_for(pos.direction, pos.entry_price, px)
        costs = calculate_charges(buy, sell, pos.qty)
        self._finalize_trade(pos.trade_id, px, reason, now, costs, extremes=(pos.high, pos.low))

    def _insert_open_trade(
        self,
        *,
        cfg: BotConfig,
        direction: str,
        fill: float,
        cross_price: float,
        atr: float,
        sl: float,
        now: dt.datetime,
        qty: int | None = None,
        stop_active: bool = True,
        flipped: bool = False,
    ) -> int:
        mode = (cfg.trading_mode or "PAPER").upper()
        run_id = getattr(self, "run_id", None)
        with session_factory()() as db:
            # Next number in this trade's own book (PAPER, LIVE, or this replay run).
            book = db.query(func.max(TradeLog.book_seq)).filter(TradeLog.mode == mode, self._bot_rows())
            book = book.filter(TradeLog.run_id == run_id) if run_id is not None else book.filter(TradeLog.run_id.is_(None))
            seq = int(book.scalar() or 0) + 1
            row = TradeLog(
                date=now.date().isoformat(),
                symbol=cfg.symbol,
                direction=direction,
                qty=int(qty if qty is not None else cfg.qty),
                entry_time=now.replace(tzinfo=None),
                entry_price=fill,
                ma_cross_price=cross_price,
                atr_at_entry=atr,
                sl_trigger_price=sl,
                mode=mode,
                stop_active=bool(stop_active),
                flipped=bool(flipped),
                bot=self.bot_id,
                run_id=run_id,
                book_seq=seq,
                strategy=json.dumps({**settings_snapshot(cfg), "qty": int(qty if qty is not None else cfg.qty)}),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id)

    def _finalize_trade(
        self,
        trade_id: int,
        exit_price: float,
        reason: str,
        now: dt.datetime,
        costs: dict,
        extremes: tuple[float | None, float | None] = (None, None),
    ) -> None:
        with session_factory()() as db:
            row = db.get(TradeLog, trade_id)
            if row is None:
                return
            row.exit_time = now.replace(tzinfo=None)
            row.exit_price = exit_price
            # The entry and exit fills bound the range even when no tick was seen.
            seen = [p for p in (*extremes, row.entry_price, exit_price) if p is not None and p > 0]
            if seen:
                row.max_high = max(seen)
                row.max_low = min(seen)
            row.exit_reason = reason
            row.gross_pnl = costs["gross_pnl"]
            row.brokerage_and_taxes = costs["total_charges"]
            row.net_pnl = costs["net_pnl"]
            symbol = row.symbol
            direction = row.direction
            db.commit()
        self._trades_cache = None
        self._refresh_realized()
        # The note must not keep saying "holding" once the book is flat.
        name = (symbol or "").upper()
        if name:
            self._signals[name] = f"{name} flat — {_ALERT_REASON.get(reason, reason or 'closed')}"
        self._alert(
            close_alert(
                direction=direction,
                symbol=symbol,
                exit_price=exit_price,
                reason=reason,
                gross=float(costs.get("gross_pnl") or 0),
                net=float(costs.get("net_pnl") or 0),
                when=now,
                day=self._day_totals(symbol),
            )
        )

    def _refresh_realized(self, mode: str | None = None) -> float:
        """Realized net = the KPI net: today's closed rows of the current book.

        A running sum drifted from the KPI: it restarted at zero after a
        reboot, mixed PAPER and LIVE closes, and counted by exit day.
        """
        if mode is None:
            cfg = self._cfg_cache
            mode = (cfg.trading_mode if cfg is not None else None) or "PAPER"
        self._realized_mode = mode.upper()
        self.realized_net = float(self._kpis(self._realized_mode)["net"])
        return self.realized_net

    def _day_open_and_change(self) -> tuple[float | None, float | None]:
        """Percent versus the previous session's last close.

        A one-bar stub whose open equals the last trade is not a flat day.
        That happens when candle history has not arrived yet.
        """
        frame = self.candles
        if frame is None or getattr(frame, "empty", True) or self.ltp <= 0:
            return None, None
        today = self._now().date()
        today_start = int(dt.datetime(today.year, today.month, today.day, tzinfo=IST).timestamp())
        ts = frame["ts"]
        if not ts.is_monotonic_increasing:
            frame = frame.sort_values("ts")
            ts = frame["ts"]
        # Binary search. Walking every bar here runs on the websocket thread
        # and was leaving the desk unable to answer for the whole scan.
        idx = int(ts.searchsorted(today_start, side="left"))
        n = len(frame)
        prev_close = float(frame["close"].iloc[idx - 1]) if idx > 0 else None
        if idx < n:
            day_open = float(frame["open"].iloc[idx])
        else:
            day_open = float(frame["open"].iloc[0]) if n else None
        baseline = prev_close if prev_close and prev_close > 0 else day_open
        if baseline is None or baseline <= 0:
            return day_open, None
        # The stub bar published before candles arrive uses the last trade as
        # its open. That is not a 0% day.
        if prev_close is None and len(frame) <= 2 and abs(baseline - self.ltp) < 0.02:
            return day_open, None
        return day_open, (self.ltp - baseline) / baseline * 100

    def _loss_breached(self, cfg: BotConfig) -> bool:
        net = self.realized_net
        view = self._focus
        ltp = self.ltp
        for symbol in list(self.positions):
            self._focus = symbol
            self.ltp = self._ltps.get(symbol, ltp)
            unreal = self._unrealized()
            if unreal:
                net += unreal["net"]
        self._focus = view
        self.ltp = ltp
        return net <= -abs(float(cfg.max_daily_loss))

    def _warn_upcoming(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> None:
        """Telegram before an entry or a close. The order itself waits for the cross."""
        pos = self.positions.get(symbol)
        mode = (cfg.trading_mode or "PAPER").upper()
        side, minutes, gap_pct = minutes_until_cross(frame)
        if side is None:
            self._gate_warning((symbol, "cross", "BULLISH"), None, "")
            self._gate_warning((symbol, "cross", "BEARISH"), None, "")
        else:
            other = "BEARISH" if side == "BULLISH" else "BULLISH"
            self._gate_warning((symbol, "cross", other), None, "")
            # With the SMA cross exit off a cross never closes the trade, so no heads-up.
            closes = pos is not None and cross_exits(cfg) and (
                (pos.signal_direction == "LONG" and side == "BEARISH")
                or (pos.signal_direction == "SHORT" and side == "BULLISH")
            )
            if pos is None and now.time() >= _entry_cutoff(cfg):
                # No entry can follow, so no heads-up.
                text = ""
                minutes = None
            elif pos is None:
                text = upcoming_entry_alert(
                    mode=mode,
                    symbol=symbol,
                    side=side,
                    minutes=minutes or 0,
                    gap_pct=gap_pct or 0,
                    checks=entry_checks(frame, "LONG" if side == "BULLISH" else "SHORT", cfg),
                    trades=(self.trades_today, int(cfg.max_trades_per_day)),
                )
            elif closes:
                text = upcoming_close_alert(
                    mode=mode,
                    symbol=symbol,
                    position=pos.direction,
                    side=side,
                    minutes=minutes or 0,
                    gap_pct=gap_pct or 0,
                    checks=entry_checks(frame, "LONG" if side == "BULLISH" else "SHORT", cfg),
                )
            else:
                text = ""
                minutes = None
            self._gate_warning((symbol, "cross", side), minutes, text)

        if pos is None:
            self._gate_warning((symbol, "stop"), None, "")
            self._gate_warning((symbol, "squareoff"), None, "")
            return
        atr = _latest_atr(frame) or float(pos.atr_at_entry or 0)
        ltp = float(self._ltps.get(symbol) or self.ltp or 0)
        stop_in = minutes_until_stop(pos.direction, ltp, float(pos.sl_trigger), atr) if pos.stop_active else None
        self._gate_warning(
            (symbol, "stop"),
            stop_in,
            upcoming_stop_alert(
                symbol=symbol,
                direction=pos.direction,
                minutes=stop_in or 0,
                ltp=ltp,
                stop=float(pos.sl_trigger),
            ),
        )
        if not market_is_open(now) and self.data_source != "GROWW":
            self._gate_warning((symbol, "squareoff"), None, "")
            return
        ahead = minutes_until_clock(now, cfg.square_off_time)
        self._gate_warning(
            (symbol, "squareoff"),
            ahead,
            upcoming_square_off_alert(
                symbol=symbol,
                direction=pos.direction,
                minutes=ahead or 0,
                clock=cfg.square_off_time,
            ),
        )

    def _gate_warning(self, key: tuple, minutes: float | None, message: str) -> None:
        """Send once while the event is inside 3 minutes. Arm again after it moves past 5."""
        if minutes is not None and 0 < minutes <= _WARN_MINUTES and message:
            if key in self._warned:
                return
            self._warned.add(key)
            self._alert(message)
            return
        if minutes is None or minutes > _WARN_CLEAR_MINUTES:
            self._warned.discard(key)

    async def _stop_for_loss(self, cfg: BotConfig) -> None:
        reason = f"max_daily_loss ₹{cfg.max_daily_loss:.0f} breached"
        if self._practice_off_session(cfg):
            await self._square_off("MAX_DAILY_LOSS")
            self.status = "STOPPED"
            self.halt_reason = ""
            self.last_signal = reason
            self._announce_down("STOPPED", reason)
            return
        await self.kill(reason)

    def _practice_off_session(self, cfg: BotConfig | None = None) -> bool:
        """Paper tape outside the cash session. It must not lock the live morning."""
        if cfg is None:
            cfg = self._cfg_cache
        mode = (cfg.trading_mode if cfg is not None else "PAPER") or "PAPER"
        return mode.upper() != "LIVE" and not market_is_open(self._now())

    def _cap_the_day(self, reason: str) -> None:
        if self._practice_off_session():
            self.status = "STOPPED"
            self.halt_reason = ""
            self.last_signal = reason
            return
        self.status = "HALTED"
        self.halt_reason = reason
        self._announce_down("HALTED", reason)

    def _order_refused(self, symbol: str, text: str) -> None:
        """Keep Groww's refusal for the screen and send it to Telegram.

        The same refusal for the same stock alerts at most once in 15
        minutes, so an exit retried every tick does not flood the chat.
        Text only: nothing here sends or changes an order.
        """
        name = (symbol or "").upper()
        text = (text or "").strip()
        if not name or not text:
            return
        now = self._now()
        self._rejects[name] = (text, now)
        last = self._reject_sent.get((name, text))
        if last is not None and (now - last) < dt.timedelta(minutes=15):
            return
        self._reject_sent[(name, text)] = now
        cfg = self._cfg_cache
        mode = ((cfg.trading_mode if cfg is not None else None) or "PAPER").upper()
        self._alert(order_refused_alert(mode=mode, symbol=name, text=text, when=now))

    def _note_broker_block(self, exc: SlCancelFailed, symbol: str = "") -> None:
        self.last_error = str(exc)
        self.last_signal = f"blocked — {exc}"
        key = symbol or self._focus
        if key:
            self._signals[key] = self.last_signal
            self._order_refused(key, str(exc))
        if _intraday_is_shut(str(exc)) and self.status != "DAY_COMPLETED":
            self.status = "DAY_COMPLETED"
            self.halt_reason = str(exc)
            self._announce_down("DAY_COMPLETED", self.halt_reason)

    def _announce_down(self, status: str, reason: str) -> None:
        self._alert(bot_down_alert(status, reason, self._now()))

    async def announce_shutdown(self) -> None:
        """Tell Telegram before the process exits. A kill signal must not stay silent."""
        if self.status not in ("RUNNING", "PAUSED") and not self.positions:
            return
        reason = "The app process is stopping. Press Start bot after it comes back."
        if self.halt_reason:
            reason = f"{self.halt_reason}. {reason}"
        message = bot_down_alert(self.status, reason, self._now())
        try:
            from app.services.alert_notifier import alert_notifier

            await asyncio.wait_for(alert_notifier.send(message), timeout=5)
        except Exception:  # noqa: BLE001
            return

    def release_manual_panic(self) -> bool:
        """A panic flattens the book. Pressing Start again may trade the same day.

        A loss-limit halt stays locked. A trade-cap halt is released only when
        the saved cap is now above the trades already taken.
        """
        if self.status != "HALTED":
            return False
        if (self.halt_reason or "") != "Manual PANIC SQUARE-OFF":
            return False
        self.status = "STOPPED"
        self.halt_reason = ""
        return True

    def release_trade_cap(self, new_max: int | None = None) -> bool:
        """Resume after a raised daily cap. Open positions are left alone.

        A loss-limit halt is not this reason, so it stays locked.
        """
        if self.status != "HALTED":
            return False
        if not (self.halt_reason or "").startswith("max_trades_per_day"):
            return False
        cfg = self._cfg_cache
        try:
            cfg = self.load_config()
        except Exception:  # noqa: BLE001
            pass
        cap = int(new_max if new_max is not None else (cfg.max_trades_per_day if cfg else 0))
        if self.trades_today >= cap:
            self.halt_reason = f"max_trades_per_day ({cap}) reached"
            return False
        self.status = "STOPPED"
        self.halt_reason = ""
        self.last_signal = f"Trade cap raised to {cap}. Press Start. Open positions stay open."
        return True

    def _count_floor(self) -> int:
        """Trades up to this id do not count today: the user reset the count."""
        cfg = self._cfg_cache
        if cfg is None or getattr(cfg, "trade_count_reset_date", None) != self._session_date:
            return 0
        return int(getattr(cfg, "trade_count_reset_id", 0) or 0)

    def reset_trade_count(self) -> int:
        """Set today's trade count back to 0. Today's trades, P&L and the loss limit stay.

        Kept on this engine's settings row, so a restart does not count the
        earlier trades again. A trade-cap halt is lifted (press Start); a
        loss-limit or panic halt stays locked.
        """
        self._roll_session(self._now())
        self.load_config()  # the research desk creates its settings row here if it is new
        with session_factory()() as db:
            top = db.query(func.max(TradeLog.id)).scalar() or 0
            row = db.get(BotConfig, self.config_id)
            if row is None:
                raise RuntimeError("BotConfig missing")
            row.trade_count_reset_id = int(top)
            row.trade_count_reset_date = self._session_date
            db.commit()
        self.load_config()
        before = self.trades_today
        self.trades_today = 0
        if self.status == "HALTED" and (self.halt_reason or "").startswith("max_trades_per_day"):
            self.status = "STOPPED"
            self.halt_reason = ""
        self.last_signal = f"Trade count reset ({before} → 0). Press Start if the bot is stopped."
        return before

    def restore_trades_today(self) -> int:
        """A restart must not forget how many entries this session already took."""
        cfg = self._cfg_cache
        try:
            cfg = self.load_config()
        except Exception:  # noqa: BLE001
            pass
        mode = ((cfg.trading_mode if cfg is not None else None) or "PAPER").upper()
        day = self._session_date
        floor = self._count_floor()
        with session_factory()() as db:
            rows = (
                db.query(TradeLog.mode, TradeLog.exit_reason)
                .filter(TradeLog.date == day, TradeLog.id > floor, self._bot_rows())
                .all()
            )
        # A row Groww never held was not a trade, so it does not use the cap.
        self.trades_today = sum(
            1
            for row_mode, reason in rows
            if (row_mode or "PAPER").upper() == mode and reason != "NOT_ON_GROWW"
        )
        # A restart must not forget today's realized P&L either.
        self._refresh_realized(mode)
        return self.trades_today

    def release_paper_halt(self) -> bool:
        """Drop a practice halt so confirming live is not blocked by the simulator."""
        if self.status not in ("HALTED", "DAY_COMPLETED"):
            return False
        cfg = self._cfg_cache
        try:
            cfg = self.load_config()
        except Exception:  # noqa: BLE001
            pass
        mode = (cfg.trading_mode if cfg is not None else "PAPER") or "PAPER"
        if mode.upper() == "LIVE":
            return False
        self.status = "STOPPED"
        self.halt_reason = ""
        self.trades_today = 0
        return True

    def _past_square_off(self, now: dt.datetime, cfg: BotConfig) -> bool:
        """True once the day's session is over for an open position.

        LIVE: from square-off time while Groww still takes orders (MIS is
        flattened by the broker after that). PAPER: from square-off time
        until the next open, weekends included, so a practice book is
        never carried overnight.
        """
        if (cfg.trading_mode or "").upper() == "LIVE":
            if not market_is_open(now) and self.data_source != "GROWW":
                return False
            return now.time() >= _parse_hhmm(cfg.square_off_time)
        if now.weekday() >= 5:
            return True
        t = now.time()
        return t >= _parse_hhmm(cfg.square_off_time) or t < dt.time(9, 15)

    async def _close_finished_paper_books(self, now: dt.datetime, cfg: BotConfig) -> None:
        """Square off practice positions at the end of their session.

        Runs even when the bot is paused or stopped. A position opened on
        an earlier day (for example, held across a restart) closes at once.
        """
        if (cfg.trading_mode or "").upper() == "LIVE" or not self.positions:
            return
        today = now.astimezone(IST).date() if now.tzinfo else now.date()
        if self._past_square_off(now, cfg):
            due = [s for s, p in self.positions.items() if p is not None and (p.mode or "PAPER").upper() != "LIVE"]
        else:
            due = [
                s
                for s, p in self.positions.items()
                if p is not None and (p.mode or "PAPER").upper() != "LIVE" and _entry_day(p) < today
            ]
        if due:
            await self._square_off("EOD_SQUARE_OFF", symbols=due)

    def _open_net(self, symbol: str) -> float | None:
        """Unrealized net of one held stock at its last price. None when flat."""
        unreal = self._open_pnl(symbol)
        return float(unreal["net"]) if unreal else None

    def _open_pnl(self, symbol: str) -> dict | None:
        """Unrealized gross, charges and net of one held stock at its last price. None when flat."""
        name = (symbol or "").upper()
        if name not in self.positions:
            return None
        view, ltp = self._focus, self.ltp
        try:
            self._focus = name
            self.ltp = float(self._ltps.get(name) or 0)
            return self._unrealized()
        finally:
            self._focus, self.ltp = view, ltp

    def _day_totals(self, exclude: str = "") -> dict:
        """Today's closed net and trade count of this book, plus the open P&L of
        every other held stock. For alert text only.
        """
        try:
            kpis = self._kpis(self._realized_mode or "PAPER")
            skip = (exclude or "").upper()
            open_net = 0.0
            held = 0
            for name in list(self.positions):
                if name == skip:
                    continue
                value = self._open_net(name)
                if value is not None:
                    open_net += value
                    held += 1
            return {"net": float(kpis["net"]), "trades": int(kpis["trades"]), "open_net": open_net, "held": held}
        except Exception:  # noqa: BLE001
            logger.exception("day totals for alert failed")
            return {}

    def _unrealized(self) -> dict | None:
        pos = self.position
        if pos is None or self.ltp <= 0:
            return None
        if pos.direction == "LONG":
            buy, sell = pos.entry_price, self.ltp
        else:
            buy, sell = self.ltp, pos.entry_price
        costs = calculate_charges(buy, sell, pos.qty)
        # Distance to the stop, signed so a positive number means "room left".
        # No stop, no distance.
        room = None
        room_pct = None
        if pos.stop_active:
            if pos.direction == "LONG":
                room = self.ltp - pos.sl_trigger
            else:
                room = pos.sl_trigger - self.ltp
            room_pct = (room / self.ltp * 100) if self.ltp else 0.0
        return {
            "gross": costs["gross_pnl"],
            "charges": costs["total_charges"],
            "net": costs["net_pnl"],
            "breakdown": costs,
            "room": room,
            "room_pct": room_pct,
        }

    def snapshot(self) -> dict:
        # Serve the config the loop already loaded. A sqlite read on this
        # request path blocks every other page while the file is busy.
        cfg = self._cfg_cache
        if cfg is None:
            try:
                cfg = self.load_config()
            except Exception:  # noqa: BLE001
                cfg = None
        view = (cfg.symbol if cfg else self._focus or "").upper()
        view_cfg = _cfg_for(cfg, view) if cfg is not None else None
        saved_focus, saved_ltp = self._focus, self.ltp
        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]
        try:
            unreal = self._unrealized()
            pos = self.position
            day_open, day_change = self._day_open_and_change()
            armed_names = trade_names(cfg) if cfg else []
            shown = list(dict.fromkeys([*armed_names, *self.positions.keys()]))
            kpis = self._kpis((cfg.trading_mode if cfg else "PAPER") or "PAPER")
            closed_by = kpis.get("by_symbol", {})
            books = []
            open_total = 0.0
            open_gross_total = 0.0
            for symbol in shown:
                book = self.positions.get(symbol)
                unreal_one = self._open_pnl(symbol)
                open_net = float(unreal_one["net"]) if unreal_one else None
                open_gross = float(unreal_one["gross"]) if unreal_one else None
                open_total += open_net or 0.0
                open_gross_total += open_gross or 0.0
                closed = closed_by.get(symbol, {"net": 0.0, "gross": 0.0, "trades": 0})
                books.append(
                    {
                        "symbol": symbol,
                        "direction": book.direction if book else "FLAT",
                        "qty": book.qty if book else 0,
                        "entry_price": book.entry_price if book else None,
                        "sl_trigger": book.sl_trigger if book and book.stop_active else None,
                        "stop_active": book.stop_active if book else None,
                        "target": book.target if book else None,
                        "trailing": book.trailing if book else None,
                        "tsl_step": book.tsl_step if book else None,
                        "ltp": self._ltps.get(symbol),
                        "note": _book_note(symbol, book, self._signals.get(symbol, "")),
                        # Today's P&L of this stock, whichever stock the chart shows.
                        "open_net": open_net,
                        "closed_net": closed["net"],
                        "closed_trades": closed["trades"],
                        "day_net": closed["net"] + (open_net or 0.0),
                        # The same before charges (display only; limits use the net).
                        "open_gross": open_gross,
                        "closed_gross": closed.get("gross", 0.0),
                        "day_gross": closed.get("gross", 0.0) + (open_gross or 0.0),
                        # Groww's last refusal on this stock today, until an order fills.
                        "last_reject": self._rejects[symbol][0] if symbol in self._rejects else None,
                        "last_reject_at": self._rejects[symbol][1].strftime("%H:%M")
                        if symbol in self._rejects
                        else None,
                    }
                )
        finally:
            self._focus, self.ltp = saved_focus, saved_ltp
        return {
            "bot": self.bot_id,
            "bot_name": (getattr(cfg, "bot_name", None) if cfg else None) or f"Bot {self.bot_id}",
            "bot_status": self.status,
            "halt_reason": self.halt_reason,
            "mode": (cfg.trading_mode if cfg else "PAPER"),
            "data_source": self.data_source,
            "second_ticks": tick_store.enabled(),
            "last_error": self.last_error,
            "last_signal": self.last_signal,
            "symbol": cfg.symbol if cfg else "",
            "trade_symbols": trade_names(cfg) if cfg else [],
            "books": books,
            "stop_enabled": True if view_cfg is None or view_cfg.use_stop is None else bool(view_cfg.use_stop),
            "atr_multiplier": float(view_cfg.atr_multiplier) if view_cfg else 1.5,
            "stop_type": (getattr(view_cfg, "stop_type", None) or "ATR") if view_cfg else "ATR",
            "flip_orders": flips_orders(view_cfg) if view_cfg is not None else False,
            "cross_exit": cross_exits(view_cfg) if view_cfg is not None else True,
            # The last loan the practice wallet took for this bot's entry (the screens pop it up once).
            "wallet_loan": getattr(self, "wallet_loan", None),
            # Stocks with their own strategy settings, and which fields they set.
            "stock_settings": stock_settings(cfg) if cfg else {},
            "exchange": cfg.exchange if cfg else "NSE",
            "ltp": self.ltp,
            "day_open": day_open,
            "day_change_pct": day_change,
            "sma9": self.sma9,
            "sma21": self.sma21,
            "sma_gap": self.sma_gap,
            "sma_gap_pct": self.sma_gap_pct,
            "vwap": self.vwap,
            "minute_volume": self.minute_volume,
            "avg_minute_volume": self.avg_minute_volume,
            "volume_ratio": self.volume_ratio,
            "rsi14": self.rsi14,
            "atr14": self.atr14,
            "adx14": self.adx14,
            "position": None
            if pos is None
            else {
                "direction": pos.direction,
                "flipped": pos.flipped,
                "qty": pos.qty,
                "entry_price": pos.entry_price,
                "ma_cross_price": pos.ma_cross_price,
                "atr_at_entry": pos.atr_at_entry,
                "sl_trigger": pos.sl_trigger if pos.stop_active else None,
                "sl_order_id": pos.sl_order_id,
                "entry_time": pos.entry_time.isoformat(),
                "mode": pos.mode,
                "stop_active": pos.stop_active,
                "trailing": pos.trailing,
                "target": pos.target,
                "tsl_step": pos.tsl_step,
                "tsl_points": pos.tsl_points,
            },
            "active_sl_trigger": pos.sl_trigger if pos and pos.stop_active else None,
            "unrealized_gross_pnl": unreal["gross"] if unreal else 0.0,
            "estimated_charges": unreal["charges"] if unreal else 0.0,
            "unrealized_net_pnl": unreal["net"] if unreal else 0.0,
            # Every held stock, not only the one on the chart.
            "open_net_total": open_total,
            "open_gross_total": open_gross_total,
            "sl_room": unreal["room"] if unreal else None,
            "sl_room_pct": unreal["room_pct"] if unreal else None,
            "charge_estimate": unreal["breakdown"] if unreal else None,
            # Same number as kpis.net, by construction.
            "realized_net_pnl": kpis["net"],
            "trades_today": self.trades_today,
            "max_trades": cfg.max_trades_per_day if cfg else 40,
            "max_daily_loss": cfg.max_daily_loss if cfg else 5000,
            "kpis": kpis,
            "connected": self.data_source != "ERROR",
        }

    def _marker_book(self, cfg: BotConfig | None):
        """Chart markers come from this engine's own book only.

        Practice, real and replay trades share one table. Without this, every
        replay of the same day drew its entries again on the chart.
        """
        mode = ((cfg.trading_mode if cfg else None) or "PAPER").upper()
        if mode == "PAPER":
            return or_(TradeLog.mode == "PAPER", TradeLog.mode.is_(None))
        return TradeLog.mode == mode

    def _chart_parts(self, frame: pd.DataFrame, cfg, symbol: str = "") -> tuple[list[dict], list[dict]]:
        """Closed candle rows and refused crosses, reused until a candle closes.

        Both depend only on closed candles (and the filter settings), so the
        page's polls between two closes reuse one computation. Re-scanning
        every cross on every poll held the server for seconds during a
        replay, and /api/state timed out behind it.
        """
        closed = frame.iloc[-2]
        key = (
            len(frame),
            int(frame["ts"].iloc[0]),
            int(closed["ts"]),
            float(closed["close"]),
            float(closed.get("volume", 0) or 0),
            tuple(getattr(cfg, name, None) for name in _FILTER_KEYS) if cfg is not None else None,
        )
        # One entry per stock: a second tab on another stock keeps its own.
        caches = getattr(self, "_chart_cache", None)
        if not isinstance(caches, dict):
            caches = self._chart_cache = {}
        cached = caches.get(symbol)
        if cached is not None and cached[0] == key:
            return cached[1], cached[2]
        rows = candle_rows(frame)[:-1]
        blocks = filter_blocks(frame, cfg)
        if symbol not in caches and len(caches) >= 12:
            caches.pop(next(iter(caches)))
        caches[symbol] = (key, rows, blocks)
        return rows, blocks

    def _other_frame(self, symbol: str, frame: pd.DataFrame, cfg) -> pd.DataFrame:
        """Another stock's enriched candles for its own chart tab, redone only when its tape moves."""
        key = (len(frame), int(frame["ts"].iloc[-1]), float(frame["close"].iloc[-1]), int(cfg.sma_fast), int(cfg.sma_slow), int(cfg.atr_period))
        caches = getattr(self, "_other_frames", None)
        if caches is None:
            caches = self._other_frames = {}
        hit = caches.get(symbol)
        if hit is not None and hit[0] == key:
            return hit[1]
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        if symbol not in caches and len(caches) >= 12:
            caches.pop(next(iter(caches)))
        caches[symbol] = (key, enriched)
        return enriched

    def chart_payload(self, limit: int = 240, symbol: str | None = None) -> dict:
        """The chart's candles, markers and levels.

        `symbol` draws another watched stock without moving the chart focus
        (a second browser tab on one stock of a replay). An unknown stock gives
        an empty chart.
        """
        frame = self.candles
        markers = []
        cfg = self._cfg_cache
        if cfg is None:
            try:
                cfg = self.load_config()
            except Exception:  # noqa: BLE001
                cfg = None
        focus = ((cfg.symbol if cfg else None) or self._focus or "").upper()
        other = (symbol or "").upper().strip()
        if other == focus:
            other = ""
        if other:
            raw = self._frames.get(other)
            frame = pd.DataFrame() if raw is None else raw
        if cfg is not None:
            # The chart's stock is judged with its own settings.
            cfg = settings_for(cfg, other or (cfg.symbol or "").upper())
            if other and frame is not None and not frame.empty:
                frame = self._other_frame(other, frame, cfg)
        candles: list[dict] = []
        all_blocks: list[dict] = []
        if frame is not None and not frame.empty:
            if len(frame) >= 2:
                # VWAP and RSI need the whole session, so compute before trimming.
                closed_rows, all_blocks = self._chart_parts(frame, cfg, other or focus)
                candles = [*closed_rows, _forming_row(frame.iloc[-1])][-limit:]
            else:
                candles = [_forming_row(frame.iloc[-1])]
        first_shown = candles[0]["time"] if candles else None
        blocked = [mark for mark in all_blocks if first_shown is not None and mark["time"] >= first_shown]
        view = other or (cfg.symbol if cfg else self._focus or "").upper()
        with session_factory()() as db:
            query = db.query(TradeLog).filter(self._marker_book(cfg), self._bot_rows())
            if view:
                query = query.filter(func.upper(TradeLog.symbol) == view)
            rows = (
                query
                .order_by(TradeLog.id.desc())
                .limit(40)
                .all()
            )
        for row in reversed(rows):
            if view and (row.symbol or "").upper() != view:
                continue
            net = row.net_pnl if row.net_pnl is not None else row.gross_pnl
            ref = trade_ref(row.mode, row.run_id, row.book_seq, row.id, getattr(row, "bot", None))
            if row.entry_time is not None:
                markers.append(
                    {
                        "time": _epoch_ist(row.entry_time),
                        "direction": row.direction,
                        "price": row.entry_price,
                        "kind": "ENTRY",
                        "net_pnl": net,
                        "gross_pnl": row.gross_pnl,
                        "trade_ref": ref,
                        "open": row.exit_time is None,
                    }
                )
            if row.exit_time is not None and row.exit_price is not None:
                markers.append(
                    {
                        "time": _epoch_ist(row.exit_time),
                        "direction": row.direction,
                        "price": row.exit_price,
                        "kind": "EXIT",
                        "net_pnl": net,
                        "gross_pnl": row.gross_pnl,
                        "reason": row.exit_reason,
                        "trade_ref": ref,
                        "open": False,
                    }
                )
        pos = self.positions.get(other) if other else self.position
        return {
            "symbol": view,
            "candles": candles,
            "markers": markers,
            "entry_price": pos.entry_price if pos else None,
            "sl_trigger": pos.sl_trigger if pos and pos.stop_active else None,
            "target": pos.target if pos else None,
            "trailing": bool(pos.trailing) if pos else False,
            "tsl_step": pos.tsl_step if pos else None,
            "atr_multiplier": float(cfg.atr_multiplier) if cfg else 1.5,
            "blocked": blocked,
            "filters": chart_filters(cfg),
        }

    def _kpis(self, mode: str = "PAPER") -> dict:
        day = self._session_date
        book = (mode or "PAPER").upper()
        with session_factory()() as db:
            rows = (
                db.query(TradeLog)
                .filter(TradeLog.date == day, TradeLog.exit_price.isnot(None), self._bot_rows())
                .all()
            )
        rows = [row for row in rows if (row.mode or "PAPER").upper() == book]
        # A replay counts only its own run, not earlier replays of that date.
        floor_id = int(getattr(self, "_min_trade_id", 0) or 0)
        if floor_id:
            rows = [row for row in rows if int(row.id) > floor_id]
        theoretical = 0.0
        actual = 0.0
        charges = 0.0
        net = 0.0
        wins = 0
        gross_wins = 0
        by_symbol: dict[str, dict] = {}
        breakdown = {
            "brokerage": 0.0,
            "stt": 0.0,
            "exchange_charge": 0.0,
            "sebi_fee": 0.0,
            "stamp_duty": 0.0,
            "gst": 0.0,
        }
        for row in rows:
            sign_exit = float(row.exit_price or 0)
            if row.direction == "LONG":
                theoretical += (sign_exit - float(row.ma_cross_price)) * row.qty
            else:
                theoretical += (float(row.ma_cross_price) - sign_exit) * row.qty
            actual += float(row.gross_pnl or 0)
            charges += float(row.brokerage_and_taxes or 0)
            net += float(row.net_pnl or 0)
            if (row.net_pnl or 0) > 0:
                wins += 1
            if (row.gross_pnl or 0) > 0:
                gross_wins += 1
            per = by_symbol.setdefault((row.symbol or "").upper(), {"net": 0.0, "gross": 0.0, "trades": 0})
            per["net"] += float(row.net_pnl or 0)
            per["gross"] += float(row.gross_pnl or 0)
            per["trades"] += 1
            buy, sell = legs_for(row.direction, row.entry_price, sign_exit)
            part = calculate_charges(buy, sell, row.qty)
            for key in breakdown:
                breakdown[key] += part[key]
        n = len(rows)
        return {
            "theoretical_gross": theoretical,
            "actual_gross": actual,
            "total_charges": charges,
            "charge_breakdown": breakdown,
            "net": net,
            "win_rate": (wins / n * 100) if n else 0.0,
            "trades": n,
            "wins": wins,
            # Wins counted before charges, for screens that show P&L before charges.
            "gross_wins": gross_wins,
            "by_symbol": by_symbol,
        }

    def book(self, mode: str | None, limit: int = BOOK_LIMIT, bot: int | None = None) -> dict:
        """One book's newest trades (all books when mode is None), up to `limit`.

        `total` is how many the book holds, so the page can say when it shows
        only the newest part of it.
        """
        limit = max(1, min(int(limit), BOOK_LIMIT))
        table = TradeLog.__table__
        where = _mode_filter(mode) if mode is not None else true()
        if bot is not None:
            where = and_(where, bot_rows(bot))
        with session_factory()() as db:
            total = db.execute(select(func.count()).select_from(table).where(where)).scalar_one()
            # Plain rows, not ORM objects: about 3x faster for thousands of trades.
            rows = db.execute(select(table).where(where).order_by(table.c.id.desc()).limit(limit)).all()
        parsed: dict[str, dict | None] = {}
        return {"total": total, "rows": [_trade_dict(r, parsed) for r in rows]}

    def book_counts(self, bot: int | None = None) -> dict[str, int]:
        """How many trades each book holds; with `bot`, that bot's PAPER and LIVE books."""
        with session_factory()() as db:
            out = {}
            for mode in BOOKS:
                query = db.query(TradeLog).filter(_mode_filter(mode))
                if bot is not None and mode in ("PAPER", "LIVE"):
                    query = query.filter(bot_rows(bot))
                out[mode] = query.count()
            return out

    def trades(self) -> list[dict]:
        now = time.monotonic()
        cached = getattr(self, "_trades_cache", None)
        if cached is not None and now - cached[0] < 8:
            return self.with_open_extremes(cached[1])
        with session_factory()() as db:
            rows = db.query(TradeLog).order_by(TradeLog.id.desc()).limit(200).all()
        payload = [_trade_dict(r) for r in rows]
        self._trades_cache = (now, payload)
        return self.with_open_extremes(payload)

    def with_open_extremes(self, rows: list[dict]) -> list[dict]:
        """Open trades show the high and low so far; they are saved at the close."""
        live = {pos.trade_id: pos for pos in self.positions.values()}
        if not live:
            return rows
        out = []
        for row in rows:
            pos = live.get(row.get("id")) if row.get("exit_price") is None else None
            if pos is not None and pos.high is not None:
                row = {**row, "max_high": pos.high, "max_low": pos.low}
            out.append(row)
        return out


_WARN_MINUTES = 3
_WARN_CLEAR_MINUTES = 5

_ALERT_REASON = {
    "MA_CROSS": "MA cross",
    "MA_APPROACH": "sold before the MA cross",
    "ATR_SL_HIT": "ATR stop",
    "GAP_SL_HIT": "moving stop (SMA gap)",
    "TSL_HIT": "trailing stop",
    "TARGET_HIT": "target hit",
    "BB_TARGET": "Bollinger band target",
    "BB_MIDDLE": "Bollinger middle band",
    "GAP_FADE": "SMA gap faded",
    "CANDLE_END": "end of the pattern candle",
    "REPLAY_STOPPED": "replay stopped",
    "EOD_SQUARE_OFF": "square-off",
    "KILL_SWITCH": "panic square-off",
    "NOT_ON_GROWW": "not on Groww — no order sent",
    "SL_REJECTED": "stop refused — closed at once",
    "MANUAL_CLOSE": "closed from the screen",
}


def bot_down_alert(status: str, reason: str, when: dt.datetime) -> str:
    clock = when.strftime("%d %b %H:%M:%S")
    why = (reason or "no reason recorded").strip()
    return f"PalTra bot {status}\n{why}\n{clock} IST"


def fill_alert(
    *,
    mode: str,
    direction: str,
    symbol: str,
    qty: int,
    fill: float,
    stop: float | None,
    when: dt.datetime,
    day: dict | None = None,
) -> str:
    """WhatsApp text for a fill. No account numbers or order ids."""
    clock = when.strftime("%d %b %H:%M:%S")
    stop_text = f"Stop {stop:,.2f}" if stop is not None else "Stop OFF — no stop order"
    return (
        f"PalTra Order placed\n"
        f"{mode} {direction} {symbol}\n"
        f"Filled {qty} @ {fill:,.2f}\n"
        f"{stop_text}\n"
        f"{_day_text(day)}"
        f"{clock} IST"
    )


def _day_text(day: dict | None) -> str:
    """Today's running total for an alert, or nothing when it is unknown."""
    if not day:
        return ""
    trades = int(day.get("trades") or 0)
    text = f"Today net {float(day.get('net') or 0):+,.2f} ({trades} closed trade{'' if trades == 1 else 's'})"
    held = int(day.get("held") or 0)
    if held:
        open_net = float(day.get("open_net") or 0)
        total = float(day.get("net") or 0) + open_net
        text += f"\nOpen {held} other stock{'' if held == 1 else 's'} {open_net:+,.2f} · total {total:+,.2f}"
    return text + "\n"


def close_alert(
    *,
    direction: str,
    symbol: str,
    exit_price: float,
    reason: str,
    gross: float,
    net: float,
    when: dt.datetime,
    day: dict | None = None,
) -> str:
    clock = when.strftime("%d %b %H:%M:%S")
    why = _ALERT_REASON.get(reason, reason or "closed")
    return (
        f"PalTra closed {direction} {symbol}\n"
        f"Exit {exit_price:,.2f} · {why}\n"
        f"This trade P&L {gross:+,.2f}  net {net:+,.2f}\n"
        f"{_day_text(day)}"
        f"{clock} IST"
    )


def order_refused_alert(*, mode: str, symbol: str, text: str, when: dt.datetime) -> str:
    """An order Groww (or the pre-checks before Groww) refused, in its own words."""
    clock = when.strftime("%d %b %H:%M:%S")
    return f"PalTra order refused\n{mode} {symbol}\n{text}\n{clock} IST"


def refused_alert(
    *,
    mode: str,
    symbol: str,
    signal: str,
    reason: str,
    closed: bool,
    checks: list[str],
    price: float | None,
    when: dt.datetime,
) -> str:
    """A cross the bot saw but did not order on, and why."""
    clock = when.strftime("%d %b %H:%M:%S")
    order = "BUY" if signal == "BULLISH" else "SELL"
    head = f"{symbol} closed on the {signal} cross, no reverse {order}" if closed else f"{symbol} {signal} cross — no {order} placed"
    at = f" at {price:,.2f}" if price else ""
    lines = [f"PalTra no order", head + at, f"Why: {reason}"]
    if checks:
        lines.append("Checks on the cross candle:")
        lines.extend(checks)
    lines.append(f"{mode} · {clock} IST")
    return "\n".join(lines)


def minutes_until_cross(frame: pd.DataFrame, lookback: int = 3) -> tuple[str | None, float | None, float | None]:
    """How soon SMA 9 will cross SMA 21 if the last closed bars keep their pace.

    The gap on each candle is a percent of that candle's SMA 21, so a ₹120
    stock and a ₹1,160 stock share one scale. The forming bar is ignored.
    Returns ("BULLISH" or "BEARISH", minutes, gap percent), or (None, None, None)
    when the averages are moving apart or are not ready.
    """
    if frame is None or len(frame) < lookback + 1:
        return None, None, None
    closed = frame.iloc[-(lookback + 1) : -1]
    gaps: list[float] = []
    for _, row in closed.iterrows():
        gap = sma_gap_pct(row.get("sma_9"), row.get("sma_21"))
        if gap is None:
            return None, None, None
        gaps.append(gap)
    if len(gaps) < 2:
        return None, None, None
    gap = gaps[-1]
    slope = (gaps[-1] - gaps[0]) / (len(gaps) - 1)
    if gap < 0 and slope > 0:
        return "BULLISH", abs(gap) / slope, gap
    if gap > 0 and slope < 0:
        return "BEARISH", gap / abs(slope), gap
    return None, None, None


def minutes_until_stop(direction: str, ltp: float, stop: float, atr: float) -> float | None:
    """Minutes to the stop if price walks toward it at about one ATR per minute."""
    if atr is None or atr <= 0 or ltp <= 0:
        return None
    if direction == "LONG":
        room = ltp - stop
    elif direction == "SHORT":
        room = stop - ltp
    else:
        return None
    if room <= 0:
        return None
    return room / atr


def minutes_until_clock(now: dt.datetime, hhmm: str) -> float | None:
    target = _parse_hhmm(hhmm)
    due = now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    minutes = (due - now).total_seconds() / 60
    if minutes <= 0:
        return None
    return minutes


def _about(minutes: float) -> int:
    return max(1, int(round(minutes)))


def _gap_text(gap_pct: float) -> str:
    return f"{abs(gap_pct):.2f}% from SMA 21"


def _checks_text(checks: list[str] | None) -> str:
    if not checks:
        return ""
    return "Checks now (re-checked on the cross candle):\n" + "\n".join(checks) + "\n"


def upcoming_entry_alert(
    *,
    mode: str,
    symbol: str,
    side: str,
    minutes: float,
    gap_pct: float,
    checks: list[str] | None = None,
    trades: tuple[int, int] | None = None,
) -> str:
    order = "BUY" if side == "BULLISH" else "SELL"
    refused = any(line.startswith("❌") for line in checks or [])
    verdict = ""
    if checks:
        verdict = (
            f"If the cross printed now: no {order} — a check refuses it.\n"
            if refused
            else f"If the cross printed now: {order} would be placed.\n"
        )
    cap = f"Trades today {trades[0]}/{trades[1]}\n" if trades else ""
    return (
        f"PalTra heads-up\n"
        f"{symbol} may be ordered in about {_about(minutes)} min\n"
        f"SMA 9 is {_gap_text(gap_pct)}. A {order} would be placed.\n"
        f"{_checks_text(checks)}"
        f"{verdict}"
        f"{cap}"
        f"{mode} · no order yet"
    )


def upcoming_close_alert(
    *,
    mode: str,
    symbol: str,
    position: str,
    side: str,
    minutes: float,
    gap_pct: float,
    checks: list[str] | None = None,
) -> str:
    order = "BUY" if side == "BULLISH" else "SELL"
    reverse = ("Reverse entry — " + _checks_text(checks)) if checks else ""
    return (
        f"PalTra heads-up\n"
        f"{position} {symbol} may close in about {_about(minutes)} min\n"
        f"SMA 9 is {_gap_text(gap_pct)}. A {order} would follow.\n"
        f"{reverse}"
        f"{mode} · no close yet"
    )


def upcoming_stop_alert(*, symbol: str, direction: str, minutes: float, ltp: float, stop: float) -> str:
    return (
        f"PalTra heads-up\n"
        f"{direction} {symbol} may hit its stop in about {_about(minutes)} min\n"
        f"Price {ltp:,.2f} · stop {stop:,.2f}\n"
        f"No close yet"
    )


def upcoming_square_off_alert(*, symbol: str, direction: str, minutes: float, clock: str) -> str:
    return (
        f"PalTra heads-up\n"
        f"{direction} {symbol} square-off in about {_about(minutes)} min\n"
        f"Closes at {clock} IST\n"
        f"No close yet"
    )


def _schedule_whatsapp(message: str) -> None:
    """Send after the order is booked. A failed alert must not change the fill."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _send() -> None:
        try:
            from app.services.alert_notifier import alert_notifier

            await alert_notifier.send(message)
        except Exception:  # noqa: BLE001
            return

    loop.create_task(_send())


def _book_note(symbol: str, book: OpenPosition | None, note: str) -> str:
    """Drop a note that contradicts the book, such as "holding" while FLAT."""
    if book is None and "holding" in (note or ""):
        return f"{symbol} flat"
    if book is not None and " flat" in (note or ""):
        return f"{symbol} holding {book.direction}"
    return note


def _entry_cutoff(cfg: BotConfig) -> dt.time:
    """Last minute a new position may open. Never after the square-off."""
    try:
        cutoff = _parse_hhmm(getattr(cfg, "entry_cutoff_time", None) or "15:00")
    except (TypeError, ValueError):
        cutoff = dt.time(15, 0)
    try:
        square = _parse_hhmm(getattr(cfg, "square_off_time", None) or "15:15")
    except (TypeError, ValueError):
        square = dt.time(15, 15)
    return min(cutoff, square)


def _entry_day(pos: OpenPosition) -> dt.date:
    when = pos.entry_time
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return when.astimezone(IST).date()


def _candle_is_behind(closed_ts: int, now: dt.datetime) -> bool:
    """True when the frame is missing the bar that should already be closed."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    now_minute = int(now.replace(second=0, microsecond=0).timestamp())
    return closed_ts < now_minute - 60


# Exits the bot sends itself while an exchange stop may still be working.
_TAKEN_EXITS = ("TARGET_HIT", "BB_TARGET", "BB_MIDDLE", "GAP_FADE", "CANDLE_END")


def _filter_note(cfg: BotConfig) -> str:
    """Say which entry checks are on. Unchecked checks are not read."""
    checked: list[str] = []
    if bool(getattr(cfg, "use_vwap", False)):
        checked.append("VWAP")
    if bool(getattr(cfg, "use_volume", False)):
        checked.append("volume")
    if bool(getattr(cfg, "use_density", False)):
        checked.append("density")
    if bool(getattr(cfg, "use_rsi", False)):
        checked.append("RSI")
    if bool(getattr(cfg, "use_bollinger", False)):
        checked.append("Bollinger")
    if bool(getattr(cfg, "use_gap_long", False)) or bool(getattr(cfg, "use_gap_short", False)):
        checked.append("SMA gap")
    if bool(getattr(cfg, "use_candle_dir", False)):
        checked.append("candle direction")
    if bool(getattr(cfg, "use_adx_filter", False)):
        checked.append("ADX")
    if not checked:
        return "VWAP, volume, density, RSI, Bollinger, SMA gap and candle direction are off."
    return "Checked: " + ", ".join(checked) + "."


def _sma_side_text(frame: pd.DataFrame) -> str:
    if frame is None or getattr(frame, "empty", True) or len(frame) < 2:
        return ""
    row = frame.iloc[-2]
    fast, slow = row.get("sma_9"), row.get("sma_21")
    if pd.isna(fast) or pd.isna(slow):
        return ""
    if float(fast) > float(slow):
        return "SMA 9 is above SMA 21"
    if float(slow) > float(fast):
        return "SMA 9 is below SMA 21"
    return "SMA 9 is equal to SMA 21"


def _signal_on_unjudged_bars(frame: pd.DataFrame, judged_ts: int | None) -> tuple[str | None, pd.DataFrame | None]:
    """Newest unjudged cross on the last two closed bars.

    The newest closed bar is the normal signal. The bar before it is included
    only after this symbol has already been judged once, so a candle that
    arrives a minute late can still order, and a cross from before the bot
    was running cannot.
    """
    if frame is None or len(frame) < 3:
        return None, None
    newest_ts = _closed_bar_ts(frame)
    choices: list[tuple[str, int, pd.DataFrame]] = []
    if judged_ts is not None and len(frame) >= 4:
        older = frame.iloc[:-1].reset_index(drop=True)
        older_signal = closed_candle_cross(older)
        older_ts = _closed_bar_ts(older)
        if older_signal and older_ts is not None and older_ts > judged_ts:
            choices.append((older_signal, older_ts, older))
    newest_signal = closed_candle_cross(frame)
    if newest_signal and newest_ts is not None and (judged_ts is None or newest_ts > judged_ts):
        choices.append((newest_signal, newest_ts, frame))
    if not choices:
        return None, None
    signal, _ts, signal_frame = choices[-1]
    return signal, signal_frame


def _closed_bar_ts(frame: pd.DataFrame | None) -> int | None:
    if frame is None or getattr(frame, "empty", True) or len(frame) < 2 or "ts" not in frame.columns:
        return None
    try:
        return int(frame.iloc[-2]["ts"])
    except (TypeError, ValueError, KeyError):
        return None


def _live_ma_side(frame: pd.DataFrame) -> str | None:
    """SMA 9 versus SMA 21 on the forming bar, then the last closed bar."""
    if frame is None or getattr(frame, "empty", True):
        return None
    for idx in (-1, -2):
        if len(frame) < abs(idx):
            continue
        row = frame.iloc[idx]
        fast, slow = row.get("sma_9"), row.get("sma_21")
        if pd.isna(fast) or pd.isna(slow) or fast == slow:
            continue
        return "BULLISH" if fast > slow else "BEARISH"
    return None


def _latest_atr(frame: pd.DataFrame) -> float | None:
    for idx in (-1, -2):
        if len(frame) < abs(idx):
            continue
        atr = _finite(frame.iloc[idx].get("atr_14"))
        if atr is not None and atr > 0:
            return atr
    return None


def mark_to_market(direction: str, entry: float, market: float | None, qty: int) -> tuple[float, float] | None:
    """Signed points and rupee P&L of a fill against a market (or exit) price."""
    if market is None:
        return None
    try:
        entry_f = float(entry)
        market_f = float(market)
        qty_i = int(qty)
    except (TypeError, ValueError):
        return None
    if not (entry_f == entry_f and market_f == market_f):
        return None
    side = (direction or "").upper()
    if side == "LONG":
        points = market_f - entry_f
    elif side == "SHORT":
        points = entry_f - market_f
    else:
        return None
    return points, points * qty_i


def attach_market_prices(rows: list[dict], ltps: dict[str, float]) -> list[dict]:
    """Copy trade rows and add the price used to mark them.

    A closed row is marked at its exit fill. An open row uses the latest
    quote for that symbol. Stored gross and net stay untouched.
    """
    stamped: list[dict] = []
    for row in rows:
        item = dict(row)
        exit_px = item.get("exit_price")
        market: float | None
        try:
            if exit_px is not None and float(exit_px) > 0:
                market = float(exit_px)
            else:
                raw = ltps.get(str(item.get("symbol") or "").upper())
                market = float(raw) if raw else None
        except (TypeError, ValueError):
            market = None
        item["market_price"] = market
        marked = mark_to_market(str(item.get("direction") or ""), item.get("entry_price"), market, item.get("qty") or 0)
        item["mark_pnl"] = None if marked is None else round(marked[1], 2)
        if item.get("points") is None and marked is not None:
            item["points"] = round(marked[0], 4)
        stamped.append(item)
    return stamped


def _trade_dict(row: TradeLog, parsed: dict[str, dict | None] | None = None) -> dict:
    points = None
    if row.exit_price is not None:
        raw = float(row.exit_price) - float(row.entry_price)
        points = raw if row.direction == "LONG" else -raw
    return {
        "id": row.id,
        "date": row.date,
        "symbol": row.symbol,
        "direction": row.direction,
        "flipped": bool(getattr(row, "flipped", False)),
        "qty": row.qty,
        "entry_time": row.entry_time.isoformat() if row.entry_time else None,
        "entry_price": row.entry_price,
        "ma_cross_price": row.ma_cross_price,
        "fill_lag_points": _fill_lag(row.direction, row.entry_price, row.ma_cross_price),
        "atr_at_entry": row.atr_at_entry,
        "sl_trigger_price": row.sl_trigger_price if row.stop_active is not False else None,
        "stop_active": row.stop_active is not False,
        "exit_time": row.exit_time.isoformat() if row.exit_time else None,
        "exit_price": row.exit_price,
        "exit_reason": row.exit_reason,
        "gross_pnl": row.gross_pnl,
        "brokerage_and_taxes": row.brokerage_and_taxes,
        "net_pnl": row.net_pnl,
        "points": points,
        "mode": row.mode,
        "run_id": getattr(row, "run_id", None),
        "book_seq": getattr(row, "book_seq", None),
        "trade_ref": trade_ref(
            row.mode, getattr(row, "run_id", None), getattr(row, "book_seq", None), row.id, getattr(row, "bot", None)
        ),
        "bot": int(getattr(row, "bot", None) or 1),
        "strategy": _strategy_of(row, parsed),
        "max_high": getattr(row, "max_high", None),
        "max_low": getattr(row, "max_low", None),
    }


def _fill_lag(direction: str, entry: float | None, cross: float | None) -> float | None:
    """Points paid versus the cross price. Positive means a worse fill."""
    if entry is None or cross is None:
        return None
    lag = float(entry) - float(cross)
    return round(lag if (direction or "").upper() == "LONG" else -lag, 4)


def _epoch_ist(when: dt.datetime) -> int:
    """A saved trade time (naive = IST) as epoch seconds."""
    return int(when.replace(tzinfo=IST).timestamp()) if when.tzinfo is None else int(when.timestamp())


def _finite(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
