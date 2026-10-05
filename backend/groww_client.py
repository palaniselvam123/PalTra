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
from tick_sizes import round_price

IST = ZoneInfo("Asia/Kolkata")
# A closed session does not need a new candle download every second.
_QUOTE_TTL_OPEN_SEC = 3.0
_QUOTE_TTL_CLOSED_SEC = 60.0
_QUOTE_TIMEOUT_SEC = 8.0
_QUOTE_RETRY_SEC = 20.0
_SDK_TIMEOUT_SEC = 6
# A full candle download on every price check was holding the bot for seconds
# and letting the displayed price drift from Groww. History is refreshed once
# a minute. The last trade is fetched on the normal quote interval.
_CANDLE_REFRESH_SEC = 55.0
_CANDLE_MIN_BARS = 30
_DESK_TOKEN_TTL_SEC = 30.0
_desk_token_cache: tuple[float, str] | None = None
_sma_broker: "GrowwClient | None" = None

# Statuses that mean "do not place another order yet".
IN_FLIGHT = frozenset({"PENDING", "TRANSIT", "NEW", "OPEN", "PLACED"})
TERMINAL_CANCELLED = frozenset({"CANCELLED", "CANCELED", "REJECTED", "EXPIRED"})
# Groww's own word for a filled order is EXECUTED. Missing it left a long
# on the terminal after the exchange stop had already sold the shares.
# TRIGGERED is not here: a triggered stop has only become a working limit
# order. Reading it as a fill booked a position Groww did not hold.
TERMINAL_FILLED = frozenset({"FILLED", "COMPLETE", "COMPLETED", "EXECUTED", "DELIVERY_AWAITED"})
_DEAD_ORDER = TERMINAL_CANCELLED | {"REJECTED", "FAILED", "FAILURE"}


class GrowwOrderRejected(RuntimeError):
    """Groww did not accept the order. Nothing should be booked locally."""


def _status_and_price(data: dict) -> tuple[str, float | None]:
    status = str(data.get("order_status") or data.get("status") or "")
    raw_price = data.get("average_fill_price") or data.get("filled_price")
    try:
        price = float(raw_price) if raw_price not in (None, "", 0, 0.0) else None
    except (TypeError, ValueError):
        price = None
    return status, price


def _remark(data) -> str:
    """Groww's own words on an order (why it was rejected, for example)."""
    if not isinstance(data, dict):
        return ""
    for key in ("remark", "rejection_reason", "reason", "message"):
        text = str(data.get(key) or "").strip()
        if text:
            return text
    return ""


def _order_ack_from_response(raw) -> OrderAck:
    """A Groww place_order body. A refusal or a missing id is not an order.

    Inventing a local id and calling it PENDING booked a position the exchange
    never had. That resting DAY order could then fill hours later.
    """
    if not isinstance(raw, dict):
        raise GrowwOrderRejected("Groww returned no order")
    if str(raw.get("status") or "").upper() == "FAILURE":
        err = raw.get("error")
        message = ""
        if isinstance(err, dict):
            message = str(err.get("message") or err.get("code") or "")
        elif err:
            message = str(err)
        raise GrowwOrderRejected(message or "Groww refused the order")
    data = raw.get("payload", raw) if "payload" in raw else raw
    if not isinstance(data, dict):
        data = {}
    remark = str(data.get("remark") or data.get("message") or "")
    order_id = str(data.get("groww_order_id") or data.get("order_id") or "").strip()
    status = str(data.get("order_status") or data.get("status") or "").upper()
    if not order_id:
        raise GrowwOrderRejected(remark or "Groww did not accept the order")
    if status in _DEAD_ORDER:
        raise GrowwOrderRejected(remark or f"Groww {status} the order")
    _status, price = _status_and_price(data)
    return OrderAck(order_id, status or "PENDING", price, remark)


def _env_access_token() -> str:
    return (get_settings().groww_access_token or "").strip()


def preferred_quote_token() -> str:
    """Desk login first. The Fly secret is only a fallback.

    Connect Live Data writes a fresh access token. A copy stored as
    GROWW_ACCESS_TOKEN goes stale and was being sent instead, so Groww
    answered 401 while Settings still showed the session as active.
    """
    desk = desk_session_token().strip()
    return desk or _env_access_token()


def note_fresh_desk_token() -> None:
    """Settings just saved a new access token. Quotes must use it now."""
    global _desk_token_cache
    _desk_token_cache = None
    if _sma_broker is not None:
        _sma_broker._rejected.clear()
        _sma_broker.adopt_saved_session(force=True)


def _is_auth_error(exc: BaseException) -> bool:
    if type(exc).__name__ == "GrowwAPIAuthenticationException":
        return True
    text = str(exc).lower()
    return "authentication failed" in text or "token has either expired" in text


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
        self._cum = 0
        self._cum_day = None
        self._seed(120)

    def _cumulative(self, ts: int, traded: int) -> int:
        """Session running total. `volume` on these candles matches Groww."""
        day = dt.datetime.fromtimestamp(int(ts), tz=IST).date()
        if self._cum_day != day:
            self._cum_day = day
            self._cum = 0
        self._cum += max(0, int(traded))
        return self._cum

    def _seed(self, n: int) -> None:
        anchor = self.price
        price = self.price
        # Steps were sized for an 875 stock. Scale them so a 40 stock does
        # not swing 10% and a 3,000 stock does not sit still.
        scale = max(anchor, 1.0) / 875.0
        start = dt.datetime.now(IST) - dt.timedelta(minutes=n)
        start = start.replace(second=0, microsecond=0)
        for i in range(n):
            # Drift up, then roll over, so SMA9/SMA21 are populated and a
            # historical cross exists for the chart markers.
            drift = (0.15 if i < int(n * 0.65) else -0.22) * scale
            shock = self._rng.uniform(-0.6, 0.6) * scale
            o = price
            c = max(1.0, price + drift + shock)
            h = max(o, c) + abs(shock) * 0.4
            l = min(o, c) - abs(shock) * 0.3
            ts = start + dt.timedelta(minutes=i)
            traded = self._rng.randint(5_000, 40_000)
            self.candles.append(
                {
                    "ts": int(ts.timestamp()),
                    "open": round(o, 2),
                    "high": round(h, 2),
                    "low": round(l, 2),
                    "close": round(c, 2),
                    "volume": self._cumulative(int(ts.timestamp()), traded),
                }
            )
            price = c
        # Shift the history so it ends on the start price. The tape then
        # continues from this stock's own price, not wherever the walk wandered.
        shift = anchor - price
        for bar in self.candles:
            for key in ("open", "high", "low", "close"):
                bar[key] = round(max(0.05, bar[key] + shift), 2)
        self.price = anchor
        # Forming bar on top of the closed history. Its cumulative volume
        # matches the previous candle until this minute actually trades.
        now = dt.datetime.now(IST).replace(second=0, microsecond=0)
        self._minute_key = now.strftime("%Y-%m-%d %H:%M")
        self.candles.append(
            {
                "ts": int(now.timestamp()),
                "open": round(self.price, 2),
                "high": round(self.price, 2),
                "low": round(self.price, 2),
                "close": round(self.price, 2),
                "volume": self._cumulative(int(now.timestamp()), 0),
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
            ts = int(now.replace(second=0, microsecond=0).timestamp())
            self.candles.append(
                {
                    "ts": ts,
                    "open": px,
                    "high": px,
                    "low": px,
                    "close": px,
                    "volume": self._cumulative(ts, 0),
                }
            )
        else:
            bar["close"] = px
            bar["high"] = round(max(bar["high"], px), 2)
            bar["low"] = round(min(bar["low"], px), 2)
            bar["volume"] = self._cumulative(int(bar["ts"]), self._rng.randint(10, 80))
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
        # Adopted Groww frames already store the session running total.
        self._cum = int(float(rows[-1].get("volume") or 0))
        self._cum_day = last.date()

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.candles)


class GrowwClient:
    def __init__(self, mode: str = "PAPER", token: str = ""):
        global _sma_broker
        self.mode = "LIVE" if (mode or "").upper() == "LIVE" else "PAPER"
        self.token = (token or "").strip()
        self._rejected: set[str] = set()
        # Groww's remark per order id, kept so a rejection can say why.
        self.order_remarks: dict[str, str] = {}
        self._using_saved = False
        self.simulator = CandleSimulator()
        self.data_source = "SIMULATOR"
        self.last_error = ""
        self._orders: dict[str, _SimOrder] = {}
        self._seq = 0
        self._ltp = self.simulator.price
        self._sdk = None
        self._positions: list[dict] = []
        self._quotes: dict[str, tuple[float, pd.DataFrame, float]] = {}
        self._candle_frames: dict[str, pd.DataFrame] = {}
        self._candle_at: dict[str, float] = {}
        self._retry_after: dict[str, float] = {}
        self._simulators: dict[str, CandleSimulator] = {}
        # A stock's own price to start a practice tape from when Groww has
        # not quoted it, such as the entry of a position held over a restart.
        self._anchors: dict[str, float] = {}
        # Tapes that started at the demo price because nothing was known.
        self._unanchored: set[str] = set()
        self._sim_symbol = ""
        _sma_broker = self
        self.adopt_saved_session()

    def set_mode(self, mode: str, token: str | None = None) -> None:
        self.mode = "LIVE" if mode.upper() == "LIVE" else "PAPER"
        # Boot passes the empty env token. That must not wipe the desk session.
        if token and token != self.token:
            self._sdk = None
            self.token = token
            self._using_saved = False
        self.adopt_saved_session()

    def adopt_saved_session(self, *, force: bool = False) -> None:
        """Follow the desk access token whenever this client is not pinned.

        A token passed in by a test stays put. The Fly secret does not: it
        loses to the session Settings saved.
        """
        desk = desk_session_token().strip()
        env = _env_access_token()
        chosen = ""
        for candidate in (desk, env):
            if candidate and candidate not in self._rejected:
                chosen = candidate
                break
        if not chosen:
            return
        follows_saved = force or self._using_saved or not self.token or self.token == env
        if not follows_saved:
            return
        if chosen != self.token:
            self._sdk = None
            self.token = chosen
        self._using_saved = True

    def _recover_from_auth_failure(self) -> bool:
        if self.token:
            self._rejected.add(self.token)
        self.token = ""
        self._sdk = None
        self._using_saved = True
        self.adopt_saved_session(force=True)
        return bool(self.token)

    def _next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:06d}"

    def _require_sdk(self):
        self.adopt_saved_session()
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

    def anchor_price(self, symbol: str, price: float | None) -> None:
        """Start this stock's practice tape from its own price.

        Every tape used to start at 875, so after a restart or without a
        Groww session every stock was priced near 875, and a square-off
        closed them all at that one price.
        """
        symbol = (symbol or "").upper()
        try:
            value = float(price or 0)
        except (TypeError, ValueError):
            return
        if not symbol or not math.isfinite(value) or value <= 0:
            return
        self._anchors[symbol] = value
        if symbol in self._unanchored:
            # Its tape is still walking from the demo price. Drop it.
            self._simulators.pop(symbol, None)
            self._unanchored.discard(symbol)

    def _simulator_quote(self, symbol: str) -> tuple[float, pd.DataFrame, str]:
        sim = self._simulators.get(symbol)
        cached = self._quotes.get(symbol)
        if sim is None:
            if cached:
                start = float(cached[0])
            elif symbol in self._anchors:
                start = self._anchors[symbol]
            else:
                start = 875.0
                self._unanchored.add(symbol)
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
                        ltp, frame = await self._fetch_live(symbol)
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
                    ltp, frame = await self._fetch_live(symbol)
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
        self.adopt_saved_session()

    async def _fetch_live(self, symbol: str) -> tuple[float, pd.DataFrame]:
        try:
            return await asyncio.wait_for(self._refresh_live(symbol), timeout=_QUOTE_TIMEOUT_SEC)
        except Exception as exc:
            if not _is_auth_error(exc) or not self._recover_from_auth_failure():
                raise
            return await asyncio.wait_for(self._refresh_live(symbol), timeout=_QUOTE_TIMEOUT_SEC)

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
        held = self._candle_frames.get(symbol)
        if (
            held is not None
            and len(held) >= _CANDLE_MIN_BARS
            and time.monotonic() - self._candle_at.get(symbol, 0.0) < _CANDLE_REFRESH_SEC
        ):
            frame = _apply_ltp(held, ltp)
            self._candle_frames[symbol] = frame
            self._quotes[symbol] = (float(ltp), frame, time.monotonic())
            return float(ltp), frame
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
        frame = _apply_ltp(frame, ltp)
        if len(frame) >= _CANDLE_MIN_BARS:
            self._candle_frames[symbol] = frame
            self._candle_at[symbol] = time.monotonic()
        self._quotes[symbol] = (float(ltp), frame, time.monotonic())
        return float(ltp), frame

    async def place_entry(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:
        """side is BUY or SELL. PAPER fills at the candle close / LTP."""
        if self.mode == "PAPER":
            oid = self._next_id("PAPER")
            px = round_price(symbol, ltp)
            self._orders[oid] = _SimOrder(oid, side, qty, "ENTRY", "FILLED", px)
            self._apply_paper_position(symbol, side, qty, px)
            return OrderAck(oid, "FILLED", px)
        return await self._live_limit(symbol, side, qty, ltp, kind="ENTRY")

    async def place_exit(self, symbol: str, side: str, qty: int, ltp: float) -> OrderAck:
        if self.mode == "PAPER":
            oid = self._next_id("PAPER")
            px = round_price(symbol, ltp)
            self._orders[oid] = _SimOrder(oid, side, qty, "EXIT", "FILLED", px)
            self._positions = [p for p in self._positions if p.get("symbol") != symbol]
            return OrderAck(oid, "FILLED", px)
        return await self._live_limit(symbol, side, qty, ltp, kind="EXIT")

    async def place_sl(self, symbol: str, side: str, qty: int, trigger: float) -> OrderAck:
        trigger = round_price(symbol, trigger)
        if self.mode == "PAPER":
            oid = self._next_id("PAPERSL")
            self._orders[oid] = _SimOrder(oid, side, qty, "SL", "TRIGGER_PENDING", trigger=trigger)
            return OrderAck(oid, "TRIGGER_PENDING", None)
        # Exchange SL: limit sits 0.20% through the trigger so a touch can fill
        # without a naked market order.
        buffer = get_settings().market_protection_pct / 100.0
        if side == "SELL":
            limit = round_price(symbol, trigger * (1 - buffer))
        else:
            limit = round_price(symbol, trigger * (1 + buffer))
        return await self._live_order(
            symbol=symbol,
            side=side,
            qty=qty,
            order_type="SL",
            price=limit,
            trigger=trigger,
        )

    async def modify_sl(self, order_id: str, symbol: str, side: str, qty: int, trigger: float) -> None:
        """Move an open exchange stop to a new trigger (trailing stop).

        Groww modifies the order in place, so the position is never without a
        stop. Raises when Groww refuses: the old stop is then still live.
        """
        trigger = round_price(symbol, trigger)
        if self.mode == "PAPER" or order_id.startswith("PAPER"):
            order = self._orders.get(order_id)
            if order is not None and order.status not in TERMINAL_FILLED:
                order.trigger = trigger
            return
        if not order_id:
            raise RuntimeError("No exchange stop id to modify")
        buffer = get_settings().market_protection_pct / 100.0
        if side == "SELL":
            limit = round_price(symbol, trigger * (1 - buffer))
        else:
            limit = round_price(symbol, trigger * (1 + buffer))
        sdk = self._require_sdk()
        await asyncio.to_thread(
            sdk.modify_order,
            order_type="SL",
            segment="CASH",
            groww_order_id=order_id,
            quantity=int(qty),
            price=float(limit),
            trigger_price=float(trigger),
        )
        order = self._orders.get(order_id)
        if order is not None:
            order.trigger = trigger

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
        status, _price = await self.read_order(order_id)
        return status

    async def order_remark(self, order_id: str) -> str:
        """Why Groww rejected (or cancelled) an order, in Groww's words.

        Read-only: one get_order_detail call, cached. Empty when Groww gave
        no remark or the read failed; it never raises.
        """
        if not order_id:
            return ""
        if self.order_remarks.get(order_id):
            return self.order_remarks[order_id]
        if self.mode == "PAPER" or order_id.startswith("PAPER"):
            return ""
        try:
            sdk = self._require_sdk()
            if not hasattr(sdk, "get_order_detail"):
                return ""
            raw = await asyncio.to_thread(sdk.get_order_detail, segment="CASH", groww_order_id=order_id)
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return ""
        data = raw.get("payload", raw) if isinstance(raw, dict) else raw
        text = _remark(data)
        if text:
            self.order_remarks[order_id] = text
        return text

    async def read_order(self, order_id: str) -> tuple[str, float | None]:
        """Groww status and average fill. ('', None) when the id is blank.

        UNKNOWN means the read failed. Callers must not treat that as flat
        or as a fill.
        """
        if not order_id:
            return "", None
        local = self._orders.get(order_id)
        if local is not None and (self.mode == "PAPER" or order_id.startswith("PAPER")):
            return local.status, local.fill_price
        sdk = self._require_sdk()

        def _read():
            if hasattr(sdk, "get_order_status"):
                raw = sdk.get_order_status(groww_order_id=order_id, segment="CASH")
                data = raw.get("payload", raw) if isinstance(raw, dict) else raw
                if isinstance(data, dict):
                    if _remark(data):
                        self.order_remarks[order_id] = _remark(data)
                    return _status_and_price(data)
            raw = sdk.get_order_list(segment="CASH")
            orders = raw.get("payload", raw) if isinstance(raw, dict) else raw
            for o in orders or []:
                oid = str(o.get("groww_order_id") or o.get("order_id") or "")
                if oid == order_id:
                    if _remark(o):
                        self.order_remarks[order_id] = _remark(o)
                    return _status_and_price(o)
            return "", None

        try:
            status, price = await asyncio.to_thread(_read)
            return status.upper(), price
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)
            return "UNKNOWN", None

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
            price = round_price(symbol, ltp * (1 + buffer))
        else:
            price = round_price(symbol, ltp * (1 - buffer))
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
        ack = _order_ack_from_response(raw)
        self._orders[ack.order_id] = _SimOrder(
            ack.order_id, side, qty, order_type, ack.status, ack.fill_price, trigger
        )
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


def _apply_ltp(frame: pd.DataFrame, ltp: float) -> pd.DataFrame:
    """Keep the forming bar on the live last trade. Closed bars stay put."""
    if frame is None or getattr(frame, "empty", True):
        return _one_bar(ltp)
    frame = frame.copy()
    last_ts = int(frame.iloc[-1]["ts"])
    now_ts = int(dt.datetime.now(IST).replace(second=0, microsecond=0).timestamp())
    if now_ts > last_ts:
        # No shares have printed on the new minute yet, so the running
        # total stays where the previous candle left it. A zero here would
        # look like the cumulative counter fell.
        carried = int(float(frame.iloc[-1]["volume"] or 0))
        return pd.concat(
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
                            "volume": carried,
                        }
                    ]
                ),
            ],
            ignore_index=True,
        )
    frame.loc[frame.index[-1], "close"] = ltp
    frame.loc[frame.index[-1], "high"] = max(float(frame.iloc[-1]["high"]), ltp)
    frame.loc[frame.index[-1], "low"] = min(float(frame.iloc[-1]["low"]), ltp)
    return frame


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


def _parse_candles(raw, limit: int | None = _MAX_CANDLES) -> pd.DataFrame:
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
    if limit is not None and len(frame) > limit:
        frame = frame.iloc[-limit:].reset_index(drop=True)
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

