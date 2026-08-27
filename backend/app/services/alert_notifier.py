"""WhatsApp alert delivery for scanner signals.

Supports two providers because they suit different users:

* **CallMeBot** — a single authenticated GET, no account beyond a one-time
  WhatsApp handshake. Easiest to get working.
* **Twilio** — a real messaging API with delivery receipts, but needs an
  account SID, auth token and a sandbox/approved sender.

Credentials are Fernet-encrypted at rest like every other secret in this app,
and are never returned decrypted over the API.

Delivery failures are recorded, never swallowed: a scanner whose alerts
silently stop arriving is worse than one that says it could not send.
"""
from __future__ import annotations

import asyncio
import datetime as dt
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from app.core.encryption import get_vault
from app.core.market_clock import ist_now
from app.models.database import AlertChannel, async_session

CALLMEBOT = "callmebot"
TWILIO = "twilio"

REQUEST_TIMEOUT = 20.0
MAX_ATTEMPTS = 3


@dataclass
class DeliveryResult:
    ok: bool
    provider: str | None
    error: str | None = None
    skipped_reason: str | None = None


def format_message(
    *,
    side: str,
    symbol: str,
    timeframe: str,
    price: float,
    fast_label: str,
    slow_label: str,
    when: dt.datetime,
    extra_reasons: list[str] | None = None,
) -> str:
    direction = "above" if side == "BUY" else "below"
    lines = [
        f"\U0001F6A8 [{side} SIGNAL] - {symbol}",
        f"Timeframe: {timeframe} | Trigger Price: ₹{price:,.2f}",
        f"Signal: {fast_label} crossed {direction} {slow_label}",
        f"Time: {when.strftime('%Y-%m-%d %H:%M:%S')} IST",
    ]
    for reason in (extra_reasons or [])[:2]:
        lines.append(f"• {reason}")
    return "\n".join(lines)


class AlertNotifier:
    """Owns delivery plus the dedup/cooldown policy.

    Cooldown state is in memory and therefore resets on restart. That is the
    honest trade-off for a "per session" rule — persisting it would make
    `once_per_session` mean "once ever", which is not what the setting says.
    """

    def __init__(self) -> None:
        # (symbol, timeframe, side) -> last sent time
        self._last_sent: dict[tuple[str, str, str], dt.datetime] = {}
        # (symbol, timeframe) -> alerted at least once this session
        self._session_fired: set[tuple[str, str]] = set()

    def reset_session(self) -> None:
        self._last_sent.clear()
        self._session_fired.clear()

    def should_send(
        self,
        symbol: str,
        timeframe: str,
        side: str,
        *,
        cooldown_minutes: int,
        once_per_session: bool,
    ) -> tuple[bool, str | None]:
        if once_per_session and (symbol, timeframe) in self._session_fired:
            return False, f"already alerted for {symbol} on {timeframe} this session"
        key = (symbol, timeframe, side)
        last = self._last_sent.get(key)
        if last is not None:
            elapsed = (ist_now() - last).total_seconds() / 60
            if elapsed < cooldown_minutes:
                remaining = cooldown_minutes - elapsed
                return False, f"cooldown: {remaining:.0f} min left for {symbol} {side} {timeframe}"
        return True, None

    def mark_sent(self, symbol: str, timeframe: str, side: str) -> None:
        self._last_sent[(symbol, timeframe, side)] = ist_now()
        self._session_fired.add((symbol, timeframe))

    # ---- credentials ----------------------------------------------------

    async def active_channel(self) -> AlertChannel | None:
        async with async_session() as session:
            rows = (
                await session.execute(select(AlertChannel).where(AlertChannel.enabled.is_(True)))
            ).scalars().all()
        return rows[0] if rows else None

    async def configured_providers(self) -> list[dict]:
        async with async_session() as session:
            rows = (await session.execute(select(AlertChannel))).scalars().all()
        return [
            {"provider": r.provider, "enabled": r.enabled, "configured": bool(r.secret_encrypted)}
            for r in rows
        ]

    # ---- delivery -------------------------------------------------------

    async def send(self, message: str) -> DeliveryResult:
        channel = await self.active_channel()
        if channel is None:
            return DeliveryResult(False, None, skipped_reason="No WhatsApp channel is enabled")

        vault = get_vault()
        try:
            target = vault.decrypt(channel.target_encrypted) if channel.target_encrypted else ""
            secret = vault.decrypt(channel.secret_encrypted) if channel.secret_encrypted else ""
            extra = vault.decrypt(channel.extra_encrypted) if channel.extra_encrypted else ""
        except Exception as exc:  # noqa: BLE001
            return DeliveryResult(False, channel.provider, error=f"Could not decrypt credentials: {exc}")

        if channel.provider == CALLMEBOT:
            return await self._send_callmebot(target, secret, message)
        if channel.provider == TWILIO:
            return await self._send_twilio(target, secret, extra, message)
        return DeliveryResult(False, channel.provider, error=f"Unknown provider {channel.provider}")

    async def _with_retries(self, call, provider: str) -> DeliveryResult:
        """Retries transient network failures with backoff.

        A 4xx is NOT retried: a bad API key will still be bad three attempts
        later, and hammering the endpoint risks a rate-limit ban on top of the
        original problem. Only timeouts, network errors, 429 and 5xx retry.
        """
        last_error = "unknown"
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await call()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(2**attempt)
                    continue
                return DeliveryResult(
                    False, provider, error=f"network failure after {MAX_ATTEMPTS} attempts: {last_error}"
                )
            except Exception as exc:  # noqa: BLE001
                return DeliveryResult(False, provider, error=f"{type(exc).__name__}: {exc}")

            if 200 <= response.status_code < 300:
                return DeliveryResult(True, provider)

            body = (response.text or "")[:200]
            if response.status_code == 429 or response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {body}"
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(2**attempt)
                    continue
            return DeliveryResult(False, provider, error=f"HTTP {response.status_code}: {body}")
        return DeliveryResult(False, provider, error=last_error)

    async def _send_callmebot(self, phone: str, apikey: str, message: str) -> DeliveryResult:
        if not phone or not apikey:
            return DeliveryResult(False, CALLMEBOT, error="CallMeBot needs both a phone number and an API key")

        async def call():
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                return await client.get(
                    "https://api.callmebot.com/whatsapp.php",
                    params={"phone": phone, "text": message, "apikey": apikey},
                )

        return await self._with_retries(call, CALLMEBOT)

    async def _send_twilio(self, to_number: str, auth: str, from_number: str, message: str) -> DeliveryResult:
        # `auth` is stored as "account_sid:auth_token" so one encrypted field
        # covers the pair without needing an extra schema column.
        sid, _, token = auth.partition(":")
        if not (sid and token and to_number and from_number):
            return DeliveryResult(
                False, TWILIO, error="Twilio needs account SID, auth token, a from number and a to number"
            )

        async def call():
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                return await client.post(
                    f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
                    auth=(sid, token),
                    data={
                        "From": f"whatsapp:{from_number}",
                        "To": f"whatsapp:{to_number}",
                        "Body": message,
                    },
                )

        return await self._with_retries(call, TWILIO)


alert_notifier = AlertNotifier()
