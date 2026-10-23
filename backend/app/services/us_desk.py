"""US practice desk: the SMA cross strategy on US stocks, on New York time. Practice money only.

**No order path.** Every fill is local (`UsPaperTrade`): nothing here calls Groww's order
API, Alpaca's order API, or any other broker. Prices come from Alpaca's free market data
(`alpaca.py`, IEX feed) when a key pair is saved, else from a built-in simulator so the
desk works with no credentials.

Strategy (a lean copy of the SMA terminal's core, `strategy_engine.py`):

* 1-minute bars grouped into `candle_minutes` candles counted from midnight New York time
  (so from the 09:30 open for 1-30 minute candles). Signals are read on **closed**
  candles only: SMA fast crossing above slow buys, below sells (shorts when
  `allow_short`). An opposite cross closes the trade (`MA_CROSS`) and opens the reverse.
  A cross already on the tape when the desk first sees a stock is not traded.
* Stop: `atr_mult` × Wilder ATR from the fill (`STOP_LOSS`), checked on every pass.
* Size: `dollars_per_trade` ÷ price, whole shares (no fractional shares).
* Times (New York): no entry before `entry_start` or after `entry_cutoff`; open trades are
  squared off at `square_off` (`SQUARE_OFF`), and a trade left from an earlier day is closed
  on the next pass (`DAY_END`). Caps: `max_trades_per_day`, `daily_loss_limit` ($ before
  charges). Real (Alpaca) prices only fill in the regular session; the **simulated feed
  trades around the clock** so the desk can be tried at any hour, and says so.
* Charges are a US estimate on the sell side (SEC fee and FINRA TAF); commission is zero.
  Currency conversion and India's TCS on money sent abroad are not trade costs and are
  not included. P&L is in US dollars, before charges.

**Groww US check** (`groww_us_check`, read-only): asks Groww's trading API for a US price
(`exchange="US"`) with the desk's Groww session. Today the API covers NSE / BSE / MCX
only; the desk re-checks every few hours and sends one Telegram the first time it answers.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import random
import time
import zlib
from collections import deque

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.models.database import UsCredential, UsDeskConfig, UsPaperTrade, async_session
from app.services import alpaca, us_market
from app.services.us_market import ET

POLL_SEC = 5.0
START_DELAY_SEC = 20
BARS_EVERY_SEC = 30.0
IDLE_REFRESH_SEC = 60.0
HISTORY_DAYS = 5  # calendar days of 1-minute bars loaded at first (SMA 21 on 15-minute candles needs the day before)
CANDLE_MINUTES = (1, 2, 3, 5, 10, 15, 30)
MAX_SYMBOLS = 30
GROWW_CHECK_EVERY = dt.timedelta(hours=6)

# US sell-side fees (estimates): SEC Section 31 fee on the sale value, FINRA TAF per share sold (capped).
SEC_FEE_RATE = 0.0000278
FINRA_TAF_PER_SHARE = 0.000166
FINRA_TAF_MAX = 8.30


def charges(qty: int, sell_price: float) -> float:
    return round(SEC_FEE_RATE * qty * sell_price + min(FINRA_TAF_PER_SHARE * qty, FINRA_TAF_MAX), 4)


def parse_symbols(text: str | None) -> list[str]:
    out: list[str] = []
    for raw in str(text or "").replace(";", ",").replace(" ", ",").split(","):
        s = raw.strip().upper()
        if s and s not in out and len(s) <= 10 and all(c.isalnum() or c in ".-" for c in s):
            out.append(s)
    return out[:MAX_SYMBOLS]


# ---- candles and signals -----------------------------------------------------


def to_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])


def resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """1-minute bars as `minutes` candles counted from midnight New York time (start ts per candle)."""
    if frame is None or frame.empty or minutes <= 1:
        return frame
    ts = frame["ts"].to_numpy(dtype="int64")
    offsets = np.array(
        [int(dt.datetime.fromtimestamp(int(t), ET).utcoffset().total_seconds()) for t in ts], dtype="int64"
    )
    local = ts + offsets
    span = minutes * 60
    start = local - local % span - offsets
    first = np.flatnonzero(np.r_[True, start[1:] != start[:-1]])
    last = np.r_[first[1:] - 1, len(ts) - 1]
    out = pd.DataFrame(
        {
            "ts": start[first],
            "open": frame["open"].to_numpy(dtype="float64")[first],
            "high": np.maximum.reduceat(frame["high"].to_numpy(dtype="float64"), first),
            "low": np.minimum.reduceat(frame["low"].to_numpy(dtype="float64"), first),
            "close": frame["close"].to_numpy(dtype="float64")[last],
            "volume": np.add.reduceat(frame["volume"].to_numpy(dtype="float64"), first),
        }
    )
    return out


def closed_candles(frame: pd.DataFrame, minutes: int, now_ts: float) -> pd.DataFrame:
    """Only candles that have ended by `now_ts` (the forming one is dropped)."""
    candles = resample(frame, minutes)
    if candles is None or candles.empty:
        return candles
    return candles[candles["ts"] + minutes * 60 <= now_ts].reset_index(drop=True)


def enrich(candles: pd.DataFrame, fast: int, slow: int, atr_period: int) -> pd.DataFrame:
    from indicators import atr_wilder

    out = candles.copy()
    out["sma_fast"] = out["close"].rolling(int(fast)).mean()
    out["sma_slow"] = out["close"].rolling(int(slow)).mean()
    out["atr"] = atr_wilder(out, int(atr_period)) if len(out) else pd.Series(dtype=float)
    return out


def cross(enriched: pd.DataFrame) -> str | None:
    """BUY / SELL when the SMAs crossed on the last closed candle, else None."""
    if enriched is None or len(enriched) < 2:
        return None
    prev, last = enriched.iloc[-2], enriched.iloc[-1]
    vals = (prev["sma_fast"], prev["sma_slow"], last["sma_fast"], last["sma_slow"])
    if any(pd.isna(v) for v in vals):
        return None
    if prev["sma_fast"] <= prev["sma_slow"] and last["sma_fast"] > last["sma_slow"]:
        return "BUY"
    if prev["sma_fast"] >= prev["sma_slow"] and last["sma_fast"] < last["sma_slow"]:
        return "SELL"
    return None


# ---- simulated feed ------------------------------------------------------------


class SimFeed:
    """Random-walk 1-minute bars per symbol with trends that turn every so often (no credentials needed)."""

    def __init__(self, seed: int | None = None):
        self._seed = seed
        self._bars: dict[str, list[dict]] = {}
        self._cur: dict[str, dict] = {}
        self._rng: dict[str, random.Random] = {}
        self._drift: dict[str, tuple[float, int]] = {}

    def _rand(self, symbol: str) -> random.Random:
        if symbol not in self._rng:
            base = zlib.crc32(symbol.encode()) if self._seed is None else self._seed + zlib.crc32(symbol.encode())
            self._rng[symbol] = random.Random(base)
        return self._rng[symbol]

    def _step(self, symbol: str, price: float, minute: int) -> float:
        rng = self._rand(symbol)
        drift, until = self._drift.get(symbol, (0.0, 0))
        if minute >= until:
            drift = rng.choice([-1, 1]) * rng.uniform(0.0002, 0.0012)
            until = minute + rng.randint(20, 90)
            self._drift[symbol] = (drift, until)
        return max(1.0, price * math.exp(drift + rng.gauss(0, 0.0015)))

    def _start(self, symbol: str, now_min: int) -> None:
        rng = self._rand(symbol)
        price = 20 + (zlib.crc32(symbol.encode()) % 480)
        bars = []
        for m in range(now_min - 600, now_min):
            o = price
            price = self._step(symbol, price, m)
            hi = max(o, price) * (1 + abs(rng.gauss(0, 0.0005)))
            lo = min(o, price) * (1 - abs(rng.gauss(0, 0.0005)))
            bars.append({"ts": m * 60, "open": o, "high": hi, "low": lo, "close": price, "volume": rng.randint(1000, 50000)})
        self._bars[symbol] = bars
        self._cur[symbol] = {"ts": now_min * 60, "open": price, "high": price, "low": price, "close": price, "volume": 0}

    def tick(self, symbols: list[str], now: float) -> None:
        now_min = int(now // 60)
        for symbol in symbols:
            if symbol not in self._bars:
                self._start(symbol, now_min)
            cur = self._cur[symbol]
            while cur["ts"] // 60 < now_min:  # the minute ended: keep it, start the next
                self._bars[symbol].append(dict(cur))
                p = cur["close"]
                cur = {"ts": cur["ts"] + 60, "open": p, "high": p, "low": p, "close": p, "volume": 0}
                if cur["ts"] // 60 < now_min:  # a minute nobody watched: walk it in one step
                    cur["close"] = self._step(symbol, p, cur["ts"] // 60)
                    cur["high"], cur["low"] = max(p, cur["close"]), min(p, cur["close"])
            rng = self._rand(symbol)
            price = cur["close"] * math.exp(rng.gauss(0, 0.0006)) if cur["volume"] else self._step(symbol, cur["close"], now_min)
            cur.update(close=price, high=max(cur["high"], price), low=min(cur["low"], price), volume=cur["volume"] + rng.randint(100, 3000))
            self._cur[symbol] = cur
            del self._bars[symbol][:-2000]

    def frame(self, symbol: str) -> pd.DataFrame:
        return to_frame(self._bars.get(symbol, []) + ([self._cur[symbol]] if symbol in self._cur else []))

    def price(self, symbol: str) -> float | None:
        cur = self._cur.get(symbol)
        return cur["close"] if cur else None


# ---- the desk ------------------------------------------------------------------


def _now_ts() -> float:
    return time.time()


class UsDesk:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.sim = SimFeed()
        self._data: alpaca.AlpacaData | None = None
        self._data_sig: tuple | None = None
        self._minute: dict[str, pd.DataFrame] = {}  # alpaca 1-minute bars per symbol
        self._bars_at = 0.0
        self._idle_at = 0.0
        self.prices: dict[str, float] = {}
        self._seen: dict[str, int] = {}  # symbol -> last closed candle ts acted on
        self.feed = "simulated"
        self.key_source: str | None = None
        self.error: str | None = None
        self.last_pass: str | None = None
        self.events: deque[dict] = deque(maxlen=80)

    # -- lifecycle --
    def start(self) -> None:
        # US_DESK=off (the tests set it) keeps the loop from touching the desk DB on a test's short-lived loop.
        if os.environ.get("US_DESK", "on").strip().lower() == "off":
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(START_DELAY_SEC)
        while True:
            try:
                await self.step()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive
                self.error = f"{type(exc).__name__}: {str(exc)[:200]}"
            try:
                await self.maybe_check_groww()
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(POLL_SEC)

    def note(self, text: str, level: str = "INFO") -> None:
        self.events.appendleft({"at": us_market.et_now().strftime("%H:%M:%S"), "level": level, "text": text})

    # -- config and keys --
    async def config(self) -> UsDeskConfig:
        async with async_session() as session:
            row = await session.get(UsDeskConfig, 1)
            if row is None:
                row = UsDeskConfig(id=1)
                session.add(row)
                await session.commit()
                await session.refresh(row)
            return row

    async def keys(self) -> alpaca.Keys | None:
        async with async_session() as session:
            row = await session.scalar(select(UsCredential).where(UsCredential.provider == "alpaca"))
        if row is not None:
            try:
                from app.core.encryption import get_vault

                vault = get_vault()
                return alpaca.Keys(vault.decrypt(row.key_id_encrypted), vault.decrypt(row.secret_encrypted), "saved")
            except Exception:  # noqa: BLE001 - vault key changed: fall back to the environment
                pass
        return alpaca.env_keys()

    async def _client(self, keys: alpaca.Keys | None) -> alpaca.AlpacaData | None:
        sig = (keys.key_id, keys.secret) if keys else None
        if sig != self._data_sig:
            if self._data is not None:
                await self._data.aclose()
            self._data = alpaca.AlpacaData(keys) if keys else None
            self._data_sig = sig
            self._minute.clear()
            self._bars_at = 0.0
            self._seen.clear()
        return self._data

    # -- prices --
    async def _refresh(self, symbols: list[str], now: float) -> None:
        if self.feed == "simulated":
            self.sim.tick(symbols, now)
            for s in symbols:
                p = self.sim.price(s)
                if p:
                    self.prices[s] = p
            return
        data = self._data
        assert data is not None
        missing = [s for s in symbols if s not in self._minute]
        if missing or now - self._bars_at >= BARS_EVERY_SEC:
            start_new = dt.datetime.fromtimestamp(now, dt.timezone.utc) - dt.timedelta(days=HISTORY_DAYS)
            if missing:
                got = await data.bars(missing, start_new)
                for s in missing:
                    self._minute[s] = to_frame(got.get(s, []))
            known = [s for s in symbols if s not in missing]
            if known:
                since = min(int(self._minute[s]["ts"].max()) if len(self._minute[s]) else int(now) - 3600 for s in known)
                got = await data.bars(known, dt.datetime.fromtimestamp(since, dt.timezone.utc))
                for s in known:
                    fresh = to_frame(got.get(s, []))
                    if len(fresh):
                        merged = pd.concat([self._minute[s], fresh]).drop_duplicates("ts", keep="last")
                        self._minute[s] = merged.sort_values("ts").tail(5000).reset_index(drop=True)
            self._bars_at = now
        latest = await data.latest(symbols)
        for s, t in latest.items():
            self.prices[s] = t["price"]

    def frame(self, symbol: str) -> pd.DataFrame:
        return self.sim.frame(symbol) if self.feed == "simulated" else self._minute.get(symbol, to_frame([]))

    # -- one pass --
    async def step(self, now: float | None = None) -> None:
        async with self._lock:
            await self._step(_now_ts() if now is None else now)

    async def _step(self, now: float) -> None:
        cfg = await self.config()
        keys = await self.keys()
        await self._client(keys)
        feed = "alpaca" if keys else "simulated"
        if feed != self.feed:
            self._seen.clear()
            self.feed = feed
        self.key_source = keys.source if keys else None
        armed = parse_symbols(cfg.symbols)
        async with async_session() as session:
            open_rows = list((await session.scalars(select(UsPaperTrade).where(UsPaperTrade.status == "OPEN"))).all())
        watch = list(dict.fromkeys(armed + [r.symbol for r in open_rows]))
        when = dt.datetime.fromtimestamp(now, ET)
        market_open = self.feed == "simulated" or us_market.is_open(when)
        idle_due = now - self._idle_at >= IDLE_REFRESH_SEC  # closed market: prices for the page once a minute
        if watch and (market_open or open_rows or idle_due):
            self._idle_at = now
            try:
                await self._refresh(watch, now)
                self.error = None
            except alpaca.AlpacaError as exc:
                self.error = str(exc)
                return
        self.last_pass = when.strftime("%Y-%m-%d %H:%M:%S")
        today = when.date().isoformat()
        await self._guard_open(cfg, open_rows, when, today)
        if not cfg.running or not market_open:
            return
        minutes = cfg.candle_minutes if cfg.candle_minutes in CANDLE_MINUTES else 5
        for symbol in armed:
            await self._judge(cfg, symbol, minutes, now, when, today)

    async def _guard_open(self, cfg: UsDeskConfig, rows: list[UsPaperTrade], when: dt.datetime, today: str) -> None:
        square = us_market.parse_hhmm(cfg.square_off, dt.time(15, 50))
        for row in rows:
            price = self.prices.get(row.symbol)
            if price is None:
                continue
            if row.trade_day != today:
                await self._close(row.id, price, "DAY_END")
            elif self.feed == "alpaca" and when.time() >= square:
                await self._close(row.id, price, "SQUARE_OFF")
            elif row.stop_price and (
                (row.direction == "LONG" and price <= row.stop_price)
                or (row.direction == "SHORT" and price >= row.stop_price)
            ):
                await self._close(row.id, price, "STOP_LOSS")

    async def _judge(self, cfg, symbol: str, minutes: int, now: float, when: dt.datetime, today: str) -> None:
        candles = closed_candles(self.frame(symbol), minutes, now)
        if candles is None or candles.empty:
            return
        last_ts = int(candles["ts"].iloc[-1])
        seen = self._seen.get(symbol)
        self._seen[symbol] = last_ts
        if seen is None or last_ts <= seen:
            return  # first look at this stock (an old cross is not traded) or no new closed candle
        enriched = enrich(candles, cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        signal = cross(enriched)
        if signal is None:
            return
        side = "LONG" if signal == "BUY" else "SHORT"
        async with async_session() as session:
            held = await session.scalar(
                select(UsPaperTrade).where(UsPaperTrade.symbol == symbol, UsPaperTrade.status == "OPEN")
            )
        price = self.prices.get(symbol) or float(candles["close"].iloc[-1])
        if held is not None:
            if held.direction == side:
                return
            await self._close(held.id, price, "MA_CROSS")
        why = await self.entry_block(cfg, side, when, today)
        if why:
            self.note(f"{symbol}: {signal} cross, no entry: {why}")
            return
        atr = enriched["atr"].iloc[-1]
        await self.open(symbol, side, price, None if pd.isna(atr) else float(atr), cfg, today, "SMA_CROSS")

    async def entry_block(self, cfg, side: str, when: dt.datetime, today: str) -> str | None:
        if side == "SHORT" and not cfg.allow_short:
            return "shorting is off"
        if self.feed == "alpaca":
            t = when.time()
            if t < us_market.parse_hhmm(cfg.entry_start, dt.time(9, 35)):
                return f"before the entry start {cfg.entry_start} New York"
            if t >= us_market.parse_hhmm(cfg.entry_cutoff, dt.time(15, 30)):
                return f"after the entry cut-off {cfg.entry_cutoff} New York"
        async with async_session() as session:
            rows = list((await session.scalars(select(UsPaperTrade).where(UsPaperTrade.trade_day == today))).all())
        if cfg.max_trades_per_day and len(rows) >= cfg.max_trades_per_day:
            return f"{len(rows)} trades today (cap {cfg.max_trades_per_day})"
        closed = sum(r.pnl or 0.0 for r in rows if r.status == "CLOSED")
        if cfg.daily_loss_limit and closed <= -abs(cfg.daily_loss_limit):
            return f"day loss ${-closed:,.2f} reached the ${cfg.daily_loss_limit:,.0f} limit"
        return None

    async def open(self, symbol, side, price, atr, cfg, today, reason) -> UsPaperTrade | None:
        qty = int(cfg.dollars_per_trade // price) if price > 0 else 0
        if qty < 1:
            self.note(f"{symbol}: ${price:,.2f} is more than ${cfg.dollars_per_trade:,.0f} a trade, no entry", "WARN")
            return None
        stop = None
        if cfg.stop_on and atr:
            gap = atr * cfg.atr_mult
            stop = round(price - gap, 2) if side == "LONG" else round(price + gap, 2)
        row = UsPaperTrade(
            symbol=symbol, direction=side, qty=qty, entry_price=round(price, 4), stop_price=stop,
            entry_reason=reason, feed=self.feed, trade_day=today,
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        self.note(f"{'Bought' if side == 'LONG' else 'Shorted'} {qty} {symbol} @ ${price:,.2f}"
                  + (f", stop ${stop:,.2f}" if stop else "") + f" ({reason})")
        return row

    async def _close(self, trade_id: int, price: float, reason: str) -> UsPaperTrade | None:
        async with async_session() as session:
            row = await session.get(UsPaperTrade, trade_id)
            if row is None or row.status != "OPEN":
                return None
            sign = 1 if row.direction == "LONG" else -1
            row.exit_price = round(price, 4)
            row.exit_reason = reason
            row.pnl = round(sign * (price - row.entry_price) * row.qty, 2)
            sell = price if row.direction == "LONG" else row.entry_price
            row.charges = charges(row.qty, sell)
            row.status = "CLOSED"
            row.closed_at = dt.datetime.utcnow()
            await session.commit()
            await session.refresh(row)
        self.note(f"Closed {row.symbol} {row.direction} @ ${price:,.2f}: {'+' if row.pnl >= 0 else '−'}${abs(row.pnl):,.2f} ({reason})")
        return row

    # -- actions from the page --
    async def close_trade(self, trade_id: int, reason: str = "MANUAL_EXIT") -> UsPaperTrade | None:
        async with self._lock:
            async with async_session() as session:
                row = await session.get(UsPaperTrade, trade_id)
            if row is None or row.status != "OPEN":
                return None
            price = self.prices.get(row.symbol)
            if price is None:
                raise ValueError(f"No price for {row.symbol} yet")
            return await self._close(trade_id, price, reason)

    async def close_all(self, reason: str = "MANUAL_EXIT") -> int:
        async with async_session() as session:
            ids = list((await session.scalars(select(UsPaperTrade.id).where(UsPaperTrade.status == "OPEN"))).all())
        done = 0
        for trade_id in ids:
            try:
                done += 1 if await self.close_trade(trade_id, reason) else 0
            except ValueError:
                continue
        return done

    # -- what the page reads --
    async def snapshot(self) -> dict:
        cfg = await self.config()
        when = us_market.et_now()
        today = when.date().isoformat()
        async with async_session() as session:
            today_rows = list((await session.scalars(
                select(UsPaperTrade).where(UsPaperTrade.trade_day == today).order_by(UsPaperTrade.id.desc())
            )).all())
            open_rows = list((await session.scalars(select(UsPaperTrade).where(UsPaperTrade.status == "OPEN"))).all())
        positions = []
        open_pnl = 0.0
        for r in open_rows:
            price = self.prices.get(r.symbol)
            pnl = None
            if price is not None:
                pnl = round((price - r.entry_price) * r.qty * (1 if r.direction == "LONG" else -1), 2)
                open_pnl += pnl
            positions.append({**trade_out(r), "last": price, "open_pnl": pnl})
        closed = [r for r in today_rows if r.status == "CLOSED"]
        armed = parse_symbols(cfg.symbols)
        return {
            "config": config_out(cfg),
            "feed": self.feed,
            "key_source": self.key_source,
            "market": us_market.status(when),
            "running": cfg.running,
            "error": self.error,
            "last_pass": self.last_pass,
            "prices": {s: self.prices.get(s) for s in armed},
            "positions": positions,
            "trades": [trade_out(r) for r in today_rows],
            "kpis": {
                "day": today,
                "closed_pnl": round(sum(r.pnl or 0 for r in closed), 2),
                "open_pnl": round(open_pnl, 2),
                "charges": round(sum(r.charges or 0 for r in closed), 2),
                "trades": len(today_rows),
                "wins": sum(1 for r in closed if (r.pnl or 0) > 0),
                "losses": sum(1 for r in closed if (r.pnl or 0) < 0),
            },
            "events": list(self.events)[:40],
            "groww": groww_out(cfg),
        }

    async def chart(self, symbol: str) -> dict:
        cfg = await self.config()
        minutes = cfg.candle_minutes if cfg.candle_minutes in CANDLE_MINUTES else 5
        candles = resample(self.frame(symbol), minutes)
        if candles is None or candles.empty:
            return {"symbol": symbol, "candle_minutes": minutes, "feed": self.feed, "candles": [], "markers": []}
        candles = enrich(candles.tail(400).reset_index(drop=True), cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
        lo = int(candles["ts"].iloc[0])
        rows = [
            {
                "time": _et_wall(int(r.ts)), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                "fast": None if pd.isna(r.sma_fast) else round(r.sma_fast, 4),
                "slow": None if pd.isna(r.sma_slow) else round(r.sma_slow, 4),
            }
            for r in candles.itertuples()
        ]
        async with async_session() as session:
            trades = list((await session.scalars(
                select(UsPaperTrade).where(UsPaperTrade.symbol == symbol, UsPaperTrade.feed == self.feed)
                .order_by(UsPaperTrade.id.desc()).limit(100)
            )).all())
        markers = []
        for t in trades:
            opened = int(t.opened_at.replace(tzinfo=dt.timezone.utc).timestamp())
            if opened >= lo:
                markers.append({"time": _et_wall(opened), "kind": "entry", "side": t.direction, "price": t.entry_price})
            if t.closed_at is not None:
                closed = int(t.closed_at.replace(tzinfo=dt.timezone.utc).timestamp())
                if closed >= lo:
                    markers.append({"time": _et_wall(closed), "kind": "exit", "side": t.direction, "price": t.exit_price,
                                    "reason": t.exit_reason, "pnl": t.pnl})
        return {"symbol": symbol, "candle_minutes": minutes, "feed": self.feed, "candles": rows,
                "markers": sorted(markers, key=lambda m: m["time"])}

    # -- the Groww US check --
    async def maybe_check_groww(self) -> None:
        cfg = await self.config()
        last = cfg.groww_checked_at
        if last is not None and dt.datetime.utcnow() - last < GROWW_CHECK_EVERY:
            return
        if _groww_sdk() is None:
            return  # no Groww session on the desk yet; checked once one is there
        await run_groww_check(cfg.groww_check_symbol or "AAPL", alert=True)


def _et_wall(ts: int) -> int:
    """Epoch seconds shifted to New York wall-clock time, so the chart's axis (which shows UTC) reads New York time."""
    return ts + int(dt.datetime.fromtimestamp(ts, ET).utcoffset().total_seconds())


def trade_out(r: UsPaperTrade) -> dict:
    return {
        "id": r.id, "symbol": r.symbol, "direction": r.direction, "qty": r.qty, "entry_price": r.entry_price,
        "stop_price": r.stop_price, "entry_reason": r.entry_reason, "feed": r.feed, "status": r.status,
        "exit_price": r.exit_price, "exit_reason": r.exit_reason, "pnl": r.pnl, "charges": r.charges,
        "trade_day": r.trade_day,
        "opened_at": r.opened_at.replace(tzinfo=dt.timezone.utc).isoformat() if r.opened_at else None,
        "closed_at": r.closed_at.replace(tzinfo=dt.timezone.utc).isoformat() if r.closed_at else None,
    }


CONFIG_FIELDS = (
    "symbols", "candle_minutes", "sma_fast", "sma_slow", "atr_period", "stop_on", "atr_mult", "allow_short",
    "dollars_per_trade", "max_trades_per_day", "daily_loss_limit", "entry_start", "entry_cutoff", "square_off",
    "groww_check_symbol",
)


def config_out(cfg: UsDeskConfig) -> dict:
    out = {k: getattr(cfg, k) for k in CONFIG_FIELDS}
    out["symbols"] = parse_symbols(cfg.symbols)
    out["running"] = cfg.running
    return out


def groww_out(cfg: UsDeskConfig) -> dict:
    return {
        "symbol": cfg.groww_check_symbol,
        "checked_at": cfg.groww_checked_at.replace(tzinfo=dt.timezone.utc).isoformat() if cfg.groww_checked_at else None,
        "ok": cfg.groww_ok,
        "note": cfg.groww_note,
        "session": _groww_sdk() is not None,
    }


# ---- Groww US check (read-only) ------------------------------------------------


def _groww_sdk():
    from app.services.options import _groww_sdk as sdk

    return sdk()


def _price_in(answer) -> float | None:
    """A positive price anywhere in Groww's answer (`last_price`, or the value of an LTP map)."""
    if isinstance(answer, (int, float)) and not isinstance(answer, bool):
        return float(answer) if answer > 0 else None
    if isinstance(answer, dict):
        for key in ("last_price", "ltp", "lastPrice"):
            v = answer.get(key)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        for v in answer.values():
            found = _price_in(v)
            if found:
                return found
    return None


def probe_groww(sdk, symbol: str) -> dict:
    """Blocking: ask Groww's API for a US price two ways. Never touches the order API."""
    tries = []
    calls = (
        ("quote", lambda: sdk.get_quote(trading_symbol=symbol, exchange="US", segment="CASH", timeout=10)),
        ("ltp", lambda: sdk.get_ltp(exchange_trading_symbols=(f"US_{symbol}",), segment="CASH", timeout=10)),
    )
    for name, call in calls:
        try:
            answer = call()
            price = _price_in(answer)
            tries.append({"call": name, "ok": price is not None, "price": price,
                          "note": None if price else f"answered without a price: {str(answer)[:160]}"})
        except Exception as exc:  # noqa: BLE001 - the refusal is the answer
            tries.append({"call": name, "ok": False, "price": None, "note": f"{type(exc).__name__}: {str(exc)[:160]}"})
    return {"symbol": symbol, "ok": any(t["ok"] for t in tries), "tries": tries}


async def run_groww_check(symbol: str, *, alert: bool = False) -> dict:
    symbol = (parse_symbols(symbol) or ["AAPL"])[0]
    sdk = _groww_sdk()
    if sdk is None:
        result = {"symbol": symbol, "ok": False, "tries": [],
                  "note": "No Groww session on the desk: log in to Groww (Settings) and check again."}
    else:
        result = await asyncio.to_thread(probe_groww, sdk, symbol)
        if result["ok"]:
            price = next(t["price"] for t in result["tries"] if t["ok"])
            result["note"] = f"Groww's API answered a US price: {symbol} {price}. US trading through Groww may now be possible."
        else:
            result["note"] = "Groww's API does not serve US prices yet: " + "; ".join(
                f"{t['call']}: {t['note']}" for t in result["tries"]
            )
    async with async_session() as session:
        cfg = await session.get(UsDeskConfig, 1)
        if cfg is None:
            cfg = UsDeskConfig(id=1)
            session.add(cfg)
        first_ok = result["ok"] and not cfg.groww_alerted
        if sdk is not None:
            cfg.groww_checked_at = dt.datetime.utcnow()
            cfg.groww_ok = result["ok"]
            cfg.groww_note = result["note"][:1000]
        if first_ok and alert:
            cfg.groww_alerted = True
        await session.commit()
    if first_ok and alert:
        try:
            from app.services.alert_notifier import alert_notifier

            await alert_notifier.send(f"🇺🇸 Groww US check: {result['note']}")
        except Exception:  # noqa: BLE001 - the page still shows it
            pass
    return result


us_desk = UsDesk()
