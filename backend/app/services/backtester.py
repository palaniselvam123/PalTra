"""Historical replay — does the strategy actually have an edge?

Everything here exists to answer one question honestly, so the design is built
around the ways a backtest usually lies:

**No look-ahead.** The engine only ever sees bars up to and including the one
being judged. Entry happens on the NEXT bar, because at the moment a signal
bar closes you have not yet traded — you act on the bar after it.

**Intra-bar ambiguity resolves against you.** If a bar's range contains both
the stop and the target, OHLC data cannot say which was touched first. Assuming
the target is the single most common way a backtest invents profit, so this
assumes the STOP. Real results land between the two; this end is the safe one.

**Identical costs to live.** Slippage and charges come from `paper_engine`, the
same functions the live paper account uses. A backtest with frictionless fills
would have shown the real HDFCBANK trade from this app's own history as a small
win, when it actually lost ₹3,591 to costs.

**Identical sizing.** Quantity comes from the same risk manager, including the
leverage cap — otherwise the backtest sizes positions the live engine would
refuse.

**Intraday means intraday.** Every position is closed at the session cut-off.
Carrying overnight would measure a different strategy than the one being run.
"""
from __future__ import annotations

import datetime as dt
import math
import statistics
from dataclasses import dataclass, field

from app.core.market_clock import IST
from app.services.indicators import OHLCV, adx, atr
from app.services.paper_engine import estimate_charges
from app.services.scanner_engine import StrategyEngine, StrategyParams

SLIPPAGE_PCT = 0.05          # per leg, matching the live paper engine
SESSION_CLOSE = dt.time(15, 15)   # square off before the 15:30 bell
TRENDING_ADX = 20.0


@dataclass
class BacktestConfig:
    capital: float = 100_000.0
    risk_per_trade_pct: float = 1.0
    max_leverage: float = 5.0
    stop_atr_multiple: float = 1.5    # stop distance = N x ATR(14)
    risk_reward: float = 2.0          # target = R:R x stop distance
    exit_on_opposite: bool = True     # a reverse signal closes the position
    max_trades_per_day: int = 5


@dataclass
class ForwardOutcome:
    """What the price actually did after a signal, independent of the exit
    rules. This is the yardstick every later component is graded against: it
    answers "did HIGH movement potential really move more?" without the answer
    being contaminated by whatever stop and target happened to be used.
    """

    mfe: float                      # max favourable excursion, rupees/share
    mae: float                      # max adverse excursion, rupees/share
    mfe_pct: float
    mae_pct: float
    move_5m: float | None           # signed move in the trade's direction
    move_15m: float | None
    move_30m: float | None
    move_60m: float | None
    bars_to_mfe: int | None
    reached_1r: bool                # did it ever reach 1x the initial risk?
    reached_2r: bool


@dataclass
class BacktestTrade:
    symbol: str
    side: str
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    quantity: int
    stop_loss: float
    target: float
    gross_pnl: float
    charges: float
    net_pnl: float
    r_multiple: float | None
    exit_reason: str
    adx_at_entry: float | None
    regime: str                        # TRENDING | RANGING | UNKNOWN
    bars_held: int
    forward: ForwardOutcome | None = None


def _ist_date(ts: int) -> dt.date:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).date()


def _ist_time(ts: int) -> dt.time:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).astimezone(IST).time()


def _size(config: BacktestConfig, entry: float, stop: float) -> int:
    """Same two-limb rule as the live risk manager: risk-based size bounded by
    what the account can actually carry.
    """
    per_share = abs(entry - stop)
    if per_share <= 0 or entry <= 0:
        return 0
    by_risk = int((config.capital * config.risk_per_trade_pct / 100) // per_share)
    by_exposure = int((config.capital * config.max_leverage) // entry)
    return max(min(by_risk, by_exposure), 0)


def _fill(side: str, price: float, opening: bool) -> float:
    """Slippage always works against the trader, on entry and exit alike."""
    slip = price * (SLIPPAGE_PCT / 100)
    if opening:
        return round(price + slip if side == "BUY" else price - slip, 2)
    return round(price - slip if side == "BUY" else price + slip, 2)


def run_backtest(
    symbol: str,
    candles: list[OHLCV],
    params: StrategyParams,
    config: BacktestConfig | None = None,
) -> list[BacktestTrade]:
    config = config or BacktestConfig()
    engine = StrategyEngine(params)
    # Indicators computed once for the whole series; evaluate_at then costs
    # O(1) per bar instead of recomputing every EMA on every bar.
    ctx = engine.precompute(candles)
    warmup = max(params.warmup_bars(), 30)
    if len(candles) < warmup + 5:
        return []

    atr_series = atr(candles, 14)
    adx_series = adx(candles, 14)["adx"]
    # Inferred from the data rather than passed in, so a caller cannot mislabel
    # 15m bars as 5m and silently corrupt every "move after N minutes" figure.
    bar_seconds = (candles[1].ts - candles[0].ts) if len(candles) > 1 else 300

    trades: list[BacktestTrade] = []
    open_pos: dict | None = None
    trades_today = 0
    current_day = _ist_date(candles[warmup].ts)

    for i in range(warmup, len(candles) - 1):
        bar = candles[i]
        day = _ist_date(bar.ts)
        if day != current_day:
            current_day = day
            trades_today = 0

        # ---- manage an open position on THIS bar -------------------------
        if open_pos is not None:
            hit_stop = bar.low <= open_pos["stop"] if open_pos["side"] == "BUY" else bar.high >= open_pos["stop"]
            hit_target = bar.high >= open_pos["target"] if open_pos["side"] == "BUY" else bar.low <= open_pos["target"]
            reason = None
            exit_price = None

            # Stop is checked first on purpose — see the module docstring.
            if hit_stop:
                reason, exit_price = "STOP-LOSS HIT", open_pos["stop"]
            elif hit_target:
                reason, exit_price = "TARGET HIT", open_pos["target"]
            elif _ist_time(bar.ts) >= SESSION_CLOSE or day != _ist_date(open_pos["entry_ts"]):
                reason, exit_price = "SESSION CLOSE", bar.close

            if reason is None and config.exit_on_opposite:
                sig = engine.evaluate_at(symbol, "bt", candles, i, ctx)
                if sig is not None and sig.side != open_pos["side"]:
                    reason, exit_price = "OPPOSITE SIGNAL", bar.close

            if reason is not None and exit_price is not None:
                trade = _close(open_pos, exit_price, bar.ts, reason, i)
                trade.forward = measure_forward(
                    candles,
                    open_pos["entry_index"],
                    open_pos["side"],
                    open_pos["entry_price"],
                    abs(open_pos["entry_price"] - open_pos["stop"]),
                    bar_seconds,
                )
                trades.append(trade)
                open_pos = None

        # ---- look for a new entry ---------------------------------------
        if open_pos is None and trades_today < config.max_trades_per_day:
            if _ist_time(bar.ts) >= SESSION_CLOSE:
                continue
            signal = engine.evaluate_at(symbol, "bt", candles, i, ctx)
            if signal is None:
                continue

            a = atr_series[i]
            if a is None or a <= 0:
                continue

            # Entry on the NEXT bar's open: at the instant this bar closed the
            # trade had not happened yet.
            nxt = candles[i + 1]
            if _ist_date(nxt.ts) != day or _ist_time(nxt.ts) >= SESSION_CLOSE:
                continue

            entry = _fill(signal.side, nxt.open, opening=True)
            stop_dist = a * config.stop_atr_multiple
            stop = entry - stop_dist if signal.side == "BUY" else entry + stop_dist
            target = (
                entry + stop_dist * config.risk_reward
                if signal.side == "BUY"
                else entry - stop_dist * config.risk_reward
            )
            qty = _size(config, entry, stop)
            if qty <= 0:
                continue

            open_pos = {
                "symbol": symbol,
                "side": signal.side,
                "entry_ts": nxt.ts,
                "entry_price": entry,
                "quantity": qty,
                "stop": round(stop, 2),
                "target": round(target, 2),
                "adx": adx_series[i],
                "entry_index": i + 1,
            }
            trades_today += 1

    # A position still open at the end of the data is closed on the last bar
    # rather than silently discarded — dropping it would hide a loss.
    if open_pos is not None:
        last = candles[-1]
        trade = _close(open_pos, last.close, last.ts, "END OF DATA", len(candles) - 1)
        trade.forward = measure_forward(
            candles,
            open_pos["entry_index"],
            open_pos["side"],
            open_pos["entry_price"],
            abs(open_pos["entry_price"] - open_pos["stop"]),
            bar_seconds,
        )
        trades.append(trade)

    return trades


def _close(pos: dict, raw_exit: float, exit_ts: int, reason: str, exit_index: int) -> BacktestTrade:
    side, qty, entry = pos["side"], pos["quantity"], pos["entry_price"]
    exit_price = _fill(side, raw_exit, opening=False)
    gross = (exit_price - entry) * qty if side == "BUY" else (entry - exit_price) * qty
    charges = estimate_charges(side, entry, qty) + estimate_charges(
        "SELL" if side == "BUY" else "BUY", exit_price, qty
    )
    net = gross - charges
    planned_risk = abs(entry - pos["stop"]) * qty
    a = pos.get("adx")
    return BacktestTrade(
        symbol=pos["symbol"],
        side=side,
        entry_ts=pos["entry_ts"],
        exit_ts=exit_ts,
        entry_price=entry,
        exit_price=exit_price,
        quantity=qty,
        stop_loss=pos["stop"],
        target=pos["target"],
        gross_pnl=round(gross, 2),
        charges=round(charges, 2),
        net_pnl=round(net, 2),
        r_multiple=round(net / planned_risk, 2) if planned_risk > 0 else None,
        exit_reason=reason,
        adx_at_entry=round(a, 1) if a is not None else None,
        regime=("TRENDING" if a >= TRENDING_ADX else "RANGING") if a is not None else "UNKNOWN",
        bars_held=exit_index - pos["entry_index"],
    )


# ---- metrics ---------------------------------------------------------------


def summarise(trades: list[BacktestTrade], config: BacktestConfig | None = None) -> dict:
    config = config or BacktestConfig()
    if not trades:
        return {"trades": 0, "note": "No trades were generated — the filters matched nothing in this data."}

    nets = [t.net_pnl for t in trades]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for n in nets:
        equity += n
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # Sharpe on DAILY returns, not per-trade: a per-trade figure inflates with
    # trade frequency and is not comparable to any published number.
    by_day: dict[dt.date, float] = {}
    for t in trades:
        by_day[_ist_date(t.exit_ts)] = by_day.get(_ist_date(t.exit_ts), 0.0) + t.net_pnl
    daily = [v / config.capital for v in by_day.values()]
    sharpe = None
    if len(daily) > 1:
        sd = statistics.pstdev(daily)
        if sd > 0:
            sharpe = round(statistics.fmean(daily) / sd * math.sqrt(252), 2)

    rs = [t.r_multiple for t in trades if t.r_multiple is not None]

    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1),
        "net_pnl": round(sum(nets), 2),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        # Profit factor is undefined with no losses; None says so rather than
        # printing an infinity that reads like a spectacular result.
        "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else None,
        "expectancy": round(statistics.fmean(nets), 2),
        "expectancy_r": round(statistics.fmean(rs), 2) if rs else None,
        "avg_win": round(statistics.fmean(wins), 2) if wins else 0.0,
        "avg_loss": round(statistics.fmean(losses), 2) if losses else 0.0,
        "payoff_ratio": round(statistics.fmean(wins) / abs(statistics.fmean(losses)), 2)
        if wins and losses
        else None,
        "largest_win": round(max(nets), 2),
        "largest_loss": round(min(nets), 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd / config.capital * 100, 2),
        "sharpe_daily": sharpe,
        "trading_days": len(by_day),
        "total_charges": round(sum(t.charges for t in trades), 2),
        "gross_before_costs": round(sum(t.gross_pnl for t in trades), 2),
        "avg_bars_held": round(statistics.fmean([t.bars_held for t in trades]), 1),
    }


def breakdown(trades: list[BacktestTrade], key: str) -> list[dict]:
    """Group trades by an attribute — regime, exit reason, symbol, side."""
    groups: dict[str, list[BacktestTrade]] = {}
    for t in trades:
        groups.setdefault(str(getattr(t, key, "?")), []).append(t)
    out = []
    for name, group in groups.items():
        nets = [t.net_pnl for t in group]
        wins = [n for n in nets if n > 0]
        out.append(
            {
                "key": name,
                "trades": len(group),
                "wins": len(wins),
                "win_rate_pct": round(len(wins) / len(group) * 100, 1),
                "net_pnl": round(sum(nets), 2),
                "avg_pnl": round(statistics.fmean(nets), 2),
            }
        )
    return sorted(out, key=lambda r: -r["net_pnl"])


def equity_curve(trades: list[BacktestTrade]) -> list[dict]:
    curve, equity, peak = [], 0.0, 0.0
    for t in trades:
        equity += t.net_pnl
        peak = max(peak, equity)
        curve.append(
            {
                "ts": t.exit_ts,
                "symbol": t.symbol,
                "pnl": t.net_pnl,
                "cumulative": round(equity, 2),
                "drawdown": round(peak - equity, 2),
            }
        )
    return curve


# ---- forward outcome measurement -------------------------------------------


def measure_forward(
    candles: list[OHLCV],
    entry_index: int,
    side: str,
    entry_price: float,
    risk_per_share: float,
    bar_seconds: int,
    horizon_bars: int = 24,
) -> ForwardOutcome:
    """Price behaviour after entry, measured over a FIXED horizon.

    Deliberately independent of the exit rules. A trade stopped out after two
    bars and a trade that ran to target both get measured the same way, so the
    question "which conditions preceded the largest favourable move?" can be
    asked without the stop placement answering it for you.

    Excursions are measured against the bar extremes, not closes: a stop sitting
    inside the bar's range would have been hit, and closes would hide that.
    Measurement never crosses the session boundary — an overnight gap is not
    intraday movement.
    """
    n = len(candles)
    entry_day = _ist_date(candles[entry_index].ts)
    window = []
    for j in range(entry_index, min(entry_index + horizon_bars + 1, n)):
        if _ist_date(candles[j].ts) != entry_day:
            break
        window.append(candles[j])

    if not window:
        return ForwardOutcome(0, 0, 0, 0, None, None, None, None, None, False, False)

    if side == "BUY":
        best = max(b.high for b in window)
        worst = min(b.low for b in window)
        mfe, mae = best - entry_price, entry_price - worst
    else:
        best = min(b.low for b in window)
        worst = max(b.high for b in window)
        mfe, mae = entry_price - best, worst - entry_price

    mfe, mae = max(mfe, 0.0), max(mae, 0.0)

    bars_to_mfe = None
    running = -float("inf")
    for k, b in enumerate(window):
        excursion = (b.high - entry_price) if side == "BUY" else (entry_price - b.low)
        if excursion > running:
            running, bars_to_mfe = excursion, k

    def move_after(minutes: int) -> float | None:
        steps = int(minutes * 60 / bar_seconds)
        if steps <= 0 or steps >= len(window):
            return None
        close = window[steps].close
        signed = (close - entry_price) if side == "BUY" else (entry_price - close)
        return round(signed, 2)

    return ForwardOutcome(
        mfe=round(mfe, 2),
        mae=round(mae, 2),
        mfe_pct=round(mfe / entry_price * 100, 3) if entry_price else 0.0,
        mae_pct=round(mae / entry_price * 100, 3) if entry_price else 0.0,
        move_5m=move_after(5),
        move_15m=move_after(15),
        move_30m=move_after(30),
        move_60m=move_after(60),
        bars_to_mfe=bars_to_mfe,
        reached_1r=mfe >= risk_per_share if risk_per_share > 0 else False,
        reached_2r=mfe >= risk_per_share * 2 if risk_per_share > 0 else False,
    )


def forward_summary(trades: list[BacktestTrade]) -> dict:
    """Aggregate forward outcomes — the numbers Phase 13's tests compare."""
    withf = [t for t in trades if t.forward is not None]
    if not withf:
        return {"measured": 0}
    f = [t.forward for t in withf]

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.fmean(vals), 2) if vals else None

    return {
        "measured": len(withf),
        "avg_mfe": avg([x.mfe for x in f]),
        "avg_mae": avg([x.mae for x in f]),
        "avg_mfe_pct": avg([x.mfe_pct for x in f]),
        "avg_mae_pct": avg([x.mae_pct for x in f]),
        # Above 1.0 means price offered more upside than it took away before
        # the exit rules got involved. Below 1.0 means the entries themselves
        # were poorly located, whatever the stop was.
        "mfe_mae_ratio": round(avg([x.mfe for x in f]) / avg([x.mae for x in f]), 2)
        if avg([x.mae for x in f])
        else None,
        "reached_1r_pct": round(sum(x.reached_1r for x in f) / len(f) * 100, 1),
        "reached_2r_pct": round(sum(x.reached_2r for x in f) / len(f) * 100, 1),
        "avg_move_5m": avg([x.move_5m for x in f]),
        "avg_move_15m": avg([x.move_15m for x in f]),
        "avg_move_30m": avg([x.move_30m for x in f]),
        "avg_move_60m": avg([x.move_60m for x in f]),
        "avg_bars_to_mfe": avg([x.bars_to_mfe for x in f]),
    }
