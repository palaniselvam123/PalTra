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
"""
from __future__ import annotations

import asyncio
import datetime as dt
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from charges import calculate_charges, legs_for
from database import session_factory
from groww_client import IN_FLIGHT, TERMINAL_CANCELLED, TERMINAL_FILLED, GrowwClient, market_is_open
from indicators import closed_candle_cross, enrich, round_to_nse_tick
from models import BotConfig, TradeLog

IST = ZoneInfo("Asia/Kolkata")


class OrderBusy(Exception):
    pass


class SlCancelFailed(Exception):
    pass


class ForceRefused(Exception):
    """A manual force-order was refused before a broker request."""


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


def _ist_now() -> dt.datetime:
    return dt.datetime.now(IST)


def _parse_hhmm(value: str) -> dt.time:
    hh, mm = (value or "15:15").split(":")
    return dt.time(int(hh), int(mm))


MAX_TRADE_SYMBOLS = 4


def trade_names(cfg: BotConfig) -> list[str]:
    """Stocks the bot may order. The chart symbol is not implied."""
    raw = getattr(cfg, "trade_symbols", None) or ""
    names: list[str] = []
    for part in str(raw).upper().replace(" ", "").split(","):
        if part and part.isalnum() and part not in names:
            names.append(part)
    return names[:MAX_TRADE_SYMBOLS]


def _cfg_for(cfg: BotConfig, symbol: str) -> BotConfig:
    data = {col.name: getattr(cfg, col.name) for col in BotConfig.__table__.columns}
    data["symbol"] = symbol
    return BotConfig(**data)


class StrategyEngine:
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
        self._minutes: dict[str, str] = {}
        self._signals: dict[str, str] = {}
        # symbol -> timestamp of the closed bar already on the tape when the
        # bot was started or the stock was armed. None means the first bar
        # we see is that bar. A cross on it must not trade.
        self._skip_cross_until: dict[str, int | None] = {}
        # Heads-up keys already sent, so a near cross does not message every minute.
        self._warned: set[tuple] = set()
        self.ltp = 0.0
        self.sma9 = None
        self.sma21 = None
        self.atr14 = None
        self.adx14 = None
        self.data_source = "SIMULATOR"
        self.last_error = ""
        self.last_signal = ""
        self.candles = pd.DataFrame()
        self._stop = False
        self._sleep = asyncio.sleep
        self.realized_net = 0.0
        self.trades_today = 0
        self._session_date = _ist_now().date().isoformat()
        self._quote_symbol = ""
        self._cfg_cache = None

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
            rows = db.query(TradeLog).filter(TradeLog.exit_time.is_(None)).all()
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
                )
                for row in rows
            ]
        for symbol, direction, qty, entry, cross, atr, sl, when, trade_id, mode in pending:
            if not symbol or symbol in self.positions:
                continue
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
                entry_time=when or _ist_now(),
                trade_id=trade_id,
                mode=mode,
            )

    def stop(self) -> None:
        self._stop = True

    def load_config(self) -> BotConfig:
        with session_factory()() as db:
            row = db.get(BotConfig, 1)
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
            self.trades_today = 0
            self._warned.clear()
            if self.status in ("DAY_COMPLETED", "HALTED"):
                self.status = "STOPPED"
                self.halt_reason = ""

    async def run(self) -> None:
        """1-second loop. Candle logic fires once at second == 1 of each minute."""
        while not self._stop:
            try:
                await self.tick(_ist_now())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            # A stopped book after the close does not need two quotes a second.
            # That loop was keeping the only CPU busy while the desk waited.
            pause = 5.0 if (not market_is_open() and self.status != "RUNNING") else 0.5
            await self._sleep(pause)

    async def tick(self, now: dt.datetime) -> None:
        cfg = self.load_config()
        self._roll_session(now)
        self.broker.set_mode(cfg.trading_mode)
        view = (cfg.symbol or "").upper()
        armed = trade_names(cfg)
        # The chart can be a stock the bot is not ordering. Open books stay watched.
        watch = list(dict.fromkeys([*self.positions.keys(), *armed, view]))[: MAX_TRADE_SYMBOLS + 1]
        self._focus = view
        await self._settle_exchange_flat(cfg, armed)
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
            self._frames[symbol] = frame
            self._ltps[symbol] = float(ltp)
            if symbol != view:
                continue
            self.last_error = ""
            self.ltp = float(ltp)
            self.data_source = source
            enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period) if not frame.empty else frame
            self.candles = enriched
            if not enriched.empty and len(enriched) >= 2:
                # Display values from the last CLOSED bar so the UI does not
                # repaint SMA/ATR with the forming tick.
                closed = enriched.iloc[-2]
                self.sma9 = _finite(closed.get("sma_9"))
                self.sma21 = _finite(closed.get("sma_21"))
                self.atr14 = _finite(closed.get("atr_14"))
                self.adx14 = _finite(closed.get("adx_14"))

        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]

        if self.status != "RUNNING":
            return

        if self._loss_breached(cfg):
            await self._stop_for_loss(cfg)
            return

        for symbol in list(self.positions):
            self._focus = symbol
            self.ltp = self._ltps.get(symbol, 0.0)
            await self._watch_stop(_cfg_for(cfg, symbol))

        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]
        if self.status != "RUNNING":
            return

        minute_key = now.strftime("%Y-%m-%d %H:%M")
        for symbol in armed:
            if self._minutes.get(symbol) == minute_key:
                continue
            frame = self._frames.get(symbol)
            if frame is None or getattr(frame, "empty", True) or len(frame) < 3:
                continue
            enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
            self._focus = symbol
            self.ltp = self._ltps.get(symbol, self.ltp)
            await self.on_minute(now, _cfg_for(cfg, symbol), enriched)
            self._minutes[symbol] = minute_key
            if self.status != "RUNNING":
                break
        self._focus = view
        if view in self._ltps:
            self.ltp = self._ltps[view]

    async def on_minute(self, now: dt.datetime, cfg: BotConfig, frame: pd.DataFrame) -> None:
        if self.status != "RUNNING":
            return
        if self._past_square_off(now, cfg):
            await self._square_off("EOD_SQUARE_OFF")
            self.status = "DAY_COMPLETED"
            self.halt_reason = f"Auto square-off at {cfg.square_off_time} IST"
            return

        # A real last-close tape must not open a position after the bell.
        # The simulator still demonstrates signals when no NSE quote exists.
        if not market_is_open(now) and self.data_source != "SIMULATOR":
            self.last_signal = "market closed — showing the last NSE price"
            return

        # Opening-auction buffer applies whenever we are inside a real session.
        if market_is_open(now) and now.time() < dt.time(9, 20):
            self.last_signal = "skipped — opening auction buffer (09:15–09:20)"
            return

        if frame is None or frame.empty:
            return
        symbol = (cfg.symbol or "").upper()
        self._focus = symbol
        self._warn_upcoming(symbol, frame, cfg, now)
        cross = closed_candle_cross(frame)
        # A cross that was already printed when the bot started, or when this
        # stock was armed, is skipped. The next cross on a newer closed bar
        # is the one that may trade. Being flat does not enter early.
        if self._cross_is_stale(symbol, frame):
            text = f"{symbol} waiting for the next MA cross"
            self._signals[symbol] = text
            self.last_signal = text
            return
        signal = cross
        if signal is None:
            text = (
                f"{symbol} holding"
                if symbol in self.positions
                else f"{symbol} flat — waiting for an SMA cross"
            )
            self._signals[symbol] = text
            self.last_signal = text
            return
        await self.apply_signal(signal, frame, cfg, now)

    async def apply_signal(self, signal: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Stop-and-reverse on a closed-candle cross. Returns a short status."""
        self._focus = (cfg.symbol or "").upper()
        curr = frame.iloc[-2]
        atr = _finite(curr.get("atr_14"))
        if atr is None or atr <= 0:
            return "skipped — ATR not ready"
        cross_price = round_to_nse_tick(float(curr["close"]))
        adx = _finite(curr.get("adx_14"))
        adx_blocks_entry = bool(cfg.use_adx_filter) and (adx is None or adx < float(cfg.adx_threshold))

        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                self.last_signal = "blocked — order PENDING/TRANSIT"
                return self.last_signal
            self.inflight = "PENDING"
            try:
                self.inflight = "TRANSIT"
                result = await self._apply_locked(
                    signal=signal,
                    cross_price=cross_price,
                    atr=atr,
                    adx_blocks_entry=adx_blocks_entry,
                    cfg=cfg,
                    now=now,
                )
                self.last_signal = result
                self._signals[self._focus] = result
                return result
            except SlCancelFailed as exc:
                self.last_error = str(exc)
                self.last_signal = f"blocked — {exc}"
                self._signals[self._focus] = self.last_signal
                return self.last_signal
            finally:
                self.inflight = None

    async def _apply_locked(
        self,
        *,
        signal: str,
        cross_price: float,
        atr: float,
        adx_blocks_entry: bool,
        cfg: BotConfig,
        now: dt.datetime,
    ) -> str:
        want = "LONG" if signal == "BULLISH" else "SHORT"
        pos = self.position

        if pos is not None and pos.direction == want:
            return f"already {want}"

        if pos is not None and pos.direction != want:
            # Opposite cross: cancel SL, verify, flatten, then maybe reverse.
            await self._cancel_sl_verified(pos)
            await self._close_position(pos, cross_price, "MA_CROSS", now, cfg)
            self.position = None
            if adx_blocks_entry:
                return f"closed on {signal} — ADX filter blocked the reverse"
            if self.trades_today >= int(cfg.max_trades_per_day):
                self._cap_the_day(f"max_trades_per_day ({cfg.max_trades_per_day}) reached")
                return "closed on cross — trade cap locks the day"
            await self._open(want, cross_price, atr, cfg, now)
            return f"reversed to {want}"

        # FLAT
        if adx_blocks_entry:
            return f"{signal} ignored — ADX below {cfg.adx_threshold}"
        if self.trades_today >= int(cfg.max_trades_per_day):
            self._cap_the_day(f"max_trades_per_day ({cfg.max_trades_per_day}) reached")
            return "entry blocked — trade cap"
        await self._open(want, cross_price, atr, cfg, now)
        return f"opened {want}"

    def hold_for_next_cross(self, symbols: list[str]) -> None:
        """Remember the closed bar already on the tape. Do not trade that cross."""
        for symbol in symbols:
            name = (symbol or "").upper()
            if not name:
                continue
            self._skip_cross_until[name] = _closed_bar_ts(self._frames.get(name))

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

    async def force_order(self, symbol: str) -> str:
        """Buy or sell from the live SMA side, then leave the bot running.

        This does not wait for a cross and does not require the candle, or
        the session, to be closed. A cross already on the tape still cannot
        fire an extra order on this same bar.
        """
        if self.status == "HALTED":
            raise ForceRefused(self.halt_reason or "Halted for the day")
        if self.status == "DAY_COMPLETED":
            raise ForceRefused(self.halt_reason or "Session already squared off")
        name = (symbol or "").upper().strip()
        if not name or not name.isalnum():
            raise ForceRefused("Choose a stock on the chart first")
        if name in self.positions:
            direction = self.positions[name].direction
            raise ForceRefused(f"{name} is already {direction}. Force does not add a second order.")
        cfg = self.load_config()
        if self.status != "RUNNING":
            self.status = "RUNNING"
            self.halt_reason = ""
            # Other armed stocks keep waiting for a cross that prints after this start.
            self.hold_for_next_cross([item for item in trade_names(cfg) if item != name])
        frame = self._frames.get(name)
        if frame is None or getattr(frame, "empty", True):
            try:
                ltp, frame, source = await self.broker.refresh(name)
            except Exception as exc:  # noqa: BLE001
                raise ForceRefused(str(exc)) from exc
            if frame is None or getattr(frame, "empty", True):
                raise ForceRefused(f"{name} has no candles yet")
            self._frames[name] = frame
            self._ltps[name] = float(ltp)
            if name == (cfg.symbol or "").upper():
                self.ltp = float(ltp)
                self.last_error = ""
                self.data_source = source
        enriched = enrich(frame, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        side = _live_ma_side(enriched)
        if side is None:
            raise ForceRefused(f"{name} has no SMA yet")
        price = float(self._ltps.get(name) or enriched.iloc[-1]["close"] or 0)
        if price <= 0:
            raise ForceRefused(f"{name} has no price")
        atr = _latest_atr(enriched)
        if atr is None:
            raise ForceRefused(f"{name} ATR is not ready")
        # The bar we just looked at stays ignored, so this manual fill is not
        # reversed by a cross that was already printed.
        self.hold_for_next_cross([name])
        self._focus = name
        self.ltp = price
        now = _ist_now()
        async with self.lock:
            if self.inflight in IN_FLIGHT or self.inflight in ("PENDING", "TRANSIT"):
                raise ForceRefused("blocked — order PENDING/TRANSIT")
            self.inflight = "TRANSIT"
            try:
                result = await self._apply_locked(
                    signal=side,
                    cross_price=price,
                    atr=atr,
                    adx_blocks_entry=False,
                    cfg=_cfg_for(cfg, name),
                    now=now,
                )
            except SlCancelFailed as exc:
                self.last_error = str(exc)
                raise ForceRefused(str(exc)) from exc
            finally:
                self.inflight = None
        self.last_signal = result
        self._signals[name] = result
        return result

    async def _open(self, direction: str, cross_price: float, atr: float, cfg: BotConfig, now: dt.datetime) -> None:
        side = "BUY" if direction == "LONG" else "SELL"
        ack = await self.broker.place_entry(cfg.symbol, side, int(cfg.qty), cross_price)
        if ack.status in ("REJECTED", "FAILED"):
            raise SlCancelFailed(f"Entry rejected: {ack.message or ack.status}")
        fill = round_to_nse_tick(ack.fill_price or cross_price)
        sl = self._sl_price(direction, fill, atr, float(cfg.atr_multiplier))
        sl_side = "SELL" if direction == "LONG" else "BUY"
        sl_id = ""
        if bool(getattr(cfg, "use_stop", True)):
            sl_ack = await self.broker.place_sl(cfg.symbol, sl_side, int(cfg.qty), sl)
            sl_id = sl_ack.order_id
        trade_id = self._insert_open_trade(
            cfg=cfg,
            direction=direction,
            fill=fill,
            cross_price=cross_price,
            atr=atr,
            sl=sl,
            now=now,
        )
        self.trades_today += 1
        _schedule_whatsapp(
            fill_alert(
                mode=(cfg.trading_mode or "PAPER").upper(),
                direction=direction,
                symbol=cfg.symbol,
                qty=int(cfg.qty),
                fill=fill,
                stop=sl,
                when=now,
            )
        )
        self.position = OpenPosition(
            direction=direction,
            qty=int(cfg.qty),
            entry_price=fill,
            ma_cross_price=cross_price,
            atr_at_entry=atr,
            sl_trigger=sl,
            sl_order_id=sl_id,
            entry_order_id=ack.order_id,
            entry_time=now,
            trade_id=trade_id,
            mode=cfg.trading_mode,
        )

    def _sl_price(self, direction: str, entry: float, atr: float, mult: float) -> float:
        if direction == "LONG":
            return round_to_nse_tick(entry - mult * atr)
        return round_to_nse_tick(entry + mult * atr)

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
                rows = (
                    db.query(TradeLog)
                    .filter(TradeLog.exit_time.is_(None), TradeLog.symbol == symbol)
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
            for trade_id, px, direction, entry, qty in pending:
                buy, sell = legs_for(direction, entry, px)
                costs = calculate_charges(buy, sell, qty)
                self.realized_net += costs["net_pnl"]
                self._finalize_trade(trade_id, px, "ATR_SL_HIT", _ist_now(), costs)
                self.last_signal = "Exchange stop already filled — flat"

    async def _watch_stop(self, cfg: BotConfig) -> None:
        pos = self.position
        if pos is None or self.status != "RUNNING":
            return
        # No exchange stop and no resting order: nothing to watch. Square-off
        # and an opposite cross still close the position.
        if not bool(getattr(cfg, "use_stop", True)) and not pos.sl_order_id:
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
            fill_price = pos.sl_trigger or self.ltp
        elif (cfg.trading_mode or "").upper() == "LIVE" and pos.sl_order_id:
            status = (await self.broker.get_order_status(pos.sl_order_id)).upper()
            if status in TERMINAL_FILLED:
                hit = True
                # Exchange fill price if the adapter stored one; else the trigger.
                fill_price = pos.sl_trigger
            elif status not in IN_FLIGHT:
                # Groww reports a filled stop as EXECUTED. If that word was
                # missed, a flat MIS book is the same fact: the shares are gone.
                opened = pos.entry_time
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=IST)
                age = (_ist_now() - opened).total_seconds()
                if age >= 20 and await self._groww_net(cfg.symbol) == 0:
                    hit = True
                    fill_price = pos.sl_trigger or self.ltp
        elif bool(getattr(cfg, "use_stop", True)):
            if pos.direction == "LONG" and self.ltp <= pos.sl_trigger:
                hit = True
                fill_price = self.ltp
            elif pos.direction == "SHORT" and self.ltp >= pos.sl_trigger:
                hit = True
                fill_price = self.ltp
        if not hit:
            return
        async with self.lock:
            if self.position is None or self.inflight:
                return
            self.inflight = "TRANSIT"
            try:
                # SL already fired (or paper touch). Do not place a reverse.
                self.position.sl_order_id = ""
                await self._close_position(self.position, fill_price, "ATR_SL_HIT", _ist_now(), cfg)
                self.position = None
                self.last_signal = "ATR stop hit — flat"
            finally:
                self.inflight = None
        if self._loss_breached(cfg):
            await self._stop_for_loss(cfg)

    async def _square_off(self, reason: str) -> None:
        async with self.lock:
            if self.inflight:
                return
            self.inflight = "TRANSIT"
            try:
                cfg = self.load_config()
                for symbol in list(self.positions):
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
                    px = self._ltps.get(symbol) or self.position.entry_price
                    await self._close_position(
                        self.position, px, reason, _ist_now(), _cfg_for(cfg, symbol)
                    )
                    self.position = None
            finally:
                self.inflight = None

    async def kill(self, reason: str) -> None:
        self.halt_reason = reason
        self.status = "HALTED"
        await self._square_off("KILL_SWITCH")

    async def _close_position(
        self,
        pos: OpenPosition,
        exit_price: float,
        reason: str,
        now: dt.datetime,
        cfg: BotConfig,
    ) -> None:
        exit_side = "SELL" if pos.direction == "LONG" else "BUY"
        px = round_to_nse_tick(float(exit_price))
        if reason != "ATR_SL_HIT" or (cfg.trading_mode or "").upper() == "PAPER":
            # LIVE ATR hits are filled by the exchange SL; don't send a second exit.
            if not ((cfg.trading_mode or "").upper() == "LIVE" and reason == "ATR_SL_HIT"):
                ack = await self.broker.place_exit(cfg.symbol, exit_side, pos.qty, px)
                if ack.fill_price:
                    px = round_to_nse_tick(ack.fill_price)
        buy, sell = legs_for(pos.direction, pos.entry_price, px)
        costs = calculate_charges(buy, sell, pos.qty)
        self.realized_net += costs["net_pnl"]
        self._finalize_trade(pos.trade_id, px, reason, now, costs)

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
    ) -> int:
        with session_factory()() as db:
            row = TradeLog(
                date=now.date().isoformat(),
                symbol=cfg.symbol,
                direction=direction,
                qty=int(cfg.qty),
                entry_time=now.replace(tzinfo=None),
                entry_price=fill,
                ma_cross_price=cross_price,
                atr_at_entry=atr,
                sl_trigger_price=sl,
                mode=(cfg.trading_mode or "PAPER").upper(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return int(row.id)

    def _finalize_trade(self, trade_id: int, exit_price: float, reason: str, now: dt.datetime, costs: dict) -> None:
        with session_factory()() as db:
            row = db.get(TradeLog, trade_id)
            if row is None:
                return
            row.exit_time = now.replace(tzinfo=None)
            row.exit_price = exit_price
            row.exit_reason = reason
            row.gross_pnl = costs["gross_pnl"]
            row.brokerage_and_taxes = costs["total_charges"]
            row.net_pnl = costs["net_pnl"]
            symbol = row.symbol
            direction = row.direction
            db.commit()
        self._trades_cache = None
        _schedule_whatsapp(
            close_alert(
                direction=direction,
                symbol=symbol,
                exit_price=exit_price,
                reason=reason,
                gross=float(costs.get("gross_pnl") or 0),
                net=float(costs.get("net_pnl") or 0),
                when=now,
            )
        )

    def _day_open_and_change(self) -> tuple[float | None, float | None]:
        """Percent versus the previous session's last close.

        A one-bar stub whose open equals the last trade is not a flat day.
        That happens when candle history has not arrived yet.
        """
        frame = self.candles
        if frame is None or getattr(frame, "empty", True) or self.ltp <= 0:
            return None, None
        today = _ist_now().date()
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
        """Telegram before an entry, and before a close. The order itself is a separate message."""
        pos = self.positions.get(symbol)
        mode = (cfg.trading_mode or "PAPER").upper()
        side, minutes, gap_pct = minutes_until_cross(frame)
        if side is None:
            self._gate_warning((symbol, "cross", "BULLISH"), None, "")
            self._gate_warning((symbol, "cross", "BEARISH"), None, "")
        else:
            other = "BEARISH" if side == "BULLISH" else "BULLISH"
            self._gate_warning((symbol, "cross", other), None, "")
            closes = pos is not None and (
                (pos.direction == "LONG" and side == "BEARISH")
                or (pos.direction == "SHORT" and side == "BULLISH")
            )
            if pos is None:
                text = upcoming_entry_alert(
                    mode=mode, symbol=symbol, side=side, minutes=minutes or 0, gap_pct=gap_pct or 0
                )
            elif closes:
                text = upcoming_close_alert(
                    mode=mode,
                    symbol=symbol,
                    position=pos.direction,
                    side=side,
                    minutes=minutes or 0,
                    gap_pct=gap_pct or 0,
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
        stop_in = minutes_until_stop(pos.direction, ltp, float(pos.sl_trigger), atr)
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
            _schedule_whatsapp(message)
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
            return
        await self.kill(reason)

    def _practice_off_session(self, cfg: BotConfig | None = None) -> bool:
        """Paper tape outside the cash session. It must not lock the live morning."""
        if cfg is None:
            cfg = self._cfg_cache
        mode = (cfg.trading_mode if cfg is not None else "PAPER") or "PAPER"
        return mode.upper() != "LIVE" and not market_is_open()

    def _cap_the_day(self, reason: str) -> None:
        if self._practice_off_session():
            self.status = "STOPPED"
            self.halt_reason = ""
            self.last_signal = reason
            return
        self.status = "HALTED"
        self.halt_reason = reason

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
        if not market_is_open(now) and self.data_source != "GROWW":
            return False
        return now.time() >= _parse_hhmm(cfg.square_off_time)

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
            books = []
            for symbol in shown:
                book = self.positions.get(symbol)
                books.append(
                    {
                        "symbol": symbol,
                        "direction": book.direction if book else "FLAT",
                        "qty": book.qty if book else 0,
                        "entry_price": book.entry_price if book else None,
                        "sl_trigger": book.sl_trigger if book else None,
                        "ltp": self._ltps.get(symbol),
                        "note": self._signals.get(symbol, ""),
                    }
                )
        finally:
            self._focus, self.ltp = saved_focus, saved_ltp
        kpis = self._kpis((cfg.trading_mode if cfg else "PAPER") or "PAPER")
        return {
            "bot_status": self.status,
            "halt_reason": self.halt_reason,
            "mode": (cfg.trading_mode if cfg else "PAPER"),
            "data_source": self.data_source,
            "last_error": self.last_error,
            "last_signal": self.last_signal,
            "symbol": cfg.symbol if cfg else "",
            "trade_symbols": trade_names(cfg) if cfg else [],
            "books": books,
            "exchange": cfg.exchange if cfg else "NSE",
            "ltp": self.ltp,
            "day_open": day_open,
            "day_change_pct": day_change,
            "sma9": self.sma9,
            "sma21": self.sma21,
            "atr14": self.atr14,
            "adx14": self.adx14,
            "position": None
            if pos is None
            else {
                "direction": pos.direction,
                "qty": pos.qty,
                "entry_price": pos.entry_price,
                "ma_cross_price": pos.ma_cross_price,
                "atr_at_entry": pos.atr_at_entry,
                "sl_trigger": pos.sl_trigger,
                "sl_order_id": pos.sl_order_id,
                "entry_time": pos.entry_time.isoformat(),
                "mode": pos.mode,
            },
            "active_sl_trigger": pos.sl_trigger if pos else None,
            "unrealized_gross_pnl": unreal["gross"] if unreal else 0.0,
            "estimated_charges": unreal["charges"] if unreal else 0.0,
            "unrealized_net_pnl": unreal["net"] if unreal else 0.0,
            "sl_room": unreal["room"] if unreal else None,
            "sl_room_pct": unreal["room_pct"] if unreal else None,
            "charge_estimate": unreal["breakdown"] if unreal else None,
            "realized_net_pnl": self.realized_net,
            "trades_today": self.trades_today,
            "max_trades": cfg.max_trades_per_day if cfg else 15,
            "max_daily_loss": cfg.max_daily_loss if cfg else 5000,
            "kpis": kpis,
            "connected": self.data_source != "ERROR",
        }

    def chart_payload(self, limit: int = 240) -> dict:
        frame = self.candles
        candles = []
        if frame is not None and not frame.empty:
            tail = frame.tail(limit)
            for _, row in tail.iterrows():
                candles.append(
                    {
                        "time": int(row["ts"]),
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "sma9": _finite(row.get("sma_9")),
                        "sma21": _finite(row.get("sma_21")),
                        "atr14": _finite(row.get("atr_14")),
                    }
                )
        # The last row is the forming bar. Blank its indicators so the chart
        # lines stop on the last closed candle and do not repaint.
        if candles:
            candles[-1]["sma9"] = None
            candles[-1]["sma21"] = None
            candles[-1]["atr14"] = None
        markers = []
        cfg = self._cfg_cache
        if cfg is None:
            try:
                cfg = self.load_config()
            except Exception:  # noqa: BLE001
                cfg = None
        view = (cfg.symbol if cfg else self._focus or "").upper()
        with session_factory()() as db:
            rows = db.query(TradeLog).order_by(TradeLog.id.desc()).limit(40).all()
        for row in reversed(rows):
            if view and (row.symbol or "").upper() != view:
                continue
            if row.entry_time is not None:
                markers.append(
                    {
                        "time": int(row.entry_time.replace(tzinfo=IST).timestamp())
                        if row.entry_time.tzinfo is None
                        else int(row.entry_time.timestamp()),
                        "direction": row.direction,
                        "price": row.entry_price,
                        "kind": "ENTRY",
                    }
                )
        pos = self.position
        return {
            "candles": candles,
            "markers": markers,
            "entry_price": pos.entry_price if pos else None,
            "sl_trigger": pos.sl_trigger if pos else None,
        }

    def _kpis(self, mode: str = "PAPER") -> dict:
        day = self._session_date
        book = (mode or "PAPER").upper()
        with session_factory()() as db:
            rows = db.query(TradeLog).filter(TradeLog.date == day, TradeLog.exit_price.isnot(None)).all()
        rows = [row for row in rows if (row.mode or "PAPER").upper() == book]
        theoretical = 0.0
        actual = 0.0
        charges = 0.0
        net = 0.0
        wins = 0
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
        }

    def trades(self) -> list[dict]:
        now = time.monotonic()
        cached = getattr(self, "_trades_cache", None)
        if cached is not None and now - cached[0] < 8:
            return cached[1]
        with session_factory()() as db:
            rows = db.query(TradeLog).order_by(TradeLog.id.desc()).limit(200).all()
        payload = [_trade_dict(r) for r in rows]
        self._trades_cache = (now, payload)
        return payload


_WARN_MINUTES = 3
_WARN_CLEAR_MINUTES = 5

_ALERT_REASON = {
    "MA_CROSS": "MA cross",
    "ATR_SL_HIT": "ATR stop",
    "EOD_SQUARE_OFF": "square-off",
    "KILL_SWITCH": "panic square-off",
}


def fill_alert(
    *,
    mode: str,
    direction: str,
    symbol: str,
    qty: int,
    fill: float,
    stop: float,
    when: dt.datetime,
) -> str:
    """WhatsApp text for a fill. No account numbers or order ids."""
    clock = when.strftime("%d %b %H:%M:%S")
    return (
        f"PalTra Order placed\n"
        f"{mode} {direction} {symbol}\n"
        f"Filled {qty} @ {fill:,.2f}\n"
        f"Stop {stop:,.2f}\n"
        f"{clock} IST"
    )


def close_alert(
    *,
    direction: str,
    symbol: str,
    exit_price: float,
    reason: str,
    gross: float,
    net: float,
    when: dt.datetime,
) -> str:
    clock = when.strftime("%d %b %H:%M:%S")
    why = _ALERT_REASON.get(reason, reason or "closed")
    return (
        f"PalTra closed {direction} {symbol}\n"
        f"Exit {exit_price:,.2f} · {why}\n"
        f"P&L {gross:+,.2f}  net {net:+,.2f}\n"
        f"{clock} IST"
    )


def sma_gap_pct(fast: float, slow: float) -> float | None:
    """SMA 9 minus SMA 21, as a percent of SMA 21. A ₹1 gap is not the same on every stock."""
    if pd.isna(fast) or pd.isna(slow):
        return None
    slow_f = float(slow)
    if slow_f == 0:
        return None
    return (float(fast) - slow_f) / slow_f * 100.0


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


def upcoming_entry_alert(*, mode: str, symbol: str, side: str, minutes: float, gap_pct: float) -> str:
    order = "BUY" if side == "BULLISH" else "SELL"
    return (
        f"PalTra heads-up\n"
        f"{symbol} may be ordered in about {_about(minutes)} min\n"
        f"SMA 9 is {_gap_text(gap_pct)}. A {order} would be placed.\n"
        f"{mode} · no order yet"
    )


def upcoming_close_alert(
    *, mode: str, symbol: str, position: str, side: str, minutes: float, gap_pct: float
) -> str:
    order = "BUY" if side == "BULLISH" else "SELL"
    return (
        f"PalTra heads-up\n"
        f"{position} {symbol} may close in about {_about(minutes)} min\n"
        f"SMA 9 is {_gap_text(gap_pct)}. A {order} would follow.\n"
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


def _trade_dict(row: TradeLog) -> dict:
    points = None
    if row.exit_price is not None:
        raw = float(row.exit_price) - float(row.entry_price)
        points = raw if row.direction == "LONG" else -raw
    return {
        "id": row.id,
        "date": row.date,
        "symbol": row.symbol,
        "direction": row.direction,
        "qty": row.qty,
        "entry_time": row.entry_time.isoformat() if row.entry_time else None,
        "entry_price": row.entry_price,
        "ma_cross_price": row.ma_cross_price,
        "atr_at_entry": row.atr_at_entry,
        "sl_trigger_price": row.sl_trigger_price,
        "exit_time": row.exit_time.isoformat() if row.exit_time else None,
        "exit_price": row.exit_price,
        "exit_reason": row.exit_reason,
        "gross_pnl": row.gross_pnl,
        "brokerage_and_taxes": row.brokerage_and_taxes,
        "net_pnl": row.net_pnl,
        "points": points,
        "mode": row.mode,
    }


def _finite(value) -> float | None:
    try:
        if value is None or pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
