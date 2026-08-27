from __future__ import annotations

import datetime as dt

import pyotp
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.groww_client import GrowwClient
from app.core.encryption import get_vault
from app.models.database import BrokerCredential, async_session, get_session

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Single-broker session held in memory for now; a multi-broker registry can
# replace this dict once more than one adapter is wired up live.
_active_clients: dict[str, GrowwClient] = {}


async def restore_broker_sessions() -> list[str]:
    """Rebuilds broker sessions from stored, still-valid access tokens.

    Called once at startup. Without it a backend restart silently dropped the
    live feed and made the saved credentials look lost, even though the token
    was on disk and good until end of day.
    """
    async with async_session() as session:
        rows = (await session.execute(select(BrokerCredential))).scalars().all()

    vault = get_vault()
    now = dt.datetime.now()
    restored: list[str] = []

    for row in rows:
        if row.broker != "groww" or not row.access_token_encrypted:
            continue
        if not row.token_expires_at or row.token_expires_at <= now:
            continue  # expired overnight — the morning login has to run again
        client = GrowwClient()
        try:
            await client.restore_session(vault.decrypt(row.access_token_encrypted), row.token_expires_at)
        except Exception:  # noqa: BLE001
            continue  # a broken token must not stop the app from starting
        _active_clients[row.broker] = client
        restored.append(row.broker)

    return restored


class SaveCredentialsRequest(BaseModel):
    broker: str
    api_key: str
    api_secret: str
    totp_secret: str


class CredentialStatus(BaseModel):
    broker: str
    configured: bool
    token_valid: bool
    token_expires_at: dt.datetime | None = None


@router.post("/credentials")
async def save_credentials(body: SaveCredentialsRequest, session: AsyncSession = Depends(get_session)):
    vault = get_vault()
    existing = await session.scalar(select(BrokerCredential).where(BrokerCredential.broker == body.broker))
    if existing:
        existing.api_key_encrypted = vault.encrypt(body.api_key)
        existing.api_secret_encrypted = vault.encrypt(body.api_secret)
        existing.totp_secret_encrypted = vault.encrypt(body.totp_secret)
    else:
        session.add(
            BrokerCredential(
                broker=body.broker,
                api_key_encrypted=vault.encrypt(body.api_key),
                api_secret_encrypted=vault.encrypt(body.api_secret),
                totp_secret_encrypted=vault.encrypt(body.totp_secret),
            )
        )
    await session.commit()
    return {"ok": True}


@router.get("/credentials/{broker}", response_model=CredentialStatus)
async def get_credential_status(broker: str, session: AsyncSession = Depends(get_session)):
    row = await session.scalar(select(BrokerCredential).where(BrokerCredential.broker == broker))
    if not row:
        return CredentialStatus(broker=broker, configured=False, token_valid=False)
    client = _active_clients.get(broker)
    token_valid = await client.is_token_valid() if client else False
    return CredentialStatus(broker=broker, configured=True, token_valid=token_valid, token_expires_at=row.token_expires_at)


@router.post("/login/{broker}")
async def login(broker: str, session: AsyncSession = Depends(get_session)):
    """Runs the morning login workflow: decrypt stored creds, generate TOTP,
    exchange for a daily access token. Only Groww is wired to a real SDK
    today; other brokers 404 until their adapters are implemented.
    """
    if broker != "groww":
        raise HTTPException(400, f"No live adapter implemented for '{broker}' yet — use Paper mode.")

    row = await session.scalar(select(BrokerCredential).where(BrokerCredential.broker == broker))
    if not row:
        raise HTTPException(404, "No credentials saved for this broker. Save them in Settings first.")

    vault = get_vault()
    client = GrowwClient()
    try:
        token = await client.login(
            vault.decrypt(row.api_key_encrypted),
            vault.decrypt(row.api_secret_encrypted),
            vault.decrypt(row.totp_secret_encrypted),
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, str(exc)) from exc

    row.access_token_encrypted = vault.encrypt(token)
    row.token_expires_at = dt.datetime.combine(dt.date.today(), dt.time(23, 59))
    await session.commit()
    _active_clients[broker] = client
    return {"ok": True, "token_expires_at": row.token_expires_at}


@router.post("/test-totp/{broker}")
async def test_totp(broker: str, session: AsyncSession = Depends(get_session)):
    """Cheap connection sanity check: just proves the stored TOTP secret is
    valid and can produce a current 6-digit code, without hitting the broker.
    """
    row = await session.scalar(select(BrokerCredential).where(BrokerCredential.broker == broker))
    if not row:
        raise HTTPException(404, "No credentials saved for this broker.")
    vault = get_vault()
    try:
        code = pyotp.TOTP(vault.decrypt(row.totp_secret_encrypted)).now()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"Stored TOTP secret is invalid: {exc}") from exc
    return {"ok": True, "sample_code": code}
