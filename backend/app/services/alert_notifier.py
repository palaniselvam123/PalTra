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

**A 2xx status is not delivery.** CallMeBot answers a rejected request with a
2xx and an HTML body describing the problem — an invalid API key returns
HTTP 201 with "ERROR: apikey can not be empty or it has an invalid format".
Treating any 2xx as success therefore reported failed sends as delivered, which
is precisely the silent failure this module claims not to have. Classification
now reads the body.

Even a clean acceptance is only that: CallMeBot confirms it queued the message,
not that WhatsApp delivered it. `DeliveryResult.classification` distinguishes
ACCEPTED from DELIVERED so callers cannot conflate them.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from app.core.encryption import get_vault
from app.core.market_clock import ist_now
from app.models.database import AlertChannel, async_session

CALLMEBOT = "callmebot"
TWILIO = "twilio"

REQUEST_TIMEOUT = 20.0
MAX_ATTEMPTS = 3

log = logging.getLogger("alerts")

# Classification of what a provider response actually means.
DELIVERED = "DELIVERED"      # provider confirms the message reached the recipient
ACCEPTED = "ACCEPTED"        # provider took the request; delivery unconfirmed
REJECTED = "REJECTED"        # provider refused it (bad key, unregistered number, ...)
FAILED = "FAILED"            # transport failure, timeout, or 4xx/5xx
UNKNOWN = "UNKNOWN"          # unrecognisable response

# Substrings that mark a rejection even under a 2xx status.
_REJECTION_MARKERS = (
    "error",
    "apikey",
    "api key",
    "invalid",
    "not registered",
    "activate",
    "unauthorized",
    "forbidden",
)


def mask_secret(value: str | None) -> str:
    """Describe a secret without revealing it."""
    if not value:
        return "<unset>"
    return f"<set, {len(value)} chars>"


def mask_phone(value: str | None) -> str:
    """Country code plus the last two digits, nothing else."""
    v = (value or "").strip()
    if len(v) < 5:
        return "<unset>"
    return f"{v[:3]}{'*' * (len(v) - 5)}{v[-2:]}"


def sanitize(text: str | None, *secrets: str) -> str:
    """Strip secrets and identifiers out of anything destined for a log."""
    out = (text or "")[:400]
    for sec in secrets:
        if sec:
            bare = sec.lstrip("+")
            for form in (sec, bare, "%2B" + bare):
                out = out.replace(form, "<REDACTED>")
    out = re.sub(r"(?i)(apikey|api_key|token|auth|password)=[^&\s\"'<]+", r"\1=<REDACTED>", out)
    out = re.sub(r"(?i)(phone|number|to)=[^&\s\"'<]+", r"\1=<REDACTED>", out)
    out = re.sub(r"\+?\d{10,15}", "<REDACTED>", out)
    return out


def classify_response(status: int, body: str) -> tuple[str, str]:
    """What a provider response really means, and why.

    Reads the body rather than trusting the status code, because the primary
    provider signals rejection with a 2xx.
    """
    text = (body or "").strip()
    lowered = text.lower()

    if status >= 500:
        return FAILED, f"provider error, HTTP {status}"
    if status >= 400:
        return FAILED, f"request rejected, HTTP {status}"

    for marker in _REJECTION_MARKERS:
        if marker in lowered:
            return REJECTED, f"provider returned an error body (HTTP {status})"

    if not text:
        return ACCEPTED, f"empty body, HTTP {status} (acceptance, not proof of delivery)"
    if any(w in lowered for w in ("queued", "sent", "success", "message")):
        return ACCEPTED, f"provider acknowledged the request (HTTP {status}); delivery unconfirmed"
    return UNKNOWN, f"unrecognised response body (HTTP {status})"


@dataclass
class DeliveryResult:
    ok: bool
    provider: str | None
    error: str | None = None
    skipped_reason: str | None = None
    status_code: int | None = None
    classification: str | None = None
    provider_message: str | None = None      # sanitised; never contains secrets

    @property
    def delivery_confirmed(self) -> bool:
        """True only when the provider confirms delivery, not mere acceptance.

        Kept separate from `ok` so a caller cannot mistake "the API took it" for
        "the message arrived" — the distinction that made this bug invisible.
        """
        return self.classification == DELIVERED


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

    async def _with_retries(self, call, provider: str, *secrets: str) -> DeliveryResult:
        """Retries transient network failures with backoff.

        A 4xx is NOT retried: a bad API key will still be bad three attempts
        later, and hammering the endpoint risks a rate-limit ban on top of the
        original problem. Only timeouts, network errors, 429 and 5xx retry.

        `secrets` are values to strip from anything logged or returned. They are
        passed explicitly rather than pattern-matched, because a provider that
        echoes a credential back in prose ("apikey ABC123 rejected") defeats any
        `key=value` regex — which a test caught here.
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

            body = (response.text or "")[:400]
            classification, reason = classify_response(response.status_code, body)
            sanitized = sanitize(body, *secrets)

            log.info(
                "alert.delivery provider=%s status=%s classification=%s reason=%s response=%s",
                provider, response.status_code, classification, reason, sanitized,
            )

            if classification in (ACCEPTED, DELIVERED):
                return DeliveryResult(
                    True, provider, status_code=response.status_code,
                    classification=classification, provider_message=sanitized,
                )

            if classification == FAILED and (response.status_code == 429 or response.status_code >= 500):
                last_error = f"HTTP {response.status_code}: {sanitized}"
                if attempt < MAX_ATTEMPTS:
                    await asyncio.sleep(2**attempt)
                    continue

            # A rejection is not retried: a bad key stays bad, and repeating the
            # call risks a rate-limit ban on top of the original problem.
            return DeliveryResult(
                False, provider,
                error=f"{reason}: {sanitized}",
                status_code=response.status_code,
                classification=classification,
                provider_message=sanitized,
            )
        return DeliveryResult(False, provider, error=last_error, classification=FAILED)

    async def _send_callmebot(self, phone: str, apikey: str, message: str) -> DeliveryResult:
        if not phone or not apikey:
            return DeliveryResult(False, CALLMEBOT, error="CallMeBot needs both a phone number and an API key")

        async def call():
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                return await client.get(
                    "https://api.callmebot.com/whatsapp.php",
                    params={"phone": phone, "text": message, "apikey": apikey},
                )

        return await self._with_retries(call, CALLMEBOT, apikey, phone)

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

        return await self._with_retries(call, TWILIO, token, sid, to_number, from_number)


alert_notifier = AlertNotifier()
