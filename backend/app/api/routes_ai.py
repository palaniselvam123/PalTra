from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.encryption import get_vault
from app.models.database import AiAnalysis, AiConfig, AiCredential, async_session
from app.services.ai_advisor import AiUnavailable, ai_advisor
from app.services.broadcaster import broadcaster

router = APIRouter(prefix="/api/ai", tags=["ai"])


class SaveKeyRequest(BaseModel):
    api_key: str = Field(min_length=10)


class ConfigRequest(BaseModel):
    model: str | None = None
    web_search_enabled: bool | None = None
    gate_enabled: bool | None = None
    min_conviction: int | None = None
    require_agreement: bool | None = None
    cache_ttl_sec: int | None = None


class AnalyzeRequest(BaseModel):
    symbol: str
    force: bool = False


class TestRequest(BaseModel):
    model: str | None = None


@router.get("/status")
async def ai_status():
    return await ai_advisor.status()


@router.post("/key")
async def save_key(body: SaveKeyRequest):
    """Stores the OpenAI key Fernet-encrypted, exactly like the broker
    credentials. It is never returned by any endpoint after this point.
    """
    key = body.api_key.strip()
    vault = get_vault()
    async with async_session() as session:
        row = await session.scalar(select(AiCredential).where(AiCredential.provider == "openai"))
        if row:
            row.api_key_encrypted = vault.encrypt(key)
            row.updated_at = dt.datetime.utcnow()
        else:
            session.add(AiCredential(provider="openai", api_key_encrypted=vault.encrypt(key)))
        await session.commit()
    return {"ok": True, "configured": True}


@router.delete("/key")
async def delete_key():
    """Removes the key and disables the gate with it — leaving the gate on
    with no key would block every bot entry until someone noticed.
    """
    async with async_session() as session:
        row = await session.scalar(select(AiCredential).where(AiCredential.provider == "openai"))
        if row:
            await session.delete(row)
        config = await session.get(AiConfig, 1)
        if config:
            config.gate_enabled = False
        await session.commit()
    ai_advisor.invalidate_config()
    return {"ok": True, "configured": False, "gate_enabled": False}


@router.post("/test")
async def test_key(body: TestRequest):
    try:
        return await ai_advisor.test_connection(body.model)
    except AiUnavailable as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.get("/config")
async def get_config():
    cfg = await ai_advisor.config(refresh=True)
    return {
        "model": cfg.model,
        "web_search_enabled": cfg.web_search_enabled,
        "gate_enabled": cfg.gate_enabled,
        "min_conviction": cfg.min_conviction,
        "require_agreement": cfg.require_agreement,
        "cache_ttl_sec": cfg.cache_ttl_sec,
    }


@router.post("/config")
async def set_config(body: ConfigRequest):
    if body.min_conviction is not None and not 0 <= body.min_conviction <= 100:
        raise HTTPException(400, "min_conviction must be between 0 and 100")
    if body.cache_ttl_sec is not None and body.cache_ttl_sec < 60:
        raise HTTPException(400, "cache_ttl_sec must be at least 60 — shorter means paying for a fresh "
                                 "web search on almost every candle.")
    if body.gate_enabled and not await ai_advisor.has_key():
        raise HTTPException(428, "Save an OpenAI API key before enabling the AI entry gate.")

    async with async_session() as session:
        row = await session.get(AiConfig, 1)
        if row is None:
            row = AiConfig(id=1)
            session.add(row)
        for name in (
            "model",
            "web_search_enabled",
            "gate_enabled",
            "min_conviction",
            "require_agreement",
            "cache_ttl_sec",
        ):
            value = getattr(body, name)
            if value is not None:
                setattr(row, name, value)
        await session.commit()

    ai_advisor.invalidate_config()
    cfg = await ai_advisor.config(refresh=True)
    if body.gate_enabled is not None:
        await broadcaster.publish(
            "log",
            {
                "level": "WARN",
                "message": f"AI entry gate {'ENABLED' if cfg.gate_enabled else 'DISABLED'} — "
                f"min conviction {cfg.min_conviction}, "
                f"{'stance must agree with the signal' if cfg.require_agreement else 'stance agreement not required'}.",
            },
        )
    return await get_config()


@router.post("/analyze")
async def analyze(body: AnalyzeRequest):
    """On-demand expert view. This one does await the provider — with web
    search on it can take 30-60 seconds, which is fine for a user-initiated
    request but is exactly why the bot gate reads the cache instead.
    """
    symbol = body.symbol.strip().upper()
    if not symbol:
        raise HTTPException(400, "symbol is required")

    if not body.force:
        cfg = await ai_advisor.config()
        cached = ai_advisor.cached(symbol, cfg.cache_ttl_sec)
        if cached:
            return {"cached": True, "view": cached.to_dict()}

    try:
        view = await ai_advisor.analyze(symbol, requested_by="USER")
    except AiUnavailable as exc:
        raise HTTPException(exc.status_code, exc.message) from exc

    await broadcaster.publish("ai_view", view.to_dict())
    return {"cached": False, "view": view.to_dict()}


@router.get("/view/{symbol}")
async def cached_view(symbol: str):
    cfg = await ai_advisor.config()
    view = ai_advisor.cached(symbol, cfg.cache_ttl_sec)
    return {"view": view.to_dict() if view else None}


@router.get("/analyses")
async def recent_analyses(symbol: str | None = Query(None), limit: int = Query(25, le=200)):
    async with async_session() as session:
        query = select(AiAnalysis).order_by(AiAnalysis.created_at.desc()).limit(limit)
        if symbol:
            query = query.where(AiAnalysis.symbol == symbol.upper())
        rows = (await session.execute(query)).scalars().all()

    return [
        {
            "id": r.id,
            "symbol": r.symbol,
            "stance": r.stance,
            "conviction": r.conviction,
            "sentiment_label": r.sentiment_label,
            "sentiment_score": r.sentiment_score,
            "thesis": r.thesis,
            "catalysts": json.loads(r.catalysts or "[]"),
            "risks": json.loads(r.risks or "[]"),
            "invalidation": r.invalidation,
            "sources": json.loads(r.sources or "[]"),
            "recency_note": r.recency_note,
            "model": r.model,
            "web_search_used": r.web_search_used,
            "latency_ms": r.latency_ms,
            "requested_by": r.requested_by,
            "gate_side": r.gate_side,
            "gate_passed": r.gate_passed,
            "gate_reason": r.gate_reason,
            "trade_id": r.trade_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
