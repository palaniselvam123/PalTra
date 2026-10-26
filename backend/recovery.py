"""Structural exit, recovery hold and the trend-to-chop handoff (recovery.py).

All off by default, per bot and per stock, PAPER and LIVE. Meant for the trend bot
(Bot 3); the handoff arms the stock on Bot 2. Nothing here places an order: the
engine decides and orders through its usual paths.

**Slow SMA exit** (`slow_sma_exit`): a trade closes on the first candle, closed after
the entry, that closes strictly beyond the slow SMA against it (below for a long, above
for a short). Exit reason `SLOW_SMA_BREACH`. While it is on, the counter-candle exit is
not used: minor candles against the trade are ignored while the close holds the slow SMA.
A trade in recovery hold is judged by the recovery rules instead.

**Recovery hold** (`recovery_hold`, needs the SMA cross exit on): on an opposite SMA
cross, a trade in profit closes at once (`PROFITABLE_TREND_EXHAUSTION`) and the bot
reverses as usual. A trade at a loss is not closed: it goes into recovery hold with the
loss at the cross kept as its reference. On each candle close after that, in order:

1. Recovery target (`RECOVERY_TARGET_HIT`): the loss has shrunk to `recovery_tolerance_pct`
   % of the entry price or less, or the candle touched or closed back across the slow
   SMA. With `recovery_handoff` on, the stock then moves to Bot 2 (`HANDOFF_BOT`).
2. Emergency exit (`EMERGENCY_RECOVERY_ABORT`): the loss reached `recovery_emergency_mult`
   x the loss at the cross, or `recovery_atr_mult` x ATR, or `recovery_max_candles`
   candles have closed in recovery. The stock is not traded again on this bot today.

A trade still in recovery at the entry cut-off (15:00 by default) closes at market
(`SQUARE_OFF_CUTOFF`). The stop and the other exits still guard the trade throughout.

**Handoff** (`handoff`): the stock leaves the source bot's Trade list and is armed on
Bot 2 with its own settings: flipped orders, SMA gap mode in at ±0.119 %, out at
±0.003 %, the fade confirmed by a close past the slow SMA, VWAP check, RSI buy 40-70 and
sell 30-60, and the breakout handoff back to Bot 3 (breakout.py). Bot 2 waits for its
next cross; it never orders at once. A PAPER source never
arms a LIVE Bot 2, and the one-LIVE-bot-per-stock rule still applies.
"""
from __future__ import annotations

import json
import logging
import math

log = logging.getLogger("sma.recovery")

PROFIT_EXIT = "PROFITABLE_TREND_EXHAUSTION"
TARGET_EXIT = "RECOVERY_TARGET_HIT"
ABORT_EXIT = "EMERGENCY_RECOVERY_ABORT"
CUTOFF_EXIT = "SQUARE_OFF_CUTOFF"
SLOW_SMA_EXIT = "SLOW_SMA_BREACH"

DEFAULT_TOLERANCE_PCT = 0.15
DEFAULT_EMERGENCY_MULT = 1.5
DEFAULT_ATR_MULT = 2.5
DEFAULT_MAX_CANDLES = 6
MAX_CANDLES = 75

HANDOFF_BOT = 2
# Bot 2's own settings for a handed-off stock (the owner's chop recipe).
HANDOFF_SETTINGS = {
    "flip_orders": True,
    "use_gap_mode": True,
    "gap_entry_long": 0.119,
    "gap_entry_short": -0.119,
    "gap_exit_long": 0.003,
    "gap_exit_short": -0.003,
    "gap_fade_confirm_sma": True,
    "use_vwap": True,
    "use_rsi": True,
    "rsi_long_min": 40.0,
    "rsi_long_max": 70.0,
    "rsi_short_min": 30.0,
    "rsi_short_max": 60.0,
    # A breakout hands it back to the trend bot (breakout.py).
    "breakout_handoff": True,
}


def _num(cfg, name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(getattr(cfg, name, None) if getattr(cfg, name, None) is not None else default)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(low, min(high, value))


def slow_exit_on(cfg) -> bool:
    return bool(getattr(cfg, "slow_sma_exit", False))


def on(cfg) -> bool:
    return bool(getattr(cfg, "recovery_hold", False))


def handoff_on(cfg) -> bool:
    return getattr(cfg, "recovery_handoff", None) is not False


def handoff_now_on(cfg) -> bool:
    """Bot 2 orders at once after a hand-off (the opposite of the closed trade), off by default."""
    return bool(getattr(cfg, "handoff_now", False))


def order_now(to_bot: int, symbol: str, direction: str) -> str:
    """Bot `to_bot` orders `direction` (LONG / SHORT) on `symbol` at once (`handoff_now`). Never raises.

    The order runs on the event loop as Bot 2's own task (`StrategyEngine.direct_order`, entry reason
    HANDOFF): no filters, its own stop and exits; a RUNNING bot, market hours, the cut-off, the trade
    cap and one LIVE bot per stock still apply. The result goes to Bot 2's alerts and its signal line.
    """
    import asyncio

    import bots

    engine = bots.engines().get(int(to_bot))
    if engine is None:
        return f"Bot {to_bot} is not running: no order"
    signal = "BULLISH" if direction == "LONG" else "BEARISH"
    word = "buys" if direction == "LONG" else "sells"

    async def go() -> None:
        from strategy_engine import ForceRefused

        name = symbol.upper()
        try:
            result = await engine.direct_order(name, signal, why="HANDOFF", note=f"handed over, {word} at once")
        except ForceRefused as exc:
            result = f"not ordered: {exc}"
        except Exception as exc:  # noqa: BLE001 - the stock stays armed for its next cross
            log.exception("hand-off order failed for %s", name)
            result = f"not ordered: {exc}"
        engine._signals[name] = f"{name} hand-off order: {result}"  # noqa: SLF001
        engine._alert(f"{name} handed over: Bot {to_bot} {word} at once — {result}")  # noqa: SLF001

    try:
        asyncio.get_running_loop().create_task(go())
    except RuntimeError:
        return f"Bot {to_bot} order not sent: no event loop"
    return f"Bot {to_bot} {word} at once"


def tolerance_pct(cfg) -> float:
    return _num(cfg, "recovery_tolerance_pct", DEFAULT_TOLERANCE_PCT, 0.0, 5.0)


def emergency_mult(cfg) -> float:
    return _num(cfg, "recovery_emergency_mult", DEFAULT_EMERGENCY_MULT, 1.0, 10.0)


def atr_mult(cfg) -> float:
    return _num(cfg, "recovery_atr_mult", DEFAULT_ATR_MULT, 0.5, 20.0)


def max_candles(cfg) -> int:
    return int(_num(cfg, "recovery_max_candles", DEFAULT_MAX_CANDLES, 1, MAX_CANDLES))


def loss_per_share(direction: str, entry: float, price: float) -> float:
    """Positive while the trade is losing, negative while it is in profit."""
    return (entry - price) if direction == "LONG" else (price - entry)


def slow_breach(direction: str, close: float | None, slow: float | None) -> bool:
    """Did this close go strictly beyond the slow SMA against the trade?"""
    if close is None or slow is None:
        return False
    return close < slow if direction == "LONG" else close > slow


def judge(
    direction: str,
    entry: float,
    bar: dict,
    adverse_loss: float,
    candles_held: int,
    cfg,
) -> tuple[str, str]:
    """("target" | "abort" | "hold", note) for a trade in recovery hold on one closed candle.

    `bar` has close, high, low, sma_21 (slow) and atr_14 of that candle; `direction` is the
    real position's side; `adverse_loss` is the loss per share at the cross (>= 0).
    """
    close = bar.get("close")
    if close is None:
        return "hold", "no close"
    loss = loss_per_share(direction, entry, close)
    slow = bar.get("sma_21")
    tol = tolerance_pct(cfg) / 100 * entry
    if loss <= tol:
        return "target", f"loss {max(loss, 0):.2f} is within {tolerance_pct(cfg):g}% of the entry"
    if slow is not None:
        touched = bar.get("high", close) >= slow if direction == "LONG" else bar.get("low", close) <= slow
        if touched:
            return "target", f"the candle came back to the slow SMA {slow:.2f}"
    floor = emergency_mult(cfg) * max(adverse_loss, 0.0)
    if adverse_loss > 0 and loss >= floor:
        return "abort", f"loss {loss:.2f} reached {emergency_mult(cfg):g}x the {adverse_loss:.2f} at the cross"
    atr = bar.get("atr_14")
    if atr is not None and atr > 0 and loss >= atr_mult(cfg) * atr:
        return "abort", f"loss {loss:.2f} reached {atr_mult(cfg):g}x ATR {atr:.2f}"
    if candles_held >= max_candles(cfg):
        return "abort", f"no recovery within {max_candles(cfg)} candles"
    return "hold", f"loss {loss:.2f}, {candles_held} of {max_candles(cfg)} candles"


def handoff(from_bot: int, symbol: str, source_live: bool, to_bot: int = HANDOFF_BOT) -> str:
    """Move `symbol` from `from_bot` to `to_bot`. Returns what happened.

    To Bot 2 (`HANDOFF_BOT`) the stock gets the chop settings; to another bot (the
    breakout handoff back to Bot 3, breakout.py) it trades on that bot's own settings.
    The target is armed to wait for its next cross (no order now). Never raises.
    """
    from database import session_factory
    from models import BotConfig
    import bots
    from strategy_engine import MAX_TRADE_SYMBOLS, apply_trade_sources, stock_settings, trade_names

    name = (symbol or "").upper()
    to_bot = int(to_bot)
    if int(from_bot) == to_bot:
        return f"already on Bot {to_bot}: no handoff"
    try:
        with session_factory()() as db:
            target = db.get(BotConfig, bots.config_id_for(to_bot))
            source = db.get(BotConfig, bots.config_id_for(int(from_bot)))
            if target is None or source is None:
                return f"no handoff: Bot {to_bot} is not set up"
            target_live = (target.trading_mode or "PAPER").upper() == "LIVE"
            if target_live and not source_live:
                return f"no handoff: Bot {to_bot} is LIVE and this trade was practice money"
            names = trade_names(target)
            if name not in names and len(names) >= MAX_TRADE_SYMBOLS:
                return f"no handoff: Bot {to_bot}'s Trade list is full ({MAX_TRADE_SYMBOLS})"
            # Off the source bot first, so the one-LIVE-bot-per-stock rule sees it gone.
            left = [n for n in trade_names(source) if n != name]
            source.trade_symbols = ",".join(left)
            source.trade_sources = apply_trade_sources(source, {name: None}, left)
            db.commit()
            if target_live:
                why = bots.live_conflict(to_bot, [name])
                if why:
                    return f"no handoff: {why}"
            if to_bot == HANDOFF_BOT:
                everything = stock_settings(target)
                everything[name] = {**everything.get(name, {}), **HANDOFF_SETTINGS}
                target.stock_settings = json.dumps(everything)
            if name not in names:
                names.append(name)
            target.trade_symbols = ",".join(names)
            target.trade_sources = apply_trade_sources(target, {name: f"Handoff from Bot {from_bot}"}, names)
            db.commit()
    except Exception as exc:  # noqa: BLE001
        log.exception("recovery handoff failed for %s", name)
        return f"no handoff: {exc}"
    engine = bots.engines().get(to_bot)
    if engine is not None:
        # The cross already on the tape is not traded: Bot 2 waits for its next one.
        engine.hold_for_next_cross([name])
    if to_bot == HANDOFF_BOT:
        return "moved to Bot 2 (flipped, gap mode) — waits for its next cross"
    return f"moved to Bot {to_bot} — waits for its next cross"
