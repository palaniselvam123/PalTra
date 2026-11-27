"""Strategy lab (strategy_lab.py): which entry checks and exits make the SMA strategy money, answered in seconds.

The replay plays a day tick by tick through the whole bot, so ten stocks take hours. The lab instead works on
closed candles, once per stock and date range, and keeps a **table of base trades** with every rule's answer
already in it, so any combination of rules is arithmetic on that table (milliseconds), not another replay:

1. the stock's 1-minute Groww candles for the range (plus `WARMUP_DAYS` before it) are downloaded once and kept
   on disk per day (`_cache_dir`), so a second run of the same days downloads nothing;
2. they are grouped into the bot's candle (`candles.resample`) and every indicator is worked out once over the
   whole frame (the same arithmetic the bot uses: `indicators.enrich`, RSI, VWAP, Bollinger, EMA, MACD,
   cumulative delta, Supertrend, SMA 9 slope);
3. the **base trades** are the SMA crosses on closed candles (`sma_fast` vs `sma_slow`, judged like
   `indicators.closed_candle_cross`): the trade opens at the open of the candle after the cross (the price the bot
   sees when the cross candle closes), from 09:20 to the bot's entry cut-off, on the days asked for;
4. for each base trade the lab pre-answers every entry check at its signal candle (`checks`: True = the check
   agrees) and pre-finds, for every exit rule, the first candle after the entry on which that rule would have
   fired (`exits`: when and at what price, None = never before the square-off), each stop type's first hit on the
   1-minute candles (`stops`), the trailing-stop target and the pivot targets (`targets`), and the square-off.

`evaluate(trades, choice)` then applies a choice of checks / exits / stop (`Choice`): a trade one chosen check
refused is skipped, a trade that starts while the previous kept trade on that stock is still open is skipped
(the bot is in a position: crosses only open trades while flat), and the trade ends at the earliest of its chosen
exits, its stop, its target and the square-off. P&L before charges and after (`charges.calculate_charges`), per
stock, per day, by exit reason, and the equity line. `recommend` toggles each rule one at a time against the
current choice ("what each rule adds") and hill-climbs to the best combination it finds.

Rules the bot's own modules answer are read through them: fresh trend (`fresh_entry.read`), the opening-range checks
(`opening_range.entry_reason`), executed volume (`trade_flow.minutes`, `flow_exit.entry_refusal`), colours on smaller
candles (`small_colour.entry_reason`), the exit-candle SMA cross (`exit_candle.judge`, the bot's exit candle or the next
size up), the executed volume exit (`flow_exit.judge`, one reading), the smart stop (`smart_stop.room` at the entry, then
`want`, no live re-size), the ATR stop with the staged plan (`stop_plan`), and the ATR target (the bot's multiple or
3×), day high / low target (`day_target`) and profit safety net (`target_net`). A run read before a rule existed has no
answer for it (`missing_rules`): it is left out of "what each rule adds" until the days are read again.

What it is not: the bot. Candle-close resolution; stops and targets on the 1-minute candles' high / low, the stop
first when both are inside one minute (the cautious reading); fills at candle opens; no pullback / candle-extreme /
entry-candle waits, no gap mode, no hand-offs, no reviews, no fast movers, no news entries, no slope candle other
than the bot's own, real candle colours (no Heikin Ashi). Only the SMA cross opens trades (no turn, join, always-in,
pattern or colour entries). Use it to choose; use the replay to confirm. Practice money, no order path, nothing
armed, nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import json
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import atr_target
import cum_delta
import day_target
import ema_filter
import exit_candle
import flow_exit
import fresh_entry
import indicators
import level_check
import macd
import opening_range
import pivots
import slope_exit
import small_colour
import smart_stop
import stack_entry
import stop_plan
import supertrend
import target_net
import trade_flow
import tsl as tsl_mod
from candles import CANDLE_MINUTES, candle_minutes, resample
from charges import calculate_charges, legs_for
from decel_guard import early_warning
from indicators import _direction_reason, _rsi_reason, bollinger, minute_volume_stats, rsi_wilder, session_vwap_series
from tick_sizes import round_price

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

WARMUP_DAYS = 5
MAX_STOCKS = 250
MAX_RANGE_DAYS = 45
KEEP_JOBS = 20
FIRST_ENTRY = dt.time(9, 20)
SESSION_END = dt.time(15, 30)
#: Trades the evaluate answer carries (the newest first beyond this are left out; the totals count every one).
MAX_TRADES_OUT = 3000
#: The greedy search: at most this many rule changes, each the single change that adds the most net P&L.
SEARCH_STEPS = 8
#: A combination with fewer trades than this is not recommended.
SEARCH_MIN_TRADES = 20

#: Entry checks the lab answers (key → label). "gap" is the SMA gap range on the trade's side.
ENTRY_CHECKS: dict[str, str] = {
    "vwap": "VWAP",
    "volume": "Volume vs average",
    "density": "Candle density",
    "rsi": "RSI range",
    "bollinger": "Bollinger (not stretched, no squeeze)",
    "gap": "SMA gap range",
    "candle_dir": "Candle direction",
    "ema": "EMA cross side",
    "macd": "MACD side",
    "cvd": "Cumulative delta",
    "supertrend": "Supertrend side",
    "level": "Price level",
    "adx": "ADX at least",
    "fresh": "Fresh trend",
    "or_volume": "Opening range: volume",
    "or_vwap": "Opening range: VWAP",
    "or_spread": "Opening range: SMA spread",
    "or_break": "Opening range: breakout",
    "flow": "Executed volume (order's side)",
    "small_colour": "Colours on smaller candles",
}
#: Exit rules (key → label).
EXIT_RULES: dict[str, str] = {
    "cross": "SMA cross against",
    "bb_band": "Bollinger band (target)",
    "bb_middle": "Bollinger middle band",
    "supertrend": "Supertrend flip",
    "ema": "EMA cross against",
    "macd": "MACD cross against",
    "cvd": "CVD divergence",
    "counter": "Counter candles",
    "slow_sma": "Close beyond the slow SMA",
    "slope_turn": "SMA 9 turn",
    "slope_bend": "SMA 9 bend",
    "vturn": "SMA 9 V-turn",
    "decel": "SMA 9 deceleration guard",
    "exit_candle": "SMA cross on the exit candle",
    "flow_exit": "Executed volume exit",
}
STOPS = ("NONE", "ATR", "TSL", "SUPERTREND", "VWAP", "SMART", "PLAN")
TARGETS: dict[str, str] = {
    "tsl": "Trailing-stop target",
    "pivot_r1": "Pivot R1 / S1",
    "pivot_r2": "Pivot R2 / S2",
    "atr": "ATR target",
    "day_level": "Day high / low target",
    "net": "Profit safety net",
}
#: What opens the base trades (owner's request: the bot's "Enter on three SMAs with widening gaps" in the lab too).
ENTRY_SOURCES: dict[str, str] = {
    "CROSS": "SMA cross",
    "STACK": "3 SMAs stacked, gaps widening",
}
#: The "each gap at least" levels (%) a STACK run is read at: each widening run gives one entry per level, at the first
#: candle both gaps reached it, so the evaluate step can pick any of them without reading the days again.
STACK_GAPS = (0.0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.5)
#: The exit candle the lab reads when the bot has none set: the next size up from the bot's candle.
EXIT_CANDLE_DEFAULTS = (5, 15, 30, 60)
#: The ATR target multiple the lab reads when the bot has none set.
DEFAULT_ATR_TARGET = 3.0
EXIT_REASONS = {
    "cross": "MA_CROSS",
    "bb_band": "BB_TARGET",
    "bb_middle": "BB_MIDDLE",
    "supertrend": "SUPERTREND",
    "ema": "EMA_CROSS",
    "macd": "MACD_CROSS",
    "cvd": "CVD_DIVERGENCE",
    "counter": "COUNTER_CANDLES",
    "slow_sma": "SLOW_SMA_BREACH",
    "slope_turn": "SMA9_TURN",
    "slope_bend": "SMA9_BEND",
    "vturn": "SMA9_VTURN",
    "decel": "SMA9_DECEL",
    "exit_candle": "EXIT_CANDLE_CROSS",
    "flow_exit": "FLOW_EXIT",
    "stop:SMART": "SMART_SL_HIT",
    "stop:PLAN": "PLAN_SL_HIT",
    "target:atr": "TARGET_HIT",
    "target:day_level": "DAY_LEVEL_TARGET",
    "target:net": "TARGET_NET",
    "stop:ATR": "ATR_SL_HIT",
    "stop:TSL": "TSL_HIT",
    "stop:SUPERTREND": "ST_SL_HIT",
    "stop:VWAP": "VWAP_SL_HIT",
    "target:tsl": "TARGET_HIT",
    "target:pivot_r1": "PIVOT_TARGET",
    "target:pivot_r2": "PIVOT_TARGET",
    "square": "EOD_SQUARE_OFF",
}


# ----------------------------------------------------------------------------------------------------------------
# Settings read from the bot
# ----------------------------------------------------------------------------------------------------------------


def _num(cfg, name: str, default: float) -> float:
    try:
        value = getattr(cfg, name, None)
        return default if value is None else float(value)
    except (TypeError, ValueError):
        return default


def _int(cfg, name: str, default: int) -> int:
    return int(round(_num(cfg, name, default)))


class _Over:
    """`cfg` with some fields set (e.g. a check's own switch on), so the bot's modules read the rule the lab tests."""

    def __init__(self, cfg, **over) -> None:
        self._cfg = cfg
        self._over = over

    def __getattr__(self, name: str):
        if name in self._over:
            return self._over[name]
        return getattr(self._cfg, name, None)


def _exit_size(cfg, own: int) -> int:
    """The exit candle the lab reads: the bot's own setting when set (and not its own candle), else the next size up."""
    size = _int(cfg, "exit_candle_minutes", 0)
    if size in exit_candle.EXIT_MINUTES and size > 0 and size != own:
        return size
    return next((n for n in EXIT_CANDLE_DEFAULTS if n > own), 0)


def _small_sizes(cfg, own: int) -> str:
    """The smaller candles the lab reads: the bot's own ticked sizes, else up to two of 1 / 3 / 5 under its candle."""
    mine = small_colour.watched(cfg, own)
    if mine:
        return ",".join(str(n) for n in mine)
    return ",".join(str(n) for n in [n for n in (1, 3, 5) if n < own][:2])


def _hhmm(value, default: str) -> dt.time:
    try:
        hh, mm = str(value or default).split(":")
        return dt.time(int(hh), int(mm))
    except (TypeError, ValueError):
        hh, mm = default.split(":")
        return dt.time(int(hh), int(mm))


def with_candle(cfg, minutes: int):
    """A copy of `cfg` that trades SMA crosses on `minutes`-minute candles (the lab's own candle pick)."""
    import copy

    if hasattr(cfg, "__table__"):
        out = type(cfg)(**{col.name: getattr(cfg, col.name) for col in cfg.__table__.columns})
    else:
        out = copy.copy(cfg)
    out.candle_minutes = int(minutes)
    out.entry_mode = "SMA"
    return out


def lab_settings(cfg) -> dict:
    """The numbers behind every rule, as the bot has them (so a stock's own settings apply when `cfg` is its own)."""
    return {
        "sma_fast": max(2, _int(cfg, "sma_fast", 9)),
        "sma_slow": max(3, _int(cfg, "sma_slow", 21)),
        "atr_period": max(2, _int(cfg, "atr_period", 14)),
        "atr_multiplier": _num(cfg, "atr_multiplier", 1.5),
        "candle_minutes": candle_minutes(cfg),
        "qty": max(1, _int(cfg, "qty", 1)),
        "entry_cutoff": _hhmm(getattr(cfg, "entry_cutoff_time", None), "15:00").strftime("%H:%M"),
        "square_off": _hhmm(getattr(cfg, "square_off_time", None), "15:15").strftime("%H:%M"),
        "max_trades_per_stock": max(0, _int(cfg, "max_trades_per_stock", 0)),
        "adx_threshold": _num(cfg, "adx_threshold", 20.0),
        "volume_lookback": max(1, _int(cfg, "volume_lookback", 20)),
        "volume_min_ratio": _num(cfg, "volume_min_ratio", 1.0),
        "density_min_pct": _num(cfg, "density_min_pct", 50.0),
        "rsi_period": max(2, _int(cfg, "rsi_period", 14)),
        "rsi_long_min": _num(cfg, "rsi_long_min", 40.0),
        "rsi_long_max": _num(cfg, "rsi_long_max", 70.0),
        "rsi_short_min": _num(cfg, "rsi_short_min", 30.0),
        "rsi_short_max": _num(cfg, "rsi_short_max", 60.0),
        "bb_period": max(2, _int(cfg, "bb_period", 20)),
        "bb_std": _num(cfg, "bb_std", 2.0),
        "bb_min_width_pct": _num(cfg, "bb_min_width_pct", 0.15),
        "gap_long_min": _num(cfg, "gap_long_min", 0.02),
        "gap_long_max": _num(cfg, "gap_long_max", 0.5),
        "gap_short_min": _num(cfg, "gap_short_min", -0.5),
        "gap_short_max": _num(cfg, "gap_short_max", -0.02),
        "candle_dir_count": max(1, _int(cfg, "candle_dir_count", 2)),
        "candle_dir_rule": str(getattr(cfg, "candle_dir_rule", None) or "CLOSES").upper(),
        "ema_fast": ema_filter.lengths(cfg)[0],
        "ema_slow": ema_filter.lengths(cfg)[1],
        "macd": list(macd.lengths(cfg)),
        "cvd_lookback": cum_delta.lookback(cfg),
        "cvd_div_lookback": cum_delta.div_lookback(cfg),
        "st_period": supertrend.settings(cfg)[0],
        "st_mult": supertrend.settings(cfg)[1],
        "level_line": level_check.line_of(cfg),
        "counter_count": max(1, _int(cfg, "counter_exit_count", 0) or 3),
        "counter_rule": str(getattr(cfg, "counter_rule", None) or "COLOUR").upper(),
        "counter_need_fast": bool(getattr(cfg, "counter_need_fast", True) if getattr(cfg, "counter_need_fast", None) is not None else True),
        "counter_min_body": max(0.0, _num(cfg, "counter_min_body", 0.0)),
        "slope_lookback": slope_exit.lookback(cfg),
        "slope_turn_pct": slope_exit.turn_pct(cfg),
        "slope_bend_pct": slope_exit.bend_pct(cfg) or 60.0,
        "vturn_move_pct": slope_exit.vturn_move_pct(cfg),
        "vturn_turn_pct": slope_exit.vturn_turn_pct(cfg),
        "vturn_within": slope_exit.vturn_within(cfg),
        "tsl_mode": tsl_mod.tsl_mode(cfg),
        "tsl_points": list(tsl_mod.tsl_settings(cfg)),
        "tsl_pcts": list(tsl_mod.tsl_pcts(cfg)),
        "tsl_atrs": list(tsl_mod.tsl_atrs(cfg)),
        "vwap_stop_atr": max(0.0, _num(cfg, "vwap_stop_atr", 0.5)),
        "decel": {k: v for k, v in _decel_settings(cfg).items()},
        "fresh_rvol": fresh_entry.min_rvol(cfg),
        "or_volume_ratio": opening_range.ratio(cfg),
        "or_spread_pct": opening_range.spread_pct(cfg),
        "flow_entry_pct": _num(cfg, "flow_entry_pct", 55.0),
        "flow_entry_window": max(0, _int(cfg, "flow_entry_window", 3)),
        "sc_minutes": _small_sizes(cfg, candle_minutes(cfg)),
        "exit_candle_minutes": _exit_size(cfg, candle_minutes(cfg)),
        "flow_exit_pct": flow_exit.pct(cfg),
        "flow_exit_window": flow_exit.window(cfg),
        "atr_target_mult": atr_target.mult(cfg) or DEFAULT_ATR_TARGET,
        "smart": smart_stop.settings(cfg),
        "plan_be_r": stop_plan.be_r(cfg),
        "plan_trail_r": stop_plan.trail_r(cfg),
        # The three lines and widening run of the three-SMA entry (stack_entry.periods / widen_need); None = no third SMA.
        "stack_lines": list(stack_entry.periods(cfg) or []) or None,
        "stack_widen": stack_entry.widen_need(cfg),
        "stack_third": _int(cfg, "stack_third", stack_entry.THIRD_DEFAULT),
    }


def snap_gap(value) -> float:
    """The nearest STACK_GAPS level to `value` (a typed or saved "each gap at least" %)."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.05
    return min(STACK_GAPS, key=lambda g: abs(g - v))


def _stack_signals(rich: pd.DataFrame, s: dict) -> list[tuple[int, str, list[float]]]:
    """The three-SMA entries on the bot's candles, as `stack_entry.call` takes them: (closed candle, side, the gap levels
    it is the entry for). Lines in order shortest to longest; both gaps widening `stack_widen` closed candles in a row
    (chop_scan.three_read's count); each widening run entered once per gap level, at the first candle its smaller gap
    reached that level; a run widening `stack_entry.RUN_CAP` candles or more is too old to join."""
    lines = s.get("stack_lines")
    if not lines or "close" not in rich:
        return []
    a, b, c = sorted(int(n) for n in lines)
    close = pd.to_numeric(rich["close"], errors="coerce")
    f, m, t = (close.rolling(n).mean().to_numpy(dtype=float) for n in (a, b, c))
    with np.errstate(divide="ignore", invalid="ignore"):
        g1 = (f - m) / m * 100.0
        g2 = (m - t) / t * 100.0
    need = max(1, int(s.get("stack_widen") or 2))
    out: list[tuple[int, str, list[float]]] = []
    widen = 0
    used: dict[tuple[str, int], set[float]] = {}
    for i in range(1, len(close)):
        x1, x2, p1, p2 = g1[i], g2[i], g1[i - 1], g2[i - 1]
        if not (np.isfinite(x1) and np.isfinite(x2)) or not ((x1 > 0 and x2 > 0) or (x1 < 0 and x2 < 0)):
            widen = 0
            continue
        sign = 1.0 if x1 > 0 else -1.0
        grew = np.isfinite(p1) and np.isfinite(p2) and abs(x1) > abs(p1) and abs(x2) > abs(p2)
        same = np.isfinite(p1) and np.isfinite(p2) and p1 * sign > 0 and p2 * sign > 0
        widen = (widen + 1 if same else 1) if grew else 0
        if widen < need or widen >= stack_entry.RUN_CAP:
            continue
        side = "LONG" if sign > 0 else "SHORT"
        run = (side, i - widen + 1)
        floor = min(abs(x1), abs(x2))
        taken = used.setdefault(run, set())
        steps = [g for g in STACK_GAPS if g not in taken and floor >= g]
        if steps:
            taken.update(steps)
            out.append((i, side, steps))
    return out


def _decel_settings(cfg) -> dict:
    from decel_guard import settings as decel_settings

    return decel_settings(cfg)


def choice_from_cfg(cfg) -> dict:
    """The bot's own switches as a lab choice: what it would trade with today."""
    entries: list[str] = []
    for name in ENTRY_CHECKS:
        if name == "gap":
            on = bool(getattr(cfg, "use_gap_long", False)) or bool(getattr(cfg, "use_gap_short", False))
        elif name == "flow":
            on = bool(getattr(cfg, "flow_entry", False))
        elif name == "small_colour":
            on = small_colour.entry_on(cfg) and bool(small_colour.watched(cfg, candle_minutes(cfg)))
        else:
            on = bool(getattr(cfg, "use_adx_filter" if name == "adx" else f"use_{name}", False))
        if on:
            entries.append(name)
    exits: list[str] = []
    cross = getattr(cfg, "cross_exit", True)
    if cross is None or bool(cross):
        exits.append("cross")
    bb = str(getattr(cfg, "bb_exit", None) or "OFF").upper()
    if bb in ("BAND", "BOTH"):
        exits.append("bb_band")
    if bb in ("MIDDLE", "BOTH"):
        exits.append("bb_middle")
    if bool(getattr(cfg, "st_exit", False)):
        exits.append("supertrend")
    if bool(getattr(cfg, "ema_exit", False)):
        exits.append("ema")
    if bool(getattr(cfg, "macd_exit", False)):
        exits.append("macd")
    if bool(getattr(cfg, "cvd_div_exit", False)):
        exits.append("cvd")
    if _int(cfg, "counter_exit_count", 0) > 0:
        exits.append("counter")
    if bool(getattr(cfg, "slow_sma_exit", False)):
        exits.append("slow_sma")
    if slope_exit.on(cfg):
        if slope_exit.turn_on(cfg):
            exits.append("slope_turn")
        if slope_exit.bend_pct(cfg) > 0:
            exits.append("slope_bend")
    if slope_exit.vturn_on(cfg):
        exits.append("vturn")
    if bool(getattr(cfg, "decel_exit", False)):
        exits.append("decel")
    if exit_candle.active(cfg, candle_minutes(cfg)):
        exits.append("exit_candle")
    if flow_exit.on(cfg) and flow_exit.closes(cfg):
        exits.append("flow_exit")
    stop = "NONE"
    use_stop = getattr(cfg, "use_stop", None)
    if use_stop is None or bool(use_stop):
        kind = str(getattr(cfg, "stop_type", None) or "ATR").upper()
        stop = kind if kind in STOPS else "ATR"
        if stop == "ATR" and stop_plan.on(cfg):
            stop = "PLAN"
    targets: list[str] = []
    if atr_target.on(cfg):
        targets.append("atr")
    if day_target.on(cfg):
        targets.append("day_level")
    if target_net.on(cfg):
        targets.append("net")
    if stop == "TSL" and tsl_mod.tsl_settings(cfg)[2] > 0:
        targets.append("tsl")
    mode = pivots.target_mode(cfg)
    if mode == "R1":
        targets.append("pivot_r1")
    elif mode == "R2":
        targets.append("pivot_r2")
    source = "STACK" if stack_entry.enabled(cfg) and stack_entry.only(cfg) else "CROSS"
    return {"entries": entries, "exits": exits, "stop": stop, "targets": targets, "source": source,
            "stack_gap": snap_gap(stack_entry.min_gap(cfg))}


def settings_for_choice(choice: dict, base: dict | None = None) -> dict:
    """The BotConfig fields that make the bot trade this choice (for "Backtest this in the replay")."""
    entries = set(choice.get("entries") or [])
    exits = set(choice.get("exits") or [])
    out: dict = {}
    for name in ENTRY_CHECKS:
        if name == "gap":
            out["use_gap_long"] = out["use_gap_short"] = "gap" in entries
        elif name == "flow":
            out["flow_entry"] = "flow" in entries
        elif name == "small_colour":
            out["sc_entry"] = "small_colour" in entries
            if "small_colour" in entries and (base or {}).get("sc_minutes"):
                out["sc_minutes"] = str(base["sc_minutes"])
        elif name == "adx":
            out["use_adx_filter"] = "adx" in entries
        else:
            out[f"use_{name}"] = name in entries
    out["cross_exit"] = "cross" in exits
    band, middle = "bb_band" in exits, "bb_middle" in exits
    out["bb_exit"] = "BOTH" if band and middle else "BAND" if band else "MIDDLE" if middle else "OFF"
    out["st_exit"] = "supertrend" in exits
    out["ema_exit"] = "ema" in exits
    out["macd_exit"] = "macd" in exits
    out["cvd_div_exit"] = "cvd" in exits
    count = int((base or {}).get("counter_count") or 3)
    out["counter_exit_count"] = count if "counter" in exits else 0
    out["slow_sma_exit"] = "slow_sma" in exits
    out["slope_exit"] = "slope_turn" in exits or "slope_bend" in exits
    out["slope_turn"] = "slope_turn" in exits
    out["slope_bend_pct"] = float((base or {}).get("slope_bend_pct") or 60.0) if "slope_bend" in exits else 0.0
    out["vturn_exit"] = "vturn" in exits
    out["decel_exit"] = "decel" in exits
    out["exit_candle_minutes"] = int((base or {}).get("exit_candle_minutes") or 0) if "exit_candle" in exits else 0
    out["flow_exit"] = "flow_exit" in exits
    if "flow_exit" in exits:
        out["flow_exit_mode"] = "EXIT"
    stop = str(choice.get("stop") or "NONE").upper()
    out["use_stop"] = stop != "NONE"
    out["stop_plan"] = stop == "PLAN"
    if stop != "NONE":
        out["stop_type"] = "ATR" if stop == "PLAN" else stop
    targets = set(choice.get("targets") or [])
    out["pivot_target"] = "R1" if "pivot_r1" in targets else "R2" if "pivot_r2" in targets else "OFF"
    out["atr_target_mult"] = float((base or {}).get("atr_target_mult") or DEFAULT_ATR_TARGET) if "atr" in targets else 0.0
    out["day_target"] = "day_level" in targets
    out["target_net"] = "net" in targets
    if base and base.get("candle_minutes"):
        out["candle_minutes"] = int(base["candle_minutes"])
    # What opens trades: the three-SMA entry alone (STACK) or the SMA cross (the three-SMA entry off).
    stack = str(choice.get("source") or "CROSS").upper() == "STACK"
    out["stack_entry"] = stack
    out["stack_only"] = stack
    if stack:
        out["stack_min_gap"] = snap_gap(choice.get("stack_gap"))
        if base and base.get("stack_widen"):
            out["stack_widen"] = int(base["stack_widen"])
    return out


def numbers_for_bot(settings: dict | None, choice: dict) -> dict:
    """The numbers the lab read (`lab_settings`) as BotConfig fields, so a bot reset to its defaults gets them back and
    the replay trades the same SMAs, stop distance, filter ranges and quantity the lab tested ("Reset the other settings
    first"). The run's quantity unless the choice set one."""
    s = settings or {}
    if not s:
        return {}
    out: dict = {}

    def put(field: str, key: str) -> None:
        if s.get(key) is not None:
            out[field] = s[key]

    for key in ("sma_fast", "sma_slow", "atr_period", "atr_multiplier", "candle_minutes", "max_trades_per_stock",
                "adx_threshold", "volume_min_ratio", "density_min_pct", "rsi_long_min", "rsi_long_max", "rsi_short_min",
                "rsi_short_max", "bb_period", "bb_std", "bb_min_width_pct", "gap_long_min", "gap_long_max", "gap_short_min",
                "gap_short_max", "candle_dir_count", "candle_dir_rule", "ema_fast", "ema_slow", "cvd_lookback",
                "cvd_div_lookback", "st_period", "st_mult", "level_line", "counter_rule", "counter_need_fast",
                "counter_min_body", "slope_lookback", "slope_turn_pct", "vturn_move_pct", "vturn_turn_pct", "vturn_within",
                "tsl_mode", "vwap_stop_atr", "fresh_rvol", "or_volume_ratio", "or_spread_pct", "flow_entry_pct",
                "flow_entry_window", "flow_exit_pct", "flow_exit_window", "plan_be_r", "plan_trail_r"):
        put(key, key)
    put("entry_cutoff_time", "entry_cutoff")
    put("square_off_time", "square_off")
    out["qty"] = int(choice.get("qty") or s.get("qty") or 1)
    macd_len = s.get("macd") or []
    for field, value in zip(("macd_fast", "macd_slow", "macd_signal"), macd_len):
        out[field] = value
    for prefix, key in (("", "tsl_points"), ("", "tsl_pcts"), ("", "tsl_atrs")):
        vals = s.get(key) or []
        unit = {"tsl_points": "points", "tsl_pcts": "pct", "tsl_atrs": "atr"}[key]
        for field, value in zip((f"tsl_sl_{unit}", f"tsl_trail_{unit}", f"tsl_target_{unit}"), vals):
            out[field] = value
    for k, v in (s.get("decel") or {}).items():
        out[f"decel_{k}"] = v
    smart = s.get("smart") or {}
    for k in ("pct", "days", "min_pct", "max_pct", "follow"):
        if k in smart:
            out[f"smart_{k}"] = smart[k]
    # The lab opens trades on SMA crosses or the three-SMA stack (stack_entry, on top of the SMA entry mode).
    out["entry_mode"] = "SMA"
    put("stack_widen", "stack_widen")
    put("stack_third", "stack_third")
    return out


# ----------------------------------------------------------------------------------------------------------------
# Candle cache
# ----------------------------------------------------------------------------------------------------------------


def _data_dir() -> Path:
    explicit = os.environ.get("STRATEGY_LAB_DIR", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :]).parent / "strategy_lab"
    return Path("strategy_lab")


def _cache_dir() -> Path:
    return _data_dir() / "candles"


def _cache_file(symbol: str, day: dt.date) -> Path:
    return _cache_dir() / symbol.upper() / f"{day.isoformat()}.json.gz"


_COLUMNS = ("ts", "open", "high", "low", "close", "volume")


def _write_day(path: Path, part: pd.DataFrame) -> None:
    """One day's candles as gzip JSON columns (plain numbers, no pickle)."""
    data = {name: pd.to_numeric(part[name], errors="coerce").tolist() if name in part.columns else [] for name in _COLUMNS}
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as fh:
        json.dump(data, fh)


def _read_day(path: Path) -> pd.DataFrame:
    with gzip.open(path, "rt") as fh:
        data = json.load(fh)
    frame = pd.DataFrame({name: data.get(name) or [] for name in _COLUMNS})
    if len(frame):
        frame["ts"] = frame["ts"].astype("int64")
    return frame


def _split_days(frame: pd.DataFrame) -> dict[dt.date, pd.DataFrame]:
    if frame is None or getattr(frame, "empty", True) or "ts" not in frame.columns:
        return {}
    days = pd.to_datetime(frame["ts"], unit="s", utc=True).dt.tz_convert(IST).dt.date
    return {day: part.reset_index(drop=True) for day, part in frame.groupby(days.values)}


async def load_tape(symbol: str, first: dt.date, last: dt.date, fetch, today: dt.date | None = None) -> pd.DataFrame:
    """The stock's 1-minute candles for [first, last], from the day cache; the missing days downloaded once.

    `fetch(symbol, start, end)` downloads (candle_history.fetch_frame). Only days before `today` are cached."""
    today = today or dt.datetime.now(IST).date()
    wanted = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    # Reading, writing and joining the gzip day files runs in a worker thread, so a long test never stalls the
    # event loop the bots and every page share.
    parts, missing = await asyncio.to_thread(_read_cached_days, symbol, wanted, today)
    if missing:
        start = dt.datetime.combine(missing[0], dt.time(9, 0), tzinfo=IST)
        end = dt.datetime.combine(missing[-1], SESSION_END, tzinfo=IST)
        got = await fetch(symbol, start, end)
        await asyncio.to_thread(_store_missing_days, symbol, got, missing, today, parts)
    return await asyncio.to_thread(_join_days, parts, wanted)


def _read_cached_days(symbol: str, wanted: list[dt.date], today: dt.date) -> tuple[dict[dt.date, pd.DataFrame], list[dt.date]]:
    parts: dict[dt.date, pd.DataFrame] = {}
    missing: list[dt.date] = []
    for day in wanted:
        path = _cache_file(symbol, day)
        if day < today and path.is_file():
            try:
                parts[day] = _read_day(path)
                continue
            except (OSError, ValueError, KeyError):  # a bad file is downloaded again
                pass
        missing.append(day)
    return parts, missing


def _store_missing_days(symbol: str, got, missing: list[dt.date], today: dt.date, parts: dict) -> None:
    got_days = _split_days(got)
    for day in missing:
        part = got_days.get(day, pd.DataFrame(columns=list(_COLUMNS)))
        parts[day] = part
        if day < today:
            try:
                _write_day(_cache_file(symbol, day), part)
            except (OSError, ValueError) as exc:
                log.warning("strategy lab: could not cache %s %s: %s", symbol, day, exc)


def _join_days(parts: dict, wanted: list[dt.date]) -> pd.DataFrame:
    frames = [parts[d] for d in wanted if d in parts and len(parts[d])]
    if not frames:
        return pd.DataFrame(columns=list(_COLUMNS))
    tape = pd.concat(frames, ignore_index=True)
    return tape.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


# ----------------------------------------------------------------------------------------------------------------
# One stock: indicators, base trades, answers
# ----------------------------------------------------------------------------------------------------------------


def _day_of(ts: np.ndarray) -> np.ndarray:
    return (ts + 19800) // 86400


def _minute_of_day(ts: int) -> int:
    return int(((ts + 19800) % 86400) // 60)


def _time_minutes(value: dt.time) -> int:
    return value.hour * 60 + value.minute


def _rows_from_entry(ts: np.ndarray, entry_ts: int) -> int:
    return int(np.searchsorted(ts, entry_ts, side="left"))


def _volume_ok(rich: pd.DataFrame, i: int, lookback: int, ratio: float) -> bool:
    current, average, _ = minute_volume_stats(rich.iloc[: i + 1], lookback)
    if current is None or average is None or average <= 0:
        return False
    return current >= ratio * average


def _candle_dir_ok(rich: pd.DataFrame, i: int, side: str, count: int, rule: str) -> bool:
    return not _direction_reason(rich.iloc[max(0, i - count - 2) : i + 1], side, count, rule)


@dataclass
class _Series:
    ts: np.ndarray
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    fast: np.ndarray
    slow: np.ndarray
    atr: np.ndarray
    adx: np.ndarray
    rsi: np.ndarray
    vwap: np.ndarray
    bb_mid: np.ndarray
    bb_up: np.ndarray
    bb_lo: np.ndarray
    ema_hist: np.ndarray
    macd_hist: np.ndarray
    cvd: np.ndarray
    st_trend: np.ndarray
    st_line: np.ndarray
    gap: np.ndarray
    slope: np.ndarray
    day: np.ndarray
    counter_long: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    counter_short: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    cvd_div_long: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    cvd_div_short: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))


def _build_series(rich: pd.DataFrame, s: dict) -> _Series:
    close = pd.to_numeric(rich["close"], errors="coerce")
    bb_mid, bb_up, bb_lo = bollinger(close, s["bb_period"], s["bb_std"])
    ef, es = ema_filter.emas(rich, s["ema_fast"], s["ema_slow"])
    _, _, m_hist = macd.series(rich, *s["macd"])
    st_line, st_trend = supertrend.compute(rich, s["st_period"], s["st_mult"])
    sma = close.rolling(s["sma_fast"]).mean()
    back = s["slope_lookback"]
    slope = (sma - sma.shift(back)) / sma.shift(back) * 100.0 / back
    fast = rich["sma_fast"].to_numpy(dtype=float)
    slow = rich["sma_slow"].to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        gap = np.where(slow != 0, (fast - slow) / slow * 100.0, np.nan)
    ts = rich["ts"].to_numpy(dtype="int64")
    out = _Series(
        ts=ts,
        open=rich["open"].to_numpy(dtype=float),
        high=rich["high"].to_numpy(dtype=float),
        low=rich["low"].to_numpy(dtype=float),
        close=close.to_numpy(dtype=float),
        fast=fast,
        slow=slow,
        atr=rich["atr_14"].to_numpy(dtype=float),
        adx=rich["adx_14"].to_numpy(dtype=float),
        rsi=rsi_wilder(close, s["rsi_period"]).to_numpy(dtype=float),
        vwap=session_vwap_series(rich).to_numpy(dtype=float),
        bb_mid=bb_mid.to_numpy(dtype=float),
        bb_up=bb_up.to_numpy(dtype=float),
        bb_lo=bb_lo.to_numpy(dtype=float),
        ema_hist=(ef - es).to_numpy(dtype=float),
        macd_hist=m_hist.to_numpy(dtype=float),
        cvd=cum_delta.series(rich).to_numpy(dtype=float),
        st_trend=np.array([0 if t is None else int(t) for t in st_trend], dtype=int),
        st_line=np.array([np.nan if v is None else float(v) for v in st_line], dtype=float),
        gap=gap,
        slope=slope.to_numpy(dtype=float),
        day=_day_of(ts),
    )
    out.counter_long, out.counter_short = _counter_flags(out, rich, s)
    out.cvd_div_long, out.cvd_div_short = _cvd_divergence_flags(out, s["cvd_div_lookback"])
    return out


def _counter_flags(x: _Series, rich: pd.DataFrame, s: dict) -> tuple[np.ndarray, np.ndarray]:
    """Per candle: the last N candles all went against a long (red / falling) or against a short (green / rising)."""
    n, rule, fast, body = s["counter_count"], s["counter_rule"], s["counter_need_fast"], s["counter_min_body"]
    opened = rich["open"].to_numpy(dtype=float)
    colour = np.sign(x.close - opened)
    step = np.r_[np.nan, np.diff(x.close)]

    def run(sign: float) -> np.ndarray:
        ok = np.ones(len(x.close), dtype=bool)
        if rule in ("COLOUR", "BOTH"):
            ok &= colour == sign
        if rule in ("CLOSES", "BOTH"):
            ok &= np.sign(step) == sign
        if body > 0:
            ok &= np.abs(x.close - opened) >= body * x.atr
        out = ok.copy()
        for k in range(1, n):
            shifted = np.r_[np.zeros(k, dtype=bool), ok[:-k]]
            out &= shifted
        if fast:
            out &= (x.close - x.fast) * sign > 0
        return out

    # Against a long = a run downwards (sign −1); against a short = upwards.
    return run(-1.0), run(1.0)


def _cvd_divergence_flags(x: _Series, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Per candle (same session, after at least 2 candles): a new high with the delta below its value at the earlier
    high (a long's divergence), and the mirror on lows."""
    size = len(x.close)
    long_flag = np.zeros(size, dtype=bool)
    short_flag = np.zeros(size, dtype=bool)
    for j in range(1, size):
        start = j - n
        if start < 0:
            start = 0
        # same IST session only
        while start < j and x.day[start] != x.day[j]:
            start += 1
        if start >= j:
            continue
        highs = x.high[start:j]
        lows = x.low[start:j]
        if np.all(np.isnan(highs)):
            continue
        at = int(np.nanargmax(highs))
        if x.high[j] > highs[at] and x.cvd[j] < x.cvd[start + at]:
            long_flag[j] = True
        at = int(np.nanargmin(lows))
        if x.low[j] < lows[at] and x.cvd[j] > x.cvd[start + at]:
            short_flag[j] = True
    return long_flag, short_flag


def _entry_checks(x: _Series, rich: pd.DataFrame, i: int, side: str, s: dict, lv: dict | None) -> dict[str, bool]:
    """Every entry check's answer at signal candle `i` (True = it agrees), the bot's arithmetic on closed candles."""
    long = side == "LONG"
    close = x.close[i]
    out: dict[str, bool] = {}
    v = x.vwap[i]
    out["vwap"] = bool(np.isfinite(v) and (close >= v if long else close <= v))
    out["volume"] = _volume_ok(rich, i, s["volume_lookback"], s["volume_min_ratio"])
    span = x.high[i] - x.low[i]
    density = 0.0 if span <= 0 else abs(close - x.open[i]) / span * 100
    out["density"] = bool(density + 1e-9 >= s["density_min_pct"])
    r = x.rsi[i]
    lo, hi = (s["rsi_long_min"], s["rsi_long_max"]) if long else (s["rsi_short_min"], s["rsi_short_max"])
    out["rsi"] = bool(np.isfinite(r) and lo <= r <= hi)
    m, u, b = x.bb_mid[i], x.bb_up[i], x.bb_lo[i]
    if np.isfinite(m) and np.isfinite(u) and np.isfinite(b) and m > 0:
        width = (u - b) / m * 100
        squeeze = s["bb_min_width_pct"] > 0 and width < s["bb_min_width_pct"]
        stretched = close > u if long else close < b
        out["bollinger"] = not (squeeze or stretched)
    else:
        out["bollinger"] = False
    g = x.gap[i]
    glo, ghi = (s["gap_long_min"], s["gap_long_max"]) if long else (s["gap_short_min"], s["gap_short_max"])
    out["gap"] = bool(np.isfinite(g) and glo <= ghi and glo <= g <= ghi)
    out["candle_dir"] = _candle_dir_ok(rich, i, side, s["candle_dir_count"], s["candle_dir_rule"])
    e = x.ema_hist[i]
    out["ema"] = bool(i + 1 >= max(s["ema_fast"], s["ema_slow"]) and np.isfinite(e) and (e > 0 if long else e < 0))
    mh = x.macd_hist[i]
    out["macd"] = bool(i + 1 >= max(s["macd"][0], s["macd"][1]) + s["macd"][2] and np.isfinite(mh) and (mh > 0 if long else mh < 0))
    # cumulative delta: risen over the last n candles of this session (since the open when fewer)
    n = s["cvd_lookback"]
    first = i
    while first > 0 and x.day[first - 1] == x.day[i]:
        first -= 1
    have = i - first + 1
    before = x.cvd[i - n] if have > n else 0.0
    change = x.cvd[i] - before
    out["cvd"] = bool(change > 0 if long else change < 0)
    t = x.st_trend[i]
    out["supertrend"] = bool(t == (1 if long else -1))
    out["level"] = _level_ok(x, i, side, s, lv)
    a = x.adx[i]
    out["adx"] = bool(np.isfinite(a) and a >= s["adx_threshold"])
    return out


def _level_ok(x: _Series, i: int, side: str, s: dict, lv: dict | None) -> bool:
    line = s["level_line"]
    close = x.close[i]
    long = side == "LONG"
    if line == "BB_MIDDLE":
        value = x.bb_mid[i]
    elif line == "BB_UPPER":
        value = x.bb_up[i] if long else x.bb_lo[i]
    elif line == "PIVOT_P":
        value = lv["p"] if lv else np.nan
    else:
        value = (lv["r1"] if long else lv["s1"]) if lv else np.nan
    if not np.isfinite(value):
        return False
    return bool(close > value if long else close < value)


def _fill_after(x: _Series, j: int, square_ts: int) -> tuple[int, float] | None:
    """The bot acts on candle j's close at the next candle's open, on the same day and before the square-off."""
    k = j + 1
    if k >= len(x.ts) or x.day[k] != x.day[j] or x.ts[k] >= square_ts:
        return None
    return int(x.ts[k]), float(x.open[k])


def _exit_candidates(x: _Series, e: int, side: str, s: dict, square_ts: int, rich: pd.DataFrame) -> dict[str, list | None]:
    """For each exit rule, the first candle at or after entry candle `e` on which it fires, as [ts, price] of the fill."""
    long = side == "LONG"
    sign = 1.0 if long else -1.0
    out: dict[str, list | None] = {name: None for name in EXIT_RULES}
    last = e
    while last + 1 < len(x.ts) and x.day[last + 1] == x.day[e]:
        last += 1
    rng = range(e, last + 1)

    def first(flags) -> list | None:
        for j in rng:
            if flags(j):
                fill = _fill_after(x, j, square_ts)
                return list(fill) if fill else None
        return None

    diff = x.fast - x.slow
    out["cross"] = first(lambda j: np.isfinite(diff[j]) and np.isfinite(diff[j - 1]) and ((diff[j - 1] >= 0 > diff[j]) if long else (diff[j - 1] <= 0 < diff[j])))
    out["bb_band"] = first(lambda j: np.isfinite(x.bb_up[j]) and (x.close[j] >= x.bb_up[j] if long else x.close[j] <= x.bb_lo[j]))
    armed = False

    def middle(j: int) -> bool:
        nonlocal armed
        m = x.bb_mid[j]
        if not np.isfinite(m):
            return False
        if armed and ((x.close[j] < m) if long else (x.close[j] > m)):
            return True
        armed = armed or ((x.close[j] > m) if long else (x.close[j] < m))
        return False

    out["bb_middle"] = first(middle)
    out["supertrend"] = first(lambda j: j > 0 and x.st_trend[j] != 0 and x.st_trend[j - 1] != 0 and x.st_trend[j] != x.st_trend[j - 1] and x.st_trend[j] == (-1 if long else 1))
    need_ema = max(s["ema_fast"], s["ema_slow"])
    out["ema"] = first(lambda j: j + 1 >= need_ema and j > 0 and np.isfinite(x.ema_hist[j]) and ((x.ema_hist[j - 1] >= 0 > x.ema_hist[j]) if long else (x.ema_hist[j - 1] <= 0 < x.ema_hist[j])))
    need_macd = max(s["macd"][0], s["macd"][1]) + s["macd"][2]
    out["macd"] = first(lambda j: j + 1 >= need_macd and j > 0 and np.isfinite(x.macd_hist[j]) and ((x.macd_hist[j - 1] >= 0 > x.macd_hist[j]) if long else (x.macd_hist[j - 1] <= 0 < x.macd_hist[j])))
    div = x.cvd_div_long if long else x.cvd_div_short
    out["cvd"] = first(lambda j: bool(div[j]))
    n = s["counter_count"]
    counter = x.counter_long if long else x.counter_short
    out["counter"] = first(lambda j: j - e + 1 >= n and bool(counter[j]))
    out["slow_sma"] = first(lambda j: np.isfinite(x.slow[j]) and ((x.close[j] < x.slow[j]) if long else (x.close[j] > x.slow[j])))
    # SMA 9 slope exits: the same judges the bot runs, one state per rule.
    turn_cfg = SimpleNamespace(slope_turn=True, slope_bend_pct=0.0, slope_turn_pct=s["slope_turn_pct"])
    bend_cfg = SimpleNamespace(slope_turn=False, slope_bend_pct=s["slope_bend_pct"], slope_turn_pct=s["slope_turn_pct"])
    v_cfg = SimpleNamespace(vturn_move_pct=s["vturn_move_pct"], vturn_turn_pct=s["vturn_turn_pct"], vturn_within=s["vturn_within"])
    judges = {
        "slope_turn": lambda v, st: slope_exit.judge(side, v, st, turn_cfg),
        "slope_bend": lambda v, st: slope_exit.judge(side, v, st, bend_cfg),
        "vturn": lambda v, st: slope_exit.judge_vturn(side, v, st, v_cfg),
    }
    states: dict[str, dict] = {name: {} for name in judges}
    pending = set(judges)
    for j in rng:
        value = x.slope[j]
        if not np.isfinite(value) or not pending:
            continue
        for name in list(pending):
            if judges[name](float(value), states[name]):
                fill = _fill_after(x, j, square_ts)
                out[name] = list(fill) if fill else None
                pending.discard(name)
    out["decel"] = _decel_candidate(x, rich, e, last, side, s, square_ts)
    return out


def _decel_candidate(x: _Series, rich: pd.DataFrame, e: int, last: int, side: str, s: dict, square_ts: int) -> list | None:
    start = max(0, e - 400)
    part = rich.iloc[start : last + 1].reset_index(drop=True)
    if len(part) < s["sma_fast"] + 14:
        return None
    cfg = SimpleNamespace(**{f"decel_{k}": v for k, v in s["decel"].items()})
    try:
        flags = early_warning(part, side, e - start, cfg, s["sma_fast"])["early_warning_exit"].to_numpy()
    except Exception:  # noqa: BLE001
        return None
    for j in range(e, last + 1):
        if flags[j - start]:
            fill = _fill_after(x, j, square_ts)
            return list(fill) if fill else None
    return None


def _stop_walk(
    tape_ts: np.ndarray, tape_high: np.ndarray, tape_low: np.ndarray, m0: int, m_end: int, side: str, level_at
) -> tuple[int, float] | None:
    """Walk the 1-minute candles from `m0` (the entry minute) to `m_end` (exclusive): the first minute the stop is
    hit. `level_at(m)` gives the stop level in force at the start of minute m (None = no stop yet). The stop in
    force when a minute starts is judged on that minute's low / high (the cautious order: hit before trailing)."""
    long = side == "LONG"
    for m in range(m0, m_end):
        level = level_at(m)
        if level is None or not np.isfinite(level):
            continue
        if (long and tape_low[m] <= level) or (not long and tape_high[m] >= level):
            return int(tape_ts[m]), float(level)
    return None


def _target_walk(tape_ts, tape_high, tape_low, m0: int, m_end: int, side: str, level: float) -> tuple[int, float] | None:
    long = side == "LONG"
    for m in range(m0, m_end):
        if (long and tape_high[m] >= level) or (not long and tape_low[m] <= level):
            return int(tape_ts[m]), float(level)
    return None


def analyze_stock(symbol: str, tape: pd.DataFrame | None, cfg, days: list[dt.date]) -> tuple[list[dict], dict]:
    """The stock's base trades with every rule's answer, and the settings they were read with."""
    s = lab_settings(cfg)
    empty: tuple[list[dict], dict] = ([], s)
    if tape is None or getattr(tape, "empty", True) or len(tape) < s["sma_slow"] * s["candle_minutes"] + 5:
        return empty
    tape = tape.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    minutes = s["candle_minutes"]
    frame = resample(tape, minutes) if minutes > 1 else tape
    if frame is None or len(frame) < s["sma_slow"] + 3:
        return empty
    rich = indicators.enrich(frame.reset_index(drop=True), s["sma_fast"], s["sma_slow"], s["atr_period"])
    x = _build_series(rich, s)
    t_ts = tape["ts"].to_numpy(dtype="int64")
    t_open = tape["open"].to_numpy(dtype=float)
    t_high = tape["high"].to_numpy(dtype=float)
    t_low = tape["low"].to_numpy(dtype=float)
    t_close = tape["close"].to_numpy(dtype=float)
    t_day = _day_of(t_ts)
    wanted = {int((dt.datetime.combine(d, dt.time(0, 0), tzinfo=IST).timestamp() + 19800) // 86400) for d in days}
    first_entry = _time_minutes(FIRST_ENTRY)
    cutoff = _time_minutes(_hhmm(s["entry_cutoff"], "15:00"))
    square = _time_minutes(_hhmm(s["square_off"], "15:15"))
    diff = x.fast - x.slow
    trades: list[dict] = []
    pivot_cache: dict[int, dict | None] = {}
    xc_cache: dict[int, pd.DataFrame | None] = {}
    or_cache: dict[tuple, bool] = {}
    day_hl = _day_highs_lows(t_ts, t_high, t_low, t_day)
    day_list = np.unique(t_day)
    signals: list[tuple[int, str, str, list[float] | None]] = []
    for i in range(1, len(x.ts) - 1):
        a, b = diff[i - 1], diff[i]
        if not (np.isfinite(a) and np.isfinite(b)):
            continue
        if a <= 0 < b:
            signals.append((i, "LONG", "cross", None))
        elif a >= 0 > b:
            signals.append((i, "SHORT", "cross", None))
    signals += [(i, side, "stack", steps) for i, side, steps in _stack_signals(rich, s) if i < len(x.ts) - 1]
    signals.sort(key=lambda sig: (sig[0], sig[2]))
    for i, side, source, gap_steps in signals:
        if x.day[i] not in wanted:
            continue
        e = i + 1
        if x.day[e] != x.day[i]:
            continue
        entry_min = _minute_of_day(int(x.ts[e]))
        if entry_min < first_entry or entry_min >= cutoff:
            continue
        atr = x.atr[i]
        if not np.isfinite(atr) or atr <= 0:
            continue
        entry_ts = int(x.ts[e])
        entry = float(x.open[e])
        day_key = int(x.day[i])
        day_date = dt.datetime.fromtimestamp(entry_ts, IST).date()
        if day_key not in pivot_cache:
            pivot_cache[day_key] = pivots.levels(tape, day_date)
        lv = pivot_cache[day_key]
        # 1-minute walk bounds: the entry minute up to the square-off minute of that day.
        m0 = int(np.searchsorted(t_ts, entry_ts, side="left"))
        m_sq = m0
        while m_sq < len(t_ts) and t_day[m_sq] == day_key and _minute_of_day(int(t_ts[m_sq])) < square:
            m_sq += 1
        if m_sq < len(t_ts) and t_day[m_sq] == day_key:
            square_ts, square_price = int(t_ts[m_sq]), float(t_open[m_sq])
        else:
            m_last = m_sq - 1
            square_ts, square_price = int(t_ts[m_last]) + 60, float(t_close[m_last])
        if m0 >= m_sq:
            continue
        sign = 1.0 if side == "LONG" else -1.0
        checks = _entry_checks(x, rich, i, side, s, lv)
        checks.update(_extra_checks(symbol, cfg, s, tape, rich, e, side, entry_ts, minutes, or_cache, m0))
        exits = _exit_candidates(x, e, side, s, square_ts, rich)
        exits["exit_candle"] = _exit_candle_hit(cfg, s, tape, rich, xc_cache, day_key, side, entry_ts, square_ts, t_ts, t_open, minutes)
        flow_pts = trade_flow.minutes(symbol, tape, flow_exit.first_minute(entry_ts), square_ts)
        exits["flow_exit"] = _flow_exit_hit(flow_pts, s, side, entry_ts, square_ts, t_ts, t_open, minutes)
        # Stops on the 1-minute candles.
        atr_stop = round_price(symbol, entry - sign * s["atr_multiplier"] * atr)
        stops: dict[str, list | None] = {}
        hit = _stop_walk(t_ts, t_high, t_low, m0, m_sq, side, lambda m: atr_stop)
        stops["ATR"] = list(hit) if hit else None
        sl_pts, step, target_pts = tsl_mod.tsl_settings(cfg, entry_price=entry, atr=float(atr))
        best = [entry]

        def tsl_level(m: int) -> float:
            # the stop in force at the start of minute m: the best price up to the minute before
            level = tsl_mod.tsl_stop(side, entry, sl_pts, step, best[0])
            best[0] = max(best[0], t_high[m]) if side == "LONG" else min(best[0], t_low[m])
            return round_price(symbol, level)

        hit = _stop_walk(t_ts, t_high, t_low, m0, m_sq, side, tsl_level)
        stops["TSL"] = list(hit) if hit else None
        span = minutes * 60
        stops["SUPERTREND"] = _line_stop(x, t_ts, t_high, t_low, m0, m_sq, side, entry, atr_stop, i, span, symbol, lambda j: _st_guard(x, j, side))
        stops["VWAP"] = _line_stop(x, t_ts, t_high, t_low, m0, m_sq, side, entry, atr_stop, i, span, symbol, lambda j: _vwap_guard(x, j, side, s["vwap_stop_atr"]))
        breakeven = stop_plan.breakeven(side, entry, s["qty"])
        # the smart stop reads only its own sessions before the entry (smart_days), not the whole range
        first_day = day_list[max(0, int(np.searchsorted(day_list, day_key)) - int(s["smart"]["days"]))]
        smart_tape = tape.iloc[int(np.searchsorted(t_day, first_day, side="left")) : m0 + 1]
        stops["SMART"] = _smart_stop(cfg, smart_tape, t_ts, t_high, t_low, m0, m_sq, side, entry, entry_ts, atr, s, symbol, minutes, breakeven)
        stops["PLAN"] = _plan_stop(cfg, x, t_ts, t_high, t_low, m0, m_sq, side, entry, atr_stop, i, span, symbol, breakeven)
        targets: dict[str, list | None] = {"tsl": None, "pivot_r1": None, "pivot_r2": None}
        if target_pts > 0:
            goal = entry + sign * target_pts
            hit = _target_walk(t_ts, t_high, t_low, m0, m_sq, side, round_price(symbol, goal))
            targets["tsl"] = list(hit) if hit else None
        for mode, key in (("R1", "pivot_r1"), ("R2", "pivot_r2")):
            goal = pivots.target_for(side, entry, lv, mode)
            if goal is not None:
                hit = _target_walk(t_ts, t_high, t_low, m0, m_sq, side, float(goal[1]))
                targets[key] = list(hit) if hit else None
        goal = entry + sign * s["atr_target_mult"] * float(atr)
        hit = _target_walk(t_ts, t_high, t_low, m0, m_sq, side, round_price(symbol, goal))
        targets["atr"] = list(hit) if hit else None
        day_goal = day_target.target_for(side, entry, _day_levels(day_hl, t_ts, t_high, t_low, t_day, day_key, entry_ts, day_target.skip_min(cfg)), cfg)
        hit = _target_walk(t_ts, t_high, t_low, m0, m_sq, side, round_price(symbol, day_goal[2])) if day_goal else None
        targets["day_level"] = list(hit) if hit else None
        targets["net"] = _net_target(cfg, t_ts, t_high, t_low, m0, m_sq, side, entry, float(atr), s["qty"], symbol)
        held = t_high[m0:m_sq] if side == "LONG" else t_low[m0:m_sq]
        worst = t_low[m0:m_sq] if side == "LONG" else t_high[m0:m_sq]
        trades.append(
            {
                "symbol": symbol,
                "day": day_date.isoformat(),
                "side": side,
                # What opened it: "cross" (SMA cross) or "stack" (three SMAs stacked, gaps widening; gap_steps = the
                # "each gap at least" levels this is the entry for).
                "source": source,
                **({"gap_steps": gap_steps} if gap_steps is not None else {}),
                "cross_ts": int(x.ts[i]),
                "entry_ts": entry_ts,
                "entry_price": entry,
                "atr": float(atr),
                "qty": s["qty"],
                "checks": checks,
                "exits": exits,
                "stops": stops,
                "targets": targets,
                "square": [square_ts, square_price],
                "best": float(sign * (float(held.max() if side == "LONG" else held.min()) - entry)) if len(held) else 0.0,
                "worst": float(sign * (float(worst.min() if side == "LONG" else worst.max()) - entry)) if len(worst) else 0.0,
            }
        )
    return trades, s


def _day_highs_lows(t_ts, t_high, t_low, t_day) -> dict[int, tuple[float, float]]:
    """Each day's high and low of the 1-minute tape (for yesterday's level of the day high / low target)."""
    out: dict[int, tuple[float, float]] = {}
    for key in np.unique(t_day):
        sel = t_day == key
        out[int(key)] = (float(np.nanmax(t_high[sel])), float(np.nanmin(t_low[sel])))
    return out


def _day_levels(day_hl: dict, t_ts, t_high, t_low, t_day, day_key: int, entry_ts: int, skip: int) -> dict | None:
    """`day_target.levels` on the arrays: today's high / low from the 1-minute candles that began before the entry minute
    (from 09:15 + `skip` minutes) and the session before's high / low."""
    minute = int(entry_ts) // 60 * 60
    sel = (t_day == day_key) & (t_ts < minute)
    if skip > 0:
        sel &= t_ts >= (day_key * 86400 - 19800) + (9 * 60 + 15 + skip) * 60
    out = {"today_high": None, "today_low": None, "prev_high": None, "prev_low": None, "prev_day": None}
    if sel.any():
        out["today_high"] = float(np.nanmax(t_high[sel]))
        out["today_low"] = float(np.nanmin(t_low[sel]))
    earlier = [k for k in day_hl if k < day_key]
    if earlier:
        prev = max(earlier)
        out["prev_high"], out["prev_low"] = day_hl[prev]
        out["prev_day"] = dt.date(1970, 1, 1).fromordinal(dt.date(1970, 1, 1).toordinal() + prev).isoformat()
    if all(out[k] is None for k in ("today_high", "prev_high")):
        return None
    return out


#: Sessions of history the fresh-trend and opening-range checks read before the signal's day (their volume compares
#: with earlier sessions); a fixed window keeps the answer the same on the range's first and last day, and quick.
CHECK_SESSIONS = 10

#: The opening-range checks that read only the 09:15-09:30 window (and the first candle after it): once that candle
#: has closed their answer is the same for every signal of the day and side, so the lab reads them once.
_OR_DAY_CHECKS = ("or_volume", "or_vwap", "or_break")


def _extra_checks(symbol: str, cfg, s: dict, tape: pd.DataFrame, rich: pd.DataFrame, e: int, side: str, entry_ts: int,
                  minutes: int, or_cache: dict | None = None, m0: int | None = None) -> dict[str, bool]:
    """The checks the bot's own modules answer (fresh trend, opening range, executed volume, smaller candles), read on
    the candles closed at the signal candle's close; `rich.iloc[: e + 1]` keeps the entry candle as the forming one, as
    the bot sees it."""
    out: dict[str, bool] = {}
    days = np.unique(rich_days := ((rich["ts"].to_numpy(dtype="int64")[: e + 1] + 19800) // 86400))
    since = days[max(0, len(days) - 1 - CHECK_SESSIONS)]
    frame = rich.iloc[int(np.searchsorted(rich_days, since, side="left")) : e + 1]
    got = fresh_entry.read(frame, side, _Over(cfg, fresh_rvol=s["fresh_rvol"]))
    out["fresh"] = bool(got and all(got[0].values()))
    day = int((entry_ts + 19800) // 86400)
    ready_at = day * 86400 - 19800 + (9 * 60 + 30 + minutes) * 60  # the first candle from 09:30 has closed
    for name in opening_range.CHECKS:
        key = (day, side, name)
        if or_cache is not None and name in _OR_DAY_CHECKS and entry_ts >= ready_at and key in or_cache:
            out[name] = or_cache[key]
            continue
        try:
            why = opening_range.entry_reason(frame, side, _Over(cfg, **{f"use_{name}": True}), only=name)
        except Exception:  # noqa: BLE001 - a check that cannot be read refuses, like the bot
            why = "not readable"
        out[name] = why is None
        if or_cache is not None and name in _OR_DAY_CHECKS and entry_ts >= ready_at:
            or_cache[key] = out[name]
    win = s["flow_entry_window"]
    start, end = flow_exit.entry_span(entry_ts, win, minutes)
    pts = trade_flow.minutes(symbol, tape, start, end)
    out["flow"] = flow_exit.entry_refusal(pts, side, s["flow_entry_pct"], max(1, (end - start) // 60)) is None
    if s["sc_minutes"]:
        if m0 is not None:
            # enough minutes for the largest smaller candle's run (5 candles of 10 minutes), not the whole warm-up
            cut = tape.iloc[max(0, m0 - 150) : m0 + 1]
        else:
            ts = pd.to_numeric(tape["ts"], errors="coerce")
            cut = tape[ts <= entry_ts]
        over = _Over(cfg, sc_entry=True, sc_minutes=s["sc_minutes"], sma_fast=s["sma_fast"], sma_slow=s["sma_slow"])
        out["small_colour"] = small_colour.entry_reason(cut, side, over, minutes) is None
    else:
        out["small_colour"] = True  # 1-minute candles: nothing smaller to read
    return out


def _fill_at(t_ts: np.ndarray, t_open: np.ndarray, at_ts: int, square_ts: int) -> list | None:
    """The open of the first 1-minute candle at or after `at_ts`, before the square-off."""
    m = int(np.searchsorted(t_ts, at_ts, side="left"))
    if m >= len(t_ts) or t_ts[m] >= square_ts:
        return None
    return [int(t_ts[m]), float(t_open[m])]


def _exit_candle_hit(cfg, s: dict, tape: pd.DataFrame, rich: pd.DataFrame, cache: dict, day_key: int, side: str,
                     entry_ts: int, square_ts: int, t_ts: np.ndarray, t_open: np.ndarray, own: int) -> list | None:
    """The SMA cross exit read on the exit candle (exit_candle.judge), once per exit candle closed after the entry."""
    size = s["exit_candle_minutes"]
    if not size or size == own:
        return None
    if day_key not in cache:
        cut = tape[t_ts < square_ts + size * 60]
        cache[day_key] = exit_candle.closed_frame(cut, size, s["sma_fast"], s["sma_slow"])
    full = cache[day_key]
    if full is None or full.empty:
        return None
    ts = full["ts"].to_numpy(dtype="int64")
    r_ts = rich["ts"].to_numpy(dtype="int64")
    for k in np.nonzero(ts >= entry_ts)[0]:
        closed_at = int(ts[k]) + size * 60
        if closed_at > square_ts:
            break
        own_closed = rich.iloc[: int(np.searchsorted(r_ts + own * 60, closed_at, side="right"))]
        hit, _ = exit_candle.judge(full.iloc[: k + 1], own_closed, side, s["sma_fast"], s["sma_slow"])
        if hit:
            return _fill_at(t_ts, t_open, closed_at, square_ts)
    return None


def _flow_exit_hit(points: list[dict], s: dict, side: str, entry_ts: int, square_ts: int, t_ts: np.ndarray,
                   t_open: np.ndarray, own: int) -> list | None:
    """The executed volume exit (flow_exit.judge): the first closed window after the entry in which the other side did
    the % of the shares traded; filled at the next minute's open."""
    if not points:
        return None
    win = s["flow_exit_window"]
    first = flow_exit.first_minute(entry_ts)
    for end in range(first + 60, square_ts + 1, 60):
        sp = flow_exit.span(end, entry_ts, win, own)
        if sp is None:
            continue
        part = [p for p in points if sp[0] <= p["time"] < sp[1]]
        verdict, _ = flow_exit.judge(part, side, s["flow_exit_pct"], max(1, (sp[1] - sp[0]) // 60))
        if verdict:
            return _fill_at(t_ts, t_open, end, square_ts)
    return None


def _smart_stop(cfg, tape, t_ts, t_high, t_low, m0, m_sq, side, entry, entry_ts, atr, s, symbol, minutes, breakeven) -> list | None:
    """The smart stop (smart_stop.py): the stock's own room from its dips before the entry, following the best price
    since the entry, tighter only, moved only when it gains enough (no live re-size)."""
    try:
        info = smart_stop.room(tape, side, entry, cfg, before_ts=entry_ts, fallback=s["atr_multiplier"] * float(atr), minutes=minutes)
    except Exception:  # noqa: BLE001
        return None
    room_rs = float(info.get("rs") or 0.0)
    if room_rs <= 0:
        return None
    long = side == "LONG"
    follow = bool(s["smart"].get("follow", True))
    current = [round_price(symbol, entry - room_rs if long else entry + room_rs)]
    best = [entry]

    def level_at(m: int) -> float:
        want = round_price(symbol, smart_stop.want(side, entry, best[0], room_rs, breakeven, follow))
        tighter = want > current[0] if long else want < current[0]
        if tighter and smart_stop.worth_moving(current[0], want, room_rs):
            current[0] = want
        best[0] = max(best[0], t_high[m]) if long else min(best[0], t_low[m])
        return current[0]

    hit = _stop_walk(t_ts, t_high, t_low, m0, m_sq, side, level_at)
    return list(hit) if hit else None


def _plan_stop(cfg, x, t_ts, t_high, t_low, m0, m_sq, side, entry, atr_stop, signal_i, span, symbol, breakeven) -> list | None:
    """The staged stop plan (stop_plan.py) on the ATR stop: breakeven plus charges at +plan_be_r R, then the Supertrend
    line (never under breakeven) from +plan_trail_r R; tighter only, stages only forward."""
    r_ps = stop_plan.risk(entry, atr_stop)
    if not r_ps:
        return None
    long = side == "LONG"
    current = [atr_stop]
    best = [entry]
    stage = [0]
    next_j = [signal_i + 1]
    line = [None]

    def level_at(m: int) -> float:
        while next_j[0] < len(x.ts) and x.ts[next_j[0]] + span <= t_ts[m]:
            line[0] = _st_guard(x, next_j[0], side)
            next_j[0] += 1
        stage[0] = max(stage[0], stop_plan.stage(side, entry, best[0], r_ps, cfg))
        want = current[0]
        if stage[0] >= 1:
            want = breakeven
        if stage[0] >= 2 and line[0] is not None:
            want = max(breakeven, line[0]) if long else min(breakeven, line[0])
        want = round_price(symbol, want)
        if (long and want > current[0]) or (not long and want < current[0]):
            current[0] = want
        best[0] = max(best[0], t_high[m]) if long else min(best[0], t_low[m])
        return current[0]

    hit = _stop_walk(t_ts, t_high, t_low, m0, m_sq, side, level_at)
    return list(hit) if hit else None


def _net_target(cfg, t_ts, t_high, t_low, m0, m_sq, side, entry, atr, qty, symbol) -> list | None:
    """The profit safety net (target_net.py): from the start distance on, keep the % of the best profit per share; the
    first minute whose low (high for a short) reaches that floor closes. The floor uses the best price before the
    minute (the cautious order)."""
    begin = target_net.start_level(side, entry, qty, atr, cfg)
    if begin is None:
        return None
    long = side == "LONG"
    best = entry
    for m in range(m0, m_sq):
        got = target_net.floor(side, entry, begin[1], best, cfg)
        if got is not None:
            level = round_price(symbol, got[0])
            if (long and t_low[m] <= level) or (not long and t_high[m] >= level):
                return [int(t_ts[m]), float(level)]
        best = max(best, t_high[m]) if long else min(best, t_low[m])
    return None


def _st_guard(x: _Series, j: int, side: str) -> float | None:
    """The Supertrend line on closed candle j when it guards `side`."""
    t, line = x.st_trend[j], x.st_line[j]
    if not np.isfinite(line) or t == 0:
        return None
    if (side == "LONG" and t == 1) or (side == "SHORT" and t == -1):
        return float(line)
    return None


def _vwap_guard(x: _Series, j: int, side: str, k: float) -> float | None:
    v, a = x.vwap[j], x.atr[j]
    if not (np.isfinite(v) and np.isfinite(a)):
        return None
    return float(v - k * a) if side == "LONG" else float(v + k * a)


def _line_stop(x, t_ts, t_high, t_low, m0, m_sq, side, entry, atr_stop, signal_i, span, symbol, guard) -> list | None:
    """A stop that sits on a line read once per closed candle (Supertrend / VWAP ± ATR), tighter only; the ATR stop
    until the line guards the real position (beyond the fill), as the bot does. `span` = the candle in seconds."""
    long = side == "LONG"

    def beyond(level: float) -> bool:
        return level < entry if long else level > entry

    level0 = guard(signal_i)
    current = [round_price(symbol, level0) if level0 is not None and beyond(level0) else atr_stop]
    next_j = [signal_i + 1]

    def level_at(m: int) -> float:
        # move the stop once the candle before minute m has closed
        while next_j[0] < len(x.ts) and x.ts[next_j[0]] + span <= t_ts[m]:
            line = guard(next_j[0])
            if line is not None and beyond(line):
                proposed = round_price(symbol, line)
                if (long and proposed > current[0]) or (not long and proposed < current[0]):
                    current[0] = proposed
            next_j[0] += 1
        return current[0]

    hit = _stop_walk(t_ts, t_high, t_low, m0, m_sq, side, level_at)
    return list(hit) if hit else None


# ----------------------------------------------------------------------------------------------------------------
# Evaluate a choice
# ----------------------------------------------------------------------------------------------------------------


def clean_choice(raw: dict | None) -> dict:
    raw = raw or {}
    entries = [e for e in (raw.get("entries") or []) if e in ENTRY_CHECKS]
    exits = [e for e in (raw.get("exits") or []) if e in EXIT_RULES]
    stop = str(raw.get("stop") or "NONE").upper()
    if stop not in STOPS:
        stop = "NONE"
    targets = [t for t in (raw.get("targets") or []) if t in TARGETS]
    entry_from = _hhmm(raw.get("entry_from"), "09:20")
    entry_to = _hhmm(raw.get("entry_to"), "15:30")
    try:
        qty = max(0, int(raw.get("qty") or 0))
    except (TypeError, ValueError):
        qty = 0
    # Only these stocks count (the lab's Stocks tab); empty = every stock of the run.
    symbols = sorted({str(x).strip().upper() for x in (raw.get("symbols") or []) if str(x).strip()})
    try:
        margin = float(raw.get("margin_pct") or DEFAULT_MARGIN_PCT)
    except (TypeError, ValueError):
        margin = DEFAULT_MARGIN_PCT
    return {
        "entries": sorted(set(entries)),
        "exits": sorted(set(exits)),
        "stop": stop,
        "targets": sorted(set(targets)),
        "entry_from": entry_from.strftime("%H:%M"),
        "entry_to": entry_to.strftime("%H:%M"),
        "qty": qty,
        "symbols": symbols,
        "margin_pct": round(min(100.0, max(1.0, margin)), 2),
        "source": str(raw.get("source") or "CROSS").upper() if str(raw.get("source") or "CROSS").upper() in ENTRY_SOURCES else "CROSS",
        "stack_gap": snap_gap(raw.get("stack_gap", 0.05)),
    }


#: Intraday margin blocked per trade, % of its value (Groww MIS, 5x); the practice wallet's default too.
DEFAULT_MARGIN_PCT = 20.0


def capital(kept: list[dict], margin_pct: float = DEFAULT_MARGIN_PCT) -> dict:
    """The money the kept trades needed: each trade's full value (qty × entry price), the most in use at one moment
    (trades open at the same time add up; one that closes at a minute frees its money before another opens then),
    the busiest day, and the intraday margin that blocks (`margin_pct` of the value)."""
    if not kept:
        return {"peak_value": 0.0, "peak_margin": 0.0, "peak_at": None, "peak_open": 0, "max_trade_value": 0.0,
                "avg_trade_value": 0.0, "margin_pct": margin_pct, "return_pct": None, "return_on_margin_pct": None,
                "per_day": []}
    events: list[tuple[int, int, float]] = []
    for k in kept:
        value = float(k["qty"]) * float(k["entry_price"])
        events.append((int(k["entry_ts"]), 1, value))
        events.append((int(k["exit_ts"]), 0, -value))
    events.sort()
    used = peak = 0.0
    open_now = peak_open = 0
    peak_at = None
    day_peak: dict[str, float] = {}
    for ts, kind, value in events:
        used += value
        open_now += 1 if kind else -1
        day = dt.datetime.fromtimestamp(ts, IST).date().isoformat()
        day_peak[day] = max(day_peak.get(day, 0.0), used)
        if used > peak + 1e-9:
            peak, peak_at, peak_open = used, ts, open_now
    values = [float(k["qty"]) * float(k["entry_price"]) for k in kept]
    gross = sum(k["gross"] for k in kept)
    margin = peak * margin_pct / 100.0
    return {
        "peak_value": round(peak, 2),
        "peak_margin": round(margin, 2),
        "peak_at": dt.datetime.fromtimestamp(peak_at, IST).isoformat(timespec="minutes") if peak_at else None,
        "peak_open": peak_open,
        "max_trade_value": round(max(values), 2),
        "avg_trade_value": round(sum(values) / len(values), 2),
        "margin_pct": margin_pct,
        "return_pct": round(gross / peak * 100, 2) if peak else None,
        "return_on_margin_pct": round(gross / margin * 100, 2) if margin else None,
        "per_day": [{"day": d, "peak_value": round(v, 2), "peak_margin": round(v * margin_pct / 100.0, 2)} for d, v in sorted(day_peak.items())],
    }


def _trade_result(t: dict, choice: dict) -> dict | None:
    """The kept trade's exit under `choice`, or None when a chosen entry check refused it."""
    checks = t.get("checks") or {}
    for name in choice["entries"]:
        if not checks.get(name, False):
            return None
    best: tuple[int, float, str] = (int(t["square"][0]), float(t["square"][1]), "square")
    candidates: list[tuple[int, float, str]] = []
    stop = choice["stop"]
    if stop != "NONE":
        hit = (t.get("stops") or {}).get(stop)
        if hit:
            candidates.append((int(hit[0]), float(hit[1]), f"stop:{stop}"))
    for name in choice["targets"]:
        hit = (t.get("targets") or {}).get(name)
        if hit:
            candidates.append((int(hit[0]), float(hit[1]), f"target:{name}"))
    for name in choice["exits"]:
        hit = (t.get("exits") or {}).get(name)
        if hit:
            candidates.append((int(hit[0]), float(hit[1]), name))
    # Earliest wins; at the same minute a stop beats a target beats a candle exit (the cautious reading).
    order = {"stop": 0, "target": 1}
    for c in candidates:
        if c[0] < best[0] or (c[0] == best[0] and order.get(c[2].split(":")[0], 2) < order.get(best[2].split(":")[0], 2)):
            best = c
    return {"exit_ts": best[0], "exit_price": best[1], "reason": EXIT_REASONS.get(best[2], best[2]), "rule": best[2]}


def evaluate(trades: list[dict], choice: dict, settings: dict | None = None, with_trades: bool = True) -> dict:
    """Apply one choice to the base trades: totals, per stock, per day, by exit reason, equity line, trades."""
    choice = clean_choice(choice)
    from_min = _time_minutes(_hhmm(choice["entry_from"], "09:20"))
    to_min = _time_minutes(_hhmm(choice["entry_to"], "15:30"))
    cap = int((settings or {}).get("max_trades_per_stock") or 0)
    wanted = set(choice["symbols"])
    by_symbol: dict[str, list[dict]] = {}
    universe: dict[str, int] = {}
    excluded = 0
    source = choice["source"].lower()
    gap = choice["stack_gap"]
    for t in trades:
        if t.get("source", "cross") != source:
            continue
        if source == "stack" and not any(abs(float(g) - gap) < 1e-9 for g in t.get("gap_steps") or []):
            continue
        universe[t["symbol"]] = universe.get(t["symbol"], 0) + 1
        if wanted and t["symbol"] not in wanted:
            excluded += 1
            continue
        by_symbol.setdefault(t["symbol"], []).append(t)
    kept: list[dict] = []
    refused = held = window = capped = 0
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda r: r["entry_ts"])
        busy_until = -1
        per_day: dict[str, int] = {}
        for t in rows:
            if t["entry_ts"] < busy_until:
                held += 1
                continue
            minute = _minute_of_day(int(t["entry_ts"]))
            if minute < from_min or minute > to_min:
                window += 1
                continue
            if cap and per_day.get(t["day"], 0) >= cap:
                capped += 1
                continue
            res = _trade_result(t, choice)
            if res is None:
                refused += 1
                continue
            qty = choice["qty"] or int(t.get("qty") or 1)
            sign = 1.0 if t["side"] == "LONG" else -1.0
            gross = sign * (res["exit_price"] - t["entry_price"]) * qty
            buy, sell = legs_for(t["side"], t["entry_price"], res["exit_price"])
            fees = calculate_charges(buy, sell, qty)["total_charges"]
            kept.append(
                {
                    "symbol": symbol,
                    "day": t["day"],
                    "side": t["side"],
                    "entry_ts": t["entry_ts"],
                    "entry_price": t["entry_price"],
                    "exit_ts": res["exit_ts"],
                    "exit_price": res["exit_price"],
                    "reason": res["reason"],
                    "qty": qty,
                    "gross": round(gross, 2),
                    "net": round(gross - fees, 2),
                    "charges": round(fees, 2),
                    "best": round(float(t.get("best") or 0.0) * qty, 2),
                    "worst": round(float(t.get("worst") or 0.0) * qty, 2),
                }
            )
            busy_until = res["exit_ts"]
            per_day[t["day"]] = per_day.get(t["day"], 0) + 1
    kept.sort(key=lambda r: (r["entry_ts"], r["symbol"]))
    out = _summarize(kept)
    out["skipped"] = {"refused": refused, "in_position": held, "outside_window": window, "stock_cap": capped,
                      "unticked": excluded}
    out["base_trades"] = len(trades)
    out["capital"] = capital(kept, choice["margin_pct"])
    # Every stock of the run with its base trades (signals), for the Stocks tab's tick boxes.
    out["universe"] = [{"symbol": sym, "signals": n} for sym, n in sorted(universe.items())]
    out["choice"] = choice
    out["settings"] = settings_for_choice(choice, settings)
    if with_trades:
        out["missing"] = missing_rules(trades)
        # A run read before the three-SMA source existed has only cross trades: read the days again for it.
        out["stack_missing"] = bool(trades) and not any("source" in t for t in trades)
        out["stack_lines"] = (settings or {}).get("stack_lines")
        out["stack_widen"] = (settings or {}).get("stack_widen")
        out["trades"] = kept[-MAX_TRADES_OUT:]
        out["trades_cut"] = max(0, len(kept) - MAX_TRADES_OUT)
    return out


def _summarize(kept: list[dict]) -> dict:
    gross = sum(k["gross"] for k in kept)
    net = sum(k["net"] for k in kept)
    wins = [k for k in kept if k["gross"] > 0]
    losses = [k for k in kept if k["gross"] < 0]
    win_sum = sum(k["gross"] for k in wins)
    loss_sum = -sum(k["gross"] for k in losses)
    days: dict[str, dict] = {}
    for k in kept:
        d = days.setdefault(k["day"], {"day": k["day"], "trades": 0, "gross": 0.0, "net": 0.0})
        d["trades"] += 1
        d["gross"] += k["gross"]
        d["net"] += k["net"]
    day_rows = sorted(days.values(), key=lambda d: d["day"])
    cum = 0.0
    peak = 0.0
    drawdown = 0.0
    for d in day_rows:
        cum += d["gross"]
        d["cum_gross"] = round(cum, 2)
        d["gross"] = round(d["gross"], 2)
        d["net"] = round(d["net"], 2)
        peak = max(peak, cum)
        drawdown = min(drawdown, cum - peak)
    stocks: dict[str, dict] = {}
    for k in kept:
        st = stocks.setdefault(k["symbol"], {"symbol": k["symbol"], "trades": 0, "wins": 0, "gross": 0.0, "net": 0.0, "days": set(), "green": set()})
        st["trades"] += 1
        st["wins"] += 1 if k["gross"] > 0 else 0
        st["gross"] += k["gross"]
        st["net"] += k["net"]
        st["days"].add(k["day"])
    day_gross: dict[tuple[str, str], float] = {}
    for k in kept:
        day_gross[(k["symbol"], k["day"])] = day_gross.get((k["symbol"], k["day"]), 0.0) + k["gross"]
    for (sym, day), g in day_gross.items():
        if g > 0:
            stocks[sym]["green"].add(day)
    stock_rows = []
    for st in stocks.values():
        stock_rows.append(
            {
                "symbol": st["symbol"],
                "trades": st["trades"],
                "win_pct": round(st["wins"] / st["trades"] * 100, 1) if st["trades"] else 0.0,
                "gross": round(st["gross"], 2),
                "net": round(st["net"], 2),
                "days": len(st["days"]),
                "green_days": len(st["green"]),
                "per_trade": round(st["gross"] / st["trades"], 2) if st["trades"] else 0.0,
            }
        )
    stock_rows.sort(key=lambda r: -r["gross"])
    # Each stock on each day (the By day rows open into their stocks, the By stock rows into their days).
    pairs: dict[tuple[str, str], dict] = {}
    for k in kept:
        p = pairs.setdefault((k["symbol"], k["day"]), {"symbol": k["symbol"], "day": k["day"], "trades": 0, "wins": 0, "gross": 0.0, "net": 0.0})
        p["trades"] += 1
        p["wins"] += 1 if k["gross"] > 0 else 0
        p["gross"] += k["gross"]
        p["net"] += k["net"]
    pair_rows = [{**p, "gross": round(p["gross"], 2), "net": round(p["net"], 2)} for p in pairs.values()]
    pair_rows.sort(key=lambda r: (r["day"], -r["gross"]))
    reasons: dict[str, dict] = {}
    for k in kept:
        r = reasons.setdefault(k["reason"], {"reason": k["reason"], "trades": 0, "gross": 0.0, "wins": 0})
        r["trades"] += 1
        r["gross"] += k["gross"]
        r["wins"] += 1 if k["gross"] > 0 else 0
    reason_rows = sorted(({**r, "gross": round(r["gross"], 2)} for r in reasons.values()), key=lambda r: -r["trades"])
    green_days = sum(1 for d in day_rows if d["gross"] > 0)
    return {
        "totals": {
            "trades": len(kept),
            "gross": round(gross, 2),
            "net": round(net, 2),
            "charges": round(gross - net, 2),
            "wins": len(wins),
            "win_pct": round(len(wins) / len(kept) * 100, 1) if kept else 0.0,
            "per_trade": round(gross / len(kept), 2) if kept else 0.0,
            "profit_factor": round(win_sum / loss_sum, 2) if loss_sum > 0 else (None if not win_sum else 99.0),
            "days": len(day_rows),
            "green_days": green_days,
            "green_pct": round(green_days / len(day_rows) * 100, 1) if day_rows else 0.0,
            "per_day": round(gross / len(day_rows), 2) if day_rows else 0.0,
            "best_trade": round(max((k["gross"] for k in kept), default=0.0), 2),
            "worst_trade": round(min((k["gross"] for k in kept), default=0.0), 2),
            "best_day": round(max((d["gross"] for d in day_rows), default=0.0), 2),
            "worst_day": round(min((d["gross"] for d in day_rows), default=0.0), 2),
            "drawdown": round(drawdown, 2),
            "stocks": len(stock_rows),
        },
        "per_day": day_rows,
        "per_stock": stock_rows,
        "per_stock_day": pair_rows,
        "by_reason": reason_rows,
    }


STOP_LABELS = {"NONE": "No stop", "ATR": "ATR stop", "TSL": "Trailing stop", "SUPERTREND": "Supertrend stop", "VWAP": "VWAP + ATR stop",
               "SMART": "Smart stop", "PLAN": "ATR stop + staged plan"}


def rule_label(kind: str, name: str) -> str:
    if kind == "entry":
        return ENTRY_CHECKS.get(name, name)
    if kind == "exit":
        return EXIT_RULES.get(name, name)
    if kind == "target":
        return TARGETS.get(name, name)
    return STOP_LABELS.get(name, name)


def missing_rules(trades: list[dict]) -> list[str]:
    """Rules ("entry:fresh", "stop:SMART", ...) a run read before they were in the lab: its base trades carry no answer
    for them, so they are left out of "what each rule adds" until the days are read again."""
    if not trades:
        return []
    t = trades[0]
    have = {
        "entry": set(t.get("checks") or {}),
        "exit": set(t.get("exits") or {}),
        "stop": set(t.get("stops") or {}) | {"NONE"},
        "target": set(t.get("targets") or {}),
    }
    out = [f"entry:{n}" for n in ENTRY_CHECKS if n not in have["entry"]]
    out += [f"exit:{n}" for n in EXIT_RULES if n not in have["exit"]]
    out += [f"stop:{n}" for n in STOPS if n not in have["stop"]]
    out += [f"target:{n}" for n in TARGETS if n not in have["target"]]
    return out


def _toggles(choice: dict, skip: set[str] | frozenset = frozenset()) -> list[tuple[str, str, dict]]:
    """Every single change of `choice`: (kind, name, the changed choice); rules in `skip` (missing_rules) are left out."""
    out: list[tuple[str, str, dict]] = []
    for name in ENTRY_CHECKS:
        if f"entry:{name}" in skip:
            continue
        entries = set(choice["entries"])
        entries.symmetric_difference_update({name})
        out.append(("entry", name, {**choice, "entries": sorted(entries)}))
    for name in EXIT_RULES:
        if f"exit:{name}" in skip:
            continue
        exits = set(choice["exits"])
        exits.symmetric_difference_update({name})
        out.append(("exit", name, {**choice, "exits": sorted(exits)}))
    for stop in STOPS:
        if stop != choice["stop"] and f"stop:{stop}" not in skip:
            out.append(("stop", stop, {**choice, "stop": stop}))
    for name in TARGETS:
        if f"target:{name}" in skip:
            continue
        targets = set(choice["targets"])
        targets.symmetric_difference_update({name})
        out.append(("target", name, {**choice, "targets": sorted(targets)}))
    return out


JUDGES = ("NET", "ROC")


def _roc(out: dict) -> float | None:
    """Return on capital: net P&L after charges ÷ the most margin in use at one moment, in %. None without trades."""
    margin = (out.get("capital") or {}).get("peak_margin") or 0.0
    return round(out["totals"]["net"] / margin * 100, 2) if margin > 0 else None


def _brief(out: dict) -> dict:
    """The totals plus the capital they needed and the return on it."""
    cap = out.get("capital") or {}
    return {**out["totals"], "peak_margin": cap.get("peak_margin", 0.0), "peak_value": cap.get("peak_value", 0.0), "roc": _roc(out)}


def _score(brief: dict, judge: str) -> float:
    if judge == "ROC":
        return brief["roc"] if brief["roc"] is not None else float("-inf")
    return brief["net"]


def recommend(trades: list[dict], choice: dict, settings: dict | None = None, judge: str = "NET") -> dict:
    """What each rule adds on top of the current choice, and the best combination a greedy search finds.

    `judge` NET ranks by P&L after charges; ROC by return on capital (net ÷ the most margin in use at once), so a set
    that makes a little less with far less money can win."""
    judge = judge if judge in JUDGES else "NET"
    choice = clean_choice(choice)
    now = _brief(evaluate(trades, choice, settings, with_trades=False))
    skip = set(missing_rules(trades))
    rows = []
    for kind, name, changed in _toggles(choice, skip):
        got = _brief(evaluate(trades, changed, settings, with_trades=False))
        on_now = (
            name in choice["entries"] if kind == "entry" else name in choice["exits"] if kind == "exit" else name in choice["targets"] if kind == "target" else False
        )
        label = rule_label(kind, name)
        rows.append(
            {
                "kind": kind,
                "name": name,
                "label": label,
                "on": on_now,
                "gross_now": now["gross"],
                "gross_changed": got["gross"],
                "net_now": now["net"],
                "net_changed": got["net"],
                "delta_gross": round(got["gross"] - now["gross"], 2),
                "delta_net": round(got["net"] - now["net"], 2),
                "trades_now": now["trades"],
                "trades_changed": got["trades"],
                "win_pct_now": now["win_pct"],
                "win_pct_changed": got["win_pct"],
                "profit_factor_changed": got["profit_factor"],
                "green_pct_changed": got["green_pct"],
                "capital_now": now["peak_margin"],
                "capital_changed": got["peak_margin"],
                "roc_now": now["roc"],
                "roc_changed": got["roc"],
                "delta_roc": round(got["roc"] - now["roc"], 2) if got["roc"] is not None and now["roc"] is not None else None,
            }
        )
    if judge == "ROC":
        rows.sort(key=lambda r: -(r["roc_changed"] if r["roc_changed"] is not None else float("-inf")))
    else:
        rows.sort(key=lambda r: -r["delta_net"])
    # Greedy: from the current choice, take the single change that adds the most (net or return on capital), until
    # nothing adds; a set left with fewer than SEARCH_MIN_TRADES trades is never picked.
    best_choice = dict(choice)
    best = now
    steps: list[dict] = []
    for _ in range(SEARCH_STEPS):
        candidates = []
        for kind, name, changed in _toggles(best_choice, skip):
            got = _brief(evaluate(trades, changed, settings, with_trades=False))
            if got["trades"] >= SEARCH_MIN_TRADES and _score(got, judge) > _score(best, judge) + 1e-9:
                candidates.append((_score(got, judge), kind, name, changed, got))
        if not candidates:
            break
        candidates.sort(key=lambda c: -c[0])
        _, kind, name, changed, got = candidates[0]
        steps.append({"kind": kind, "name": name, "net_before": best["net"], "net_after": got["net"], "trades": got["trades"],
                      "roc_before": best["roc"], "roc_after": got["roc"]})
        best_choice, best = changed, got
    return {
        "judge": judge,
        "now": now,
        "rows": rows,
        "best": {"choice": best_choice, "totals": best, "steps": steps, "settings": settings_for_choice(best_choice, settings)},
        "missing": sorted(skip),
    }


# ----------------------------------------------------------------------------------------------------------------
# Jobs
# ----------------------------------------------------------------------------------------------------------------


def trading_days_between(first: dt.date, last: dt.date) -> list[dt.date]:
    out = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += dt.timedelta(days=1)
    return out


def _meta_path() -> Path:
    return _data_dir() / "jobs.json"


def _trades_path(job_id: str) -> Path:
    return _data_dir() / f"{job_id}.json.gz"


class StrategyLab:
    """One job at a time: download (cached), read each stock, keep the base trades. Memory plus disk."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.current: dict | None = None
        self._trades: dict[str, list[dict]] = {}
        self._task: asyncio.Task | None = None
        self._stop = False
        self._load()

    # -- persistence
    def _load(self) -> None:
        try:
            raw = json.loads(_meta_path().read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and isinstance(raw.get("jobs"), list):
            self.jobs = [j for j in raw["jobs"] if isinstance(j, dict)]
            for j in self.jobs:
                if j.get("status") == "RUNNING":
                    j["status"] = "INTERRUPTED"

    def _save(self) -> None:
        try:
            _data_dir().mkdir(parents=True, exist_ok=True)
            _meta_path().write_text(json.dumps({"jobs": self.jobs[-KEEP_JOBS:]}))
        except OSError as exc:
            log.warning("strategy lab: could not save the jobs: %s", exc)

    def trades_of(self, job_id: str) -> list[dict] | None:
        if job_id in self._trades:
            return self._trades[job_id]
        try:
            with gzip.open(_trades_path(job_id), "rt") as fh:
                rows = json.load(fh)
        except (OSError, ValueError):
            return None
        if isinstance(rows, list):
            self._trades[job_id] = rows
            return rows
        return None

    def _keep_trades(self, job_id: str, rows: list[dict]) -> None:
        self._trades[job_id] = rows
        try:
            _data_dir().mkdir(parents=True, exist_ok=True)
            with gzip.open(_trades_path(job_id), "wt") as fh:
                json.dump(rows, fh)
        except OSError as exc:
            log.warning("strategy lab: could not save the trades: %s", exc)
        # keep memory small: only the newest few jobs stay loaded
        for old in list(self._trades)[:-3]:
            self._trades.pop(old, None)

    # -- views
    @property
    def running(self) -> bool:
        return bool(self.current and self.current.get("status") == "RUNNING")

    def job(self, job_id: str) -> dict | None:
        for j in self.jobs:
            if j.get("id") == job_id:
                return j
        return None

    def view(self) -> dict:
        return {"running": self.running, "current": self.current, "jobs": list(reversed(self.jobs[-KEEP_JOBS:]))}

    def delete(self, job_id: str) -> bool:
        before = len(self.jobs)
        self.jobs = [j for j in self.jobs if j.get("id") != job_id]
        self._trades.pop(job_id, None)
        try:
            _trades_path(job_id).unlink(missing_ok=True)
        except OSError:
            pass
        self._save()
        return len(self.jobs) < before

    # -- run
    def start(
        self, symbols: list[str], first: dt.date, last: dt.date, bot: int, cfg_for, fetch, wait=None, label: str = "", minutes: int | None = None
    ) -> dict:
        """`minutes` (1/2/3/5/10/15) reads every stock on that candle instead of the bot's own (None = the bot's)."""
        if self.running:
            raise RuntimeError("A Strategy lab run is already going. Stop it or wait for it to finish.")
        if minutes is not None and int(minutes) not in CANDLE_MINUTES:
            raise ValueError(f"The candle must be one of {', '.join(str(m) for m in CANDLE_MINUTES)} minutes.")
        names = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))[:MAX_STOCKS]
        if not names:
            raise ValueError("Pick at least one stock.")
        if last < first:
            raise ValueError("The end date is before the start date.")
        if (last - first).days > MAX_RANGE_DAYS:
            raise ValueError(f"At most {MAX_RANGE_DAYS} calendar days a run.")
        days = trading_days_between(first, last)
        if not days:
            raise ValueError("No trading days in that range.")
        job = {
            "id": uuid.uuid4().hex[:12],
            "status": "RUNNING",
            "label": label[:60],
            "bot": bot,
            "minutes": int(minutes) if minutes is not None else None,
            "symbols": names,
            "first": first.isoformat(),
            "last": last.isoformat(),
            "days": [d.isoformat() for d in days],
            "done": 0,
            "total": len(names),
            "errors": [],
            "trades": 0,
            "started_at": dt.datetime.now(IST).isoformat(timespec="seconds"),
            "finished_at": None,
            "settings": None,
            "choice": None,
        }
        self.jobs.append(job)
        self.jobs = self.jobs[-KEEP_JOBS:]
        self.current = job
        self._stop = False
        self._save()
        self._task = asyncio.create_task(self._run(job, names, days, cfg_for, fetch, wait))
        return job

    def stop(self) -> dict:
        self._stop = True
        return self.view()

    async def _run(self, job: dict, names: list[str], days: list[dt.date], cfg_for, fetch, wait) -> None:
        first = days[0] - dt.timedelta(days=WARMUP_DAYS)
        last = days[-1]
        rows: list[dict] = []
        settings: dict | None = None
        choice: dict | None = None
        try:
            for name in names:
                if self._stop:
                    break
                try:
                    if wait is not None:
                        await wait()
                    tape = await load_tape(name, first, last, fetch)
                    cfg = cfg_for(name)
                    if job.get("minutes"):
                        cfg = with_candle(cfg, int(job["minutes"]))
                    got, s = await asyncio.to_thread(analyze_stock, name, tape, cfg, days)
                    rows.extend(got)
                    if settings is None:
                        settings = s
                        choice = choice_from_cfg(cfg)
                except Exception as exc:  # noqa: BLE001 - one stock never stops the run
                    job["errors"].append({"symbol": name, "error": str(exc)[:200]})
                job["done"] += 1
                job["trades"] = len(rows)
                await asyncio.sleep(0)
            job["settings"] = settings
            job["choice"] = choice
            job["status"] = "STOPPED" if self._stop else "DONE"
            self._keep_trades(job["id"], rows)
        except Exception as exc:  # noqa: BLE001
            log.exception("strategy lab run failed")
            job["errors"].append({"symbol": "", "error": str(exc)[:200]})
            job["status"] = "FAILED"
        finally:
            job["finished_at"] = dt.datetime.now(IST).isoformat(timespec="seconds")
            job["errors"] = job["errors"][-30:]
            self._save()


_LAB: StrategyLab | None = None


def lab() -> StrategyLab:
    global _LAB
    if _LAB is None:
        _LAB = StrategyLab()
    return _LAB
