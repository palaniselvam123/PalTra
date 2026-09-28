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


class StrategyEngine:
    def __init__(self, broker: GrowwClient | None = None):
        self.broker = broker or GrowwClient(mode="PAPER")
        self.lock = asyncio.Lock()
        self.inflight: str | None = None  # PENDING | TRANSIT | None
        self.status = "STOPPED"  # STOPPED | RUNNING | PAUSED | DAY_COMPLETED | HALTED
        self.halt_reason = ""
        self.position: OpenPosition | None = None
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
        self._last_minute = ""
        self._sleep = asyncio.sleep
        self.realized_net = 0.0
        self.trades_today = 0
        self._session_date = _ist_now().date().isoformat()
        self._quote_symbol = ""

    def stop(self) -> None:
        self._stop = True

    def load_config(self) -> BotConfig:
        with session_factory()() as db:
            row = db.get(BotConfig, 1)
            if row is None:
                raise RuntimeError("BotConfig missing — init_db() was not called")
            db.expunge(row)
            return row

    def _roll_session(self, now: dt.datetime) -> None:
        day = now.date().isoformat()
        if day != self._session_date:
            self._session_date = day
            self.realized_net = 0.0
            self.trades_today = 0
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
            await self._sleep(0.5)

    async def tick(self, now: dt.datetime) -> None:
        cfg = self.load_config()
        self._roll_session(now)
        self.broker.set_mode(cfg.trading_mode)
        symbol = (cfg.symbol or "").upper()
        if symbol != self._quote_symbol:
            # Drop the previous name's tape so a new symbol cannot inherit it.
            self._quote_symbol = symbol
            self.candles = pd.DataFrame()
            self.ltp = 0.0
        try:
            ltp, frame, source = await self.broker.refresh(cfg.symbol)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            self.data_source = "ERROR"
            return
        self.ltp = float(ltp)
        self.data_source = source
        self.candles = frame
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

        if self.status != "RUNNING":
            return

        if self._loss_breached(cfg):
            await self.kill(f"max_daily_loss ₹{cfg.max_daily_loss:.0f} breached")
            return

        await self._watch_stop(cfg)

        minute_key = now.strftime("%Y-%m-%d %H:%M")
        if now.second == 1 and minute_key != self._last_minute:
            self._last_minute = minute_key
            await self.on_minute(now, cfg, enriched)

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
        signal = closed_candle_cross(frame)
        if signal is None:
            return
        await self.apply_signal(signal, frame, cfg, now)

    async def apply_signal(self, signal: str, frame: pd.DataFrame, cfg: BotConfig, now: dt.datetime) -> str:
        """Stop-and-reverse on a closed-candle cross. Returns a short status."""
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
                return result
            except SlCancelFailed as exc:
                self.last_error = str(exc)
                self.last_signal = f"blocked — {exc}"
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
                self.status = "HALTED"
                self.halt_reason = f"max_trades_per_day ({cfg.max_trades_per_day}) reached"
                return "closed on cross — trade cap locks the day"
            await self._open(want, cross_price, atr, cfg, now)
            return f"reversed to {want}"

        # FLAT
        if adx_blocks_entry:
            return f"{signal} ignored — ADX below {cfg.adx_threshold}"
        if self.trades_today >= int(cfg.max_trades_per_day):
            self.status = "HALTED"
            self.halt_reason = f"max_trades_per_day ({cfg.max_trades_per_day}) reached"
            return "entry blocked — trade cap"
        await self._open(want, cross_price, atr, cfg, now)
        return f"opened {want}"

    async def _open(self, direction: str, cross_price: float, atr: float, cfg: BotConfig, now: dt.datetime) -> None:
        side = "BUY" if direction == "LONG" else "SELL"
        ack = await self.broker.place_entry(cfg.symbol, side, int(cfg.qty), cross_price)
        if ack.status in ("REJECTED", "FAILED"):
            raise SlCancelFailed(f"Entry rejected: {ack.message or ack.status}")
        fill = round_to_nse_tick(ack.fill_price or cross_price)
        sl = self._sl_price(direction, fill, atr, float(cfg.atr_multiplier))
        sl_side = "SELL" if direction == "LONG" else "BUY"
        sl_ack = await self.broker.place_sl(cfg.symbol, sl_side, int(cfg.qty), sl)
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
        self.position = OpenPosition(
            direction=direction,
            qty=int(cfg.qty),
            entry_price=fill,
            ma_cross_price=cross_price,
            atr_at_entry=atr,
            sl_trigger=sl,
            sl_order_id=sl_ack.order_id,
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

    async def _watch_stop(self, cfg: BotConfig) -> None:
        pos = self.position
        if pos is None or self.status != "RUNNING":
            return
        hit = False
        fill_price = self.ltp
        if (cfg.trading_mode or "").upper() == "LIVE" and pos.sl_order_id:
            status = (await self.broker.get_order_status(pos.sl_order_id)).upper()
            if status in TERMINAL_FILLED:
                hit = True
                # Exchange fill price if the adapter stored one; else the trigger.
                fill_price = pos.sl_trigger
        else:
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
            await self.kill(f"max_daily_loss ₹{cfg.max_daily_loss:.0f} breached")

    async def _square_off(self, reason: str) -> None:
        async with self.lock:
            if self.inflight:
                return
            self.inflight = "TRANSIT"
            try:
                if self.position is not None:
                    try:
                        await self._cancel_sl_verified(self.position)
                    except SlCancelFailed:
                        # SL may have filled as we tried to cancel — book whatever
                        # position is left at LTP if we still have one.
                        pass
                    if self.position is not None:
                        cfg = self.load_config()
                        await self._close_position(self.position, self.ltp or self.position.entry_price, reason, _ist_now(), cfg)
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
            db.commit()

    def _day_open_and_change(self) -> tuple[float | None, float | None]:
        """Session change versus the first candle open of today (IST)."""
        frame = self.candles
        if frame is None or getattr(frame, "empty", True) or self.ltp <= 0:
            return None, None
        today = _ist_now().date()
        day_open = None
        for _, row in frame.iterrows():
            ts = dt.datetime.fromtimestamp(int(row["ts"]), IST)
            if ts.date() == today:
                day_open = float(row["open"])
                break
        if day_open is None:
            day_open = float(frame.iloc[0]["open"])
        if day_open <= 0:
            return day_open, None
        return day_open, (self.ltp - day_open) / day_open * 100

    def _loss_breached(self, cfg: BotConfig) -> bool:
        unreal = self._unrealized()
        net = self.realized_net + (unreal["net"] if unreal else 0.0)
        return net <= -abs(float(cfg.max_daily_loss))

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
        cfg = None
        try:
            cfg = self.load_config()
        except Exception:  # noqa: BLE001
            cfg = None
        unreal = self._unrealized()
        kpis = self._kpis()
        pos = self.position
        day_open, day_change = self._day_open_and_change()
        return {
            "bot_status": self.status,
            "halt_reason": self.halt_reason,
            "mode": (cfg.trading_mode if cfg else "PAPER"),
            "data_source": self.data_source,
            "last_error": self.last_error,
            "last_signal": self.last_signal,
            "symbol": cfg.symbol if cfg else "",
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
        with session_factory()() as db:
            rows = db.query(TradeLog).order_by(TradeLog.id.desc()).limit(40).all()
        for row in reversed(rows):
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

    def _kpis(self) -> dict:
        day = self._session_date
        with session_factory()() as db:
            rows = db.query(TradeLog).filter(TradeLog.date == day, TradeLog.exit_price.isnot(None)).all()
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
        with session_factory()() as db:
            rows = db.query(TradeLog).order_by(TradeLog.id.desc()).limit(200).all()
        return [_trade_dict(r) for r in rows]


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
