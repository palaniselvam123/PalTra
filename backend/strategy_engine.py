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
import re
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
    closed_candle_cross_triple,
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
from candle_patterns import closes_bucket, colour_call, pattern_call, per_candle, setting as pattern_setting, uses_colour, uses_patterns
from candles import bucket_start, candle_minutes, resample
from gap_mode import Pending, fade_confirmed, judge_exit, judge_pending, uses_gap_mode
from gap_trail import gap_levels, tighten, uses_gap_stop
from tsl import tsl_entry_levels, tsl_settings, tsl_stop, uses_tsl
from models import BotConfig, ReviewLog, TradeLog, trade_ref
import counter_candles
import ema_filter
import exit_candle
import supertrend
import opening_range
import join_trend
import late_entry
import tall_pullback
import extreme_entry
import entry_candle
import slope_exit
import turn_entry
import level_check
import day_extreme
import recovery
import pivots
import breakout
import review
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
    """SMA cross exit (cross_exit, on unless set off): an opposite cross closes the trade.

    Off for the bot's own candle while a different exit candle is set (exit_candle.py):
    that candle's cross closes the trade instead (`_watch_exit_candle`).
    """
    if getattr(cfg, "cross_exit", None) is False:
        return False
    return not exit_candle.active(cfg, candle_minutes(cfg))


# Why a trade opened, saved on TradeLog.entry_reason. A fill from a waiting limit adds
# ":PULLBACK" (tall candle / breakout level) or ":EXTREME_LIMIT" (extreme_entry.py).
ENTRY_REASONS = (
    "SMA_CROSS",
    "GAP_MODE",
    "JOIN_TREND",
    "LATE_ENTRY",
    "EARLY_REVERSAL",
    "COUNTER_REENTRY",
    "CANDLE_PATTERN",
    "CANDLE_COLOUR",
    "BREAKOUT_ARRIVAL",
    "FORCE_ORDER",
    "NEWS",
    "HANDOFF",
    "SMA9_TURN",
)


@dataclass
class PullbackOrder:
    """A tall signal candle's entry waiting for the price to pull back (tall_pullback.py)."""

    direction: str  # the signal's side: LONG | SHORT
    limit: float
    cross_price: float  # the signal candle's close
    atr: float
    signal_ts: int  # start of the signal candle
    span_sec: int  # candle length
    candles: int  # closed candles to wait after the signal candle
    # Candle-extreme limit entry (extreme_entry.py); a tall-candle pullback leaves these unset.
    side: str | None = None  # the real order's side when it differs (a flipped stock)
    invalid_at: float | None = None  # dropped once the price breaks beyond this
    sma_side: int = 0  # SMA fast vs slow on the signal candle (+1 above, -1 below)
    cancel_on_cross: bool = False  # dropped once the SMAs turn against the signal
    # Entry-candle colour check (entry_candle.py): the level follows each forming candle's open.
    confirm: bool = False
    confirm_pct: float = 0.0
    note: str = "tall candle"
    why: str = "SMA_CROSS"  # entry reason of the signal that armed it (ENTRY_REASONS)


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
    # Supertrend stop (supertrend.py, stop_type SUPERTREND): the stop follows the line, tighter only;
    # st_stop_bar is the last closed candle read.
    st_stop: bool = False
    st_stop_bar: int | None = None
    # Bollinger exit: the last closed candle read, and whether a close has
    # been on the trade's side of the middle band (arms the MIDDLE exit).
    bb_bar: int | None = None
    bb_armed: bool = False
    # Counter-candle exit (counter_candles.py): the last closed candle read.
    cc_bar: int | None = None
    # EMA cross exit (ema_filter.py): the last closed candle read.
    ema_bar: int | None = None
    # Last exit candle read by the mixed-candle exit (exit_candle.py), memory only.
    xc_bar: int | None = None
    # Supertrend exit (supertrend.py): the last closed candle read.
    st_bar: int | None = None
    # SMA 9 slope exit (slope_exit.py): the last closed candle read and the steepest slope.
    slope_bar: int | None = None
    slope_state: dict = field(default_factory=dict)
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
    # Recovery hold (recovery.py): the loss per share at the adverse cross, the closed candle
    # it came on (None = not in recovery), and the last candle the recovery rules read.
    recovery_loss: float | None = None
    recovery_bar: int | None = None
    rec_bar: int | None = None
    # Slow SMA exit (recovery.py): the last closed candle read.
    slow_bar: int | None = None
    # Why the trade opened (TradeLog.entry_reason), and the last closed candle the turn-trade colour exit read.
    entry_why: str = ""
    tc_bar: int | None = None
    # LIVE: when the exchange stop was first seen cancelled / rejected while Groww still held the shares.
    stop_dead_at: dt.datetime | None = None

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
    "qty", "sma_fast", "sma_medium", "sma_slow", "atr_period", "atr_multiplier", "use_stop",
    "stop_type", "gap_sl_mult", "gap_tp_mult", "gap_min_pct",
    "tsl_sl_points", "tsl_trail_points", "tsl_target_points",
    "tsl_mode", "tsl_sl_pct", "tsl_trail_pct", "tsl_target_pct",
    "use_adx_filter", "adx_threshold", "use_vwap", "use_volume", "volume_min_ratio",
    "use_density", "density_min_pct", "use_rsi", "rsi_long_min", "rsi_long_max",
    "rsi_short_min", "rsi_short_max", "use_bollinger", "bb_period", "bb_std", "bb_min_width_pct",
    "bb_exit", "use_gap_long", "gap_long_min", "gap_long_max", "use_gap_short", "gap_short_min",
    "gap_short_max", "use_gap_mode", "gap_entry_long", "gap_exit_long", "gap_entry_short",
    "gap_exit_short", "gap_giveback_pct", "gap_entry_delay_min", "gap_entry_window_min",
    "gap_fade_confirm_sma", "gap_fade_min_candles", "gap_fade_intrabar", "gap_fade_minute",
    "use_candle_dir", "candle_dir_count", "candle_dir_rule", "flip_orders", "cross_exit", "late_entry_min", "join_trend", "join_after_min",
    "counter_exit_count", "counter_entry", "counter_rule", "counter_need_fast", "counter_min_body",
    "counter_reenter", "counter_reenter_max",
    "use_ema", "ema_exit", "ema_fast", "ema_slow",
    "use_supertrend", "st_exit", "st_period", "st_mult", "use_level", "level_line",
    "use_or_volume", "use_or_vwap", "use_or_spread", "use_or_break", "or_volume_ratio", "or_spread_pct",
    "tall_pullback", "tall_atr_mult", "tall_expiry_candles",
    "extreme_entry", "extreme_anchor", "extreme_invalid_pct", "extreme_expiry_candles", "extreme_cancel_on_cross",
    "entry_candle_confirm", "entry_candle_pct", "entry_candle_max",
    "slope_exit", "slope_lookback", "slope_bend_pct", "slope_turn", "slope_turn_pct",
    "vturn_exit", "vturn_move_pct", "vturn_turn_pct", "vturn_within", "vturn_handoff", "handoff_now", "pivot_target",
    "turn_alert", "turn_entry", "uturn_on", "uturn_run", "uturn_min_pct", "turn_toward_cross", "turn_exit_candle", "turn_colour_exit",
    "day_extreme_guard", "day_extreme_pct", "day_extreme_pull_pct", "day_extreme_candles",
    "slow_sma_exit", "recovery_hold", "recovery_tolerance_pct", "recovery_emergency_mult",
    "recovery_atr_mult", "recovery_max_candles", "recovery_handoff",
    "breakout_handoff", "breakout_atr_mult", "breakout_gap_pct", "breakout_volume_mult",
    "entry_mode", "pattern_tf", "pattern_trend", "pattern_set", "pattern_min_edge", "candle_minutes",
    "exit_candle_minutes",
    "slope_minutes",
    "review_on", "review_gap_pct", "review_cooldown_min",
    "review_check_minutes", "review_check_mode", "review_default_answer",
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


# NSE trading symbols are letters and digits plus "&", "-" and "_" (M&M,
# GVT&D, BAJAJ-AUTO). A plain isalnum() check refused every name with "&".
_NSE_SYMBOL = re.compile(r"[A-Z0-9][A-Z0-9&_-]{0,19}")


def is_nse_symbol(name: str | None) -> bool:
    """True for an upper-case NSE trading symbol such as TCS, M&M or BAJAJ-AUTO."""
    return bool(name) and _NSE_SYMBOL.fullmatch(name) is not None


def trade_names(cfg: BotConfig) -> list[str]:
    """Stocks the bot may order. The chart symbol is not implied."""
    raw = getattr(cfg, "trade_symbols", None) or ""
    names: list[str] = []
    for part in str(raw).upper().replace(" ", "").split(","):
        if is_nse_symbol(part) and part not in names:
            names.append(part)
    return names[:MAX_TRADE_SYMBOLS]


def trade_sources(cfg: BotConfig) -> dict[str, str]:
    """Where each armed stock came from: {SYMBOL: source string}. Safe on bad JSON."""
    raw = getattr(cfg, "trade_sources", None) or "{}"
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        name = str(key).upper().strip()
        text = str(value).strip()[:40]
        if is_nse_symbol(name) and text:
            out[name] = text
    return out


def apply_trade_sources(cfg: BotConfig, updates: dict[str, str | None], current_names: list[str]) -> str:
    """Return the new `trade_sources` JSON after applying the updates and dropping
    symbols that are not on `current_names`. `updates[symbol]` set to None removes it."""
    sources = trade_sources(cfg)
    for symbol, source in updates.items():
        sym = (symbol or "").upper().strip()
        if source is None:
            sources.pop(sym, None)
            continue
        text = str(source).strip()[:40]
        if sym and text:
            sources[sym] = text
    kept = {s: sources[s] for s in sources if s in current_names}
    return json.dumps(kept, separators=(",", ":"), sort_keys=True)


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

    reason = _base_entry_reason(frame, direction, cfg, on, only, price)
    if on("ema"):
        ema = ema_filter.entry_reason(frame, direction, cfg)
        if ema:
            reason = f"{reason}; {ema}" if reason else ema
    if on("supertrend"):
        st = supertrend.entry_reason(frame, direction, cfg)
        if st:
            reason = f"{reason}; {st}" if reason else st
    if on("level"):
        lvl = level_check.entry_reason(frame, direction, cfg)
        if lvl:
            reason = f"{reason}; {lvl}" if reason else lvl
    if any(on(name) for name in opening_range.CHECKS):
        opening = opening_range.entry_reason(frame, direction, cfg, only=only)
        if opening:
            reason = f"{reason}; {opening}" if reason else opening
    return reason


def _base_entry_reason(frame, direction, cfg, on, only, price):
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
        elif low.startswith("ema"):
            parts.append("EMA")
        elif low.startswith("supertrend"):
            parts.append("ST")
        elif low.startswith("opening"):
            parts.append("Open range")
        elif low.startswith("price level"):
            parts.append("Level")
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
    ("gap", "SMA gap"), ("candle_dir", "Candle direction"), ("ema", "EMA"), ("supertrend", "Supertrend"), ("level", "Price level"),
    ("or_volume", "Opening volume"), ("or_vwap", "Opening VWAP"), ("or_spread", "Opening SMA spread"), ("or_break", "Opening break"),
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
        if name == "ema":
            return ema_filter.passed_text(frame, direction, cfg)
        if name == "supertrend":
            return supertrend.passed_text(frame, direction, cfg)
        if name == "level":
            return level_check.passed_text(frame, direction, cfg)
        if name in opening_range.CHECKS:
            return opening_range.passed_text(frame, direction, cfg, name)
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
        # EMA lines (ema_filter.py): drawn while the EMA entry check or the EMA exit is on.
        "ema_lines": ema_filter.entry_on(cfg) or ema_filter.exit_on(cfg),
        "ema_fast": ema_filter.lengths(cfg)[0],
        "ema_slow": ema_filter.lengths(cfg)[1],
        # Supertrend line (supertrend.py): drawn while its entry check or exit is on.
        "st_line": supertrend.entry_on(cfg) or supertrend.exit_on(cfg),
        "st_period": supertrend.settings(cfg)[0],
        "st_mult": supertrend.settings(cfg)[1],
        # SMA 9 slope strip (slope_exit.py): always drawn while the slope exit is on.
        "slope_exit": slope_exit.watched(cfg),
        "slope_lookback": slope_exit.lookback(cfg),
        "slope_bend_pct": slope_exit.bend_pct(cfg),
        "slope_turn_pct": slope_exit.turn_pct(cfg),
    }


# The settings filter_blocks reads. The chart cache is keyed on them.
_FILTER_KEYS = (
    "use_adx_filter", "adx_threshold", "use_vwap", "use_volume", "volume_min_ratio",
    "use_density", "density_min_pct", "use_rsi", "rsi_long_min", "rsi_long_max",
    "rsi_short_min", "rsi_short_max", "use_bollinger", "bb_period", "bb_std", "bb_min_width_pct",
    "use_gap_long", "gap_long_min", "gap_long_max", "use_gap_short", "gap_short_min", "gap_short_max",
    "use_candle_dir", "candle_dir_count", "candle_dir_rule",
    "use_ema", "ema_exit", "ema_fast", "ema_slow",
    "use_supertrend", "st_exit", "st_period", "st_mult", "use_level", "level_line",
    "use_or_volume", "use_or_vwap", "use_or_spread", "use_or_break", "or_volume_ratio", "or_spread_pct",
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


def candle_rows(
    frame: pd.DataFrame, ema: tuple[int, int] | None = None, st: tuple[int, float] | None = None
) -> list[dict]:
    """Chart candles with SMA, ATR, session VWAP and RSI 14 per candle.

    `ema` (fast, slow lengths) adds the EMA cross lines (ema_filter.py), from the
    same closes the bot's EMA check reads. A value is left out until that EMA has
    as many candles as its length, as the bot does not trust it before then.
    """
    if frame is None or frame.empty:
        return []
    ema_fast = ema_slow = None
    if ema:
        ema_fast, ema_slow = ema_filter.emas(frame, ema[0], ema[1])
    st_line, st_trend = supertrend.compute(frame, st[0], st[1]) if st else ([], [])
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
        if ema:
            rows[-1]["emaFast"] = _finite(ema_fast.iloc[i]) if i + 1 >= ema[0] else None
            rows[-1]["emaSlow"] = _finite(ema_slow.iloc[i]) if i + 1 >= ema[1] else None
        if st:
            # Two keys so the chart draws the up (green) and down (red) runs apart.
            up = st_trend[i] == 1
            rows[-1]["stUp"] = _finite(st_line[i]) if up else None
            rows[-1]["stDown"] = _finite(st_line[i]) if st_trend[i] == -1 else None
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
    # Recovery handoff (recovery.handoff), set by bots.register for the live bots only:
    # replays and the research desk never move a stock between bots.
    handoff = None
    # Set by bots.register: queue Bot 3's entry after a breakout handoff (breakout.py).
    breakout_arrival = None
    # Set by bots.register: Bot 2 orders at once after a hand-off (recovery.order_now, `handoff_now`).
    handoff_order = None
    #: Whether this engine's live prices go to the second-by-second record
    #: (tick_store). A replay's prices are made up from minute candles: never.
    records_ticks = True
    #: Whether PAPER (and REPLAY) entries take margin from the practice wallet
    #: (paper_wallet). Bots 1-4 and their replays do; the research engines don't.
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
        # Late entry (late_entry.py): a filter-refused cross waiting for its filters.
        self._late_pending: dict[str, Pending] = {}
        # Tall-candle pullback (tall_pullback.py): an entry waiting for the price to come back.
        self._pullback: dict[str, PullbackOrder] = {}
        # Stocks handed here by a breakout, waiting for this bot's entry on its side (breakout.py).
        self._arrivals: dict[str, breakout.Arrival] = {}
        # Stocks not traded again today after an emergency recovery exit (recovery.py).
        self._recovery_blocked: set[str] = set()
        # Join the morning trend (join_trend.py): the SMA run each stock was entered on
        # today (never joined), and the stocks already joined or given up on today.
        self._side_entered: dict[str, tuple[str, int]] = {}
        self._joined_today: set[str] = set()
        # Early reversal entry (counter_candles.py): the SMA run each stock was entered against.
        self._counter_done: dict[str, tuple[str, int]] = {}
        # SMA 9 turn (turn_entry.py): the last closed candle read, the turn on it, and the turn already acted on.
        self._turn_seen: dict[str, int] = {}
        self._turn_hit: dict[str, tuple[int, str, str]] = {}
        self._turn_done: dict[str, int] = {}
        # The last turn alerted per stock (direction, candle ts): a V right after a U the same way is the same turn.
        self._turn_last: dict[str, tuple[str, int]] = {}
        # A turn against the open trade waiting for a candle of the other colour (turn_exit_candle):
        # (turn direction, last candle read, note).
        self._turn_wait: dict[str, tuple[str, int, str]] = {}
        # Re-entry after a counter-candle exit (counter_candles.py): the exit waiting to be undone
        # (signal side, SMA run start, exit candle's level, exit candle ts), and re-entries used per SMA run.
        self._reentry: dict[str, tuple[str, int, float, int]] = {}
        self._reentry_used: dict[str, tuple[int, int]] = {}
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
        self.last_tick_at: float | None = None  # monotonic time the 1-second loop last finished a pass (deep health)
        self._sleep = asyncio.sleep
        self.realized_net = 0.0
        self.realized_gross = 0.0  # today's closed P&L before charges (the P&L log reads it)
        self.trades_today = 0
        self._session_date = self._now().date().isoformat()
        self._quote_symbol = ""
        self._cfg_cache = None
        self._ticks_retry_at = 0.0
        self._ticks_job = None
        self._realized_mode: str | None = None
        # 1-minute human review (review.py). The raw 1-minute tape of each
        # watched stock (the strategy reads its own candle from _frames), the
        # closed candle each held stock was last reviewed on, the uncertain
        # stretch per stock ({trade_id, open, last_at}), and recent reviews.
        self._tapes: dict[str, pd.DataFrame] = {}
        # Daily pivot levels per (stock, trading day) (pivots.py); the earlier session never changes.
        self._pivot_cache: dict[tuple[str, dt.date], dict] = {}
        self._review_bar: dict[str, int] = {}
        self._review_episode: dict[str, dict] = {}
        self.reviews: list[dict] = []
        self._reviews_loaded = False
        self._review_lock = asyncio.Lock()
        #: Called with each new review (a replay pauses so it can be answered).
        self.on_review = None

    def _now(self) -> dt.datetime:
        """The engine's clock. A replay engine runs on the replayed day instead."""
        return _ist_now()

    def _alert(self, message: str, critical: bool = False, call: bool = False) -> None:
        """Telegram/WhatsApp alert, and a phone call with `call`. A replay engine sends none."""
        if critical:
            _schedule_critical_whatsapp(message, call=call)
        else:
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
            return enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None)), True
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
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
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
                    _strategy_of(row) or {},
                    getattr(row, "recovery_loss", None),
                    getattr(row, "recovery_bar", None),
                    getattr(row, "entry_reason", None) or "",
                )
                for row in rows
            ]
        try:
            cfg = self.load_config()
        except Exception:  # noqa: BLE001
            cfg = None
        for symbol, direction, qty, entry, cross, atr, sl, when, trade_id, mode, stop_active, flipped, at_entry, rec_loss, rec_bar, why in pending:
            if not symbol or symbol in self.positions:
                continue
            # The stop the trade was opened with: this stock's own settings,
            # then the copy saved with the trade at entry (never the shared ones).
            own = None if cfg is None else _cfg_for(cfg, symbol)
            if own is not None:
                for key in ("use_stop", "stop_type", "tsl_sl_points", "tsl_trail_points", "tsl_target_points"):
                    if at_entry.get(key) is not None:
                        setattr(own, key, at_entry[key])
            # A practice book on the moving stop keeps trailing from the saved
            # stop; its target comes back on the next closed candle.
            trailing = bool(
                stop_active
                and own is not None
                and uses_gap_stop(own, live=(mode or "PAPER").upper() == "LIVE")
            )
            tsl_points = tsl_step = None
            target = None
            if stop_active and own is not None and not trailing and uses_tsl(own):
                # The trailed stop was saved on each move. It never loosens,
                # so trailing resumes from it with the best price reset to entry.
                tsl_points, tsl_step, tgt_points = tsl_settings(own, entry_price=entry)
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
                # Supertrend stop: resumes from the saved stop, tighter only.
                st_stop=bool(stop_active and own is not None and not trailing and not tsl_step and supertrend.stop_on(own)),
                # Ticks before a restart are not kept; the range restarts at entry.
                high=entry,
                low=entry,
                flipped=flipped,
                recovery_loss=float(rec_loss) if rec_loss is not None else None,
                recovery_bar=int(rec_bar) if rec_bar is not None else None,
                rec_bar=int(rec_bar) if rec_bar is not None else None,
                entry_why=why,
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
            self.realized_gross = 0.0
            self._realized_mode = None
            self.trades_today = 0
            self._warned.clear()
            self._rejects.clear()
            self._reject_sent.clear()
            self._gap_pending.clear()
            self._late_pending.clear()
            self._pullback.clear()
            self._recovery_blocked.clear()
            self._side_entered.clear()
            self._joined_today.clear()
            self._reentry.clear()
            self._reentry_used.clear()
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
            self.last_tick_at = time.monotonic()
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
            # The 1-minute tape is kept only for the human review's read-out.
            self._tapes[symbol] = frame
            # Candles of the chosen interval (candles.py); the bot never sees the 1-minute tape then.
            frame = resample(frame, candle_minutes(cfg, stock_overrides(cfg, symbol)))
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
        self._load_reviews(cfg)
        self._expire_reviews()

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
            await self._trail_supertrend(symbol_cfg)
            await self._watch_stop(symbol_cfg)
            await self._watch_bollinger(symbol_cfg)
            await self._watch_counter_candles(symbol_cfg)
            await self._watch_ema_exit(symbol_cfg)
            await self._watch_exit_candle(symbol_cfg)
            await self._watch_supertrend_exit(symbol_cfg)
            await self._watch_slope_exit(symbol_cfg)
            await self._watch_turn_colour(symbol_cfg)
            await self._watch_slow_sma(symbol_cfg)
            await self._watch_recovery(symbol_cfg, now)
            await self._watch_gap_fade(symbol_cfg)
            await self._watch_pivot_target(symbol_cfg)

        await self._watch_pullbacks(cfg, now)

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
            if _candle_is_behind(closed_ts, now, candle_minutes(cfg, stock_overrides(cfg, symbol))):
                # The bar that just closed is not in this frame yet. Judging
                # now would burn the cross, and the next minute would no
                # longer see it.
                self._signals[symbol] = f"{symbol} waiting for the closed candle"
                self.last_signal = self._signals[symbol]
                continue
            arrival = self._arrivals.pop(symbol, None)
            if arrival is not None and self._still_armed(symbol):
                await self._enter_on_arrival(symbol, arrival, frame, _cfg_for(cfg, symbol), now)
                if self.status != "RUNNING":
                    break
            if self._judged_bar.get(symbol) == closed_ts:
                continue
            # The candle on the tape when the stock was armed is not read for a breakout.
            seen_before = symbol in self._judged_bar
            if not self._still_armed(symbol):
                # Taken off the Trade list while this pass fetched quotes.
                continue
            symbol_cfg = _cfg_for(cfg, symbol)
            enriched = enrich(frame, symbol_cfg.sma_fast, symbol_cfg.sma_slow, symbol_cfg.atr_period, getattr(symbol_cfg, "sma_medium", None))
            self._focus = symbol
            self.ltp = self._ltps.get(symbol, self.ltp)
            if seen_before and await self._watch_breakout(symbol, symbol_cfg, enriched):
                self._judged_bar[symbol] = closed_ts
                if self.status != "RUNNING":
                    break
                continue
            result = await self.on_minute(now, symbol_cfg, enriched)
            if (result or "").startswith("blocked — order PENDING"):
                continue
            judged = _closed_bar_ts(enriched) or closed_ts
            if judged is not None:
                self._judged_bar[symbol] = judged
            if self.status != "RUNNING":
                break
        # After every exit and cross of this pass: a trade the strategy just
        # closed is not reviewed.
        if self.status == "RUNNING":
            self._expire_reviews()
            await self._review_positions(cfg, now)
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
        for table in (
            self._frames, self._ltps, self._judged_bar, self._signals, self._skip_cross_until, self._gap_pending, self._late_pending,
            self._pullback, self._side_entered, self._counter_done, self._reentry, self._reentry_used,
            self._tapes, self._review_bar, self._review_episode, self._turn_seen, self._turn_hit, self._turn_done,
            self._turn_last, self._turn_wait,
        ):
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
        turn = self._turn_now(symbol, frame, cfg)
        if uses_patterns(cfg):
            return await self._pattern_minute(symbol, frame, cfg, now)
        if uses_colour(cfg):
            return await self._colour_minute(symbol, frame, cfg, now)
        self._warn_upcoming(symbol, frame, cfg, now)
        # A cross that was already printed when the bot started, or when this
        # stock was armed, is skipped. The next cross on a newer closed bar
        # is the one that may trade. Being flat does not enter early.
        stale = self._cross_is_stale(symbol, frame)
        if stale and not join_trend.enabled(cfg) and not (turn is not None and turn_entry.entry_on(cfg)):
            text = f"{symbol} no order — that cross already printed, waiting for a new cross. {_filter_note(cfg)}"
            self._signals[symbol] = text
            self.last_signal = text
            return text
        # With join_trend on, an old cross is not traded as a cross; the running side may be joined below.
        signal, signal_frame = (None, None) if stale else _signal_on_unjudged_bars(frame, self._judged_bar.get(symbol))
        if uses_gap_mode(cfg):
            return await self._gap_minute(symbol, signal, signal_frame, frame, cfg, now)
        if (signal is None or signal_frame is None) and turn is not None and turn_entry.entry_on(cfg):
            text = await self._turn_minute(symbol, turn, frame, cfg, now)
            if text is not None:
                return text
        elif (signal is None or signal_frame is None) and symbol in self._turn_wait and turn_entry.entry_on(cfg):
            text = await self._turn_wait_minute(symbol, frame, cfg, now)
            if text is not None:
                return text
        if (signal is None or signal_frame is None) and symbol in self._late_pending:
            return await self._late_minute(symbol, frame, cfg, now)
        if signal is None or signal_frame is None:
            run = self._joinable(symbol, frame, cfg, now)
            if run is not None:
                return await self._join_minute(symbol, run, frame, cfg, now)
        if (signal is None or signal_frame is None) and symbol in self._reentry:
            text = await self._reentry_minute(symbol, frame, cfg, now)
            if text is not None:
                return text
        if (signal is None or signal_frame is None) and counter_candles.entry_on(cfg) and symbol not in self.positions:
            text = await self._counter_minute(symbol, frame, cfg, now)
            if text is not None:
                return text
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
        self._late_pending.pop(symbol, None)
        self._reentry.pop(symbol, None)  # a fresh cross takes over from a waiting re-entry
        result = await self.apply_signal(signal, signal_frame, cfg, now)
        window = late_entry.window_min(cfg)
        if window > 0 and symbol not in self.positions and late_entry.refused_by_filter(signal, result):
            cross_ts = _closed_bar_ts(signal_frame) or _closed_bar_ts(frame) or 0
            want = "LONG" if signal == "BULLISH" else "SHORT"
            # Already alerted as refused; a later fill sends its own fill alert.
            self._late_pending[symbol] = Pending(want, int(cross_ts), alerted=True)
            result = f"{result} · late entry armed for {window} min"
            self._signals[symbol] = result
            self.last_signal = result
        return result

    def _joinable(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> tuple[str, int] | None:
        """Today's morning trend to join (join_trend.py), or None."""
        if not join_trend.enabled(cfg) or symbol in self.positions or symbol in self._joined_today:
            return None
        start = join_trend.join_from(cfg, now)
        if now < start:
            return None
        run = join_trend.side_start(frame)
        # Only the run already going at the join time; a later cross is a normal cross.
        if run is None or run[1] >= int(start.timestamp()) or self._side_entered.get(symbol) == run:
            return None
        return run

    async def _join_minute(self, symbol: str, run: tuple[str, int], frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Join a running trend: ask the usual entry path for the side the SMAs are already on."""
        direction = run[0]
        sig = "BULLISH" if direction == "LONG" else "BEARISH"
        result = await self.apply_signal(sig, frame, cfg, now, alert=False, why="JOIN_TREND")
        if result.startswith("blocked — order PENDING"):
            return result
        if result.startswith("opened"):
            self._joined_today.add(symbol)
            text = f"{result} — joined the morning {direction} trend"
        elif late_entry.refused_by_filter(sig, result):
            text = f"{symbol} waiting to join the morning {direction} trend — {result.split(' — ', 1)[-1]}"
        elif "no new entries after" in result or "trade cap" in result:
            # No later candle of today will do better.
            self._joined_today.add(symbol)
            text = f"{symbol} no order — gave up joining the {direction} trend today: {result}"
        else:
            text = f"{symbol} no order — {result}"
        self._signals[symbol] = text
        self.last_signal = text
        return text

    def _turn_now(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig) -> tuple[int, str, str] | None:
        """The SMA 9 turn printed on the newest closed candle (turn_entry.py), read once per candle; alerts once.

        The first candle read for a stock only sets the mark: a turn from before it was armed is old news."""
        if not turn_entry.watched(cfg):
            return None
        ts = _closed_bar_ts(frame)
        if ts is None:
            return None
        last = self._turn_seen.get(symbol)
        self._turn_seen[symbol] = ts
        if last is None:
            return None
        if last != ts:
            found = turn_entry.detect(frame.iloc[:-1], int(cfg.sma_fast), cfg)
            before = self._turn_last.get(symbol)
            span = ts - last if last and ts > last else 60
            if (
                found is not None
                and before is not None
                and before[0] == found[0]
                and ts - before[1] <= turn_entry.SAME_TURN_CANDLES * span
            ):
                found = None  # the same turn, read again a candle or two later (a U, then its V)
            if found is None:
                self._turn_hit.pop(symbol, None)
            else:
                self._turn_last[symbol] = (found[0], ts)
                self._turn_hit[symbol] = (ts, found[0], found[1])
                if turn_entry.alert_on(cfg):
                    bot = getattr(cfg, "bot_name", None) or f"Bot {self.bot_id}"
                    price = _finite(frame.iloc[-2].get("close"))
                    pos = self.positions.get(symbol)
                    held = pos.signal_direction if pos is not None else None
                    closed = frame.iloc[:-1]
                    away = turn_entry.toward_on(cfg) and not turn_entry.toward_cross(closed, found[0])
                    waits = (
                        held not in (None, found[0])
                        and turn_entry.exit_candle_on(cfg)
                        and not turn_entry.candle_against(closed.iloc[-1], held)
                    )
                    self._alert(
                        turn_entry.alert_text(
                            bot, symbol, found[0], found[1], price, turn_entry.entry_on(cfg), held=held, away=away, waits=waits
                        )
                    )
        hit = self._turn_hit.get(symbol)
        return hit if hit is not None and hit[0] == ts else None

    async def _turn_minute(
        self, symbol: str, turn: tuple[int, str, str], frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime
    ) -> str | None:
        """SMA 9 turn entry (turn_entry.py): order on the turn's side through the usual entry path, once per turn."""
        ts, want, note = turn
        if self._turn_done.get(symbol) == ts:
            return None
        held = self.positions.get(symbol)
        if held is not None and held.signal_direction == want:
            self._turn_done[symbol] = ts
            return None
        toward = not turn_entry.toward_on(cfg) or turn_entry.toward_cross(frame.iloc[:-1], want)
        if held is None and not toward:
            self._turn_done[symbol] = ts
            return self._say(symbol, f"{symbol} no order on the SMA {want} turn — it points away from the 9/21 cross ({note})")
        if held is not None and turn_entry.exit_candle_on(cfg) and not turn_entry.candle_against(frame.iloc[-2], held.signal_direction):
            # The turn came on a candle of the trade's own colour: wait for one of the other colour to close.
            self._turn_done[symbol] = ts
            self._turn_wait[symbol] = (want, ts, note)
            colour = "red" if held.signal_direction == "LONG" else "green"
            return self._say(symbol, f"{symbol} holding {held.direction} — SMA 9 turned {want}, closes on the next {colour} candle ({note})")
        result = await self._turn_act(symbol, want, note, toward, frame, cfg, now)
        if result is not None and not result.startswith("blocked — order PENDING"):
            self._turn_done[symbol] = ts
        return result

    def _say(self, symbol: str, text: str) -> str:
        self._signals[symbol] = text
        self.last_signal = text
        return text

    async def _turn_act(
        self, symbol: str, want: str, note: str, toward: bool, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime
    ) -> str | None:
        """Act on a turn: open / close-and-reverse through apply_signal, or, for a turn away from the cross against
        an open trade, close it without a reverse (turn_entry.py)."""
        self._turn_wait.pop(symbol, None)
        held = self.positions.get(symbol)
        if held is not None and not toward:
            if not cross_exits(cfg):
                return self._say(symbol, f"{symbol} holding {held.direction} — SMA cross exit is off; the turn does not close it")
            await self._exit_now(cfg, self._exit_price(symbol, held), turn_entry.EXIT_REASON)
            return self._say(symbol, f"{symbol} closed {held.direction} — SMA 9 turned {want}; no reverse, it points away from the 9/21 cross ({note})")
        sig = "BULLISH" if want == "LONG" else "BEARISH"
        result = await self.apply_signal(sig, frame, cfg, now, alert=False, why=turn_entry.ENTRY_REASON)
        if result.startswith("blocked — order PENDING"):
            return result
        if result.startswith("opened") or result.startswith("reversed"):
            return self._say(symbol, f"{result} — {note}")
        return self._say(symbol, f"{symbol} no order on the SMA {want} turn ({note}): {result}")

    async def _turn_wait_minute(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str | None:
        """A turn against the open trade waiting for a candle of the other colour (turn_exit_candle). None = nothing
        to say."""
        want, since, note = self._turn_wait[symbol]
        held = self.positions.get(symbol)
        closed = frame.iloc[:-1]
        if held is None or held.signal_direction == want or closed.empty:
            self._turn_wait.pop(symbol, None)
            return None
        bar = closed.iloc[-1]
        if int(bar["ts"]) <= since:
            return None
        side = 1.0 if held.signal_direction == "LONG" else -1.0
        slope = turn_entry.slopes(closed, int(cfg.sma_fast), slope_exit.lookback(cfg)).iloc[-1]
        if not pd.isna(slope) and float(slope) * side >= slope_exit.vturn_turn_pct(cfg):
            self._turn_wait.pop(symbol, None)
            return self._say(symbol, f"{symbol} keeps its {held.direction} — SMA 9 swung back with the trade")
        if not turn_entry.candle_against(bar, held.signal_direction):
            self._turn_wait[symbol] = (want, int(bar["ts"]), note)
            colour = "red" if held.signal_direction == "LONG" else "green"
            return self._say(symbol, f"{symbol} holding {held.direction} — still waiting for a {colour} candle to close (SMA 9 turned {want})")
        toward = not turn_entry.toward_on(cfg) or turn_entry.toward_cross(closed, want)
        return await self._turn_act(symbol, want, note, toward, frame, cfg, now)

    async def _counter_minute(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str | None:
        """Early reversal entry (counter_candles.py): N candles against the SMAs' side, before the cross.

        None when there is no such run, so the usual "waiting" note is shown.
        """
        closed = frame.iloc[:-1]
        side_now = counter_candles.sma_side(closed)
        run = join_trend.side_start(frame)
        if side_now is None or run is None:
            return None
        want = counter_candles.against(side_now)
        key = (want, int(run[1]))
        if self._counter_done.get(symbol) == key:
            return None
        hit, note = counter_candles.run_towards(
            closed, want, counter_candles.entry_count(cfg), counter_candles.rule(cfg), counter_candles.need_fast(cfg)
        )
        if not hit:
            return None
        sig = "BULLISH" if want == "LONG" else "BEARISH"
        result = await self.apply_signal(sig, frame, cfg, now, alert=False, why="EARLY_REVERSAL")
        if result.startswith("blocked — order PENDING"):
            return result
        if result.startswith("opened"):
            self._counter_done[symbol] = key
            text = f"{result} — early reversal: {note}"
        else:
            text = f"{symbol} no order — early {want} reversal ({note}): {result}"
        self._signals[symbol] = text
        self.last_signal = text
        return text

    async def _reentry_minute(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str | None:
        """After a counter-candle exit: put the trade back on once a closed candle breaks the exit candle the
        trade's way, while the SMAs stay on its side (counter_candles.resumes). None = nothing to say."""
        side, run_ts, level, exit_ts = self._reentry[symbol]
        closed = frame.iloc[:-1]
        run = join_trend.side_start(frame)
        if symbol in self.positions or not counter_candles.reenter_on(cfg) or closed.empty:
            self._reentry.pop(symbol, None)
            return None
        if run is None or run[0] != side or int(run[1]) != run_ts:
            self._reentry.pop(symbol, None)  # the SMAs crossed back: no re-entry on this run
            return None
        used = self._reentry_used.get(symbol)
        done = used[1] if used and used[0] == run_ts else 0
        limit = counter_candles.reenter_max(cfg)
        if done >= limit:
            self._reentry.pop(symbol, None)
            return None
        bar = closed.iloc[-1]
        word = "below the exit candle's low" if side == "SHORT" else "above the exit candle's high"
        waiting = f"{symbol} out after counter candles — re-enters {side} on a candle closing {word} {level:.2f} ({done}/{limit} used)"
        if int(bar["ts"]) <= exit_ts:
            text = waiting
        else:
            hit, note = counter_candles.resumes(bar, side, level)
            if not hit:
                text = waiting
            else:
                sig = "BULLISH" if side == "LONG" else "BEARISH"
                result = await self.apply_signal(sig, frame, cfg, now, alert=False, why="COUNTER_REENTRY")
                if result.startswith("blocked — order PENDING"):
                    return result
                if result.startswith("opened") or symbol in self._pullback:
                    # Opened, or waiting as a limit / pullback / entry-candle order: the re-entry is used.
                    self._reentry.pop(symbol, None)
                    self._reentry_used[symbol] = (run_ts, done + 1)
                    text = f"{result} — re-entry ({done + 1}/{limit}): {note}"
                else:
                    if "no new entries after" in result or "trade cap" in result:
                        self._reentry.pop(symbol, None)  # no later candle of today will do better
                    text = f"{symbol} no order — re-entry ({note}): {result}"
        self._signals[symbol] = text
        self.last_signal = text
        return text

    async def _late_minute(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Late entry, once per closed candle: try a filter-refused cross again (late_entry.py)."""
        pending = self._late_pending[symbol]
        window = late_entry.window_min(cfg)
        bar = frame.iloc[-2]
        gap = sma_gap_signed(bar.get("sma_9"), bar.get("sma_21"))
        action, note = late_entry.judge(pending.direction, pending.cross_ts, int(bar["ts"]), gap, window)
        if symbol in self.positions or window <= 0:
            action = "drop"
        if action == "drop":
            self._late_pending.pop(symbol, None)
            text = f"{symbol} holding" if symbol in self.positions else f"{symbol} no order — late entry dropped: {note}"
            self._signals[symbol] = text
            self.last_signal = text
            return text
        sig = "BULLISH" if pending.direction == "LONG" else "BEARISH"
        result = await self.apply_signal(sig, frame, cfg, now, alert=False, why="LATE_ENTRY")
        if result.startswith("blocked — order PENDING"):
            return result
        if late_entry.refused_by_filter(sig, result):
            text = f"{symbol} {pending.direction} late entry waiting ({note}) — {result.split(' — ', 1)[-1]}"
        else:
            self._late_pending.pop(symbol, None)
            text = f"{result} — late entry, {note}" if result.startswith("opened") else result
        self._signals[symbol] = text
        self.last_signal = text
        return text

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
        result = await self.apply_signal(signal, frame, cfg, now, alert=False, why="CANDLE_PATTERN")
        if result.startswith(("opened", "reversed")):
            result = f"{result} — {call.note}"
            self._signals[symbol] = result
            self.last_signal = result
        return result

    async def _colour_minute(self, symbol: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Ride the candle colour (candle_patterns.colour_call), once per closed candle of pattern_tf minutes.

        A green candle buys and a red one sells at the start of the next candle. The trade is
        held while the candles keep its colour (a doji holds too); the first candle of the other
        colour closes it (COLOUR_TURN) and the other side opens at once. Market hours, the
        cut-off, the Trade list, the caps, the entry filters, the stop and the flip all apply as
        for a cross; after a stop-out the next candle's colour enters again.
        """
        call = colour_call(frame, cfg)
        pos = self.positions.get(symbol)
        if call.side is None or (pos is not None and pos.signal_direction == call.side):
            text = f"{symbol} {'holding' if pos is not None else 'no order'} — {call.note}"
            self._signals[symbol] = text
            self.last_signal = text
            return text
        if pos is not None:
            self._focus = symbol
            await self._exit_now(cfg, self._exit_price(symbol, pos), "COLOUR_TURN")
            if symbol in self.positions:
                text = f"{symbol} could not close on the {call.note} — {self.last_signal}"
                self._signals[symbol] = text
                return text
        signal = "BULLISH" if call.side == "LONG" else "BEARISH"
        # No Telegram for a refused candle: one closes every few minutes.
        result = await self.apply_signal(signal, frame, cfg, now, alert=False, why="CANDLE_COLOUR")
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
        if signal is None and symbol not in self._gap_pending:
            run = self._joinable(symbol, frame, cfg, now)
            if run is not None:
                # Join the morning trend: its side arms the gap entry, once a day.
                self._joined_today.add(symbol)
                self._gap_pending[symbol] = Pending(run[0], _closed_bar_ts(frame) or run[1])
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
            result = await self.apply_signal(sig, frame, cfg, now, alert=not pending.alerted, why="GAP_MODE")
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
            if self.position is pos and bool(getattr(cfg, "gap_fade_minute", False)):
                await self._gap_fade_minute(cfg, pos, symbol, frame, opened)
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
        await self._gap_fade_probe(cfg, pos, symbol, frame, float(self.ltp), "the live price")

    async def _gap_fade_minute(
        self, cfg: BotConfig, pos: OpenPosition, symbol: str, frame: pd.DataFrame, opened: dt.datetime
    ) -> None:
        """gap_fade_minute: judge the fade on each closed 1-minute candle inside a longer bot candle.

        The 1-minute close stands in for the forming candle's close, as the live
        price does for gap_fade_intrabar, but it comes from the Groww 1-minute
        history, so it also works after hours and in replays. Once per minute.
        """
        tape = self._tapes.get(symbol)
        if pos.gap_prev is None or tape is None or len(tape) < 2 or frame is None or len(frame) < 2:
            return
        minute = tape.iloc[-2]  # the last closed 1-minute candle (the tape's last row is forming)
        minute_ts = int(minute["ts"])
        if minute_ts < int(frame.iloc[-1]["ts"]) or minute_ts + 60 <= opened.timestamp():
            return  # not inside the forming bot candle (a 1-minute bot), or before the entry
        seen = getattr(self, "_minute_fade_at", None)
        if seen is None:
            seen = self._minute_fade_at = {}
        if seen.get(symbol) == minute_ts:
            return
        seen[symbol] = minute_ts
        price = _finite(minute.get("close"))
        if price is None or price <= 0:
            return
        when = dt.datetime.fromtimestamp(minute_ts + 60, IST).strftime("%H:%M")
        await self._gap_fade_probe(cfg, pos, symbol, frame, price, f"the {when} 1-minute close")

    async def _gap_fade_probe(
        self, cfg: BotConfig, pos: OpenPosition, symbol: str, frame: pd.DataFrame, price: float, source: str
    ) -> None:
        """Read the gap as if `price` closed the forming candle; exit on a fade.

        The closed-candle state (armed, widest gap, narrowing run) is only probed
        on a copy, never changed; the next closed candle updates it as usual.
        """
        need = int(cfg.sma_slow) + 2
        closes = pd.Series([*frame["close"].iloc[:-1].astype(float).tail(need).tolist(), price])
        fast = closes.rolling(int(cfg.sma_fast)).mean().iloc[-1]
        slow = closes.rolling(int(cfg.sma_slow)).mean().iloc[-1]
        gap = sma_gap_signed(fast, slow)
        if gap is None:
            return
        probe = dict(pos.gap_state)
        note = judge_exit(cfg, pos.signal_direction, gap, pos.gap_prev, probe)
        if note is None:
            return
        ok, why = fade_confirmed(cfg, pos.signal_direction, price, _finite(slow), probe)
        if not ok:
            self._signals[symbol] = f"{symbol} holding — {note} on {source}, but {why}"
            return
        self._signals[symbol] = f"{symbol} gap exit on {source} — {note}"
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
        self,
        signal: str,
        frame: pd.DataFrame,
        cfg: BotConfig,
        now: dt.datetime,
        alert: bool = True,
        pullback_at: PullbackOrder | None = None,
        why: str = "SMA_CROSS",
    ) -> str:
        """Stop-and-reverse on a closed-candle cross. Returns a short status.

        `why` is the entry reason saved on the trade (ENTRY_REASONS).

        `pullback_at` makes the entry wait at that level whatever the candle's height
        (a breakout arrival, breakout.py); a flipped stock still orders at once.
        """
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
        want = "LONG" if signal == "BULLISH" else "SHORT"
        # A new signal replaces any entry still waiting for its pullback.
        self._pullback.pop(self._focus, None)
        pullback = None
        if pullback_at is not None and not flips_orders(cfg):
            pullback = pullback_at
        elif per_candle(cfg):
            # Candle patterns / colour order at the start of the next candle: no waiting entry.
            pass
        elif extreme_entry.applies(cfg):
            # Candle-extreme limit (extreme_entry.py), judged on the side the real order goes.
            real = _opposite(want) if flips_orders(cfg) else want
            found = extreme_entry.levels(curr, real, cfg)
            if found is not None:
                span = int(frame.iloc[-1]["ts"]) - int(curr["ts"]) if len(frame) >= 2 else 0
                pullback = PullbackOrder(
                    direction=want,
                    limit=round_price(cfg.symbol, found[0]),
                    cross_price=cross_price,
                    atr=atr,
                    signal_ts=int(curr["ts"]),
                    span_sec=span if span > 0 else 60,
                    candles=extreme_entry.expiry(cfg),
                    side=real,
                    invalid_at=found[1],
                    sma_side=extreme_entry.sma_side(curr),
                    cancel_on_cross=extreme_entry.cancel_on_cross(cfg),
                    note="candle extreme",
                    why=why,
                )
        elif tall_pullback.on(cfg) and not flips_orders(cfg):
            plan = tall_pullback.plan(curr, want, atr, cfg)
            if plan is not None:
                span = int(frame.iloc[-1]["ts"]) - int(curr["ts"]) if len(frame) >= 2 else 0
                pullback = PullbackOrder(
                    direction=want,
                    limit=round_price(cfg.symbol, plan.limit),
                    cross_price=cross_price,
                    atr=atr,
                    signal_ts=int(curr["ts"]),
                    span_sec=span if span > 0 else 60,
                    candles=tall_pullback.expiry(cfg),
                    why=why,
                )
        if pullback is None and day_extreme.on(cfg) and not per_candle(cfg):
            # Day-extreme guard (day_extreme.py): a buy at today's high / a sell at today's low waits for a pullback.
            real = _opposite(want) if flips_orders(cfg) else want
            found = day_extreme.plan(frame.iloc[:-1], real, cfg)
            if found is not None:
                span = int(frame.iloc[-1]["ts"]) - int(curr["ts"]) if len(frame) >= 2 else 0
                pullback = PullbackOrder(
                    direction=want,
                    limit=round_price(cfg.symbol, found[0]),
                    cross_price=cross_price,
                    atr=atr,
                    signal_ts=int(curr["ts"]),
                    span_sec=span if span > 0 else 60,
                    candles=day_extreme.candles(cfg),
                    side=real,
                    sma_side=extreme_entry.sma_side(curr),
                    cancel_on_cross=True,
                    note=day_extreme.NOTE,
                    why=why,
                )
        if pullback is None and entry_candle.on(cfg) and not per_candle(cfg):
            # Entry-candle colour check (entry_candle.py): wait inside the forming candle for the trade's colour.
            real = _opposite(want) if flips_orders(cfg) else want
            span = int(frame.iloc[-1]["ts"]) - int(curr["ts"]) if len(frame) >= 2 else 0
            pct = entry_candle.margin_pct(cfg)
            opened_at = _finite(frame.iloc[-1].get("open")) or float(curr["close"])
            pullback = PullbackOrder(
                direction=want,
                limit=round_price(cfg.symbol, entry_candle.level(opened_at, real, pct)),
                cross_price=cross_price,
                atr=atr,
                signal_ts=int(curr["ts"]),
                span_sec=span if span > 0 else 60,
                candles=entry_candle.max_candles(cfg),
                side=real,
                sma_side=extreme_entry.sma_side(curr),
                cancel_on_cross=True,
                confirm=True,
                confirm_pct=pct,
                note="entry candle",
                why=why,
            )
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
                    pullback=pullback,
                    why=why,
                )
                entered = result.startswith(("opened", "reversed")) and self._focus in self.positions
                if entered or self._focus in self._pullback:
                    # Remember the SMA run this entry belongs to (join_trend.py: never joined twice).
                    # A pullback entry waiting for its level counts, so a join does not order it at once.
                    run = join_trend.side_start(frame)
                    side = self.positions[self._focus].signal_direction if entered else want
                    if run is not None:
                        self._side_entered[self._focus] = (side, run[1])
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
        pullback: PullbackOrder | None = None,
        why: str = "SMA_CROSS",
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

        exit_reason = turn_entry.EXIT_REASON if why == turn_entry.ENTRY_REASON else "MA_CROSS"
        if pos is not None and pos.signal_direction != want and recovery.on(cfg):
            # Recovery hold (recovery.py): close a winner at once, hold a loser for its recovery.
            if pos.recovery_loss is not None:
                return f"holding {pos.direction} — recovery hold since the earlier cross"
            price = self._market_price(cfg.symbol, cross_price)
            loss = recovery.loss_per_share(pos.direction, pos.entry_price, price)
            if loss >= 0:
                return self._enter_recovery(pos, cfg, max(loss, 0.0))
            exit_reason = turn_entry.EXIT_REASON if why == turn_entry.ENTRY_REASON else recovery.PROFIT_EXIT

        if pos is not None and pos.signal_direction != want:
            # Opposite cross: cancel SL, verify, flatten, then maybe reverse.
            await self._cancel_sl_verified(pos)
            await self._close_position(pos, self._market_price(cfg.symbol, cross_price), exit_reason, now, cfg)
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
            if (cfg.symbol or "").upper() in self._recovery_blocked:
                return "closed on cross — no new trade in this stock today (emergency recovery exit)"
            if pullback is not None:
                return "reversed — " + self._arm_pullback(pullback, cfg)
            await self._open(want, cross_price, atr, cfg, now, why=why)
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
        if (cfg.symbol or "").upper() in self._recovery_blocked:
            return "entry blocked — no new trade in this stock today (emergency recovery exit)"
        if pullback is not None:
            return self._arm_pullback(pullback, cfg)
        await self._open(want, cross_price, atr, cfg, now, why=why)
        return f"opened {want}"

    def _arm_pullback(self, order: PullbackOrder, cfg: BotConfig) -> str:
        """Wait for the price to come back to the limit level instead of ordering at the tall candle's close."""
        symbol = (cfg.symbol or "").upper()
        self._pullback[symbol] = order
        side = "BUY" if (order.side or order.direction) == "LONG" else "SELL"
        if order.confirm:
            colour, beyond = ("green", "above") if side == "BUY" else ("red", "below")
            return (
                f"waiting to {side} — the entry candle must be {colour}: {beyond} its open by {order.confirm_pct:g}% "
                f"({order.limit:.2f} now); up to {order.candles} candle{'s' if order.candles != 1 else ''}"
            )
        if order.invalid_at is not None:
            beyond = "under" if side == "BUY" else "over"
            return (
                f"limit {side} at {order.limit:.2f} — {order.note}; waiting up to {order.candles} "
                f"candle{'s' if order.candles != 1 else ''}, cancelled {beyond} {order.invalid_at:.2f}"
            )
        return (
            f"pullback {side} at {order.limit:.2f} — {order.note}; "
            f"waiting up to {order.candles} candle{'s' if order.candles != 1 else ''} for the price to come back"
        )

    def _enter_recovery(self, pos: OpenPosition, cfg: BotConfig, loss: float) -> str:
        """The adverse cross came while the trade was losing: hold it for its recovery (recovery.py)."""
        symbol = (cfg.symbol or "").upper()
        pos.recovery_loss = round(loss, 4)
        pos.recovery_bar = _closed_bar_ts(self._frames.get(symbol))
        pos.rec_bar = pos.recovery_bar
        # Saved so a restart keeps the hold and its emergency floor.
        with session_factory()() as db:
            row = db.get(TradeLog, pos.trade_id)
            if row is not None and row.exit_time is None:
                row.recovery_loss = pos.recovery_loss
                row.recovery_bar = pos.recovery_bar
                db.commit()
        return (
            f"holding {pos.direction} — recovery hold: loss {loss:.2f} a share at the cross; waits up to "
            f"{recovery.max_candles(cfg)} candles for the price to come back"
        )

    async def _watch_slow_sma(self, cfg: BotConfig) -> None:
        """Once per closed candle: exit on a close beyond the slow SMA against the trade (recovery.py)."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or not recovery.slow_exit_on(cfg) or self.ltp <= 0:
            return
        if pos.recovery_loss is not None:
            return  # the recovery rules judge a held trade
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.slow_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the Bollinger exit).
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        minutes = candle_minutes(cfg, stock_overrides(cfg, symbol))
        if closed_ts + minutes * 60 <= opened.timestamp():
            return
        pos.slow_bar = closed_ts
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
        bar = enriched.iloc[-2]
        close, slow = _finite(bar.get("close")), _finite(bar.get("sma_21"))
        if not recovery.slow_breach(pos.signal_direction, close, slow):
            return
        word = "below" if pos.signal_direction == "LONG" else "above"
        self._signals[symbol] = f"{symbol} slow SMA exit — close {close:.2f} {word} the slow SMA {slow:.2f}"
        await self._exit_now(cfg, self.ltp, recovery.SLOW_SMA_EXIT)

    async def _watch_recovery(self, cfg: BotConfig, now: dt.datetime) -> None:
        """A trade in recovery hold: recovery target, emergency exit or the cut-off (recovery.py)."""
        pos = self.position
        if pos is None or pos.recovery_loss is None or self.status != "RUNNING" or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop and the square-off.
            return
        if now.time() >= _entry_cutoff(cfg):
            self._signals[symbol] = f"{symbol} recovery hold closed at the {_entry_cutoff(cfg).strftime('%H:%M')} cut-off"
            await self._exit_now(cfg, self.ltp, recovery.CUTOFF_EXIT)
            return
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.rec_bar == closed_ts:
            return
        if pos.recovery_bar is not None and closed_ts <= pos.recovery_bar:
            return  # the cross candle itself
        pos.rec_bar = closed_ts
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
        bar = enriched.iloc[-2]
        span = candle_minutes(cfg, stock_overrides(cfg, symbol)) * 60
        held = int((closed_ts - pos.recovery_bar) // span) if pos.recovery_bar is not None else 1
        verdict, note = recovery.judge(
            pos.direction,
            pos.entry_price,
            {
                "close": _finite(bar.get("close")),
                "high": _finite(bar.get("high")),
                "low": _finite(bar.get("low")),
                "sma_21": _finite(bar.get("sma_21")),
                "atr_14": _finite(bar.get("atr_14")),
            },
            float(pos.recovery_loss),
            held,
            cfg,
        )
        if verdict == "hold":
            self._signals[symbol] = f"{symbol} recovery hold — {note}"
            return
        live = (pos.mode or "PAPER").upper() == "LIVE"
        if verdict == "abort":
            self._recovery_blocked.add(symbol)
            await self._exit_now(cfg, self.ltp, recovery.ABORT_EXIT)
            self._signals[symbol] = f"{symbol} emergency recovery exit — {note}; not traded again today"
            self._alert(f"{symbol} emergency recovery exit — {note}. Not traded again on this bot today.")
            return
        await self._exit_now(cfg, self.ltp, recovery.TARGET_EXIT)
        if self.position is not None:
            return  # the exit did not go through
        text = f"{symbol} recovery target hit — {note}"
        if recovery.handoff_on(cfg) and self.handoff is not None:
            text += f"; {await self._hand_to_chop_bot(symbol, live, cfg, pos.direction)}"
            self._alert(text)
        self._signals[symbol] = text

    async def _hand_to_chop_bot(self, symbol: str, live: bool, cfg: BotConfig | None = None, closed: str | None = None) -> str:
        """Move a stock that just closed to Bot 2 (recovery.handoff); in LIVE only once Groww shows no shares.

        With `handoff_now` Bot 2 then orders at once the opposite way of the trade that closed (`closed`,
        its real side): no filters, its own stop and exits (recovery.order_now)."""
        if live and await self._groww_net(symbol) != 0:
            return "no handoff: Groww still shows shares"
        moved = self.handoff(self.bot_id, symbol, live)
        if (
            moved.startswith("moved")
            and closed in ("LONG", "SHORT")
            and cfg is not None
            and recovery.handoff_now_on(cfg)
            and self.handoff_order is not None
        ):
            moved += "; " + self.handoff_order(recovery.HANDOFF_BOT, symbol, _opposite(closed))
        return moved

    async def _watch_breakout(self, symbol: str, cfg: BotConfig, enriched: pd.DataFrame) -> bool:
        """A breakout candle on the chop bot: cut the trade, hand the stock to Bot 3 (breakout.py).

        True when the stock was handled here (cut and/or handed off), so the cross is not judged.
        """
        if not breakout.on(cfg) or self.handoff is None or self.bot_id == breakout.TREND_BOT:
            return False
        hit, note = breakout.read(enriched, cfg)
        if not hit:
            return False
        pos = self.positions.get(symbol)
        live = False
        if pos is not None:
            live = (pos.mode or "PAPER").upper() == "LIVE"
            if live and pos.stop_active and not pos.sl_order_id:
                # Restored after a restart without its stop id: left to that stop and the square-off.
                return False
            await self._exit_now(cfg, self.ltp, breakout.BREAKOUT_EXIT)  # the focus is this stock
            if self.positions.get(symbol) is not None:
                self._signals[symbol] = f"{symbol} breakout ({note}) — the exit did not go through"
                return True
        else:
            live = (getattr(cfg, "trading_mode", None) or "PAPER").upper() == "LIVE"
        flat = True
        if live:
            flat = await self._groww_net(symbol) == 0
        moved = (
            self.handoff(self.bot_id, symbol, live, to_bot=breakout.TREND_BOT)
            if flat
            else "no handoff: Groww still shows shares"
        )
        if moved.startswith("moved") and self.breakout_arrival is not None:
            bar = enriched.iloc[-2]
            want, limit = breakout.side(bar), None
            if want is not None:
                limit = tall_pullback.level(bar, want)
            if want is not None and limit is not None:
                arrival = breakout.Arrival(direction=want, limit=float(limit), made_at=self._now().timestamp())
                moved += "; " + self.breakout_arrival(breakout.TREND_BOT, symbol, arrival)
        text = f"{symbol} trend breakout — {note}" + ("; trade cut" if pos is not None else "") + f"; {moved}"
        self._signals[symbol] = text
        self.last_signal = text
        self._alert(text)
        return True

    def queue_breakout_entry(self, symbol: str, arrival: breakout.Arrival) -> None:
        """A breakout moved this stock here: enter on its side on the next pass (breakout.py)."""
        self._arrivals[(symbol or "").upper()] = arrival

    async def _enter_on_arrival(
        self, symbol: str, arrival: breakout.Arrival, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime
    ) -> str:
        """Bot 3's entry on a breakout's side: every check applies; it waits at the pullback level."""
        side = "BUY" if arrival.direction == "LONG" else "SELL"
        if now.timestamp() - arrival.made_at > breakout.ARRIVAL_TTL_SEC:
            text = f"breakout {side} {symbol} dropped — it waited too long for this bot"
        else:
            enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
            closed = enriched.iloc[-2]
            closed_ts = _closed_bar_ts(enriched)
            atr = _finite(closed.get("atr_14"))
            if closed_ts is None or atr is None or atr <= 0:
                text = f"breakout {side} {symbol} dropped — ATR not ready"
            else:
                order = PullbackOrder(
                    direction=arrival.direction,
                    limit=round_price(symbol, arrival.limit),
                    cross_price=round_price(symbol, float(closed["close"])),
                    atr=atr,
                    signal_ts=int(closed_ts),
                    span_sec=candle_minutes(cfg, stock_overrides(cfg, symbol)) * 60,
                    candles=tall_pullback.expiry(cfg),
                    why="BREAKOUT_ARRIVAL",
                )
                self._focus = symbol
                self.ltp = self._ltps.get(symbol, self.ltp)
                signal = "BULLISH" if arrival.direction == "LONG" else "BEARISH"
                result = await self.apply_signal(
                    signal, enriched, cfg, now, alert=False, pullback_at=order, why="BREAKOUT_ARRIVAL"
                )
                text = f"breakout arrival — {result}"
        self._signals[symbol] = text
        self.last_signal = text
        return text

    async def _watch_pullbacks(self, cfg: BotConfig, now: dt.datetime) -> None:
        """Every pass: send a waiting pullback entry once the price reaches its level (tall_pullback.py)."""
        for symbol, order in list(self._pullback.items()):
            symbol_cfg = _cfg_for(cfg, symbol)
            real = order.side or order.direction
            side = "BUY" if real == "LONG" else "SELL"
            frame = self._frames.get(symbol)
            price = self._ltps.get(symbol)
            closed_ts = _closed_bar_ts(frame)
            why = None
            if symbol in self.positions:
                why = "already in a trade"
            elif not self._still_armed(symbol):
                why = "taken off the Trade list"
            elif now.time() >= _entry_cutoff(symbol_cfg):
                why = f"no new entries after {_entry_cutoff(symbol_cfg).strftime('%H:%M')} IST"
            elif order.invalid_at is not None and extreme_entry.broken(real, price, order.invalid_at):
                beyond = "under the signal candle's low" if real == "LONG" else "over the signal candle's high"
                why = f"{extreme_entry.OPPOSITE_BREAK}: {float(price):.2f} broke {beyond} ({order.invalid_at:.2f})"
            elif (
                order.cancel_on_cross
                and closed_ts is not None
                and closed_ts > order.signal_ts
                and extreme_entry.crossed_against(
                    order.direction,
                    order.sma_side,
                    extreme_entry.side_on_closed(frame, int(symbol_cfg.sma_fast), int(symbol_cfg.sma_slow)),
                )
            ):
                why = f"{extreme_entry.SMA_CROSS}: the SMAs crossed against the {order.direction.lower()} signal"
            elif tall_pullback.expired(order.signal_ts, closed_ts, order.span_sec, order.candles):
                why = (
                    f"no candle went the trade's way within {order.candles} candle(s)"
                    if order.confirm
                    else f"the price did not come back to {order.limit:.2f} within {order.candles} candle(s)"
                )
            elif self.trades_today >= int(symbol_cfg.max_trades_per_day):
                why = "trade cap reached"
            if why:
                self._pullback.pop(symbol, None)
                label = "entry-candle" if order.confirm else "limit" if order.invalid_at is not None else "pullback"
                self._signals[symbol] = f"{label} {side} {symbol} cancelled — {why}"
                continue
            if order.confirm:
                # The level follows the forming candle: its open ± the margin, the trade's way.
                forming_open = _finite(frame.iloc[-1].get("open")) if frame is not None and len(frame) else None
                if forming_open is not None:
                    order.limit = round_price(symbol, entry_candle.level(forming_open, real, order.confirm_pct))
                if not entry_candle.confirmed(real, price, order.limit):
                    continue
            elif not tall_pullback.touched(real, price, order.limit):
                continue
            async with self.lock:
                if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                    continue
                self._pullback.pop(symbol, None)
                self._focus = symbol
                self.ltp = float(price)
                self.inflight = "TRANSIT"
                try:
                    how = "CANDLE_CONFIRM" if order.confirm else "EXTREME_LIMIT" if order.invalid_at is not None else "PULLBACK"
                    await self._open(
                        order.direction, order.cross_price, order.atr, symbol_cfg, now, why=f"{order.why}:{how}"
                    )
                    if order.confirm:
                        colour = "green" if real == "LONG" else "red"
                        result = f"opened {real} — the entry candle turned {colour} ({float(price):.2f} past {order.limit:.2f})"
                    else:
                        level = "the candle-extreme limit" if order.invalid_at is not None else "the pullback level"
                        result = f"opened {real} — price came back to {level} {order.limit:.2f}"
                except SlCancelFailed as exc:
                    self._note_broker_block(exc)
                    result = f"pullback {side} not filled — {exc}"
                finally:
                    self.inflight = None
            self._signals[symbol] = result
            self.last_signal = result

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

    async def _manual_ready(self, name: str, cfg: BotConfig) -> tuple[pd.DataFrame, float, float, float]:
        """Candles, price, reference close and ATR for an order that does not wait for a cross (Force / news)."""
        frame = self._frames.get(name)
        if frame is None or getattr(frame, "empty", True):
            try:
                ltp, frame, source = await self.broker.refresh(name)
            except Exception as exc:  # noqa: BLE001
                raise ForceRefused(str(exc)) from exc
            if frame is None or getattr(frame, "empty", True):
                raise ForceRefused(f"{name} has no candles yet")
            frame = resample(frame, candle_minutes(cfg, stock_overrides(cfg, name)))
            self._frames[name] = frame
            self._ltps[name] = float(ltp)
            if name == (cfg.symbol or "").upper():
                self.ltp = float(ltp)
                self.last_error = ""
                self.data_source = source
        own = _cfg_for(cfg, name)
        enriched = enrich(frame, own.sma_fast, own.sma_slow, own.atr_period, getattr(own, "sma_medium", None))
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
        return enriched, price, reference, atr

    async def news_order(self, symbol: str, signal: str, note: str = "") -> str:
        """Enter at once on a news headline (news_entry.py): `signal` BULLISH buys, BEARISH shorts."""
        return await self.direct_order(symbol, signal, why="NEWS", note=note)

    async def direct_order(self, symbol: str, signal: str, why: str, note: str = "") -> str:
        """Enter at once, `signal` BULLISH buys and BEARISH shorts (a news entry, or a hand-off arrival).

        No SMA cross, candle pattern or entry filter is read, and no pullback / candle wait:
        the order goes now. Only a RUNNING bot takes it. Market hours, the entry cut-off, the
        Trade list, the halt / loss lock, the trade cap and (in `_open`) one LIVE bot per stock
        still apply. The news side is the order side (the flip setting is not applied). An
        opposite position is closed and reversed like an opposite cross (held if the SMA cross
        exit is off). After that the bot's own exits manage the trade: stop / trailing stop /
        target, the SMA cross, candle colour or pattern, the other exits and the square-off.
        """
        if signal not in ("BULLISH", "BEARISH"):
            raise ForceRefused("no direction")
        if self.status != "RUNNING":
            raise ForceRefused(f"bot is {self.status}, not RUNNING")
        name = (symbol or "").upper().strip()
        if not is_nse_symbol(name):
            raise ForceRefused(f"{name or 'stock'} is not an NSE symbol")
        want = "LONG" if signal == "BULLISH" else "SHORT"
        held = self.positions.get(name)
        if held is not None and held.direction == want:
            raise ForceRefused(f"{name} is already {want}")
        cfg = self.load_config()
        clock = self._now()
        if not market_is_open(clock):
            raise ForceRefused("market is closed")
        if clock.time() >= _parse_hhmm(cfg.square_off_time):
            raise ForceRefused(f"past the {cfg.square_off_time} IST square-off")
        if clock.time() >= _entry_cutoff(cfg):
            raise ForceRefused(f"no new entries after {_entry_cutoff(cfg).strftime('%H:%M')} IST")
        if name not in trade_names(cfg):
            raise ForceRefused(f"{name} is not on the Trade list")
        enriched, price, reference, atr = await self._manual_ready(name, cfg)
        own = _cfg_for(cfg, name)
        own.flip_orders = False
        # A cross already on the tape must not turn the news trade straight round.
        self.hold_for_next_cross([name])
        self.ltp = price
        now = self._now()
        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                raise ForceRefused("blocked — order PENDING/TRANSIT")
            self.inflight = "TRANSIT"
            self._focus = name
            try:
                result = await self._apply_locked(
                    signal=signal,
                    cross_price=price,
                    atr=atr,
                    adx_blocks_entry=False,
                    cfg=own,
                    now=now,
                    why=why,
                )
            except SlCancelFailed as exc:
                self.last_error = str(exc)
                self._order_refused(name, str(exc))
                raise ForceRefused(str(exc)) from exc
            finally:
                self.inflight = None
        self._signals[name] = result
        self._log_decision(
            signal, enriched, own, now, result,
            note=f"{why} entry: no SMA cross, pattern or entry filter is read" + (f". {note}" if note else "."),
        )
        return result

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
        if not is_nse_symbol(name):
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
        enriched, price, reference, atr = await self._manual_ready(name, cfg)
        side = _live_ma_side(enriched)
        if side is None:
            raise ForceRefused(f"{name} has no SMA yet")
        own = _cfg_for(cfg, name)
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
                    why="FORCE_ORDER",
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

    async def close_symbol(self, symbol: str, reason: str = "MANUAL_CLOSE", trade_id: int | None = None) -> str:
        """Close one open book. Does not stop the bot or touch the other stocks.

        `trade_id` (the review's EXIT): close only that trade; if the stock now
        holds another trade, or none, nothing is sent.
        """
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
                if trade_id is not None and pos.trade_id != trade_id:
                    raise ForceRefused(f"{name}: that trade has already closed")
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
                        self.position, px, reason, self._now(), _cfg_for(cfg, name)
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

    async def _open(
        self,
        direction: str,
        cross_price: float,
        atr: float,
        cfg: BotConfig,
        now: dt.datetime,
        why: str = "SMA_CROSS",
    ) -> None:
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
        practice = (cfg.trading_mode or "PAPER").upper()
        if self.uses_wallet and practice in ("PAPER", "REPLAY"):
            # Practice wallet: block the margin, borrowing any shortfall. Never stops the order.
            try:
                loan = paper_wallet.cover(cfg.symbol, qty, order_price, bot=self.bot_id, replay=practice == "REPLAY")
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
            tsl_points, tsl_step, tgt_points = tsl_settings(cfg, entry_price=fill)
            sl_tsl, tp_tsl = tsl_entry_levels(direction, fill, tsl_points, tgt_points)
            sl = round_price(cfg.symbol, sl_tsl)
            target = round_price(cfg.symbol, tp_tsl) if tp_tsl is not None else None
        st_stop = supertrend.stop_on(cfg)
        st_bar = None
        if st_stop:
            frame = self._frames.get((cfg.symbol or "").upper())
            closed = frame.iloc[:-1] if frame is not None and len(frame) > 1 else None
            line = supertrend.stop_level(closed, direction, cfg)
            # The line only when it is on the safe side of the fill; else the ATR stop until it is.
            if line is not None and ((direction == "LONG" and line < fill) or (direction == "SHORT" and line > fill)):
                sl = round_price(cfg.symbol, line)
            st_bar = _closed_bar_ts(frame)
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
            entry_reason=why,
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
            st_stop=st_stop,
            st_stop_bar=st_bar,
            high=fill,
            low=fill,
            flipped=flipped,
            entry_why=why or "",
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
            self._alert(f"PalTra ALERT\n{symbol} has no stop. {self.last_error}", critical=True, call=True)
            return
        self.position = None
        self.last_error = f"Stop refused ({why}). {symbol} was closed at once."
        self._signals[symbol] = f"{symbol} closed — stop was refused"

    def _closed_gap_pct(self, symbol: str, cfg: BotConfig) -> float | None:
        """SMA 9 vs SMA 21 gap % on this stock's last closed candle."""
        frame = self._frames.get((symbol or "").upper())
        if frame is None or getattr(frame, "empty", True) or len(frame) < 2:
            return None
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
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
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
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
        await self._move_stop(pos, symbol, moved, "Trailing stop")

    async def _trail_supertrend(self, cfg: BotConfig) -> None:
        """Once per newly closed candle: move a Supertrend stop to the line (tighter only).

        The line is read on the bot's own closed candles; it only counts while it guards the real
        position (under a long, above a short). LIVE moves the exchange stop like the trailing stop.
        """
        pos = self.position
        if pos is None or not pos.st_stop or not pos.stop_active:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or closed_ts == pos.st_stop_bar:
            return
        line = supertrend.stop_level(frame.iloc[:-1], pos.direction, cfg)
        if line is None:
            pos.st_stop_bar = closed_ts
            return
        moved = round_price(symbol, tighten(pos.direction, pos.sl_trigger, line))
        if moved == pos.sl_trigger:
            pos.st_stop_bar = closed_ts
            return
        if await self._move_stop(pos, symbol, moved, "Supertrend stop"):
            pos.st_stop_bar = closed_ts

    async def _move_stop(self, pos: OpenPosition, symbol: str, moved: float, label: str) -> bool:
        """Move an open trade's stop to `moved` (already tighter). LIVE modifies the exchange stop first;
        a refused modify leaves the old stop in place (still live). Saved so a restart keeps it."""
        if (pos.mode or "PAPER").upper() == "LIVE":
            if not pos.sl_order_id:
                # Restored after a restart: the exchange stop id is unknown,
                # so it stays where it is.
                return False
            if time.monotonic() - pos.tsl_modified_at < 2.0:
                return False
            pos.tsl_modified_at = time.monotonic()
            modify = getattr(self.broker, "modify_sl", None)
            if modify is None:
                return False
            side = "SELL" if pos.direction == "LONG" else "BUY"
            try:
                await modify(pos.sl_order_id, symbol, side, pos.qty, moved)
            except Exception as exc:  # noqa: BLE001
                self.last_error = f"{label} not moved ({exc}). The stop at {pos.sl_trigger} is still live."
                return False
        pos.sl_trigger = moved
        # Saved so a restart keeps the trailed stop, not the entry one.
        with session_factory()() as db:
            row = db.get(TradeLog, pos.trade_id)
            if row is not None and row.exit_time is None:
                row.sl_trigger_price = moved
                db.commit()
        return True

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
            fill_price = await self._real_exit_price(pos, symbol)
        elif (cfg.trading_mode or "").upper() == "LIVE" and pos.sl_order_id:
            status = (await self.broker.get_order_status(pos.sl_order_id)).upper()
            if status in TERMINAL_FILLED:
                hit = True
                # Groww's own fill price; the trigger only when Groww gives none.
                fill_price = await self._real_exit_price(pos, symbol)
            elif status in STOP_DEAD:
                # Groww cancelled or rejected the stop: it will never sell these shares.
                await self._stop_dead(cfg, pos, symbol, status)
                return
            elif status not in IN_FLIGHT:
                # Groww reports a filled stop as EXECUTED. If that word was
                # missed, a flat MIS book is the same fact: the shares are gone.
                opened = pos.entry_time
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=IST)
                age = (self._now() - opened).total_seconds()
                if age >= 20 and await self._groww_net(cfg.symbol) == 0:
                    hit = True
                    fill_price = await self._real_exit_price(pos, symbol)
        elif pos.stop_active:
            if pos.direction == "LONG" and self.ltp <= pos.sl_trigger:
                hit = True
                fill_price = self.ltp
            elif pos.direction == "SHORT" and self.ltp >= pos.sl_trigger:
                hit = True
                fill_price = self.ltp
        reason = "GAP_SL_HIT" if pos.trailing else ("TSL_HIT" if pos.tsl_step else ("ST_SL_HIT" if pos.st_stop else "ATR_SL_HIT"))
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

    async def _real_exit_price(self, pos: OpenPosition, symbol: str) -> float:
        """The price Groww really closed a LIVE position at: the stop's own fill, else Groww's average
        closing price for the stock today, else the stop trigger (the last resort it used to be)."""
        read = getattr(self.broker, "read_order", None)
        if pos.sl_order_id and read is not None:
            try:
                status, price = await read(pos.sl_order_id)
            except Exception:  # noqa: BLE001
                status, price = "", None
            if price and (status or "").upper() in TERMINAL_FILLED:
                return float(price)
        closed = getattr(self.broker, "closed_price", None)
        if closed is not None:
            try:
                price = await closed(symbol, pos.direction)
            except Exception:  # noqa: BLE001
                price = None
            if price:
                return float(price)
        return float(pos.sl_trigger or self.ltp)

    async def _stop_dead(self, cfg: BotConfig, pos: OpenPosition, symbol: str, status: str) -> None:
        """LIVE: Groww cancelled or rejected the exchange stop (STOP_DEAD).

        * Groww holds no shares: something other than this stop closed them (Groww's own intraday
          square-off, or a sell in the Groww app). Book the trade at Groww's real price as
          SOLD_ON_GROWW. No order is sent: one would open the other side.
        * Groww still holds them: the position has no stop. Alert with a call at once, wait
          STOP_DEAD_GRACE_SEC (Groww's square-off cancels the stop just before it sells), then close
          it at the market as STOP_LOST if Groww still holds it.
        A failed read of Groww's book does nothing.
        """
        net = await self._groww_net(symbol)
        if net is None:
            return
        if net == 0:
            price = await self._real_exit_price(pos, symbol)
            pos.sl_order_id = ""
            self._signals[symbol] = f"{symbol} closed on Groww outside the bot at {price:.2f}; stop was {status}"
            self._alert(
                f"{symbol}: Groww shows the position closed, but not by the bot's stop (stop {status}). "
                f"Booked at Groww's price {price:.2f}."
            )
            await self._exit_now(cfg, price, STOP_SOLD_OUTSIDE)
            return
        now = self._now()
        if pos.stop_dead_at is None:
            pos.stop_dead_at = now
            remark = ""
            note = getattr(self.broker, "order_remark", None)
            if note is not None:
                try:
                    remark = await note(pos.sl_order_id)
                except Exception:  # noqa: BLE001
                    remark = ""
            text = (
                f"{symbol} has NO STOP: Groww {status.lower()} the stop-loss order"
                f"{f' ({remark})' if remark else ''}. The bot will close the position in "
                f"{STOP_DEAD_GRACE_SEC} seconds if Groww still holds it."
            )
            self._signals[symbol] = text
            self.last_error = text
            self._alert(text, critical=True, call=True)
            return
        if (now - pos.stop_dead_at).total_seconds() < STOP_DEAD_GRACE_SEC:
            return
        pos.sl_order_id = ""
        self._signals[symbol] = f"{symbol} closed at the market: its Groww stop was {status.lower()}"
        await self._exit_now(cfg, self.ltp, STOP_LOST)

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

    async def _watch_counter_candles(self, cfg: BotConfig) -> None:
        """Once per closed candle: exit after N candles against the trade (counter_candles.py)."""
        pos = self.position
        count = counter_candles.exit_count(cfg)
        if pos is None or self.status != "RUNNING" or count <= 0 or self.ltp <= 0:
            return
        if recovery.slow_exit_on(cfg):
            return  # the slow SMA exit replaces it: minor candles against the trade are ignored
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.cc_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the Bollinger exit).
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        if closed_ts + 60 <= opened.timestamp():
            return
        pos.cc_bar = closed_ts
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
        closed = enriched.iloc[:-1]
        # Only candles that closed after the entry count against it.
        after = closed[closed["ts"].astype("int64") + 60 > int(opened.timestamp())]
        if len(after) < count:
            return
        towards = counter_candles.against(pos.signal_direction)
        hit, note = counter_candles.run_towards(
            closed,
            towards,
            count,
            counter_candles.rule(cfg),
            counter_candles.need_fast(cfg),
            counter_candles.min_body(cfg),
        )
        if not hit:
            return
        self._signals[symbol] = f"{symbol} counter-candle exit — {note}"
        side = pos.signal_direction
        await self._exit_now(cfg, self.ltp, "COUNTER_CANDLES")
        if counter_candles.reenter_on(cfg) and symbol not in self.positions:
            run = join_trend.side_start(enriched)
            level = counter_candles.exit_level(closed.iloc[-1], side)
            # Only while the SMAs are still on the trade's side; an exit on the cross itself waits for that cross.
            if run is not None and run[0] == side and level is not None:
                self._reentry[symbol] = (side, int(run[1]), level, int(closed_ts))

    async def _watch_ema_exit(self, cfg: BotConfig) -> None:
        """Once per closed candle: exit when the EMAs cross against the trade (ema_filter.py)."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or not ema_filter.exit_on(cfg) or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.ema_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the Bollinger exit).
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        minutes = candle_minutes(cfg, stock_overrides(cfg, symbol))
        if closed_ts + minutes * 60 <= opened.timestamp():
            # That candle had closed before the entry: a cross on it is not "after the entry".
            return
        pos.ema_bar = closed_ts
        hit, note = ema_filter.crossed_against(frame.iloc[:-1], pos.signal_direction, cfg)
        if not hit:
            return
        self._signals[symbol] = f"{symbol} EMA cross exit — {note}"
        await self._exit_now(cfg, self.ltp, "EMA_CROSS")

    async def _watch_exit_candle(self, cfg: BotConfig) -> None:
        """Once per closed exit candle: the SMA cross exit read on another candle size (exit_candle.py)."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        if not exit_candle.active(cfg, candle_minutes(cfg, stock_overrides(cfg, symbol))):
            return
        size = exit_candle.minutes(cfg)
        frame = exit_candle.closed_frame(self._tapes.get(symbol), size, int(cfg.sma_fast), int(cfg.sma_slow))
        if frame is None or frame.empty:
            return
        closed_ts = int(frame.iloc[-1]["ts"])
        if pos.xc_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the Bollinger exit).
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        if closed_ts + size * 60 <= opened.timestamp():
            # That exit candle had closed before the entry: a cross on it is not "after the entry".
            return
        pos.xc_bar = closed_ts
        own = self._frames.get(symbol)
        own_closed = own.iloc[:-1] if own is not None and len(own) > 1 else None
        hit, note = exit_candle.judge(frame, own_closed, pos.signal_direction, int(cfg.sma_fast), int(cfg.sma_slow))
        if not hit:
            return
        self._signals[symbol] = f"{symbol} {size}-minute exit candle — {note}"
        await self._exit_now(cfg, self.ltp, exit_candle.REASON)

    async def _watch_slope_exit(self, cfg: BotConfig) -> None:
        """Once per closed candle: exit when the SMA 9 bends or turns against the trade (slope_exit.py)."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or not slope_exit.watched(cfg) or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        minutes = candle_minutes(cfg, stock_overrides(cfg, symbol))
        size = slope_exit.own_size(cfg, minutes)
        if size:
            # Slope candle (slope_minutes): closed candles of that size from the 1-minute tape.
            closed = exit_candle.closed_frame(self._tapes.get(symbol), size, int(cfg.sma_fast), int(cfg.sma_slow))
            minutes = size
        else:
            own = self._frames.get(symbol)
            closed = own.iloc[:-1] if own is not None and len(own) > 1 else None
        if closed is None or closed.empty:
            return
        closed_ts = int(closed.iloc[-1]["ts"])
        if pos.slope_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the Bollinger exit).
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        if closed_ts + minutes * 60 <= opened.timestamp():
            # That candle had closed before the entry: its slope is not "since the entry".
            return
        pos.slope_bar = closed_ts
        value = slope_exit.slope(closed, int(cfg.sma_fast), slope_exit.lookback(cfg))
        if value is None:
            return
        hit = slope_exit.judge(pos.signal_direction, value, pos.slope_state, cfg) if slope_exit.on(cfg) else None
        if slope_exit.vturn_on(cfg):
            # Read every candle so its history is whole, even when the bend / turn already hit.
            vturn = slope_exit.judge_vturn(pos.signal_direction, value, pos.slope_state, cfg)
            hit = hit or vturn
        if hit is None:
            return
        reason, note = hit
        if turn_entry.exit_candle_on(cfg) and not turn_entry.candle_against(closed.iloc[-1], pos.signal_direction):
            # Wait for a candle of the other colour (turn_exit_candle); the next candle is judged again.
            colour = "red" if pos.signal_direction == "LONG" else "green"
            self._signals[symbol] = f"{symbol} {note} — held until a {colour} candle closes"
            return
        self._signals[symbol] = f"{symbol} SMA 9 slope exit — {note}"
        price, live = self.ltp, (pos.mode or "PAPER").upper() == "LIVE"
        await self._exit_now(cfg, price, reason)
        if (
            reason == slope_exit.VTURN_EXIT
            and slope_exit.vturn_handoff_on(cfg)
            and self.handoff is not None
            and self.position is None
            and slope_exit.lost(pos.direction, pos.entry_price, price)
        ):
            # A losing V-turn: the stock goes to the chop bot, like a recovery (vturn_handoff).
            text = f"{symbol} SMA 9 V-turn exit at a loss; {await self._hand_to_chop_bot(symbol, live, cfg, pos.direction)}"
            self._signals[symbol] = text
            self._alert(text)

    async def _watch_turn_colour(self, cfg: BotConfig) -> None:
        """A trade opened by an SMA 9 turn closes on the first candle of the other colour closed after its entry
        (turn_colour_exit, turn_entry.py): red for a long, green for a short. No reverse."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or not turn_entry.colour_exit_on(cfg) or self.ltp <= 0:
            return
        if not pos.entry_why.startswith(turn_entry.ENTRY_REASON):
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.tc_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            return  # restored after a restart without its stop id: left to that stop
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        if opened.timestamp() - closed_ts > TURN_COLOUR_GRACE_SEC:
            # The candle had opened before the entry (or the trade bought partway into it): its colour is
            # mostly the price before the trade, so only candles that open with or after the entry count.
            return
        pos.tc_bar = closed_ts
        if not turn_entry.candle_against(frame.iloc[-2], pos.signal_direction):
            return
        colour = "red" if pos.signal_direction == "LONG" else "green"
        self._signals[symbol] = f"{symbol} SMA 9 turn trade closed on a {colour} candle"
        await self._exit_now(cfg, self.ltp, turn_entry.COLOUR_EXIT)

    def _pivots(self, symbol: str, day: dt.date) -> dict | None:
        """Classic pivots for `day` from the stock's previous session (pivots.py); None until that day is loaded."""
        key = ((symbol or "").upper(), day)
        hit = self._pivot_cache.get(key)
        if hit is not None:
            return hit
        frame = self._tapes.get(key[0])
        if frame is None or getattr(frame, "empty", True):
            frame = self._frames.get(key[0])
        found = pivots.levels(frame, day)
        if found is not None:
            self._pivot_cache[key] = found
        return found

    async def _watch_pivot_target(self, cfg: BotConfig) -> None:
        """Every price: book the trade's profit at its pivot level (pivot_target, pivots.py)."""
        pos = self.position
        mode = pivots.target_mode(cfg)
        if pos is None or mode == "OFF" or self.status != "RUNNING" or self.ltp <= 0:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the other exits).
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        goal = pivots.target_for(pos.direction, pos.entry_price, self._pivots(symbol, opened.astimezone(IST).date()), mode)
        if goal is None or not pivots.reached(pos.direction, self.ltp, goal[1]):
            return
        self._signals[symbol] = f"{symbol} pivot target {goal[0]} {goal[1]:.2f} reached"
        await self._exit_now(cfg, self.ltp, pivots.PIVOT_EXIT)

    async def _watch_supertrend_exit(self, cfg: BotConfig) -> None:
        """Once per closed candle: exit when the Supertrend flips against the trade (supertrend.py)."""
        pos = self.position
        if pos is None or self.status != "RUNNING" or not supertrend.exit_on(cfg) or self.ltp <= 0:
            return
        symbol = (cfg.symbol or self._focus or "").upper()
        frame = self._frames.get(symbol)
        closed_ts = _closed_bar_ts(frame)
        if closed_ts is None or pos.st_bar == closed_ts:
            return
        if (pos.mode or "PAPER").upper() == "LIVE" and pos.stop_active and not pos.sl_order_id:
            # Restored after a restart without its stop id: left to that stop (as the Bollinger exit).
            return
        opened = pos.entry_time if pos.entry_time.tzinfo else pos.entry_time.replace(tzinfo=IST)
        minutes = candle_minutes(cfg, stock_overrides(cfg, symbol))
        if closed_ts + minutes * 60 <= opened.timestamp():
            # That candle had closed before the entry: a flip on it is not "after the entry".
            return
        pos.st_bar = closed_ts
        hit, note = supertrend.flipped_against(frame.iloc[:-1], pos.signal_direction, cfg)
        if not hit:
            return
        self._signals[symbol] = f"{symbol} Supertrend exit — {note}"
        await self._exit_now(cfg, self.ltp, "SUPERTREND")

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
                        reason = "TSL_HIT" if self.position.tsl_step else ("ST_SL_HIT" if self.position.st_stop else "ATR_SL_HIT")
                        fill_price = await self._real_exit_price(self.position, (cfg.symbol or "").upper())
                # SL already fired (or paper touch). Do not place a reverse.
                self.position.sl_order_id = ""
                await self._close_position(self.position, fill_price, reason, self._now(), cfg)
                self.position = None
                self.last_signal = {
                    "TARGET_HIT": "target hit — flat",
                    "GAP_SL_HIT": "moving stop hit — flat",
                    "TSL_HIT": "trailing stop hit — flat",
                    "ST_SL_HIT": "Supertrend stop hit — flat",
                    "BB_TARGET": "Bollinger band target — flat",
                    "BB_MIDDLE": "Bollinger middle band exit — flat",
                    "GAP_FADE": "SMA gap faded — flat",
                    "CANDLE_END": "candle closed — flat",
                    "COLOUR_TURN": "candle turned the other colour — flat",
                    "PIVOT_TARGET": "pivot target reached — flat",
                    "COUNTER_CANDLES": "candles turned against the trade — flat",
                    "EMA_CROSS": "EMAs crossed against the trade — flat",
                    "SUPERTREND": "Supertrend turned against the trade — flat",
                    "SMA9_BEND": "SMA 9 bent — flat",
                    "SMA9_TURN": "SMA 9 turned against the trade — flat",
                    "SMA9_VTURN": "SMA 9 V-turn — flat",
                    "SMA9_TURN_AGAINST": "SMA 9 turned against the trade — flat",
                    "SLOW_SMA_BREACH": "closed beyond the slow SMA — flat",
                    "RECOVERY_TARGET_HIT": "recovery target hit — flat",
                    "EMERGENCY_RECOVERY_ABORT": "emergency recovery exit — flat",
                    "SQUARE_OFF_CUTOFF": "recovery hold closed at the cut-off — flat",
                    "BREAKOUT_STOP": "trend breakout — flat",
                    STOP_LOST: "Groww dropped the stop — closed at the market",
                    STOP_SOLD_OUTSIDE: "closed on Groww outside the bot — booked at Groww's price",
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
        self._pullback.clear()
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
        send_exit = not (live and reason in ("ATR_SL_HIT", "TSL_HIT", "ST_SL_HIT", STOP_SOLD_OUTSIDE))
        if send_exit and live:
            net = await self._groww_net(cfg.symbol)
            if net is None:
                raise SlCancelFailed(
                    f"Could not read {cfg.symbol} on Groww, so no exit was sent"
                )
            if (pos.direction == "LONG" and net <= 0) or (pos.direction == "SHORT" and net >= 0):
                if net == 0 and reason == STOP_LOST:
                    # Groww closed it in the moment between the dead stop and this exit
                    # (its own square-off): book Groww's real price, send nothing.
                    px = round_price(cfg.symbol, await self._real_exit_price(pos, cfg.symbol))
                    buy, sell = legs_for(pos.direction, pos.entry_price, px)
                    costs = calculate_charges(buy, sell, pos.qty)
                    self._finalize_trade(pos.trade_id, px, STOP_SOLD_OUTSIDE, now, costs, extremes=(pos.high, pos.low))
                    return
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
        entry_reason: str | None = None,
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
                entry_reason=entry_reason,
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
        kpis = self._kpis(self._realized_mode)
        self.realized_net = float(kpis["net"])
        self.realized_gross = float(kpis["actual_gross"])
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
        # The day's normal end and a panic the person pressed are not worth a phone call.
        ring = status != "DAY_COMPLETED" and not (reason or "").startswith("Manual")
        self._alert(bot_down_alert(status, reason, self._now()), critical=True, call=ring)

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
            # The chart stock's own stop numbers (its overrides over the shared row).
            "stop_points": _stop_points(view_cfg),
            "flip_orders": flips_orders(view_cfg) if view_cfg is not None else False,
            "cross_exit": cross_exits(view_cfg) if view_cfg is not None else True,
            # Candle interval in minutes the chart stock trades on (candles.py).
            "candle_minutes": candle_minutes(view_cfg) if view_cfg is not None else 1,
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
            "reviews": [dict(item) for item in self.reviews],
        }

    # --- 1-minute human review (review.py) ---------------------------------

    def _load_reviews(self, cfg: BotConfig) -> None:
        """Once per engine: the recent reviews of this book, so a restart keeps them."""
        if self._reviews_loaded:
            return
        self._reviews_loaded = True
        run_id = getattr(self, "run_id", None)
        try:
            with session_factory()() as db:
                query = db.query(ReviewLog).filter(
                    ReviewLog.bot == self.bot_id, ReviewLog.mode == (cfg.trading_mode or "PAPER").upper()
                )
                query = query.filter(ReviewLog.run_id == run_id) if run_id is not None else query.filter(ReviewLog.run_id.is_(None))
                rows = query.order_by(ReviewLog.id.desc()).limit(REVIEWS_KEPT).all()
                self.reviews = [_review_dict(row) for row in reversed(rows)]
        except Exception:  # noqa: BLE001
            self.reviews = []

    def _set_review(self, item: dict, status: str, note: str) -> None:
        """Record an answer (or that none came). Saved before any order is sent."""
        at = self._now()
        item.update(status=status, note=note, action_at=at.replace(tzinfo=None).isoformat(timespec="seconds"))
        try:
            with session_factory()() as db:
                row = db.get(ReviewLog, int(item["id"]))
                if row is not None:
                    row.status, row.note, row.action_at = status, note, at.replace(tzinfo=None)
                    db.commit()
        except Exception:  # noqa: BLE001
            logger.exception("could not save review %s", item.get("id"))

    def _expire_reviews(self, symbol: str | None = None, note: str = "trade closed before an answer") -> None:
        """A pending review whose trade has closed (or, for `symbol`, any pending one) ends unanswered."""
        for item in self.reviews:
            if item["status"] != review.PENDING:
                continue
            if symbol is not None:
                if item["symbol"] == symbol:
                    self._set_review(item, review.NO_RESPONSE, note)
                continue
            pos = self.positions.get(item["symbol"])
            if pos is None or pos.trade_id != item["trade_id"]:
                self._set_review(item, review.NO_RESPONSE, note)

    async def _review_positions(self, cfg: BotConfig, now: dt.datetime) -> None:
        """Once per newly closed candle of each held stock: raise a review if its SMAs are uncertain.

        Never closes, delays or changes anything; the strategy has already run.
        """
        for symbol, pos in list(self.positions.items()):
            episode = self._review_episode.get(symbol)
            if episode is not None and episode.get("trade_id") != pos.trade_id:
                episode = None
                self._review_episode.pop(symbol, None)
            scfg = _cfg_for(cfg, symbol)
            if not review.review_on(scfg) or per_candle(scfg):
                continue
            minutes = candle_minutes(cfg, stock_overrides(cfg, symbol))
            if minutes < 2:
                continue  # a 1-minute strategy has nothing finer to show
            frame = self._frames.get(symbol)
            if frame is None or getattr(frame, "empty", True) or len(frame) < 3:
                continue
            closed_ts = _closed_bar_ts(frame)
            if closed_ts is None or _candle_is_behind(closed_ts, now, minutes):
                continue
            if self._review_bar.get(symbol) == closed_ts:
                continue
            self._review_bar[symbol] = closed_ts
            enriched = enrich(frame, scfg.sma_fast, scfg.sma_slow, scfg.atr_period, getattr(scfg, "sma_medium", None))
            band = review.gap_band(scfg)
            if episode is not None and episode.get("open") and review.episode_over(review.last_gap(enriched), band):
                episode["open"] = False
                self._expire_reviews(symbol, "SMA gap moved clear before an answer")
            if episode is not None and episode.get("open"):
                continue  # one review per uncertain stretch
            hit = review.uncertainty(enriched, pos.signal_direction, band, cross_exits(scfg))
            if hit is None:
                continue
            last = episode.get("last_at") if episode else None
            if last is not None and now - last < review.cooldown(scfg):
                continue
            # Smart check: read one or more shorter candles (1/2/3/5/10/15 min, built from
            # the 1-minute tape) and decide whether the main candle's "narrowing" really
            # looks like trouble. The combined verdict is reused in `_raise_review`, so
            # this does the work only once.
            minutes_list = review.check_minutes_list(scfg)
            evidence_by_minutes = review.check_candles_evidence(
                self._tapes.get(symbol),
                minutes_list,
                int(scfg.sma_fast),
                int(scfg.sma_slow),
                int(scfg.atr_period),
            )
            one = evidence_by_minutes.get(minutes_list[0])
            verdict = review.combined_verdict(evidence_by_minutes, pos.direction)
            mode = review.check_mode(scfg)
            if verdict is not None:
                if mode == "ONLY_IF_AGAINST" and verdict.get("label") != review.AGAINST:
                    continue  # The check candles disagree with the main one: don't fire.
                if mode == "ONLY_IF_NOT_WITH" and verdict.get("label") == review.WITH:
                    continue
            # Otherwise: not enough 1-minute tape to judge, so err on the safe side
            # and fire the review as today.
            self._review_episode[symbol] = {"trade_id": pos.trade_id, "open": True, "last_at": now}
            await self._raise_review(symbol, pos, scfg, minutes, hit, now, one=one, verdict=verdict)

    async def _raise_review(
        self,
        symbol: str,
        pos: OpenPosition,
        cfg: BotConfig,
        minutes: int,
        hit,
        now: dt.datetime,
        one: dict | None = None,
        verdict: dict | None = None,
    ) -> None:
        mode = (cfg.trading_mode or "PAPER").upper()
        band = review.gap_band(cfg)
        five = hit.as_dict()
        if one is None:
            # Caller did not read the check candle (older code paths, tests).
            minutes_list = review.check_minutes_list(cfg)
            evidence_by_minutes = review.check_candles_evidence(
                self._tapes.get(symbol), minutes_list, int(cfg.sma_fast), int(cfg.sma_slow), int(cfg.atr_period)
            )
            one = evidence_by_minutes.get(minutes_list[0])
            verdict = review.combined_verdict(evidence_by_minutes, pos.direction)
        if one is not None:
            # Saved with the evidence and shown on the card. Never read by the strategy.
            one["verdict"] = verdict
        text = review.review_text(
            symbol=symbol,
            mode=mode,
            direction=pos.direction,
            qty=int(pos.qty),
            entry=float(pos.entry_price),
            minutes=minutes,
            five=five,
            one=one,
            sma_fast=int(cfg.sma_fast),
            sma_slow=int(cfg.sma_slow),
            band=band,
        )
        default = review.default_answer(cfg)
        self._expire_reviews(symbol, "replaced by a newer review")
        if default == "EXIT":
            status, note_tail = review.AUTO_EXIT, "closed at once (default answer: Exit)"
        elif default == "CONTINUE":
            status, note_tail = review.AUTO_CONTINUE, "held (default answer: Continue)"
        else:
            status, note_tail = review.PENDING, "EXIT or WAIT (no answer keeps it open)"
        try:
            with session_factory()() as db:
                row = ReviewLog(
                    created_at=now.replace(tzinfo=None),
                    bot=self.bot_id,
                    mode=mode,
                    run_id=getattr(self, "run_id", None),
                    trade_id=int(pos.trade_id),
                    symbol=symbol,
                    direction=pos.direction,
                    candle_minutes=minutes,
                    five_min=json.dumps({**five, "band_pct": band, "sma_fast_len": int(cfg.sma_fast), "sma_slow_len": int(cfg.sma_slow)}),
                    one_min=json.dumps(one or {}),
                    message=text,
                    status=status,
                )
                db.add(row)
                db.commit()
                db.refresh(row)
                item = _review_dict(row)
        except Exception:  # noqa: BLE001
            logger.exception("could not save the review for %s", symbol)
            return
        item["entry_price"] = float(pos.entry_price)
        item["qty"] = int(pos.qty)
        self.reviews.append(item)
        # Keep every pending one; drop the oldest answered ones.
        while len(self.reviews) > REVIEWS_KEPT:
            old = next((i for i, r in enumerate(self.reviews) if r["status"] != review.PENDING), 0)
            self.reviews.pop(old)
        note = f"{symbol} review — SMA gap {hit.gap_pct:+.3f}% is uncertain; {note_tail}"
        self._signals[symbol] = note
        self.last_signal = note
        if default == "PROMPT":
            self._alert(text)
            callback = self.on_review
            if callback is not None:
                try:
                    callback(item)
                except Exception:  # noqa: BLE001
                    pass
        elif default == "EXIT":
            # Close only this trade; the stop and the other exits never change.
            try:
                await self.close_symbol(symbol, reason=review.AUTO_EXIT, trade_id=int(pos.trade_id))
            except Exception:  # noqa: BLE001
                logger.exception("auto-exit from the 1-minute review failed for %s", symbol)

    async def answer_review(self, review_id: int, action: str) -> dict:
        """EXIT or WAIT on a review. Only EXIT ever sends an order, and only for the reviewed trade."""
        action = (action or "").upper()
        if action not in ("EXIT", "WAIT"):
            raise ForceRefused("Answer EXIT or WAIT")
        async with self._review_lock:
            item = next((r for r in self.reviews if r["id"] == int(review_id)), None)
            if item is None:
                raise ForceRefused("That review is not on this desk")
            if item["status"] != review.PENDING:
                return {**item, "result": "Already answered — nothing sent"}
            symbol, trade_id = item["symbol"], item["trade_id"]
            pos = self.positions.get(symbol)
            if pos is None or pos.trade_id != trade_id:
                self._set_review(item, review.ALREADY_CLOSED, f"{action} after the trade had closed")
                return {**item, "result": f"{symbol}: that trade had already closed — nothing sent"}
            if action == "WAIT":
                self._set_review(item, review.WAIT, "kept open")
                text = f"{symbol} kept open — the strategy carries on"
                self._signals[symbol] = text
                return {**item, "result": text}
            # Marked first, so a second tap or another screen cannot send a second exit.
            self._set_review(item, review.EXIT, "exit sent")
            try:
                result = await self.close_symbol(symbol, reason=review.EXIT, trade_id=trade_id)
            except ForceRefused as exc:
                if "already closed" in str(exc) or "no open position" in str(exc):
                    self._set_review(item, review.ALREADY_CLOSED, "EXIT after the trade had closed")
                    return {**item, "result": f"{symbol}: that trade had already closed — nothing sent"}
                # Nothing closed: the review can be answered again.
                self._set_review(item, review.PENDING, f"exit refused: {exc}")
                raise
            if result.endswith("already flat"):
                self._set_review(item, review.ALREADY_CLOSED, "the stop closed it first")
            return {**item, "result": result}

    def _marker_book(self, cfg: BotConfig | None):
        """Chart markers come from this engine's own book only.

        Practice, real and replay trades share one table. Without this, every
        replay of the same day drew its entries again on the chart.
        """
        mode = ((cfg.trading_mode if cfg else None) or "PAPER").upper()
        if mode == "PAPER":
            return or_(TradeLog.mode == "PAPER", TradeLog.mode.is_(None))
        return TradeLog.mode == mode

    def _chart_parts(self, frame: pd.DataFrame, cfg, symbol: str = "", with_blocks: bool = True) -> tuple[list[dict], list[dict]]:
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
        ema = ema_filter.lengths(cfg) if cfg is not None and (ema_filter.entry_on(cfg) or ema_filter.exit_on(cfg)) else None
        st = supertrend.settings(cfg) if cfg is not None and (supertrend.entry_on(cfg) or supertrend.exit_on(cfg)) else None
        rows = candle_rows(frame, ema, st)[:-1]
        # Refused crosses belong to the bot's own candle; another chart size shows none.
        blocks = filter_blocks(frame, cfg) if with_blocks else []
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
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
        if symbol not in caches and len(caches) >= 12:
            caches.pop(next(iter(caches)))
        caches[symbol] = (key, enriched)
        return enriched

    def _interval_frame(self, symbol: str, tape: pd.DataFrame, minutes: int, cfg) -> pd.DataFrame:
        """The stock's 1-minute tape grouped into `minutes` candles and enriched, for the chart only.

        Redone only when a candle closes (the forming row is refreshed from the tape in between),
        like `_view_frame`. The bot never reads it.
        """
        bars = resample(tape, minutes)
        if bars is None or len(bars) < 2:
            return bars
        closed = bars.iloc[-2]
        key = (len(bars), int(bars["ts"].iloc[0]), int(closed["ts"]), float(closed["close"]), int(cfg.sma_fast), int(cfg.sma_slow), int(cfg.atr_period))
        caches = getattr(self, "_interval_frames", None)
        if caches is None:
            caches = self._interval_frames = {}
        hit = caches.get((symbol, minutes))
        if hit is not None and hit[0] == key:
            out = hit[1].copy()
            cols = [c for c in ("open", "high", "low", "close", "volume") if c in bars.columns and c in out.columns]
            out.iloc[-1, [out.columns.get_loc(c) for c in cols]] = bars[cols].iloc[-1].to_numpy()
            return out
        enriched = enrich(bars, cfg.sma_fast, cfg.sma_slow, cfg.atr_period, getattr(cfg, "sma_medium", None))
        if (symbol, minutes) not in caches and len(caches) >= 12:
            caches.pop(next(iter(caches)))
        caches[(symbol, minutes)] = (key, enriched)
        return enriched

    def chart_payload(self, limit: int = 240, symbol: str | None = None, interval: int | None = None) -> dict:
        """The chart's candles, markers and levels.

        `symbol` draws another watched stock without moving the chart focus
        (a second browser tab on one stock of a replay). An unknown stock gives
        an empty chart.

        `interval` (minutes, one of CHART_INTERVALS) draws the stock's 1-minute
        tape in candles of that size instead of the bot's own candle: finer
        than the bot trades on (1 or 3 minutes for a 5-minute bot), or a size
        the bot's candle does not divide into. Display only: the bot's signals,
        stops and refused crosses stay on its own candle, so none are drawn.
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
        shown_symbol = other or focus
        native = candle_minutes(cfg, stock_overrides(cfg, shown_symbol)) if cfg else 1
        alt = 0
        if cfg is not None and interval in CHART_INTERVALS and interval != native:
            tape = self._tapes.get(shown_symbol)
            if tape is not None and not getattr(tape, "empty", True):
                frame = self._interval_frame(shown_symbol, tape, int(interval), cfg)
                alt = int(interval)
        if frame is not None and not frame.empty:
            if len(frame) >= 2:
                # VWAP and RSI need the whole session, so compute before trimming.
                closed_rows, all_blocks = self._chart_parts(
                    frame, cfg, f"{shown_symbol}@{alt}" if alt else shown_symbol, with_blocks=not alt
                )
                candles = [*closed_rows, _forming_row(frame.iloc[-1])][-limit:]
                candles = _from_formed_sma(candles)
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
            # Minutes per candle sent (candles.py); the chart only merges up from it.
            "candle_minutes": alt or (candle_minutes(cfg, stock_overrides(cfg, view)) if cfg else 1),
            # Classic daily pivots from the previous session (pivots.py), drawn as lines; display only.
            "pivots": self._pivots(view, self._now().date()) if view else None,
            "pivot_target": pivots.target_mode(cfg) if cfg is not None else "OFF",
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
    "ST_SL_HIT": "Supertrend stop",
    "TARGET_HIT": "target hit",
    "BB_TARGET": "Bollinger band target",
    "BB_MIDDLE": "Bollinger middle band",
    "COUNTER_CANDLES": "candles turned against the trade",
    "EMA_CROSS": "EMA cross against the trade",
    "SUPERTREND": "Supertrend turned against the trade",
    "SMA9_BEND": "SMA 9 bent",
    "SMA9_TURN": "SMA 9 turned against the trade",
    "SMA9_VTURN": "SMA 9 V-turn",
    "GAP_FADE": "SMA gap faded",
    "CANDLE_END": "end of the pattern candle",
    "COLOUR_TURN": "candle turned the other colour",
    "PIVOT_TARGET": "pivot target (R1 / S1)",
    "REPLAY_STOPPED": "replay stopped",
    "EOD_SQUARE_OFF": "square-off",
    "KILL_SWITCH": "panic square-off",
    "NOT_ON_GROWW": "not on Groww — no order sent",
    "SL_REJECTED": "stop refused — closed at once",
    "MANUAL_CLOSE": "closed from the screen",
    "USER_REVIEW_EXIT": "EXIT on the 1-minute review",
    "AUTO_REVIEW_EXIT": "the 1-minute review's default answer was Exit",
}


REVIEWS_KEPT = 10

#: Candle sizes (minutes) the chart can draw from the 1-minute tape (`chart_payload(interval=)`).
CHART_INTERVALS = (1, 2, 3, 5, 10, 15, 30, 60, 240)


def _stop_points(cfg) -> dict | None:
    """Stop, trail and target of one stock's settings, for the screens.

    Reports both the ₹ (points) numbers and the % numbers, so the frontend
    can show whichever matches `tsl_mode`.
    """
    if cfg is None:
        return None
    import tsl as _tsl

    sl_pct, step_pct, target_pct = _tsl.tsl_pcts(cfg)
    return {
        "tsl_mode": _tsl.tsl_mode(cfg),
        "tsl_sl_points": float(getattr(cfg, "tsl_sl_points", 20.0) or 20.0),
        "tsl_trail_points": float(getattr(cfg, "tsl_trail_points", 10.0) or 10.0),
        "tsl_target_points": float(getattr(cfg, "tsl_target_points", 0.0) or 0.0),
        "tsl_sl_pct": sl_pct,
        "tsl_trail_pct": step_pct,
        "tsl_target_pct": target_pct,
        "gap_sl_mult": float(getattr(cfg, "gap_sl_mult", 1.0) or 1.0),
        "gap_tp_mult": float(getattr(cfg, "gap_tp_mult", 2.0) or 2.0),
    }


def _review_dict(row: ReviewLog) -> dict:
    def load(text: str | None) -> dict:
        try:
            data = json.loads(text) if text else {}
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    return {
        "id": int(row.id),
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else None,
        "bot": row.bot,
        "mode": row.mode,
        "run_id": row.run_id,
        "trade_id": int(row.trade_id),
        "symbol": row.symbol,
        "direction": row.direction,
        "candle_minutes": row.candle_minutes,
        "five_min": load(row.five_min),
        "one_min": load(row.one_min),
        "message": row.message,
        "status": row.status,
        "action_at": row.action_at.isoformat(timespec="seconds") if row.action_at else None,
        "note": row.note or "",
    }


def shutdown_alert(engines: list, when: dt.datetime) -> str:
    """One message for the whole app stopping: every bot's status, and which need a restart."""
    lines = ["PalTra app is stopping (deploy or restart)."]
    restart = []
    for eng in engines:
        cfg = getattr(eng, "_cfg_cache", None)
        name = (getattr(cfg, "bot_name", None) if cfg is not None else None) or f"Bot {getattr(eng, 'bot_id', 1)}"
        held = len(getattr(eng, "positions", {}) or {})
        lines.append(f"{name}: {eng.status}" + (f", {held} open position(s)" if held else ""))
        if eng.status in ("RUNNING", "PAUSED") or held:
            restart.append(name)
    if restart:
        lines.append(f"Press Start bot on {', '.join(restart)} after the app comes back.")
    lines.append(f"{when.strftime('%d %b %H:%M:%S')} IST")
    return "\n".join(lines)


async def announce_process_stop(engines: list) -> None:
    """Tell the owner before the process exits, at any hour. A deploy must not stop a bot silently."""
    if not engines:
        return
    message = shutdown_alert(engines, _ist_now())
    try:
        from app.services.alert_notifier import alert_notifier

        # In market hours it always messages and rings; after hours each follows its switch.
        await asyncio.wait_for(alert_notifier.announce_shutdown(message), timeout=8)
    except Exception:  # noqa: BLE001
        return


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


def _schedule_critical_whatsapp(message: str, call: bool = False) -> None:
    """Schedule a critical alert (bot down, app hung). Routes to the critical channel."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _send() -> None:
        try:
            from app.services.alert_notifier import alert_notifier

            await alert_notifier.send_critical(message, call=call)
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


def _candle_is_behind(closed_ts: int, now: dt.datetime, minutes: int = 1) -> bool:
    """True when the frame is missing the candle that should already be closed.

    `minutes` is the candle interval: the candle before the one `now` is in
    must be the last closed one.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=IST)
    else:
        now = now.astimezone(IST)
    current = bucket_start(int(now.timestamp()), max(1, int(minutes)))
    return closed_ts < current - max(1, int(minutes)) * 60


# Exits the bot sends itself while an exchange stop may still be working.
# The turn-trade colour exit counts a candle only when the trade opened within this many seconds of its start
# (an entry at the candle's open, judged a pass or two after the candle before it closed).
TURN_COLOUR_GRACE_SEC = 60

# LIVE stop health (_stop_dead): Groww's words for a stop that will never fill.
STOP_DEAD = frozenset({"CANCELLED", "CANCELED", "REJECTED", "EXPIRED", "FAILED", "FAILURE"})
STOP_DEAD_GRACE_SEC = 5
STOP_LOST = "STOP_LOST"
STOP_SOLD_OUTSIDE = "SOLD_ON_GROWW"

_TAKEN_EXITS = (
    "TARGET_HIT", "BB_TARGET", "BB_MIDDLE", "GAP_FADE", "CANDLE_END", "COLOUR_TURN", "COUNTER_CANDLES", "EMA_CROSS", "SUPERTREND",
    "SMA9_BEND", "SMA9_TURN", "SMA9_VTURN", "SMA9_TURN_AGAINST",
    "SLOW_SMA_BREACH", "RECOVERY_TARGET_HIT", "EMERGENCY_RECOVERY_ABORT", "SQUARE_OFF_CUTOFF",
    "BREAKOUT_STOP", "PIVOT_TARGET",
)


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
    if bool(getattr(cfg, "use_ema", False)):
        checked.append("EMA")
    if bool(getattr(cfg, "use_supertrend", False)):
        checked.append("Supertrend")
    if bool(getattr(cfg, "use_level", False)):
        checked.append("price level")
    if opening_range.any_on(cfg):
        checked.append("opening range")
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


def _pick_cross(frame: pd.DataFrame) -> str | None:
    """Return the cross signal for `frame`.

    Uses the triple MA cross when the frame has a `sma_medium` column (i.e.
    when the strategy is configured with a third SMA), and falls back to the
    standard two-SMA cross otherwise.
    """
    if "sma_medium" in frame.columns:
        return closed_candle_cross_triple(frame)
    return closed_candle_cross(frame)


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
        older_signal = _pick_cross(older)
        older_ts = _closed_bar_ts(older)
        if older_signal and older_ts is not None and older_ts > judged_ts:
            choices.append((older_signal, older_ts, older))
    newest_signal = _pick_cross(frame)
    if newest_signal and newest_ts is not None and (judged_ts is None or newest_ts > judged_ts):
        choices.append((newest_signal, newest_ts, frame))
    if not choices:
        return None, None
    signal, _ts, signal_frame = choices[-1]
    return signal, signal_frame


def _from_formed_sma(candles: list[dict]) -> list[dict]:
    """Drop the leading candles that have no slow SMA yet (the frame's warm-up),
    so the chart does not start with a stretch of candles and no SMA 21 line.
    Kept whole when no candle has it (too little history to draw it at all)."""
    for i, row in enumerate(candles):
        if row.get("sma21") is not None:
            return candles[i:] if i else candles
    return candles


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
        "entry_reason": getattr(row, "entry_reason", None),
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
