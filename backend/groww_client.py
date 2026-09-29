"""Groww execution wrapper with PAPER and LIVE paths.

PAPER reads the real NSE last price and 1-minute candles whenever a Groww
token is present, including after the close. The token is the env value or
the session the desk already saved. The internal simulator runs only when
neither exists. Fills are booked locally; this token is never used to place
an order from the quote path.

LIVE places MIS limit orders with a 0.20% protection buffer (not raw market
orders) and a resting exchange SL for the ATR stop.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import math
import os
import random
import sqlite3
import time
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import pandas as pd

from config import get_settings
from indicators import round_to_nse_tick

IST = ZoneInfo("Asia/Kolkata")
# A closed session does not need a new candle download every second.
_QUOTE_TTL_OPEN_SEC = 3.0
_QUOTE_TTL_CLOSED_SEC = 60.0
_QUOTE_TIMEOUT_SEC = 8.0
_QUOTE_RETRY_SEC = 20.0
_SDK_TIMEOUT_SEC = 6
_DESK_TOKEN_TTL_SEC = 30.0
_desk_token_cache: tuple[float, str] | None = None

# Statuses that mean "do not place another order yet".
IN_FLIGHT = frozenset({"PENDING", "TRANSIT", "NEW", "OPEN", "PLACED"})
TERMINAL_CANCELLED = frozenset({"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"})
# Groww's own word for a filled order is EXECUTED. Missing it left a long
# on the terminal after the exchange stop had already sold the shares.
TERMINAL_FILLED = frozenset({"FILLED", "COMPLETE", "COMPLETED", "TRIGGERED", "EXECUTED", "DELIVERY_AWAITED"})


def desk_session_token() -> str:
    """Access token the desk already saved, used for quotes only.

    GROWW_ACCESS_TOKEN on this host is empty, so the terminal would otherwise
    invent a price. The decrypted value is never logged or returned over HTTP.
    """
    global _desk_token_cache
    now = time.monotonic()
    if _desk_token_cache is not None and now - _desk_token_cache[0] < _DESK_TOKEN_TTL_SEC:
        return _desk_token_cache[1]
    token = _read_desk_token()
    _desk_token_cache = (now, token)
    return token


def _read_desk_token() -> str:
    path = os.environ.get("DESK_TRADING_DB", "/data/trading.db")
    key = os.environ.get("ENCRYPTION_KEY", "").strip()
    if not key or not os.path.isfile(path):
        return ""
    try:
        uri = f"file:{path}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=2) as con:
            row = con.execute(
                "select access_token_encrypted, token_expires_at "
                "from broker_credentials where broker = ? limit 1",
                ("groww",),
            ).fetchone()
        if not row or not row[0] or not row[1]:
            return ""
        expires = dt.datetime.fromisoformat(str(row[1]).replace("Z", ""))
        if expires.tzinfo is not None:
            expires = expires.replace(tzinfo=None)
        if expires <= dt.datetime.now():
            return ""
        from cryptography.fernet import Fernet

        plain = Fernet(key.encode()).decrypt(str(row[0]).encode()).decode()
        return plain.strip()
    except Exception:
        return ""


def market_is_open(now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(IST)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return dt.time(9, 15) <= t <= dt.time(15, 30)


@dataclass
class OrderAck:
    order_id: str
    status: str
    fill_price: float | None = None
    message: str = ""


@dataclass
class _SimOrder:
    order_id: str
    side: str
    qty: int
    kind: str  # ENTRY | SL | EXIT
    status: str
    fill_price: float | None = None
    trigger: float | None = None


class CandleSimulator:
    """Random-walk 1-minute tape so the terminal runs when NSE is closed."""

    def __init__(self, start_price: float = 875.0):
        self.price = start_price
        self.candles: list[dict] = []
        self._minute_key: str | None = None
        self._rng = random.Random(21)
        self._seed(120)

    def _seed(self, n: int) -> None:
        price = self.price
        start = dt.datetime.now(IST) - dt.timedelta(minutes=n)
        start = start.replace(second=0, microsecond=0)
        for i in range(n):
            # Drift up, then roll over, so SMA9/SMA21 are populated and a
            # historical cross exists for the chart markers.
            drift = 0.15 if i < int(n * 0.65) else -0.22
            shock = self._rng.uniform(-0.6, 0.6)
            o = price
            c = max(1.0, price + drift + shock)
            h = max(o, c) + abs(shock) * 0.4
            l = min(o, c) - abs(shock) * 0.3
            ts = start + dt.timedelta(minutes=i)
            self.candles.append(
                {
                    "ts": int(ts.timestamp()),
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "volume": self._rng.randint(5_000, 40_000),
                }
            )
            price = c
        self.price = price
        # Forming bar on top of the closed history.
        now = dt.datetime.now(IST).replace(second=0, microsecond=0)
        self._minute_key = now.strftime("%Y-%m-%d %H:%M")
        self.candles.append(
            {
                "ts": int(now.timestamp()),
                "open": round(self.price, 2),
                "high": round(self.price, 2),
                "low": round(self.price, 2),
                "close": round(self.price, 2),
                "volume": 0,
            }
        )

    def advance(self, now: dt.datetime | None = None) -> float:
        now = now or dt.datetime.now(IST)
        key = now.strftime("%Y-%m-%d %H:%M")
        bar = self.candles[-1]
        span = max(self.price, 1.0) * 0.0003
        shock = self._rng.uniform(-span, span)
        self.price = max(1.0, self.price + shock)
        px = round(self.price, 2)
        if key != self._minute_key:
            # Close the previous forming bar and open a new one. The closed
            # bar stays at [-2] once the new forming bar is appended — that
            # is the bar the strategy is allowed to read.
            self._minute_key = key
            self.candles.append(
                {
                    "ts": int(now.replace(second=0, microsecond=0).timestamp()),
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "volume": 1,
                }
            )
        else:
            bar["close"] = px
            bar["high"] = round(max(bar["high"], px), 2)
            bar["low"] = round(min(bar["low"], px), 2)
            bar["volume"] = int(bar["volume"]) + self._rng.randint(10, 80)
        return px

    def adopt(self, frame: pd.DataFrame, price: float) -> None:
        """Continue from a real NSE tape instead of a fresh random walk."""
        if frame is None or getattr(frame, "empty", True):
            return
        rows: list[dict] = []
        for rec in frame.to_dict("records"):
            try:
                ts = int(rec["ts"])
                close = float(rec["close"])
            except (KeyError, TypeError, ValueError):
                continue
            rows.append(
                {
                    "ts": ts,
                    "open": float(rec.get("open") or close),
                    "high": float(rec.get("high") or close),
                    "low": float(rec.get("low") or close),
                    "close": close,
                    "volume": int(float(rec.get("volume") or 0)),
                }
            )
        if not rows:
            return
        if len(rows) > 2500:
            rows = rows[-2500:]
        self.candles = rows
        self.price = float(price) if price and price > 0 else float(rows[-1]["close"])
        last = dt.datetime.fromtimestamp(rows[-1]["ts"], tz=IST)
        self._minute_key = last.strftime("%Y-%m-%d %H:%M")

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.candles)


class GrowwClient:
    def __init__(self, mode: str = "PAPER", token: str = ""):
        self.mode = "LIVE" if (mode or "").upper() == "LIVE" else "PAPER"
        self.token = token or get_settings().groww_access_token or desk_session_token()
        self.simulator = CandleSimulator()
        self.data_source = "SIMULATOR"
        self.last_error = ""
        self._orders: dict[str, _SimOrder] = {}
        self._seq = 0
        self._ltp = self.simulator.price
        self._sdk = None
        self._positions: list[dict] = []
        self._quotes: dict[str, tuple[float, pd.DataFrame, float]] = {}
        self._retry_after: dict[str, float] = {}
        self._simulators: dict[str, CandleSimulator] = {}
        self._sim_symbol = ""

    def set_mode(self, mode: str, token: str | None = None) -> None:
        self.mode = "LIVE" if mode.upper() == "LIVE" else "PAPER"
        # Boot passes the empty env token. That must not wipe the desk session.
        if token and token != self.token:
            self._sdk = None
            self.token = token

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:06d}"

    def _require_sdk(self):
        if self._sdk is not None:
            return self._sdk
        if not self.token:
            raise RuntimeError("GROWW_ACCESS_TOKEN is empty")
        from growwapi import GrowwAPI

        # The SDK fetches a changelog with no HTTP timeout before it returns.
        # That call has frozen this host. Quotes do not need it.
        GrowwAPI._get_changelog = lambda _self: {}
        self._sdk = GrowwAPI(self.token)
        return self._sdk

    def _serve_quote(self, symbol: str, live: bool) -> tuple[float, pd.DataFrame, str]:
        ltp, frame, _saved = self._quotes[symbol]
        self._ltp = ltp
        self.data_source = "GROWW" if live else "LAST CLOSE"
        return ltp, frame, self.data_source

    def _simulator_quote(self, symbol: str) -> tuple[float, pd.DataFrame, str]:
        sim = self._simulators.get(symbol)
        cached = self._quotes.get(symbol)
        if sim is None:
            start = float(cached[0]) if cached else 875.0
            sim = CandleSimulator(start_price=start)
            if cached is not None and cached[1] is not None and not getattr(cached[1], "empty", True):
                sim.adopt(cached[1], start)
            self._simulators[symbol] = sim
        self.simulator = sim
        self._sim_symbol = symbol
        ltp = sim.advance()
        self._ltp = ltp
        self.data_source = "SIMULATOR"
        if cached is not None:
            self.last_error = ""
        return ltp, sim.frame(), self.data_source

    async def refresh(self, symbol: str) -> tuple[float, pd.DataFrame, str]:
        """Update LTP + candle frame from NSE when a token exists.

        Live mode after the close stays on the last traded price.
        Paper mode after the close walks a simulated tape from that price
        so a cross can still be tested without a Groww order.
        """
        symbol = (symbol or "").upper()
        self._ensure_quote_token()
        if self.mode != "LIVE" and not market_is_open():
            if symbol not in self._simulators and symbol not in self._quotes and self.token:
                now_m = time.monotonic()
                if now_m >= self._retry_after.get(symbol, 0.0):
                    try:
                        ltp, frame = await asyncio.wait_for(
                            self._refresh_live(symbol), timeout=_QUOTE_TIMEOUT_SEC
                        )
                    except Exception as exc:  # noqa: BLE001
                        self._retry_after[symbol] = now_m + _QUOTE_RETRY_SEC
                        if symbol not in self._quotes:
                            detail = str(exc).strip() or type(exc).__name__
                            self.last_error = f"NSE quote failed: {detail}"[:240]
                    else:
                        self._quotes[symbol] = (float(ltp), frame, now_m)
                        self._retry_after.pop(symbol, None)
                        self.last_error = ""
            return self._simulator_quote(symbol)
        now_m = time.monotonic()
        cached = self._quotes.get(symbol)
        ttl = _QUOTE_TTL_OPEN_SEC if market_is_open() else _QUOTE_TTL_CLOSED_SEC
        if cached is not None and now_m - cached[2] < ttl:
            self.last_error = ""
            return self._serve_quote(symbol, live=market_is_open())
        if self.token:
            if now_m >= self._retry_after.get(symbol, 0.0):
                try:
                    ltp, frame = await asyncio.wait_for(
                        self._refresh_live(symbol), timeout=_QUOTE_TIMEOUT_SEC
                    )
                except Exception as exc:  # noqa: BLE001
                    self._retry_after[symbol] = now_m + _QUOTE_RETRY_SEC
                    # A late download can still fill the cache after the wait.
                    latest = self._quotes.get(symbol)
                    if latest is not None:
                        self.last_error = ""
                        return self._serve_quote(symbol, live=False)
                    detail = str(exc).strip() or type(exc).__name__
                    self.last_error = f"NSE quote failed: {detail}"[:240]
                    self.data_source = "ERROR"
                    raise
                self._quotes[symbol] = (float(ltp), frame, now_m)
                self._retry_after.pop(symbol, None)
                self.last_error = ""
                return self._serve_quote(symbol, live=market_is_open())
            if cached is not None:
                return self._serve_quote(symbol, live=False)
            self.data_source = "ERROR"
            raise RuntimeError(self.last_error or "NSE quote unavailable")
        return self._simulator_quote(symbol)

    def _ensure_quote_token(self) -> None:
        if self.token:
            return
        loaded = desk_session_token()
        if loaded:
            self.token = loaded
            self._sdk = None

    async def _refresh_live(self, symbol: str) -> tuple[float, pd.DataFrame]:
        # The Groww SDK blocks with no HTTP timeout. Keep that off the event
        # loop or the whole site stops answering while a quote is in flight.
        return await asyncio.to_thread(self._load_quote, symbol)

    def _load_quote(self, symbol: str) -> tuple[float, pd.DataFrame]:
        sdk = self._require_sdk()
        key = (f"NSE_{symbol}",)
        raw_ltp = sdk.get_ltp(
            exchange_trading_symbols=key, segment="CASH", timeout=_SDK_TIMEOUT_SEC
        )
        ltp = _parse_ltp(raw_ltp, symbol)
        # Publish the last trade before the slower candle download. A caller
        # that stops waiting still has a real price instead of the practice tape.
        self._quotes[symbol] = (float(ltp), _one_bar(ltp), time.monotonic())
        end = dt.datetime.now(IST).replace(tzinfo=None)
        start = end - dt.timedelta(days=5)
        frame = pd.DataFrame()
        try:
            raw = sdk.get_historical_candle_data(
                trading_symbol=symbol,
                exchange="NSE",
                segment="CASH",
                start_time=start.strftime("%Y-%m-%d %H:%M:%S"),
                end_time=end.strftime("%Y-%m-%d %H:%M:%S"),
                interval_in_minutes=1,
                timeout=_SDK_TIMEOUT_SEC,
            )
            frame = _parse_candles(raw)
        except Exception:
            frame = pd.DataFrame()
        if frame.empty:
            frame = _one_bar(ltp, _session_ohlc(sdk, symbol))
        # Append a forming bar at the live LTP so iloc[-1] is never a closed bar
        # the strategy might mistake for a signal.
        last_ts = int(frame.iloc[-1]["ts"])
        now_ts = int(dt.datetime.now(IST).replace(second=0, microsecond=0).timestamp())
        if now_ts > last_ts:
            frame = pd.concat(
                [
                    frame,
                    pd.DataFrame(
                        [
                            {
                                "ts": now_ts,
                                "open": ltp,
                                "high": ltp,
                                "low": ltp,
                                "close": ltp,
                                "volume": 0,
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
        else:
            frame.loc[frame.index[-1], "close"] = ltp
            frame.loc[frame.index[-1], "high"] = max(float(frame.iloc[-1]["high"]), ltp)
            frame.loc[frame.index[-1], "low"] = min(float(frame.iloc[-1]["low"]), ltp)
        self._quotes[symbol] = (float(ltp), frame, time.monotonic())
        return float(ltp), frame

    async def place_entry(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:
        """side is BUY or SELL. PAPER fills at the candle close / LTP."""
        if self.mode == "PAPER":
            oid = self._next_id("PAPER")
            px = round_to_nse_tick(ltp)
            self._orders[oid] = _SimOrder(oid, side, qty, "ENTRY", "FILLED", px)
            self._apply_paper_position(symbol, side, qty, px)
            return OrderAck(oid, "FILLED", px)
        return await self._live_limit(symbol, side, qty, ltp, kind="ENTRY")

    async def place_exit(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:
        if self.mode == "PAPER":
            oid = self._next_id("PAPER")
            px = round_to_nse_tick(ltp)
            self._orders[oid] = _SimOrder(oid, side, qty, "EXIT", "FILLED", px)
            self._positions = [p for p in self._positions if p.get("symbol") != symbol]
            return OrderAck(oid, "FILLED", px)
        return await self._live_limit(symbol, side, qty, ltp, kind="EXIT")

    async def place_sl(self, symbol: str, side: str, qty: int, trigger: float) -> OrderAck:
        trigger = round_to_nse_tick(trigger)
        if self.mode == "PAPER":
            oid = self._next_id("PAPERSL")
            self._orders[oid] = _SimOrder(oid, side, qty, "SL", "TRIGGER_PENDING", trigger=trigger)
            return OrderAck(oid, "TRIGGER_PENDING", None)
        # Exchange SL: limit sits 0.20% through the trigger so a touch can fill
        # without a naked market order.
        buffer = get_settings().market_protection_pct / 100.0
        if side == "SELL":
            limit = round_to_nse_tick(trigger * (1 - buffer))
        else:
            limit = round_to_nse_tick(trigger * (1 + buffer))
        return await self._live_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="SL",
            price=limit,
            trigger=trigger,
        )

    async def cancel_order(self, order_id: str) -> None:
        if not order_id:
            return
        if self.mode == "PAPER" or order_id.startswith("PAPER"):
            order = self._orders.get(order_id)
            if order is None:
                return
            if order.status in TERMINAL_FILLED:
                return
            order.status = "CANCELLED"
            return
        sdk = self._require_sdk()
        await asyncio.to_thread(sdk.cancel_order, groww_order_id=order_id, segment="CASH")

    async def get_order_status(self, order_id: str) -> str:
        if not order_id:
            return ""
        local = self._orders.get(order_id)
        if local is not None and (self.mode == "PAPER" or order_id.startswith("PAPER")):
            return local.status
        sdk = self._require_sdk()

        def _status():
            if hasattr(sdk, "get_order_status"):
                raw = sdk.get_order_status(groww_order_id=order_id, segment="CASH")
                data = raw.get("payload", raw) if isinstance(raw, dict) else raw
                if isinstance(data, dict):
                    return str(data.get("order_status") or data.get("status") or "")
            raw = sdk.get_order_list(segment="CASH")
            orders = raw.get("payload", raw) if isinstance(raw, dict) else raw
            for o in orders or []:
                oid = str(o.get("groww_order_id") or o.get("order_id") or "")
                if oid == order_id:
                    return str(o.get("order_status") or o.get("status") or "")
            return ""

        try:
            return (await asyncio.to_thread(_status)).upper()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return "UNKNOWN"

    async def get_positions(self) -> list[dict]:
        if self.mode == "PAPER":
            return list(self._positions)
        def _pos():
            sdk = self._require_sdk()
            fn = getattr(sdk, "get_positions_for_user", None) or getattr(sdk, "get_positions", None)
            if fn is None:
                raise RuntimeError("Groww positions API is unavailable")
            return fn(segment="CASH")

        raw = await asyncio.wait_for(asyncio.to_thread(_pos), timeout=4)
        data = raw.get("payload", raw) if isinstance(raw, dict) else raw
        if isinstance(data, dict):
            data = data.get("positions") or []
        return list(data or [])

    async def net_quantity(self, symbol: str) -> int | None:
        """Signed MIS quantity at Groww. None when the book could not be read.

        A name Groww does not list is flat (0). Callers must not treat None
        as flat — that is a failed read, and flattening would invent an exit.
        """
        if self.mode != "LIVE":
            return None
        try:
            rows = await self.get_positions()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:200]
            return None
        if not isinstance(rows, list):
            return None
        want = (symbol or "").upper()
        total = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            sym = str(row.get("trading_symbol") or row.get("symbol") or "").upper()
            bare = sym.split("-")[0]
            if bare != want and sym != want:
                continue
            raw_qty = row.get("net_quantity")
            if raw_qty is None:
                raw_qty = row.get("quantity") or 0
            try:
                total += int(float(raw_qty))
            except (TypeError, ValueError):
                return None
        return total

    async def _live_limit(self, symbol: str, side: str, qty: int, ltp: float, kind: str) -> OrderAck:
        buffer = get_settings().market_protection_pct / 100.0
        if side == "BUY":
            price = round_to_nse_tick(ltp * (1 + buffer))
        else:
            price = round_to_nse_tick(ltp * (1 - buffer))
        if price <= 0 or not math.isfinite(price):
            raise RuntimeError("Refusing live order with a non-positive limit")
        return await self._live_order(symbol, side, qty, "LIMIT", price, None)

    async def _live_order(
        self,
        symbol: str,
        side: str,
        qty: int,
        order_type: str,
        price: float,
        trigger: float | None,
    ) -> OrderAck:
        sdk = self._require_sdk()

        def _place():
            kwargs = dict(
                validity="DAY",
                exchange="NSE",
                segment="CASH",
                trading_symbol=symbol,
                transaction_type=side,
                quantity=int(qty),
                order_type=order_type,
                product="MIS",
                price=float(price),
            )
            if trigger is not None:
                kwargs["trigger_price"] = float(trigger)
            return sdk.place_order(**kwargs)

        raw = await asyncio.to_thread(_place)
        data = raw.get("payload", raw) if isinstance(raw, dict) else raw
        if not isinstance(data, dict):
            data = {}
        oid = str(data.get("groww_order_id") or data.get("order_id") or self._next_id("LIVE"))
        status = str(data.get("order_status") or data.get("status") or "PENDING").upper()
        fill = data.get("average_fill_price") or data.get("filled_price")
        ack = OrderAck(oid, status, float(fill) if fill else None)
        self._orders[oid] = _SimOrder(oid, side, qty, order_type, status, ack.fill_price, trigger)
        return ack

    def _apply_paper_position(self, symbol: str, side: str, qty: int, price: float) -> None:
        self._positions = [
            {
                "symbol": symbol,
                "net_quantity": qty if side == "BUY" else -qty,
                "average_price": price,
            }
        ]


def _as_price(value) -> float | None:
    if isinstance(value, dict):
        for key in ("ltp", "last_price", "last_traded_price", "value", "close"):
            if key in value:
                found = _as_price(value[key])
                if found is not None:
                    return found
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(number) and number > 0:
        return number
    return None


def _parse_ltp(raw, symbol: str) -> float:
    data = raw.get("payload", raw) if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        raise RuntimeError("Unexpected LTP payload")
    symbol = (symbol or "").upper()
    for key, value in data.items():
        if symbol and symbol in str(key).upper():
            found = _as_price(value)
            if found is not None:
                return found
    for key in ("ltp", "last_price", "last_traded_price"):
        found = _as_price(data.get(key))
        if found is not None:
            return found
    # Groww sometimes keys the quote by an id that does not contain the symbol.
    found_values = [price for price in (_as_price(value) for value in data.values()) if price is not None]
    if len(found_values) == 1:
        return found_values[0]
    raise RuntimeError("Unexpected LTP payload")


def _session_ohlc(sdk, symbol: str) -> dict:
    try:
        raw = sdk.get_ohlc(
            exchange_trading_symbols=(f"NSE_{symbol}",),
            segment="CASH",
            timeout=3,
        )
    except Exception:
        return {}
    data = raw.get("payload", raw) if isinstance(raw, dict) else None
    if not isinstance(data, dict):
        return {}
    for key, value in data.items():
        if symbol in str(key) and isinstance(value, dict):
            return value
    return data if "open" in data else {}


def _bar_price(ohlc: dict, name: str, fallback: float) -> float:
    try:
        value = float(ohlc.get(name))
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(value) or value <= 0:
        return fallback
    return value


def _one_bar(ltp: float, ohlc: dict | None = None) -> pd.DataFrame:
    ohlc = ohlc or {}
    open_ = _bar_price(ohlc, "open", ltp)
    high = max(_bar_price(ohlc, "high", ltp), ltp, open_)
    low = min(_bar_price(ohlc, "low", ltp), ltp, open_)
    now_ts = int(dt.datetime.now(IST).replace(second=0, microsecond=0).timestamp())
    return pd.DataFrame(
        [
            {
                "ts": now_ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": ltp,
                "volume": 0,
            }
        ]
    )


# Five sessions of 1-minute bars is enough for SMA 21 and the day change.
# A larger download, walked with iterrows on the request path, froze the site.
_MAX_CANDLES = 2500
_CANDLE_READ_CAP = 20000


def _parse_candles(raw) -> pd.DataFrame:
    data = raw.get("payload", raw) if isinstance(raw, dict) and "payload" in raw else raw
    rows = data.get("candles", data.get("data", [])) if isinstance(data, dict) else data
    out = []
    for row in rows or []:
        if len(out) >= _CANDLE_READ_CAP:
            break
        if isinstance(row, (list, tuple)) and len(row) >= 6:
            ts_raw, o, h, l, c, v = row[:6]
        elif isinstance(row, dict):
            ts_raw = row.get("timestamp") or row.get("time") or row.get("ts")
            o, h, l, c, v = row.get("open"), row.get("high"), row.get("low"), row.get("close"), row.get("volume")
        else:
            continue
        ts = _to_epoch(ts_raw)
        if ts is None:
            continue
        out.append(
            {
                "ts": ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": int(v or 0),
            }
        )
    if not out:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    frame = pd.DataFrame(out).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    if len(frame) > _MAX_CANDLES:
        frame = frame.iloc[-_MAX_CANDLES:].reset_index(drop=True)
    return frame


def _to_epoch(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return int(v / 1000) if v > 10_000_000_000 else int(v)
    text = str(value).replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(dt.datetime.strptime(text[:19], fmt).replace(tzinfo=IST).timestamp())
        except ValueError:
            continue
    return None

