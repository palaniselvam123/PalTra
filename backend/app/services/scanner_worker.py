"""Background scanner: watches a universe, detects MA crossovers on candle
close, and dispatches WhatsApp alerts.

Deliberately **read-only with respect to money**. It never calls the execution
path and cannot open a position — it observes and notifies. That separation is
why it has its own config table rather than sharing the ORB bot's.

The candle-close rule is enforced structurally rather than by convention: the
store's last bar is always the one still forming, so the worker slices it off
and evaluates only completed bars. It also remembers the last bar timestamp it
judged per symbol/timeframe, so a signal fires once per bar even though the
loop wakes several times inside that bar.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
from dataclasses import dataclass, field

from sqlalchemy import select

from app import state
from app.core.market_clock import IST, ist_now
from app.models.database import ScannerConfig, ScannerSignal, async_session
from app.services.alert_notifier import alert_notifier, format_message
from app.services.backfill import ensure_backfilled
from app.services.broadcaster import broadcaster
from app.services.candle_store import candle_store
from app.services.market_data import DataSource, market_data
from app.services.scanner_engine import Signal, StrategyEngine, StrategyParams
from app.strategies.scanner import NIFTY50_UNIVERSE

POLL_SECONDS = 5
# Backfill is retried at most this often per symbol so a persistently failing
# symbol cannot turn the scan loop into a broker-hammering retry storm.
BACKFILL_RETRY_SECONDS = 300


@dataclass
class ScannerStatus:
    running: bool = False
    universe_size: int = 0
    scanned_symbols: int = 0
    last_scan_at: str | None = None
    last_error: str | None = None
    signals_today: int = 0
    # What the bot did with the most recent signal, so the scanner page can
    # show the outcome next to the signal that caused it.
    last_action: str = ""
    # SELL crossovers dropped because nothing was held in that symbol.
    suppressed_sells: int = 0
    notes: list[str] = field(default_factory=list)


# How many ranked gainers the GAINERS universe scans.
GAINERS_UNIVERSE_SIZE = 50


class ScannerWorker:
    def __init__(self) -> None:
        self.running = False
        self._task: asyncio.Task | None = None
        self.status = ScannerStatus()
        # (symbol, timeframe) -> last COMPLETED bar timestamp already judged
        self._evaluated: dict[tuple[str, str], int] = {}
        self._backfill_attempted: dict[tuple[str, str], dt.datetime] = {}
        self._notes: dict[str, str] = {}
        self._timeframe: str = "5m"
        self._intrabar: bool = False

    # ---- config ---------------------------------------------------------

    async def load_config(self) -> ScannerConfig:
        async with async_session() as session:
            cfg = await session.get(ScannerConfig, 1)
            if cfg is None:
                cfg = ScannerConfig(id=1)
                session.add(cfg)
                await session.commit()
                await session.refresh(cfg)
            return cfg

    async def harden_for_live_trading(self) -> list[str]:
        """If the scanner is set to a noise MA pair / forming-bar mode, rewrite
        it to something an intraday bot can actually trade.

        Alerts can use whatever the operator likes. The trading bot will not
        follow EMA2/EMA3 on a forming bar — that combination bought and sold
        the same name 40 seconds apart.
        """
        changes: list[str] = []
        async with async_session() as session:
            cfg = await session.get(ScannerConfig, 1)
            if cfg is None:
                return changes
            if cfg.fast_period < 9:
                changes.append(f"fast MA {cfg.fast_period} → 9")
                cfg.fast_period = 9
            if cfg.slow_period < 21 or cfg.slow_period <= cfg.fast_period:
                changes.append(f"slow MA {cfg.slow_period} → 21")
                cfg.slow_period = 21
            if cfg.intrabar:
                changes.append("intrabar off (closed candles only)")
                cfg.intrabar = False
            if not cfg.adx_filter:
                changes.append("ADX filter on")
                cfg.adx_filter = True
            if not cfg.volume_filter:
                changes.append("volume filter on")
                cfg.volume_filter = True
            if not getattr(cfg, "rsi_filter", False):
                changes.append("RSI overbought filter on")
                cfg.rsi_filter = True
            if not cfg.trend_filter:
                changes.append("trend filter on (close above EMA50)")
                cfg.trend_filter = True
            if (cfg.trend_period or 0) > 50:
                changes.append(f"trend EMA {cfg.trend_period} → 50")
                cfg.trend_period = 50
            if cfg.universe not in ("CUSTOM", "CORE", "GAINERS"):
                changes.append("universe → GAINERS (top 50)")
                cfg.universe = "GAINERS"
            if (cfg.cooldown_minutes or 0) < 30:
                changes.append(f"cooldown {cfg.cooldown_minutes}m → 30m")
                cfg.cooldown_minutes = 30
            if changes:
                await session.commit()
                self._intrabar = bool(cfg.intrabar)
        return changes

    def params_from(self, cfg: ScannerConfig) -> StrategyParams:
        return StrategyParams(
            fast_period=cfg.fast_period,
            fast_type=cfg.fast_type,
            slow_period=cfg.slow_period,
            slow_type=cfg.slow_type,
            signal_type=cfg.signal_type,
            trend_filter=cfg.trend_filter,
            trend_period=cfg.trend_period,
            volume_filter=cfg.volume_filter,
            volume_multiplier=cfg.volume_multiplier,
            volume_lookback=cfg.volume_lookback,
            pattern_filter=cfg.pattern_filter,
            pattern_lookback=cfg.pattern_lookback,
            adx_filter=cfg.adx_filter,
            adx_threshold=cfg.adx_threshold,
            rsi_filter=bool(getattr(cfg, "rsi_filter", True)),
            rsi_overbought=float(getattr(cfg, "rsi_overbought", 70.0) or 70.0),
        )

    def universe_for(self, cfg: ScannerConfig) -> list[str]:
        if cfg.universe == "CORE":
            return list(NIFTY50_UNIVERSE)
        if cfg.universe == "CUSTOM":
            return [s.strip().upper() for s in (cfg.custom_symbols or "").split(",") if s.strip()]
        if cfg.universe == "GAINERS":
            # Only the strongest names of the session. Scanning the whole feed
            # produced crossover alerts on stocks that were going nowhere; the
            # ranking is the filter that makes a crossover worth acting on.
            from app.services.strategy_runner import strategy_runner

            ranked = strategy_runner.gainers_preview()[0]
            symbols = [r["symbol"] for r in ranked[:GAINERS_UNIVERSE_SIZE]]
            # Fall back to the streaming set rather than scanning nothing at
            # all before the ranking has data (e.g. right after a restart).
            return symbols or list(market_data.symbols)

        # WATCHLIST: everything currently streaming, which is the only set with
        # live prices — scanning a symbol with no feed would never fire.
        return list(market_data.symbols)

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> ScannerStatus:
        if self.running:
            return self.status
        self.running = True
        self.status = ScannerStatus(running=True)
        alert_notifier.reset_session()
        self._evaluated.clear()
        self._backfill_attempted.clear()
        self._task = asyncio.create_task(self._loop())
        await broadcaster.publish(
            "log", {"level": "INFO", "message": "[SCANNER] Started — watching for MA crossovers on candle close."}
        )
        await self.publish_status()
        return self.status

    async def stop(self) -> ScannerStatus:
        self.running = False
        if self._task:
            self._task.cancel()
            self._task = None
        self.status.running = False
        await broadcaster.publish("log", {"level": "INFO", "message": "[SCANNER] Stopped."})
        await self.publish_status()
        return self.status

    async def _loop(self) -> None:
        while self.running:
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # One bad cycle must never kill the worker; the feed itself
                # reconnects independently, so the next pass usually recovers.
                self.status.last_error = f"{type(exc).__name__}: {exc}"
                await broadcaster.publish(
                    "log", {"level": "ERROR", "message": f"[SCANNER] Scan cycle failed: {exc}"}
                )
            await asyncio.sleep(POLL_SECONDS)

    # ---- scanning -------------------------------------------------------

    async def scan_once(self) -> list[Signal]:
        cfg = await self.load_config()
        params = self.params_from(cfg)
        engine = StrategyEngine(params)
        symbols = self.universe_for(cfg)
        timeframe = cfg.timeframe
        self._timeframe = timeframe
        self._intrabar = bool(cfg.intrabar)
        source = market_data.source.value

        self.status.universe_size = len(symbols)
        scanned = 0
        fired: list[Signal] = []

        # On live data outside market hours every quote is frozen at last
        # close, so the candles being built have zero range. Frozen prices drag
        # the fast and slow averages together until they cross by a few paise —
        # manufacturing "signals" out of a dead feed. The app already refuses to
        # place entries in this state; refusing to invent signals is the same
        # rule applied one step earlier.
        health = market_data.health()
        if market_data.source is DataSource.LIVE and not health.market_open:
            self.status.last_scan_at = ist_now().isoformat()
            self.status.notes = [
                f"Market is {health.session} — live quotes are frozen at last close, so no new candles are "
                "forming. Scanning is paused until 09:15 IST rather than reading crossovers off a flat feed."
            ]
            return fired
        if market_data.source is DataSource.LIVE and health.stale:
            self.status.last_scan_at = ist_now().isoformat()
            self.status.notes = ["Live feed looks stale — scanning paused until prices move again."]
            return fired

        priced_out = 0
        suppressed_sells = 0
        for symbol in symbols:
            try:
                # Price band first: it is a cheap lookup, and skipping here
                # avoids backfilling history for a symbol that can never
                # produce an actionable alert.
                if cfg.min_price > 0 or cfg.max_price > 0:
                    quote = state.latest_quotes.get(symbol)
                    ltp = quote["ltp"] if quote else None
                    if ltp is None:
                        continue
                    if cfg.min_price > 0 and ltp < cfg.min_price:
                        priced_out += 1
                        continue
                    if cfg.max_price > 0 and ltp > cfg.max_price:
                        priced_out += 1
                        continue

                await self._ensure_history(symbol, timeframe)
                bars = candle_store.get(symbol, timeframe, source, limit=600)
                if cfg.intrabar:
                    # Act on the forming bar. This is what makes the scanner
                    # fire the moment a cross happens instead of at the next
                    # bar close — at the cost that a signal can appear and then
                    # vanish if price crosses back before the bar completes.
                    closed = bars
                else:
                    closed = bars[:-1]
                if len(closed) < params.warmup_bars():
                    continue

                scanned += 1
                latest_ts = closed[-1].ts
                key = (symbol, timeframe)
                # In intrabar mode the forming bar must be re-judged on every
                # pass, so the "already seen this bar" guard is skipped.
                if not cfg.intrabar:
                    if self._evaluated.get(key) == latest_ts:
                        continue  # this bar has already been judged
                    self._evaluated[key] = latest_ts

                signal = engine.evaluate(symbol, timeframe, closed)
                if signal is not None:
                    # A SELL is an exit signal. Raising one on a stock that was
                    # never bought is noise: there is nothing to sell, and the
                    # bot cannot act on it. Suppressed at source so it does not
                    # reach the log, the alert channel, or the signal table.
                    if signal.side == "SELL" and signal.symbol not in state.paper_engine.positions:
                        suppressed_sells += 1
                        continue
                    await self._handle_signal(signal, cfg, source)
                    fired.append(signal)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                # Isolate per symbol: a single malformed series must not stop
                # the rest of the universe from being scanned.
                self._notes[symbol] = f"{type(exc).__name__}: {exc}"

        self.status.scanned_symbols = scanned
        self.status.suppressed_sells = suppressed_sells
        self.status.last_scan_at = ist_now().isoformat()
        notes = sorted({v for v in self._notes.values()})[:4]
        if priced_out:
            band = []
            if cfg.min_price > 0:
                band.append(f"below ₹{cfg.min_price:,.0f}")
            if cfg.max_price > 0:
                band.append(f"above ₹{cfg.max_price:,.0f}")
            notes.insert(0, f"{priced_out} symbol(s) skipped — priced {' or '.join(band)}.")
        self.status.notes = notes
        return fired

    async def _ensure_history(self, symbol: str, timeframe: str) -> None:
        key = (symbol, timeframe)
        last = self._backfill_attempted.get(key)
        now = ist_now()
        if last is not None and (now - last).total_seconds() < BACKFILL_RETRY_SECONDS:
            return
        self._backfill_attempted[key] = now
        note = await ensure_backfilled(symbol, timeframe)
        if note:
            self._notes[symbol] = note
        else:
            self._notes.pop(symbol, None)

    # ---- signal handling ------------------------------------------------

    async def _handle_signal(self, signal: Signal, cfg: ScannerConfig, source: str) -> None:
        allowed, skip_reason = alert_notifier.should_send(
            signal.symbol,
            signal.timeframe,
            signal.side,
            cooldown_minutes=cfg.cooldown_minutes,
            once_per_session=cfg.once_per_session,
        )

        row = ScannerSignal(
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.side,
            price=signal.price,
            fast_label=signal.fast_label,
            slow_label=signal.slow_label,
            fast_value=signal.fast_value,
            slow_value=signal.slow_value,
            volume_ratio=signal.volume_ratio,
            pattern=signal.pattern,
            candle_pattern=signal.candle_pattern,
            candle_desc=signal.candle_desc,
            candle_open=signal.candle_ohlc[0] if signal.candle_ohlc else 0.0,
            candle_high=signal.candle_ohlc[1] if signal.candle_ohlc else 0.0,
            candle_low=signal.candle_ohlc[2] if signal.candle_ohlc else 0.0,
            candle_close=signal.candle_ohlc[3] if signal.candle_ohlc else 0.0,
            plain_english=json.dumps(signal.plain_english),
            reasons=json.dumps(signal.reasons),
            candle_ts=signal.candle_ts,
            alert_status="PENDING",
            feed_source=source,
        )

        if not allowed:
            row.alert_status = "SKIPPED"
            row.alert_error = skip_reason
        else:
            message = format_message(
                side=signal.side,
                symbol=signal.symbol,
                timeframe=signal.timeframe,
                price=signal.price,
                fast_label=signal.fast_label,
                slow_label=signal.slow_label,
                when=ist_now(),
                extra_reasons=signal.reasons[1:],
            )
            result = await alert_notifier.send(message)
            row.alert_provider = result.provider
            if result.ok:
                row.alert_status = "SENT"
                alert_notifier.mark_sent(signal.symbol, signal.timeframe, signal.side)
            elif result.skipped_reason:
                row.alert_status = "SKIPPED"
                row.alert_error = result.skipped_reason
            else:
                row.alert_status = "FAILED"
                row.alert_error = result.error

        # Route the signal to the bot. The scanner remains an observer: it
        # hands the signal over and records what came back, but it never sizes
        # or places anything itself — that stays behind place_paper_entry and
        # the risk gate, same as every other entry path.
        action = "not traded — bot is off"
        try:
            from app.services.strategy_runner import strategy_runner

            action = await strategy_runner.on_scanner_signal(
                symbol=signal.symbol,
                side=signal.side,
                price=signal.price,
                reason=signal.reasons[0] if signal.reasons else f"{signal.side} crossover",
                fast_period=cfg.fast_period,
                slow_period=cfg.slow_period,
                forming_bar=bool(cfg.intrabar),
            )
        except Exception as exc:  # noqa: BLE001 — a trade failure must not stop scanning
            action = f"error — {exc}"
            await broadcaster.publish(
                "log",
                {"level": "ERROR", "message": f"[SCANNER→BOT] {signal.symbol} could not be traded: {exc}"},
            )

        self.status.last_action = f"{signal.side} {signal.symbol}: {action}"

        async with async_session() as session:
            session.add(row)
            await session.commit()

        self.status.signals_today += 1
        level = "INFO" if row.alert_status == "SENT" else "WARN"
        await broadcaster.publish(
            "log",
            {
                "level": level,
                "message": f"[SCANNER] {signal.side} {signal.symbol} {signal.timeframe} @ ₹{signal.price} — "
                f"{signal.reasons[0]}. Alert {row.alert_status}"
                + (f" ({row.alert_error})" if row.alert_error else "")
                + f" · BOT: {action}",
            },
        )
        await broadcaster.publish(
            "scanner_signal",
            {
                "symbol": signal.symbol,
                "timeframe": signal.timeframe,
                "side": signal.side,
                "price": signal.price,
                "reasons": signal.reasons,
                "alert_status": row.alert_status,
                "at": ist_now().isoformat(),
            },
        )

    # ---- status ---------------------------------------------------------

    async def recent_signals(self, limit: int = 50) -> list[dict]:
        async with async_session() as session:
            rows = (
                await session.execute(select(ScannerSignal).order_by(ScannerSignal.id.desc()).limit(limit))
            ).scalars().all()
        return [
            {
                "id": r.id,
                "symbol": r.symbol,
                "timeframe": r.timeframe,
                "side": r.side,
                "price": r.price,
                "fast_label": r.fast_label,
                "slow_label": r.slow_label,
                "fast_value": r.fast_value,
                "slow_value": r.slow_value,
                "volume_ratio": r.volume_ratio,
                "pattern": r.pattern,
                "candle_pattern": r.candle_pattern,
                "candle_desc": r.candle_desc,
                "candle_ohlc": [r.candle_open, r.candle_high, r.candle_low, r.candle_close]
                if r.candle_high > r.candle_low
                else None,
                "plain_english": json.loads(r.plain_english or "[]"),
                "reasons": json.loads(r.reasons or "[]"),
                "alert_status": r.alert_status,
                "alert_error": r.alert_error,
                "alert_provider": r.alert_provider,
                "feed_source": r.feed_source,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]

    def next_bar_close(self) -> str | None:
        """When the current bar on the configured timeframe finishes.

        Without this, a quiet scanner is indistinguishable from a broken one:
        on a 15m timeframe there are only four evaluation opportunities an
        hour, so "nothing since 2:35" is usually just "the next bar has not
        closed yet".
        """
        from app.services.candle_store import INTERVALS, bucket_start

        seconds = INTERVALS.get(self._timeframe or "5m")
        if not seconds:
            return None
        now = int(dt.datetime.now(dt.timezone.utc).timestamp())
        nxt = bucket_start(now, self._timeframe) + seconds
        return dt.datetime.fromtimestamp(nxt, tz=dt.timezone.utc).astimezone(IST).isoformat()

    def snapshot(self) -> dict:
        return {
            "running": self.running,
            "timeframe": self._timeframe,
            "next_bar_close": self.next_bar_close(),
            "universe_size": self.status.universe_size,
            "scanned_symbols": self.status.scanned_symbols,
            "last_scan_at": self.status.last_scan_at,
            "last_error": self.status.last_error,
            "signals_today": self.status.signals_today,
            "notes": self.status.notes,
            "feed_source": market_data.source.value,
            # What the bot did with the last signal, so the scanner page shows
            # the consequence rather than only the observation.
            "last_action": self.status.last_action,
            "suppressed_sells": self.status.suppressed_sells,
            "bot_trading": _bot_is_trading_scanner_signals(),
        }

    async def publish_status(self) -> None:
        await broadcaster.publish("scanner_status", self.snapshot())


def _bot_is_trading_scanner_signals() -> bool:
    """True when the bot is enabled AND in scanner mode — i.e. these signals
    will actually be traded rather than only recorded."""
    try:
        from app.services.strategy_runner import strategy_runner

        return strategy_runner.enabled and strategy_runner.config.strategy == "scanner"
    except Exception:  # noqa: BLE001
        return False


scanner_worker = ScannerWorker()
