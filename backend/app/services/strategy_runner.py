"""Autonomous trading engine.

Two responsibilities, deliberately separated:

1. `monitor_tick()` — ALWAYS runs, bot on or off. Enforces stop-loss, target
   and trailing-stop exits on every open position. Without this an SL is just
   a number in a table; this is what makes it an actual bracket.

2. `on_candle_close()` — only runs when the bot is enabled. Builds the opening
   range, then evaluates the ORB strategy on each completed candle and fires
   entries through the shared, risk-gated execution path.

Session timing has two modes. `market` anchors the opening range to the real
NSE open (09:15-09:30 IST) — correct for live trading. `demo` anchors it to
whenever the bot was started, so the engine can be exercised outside market
hours against the simulated feed. Demo mode is NOT a trading strategy; it
exists so the machinery is testable at 2am.
"""
from __future__ import annotations

import datetime as dt
import statistics
import time
from dataclasses import dataclass, field, asdict
from enum import Enum

from app import state
from app.services.ai_advisor import ai_advisor
from app.services.broadcaster import broadcaster
from app.services.candle_builder import BuiltCandle, CandleBuilder
from app.services.execution import EntryRejected, place_paper_entry
from app.services.trade_ledger import close_and_settle
from app.services.market_data import DataSource, market_data
from app.services.scanner_engine import StrategyEngine, StrategyParams
from app.strategies.gainer_momentum import (
    GainerConfig,
    cap_to_risk,
    even_slot_budget,
    ma_pair_is_noise,
    past_last_entry,
    plan_entry,
)
from app.strategies.orb_strategy import Candle, ORBStrategy
from app.strategies.scanner import NIFTY50_UNIVERSE, OpeningRange

MARKET_OPEN_IST = dt.time(9, 15)


class BotStatus(str, Enum):
    STOPPED = "STOPPED"
    WAITING_FOR_OPEN = "WAITING_FOR_OPEN"
    BUILDING_RANGE = "BUILDING_RANGE"
    ARMED = "ARMED"
    NO_RANGE = "NO_RANGE"
    HALTED = "HALTED"


@dataclass
class StrategyConfig:
    session_mode: str = "demo"          # demo | market
    candle_interval_sec: int = 15       # market preset: 300 (5 min)
    range_duration_sec: int = 60        # market preset: 900 (15 min)
    rvol_threshold: float = 1.2         # cross-sectional in demo; 2.0 is the classic RVOL bar
    risk_reward: float = 2.0
    trailing_enabled: bool = True
    supertrend_period: int = 10
    supertrend_multiplier: float = 3.0
    max_symbols: int = 10
    # A breakout means little without a trend to break INTO — see
    # ORBStrategy.adx for why this exists. 20 is Wilder's own threshold for
    # "a trend is developing", not an arbitrary pick.
    adx_filter_enabled: bool = True
    adx_period: int = 14
    adx_threshold: float = 20.0

    # "orb" is the opening-range breakout above. "gainers" buys the strongest
    # movers of the session, sized by capital allocation instead of by stop
    # distance — see app/strategies/gainer_momentum.py for what that changes
    # about the risk profile.
    strategy: str = "orb"                # orb | gainers | scanner
    # In "scanner" mode the bot acts on the scanner's crossover signals: BUY
    # opens a position, SELL closes one it holds. Sizing reuses the gainers
    # buying-power maths so the two allocation strategies stay consistent.
    scanner_close_on_sell: bool = True
    gainers_top_n: int = 50
    # Share of BUYING POWER (balance x leverage) to deploy, split evenly
    # across `gainers_max_positions` names — never leftover-dumped into the
    # last slot.
    gainers_allocation_pct: float = 50.0
    gainers_max_positions: int = 3
    gainers_stop_loss_pct: float = 1.2
    gainers_target_pct: float = 2.4
    gainers_min_gain_pct: float = 0.0
    # The gainers strategy is driven by ticks, not candle closes — it needs a
    # ranking, not a completed bar. This throttles how often the ranking is
    # recomputed; every tick would re-query the account on all 26 symbols.
    gainers_scan_interval_sec: float = 3.0
    # No new entries this many minutes before the 15:30 square-off. A buy at
    # 15:27 with a 15:30 cut-off is a round-trip of charges, not a trade.
    last_entry_buffer_min: int = 45
    # Scanner SELL is ignored until the position has lived this long. EMA
    # death-crosses on a forming bar reverse in seconds; a 5-minute floor
    # lets the actual stop/target do the exiting instead.
    scanner_min_hold_sec: int = 300


@dataclass
class RangeAccumulator:
    high: float = float("-inf")
    low: float = float("inf")
    volume: int = 0
    candles: int = 0


@dataclass
class BotSnapshot:
    enabled: bool
    status: str
    config: dict
    range_ready: bool
    range_ends_in_sec: int | None
    opening_ranges: list[dict] = field(default_factory=list)
    symbols_traded: list[str] = field(default_factory=list)
    late_start: bool = False
    range_window: str | None = None
    # Why the gainers strategy is or isn't finding anything. "ARMED with no
    # trades" is otherwise indistinguishable from a broken strategy.
    gainers_candidates: list[dict] = field(default_factory=list)
    gainers_source: str | None = None
    # Scanner mode only: is the scanner running, and is it judging the forming
    # bar or waiting for it to close?
    scanner_running: bool = False
    scanner_intrabar: bool = False


class StrategyRunner:
    def __init__(self):
        self.config = StrategyConfig()
        self.enabled = False
        self.status = BotStatus.STOPPED
        self.candles = CandleBuilder(self.config.candle_interval_sec)
        self.strategy = ORBStrategy(risk_reward=self.config.risk_reward)

        self._accum: dict[str, RangeAccumulator] = {}
        self.opening_ranges: dict[str, OpeningRange] = {}
        self._range_start: dt.datetime | None = None
        self._range_finalized = False
        self._symbols_traded: set[str] = set()
        self._exiting: set[str] = set()
        # Latches the "nothing to rank" warning so it is said once per dry
        # spell rather than on every closed candle.
        self._gainers_empty_logged = False
        self._last_gainers_scan = 0.0
        self._session_date: dt.date | None = None
        # Last 5m bar timestamp we already acted on per symbol, so a standing
        # golden cross is not re-bought every 3-second scan.
        self._ta_acted: dict[str, int] = {}
        # True when the range was measured from the start moment because the
        # 09:15 window had already passed — a mid-session range, not an ORB.
        self._late_start = False
        # Last AI-gate veto per symbol, so a standing veto is logged once
        # instead of once per candle for the rest of the session.
        self._gate_vetoes: dict[str, str] = {}

    # ----- lifecycle -------------------------------------------------

    async def start(self) -> None:
        self.enabled = True
        self._reset_session()
        self._range_start = self._compute_range_start()
        self.status = self._status_for_now()

        window = f"{self._range_start.strftime('%H:%M')}–{self._range_end().strftime('%H:%M')}"
        if self._late_start:
            message = (
                f"BOT STARTED (market session, LATE START) — the 09:15 opening-range window has already "
                f"closed, so the range will be measured over {window} instead, on "
                f"{self.config.candle_interval_sec // 60}-min candles. This is a mid-session range, not the "
                f"classic opening range. First entries are possible once it locks at "
                f"{self._range_end().strftime('%H:%M')}."
            )
        else:
            message = (
                f"BOT STARTED ({self.config.session_mode} session) — building the {window} opening range "
                f"on {self.config.candle_interval_sec}s candles"
            )
        if self.config.strategy in ("gainers", "scanner"):
            message = (
                f"BOT STARTED ({self.config.session_mode} session, {self.config.strategy}) — "
                f"top {self.config.gainers_top_n} universe, EMA 9/21 + ADX/volume/RSI entry, "
                f"intraday brackets −{self.config.gainers_stop_loss_pct:g}% / "
                f"+{self.config.gainers_target_pct:g}%, max {self.config.gainers_max_positions} names, "
                f"no new entries after "
                f"{self._last_entry_cutoff().strftime('%H:%M') if self._last_entry_cutoff() else 'session close'} IST."
            )
        await broadcaster.publish("log", {"level": "INFO", "message": message})
        if self.config.strategy in ("gainers", "scanner"):
            await self._harden_scanner_for_trading()
            await self._ensure_scanner_running()
        await self.publish_status()

    async def stop(self) -> None:
        self.enabled = False
        self.status = BotStatus.STOPPED
        await broadcaster.publish("log", {"level": "WARN", "message": "BOT STOPPED — no new auto entries."})
        await self.publish_status()

    def _reset_session(self) -> None:
        self.candles.set_interval(self.config.candle_interval_sec)
        self.candles.reset()
        self.strategy = ORBStrategy(risk_reward=self.config.risk_reward)
        self._accum.clear()
        self.opening_ranges.clear()
        self._range_finalized = False
        self._symbols_traded.clear()
        self._gate_vetoes.clear()
        self._ta_acted.clear()
        self._session_date = dt.date.today()

    def _compute_range_start(self) -> dt.datetime:
        """Where the opening-range window begins.

        `market` normally anchors to the real 09:15 session open. If the bot is
        started after that window has already closed, anchoring there would
        measure nothing — the bot wasn't running for it — leaving an empty
        range set and a bot that silently never trades. So a late start
        measures its range from the start moment instead, still on real 5-min
        candles. That is a mid-session range, not the classic opening range,
        and `_late_start` records the difference so the UI can say so.
        """
        now = dt.datetime.now()
        if self.config.session_mode != "market":
            self._late_start = False
            return now

        session_open = dt.datetime.combine(now.date(), MARKET_OPEN_IST)
        window_end = session_open + dt.timedelta(seconds=self.config.range_duration_sec)
        self._late_start = now >= window_end
        return now if self._late_start else session_open

    def _range_end(self) -> dt.datetime | None:
        if self._range_start is None:
            return None
        return self._range_start + dt.timedelta(seconds=self.config.range_duration_sec)

    def _status_for_now(self) -> BotStatus:
        if state.risk_manager.state.locked or state.kill_switch_active:
            return BotStatus.HALTED
        if not self.enabled:
            return BotStatus.STOPPED
        # Neither the gainers nor the scanner strategy builds or reads an
        # opening range, so the range states do not apply to them — they are
        # armed the moment they are enabled. Reporting BUILDING RANGE here was
        # not just cosmetic: it told the operator to wait 15 minutes for a
        # window that these strategies never consult.
        if self.config.strategy in ("gainers", "scanner"):
            return BotStatus.ARMED

        now = dt.datetime.now()
        if self._range_start and now < self._range_start:
            return BotStatus.WAITING_FOR_OPEN
        if not self._range_finalized:
            return BotStatus.BUILDING_RANGE
        # Finalised but empty: the window produced no usable candles, so every
        # symbol fails the range lookup in _evaluate_entry. Reporting ARMED
        # here would claim the bot is hunting breakouts when it cannot take one.
        if not self.opening_ranges:
            return BotStatus.NO_RANGE
        return BotStatus.ARMED

    # ----- always-on position protection ------------------------------

    async def maybe_scan_gainers(self) -> None:
        """Tick-driven scan: TA exits on open bot positions, and (in gainers
        mode) TA entries on the top-50 ranking.

        Ranking alone is not an entry. The name has to also print a closed-bar
        golden cross through the same filters the scanner uses.
        """
        if not self.enabled:
            return
        if state.risk_manager.state.locked or state.kill_switch_active:
            return

        now = time.monotonic()
        if now - self._last_gainers_scan < self.config.gainers_scan_interval_sec:
            return
        self._last_gainers_scan = now

        await self._maybe_ta_exits()
        if self.config.strategy == "gainers":
            await self._evaluate_gainers()

    async def monitor_tick(self, symbol: str, ltp: float) -> None:
        """Bracket enforcement. Runs on every tick regardless of bot state so
        manually-opened positions are protected too.
        """
        position = state.paper_engine.positions.get(symbol)
        if position is None or symbol in self._exiting:
            return

        reason: str | None = None
        if position.side == "BUY":
            if position.stop_loss and ltp <= position.stop_loss:
                reason = "STOP-LOSS HIT"
            elif position.target and ltp >= position.target:
                reason = "TARGET HIT"
        else:
            if position.stop_loss and ltp >= position.stop_loss:
                reason = "STOP-LOSS HIT"
            elif position.target and ltp <= position.target:
                reason = "TARGET HIT"

        if reason is None:
            self._apply_trailing_stop(symbol, position)
            return

        self._exiting.add(symbol)
        try:
            _, breaker = await close_and_settle(symbol, reason)
            if breaker is not None:
                await self.halt_for_circuit_breaker(breaker.reason)
        finally:
            self._exiting.discard(symbol)

    def _apply_trailing_stop(self, symbol: str, position) -> None:
        if not self.config.trailing_enabled:
            return
        history = self.candles.history(symbol)
        if len(history) < self.config.supertrend_period + 1:
            return

        candles = [Candle(c.open, c.high, c.low, c.close, c.volume) for c in history]
        bands = ORBStrategy.supertrend_bands(
            candles, self.config.supertrend_period, self.config.supertrend_multiplier
        )
        if bands is None:
            return
        lower, upper = bands

        # Ratchet only — a trailing stop must never loosen.
        if position.side == "BUY" and lower > position.stop_loss:
            position.stop_loss = round(lower, 2)
        elif position.side == "SELL" and upper < position.stop_loss:
            position.stop_loss = round(upper, 2)

    async def force_halt(self) -> None:
        """Stop taking entries without recursing back into the kill switch."""
        self.enabled = False
        self.status = BotStatus.HALTED

    async def halt_for_circuit_breaker(self, reason: str) -> None:
        """The trading day is over — either the loss limit was breached or the
        profit target was met. Either way, stop the bot AND square off every
        remaining position: stopping entries alone would leave live risk on
        the book, which defeats the purpose of both limits.
        """
        from app.services.safety import trigger_kill_switch

        await self.force_halt()
        await broadcaster.publish("log", {"level": "ERROR", "message": f"BOT HALTED — {reason}"})
        await trigger_kill_switch(reason)

    # ----- strategy loop ----------------------------------------------

    async def on_candle_close(self, candle: BuiltCandle) -> None:
        if not self.enabled:
            return
        if self._session_date != dt.date.today():
            self._reset_session()
            self._range_start = self._compute_range_start()

        now = dt.datetime.now()
        range_end = self._range_end()
        if range_end is None:
            return

        if now < range_end:
            self._accumulate_range(candle)
            self.status = self._status_for_now()
            return

        if not self._range_finalized:
            await self._finalize_ranges()

        if state.risk_manager.state.locked or state.kill_switch_active:
            self.status = BotStatus.HALTED
            return

        self.status = BotStatus.ARMED

        await self._maybe_ta_exit(candle.symbol)

        if self.config.strategy in ("gainers", "scanner"):
            # Entries for these modes come from the TA scan / scanner worker.
            return

        await self._evaluate_entry(candle)

    def _accumulate_range(self, candle: BuiltCandle) -> None:
        acc = self._accum.setdefault(candle.symbol, RangeAccumulator())
        acc.high = max(acc.high, candle.high)
        acc.low = min(acc.low, candle.low)
        acc.volume += candle.volume
        acc.candles += 1

    async def _finalize_ranges(self) -> None:
        self._range_finalized = True
        usable = {s: a for s, a in self._accum.items() if a.candles > 0 and a.high > a.low}
        if not usable:
            await broadcaster.publish(
                "log",
                {
                    "level": "ERROR",
                    "message": "Opening range window closed with no usable candles — the bot has no range "
                    "to break out of and will NOT take any entries this session. This happens when it was "
                    "not running during the range window. Stop it, switch to DEMO TIMING to test now, or "
                    "restart before the next session open.",
                },
            )
            await self.publish_status()
            return

        # No 20-day history behind the simulated feed, so RVOL is measured
        # cross-sectionally: this symbol's range volume vs the universe median.
        volumes = [a.volume for a in usable.values()]
        median_volume = statistics.median(volumes) or 1

        for symbol, acc in usable.items():
            self.opening_ranges[symbol] = OpeningRange(
                symbol=symbol,
                high=round(acc.high, 2),
                low=round(acc.low, 2),
                volume_first_15m=acc.volume,
                avg_20d_volume=0.0,
                rvol_override=round(acc.volume / median_volume, 2),
            )

        qualifying = [r for r in self.opening_ranges.values() if r.rvol >= self.config.rvol_threshold]
        await broadcaster.publish(
            "log",
            {
                "level": "INFO",
                "message": f"Opening range locked for {len(self.opening_ranges)} symbols — "
                f"{len(qualifying)} clear RVOL ≥ {self.config.rvol_threshold}. Bot ARMED.",
            },
        )

        # Warm the expert's view for the shortlist now, while the bot is armed
        # but no breakout has fired yet. A cold cache at signal time costs a
        # candle; this usually avoids that without changing the gate's logic.
        gate_config = await ai_advisor.config(refresh=True)
        if gate_config.gate_enabled and await ai_advisor.has_key():
            shortlist = [r.symbol for r in qualifying][: self.config.max_symbols]
            if shortlist:
                await broadcaster.publish(
                    "log",
                    {
                        "level": "INFO",
                        "message": f"[AI] Pre-fetching expert views for {len(shortlist)} shortlisted symbols…",
                    },
                )
                ai_advisor.prefetch_many(shortlist)

        await self.publish_status()

    async def on_scanner_signal(
        self,
        symbol: str,
        side: str,
        price: float,
        reason: str,
        *,
        fast_period: int | None = None,
        slow_period: int | None = None,
        forming_bar: bool = False,
    ) -> str:
        """Act on a crossover the scanner just fired. Returns what was done, so
        the scanner can record it alongside the signal instead of leaving the
        operator to infer it from the positions table.

        BUY opens a position; SELL closes one already held. A SELL on a symbol
        the bot does not hold is deliberately NOT opened as a short: the
        scanner's death cross is an exit signal for a long, and treating it as
        a short entry would double the strategy's exposure to a signal that has
        never been tested in that direction.
        """
        if not self.enabled:
            return "ignored — bot is off"
        if state.kill_switch_active or state.risk_manager.state.locked:
            return "ignored — trading is halted"

        side = side.upper()
        holding = symbol in state.paper_engine.positions

        if forming_bar:
            return "ignored — forming-bar signal; bot only trades closed candles"

        if fast_period is not None and slow_period is not None and ma_pair_is_noise(fast_period, slow_period):
            return (
                f"ignored — {fast_period}/{slow_period} MA pair is noise "
                f"(need at least 9/21). Bot will not trade this scanner config."
            )

        if side == "SELL":
            if not holding:
                return "no position to close"
            if not self.config.scanner_close_on_sell:
                return "SELL ignored — close-on-sell is off"
            return await self._close_on_ta_sell(symbol, reason)

        # ---- BUY ---------------------------------------------------------
        if self.config.strategy == "orb":
            return "ignored — ORB uses its own breakout entries"
        if holding:
            return "already holding"
        if symbol in self._symbols_traded:
            return "already traded this session — no re-entry"

        if self.config.strategy == "gainers":
            ranked = {row["symbol"] for row in self.gainers_preview()[0]}
            if symbol not in ranked:
                return "ignored — not in the top-gainer universe"

        if self._entry_window_closed():
            return f"too late — no new entries after {self._last_entry_cutoff().strftime('%H:%M')} IST"

        if self.config.adx_filter_enabled:
            blocked = self._adx_blocks(symbol)
            if blocked:
                return blocked

        from app.services.trade_ledger import get_account_summary

        cfg = self._allocation_config()
        cfg.min_gain_pct = 0.0  # the crossover IS the entry test here
        account = await get_account_summary()
        signal = self._plan_sized_entry(
            symbol=symbol,
            price=price,
            pct_from_open=0.0,
            account=account,
            cfg=cfg,
        )
        if isinstance(signal, str):
            return signal

        try:
            await place_paper_entry(
                symbol=signal.symbol,
                side="BUY",
                entry_price=signal.entry,
                stop_loss=signal.stop_loss,
                target=signal.target,
                source="BOT",
                reason=f"TA BUY — {reason}",
                quantity_override=signal.quantity,
            )
        except EntryRejected as exc:
            if state.risk_manager.state.locked:
                await self.halt_for_circuit_breaker(state.risk_manager.state.lock_reason)
            return f"rejected — {exc.reason}"

        self._symbols_traded.add(symbol)
        await broadcaster.publish(
            "log",
            {
                "level": "INFO",
                "message": (
                    f"[TA→BOT] BOUGHT {signal.quantity} {symbol} @ Rs {signal.entry} "
                    f"(Rs {signal.allocation_value:,.0f}) on BUY signal — "
                    f"SL {signal.stop_loss} / TGT {signal.target} ({reason})"
                ),
            },
        )
        return f"bought {signal.quantity} @ Rs {signal.entry}"

    def _rank_gainers_from_candles(self, top_n: int) -> list[tuple[str, float]]:
        """Rank by move from the session open using the bot's own candles.

        A fallback for when the snapshot recorder has nothing for today. The
        first candle this runner built for a symbol opened at the session open
        (the builder is reset per session), so its open is the reference the
        percentage is measured from — the same definition the recorder uses,
        derived from data already in memory.
        """
        rows: list[tuple[str, float]] = []
        for symbol in list(state.latest_quotes.keys()):
            history = self.candles.history(symbol)
            if not history:
                continue
            open_price = history[0].open
            quote = state.latest_quotes.get(symbol)
            if not quote or open_price <= 0:
                continue
            pct = (float(quote["ltp"]) - open_price) / open_price * 100.0
            if pct > 0:
                rows.append((symbol, round(pct, 2)))

        rows.sort(key=lambda r: r[1], reverse=True)
        return rows[:top_n]

    def _current_gainers(self, top_n: int) -> list[tuple[str, float]]:
        """Top-N by % from open: recorded snapshots first, then live candles."""
        from app.services.movers import ranked_movers, split_gainers_losers

        try:
            movers = ranked_movers(dt.date.today(), market_data.source.value)
            ranked = [
                (m.symbol, float(m.pct_from_open))
                for m in split_gainers_losers(movers, top=top_n)["gainers"]
            ]
            if ranked:
                return ranked
        except Exception:  # noqa: BLE001 — ranking failure falls back to candles
            pass
        return self._rank_gainers_from_candles(top_n)

    def _ta_signal(self, symbol: str):
        """Closed-bar EMA 9/21 signal, or None. Prefers 5m history; falls back
        to the bot's own completed candles so DEMO TIMING can still fire.
        """
        from app.services.candle_store import candle_store
        from app.services.indicators import OHLCV

        params = StrategyParams.trading_defaults()
        engine = StrategyEngine(params)
        bars = candle_store.get(symbol, "5m", market_data.source.value, limit=400)
        closed = bars[:-1] if len(bars) > 1 else []
        if len(closed) >= params.warmup_bars():
            return engine.evaluate(symbol, "5m", closed)

        history = self.candles.history(symbol)
        if len(history) < params.warmup_bars():
            return None
        bot_bars = [
            OHLCV(c.start_ts, c.open, c.high, c.low, c.close, c.volume) for c in history
        ]
        return engine.evaluate(symbol, f"{self.config.candle_interval_sec}s", bot_bars)

    async def _maybe_ta_exits(self) -> None:
        for symbol in list(state.paper_engine.positions):
            await self._maybe_ta_exit(symbol)

    async def _maybe_ta_exit(self, symbol: str) -> None:
        if symbol not in state.paper_engine.positions or symbol in self._exiting:
            return
        ta = self._ta_signal(symbol)
        if ta is None or ta.side != "SELL":
            return
        if self._ta_acted.get(f"exit:{symbol}") == ta.candle_ts:
            return
        why = ta.reasons[0] if ta.reasons else "EMA 9/21 death cross"
        result = await self._close_on_ta_sell(symbol, why)
        if result.startswith("closed"):
            self._ta_acted[f"exit:{symbol}"] = ta.candle_ts

    async def _close_on_ta_sell(self, symbol: str, reason: str) -> str:
        position = state.paper_engine.positions.get(symbol)
        if position is None:
            return "no position to close"
        held = (dt.datetime.utcnow() - position.opened_at).total_seconds()
        if held < self.config.scanner_min_hold_sec:
            return (
                f"SELL ignored — held {held:.0f}s, minimum "
                f"{self.config.scanner_min_hold_sec}s (let the stop/target work)"
            )
        self._exiting.add(symbol)
        try:
            result, breaker = await close_and_settle(symbol, f"TA SELL — {reason}")
            if result is None:
                return "close failed — no live quote"
            self._symbols_traded.add(symbol)
            await broadcaster.publish(
                "log",
                {
                    "level": "INFO",
                    "message": (
                        f"[TA→BOT] CLOSED {symbol} on SELL signal — "
                        f"P&L Rs {result.pnl:,.2f} ({reason})"
                    ),
                },
            )
            if breaker is not None:
                await self.halt_for_circuit_breaker(breaker.reason)
            return f"closed, P&L Rs {result.pnl:,.2f}"
        finally:
            self._exiting.discard(symbol)

    async def _evaluate_gainers(self) -> None:
        """Rank the session's strongest names, then buy only on a TA entry.

        The ranking is the universe (top 50 by move from the open). The order
        still requires a closed-bar golden cross through EMA 9/21 + ADX,
        volume, trend and RSI — the same test the scanner uses. Strength
        without a cross is watched, not bought.
        """
        from app.services.trade_ledger import get_account_summary

        cfg = GainerConfig(
            top_n=self.config.gainers_top_n,
            allocation_pct=self.config.gainers_allocation_pct,
            max_positions=self.config.gainers_max_positions,
            stop_loss_pct=self.config.gainers_stop_loss_pct,
            target_pct=self.config.gainers_target_pct,
            min_gain_pct=self.config.gainers_min_gain_pct,
        )

        ranked = self._current_gainers(cfg.top_n)
        if not ranked:
            if not self._gainers_empty_logged:
                self._gainers_empty_logged = True
                await broadcaster.publish(
                    "log",
                    {
                        "level": "WARN",
                        "message": (
                            "[GAINERS] Nothing to rank: no recorded snapshots for today, and no symbol "
                            "is above its session open yet. Staying ARMED — an entry fires as soon as "
                            "a top-gainer also prints a TA buy."
                        ),
                    },
                )
            return
        self._gainers_empty_logged = False

        if self._entry_window_closed():
            return

        account = await get_account_summary()
        opened = 0
        for symbol, pct_from_open in ranked:
            if opened >= max(0, cfg.max_positions - int(account.get("open_positions", 0))):
                break

            if symbol in state.paper_engine.positions or symbol in self._symbols_traded:
                continue

            ta = self._ta_signal(symbol)
            if ta is None or ta.side != "BUY":
                continue
            if self._ta_acted.get(symbol) == ta.candle_ts:
                continue

            quote = state.latest_quotes.get(symbol)
            if not quote:
                continue

            account = await get_account_summary()
            signal = self._plan_sized_entry(
                symbol=symbol,
                price=float(quote["ltp"]),
                pct_from_open=pct_from_open,
                account=account,
                cfg=cfg,
            )
            if isinstance(signal, str):
                continue

            why = ta.reasons[0] if ta.reasons else "EMA 9/21 golden cross"
            await broadcaster.publish(
                "log",
                {
                    "level": "INFO",
                    "message": (
                        f"GAINERS+TA SIGNAL BUY {symbol} — +{signal.pct_from_open}% from open, "
                        f"{why}, {signal.quantity} sh @ Rs {signal.entry} "
                        f"(Rs {signal.allocation_value:,.0f}) "
                        f"| SL {signal.stop_loss} (-{cfg.stop_loss_pct:g}%) "
                        f"| TGT {signal.target} (+{cfg.target_pct:g}%)"
                    ),
                },
            )

            try:
                await place_paper_entry(
                    symbol=signal.symbol,
                    side="BUY",
                    entry_price=signal.entry,
                    stop_loss=signal.stop_loss,
                    target=signal.target,
                    source="BOT",
                    reason=(
                        f"top-{cfg.top_n} gainer +{signal.pct_from_open}% from open; {why}"
                    ),
                    quantity_override=signal.quantity,
                )
                self._symbols_traded.add(symbol)
                self._ta_acted[symbol] = ta.candle_ts
            except EntryRejected as exc:
                self._symbols_traded.add(symbol)
                if state.risk_manager.state.locked:
                    await self.halt_for_circuit_breaker(state.risk_manager.state.lock_reason)
                    return
                await broadcaster.publish(
                    "log", {"level": "WARN", "message": f"[GAINERS] {symbol} skipped: {exc.reason}"}
                )
                continue

            opened += 1

    async def _evaluate_entry(self, candle: BuiltCandle) -> None:
        symbol = candle.symbol
        if self._entry_window_closed():
            return
        opening_range = self.opening_ranges.get(symbol)
        if opening_range is None:
            return
        if symbol in self._symbols_traded or symbol in state.paper_engine.positions:
            return
        if opening_range.rvol < self.config.rvol_threshold:
            return

        history = self.candles.history(symbol)
        prior = history[-2] if len(history) >= 2 else None

        if self.config.adx_filter_enabled:
            adx_candles = [Candle(c.open, c.high, c.low, c.close, c.volume) for c in history]
            adx = ORBStrategy.adx(adx_candles, self.config.adx_period)
            # None means not enough candle history yet to compute it, not
            # that the market is untrending — blocking on that would make an
            # already-quiet bot even quieter for no analytical reason. Once
            # computable, it's a hard gate: this is the whole point of adding it.
            if adx is not None and adx < self.config.adx_threshold:
                if self._gate_vetoes.get(symbol) != f"ADX{adx}":
                    self._gate_vetoes[symbol] = f"ADX{adx}"
                    await broadcaster.publish(
                        "log",
                        {
                            "level": "INFO",
                            "message": f"[ADX FILTER] {symbol} breakout ignored — ADX {adx} is below the "
                            f"{self.config.adx_threshold} trend-strength threshold. Market looks range-bound, "
                            "not trending, so the breakout is more likely noise than a real move.",
                        },
                    )
                return

        signal = self.strategy.evaluate(
            opening_range,
            Candle(candle.open, candle.high, candle.low, candle.close, candle.volume),
            Candle(prior.open, prior.high, prior.low, prior.close, prior.volume) if prior else None,
        )
        if signal is None:
            return

        from app.services.indicators import OHLCV, rsi as rsi_series

        rsi_bars = [
            OHLCV(i, c.open, c.high, c.low, c.close, c.volume) for i, c in enumerate(history)
        ]
        rsi_now = rsi_series(rsi_bars)[-1] if rsi_bars else None
        if signal.side == "BUY" and rsi_now is not None and rsi_now >= 70:
            if self._gate_vetoes.get(symbol) != f"RSI{rsi_now:.0f}":
                self._gate_vetoes[symbol] = f"RSI{rsi_now:.0f}"
                await broadcaster.publish(
                    "log",
                    {
                        "level": "INFO",
                        "message": (
                            f"[RSI FILTER] {symbol} breakout ignored — RSI {rsi_now:.0f} is already "
                            "overbought. Buying here is chasing an exhausted move."
                        ),
                    },
                )
            return

        await broadcaster.publish(
            "log",
            {
                "level": "INFO",
                "message": f"ORB SIGNAL {signal.side} {symbol} — close ₹{candle.close} "
                f"broke range [{opening_range.low}, {opening_range.high}] "
                f"| RVOL {opening_range.rvol}",
            },
        )

        # The AI expert can only veto. It never originates an entry, and a
        # pass still has to clear the risk gate below.
        gate = await ai_advisor.gate(symbol, signal.side)
        if not gate.allowed:
            if self._gate_vetoes.get(symbol) != gate.reason:
                self._gate_vetoes[symbol] = gate.reason
                await broadcaster.publish(
                    "log",
                    {
                        "level": "WARN",
                        "message": f"[AI GATE] {symbol} {signal.side} entry blocked — {gate.reason}",
                    },
                )
            if gate.view is not None:
                await ai_advisor.record_gate_outcome(
                    gate.view.analysis_id, signal.side, passed=False, reason=gate.reason
                )
            # Deliberately not marked as traded: a veto is a view on right
            # now, and it should be re-evaluated when the view refreshes.
            return

        self._gate_vetoes.pop(symbol, None)
        if gate.view is not None:
            await broadcaster.publish(
                "log", {"level": "INFO", "message": f"[AI GATE] {symbol} cleared — {gate.reason}"}
            )

        try:
            direction = "above" if signal.side == "BUY" else "below"
            edge = opening_range.high if signal.side == "BUY" else opening_range.low
            why = (
                f"ORB breakout: the {self.config.candle_interval_sec // 60 or 1}-min candle closed "
                f"₹{candle.close} — {direction} the {self.config.range_duration_sec // 60}-min opening range "
                f"[{opening_range.low}, {opening_range.high}], clearing ₹{edge}. "
                f"RVOL {opening_range.rvol} vs the {self.config.rvol_threshold} threshold."
            )
            if gate.view is not None:
                why += (
                    f" AI expert {gate.view.stance} with conviction {gate.view.conviction} "
                    f"(sentiment {gate.view.sentiment_label})."
                )

            entry = await place_paper_entry(
                symbol=signal.symbol,
                side=signal.side,
                entry_price=round(signal.entry, 2),
                stop_loss=round(signal.stop_loss, 2),
                target=round(signal.target, 2),
                source="BOT",
                reason=why,
            )
            if gate.view is not None:
                await ai_advisor.record_gate_outcome(
                    gate.view.analysis_id, signal.side, passed=True, reason=gate.reason, trade_id=entry.trade_id
                )
            self._symbols_traded.add(symbol)
        except EntryRejected as exc:
            # Risk gate said no — record it so the bot doesn't retry the same
            # signal every candle and spam the log.
            self._symbols_traded.add(symbol)
            if state.risk_manager.state.locked:
                await self.halt_for_circuit_breaker(state.risk_manager.state.lock_reason)
            else:
                await broadcaster.publish(
                    "log", {"level": "WARN", "message": f"[BOT] Entry skipped for {symbol}: {exc.reason}"}
                )

    def _allocation_config(self) -> GainerConfig:
        return GainerConfig(
            top_n=self.config.gainers_top_n,
            allocation_pct=self.config.gainers_allocation_pct,
            max_positions=self.config.gainers_max_positions,
            stop_loss_pct=self.config.gainers_stop_loss_pct,
            target_pct=self.config.gainers_target_pct,
            min_gain_pct=self.config.gainers_min_gain_pct,
        )

    def _last_entry_cutoff(self) -> dt.time:
        square = state.risk_manager.config.square_off_time_ist
        buffer = self.config.last_entry_buffer_min
        return (
            dt.datetime.combine(dt.date(2000, 1, 1), square) - dt.timedelta(minutes=buffer)
        ).time()

    def _entry_window_closed(self) -> bool:
        """Live/market sessions refuse entries in the last 45 minutes.

        Demo mode on the synthetic feed stays testable after hours — there is
        no real 15:30 for that sandbox to honour.
        """
        if self.config.session_mode != "market" and market_data.source is not DataSource.LIVE:
            return False
        from app.core.market_clock import ist_now

        return past_last_entry(
            ist_now().time(),
            state.risk_manager.config.square_off_time_ist,
            self.config.last_entry_buffer_min,
        )

    def _adx_blocks(self, symbol: str) -> str | None:
        history = self.candles.history(symbol)
        candles = [Candle(c.open, c.high, c.low, c.close, c.volume) for c in history]
        adx = ORBStrategy.adx(candles, self.config.adx_period)
        if adx is not None and adx < self.config.adx_threshold:
            return f"ADX {adx} below {self.config.adx_threshold} — no trend to follow"
        return None

    def _plan_sized_entry(
        self,
        *,
        symbol: str,
        price: float,
        pct_from_open: float,
        account: dict,
        cfg: GainerConfig,
    ):
        """Even-split allocation, then capped by the 1% risk rule.

        Returns a GainerSignal, or a reason string if nothing can be sized.
        """
        balance = float(account.get("balance", 0.0))
        deployed = float(account.get("open_exposure", 0.0))
        open_count = int(account.get("open_positions", 0))
        leverage = float(state.risk_manager.config.max_leverage)
        budget = even_slot_budget(
            balance=balance,
            leverage=leverage,
            allocation_pct=cfg.allocation_pct,
            deployed=deployed,
            max_positions=cfg.max_positions,
            open_count=open_count,
        )
        if budget <= 0:
            if open_count >= cfg.max_positions:
                return f"no slot free ({cfg.max_positions} positions open)"
            return "buying power fully deployed"
        signal = plan_entry(
            symbol=symbol,
            price=price,
            pct_from_open=pct_from_open,
            budget=budget,
            config=cfg,
        )
        if signal is None:
            return "below the minimum order size"
        risk_qty = state.risk_manager.position_size(signal.entry, signal.stop_loss)
        capped = cap_to_risk(signal, risk_qty)
        if capped is None:
            return "risk sizer produced a zero-share order"
        return capped

    async def _harden_scanner_for_trading(self) -> None:
        """Rewrite a noise scanner config before the bot will trade it.

        EMA2/EMA3 + intrabar is how today's book bought and sold the same
        name 40 seconds apart. Alerts can still use whatever the operator
        likes; the trading bot will not.
        """
        from app.services.scanner_worker import scanner_worker

        changes = await scanner_worker.harden_for_live_trading()
        if not changes:
            return
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": "[SCANNER] Config was unsafe for live trading and was reset: "
                + "; ".join(changes),
            },
        )

    async def _ensure_scanner_running(self) -> None:
        """Start the scanner so TA alerts and bot entries share one engine.

        Gainers and scanner modes both need it. The operator should not have
        to visit the Scanner page and press Start just to trade the ranking.
        """
        from app.services.scanner_worker import scanner_worker

        if scanner_worker.running:
            return
        await scanner_worker.start()
        await broadcaster.publish(
            "log",
            {
                "level": "INFO",
                "message": (
                    "[SCANNER] Started with the bot — watching the configured universe "
                    "(top 50 gainers unless you picked Core/Custom) for EMA 9/21 signals."
                ),
            },
        )

    # ----- status -----------------------------------------------------

    def _indicator_snapshot(self, symbol: str) -> dict:
        """ADX and Bollinger bandwidth for one symbol, for display alongside
        its opening range — read-only context computed fresh from candle
        history, not stored state.
        """
        history = self.candles.history(symbol)
        candles = [Candle(c.open, c.high, c.low, c.close, c.volume) for c in history]
        adx = ORBStrategy.adx(candles, self.config.adx_period)
        bb = ORBStrategy.bollinger_bandwidth(candles)
        return {
            "adx": adx,
            "adx_trending": (adx >= self.config.adx_threshold) if adx is not None else None,
            "bb_squeeze": bb["squeeze"] if bb else None,
            "bb_bandwidth_pct": bb["bandwidth_pct"] if bb else None,
        }

    def gainers_preview(self) -> tuple[list[dict], str]:
        """The ranking the strategy would act on right now, and where it came
        from. Read by the dashboard so the operator can see the candidate list
        rather than infer it from an absence of trades.
        """
        import datetime as _dt

        try:
            from app.services.movers import ranked_movers, split_gainers_losers

            movers = ranked_movers(_dt.date.today(), market_data.source.value)
            rows = [
                {"symbol": m.symbol, "pct_from_open": round(float(m.pct_from_open), 2)}
                for m in split_gainers_losers(movers, top=self.config.gainers_top_n)["gainers"]
            ]
            if rows:
                return rows, "recorded snapshots"
        except Exception:  # noqa: BLE001 — preview must never break the status call
            pass

        rows = [
            {"symbol": sym, "pct_from_open": pct}
            for sym, pct in self._rank_gainers_from_candles(self.config.gainers_top_n)
        ]
        return rows, "live candles" if rows else "nothing above its open yet"

    def snapshot(self) -> BotSnapshot:
        range_end = self._range_end()
        remaining = None
        if range_end and self.enabled:
            remaining = max(0, int((range_end - dt.datetime.now()).total_seconds()))

        gainers_rows: list[dict] = []
        gainers_src: str | None = None
        if self.config.strategy == "gainers":
            gainers_rows, gainers_src = self.gainers_preview()

        # Read the scanner's live state so the bot panel can explain why
        # nothing is happening — a stopped scanner in scanner mode means no
        # signals will ever arrive, which is invisible from here otherwise.
        scanner_running = False
        scanner_intrabar = False
        if self.config.strategy in ("scanner", "gainers"):
            try:
                from app.services.scanner_worker import scanner_worker

                scanner_running = scanner_worker.running
                scanner_intrabar = bool(getattr(scanner_worker, "_intrabar", False))
            except Exception:  # noqa: BLE001
                pass

        return BotSnapshot(
            enabled=self.enabled,
            status=self._status_for_now().value,
            config=asdict(self.config),
            range_ready=self._range_finalized,
            range_ends_in_sec=remaining,
            opening_ranges=[
                {
                    "symbol": r.symbol,
                    "high": r.high,
                    "low": r.low,
                    "rvol": r.rvol,
                    "qualifies": r.rvol >= self.config.rvol_threshold,
                    **self._indicator_snapshot(r.symbol),
                }
                for r in sorted(self.opening_ranges.values(), key=lambda r: -r.rvol)
            ],
            symbols_traded=sorted(self._symbols_traded),
            gainers_candidates=gainers_rows,
            gainers_source=gainers_src,
            scanner_running=scanner_running,
            scanner_intrabar=scanner_intrabar,
            late_start=self._late_start,
            range_window=(
                f"{self._range_start.strftime('%H:%M')}–{range_end.strftime('%H:%M')}"
                if self._range_start and range_end
                else None
            ),
        )

    async def publish_status(self) -> None:
        await broadcaster.publish("bot_status", asdict(self.snapshot()))


strategy_runner = StrategyRunner()
