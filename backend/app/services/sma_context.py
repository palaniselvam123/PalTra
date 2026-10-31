"""SMA terminal facts for the chat assistant.

The chat was grounded on the ORB desk only, so it could not answer about the
SMA bot the user actually trades with. This builds the same kind of factual
snapshot for the SMA terminal: its settings (shared and per stock), open
books, today's trades with their exit reasons, recent day totals, the latest
backtest runs, and a plain guide to how the strategy and each option work.

In production the terminal runs in this process (`sma_host`), so its live
engine is read directly. Locally it is a separate process on :8001; then only
the strategy guide is returned and the assistant leans on the screen data the
page sends with the question.

Read-only: nothing here changes the engine or places an order.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from collections import defaultdict

from app.core.market_clock import IST

logger = logging.getLogger(__name__)

MAX_TRADES_TODAY = 60
RECENT_DAYS = 7
RECENT_RUNS = 5
# Past trades (every bot, PAPER / LIVE / RESEARCH) the assistant can read back, and how many days.
MAX_RECENT_TRADES = 120
RECENT_TRADE_DAYS = 7
# Today's SMA crosses per armed stock in the timeline, newest kept.
MAX_CROSSES_PER_STOCK = 40
MAX_SETTINGS_HISTORY = 10
# The screen snapshot comes from the browser. Cap it so a large page cannot
# blow up the prompt.
MAX_SCREEN_CHARS = 20_000

STRATEGY_GUIDE = {
    "entry": (
        "SMA crossover on 1-minute candles. When the fast SMA (default 9) closes above the slow SMA (default 21) "
        "the bot buys; when it closes below, it sells short. Only CLOSED candles count; the forming candle is "
        "never a signal. A cross that had already printed when the bot started or the stock was armed is "
        "skipped; it waits for a fresh cross. Stop-and-reverse: an opposite cross closes the open trade and "
        "opens the other side (after the entry cut-off it only closes)."
    ),
    "trade_list": (
        "Only stocks on the Trade list (armed) are ordered. Each stock can have its own settings (per-stock "
        "overrides); the rest come from the shared settings."
    ),
    "market_hours": (
        "No new entry outside the NSE cash session, before 09:20 IST, or after the entry cut-off (default "
        "15:00). Everything is squared off at the square-off time (default 15:15)."
    ),
    "stops": {
        "ATR": "Fixed stop at atr_multiplier x ATR(14) from the entry (exchange SL order in LIVE).",
        "SMA_GAP": (
            "PAPER only. Moving stop and target from the SMA 9/21 gap % x gap_sl_mult / gap_tp_mult "
            "(at least gap_min_pct), recalculated every closed candle; the stop only tightens."
        ),
        "TSL": (
            "Groww-style trailing stop: tsl_sl_points rupees from the entry, moved tsl_trail_points rupees for "
            "every full step the price gains; optional tsl_target_points rupee target."
        ),
        "VWAP": (
            "Stop at the session VWAP minus vwap_stop_atr x ATR for a buy (plus for a sell), read on each closed "
            "candle and only ever tightened; the ATR stop guards the trade until the level is on the safe side."
        ),
        "use_stop_off": "No stop order; the opposite cross, square-off or Bollinger exit close the trade.",
    },
    "entry_filters": {
        "use_vwap": "A buy must be above VWAP, a sell below it.",
        "use_volume": "The cross candle's volume must be at least volume_min_ratio x the average of the last 20.",
        "use_density": "Candle body must be at least density_min_pct of its range (no long-wick indecision).",
        "use_rsi": "RSI(14) must sit inside the buy range (rsi_long_min..max) or sell range (rsi_short_min..max).",
        "use_bollinger": (
            "Skip a buy that closed above the upper band (or a sell below the lower): chasing a spike. Skip any "
            "cross while the bands are narrower than bb_min_width_pct % (squeeze; 0 = check off)."
        ),
        "use_adx_filter": "ADX(14) must be at least adx_threshold (trend strength).",
        "use_gap_long / use_gap_short": (
            "SMA gap range, ticked per side: the signed gap % = (SMA9 - SMA21) / SMA21 x 100 on the cross candle "
            "must be within gap_long_min..gap_long_max for a buy, gap_short_min..gap_short_max for a sell "
            "(sell ranges are usually negative). An unticked side is not checked."
        ),
        "use_candle_dir": (
            "Candle direction: the last candle_dir_count closed candles must move the trade's way before an "
            "entry. candle_dir_rule CLOSES = each close above the one before for a buy (below for a sell); "
            "COLOUR = each candle green for a buy, red for a sell; BOTH = both. Read on the cross candle, or, "
            "in gap mode, on the candle where the order would go."
        ),
        "note": "A refused cross is shown on the chart with an x marker and the reason.",
    },
    "bollinger_exit": {
        "OFF": "No Bollinger exit.",
        "BAND": "Book the profit when a candle closes at the far band (upper for a buy, lower for a sell): BB_TARGET.",
        "MIDDLE": (
            "Exit when a candle closes back across the middle band, after a close on the trade's side of it: "
            "BB_MIDDLE."
        ),
        "BOTH": "Whichever of the two comes first.",
    },
    "gap_mode": (
        "use_gap_mode: a cross only arms the trade. A BUY is placed on the first closed candle whose signed SMA "
        "gap % is >= gap_entry_long (a SELL when <= gap_entry_short), after gap_entry_delay_min more minutes if "
        "set, and given up gap_entry_window_min minutes after the cross if set (0 = wait for the next cross). The "
        "trade closes with exit reason GAP_FADE when the gap, after clearing the exit level, fades back to "
        "gap_exit_long / gap_exit_short, or gives back gap_giveback_pct of its widest (0 = off). To ride out a pullback, gap_fade_confirm_sma makes a fade exit "
        "wait for a candle that closes on the wrong side of the slow SMA (below it for a buy, above it for a sell), and "
        "gap_fade_min_candles needs the gap to narrow that many candles in a row (0 = off). gap_fade_intrabar checks "
        "the fade about once a second on the live price, as if that second closed the candle. An opposite cross "
        "closes at once and the reverse waits for its own gap."
    ),
    "force_order": "Enters now in the cross direction, skipping the entry filters; still refused out of hours or on an unarmed stock.",
    "risk": "max_daily_loss stops the bot for the day when realised loss reaches it; max_trades_per_day caps entries.",
    "modes": (
        "PAPER fills locally at the price (virtual money). LIVE sends real Groww MIS orders with an exchange "
        "stop. REPLAY trades are backtests on past candles and have their own book. RESEARCH trades (ids Q-n) "
        "come from the research desk: a second, paper-only bot on today's live prices with its own settings, "
        "Trade list and book, which never touches the live bot."
    ),
    "exit_reasons": {
        "MA_CROSS": "opposite SMA cross",
        "ATR_SL_HIT": "ATR stop",
        "GAP_SL_HIT": "SMA-gap moving stop",
        "TSL_HIT": "trailing stop",
        "ST_SL_HIT": "Supertrend stop",
        "TARGET_HIT": "target",
        "BB_TARGET": "Bollinger band target",
        "BB_MIDDLE": "Bollinger middle band exit",
        "GAP_FADE": "SMA gap faded (gap mode)",
        "EOD_SQUARE_OFF": "square-off time",
        "KILL_SWITCH": "panic / daily loss stop",
        "MANUAL_CLOSE": "closed from the screen",
        "REPLAY_STOPPED": "backtest stopped",
    },
    "how_to_use": [
        "Pick liquid stocks (the Scalp page's Most active list helps) and arm them on the Trade list.",
        "Start in PAPER. Back-test the settings with Replay over 2-4 weeks and compare runs in the Backtests tab.",
        "Turn on one filter at a time and keep it only where it improves net P&L or drawdown.",
        "Pick a stop that fits the stock: ATR for most, TSL to lock profits in rupee steps.",
        "Watch the decision messages and chart markers to see why a cross was taken or skipped.",
        "Only switch to LIVE after the same settings were profitable in PAPER and Replay.",
    ],
}



# Plain words for the settings the user asks about most. Field names are the keys the terminal saves
# (`ConfigUpdate`); the assistant uses them in `propose_settings_change`.
FIELD_HELP = {
    "qty": "shares per order",
    "sma_fast": "fast SMA period (default 9)",
    "sma_slow": "slow SMA period (default 21)",
    "candle_minutes": "the bot's candle in minutes: 1, 2, 3, 5, 10 or 15",
    "exit_candle_minutes": "read the SMA cross exit on this candle size instead (0 = the bot's own)",
    "small_exit_minutes": "watch smaller candles while in a trade, CSV of 1,2,3,5,10 ('' = off)",
    "small_exit_cross": "small candles: exit on an SMA cross against the trade",
    "small_exit_slope": "small candles: exit on the SMA 9 slope turning against",
    "small_exit_counter": "small candles: N candles against (0 = off)",
    "small_exit_loss_pct": "small candles: only while the trade is this % under water (0 = always)",
    "small_exit_agree": "small candles: how many sizes must agree",
    "entry_mode": "SMA (cross) | PATTERN (candle patterns) | COLOUR (candle colour)",
    "use_stop": "stop-loss on / off",
    "stop_type": "ATR | TSL (trailing) | SMA_GAP (paper only) | SUPERTREND | VWAP (VWAP ∓ vwap_stop_atr × ATR, tighter each candle)",
    "vwap_stop_atr": "VWAP stop: buffer below / above VWAP in ATRs (0.5)",
    "atr_period": "ATR period", "atr_multiplier": "stop at this x ATR",
    "tsl_mode": "POINTS (rupees) | PERCENT (% of entry) for the trailing stop",
    "tsl_sl_points": "trailing stop distance in rupees", "tsl_trail_points": "trail step in rupees",
    "tsl_target_points": "target in rupees (0 = none)", "tsl_sl_pct": "trailing stop distance in %",
    "tsl_trail_pct": "trail step in %", "tsl_target_pct": "target in % (0 = none)",
    "gap_sl_mult": "SMA-gap stop multiple", "gap_tp_mult": "SMA-gap target multiple", "gap_min_pct": "SMA-gap floor %",
    "stop_plan": "staged stop plan: breakeven at +plan_be_r R, Supertrend trail from +plan_trail_r R",
    "plan_be_r": "R multiple for breakeven", "plan_trail_r": "R multiple to start the Supertrend trail",
    "pivot_target": "OFF | R1 | R2: close at the pivot level",
    "cross_exit": "an opposite SMA cross closes the trade and reverses (off = held through a cross)",
    "bb_exit": "Bollinger exit: OFF | BAND | MIDDLE | BOTH", "bb_period": "Bollinger period", "bb_std": "Bollinger std dev",
    "use_vwap": "entry check: buy above VWAP, sell below",
    "use_volume": "entry check: cross candle volume >= volume_min_ratio x average", "volume_min_ratio": "volume multiple",
    "use_density": "entry check: candle body >= density_min_pct of its range", "density_min_pct": "body % of range",
    "use_rsi": "entry check: RSI inside the buy / sell ranges", "rsi_long_min": "buy RSI low", "rsi_long_max": "buy RSI high",
    "rsi_short_min": "sell RSI low", "rsi_short_max": "sell RSI high",
    "use_bollinger": "entry check: no chasing beyond the band / no squeeze", "bb_min_width_pct": "squeeze width %",
    "use_adx_filter": "entry check: ADX >= adx_threshold", "adx_threshold": "ADX threshold",
    "use_gap_long": "entry check: SMA gap % inside gap_long_min..max for a buy",
    "use_gap_short": "entry check: SMA gap % inside gap_short_min..max for a sell",
    "use_candle_dir": "entry check: last candle_dir_count candles went the trade's way (candle_dir_rule CLOSES|COLOUR|BOTH)",
    "use_ema": "entry check: EMA fast above / below EMA slow", "ema_fast": "EMA fast period", "ema_slow": "EMA slow period",
    "ema_exit": "exit on an EMA cross against the trade",
    "use_supertrend": "entry check: Supertrend on the trade's side", "st_exit": "exit when Supertrend flips against",
    "st_period": "Supertrend ATR length", "st_mult": "Supertrend factor",
    "use_level": "entry check: close beyond level_line (BB_MIDDLE | BB_UPPER | PIVOT_P | PIVOT_R1)",
    "or_volume": "opening-range check: volume", "or_vwap": "opening-range check: VWAP side",
    "or_spread": "opening-range check: SMA spread", "or_break": "opening-range check: range break",
    "use_gap_mode": "SMA gap mode: a cross only arms; the order waits for the gap level",
    "gap_entry_long": "gap mode buy level %", "gap_entry_short": "gap mode sell level %",
    "gap_exit_long": "gap mode buy fade level %", "gap_exit_short": "gap mode sell fade level %",
    "gap_giveback_pct": "gap mode: give back this % of the widest gap", "gap_entry_window_min": "gap mode: give up after N minutes (0 = next cross)",
    "join_trend": "join the morning trend after join_after_min minutes without a fresh cross", "join_after_min": "minutes after 09:15",
    "late_entry_min": "late entry: a filter-refused cross stays armed this many minutes (0 = off)",
    "counter_exit_count": "exit after N closed candles against the trade (0 = off)", "counter_rule": "COLOUR | CLOSES | BOTH",
    "counter_need_fast": "counter candles: the last close must be past the fast SMA", "counter_min_body": "counter candles: min body x ATR",
    "counter_entry": "early reversal entry on N candles against the SMA side",
    "counter_reenter": "re-enter after a counter-candle exit", "counter_reenter_max": "re-entries per SMA run",
    "slope_exit": "SMA 9 slope exit (bend / turn)", "slope_lookback": "candles the slope is read over",
    "slope_bend_pct": "bend: % off the steepest slope (0 = off)", "slope_turn": "turn exit on", "slope_turn_pct": "turn: %/candle against",
    "vturn_exit": "SMA 9 V-turn exit", "vturn_move_pct": "V: steep run %/candle", "vturn_turn_pct": "V: turn back %/candle",
    "vturn_within": "V: within N candles", "vturn_handoff": "a losing V-turn exit hands the stock to Bot 2",
    "decel_exit": "SMA 9 deceleration guard", "slope_minutes": "candle size the slope exits read (0 = the bot's own)",
    "turn_alert": "Telegram on an SMA 9 turn", "turn_entry": "enter on an SMA 9 turn before the cross",
    "turn_toward_cross": "turn entry only toward the 9/21 cross", "turn_exit_candle": "close a turn on a candle of the other colour",
    "turn_colour_exit": "a turn trade exits on the first candle of the other colour", "uturn_on": "rounded U turn too",
    "tall_pullback": "tall signal candle: wait for a pullback", "tall_atr_mult": "tall = height >= this x ATR",
    "extreme_entry": "wait at the signal candle's extreme (limit entry)", "entry_candle_confirm": "wait for the entry candle to go the trade's way",
    "day_extreme_guard": "no buy at the day's high / sell at the day's low without a pullback",
    "slow_sma_exit": "exit on a close beyond the slow SMA against the trade", "recovery_hold": "hold a losing trade through a cross (recovery)",
    "recovery_handoff": "after a recovery, hand the stock to Bot 2", "handoff_now": "Bot 2 orders at once on a hand-off",
    "breakout_handoff": "a breakout candle hands the stock to Bot 3",
    "flip_orders": "flip: every order goes the other way of the signal",
    "max_daily_loss": "daily loss limit in rupees (the bot halts)", "max_trades_per_day": "entries per day for the bot",
    "max_trades_per_stock": "entries per day per stock (0 = off)",
    "entry_cutoff_time": "no new entries after HH:MM IST", "square_off_time": "everything closed at HH:MM IST",
    "review_on": "1-minute human review", "review_gap_pct": "review band %", "review_default_answer": "PROMPT | EXIT | CONTINUE",
    "max_concurrent": "stocks held at once",
}


def settings_catalog(mod) -> dict:
    """Every strategy field the chat may change: kind, bounds / choices and plain words, from the terminal's
    own ConfigUpdate. Empty when the terminal is not in this process."""
    model = getattr(mod, "ConfigUpdate", None)
    if model is None:
        return {}
    from app.services.chat_actions import settings_allowed

    import typing

    out: dict = {}
    for name, field in model.model_fields.items():
        if not settings_allowed(name):
            continue
        entry: dict = {}
        ann = field.annotation
        args = [a for a in typing.get_args(ann) if a is not type(None)] or [ann]
        kind = args[0]
        if typing.get_origin(kind) is typing.Literal:
            entry["choices"] = list(typing.get_args(kind))
        elif kind is bool:
            entry["kind"] = "true/false"
        elif kind is int:
            entry["kind"] = "whole number"
        elif kind is float:
            entry["kind"] = "number"
        elif kind is str:
            entry["kind"] = "text"
        else:
            entry["kind"] = "list or number"
        for meta in field.metadata:
            for attr in ("ge", "gt", "le", "lt"):
                value = getattr(meta, attr, None)
                if value is not None:
                    entry[attr] = value
        if name in FIELD_HELP:
            entry["help"] = FIELD_HELP[name]
        out[name] = entry
    return out


def _ist(ts: float | int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def _epoch(value) -> float | None:
    """A trade row's time (isoformat, naive = IST) as epoch seconds."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = dt.datetime.fromisoformat(value)
        except ValueError:
            return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=IST)
    return value.timestamp()


def _hhmm_epoch(day: dt.date, hhmm: str | None, default: str) -> float:
    try:
        h, m = (hhmm or default).split(":")
        return dt.datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=IST).timestamp()
    except (ValueError, AttributeError):
        h, m = default.split(":")
        return dt.datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=IST).timestamp()


def cross_timeline(engine, cfg, symbol: str, trades: list[dict], today: dt.date, armed_windows=None) -> dict:
    """Today's closed-candle SMA crosses of one armed stock and what the bot did with each.

    Read from the bot's own candles (`engine._frames`), judged the way the bot judges a cross (`filter_blocks`
    for the entry checks), matched against the trade book, the cut-off, the Trade list windows and the
    settings in force, so "why did it not trade / reverse at 13:00" has a recorded answer. A cross that none
    of the recorded rules explains says so; nothing is guessed.
    """
    from strategy_engine import _cfg_for, cross_exits, filter_blocks, flips_orders
    from candles import candle_minutes

    frame = (getattr(engine, "_frames", {}) or {}).get(symbol)
    stock_cfg = _cfg_for(cfg, symbol)
    rules = {
        "entry_mode": getattr(stock_cfg, "entry_mode", None) or "SMA",
        "candle_minutes": candle_minutes(stock_cfg),
        "cross_exit": cross_exits(stock_cfg),
        "use_gap_mode": bool(getattr(stock_cfg, "use_gap_mode", False)),
        "flip_orders": flips_orders(stock_cfg),
        "entry_cutoff_time": getattr(stock_cfg, "entry_cutoff_time", None) or "15:00",
        "max_trades_per_stock": int(getattr(stock_cfg, "max_trades_per_stock", 0) or 0),
        "late_entry_min": int(getattr(stock_cfg, "late_entry_min", 0) or 0),
        "join_trend": bool(getattr(stock_cfg, "join_trend", False)),
        "turn_entry": bool(getattr(stock_cfg, "turn_entry", False)),
    }
    out: dict = {"symbol": symbol, "rules_in_force": rules, "crosses_today": [], "signal_line": (getattr(engine, "_signals", {}) or {}).get(symbol)}
    if frame is None or len(frame) < 3 or not {"sma_9", "sma_21", "ts"}.issubset(frame.columns):
        out["note"] = "no candles loaded for this stock on this bot (not armed, or the bot has not run today)"
        return out
    import pandas as pd

    day_start = dt.datetime(today.year, today.month, today.day, tzinfo=IST).timestamp()
    cutoff = _hhmm_epoch(today, rules["entry_cutoff_time"], "15:00")
    first_entry = _hhmm_epoch(today, "09:20", "09:20")
    refused = {b["time"]: b["reason"] for b in filter_blocks(frame, stock_cfg)}
    skip_until = (getattr(engine, "_skip_cross_until", {}) or {}).get(symbol)
    stock_trades = [t for t in trades if t.get("symbol") == symbol]
    minutes = int(rules["candle_minutes"] or 1)
    fast = frame["sma_9"].to_numpy(dtype=float)
    slow = frame["sma_21"].to_numpy(dtype=float)
    ts_col = frame["ts"].to_numpy()
    closes = frame["close"].to_numpy(dtype=float) if "close" in frame.columns else None
    crosses: list[dict] = []
    for i in range(1, len(frame) - 1):  # the last row is the forming candle: never a signal
        a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
        if any(pd.isna(v) for v in (a0, b0, a1, b1)):
            continue
        if a0 <= b0 and a1 > b1:
            direction = "LONG"
        elif a0 >= b0 and a1 < b1:
            direction = "SHORT"
        else:
            continue
        ts = int(ts_col[i])
        if ts < day_start:
            continue
        closed_at = ts + minutes * 60
        row: dict = {"candle": _ist(ts), "closed_at": _ist(closed_at), "signal": "BUY" if direction == "LONG" else "SELL"}
        if closes is not None:
            row["close"] = round(float(closes[i]), 2)
        real_side = ("SHORT" if direction == "LONG" else "LONG") if rules["flip_orders"] else direction
        # What the book shows at that candle: an entry on it, or a close on it.
        entered = next(
            (t for t in stock_trades if (e := _epoch(t.get("entry_time"))) is not None and closed_at - 30 <= e <= closed_at + 150 and t.get("direction") == real_side),
            None,
        )
        closed = next(
            (t for t in stock_trades if (x := _epoch(t.get("exit_time"))) is not None and closed_at - 30 <= x <= closed_at + 150),
            None,
        )
        held_then = next(
            (t for t in stock_trades if (e := _epoch(t.get("entry_time"))) is not None and e < closed_at - 30 and ((x := _epoch(t.get("exit_time"))) is None or x > closed_at + 150)),
            None,
        )
        what: list[str] = []
        if closed is not None:
            what.append(f"closed trade {closed.get('id')} ({closed.get('direction')}) as {closed.get('exit_reason')}")
        if entered is not None:
            what.append(f"entered trade {entered.get('id')} {entered.get('direction')} at {entered.get('entry_price')} ({entered.get('entry_reason') or 'SMA_CROSS'})")
        elif skip_until is not None and ts <= int(skip_until):
            what.append("this cross was already on the tape when the stock was armed / the bot started, so it was not traded (the bot waits for a fresh cross)")
        elif rules["entry_mode"] != "SMA":
            what.append(f"entry mode is {rules['entry_mode']}: SMA crosses are not entries in that mode")
        elif ts in refused:
            what.append(f"refused by an entry check: {refused[ts]}" + (f"; late entry kept it armed for {rules['late_entry_min']} min" if rules["late_entry_min"] else ""))
        elif closed_at < first_entry:
            what.append("before 09:20: no entries that early")
        elif closed_at >= cutoff:
            what.append(f"after the entry cut-off {rules['entry_cutoff_time']}: a cross only closes, never opens (no reverse)")
        elif armed_windows is not None and not any(a <= closed_at <= b for a, b in armed_windows):
            what.append("the stock was not on the Trade list at that time (arm log)")
        elif held_then is not None and held_then.get("direction") == real_side:
            what.append(f"already holding {real_side} (trade {held_then.get('id')}), so nothing to do")
        elif held_then is not None and not rules["cross_exit"]:
            what.append(f"SMA cross exit is off: trade {held_then.get('id')} was held through the cross (no reverse)")
        elif rules["use_gap_mode"]:
            what.append("gap mode: the cross only armed the trade; the order waits for the SMA gap level")
        elif rules["max_trades_per_stock"] and sum(1 for t in stock_trades if (e := _epoch(t.get("entry_time"))) is not None and e < closed_at) >= rules["max_trades_per_stock"]:
            what.append(f"per-stock cap: {symbol} already had its {rules['max_trades_per_stock']} trade(s) for the day")
        else:
            what.append("no trade on this cross and no recorded refusal for it: the bot may have been STOPPED / PAUSED / HALTED or at its day cap then (status changes are not recorded by time)")
        row["what_happened"] = "; ".join(what)
        crosses.append(row)
    out["crosses_today"] = crosses[-MAX_CROSSES_PER_STOCK:]
    out["crosses_today_count"] = len(crosses)
    if not crosses:
        out["note"] = "no closed-candle SMA cross on this stock today on this bot's candle size"
    return out


def settings_history(presets) -> list[dict]:
    """Bot 1's saves, newest first, each with only the fields that changed from the save before it."""
    rows = list(reversed(presets.listing("1").get("history") or []))  # oldest first
    out: list[dict] = []
    prev: dict = {}
    for row in rows:
        settings = row.get("settings") or {}
        changed = {k: v for k, v in settings.items() if prev.get(k) != v} if prev else settings
        out.append({"saved_at": row.get("saved_at"), "changed": changed if prev else {"first_recorded_save": True, **settings}})
        prev = settings
    return list(reversed(out))[:MAX_SETTINGS_HISTORY]


def _bot_facts(mod, eng, label: str, today: dt.date, trades_today: list[dict], defaults: dict | None, full_settings: bool) -> dict:
    """One bot (or the research desk) as the assistant sees it."""
    from strategy_engine import trade_names

    cfg = eng.load_config()
    config_dict = getattr(mod, "_config_dict", None)
    settings = config_dict(cfg) if config_dict else {}
    bot_id = int(getattr(eng, "bot_id", 1) or 1)
    facts: dict = {
        "bot": label,
        "name": getattr(cfg, "bot_name", None) or (f"Bot {bot_id}" if label != "research" else "Research desk"),
        "mode": "RESEARCH" if label == "research" else (cfg.trading_mode or "PAPER").upper(),
        "status": getattr(eng, "status", None),
        "halt_reason": getattr(eng, "halt_reason", "") or None,
        "last_error": getattr(eng, "last_error", None) or None,
        "armed_stocks": trade_names(cfg),
        "trades_today_count": getattr(eng, "trades_today", 0),
        "max_trades_per_day": settings.get("max_trades_per_day"),
    }
    positions = getattr(eng, "positions", {}) or {}
    facts["open_positions"] = [
        {
            "symbol": symbol, "direction": pos.direction, "qty": pos.qty, "entry_price": pos.entry_price,
            "entry_time": pos.entry_time.isoformat() if getattr(pos, "entry_time", None) else None,
            "stop": getattr(pos, "sl_trigger", None), "trade_id": getattr(pos, "trade_id", None),
            "last_price": (getattr(eng, "_ltps", {}) or {}).get(symbol),
        }
        for symbol, pos in positions.items()
        if pos is not None
    ]
    facts["signal_lines"] = dict(getattr(eng, "_signals", {}) or {})
    if full_settings or not defaults:
        facts["settings"] = settings
    else:
        facts["settings_differing_from_defaults"] = {k: v for k, v in settings.items() if k in defaults and defaults[k] != v}
        facts["stock_settings"] = settings.get("stock_settings") or {}
    bot_trades = [t for t in trades_today if (t.get("bot") or 1) == bot_id and (t.get("mode") or "").upper() == facts["mode"]]
    windows = None
    try:
        import arm_log

        windows = arm_log.windows(int(eng.config_id), today)
    except Exception:  # noqa: BLE001
        windows = None
    stocks = list(dict.fromkeys([*facts["armed_stocks"], *positions.keys(), *(t.get("symbol") for t in bot_trades if t.get("symbol"))]))
    facts["stocks_today"] = [
        cross_timeline(eng, cfg, symbol, bot_trades, today, (windows or {}).get(symbol) if windows else None)
        for symbol in stocks[:30]
    ]
    return facts


def _recent_trades(session_factory, TradeLog, _trade_dict, today: dt.date) -> tuple[list[dict], dict]:
    """Past days' trades of every bot with the strategy each one used (deduped into `strategies_used`)."""
    since = (today - dt.timedelta(days=RECENT_TRADE_DAYS)).isoformat()
    with session_factory()() as db:
        rows = (
            db.query(TradeLog)
            .filter(TradeLog.date >= since, TradeLog.date < today.isoformat(), TradeLog.mode.in_(("PAPER", "LIVE", "RESEARCH")))
            .order_by(TradeLog.id.desc())
            .limit(MAX_RECENT_TRADES)
            .all()
        )
        dicts = [_trade_dict(r) for r in rows]
    strategies: dict[str, dict] = {}
    keys: dict[str, str] = {}
    trades: list[dict] = []
    for row in dicts:
        t = _trade(row)
        t["date"] = row.get("date")
        t["bot"] = row.get("bot")
        t["entry_reason"] = row.get("entry_reason")
        used = t.pop("settings_used", None)
        if isinstance(used, dict) and used:
            blob = json.dumps(used, sort_keys=True, default=str)
            sid = keys.get(blob)
            if sid is None:
                sid = f"S{len(strategies) + 1}"
                keys[blob] = sid
                strategies[sid] = used
            t["strategy_id"] = sid
        else:
            t["strategy_id"] = None
        trades.append(t)
    return trades, strategies


def _terminal():
    """The SMA terminal module running in this process, or None."""
    try:
        from app.sma_host import load_terminal

        return load_terminal()
    except Exception:  # noqa: BLE001
        return None


def _trade(row: dict) -> dict:
    keys = (
        "id", "symbol", "direction", "qty", "entry_time", "entry_price", "exit_time", "exit_price",
        "exit_reason", "gross_pnl", "brokerage_and_taxes", "net_pnl", "points", "mode", "sl_trigger_price",
        "max_high", "max_low",
    )
    out = {k: row.get(k) for k in keys}
    strategy = row.get("strategy")
    if isinstance(strategy, dict):
        out["settings_used"] = strategy
    return out


def _day_totals(rows) -> list[dict]:
    days: dict[tuple[str, str], dict] = defaultdict(lambda: {"trades": 0, "wins": 0, "net": 0.0, "charges": 0.0})
    for row in rows:
        if row.exit_time is None:
            continue
        d = days[(row.date, (row.mode or "PAPER").upper())]
        d["trades"] += 1
        d["wins"] += 1 if (row.net_pnl or 0) > 0 else 0
        d["net"] += float(row.net_pnl or 0)
        d["charges"] += float(row.brokerage_and_taxes or 0)
    return [
        {"date": day, "mode": mode, **{k: round(v, 2) if isinstance(v, float) else v for k, v in vals.items()}}
        for (day, mode), vals in sorted(days.items(), reverse=True)
    ]


def sma_facts() -> dict:
    """The SMA terminal's state as plain JSON for the assistant."""
    facts: dict = {"strategy_guide": STRATEGY_GUIDE}
    mod = _terminal()
    engine = getattr(mod, "engine", None) if mod is not None else None
    if engine is None:
        facts["available"] = False
        facts["note"] = (
            "The SMA terminal is not running inside this server (local development runs it separately), so "
            "its live numbers are not here. Use the `screen` data the page sent, if any."
        )
        return facts
    facts["available"] = True
    try:
        mode = (engine.load_config().trading_mode or "PAPER").upper()
    except Exception:  # noqa: BLE001
        mode = "UNKNOWN"
    facts["execution_mode"] = mode
    facts["real_money"] = mode == "LIVE"
    facts["execution_note"] = (
        "LIVE: the SMA bot sends REAL Groww orders with REAL money."
        if mode == "LIVE"
        else "PAPER: practice fills only; no order reaches Groww."
        if mode == "PAPER"
        else "Could not read the mode; check the terminal's mode button."
    )
    try:
        from database import session_factory
        from models import TradeLog
        from strategy_engine import trade_names

        cfg = engine.load_config()
        config_dict = getattr(mod, "_config_dict", None)
        facts["settings"] = config_dict(cfg) if config_dict else {}
        facts["armed_stocks"] = trade_names(cfg)
        snap = engine.snapshot()
        facts["state"] = {
            k: snap.get(k)
            for k in (
                "bot_status", "halt_reason", "mode", "data_source", "last_error", "last_signal", "symbol",
                "books", "ltp", "sma9", "sma21", "atr14", "adx14", "rsi14", "vwap", "volume_ratio",
                "realized_net_pnl", "open_net_total", "trades_today", "max_trades", "max_daily_loss",
            )
        }
        facts["today_kpis"] = {mode: engine._kpis(mode) for mode in ("PAPER", "LIVE")}

        today = dt.datetime.now(IST).date()
        since = (today - dt.timedelta(days=RECENT_DAYS * 2)).isoformat()
        with session_factory()() as db:
            todays = (
                db.query(TradeLog)
                .filter(TradeLog.date == today.isoformat(), TradeLog.mode.in_(("PAPER", "LIVE")))
                .order_by(TradeLog.id.desc())
                .limit(MAX_TRADES_TODAY)
                .all()
            )
            recent = (
                db.query(TradeLog)
                .filter(TradeLog.date >= since, TradeLog.mode.in_(("PAPER", "LIVE")))
                .all()
            )
            from strategy_engine import _trade_dict

            facts["trades_today"] = [_trade(_trade_dict(r)) for r in todays]
        facts["trades_today_count"] = len(facts["trades_today"])
        facts["recent_days"] = _day_totals(recent)[: RECENT_DAYS * 2]
        try:
            from replay import list_runs

            facts["recent_backtests"] = [
                {k: run.get(k) for k in ("id", "start_date", "end_date", "symbols", "status", "totals", "settings")}
                for run in list_runs(RECENT_RUNS)
            ]
        except Exception:  # noqa: BLE001
            logger.exception("replay runs for chat failed")
        # Every bot: its own status, book, signal lines and today's cross timeline per stock.
        try:
            with session_factory()() as db:
                all_today = (
                    db.query(TradeLog)
                    .filter(TradeLog.date == today.isoformat(), TradeLog.mode.in_(("PAPER", "LIVE", "RESEARCH")))
                    .order_by(TradeLog.id.desc())
                    .limit(MAX_TRADES_TODAY * 3)
                    .all()
                )
                today_dicts = [_trade_dict(r) for r in all_today]
            defaults_fn = getattr(mod, "strategy_defaults", None)
            defaults = defaults_fn() if callable(defaults_fn) else None
            engines: list[tuple[str, object]] = [("1", engine)]
            for n, eng in sorted((getattr(mod, "bot_engines", None) or {}).items()):
                engines.append((str(n), eng))
            research = getattr(mod, "research_engine", None)
            if research is not None:
                engines.append(("research", research))
            facts["bots"] = []
            for label, eng in engines:
                try:
                    facts["bots"].append(_bot_facts(mod, eng, label, today, today_dicts, defaults, full_settings=(label == "1")))
                except Exception as exc:  # noqa: BLE001
                    logger.exception("bot %s facts for chat failed", label)
                    facts["bots"].append({"bot": label, "error": f"could not read: {exc}"})
            facts["bots_note"] = (
                "Each bot has its own book, Trade list, settings and status. `stocks_today[].crosses_today` lists every "
                "closed-candle SMA cross on that bot's candle size today with what the bot did on it (entered, closed, "
                "refused and why, cut-off, not armed, cross exit off, gap mode, caps). Bot 1's `settings` are complete; "
                "the other bots show only the settings that differ from the defaults."
            )
            facts["recent_trades"], facts["strategies_used"] = _recent_trades(session_factory, TradeLog, _trade_dict, today)
            facts["recent_trades_note"] = (
                "Trades of the last days (newest first, every bot, PAPER / LIVE / RESEARCH). Each carries `strategy_id`; "
                "`strategies_used[strategy_id]` is the exact strategy settings saved at that trade's entry (the fields "
                "of the bot's strategy then). `entry_reason` says what opened it (SMA_CROSS, GAP_MODE, JOIN_TREND, "
                "SMA9_TURN, FAST_MOVE, NEWS ...), `exit_reason` what closed it. A trade without a strategy_id was booked "
                "before settings were recorded per trade: then use `settings_history` by time."
            )
            facts["settings_catalog"] = settings_catalog(mod)
            presets = getattr(mod, "strategy_presets", None)
            if presets is not None:
                try:
                    facts["settings_history"] = settings_history(presets)
                except Exception:  # noqa: BLE001
                    logger.exception("settings history for chat failed")
        except Exception as exc:  # noqa: BLE001
            logger.exception("bot facts for chat failed")
            facts["bots_error"] = f"Could not read the bots: {exc}"
    except Exception as exc:  # noqa: BLE001
        logger.exception("SMA facts for chat failed")
        facts["error"] = f"Could not read the SMA terminal: {exc}"
    return facts


def screen_facts(page: str | None, screen: dict | None) -> dict | None:
    """What the page showed when the question was asked, capped in size."""
    if not page and not screen:
        return None
    out: dict = {"page": (page or "")[:200]}
    if screen:
        text = json.dumps(screen, default=str)
        if len(text) > MAX_SCREEN_CHARS:
            out["data_truncated"] = True
            text = text[:MAX_SCREEN_CHARS]
            out["data_json_prefix"] = text
        else:
            out["data"] = screen
    return out
