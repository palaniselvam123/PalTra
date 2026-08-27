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
from dataclasses import dataclass, field, asdict
from enum import Enum

from app import state
from app.services.ai_advisor import ai_advisor
from app.services.broadcaster import broadcaster
from app.services.candle_builder import BuiltCandle, CandleBuilder
from app.services.execution import EntryRejected, place_paper_entry
from app.services.trade_ledger import close_and_settle
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
        self._session_date: dt.date | None = None
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
        await broadcaster.publish("log", {"level": "INFO", "message": message})
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

    async def _evaluate_entry(self, candle: BuiltCandle) -> None:
        symbol = candle.symbol
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

    def snapshot(self) -> BotSnapshot:
        range_end = self._range_end()
        remaining = None
        if range_end and self.enabled:
            remaining = max(0, int((range_end - dt.datetime.now()).total_seconds()))

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
