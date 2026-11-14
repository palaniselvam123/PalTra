"""Un-arm a stock once its trend fades (owner's request: a stock armed on a strong morning read stayed on the bot all
day after it went sideways).

While it is on, each armed stock of a RUNNING bot is read once per closed candle on the candles the bot trades
(`StrategyEngine._exit_minutes`: the bot's own candle, or the colour / pattern candles), with the same arithmetic as
the Stocks bar's Trend column (`stock_regime.read`: today's SMA crosses and the trend phase now). When that read says
choppy for `fade_unarm_min` minutes in a row the stock comes off this bot's Trade list:

* `PHASE` (default): the phase now is CHOP (no efficient run of 5+ candles, trend_phases.py);
* `SCORE`: today's crosses read CHOPPY (chop_scan.score_for);
* `EITHER`: either of them.

A read that is not ready yet (too few candles today, `phase_early`) never counts. An open trade is never cut: the stock
is un-armed once this bot is flat on it, so the trade runs to its own stop and exits. Arming only, no order; in a
replay the stock only leaves that run. The timer is memory only (a restart starts it again).
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

RULES = ("PHASE", "SCORE", "EITHER")
DEFAULT_RULE = "PHASE"
DEFAULT_MIN = 30
MIN_MIN = 5
MAX_MIN = 240


def on(cfg) -> bool:
    return bool(getattr(cfg, "fade_unarm", False))


def rule(cfg) -> str:
    value = str(getattr(cfg, "fade_unarm_rule", None) or DEFAULT_RULE).upper()
    return value if value in RULES else DEFAULT_RULE


def after_min(cfg) -> int:
    try:
        value = int(getattr(cfg, "fade_unarm_min", None) or DEFAULT_MIN)
    except (TypeError, ValueError):
        value = DEFAULT_MIN
    return max(MIN_MIN, min(MAX_MIN, value))


def faded(read: dict | None, which: str) -> str | None:
    """Plain words for why the read counts as faded, or None (trending, or not readable yet)."""
    if not read or read.get("phase_early"):
        return None
    phase_chop = read.get("phase") == "CHOP"
    score_chop = read.get("score") == "CHOPPY"
    minutes = read.get("minutes")
    size = f" on {minutes}-min candles" if minutes else ""
    if which in ("PHASE", "EITHER") and phase_chop:
        return f"no trend run now{size}"
    if which in ("SCORE", "EITHER") and score_chop:
        return f"choppy: {read.get('crosses', 0)} SMA crosses today{size}"
    return None


def unarm_row(config_id: int, symbol: str) -> str:
    """Take `symbol` off the Trade list of BotConfig row `config_id` (bot 1 = 1, research = 2, bots 2-5 = 3-6).
    Never raises; the arm log records it (arm_log.py)."""
    from database import session_factory
    from models import BotConfig
    from strategy_engine import apply_trade_sources, trade_names

    name = (symbol or "").upper()
    try:
        with session_factory()() as db:
            row = db.get(BotConfig, int(config_id))
            if row is None:
                return "not unarmed: settings row missing"
            left = [n for n in trade_names(row) if n != name]
            if len(left) == len(trade_names(row)):
                return "already off the Trade list"
            row.trade_symbols = ",".join(left)
            row.trade_sources = apply_trade_sources(row, {name: None}, left)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("fade un-arm failed for %s", name)
        return f"not unarmed: {exc}"
    return "taken off the Trade list"
