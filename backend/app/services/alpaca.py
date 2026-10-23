"""Alpaca market data for the US practice desk (read-only).

Alpaca is the US broker behind Groww's US stocks. Its free account gives an API key
pair that reads US prices: 1-minute bars and the latest trade from the IEX feed
(`feed=iex`, real time on the free plan; the full-market SIP feed needs a paid plan).

This module only reads. It has **no order endpoint**: the US desk fills its practice
trades locally (`us_desk.py`), and nothing here can send an order, not even to Alpaca's
own paper-trading account. The key pair is checked against the paper-trading host only
(`/v2/account`); a live-trading host is never called.

Keys come from the Settings form (encrypted with the desk's Fernet vault in
`us_credentials`) or the `ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` environment
variables (`fly secrets`). They are never logged or returned by the API.
"""
from __future__ import annotations

import datetime as dt
import os

import httpx

DATA_URL = "https://data.alpaca.markets"
PAPER_URL = "https://paper-api.alpaca.markets"  # key check only; never a live-trading host
FEED = "iex"
TIMEOUT = 15.0
MAX_PAGES = 20


class AlpacaError(RuntimeError):
    """A refusal or failure with a reason the page shows as is."""


class Keys:
    def __init__(self, key_id: str, secret: str, source: str):
        self.key_id = key_id.strip()
        self.secret = secret.strip()
        self.source = source  # saved | env

    def headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": self.key_id, "APCA-API-SECRET-KEY": self.secret}


def env_keys() -> Keys | None:
    key_id = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    return Keys(key_id, secret, "env") if key_id and secret else None


def _why(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        text = body.get("message") or body.get("msg") or str(body)
    except ValueError:
        text = resp.text
    text = str(text)[:200]
    if resp.status_code in (401, 403):
        return f"Alpaca refused the key ({resp.status_code}): {text}. Check the key ID and secret."
    if resp.status_code == 429:
        return "Alpaca rate limit (200 requests a minute on the free plan); retrying shortly."
    return f"Alpaca {resp.status_code}: {text}"


def _iso(when: dt.datetime) -> str:
    when = when if when.tzinfo else when.replace(tzinfo=dt.timezone.utc)
    return when.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(stamp: str) -> int:
    return int(dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp())


class AlpacaData:
    """A small async reader for Alpaca's stock data API. One shared HTTP client."""

    def __init__(self, keys: Keys, transport: httpx.AsyncBaseTransport | None = None):
        self.keys = keys
        self._client = httpx.AsyncClient(timeout=TIMEOUT, headers=keys.headers(), transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, url: str, params: dict) -> dict:
        try:
            resp = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AlpacaError(f"Alpaca not reachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise AlpacaError(_why(resp))
        return resp.json()

    async def account(self) -> dict:
        """The paper account behind the key (status and currency only; proves the key works)."""
        body = await self._get(f"{PAPER_URL}/v2/account", {})
        return {"status": body.get("status"), "currency": body.get("currency"), "account": "paper"}

    async def bars(self, symbols: list[str], start: dt.datetime, end: dt.datetime | None = None) -> dict[str, list[dict]]:
        """1-minute bars per symbol, oldest first: {ts (epoch s), open, high, low, close, volume}."""
        out: dict[str, list[dict]] = {s: [] for s in symbols}
        if not symbols:
            return out
        params = {
            "symbols": ",".join(symbols),
            "timeframe": "1Min",
            "start": _iso(start),
            "limit": 10000,
            "feed": FEED,
            "adjustment": "raw",
            "sort": "asc",
        }
        if end is not None:
            params["end"] = _iso(end)
        for _ in range(MAX_PAGES):
            body = await self._get(f"{DATA_URL}/v2/stocks/bars", params)
            for symbol, rows in (body.get("bars") or {}).items():
                for r in rows or []:
                    out.setdefault(symbol, []).append(
                        {
                            "ts": _epoch(r["t"]),
                            "open": float(r["o"]),
                            "high": float(r["h"]),
                            "low": float(r["l"]),
                            "close": float(r["c"]),
                            "volume": float(r.get("v") or 0),
                        }
                    )
            token = body.get("next_page_token")
            if not token:
                break
            params["page_token"] = token
        return out

    async def latest(self, symbols: list[str]) -> dict[str, dict]:
        """The latest trade per symbol: {price, ts}."""
        if not symbols:
            return {}
        body = await self._get(f"{DATA_URL}/v2/stocks/trades/latest", {"symbols": ",".join(symbols), "feed": FEED})
        out = {}
        for symbol, t in (body.get("trades") or {}).items():
            if t and t.get("p"):
                out[symbol] = {"price": float(t["p"]), "ts": _epoch(t["t"])}
        return out
