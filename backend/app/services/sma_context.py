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
    "force_order": "Enters now in the cross direction, skipping the entry filters; still refused out of hours or on an unarmed stock.",
    "risk": "max_daily_loss stops the bot for the day when realised loss reaches it; max_trades_per_day caps entries.",
    "modes": (
        "PAPER fills locally at the price (virtual money). LIVE sends real Groww MIS orders with an exchange "
        "stop. REPLAY trades are backtests on past candles and have their own book."
    ),
    "exit_reasons": {
        "MA_CROSS": "opposite SMA cross",
        "ATR_SL_HIT": "ATR stop",
        "GAP_SL_HIT": "SMA-gap moving stop",
        "TSL_HIT": "trailing stop",
        "TARGET_HIT": "target",
        "BB_TARGET": "Bollinger band target",
        "BB_MIDDLE": "Bollinger middle band exit",
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
