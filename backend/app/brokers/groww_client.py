"""Groww adapter, written against the real `growwapi` SDK surface.

Verified signatures (growwapi as installed in this venv):
    GrowwAPI.get_access_token(api_key, totp=None, secret=None) -> dict   [static]
    GrowwAPI(token)
    .get_ltp(exchange_trading_symbols: tuple[str], segment: str) -> dict
    .get_quote(trading_symbol: str, exchange: str, segment: str) -> dict
    .get_historical_candle_data(trading_symbol, exchange, segment,
                                start_time, end_time, interval_in_minutes) -> dict
    .place_order(validity, exchange, order_type, product, quantity,
                 segment, trading_symbol, transaction_type, ...) -> dict

The SDK is synchronous (requests-based), so every call is dispatched with
`asyncio.to_thread` — calling it inline would block the event loop and stall
the tick stream for every connected client.

Run `GET /api/marketdata/probe` if your installed version differs; only this
file needs to change.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pyotp

from app.brokers.base import (
    BrokerAuthError,
    BrokerClient,
    BrokerOrderError,
    OrderRequest,
    OrderResult,
    Quote,
)

# Keys the token response might use, in preference order.
_TOKEN_KEYS = ("token", "access_token", "accessToken", "authToken", "auth_token")


def _extract_token(payload) -> str:
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in _TOKEN_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        raise BrokerAuthError(
            f"Groww returned a token response with unexpected keys: {sorted(payload)}. "
            "Map the correct key in groww_client._TOKEN_KEYS."
        )
    raise BrokerAuthError(f"Unexpected token response type from Groww: {type(payload).__name__}")


class BrokerDataForbidden(BrokerOrderError):
    """Raised when the session authenticates but market data is not entitled."""


_FORBIDDEN_HINT = (
    "Groww rejected the market-data request as forbidden. The key authenticates fine "
    "(account endpoints work), so this is an entitlement problem, not a credential one: "
    "your Groww API subscription does not include market data. Enable the market-data / "
    "Live Data add-on in Groww's API portal, then reconnect. "
    "Run GET /api/marketdata/diagnose to re-check."
)


def _is_forbidden(exc: Exception) -> bool:
    return "forbidden" in str(exc).lower() or "403" in str(exc)


def _pick(data: dict, *names, default=None):
    for n in names:
        if isinstance(data, dict) and data.get(n) is not None:
            return data[n]
    return default


def _to_epoch_seconds(value) -> int | None:
    """Groww returns candle timestamps as epoch seconds, epoch milliseconds,
    or an ISO string depending on endpoint and SDK version. Normalise all
    three rather than assuming one shape.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = int(value)
        # Anything past ~year 2286 in seconds is really milliseconds.
        return v // 1000 if v > 10_000_000_000 else v
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return _to_epoch_seconds(int(text))
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return int(dt.datetime.strptime(text, fmt).timestamp())
            except ValueError:
                continue
    return None


class GrowwClient(BrokerClient):
    name = "groww"

    EXCHANGE = "NSE"
    SEGMENT = "CASH"

    def __init__(self):
        self._sdk = None
        self._access_token: str | None = None
        self._token_expires_at: dt.datetime | None = None

    # ---- auth ---------------------------------------------------------

    async def login(self, api_key: str, api_secret: str, totp_secret: str) -> str:
        """Groww supports two API-key styles. A TOTP key authenticates with
        api_key + a current TOTP code; an approval key uses api_key + secret.
        Whichever credential is present is used.
        """
        try:
            from growwapi import GrowwAPI
        except ImportError as exc:
            raise BrokerAuthError(
                "growwapi is not installed. Run `pip install growwapi` in the backend venv."
            ) from exc

        kwargs: dict = {"api_key": api_key}
        if totp_secret:
            try:
                kwargs["totp"] = pyotp.TOTP(totp_secret).now()
            except Exception as exc:  # noqa: BLE001
                raise BrokerAuthError(f"Could not generate a TOTP code from the stored secret: {exc}") from exc
        elif api_secret:
            kwargs["secret"] = api_secret
        else:
            raise BrokerAuthError("Provide either a TOTP secret or an API secret for Groww login.")

        try:
            response = await asyncio.to_thread(GrowwAPI.get_access_token, **kwargs)
        except Exception as exc:  # noqa: BLE001
            raise BrokerAuthError(f"Groww access-token request failed: {exc}") from exc

        token = _extract_token(response)

        try:
            self._sdk = await asyncio.to_thread(GrowwAPI, token)
        except Exception as exc:  # noqa: BLE001
            raise BrokerAuthError(f"Could not initialise Groww session: {exc}") from exc

        self._access_token = token
        # Groww daily tokens expire end-of-day; re-run login each morning.
        self._token_expires_at = dt.datetime.combine(dt.date.today(), dt.time(23, 59))
        return token

    async def restore_session(self, token: str, expires_at: dt.datetime) -> None:
        """Rebuilds the SDK from an access token already on disk.

        Groww tokens are valid for the whole trading day, but the session
        object lived only in memory — so restarting the backend forced a fresh
        TOTP login even though a perfectly good token was sitting in the
        database. The SDK only needs the token, so restoring costs nothing.
        """
        try:
            from growwapi import GrowwAPI
        except ImportError as exc:
            raise BrokerAuthError(
                "growwapi is not installed. Run `pip install growwapi` in the backend venv."
            ) from exc

        try:
            self._sdk = await asyncio.to_thread(GrowwAPI, token)
        except Exception as exc:  # noqa: BLE001
            raise BrokerAuthError(f"Could not restore the Groww session from the stored token: {exc}") from exc

        self._access_token = token
        self._token_expires_at = expires_at

    async def is_token_valid(self) -> bool:
        return bool(self._access_token) and dt.datetime.now() < (self._token_expires_at or dt.datetime.min)

    def _require_session(self):
        if not self._sdk or not self._access_token:
            raise BrokerAuthError("Not logged in to Groww. Run the login step in Settings first.")
        return self._sdk

    # ---- market data --------------------------------------------------

    async def get_ltp_batch(self, symbols: list[str]) -> dict[str, float]:
        sdk = self._require_session()
        keys = tuple(f"{self.EXCHANGE}_{s}" for s in symbols)
        try:
            raw = await asyncio.to_thread(sdk.get_ltp, exchange_trading_symbols=keys, segment=self.SEGMENT)
        except Exception as exc:  # noqa: BLE001
            if _is_forbidden(exc):
                raise BrokerDataForbidden(_FORBIDDEN_HINT) from exc
            raise BrokerOrderError(f"Groww LTP fetch failed: {exc}") from exc

        if not isinstance(raw, dict):
            raise BrokerOrderError(f"Unexpected LTP payload from Groww: {type(raw).__name__}")

        payload = raw.get("payload", raw) if "payload" in raw else raw
        out: dict[str, float] = {}
        for symbol, key in zip(symbols, keys):
            value = payload.get(key, payload.get(symbol))
            if value is None:
                continue
            if isinstance(value, dict):
                value = _pick(value, "ltp", "last_price", "value")
            try:
                out[symbol] = float(value)
            except (TypeError, ValueError):
                continue
        return out

    async def get_full_quote(self, symbol: str) -> Quote:
        """Quote including bid/ask depth, used by the spread guard."""
        sdk = self._require_session()
        try:
            raw = await asyncio.to_thread(
                sdk.get_quote, trading_symbol=symbol, exchange=self.EXCHANGE, segment=self.SEGMENT
            )
        except Exception as exc:  # noqa: BLE001
            if _is_forbidden(exc):
                raise BrokerDataForbidden(_FORBIDDEN_HINT) from exc
            raise BrokerOrderError(f"Groww quote fetch failed for {symbol}: {exc}") from exc

        data = raw.get("payload", raw) if isinstance(raw, dict) and "payload" in raw else raw
        return self._parse_quote(symbol, data or {})

    @staticmethod
    def _parse_quote(symbol: str, data: dict) -> Quote:
        ltp = float(_pick(data, "last_price", "ltp", "last_traded_price", "close", default=0.0) or 0.0)

        bid = ask = 0.0
        depth = _pick(data, "depth", "market_depth", default=None)
        if isinstance(depth, dict):
            buys, sells = depth.get("buy") or [], depth.get("sell") or []
            if buys:
                bid = float(_pick(buys[0], "price", default=0.0) or 0.0)
            if sells:
                ask = float(_pick(sells[0], "price", default=0.0) or 0.0)
        if not bid:
            bid = float(_pick(data, "bid_price", "bid", default=0.0) or 0.0)
        if not ask:
            ask = float(_pick(data, "offer_price", "ask", "offer", default=0.0) or 0.0)

        # Falling back to LTP makes the spread look like zero, which would
        # silently disable the spread guard, so callers treat bid==ask==ltp
        # as "no depth available".
        bid = bid or ltp
        ask = ask or ltp
        volume = int(float(_pick(data, "volume", "day_volume", "total_traded_volume", default=0) or 0))
        return Quote(symbol=symbol, ltp=ltp, bid=bid, ask=ask, volume=volume)

    async def get_daily_volumes(self, symbol: str, days: int = 20) -> list[int]:
        """Daily volumes for the last `days` sessions — the basis for a real
        20-day RVOL instead of a cross-sectional stand-in.
        """
        sdk = self._require_session()
        end = dt.datetime.now()
        start = end - dt.timedelta(days=days * 2)  # padding for weekends/holidays
        fmt = "%Y-%m-%d %H:%M:%S"
        try:
            raw = await asyncio.to_thread(
                sdk.get_historical_candle_data,
                trading_symbol=symbol,
                exchange=self.EXCHANGE,
                segment=self.SEGMENT,
                start_time=start.strftime(fmt),
                end_time=end.strftime(fmt),
                interval_in_minutes=1440,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrokerOrderError(f"Historical data fetch failed for {symbol}: {exc}") from exc

        data = raw.get("payload", raw) if isinstance(raw, dict) and "payload" in raw else raw
        candles = _pick(data, "candles", "data", default=[]) if isinstance(data, dict) else data
        volumes: list[int] = []
        for c in candles or []:
            # Candles come back as [ts, o, h, l, c, v] or as dicts.
            if isinstance(c, (list, tuple)) and len(c) >= 6:
                volumes.append(int(c[5]))
            elif isinstance(c, dict):
                v = _pick(c, "volume", "v")
                if v is not None:
                    volumes.append(int(v))
        return volumes[-days:]

    async def get_candles_window(
        self, symbol: str, interval_minutes: int, start: dt.datetime, end: dt.datetime
    ) -> list[tuple[int, float, float, float, float, int]]:
        """Historical bars for an EXPLICIT window.

        `get_candles` only looks back from now, which is all the chart backfill
        needs but makes older history unreachable — and Groww caps how wide a
        single request may be, so deep history has to be walked window by
        window. Added for the research ingester; `get_candles` is unchanged and
        now delegates here so both paths share one parser.
        """
        return await self._historical(symbol, interval_minutes, start, end)

    async def get_candles(
        self, symbol: str, interval_minutes: int, days: int = 5
    ) -> list[tuple[int, float, float, float, float, int]]:
        """Historical OHLCV bars as (epoch_seconds, o, h, l, c, v).

        Backfills the chart so it opens with real history rather than drawing
        itself from scratch as ticks arrive. Timestamps come back either as
        epoch numbers or ISO strings depending on the endpoint, so both are
        handled — guessing one and silently dropping the other would leave the
        chart mysteriously empty.
        """
        end = dt.datetime.now()
        return await self._historical(symbol, interval_minutes, end - dt.timedelta(days=days), end)

    async def _historical(
        self, symbol: str, interval_minutes: int, start: dt.datetime, end: dt.datetime
    ) -> list[tuple[int, float, float, float, float, int]]:
        sdk = self._require_session()
        fmt = "%Y-%m-%d %H:%M:%S"
        try:
            raw = await asyncio.to_thread(
                sdk.get_historical_candle_data,
                trading_symbol=symbol,
                exchange=self.EXCHANGE,
                segment=self.SEGMENT,
                start_time=start.strftime(fmt),
                end_time=end.strftime(fmt),
                interval_in_minutes=interval_minutes,
            )
        except Exception as exc:  # noqa: BLE001
            if _is_forbidden(exc):
                raise BrokerDataForbidden(_FORBIDDEN_HINT) from exc
            raise BrokerOrderError(f"Historical candles failed for {symbol}: {exc}") from exc

        data = raw.get("payload", raw) if isinstance(raw, dict) and "payload" in raw else raw
        rows = _pick(data, "candles", "data", default=[]) if isinstance(data, dict) else data

        out: list[tuple[int, float, float, float, float, int]] = []
        for row in rows or []:
            if isinstance(row, (list, tuple)) and len(row) >= 6:
                ts_raw, o, h, l, c, v = row[0], row[1], row[2], row[3], row[4], row[5]
            elif isinstance(row, dict):
                ts_raw = _pick(row, "timestamp", "time", "start_time", "ts")
                o = _pick(row, "open", "o")
                h = _pick(row, "high", "h")
                l = _pick(row, "low", "l")
                c = _pick(row, "close", "c")
                v = _pick(row, "volume", "v")
            else:
                continue
            ts = _to_epoch_seconds(ts_raw)
            if ts is None or None in (o, h, l, c):
                continue
            try:
                out.append((ts, float(o), float(h), float(l), float(c), int(v or 0)))
            except (TypeError, ValueError):
                continue

        out.sort(key=lambda r: r[0])
        return out

    async def get_quote(self, symbol: str) -> Quote:
        return await self.get_full_quote(symbol)

    # ---- orders (live dispatch stays disabled at the app layer) --------

    async def place_order(self, order: OrderRequest) -> OrderResult:
        sdk = self._require_session()
        try:
            resp = await asyncio.to_thread(
                sdk.place_order,
                validity="DAY",
                exchange=self.EXCHANGE,
                segment=self.SEGMENT,
                trading_symbol=order.symbol,
                transaction_type=order.side,
                quantity=order.quantity,
                order_type=order.order_type,
                product="MIS",
                price=order.price or 0.0,
                trigger_price=order.trigger_price,
            )
        except Exception as exc:  # noqa: BLE001
            raise BrokerOrderError(f"Order placement failed for {order.symbol}: {exc}") from exc

        data = resp.get("payload", resp) if isinstance(resp, dict) and "payload" in resp else resp
        return OrderResult(
            broker_order_id=str(_pick(data, "groww_order_id", "order_id", "orderId", default="")),
            status=str(_pick(data, "order_status", "status", default="PLACED")),
            filled_price=_pick(data, "average_fill_price", "filled_price"),
        )

    async def cancel_order(self, broker_order_id: str) -> None:
        sdk = self._require_session()
        try:
            await asyncio.to_thread(sdk.cancel_order, groww_order_id=broker_order_id, segment=self.SEGMENT)
        except Exception as exc:  # noqa: BLE001
            raise BrokerOrderError(f"Cancel failed for order {broker_order_id}: {exc}") from exc

    async def cancel_all_open_orders(self) -> None:
        sdk = self._require_session()
        try:
            raw = await asyncio.to_thread(sdk.get_order_list, segment=self.SEGMENT)
        except Exception as exc:  # noqa: BLE001
            raise BrokerOrderError(f"Could not list orders: {exc}") from exc
        orders = raw.get("payload", raw) if isinstance(raw, dict) else raw
        for o in (orders or []):
            if str(_pick(o, "order_status", "status", default="")).upper() in ("OPEN", "NEW", "TRIGGER_PENDING"):
                await self.cancel_order(str(_pick(o, "groww_order_id", "order_id", default="")))

    async def square_off_all(self) -> list[OrderResult]:
        sdk = self._require_session()
        try:
            raw = await asyncio.to_thread(sdk.get_positions_for_user, segment=self.SEGMENT)
        except Exception as exc:  # noqa: BLE001
            raise BrokerOrderError(f"Could not fetch positions for square-off: {exc}") from exc

        positions = raw.get("payload", raw) if isinstance(raw, dict) else raw
        if isinstance(positions, dict):
            positions = _pick(positions, "positions", default=[])

        results: list[OrderResult] = []
        for pos in positions or []:
            qty = int(_pick(pos, "net_quantity", "quantity", default=0) or 0)
            if qty == 0:
                continue
            results.append(
                await self.place_order(
                    OrderRequest(
                        symbol=str(_pick(pos, "trading_symbol", "symbol", default="")),
                        side="SELL" if qty > 0 else "BUY",
                        quantity=abs(qty),
                        order_type="MARKET",
                    )
                )
            )
        return results

    # ---- diagnostics ---------------------------------------------------

    def probe_sdk(self) -> dict:
        try:
            from growwapi import GrowwAPI
        except ImportError:
            return {"installed": False, "all_methods": [], "note": "pip install growwapi"}
        methods = sorted(m for m in dir(GrowwAPI) if not m.startswith("_"))
        keywords = ("ltp", "quote", "ohlc", "candle", "feed", "depth", "token", "order", "position", "margin")
        return {
            "installed": True,
            "all_methods": methods,
            "market_data_methods": [m for m in methods if any(k in m.lower() for k in keywords)],
        }

    async def diagnose_permissions(self) -> dict:
        """Probes each capability so an entitlement problem is distinguishable
        from a broken credential or a wrong call signature.
        """
        sdk = self._require_session()
        results: list[dict] = []

        async def check(name: str, fn, kind: str):
            try:
                await asyncio.to_thread(fn)
                results.append({"name": name, "kind": kind, "ok": True, "error": None})
            except Exception as exc:  # noqa: BLE001
                results.append({
                    "name": name, "kind": kind, "ok": False,
                    "forbidden": _is_forbidden(exc),
                    "error": str(exc)[:200],
                })

        await check("get_ltp", lambda: sdk.get_ltp(
            exchange_trading_symbols=(f"{self.EXCHANGE}_RELIANCE",), segment=self.SEGMENT), "market_data")
        await check("get_quote", lambda: sdk.get_quote(
            trading_symbol="RELIANCE", exchange=self.EXCHANGE, segment=self.SEGMENT), "market_data")
        await check("get_ohlc", lambda: sdk.get_ohlc(
            exchange_trading_symbols=(f"{self.EXCHANGE}_RELIANCE",), segment=self.SEGMENT), "market_data")
        await check("get_holdings_for_user", lambda: sdk.get_holdings_for_user(), "account")
        await check("get_positions_for_user",
                    lambda: sdk.get_positions_for_user(segment=self.SEGMENT), "account")

        market = [r for r in results if r["kind"] == "market_data"]
        account = [r for r in results if r["kind"] == "account"]
        market_ok = any(r["ok"] for r in market)
        account_ok = any(r["ok"] for r in account)

        if market_ok:
            verdict, detail = "READY", "Market data is available. You can switch the data source to LIVE NSE."
        elif account_ok and all(r.get("forbidden") for r in market):
            verdict, detail = "NO_MARKET_DATA_ENTITLEMENT", _FORBIDDEN_HINT
        elif account_ok:
            verdict, detail = "MARKET_DATA_ERROR", "Account access works but market data failed for a non-permission reason — see the per-call errors."
        else:
            verdict, detail = "SESSION_BROKEN", "Neither account nor market-data calls succeeded. Re-run login."

        return {"verdict": verdict, "detail": detail, "checks": results}
