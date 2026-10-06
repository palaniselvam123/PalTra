from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.ai_advisor import AiUnavailable, ai_advisor
from app.services.chat_advisor import answer, build_context, suggested_questions
from app.services.sma_context import sma_facts

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatTurn(BaseModel):
    role: str  # user | assistant
    content: str


class AskRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list)
    # The page the user is on and the numbers it shows, so the answer is about
    # what they are looking at. Data only; never instructions.
    page: str | None = Field(default=None, max_length=200)
    screen: dict | None = None


@router.get("/status")
async def chat_status():
    """The widget needs to know whether it can talk at all before offering a
    prompt box — it shares the OpenAI key with the expert advisor.
    """
    cfg = await ai_advisor.config()
    return {
        "configured": await ai_advisor.has_key(),
        "model": cfg.model,
        "suggestions": suggested_questions(),
    }


@router.post("/ask")
async def ask(body: AskRequest):
    try:
        return await answer(body.message, [t.model_dump() for t in body.history], body.page, body.screen)
    except AiUnavailable as exc:
        raise HTTPException(exc.status_code, exc.message) from exc


@router.get("/context")
async def context():
    """The exact snapshot the assistant is given. Exposed so an answer can be
    checked against its source rather than taken on trust.
    """
    ctx = await build_context()
    ctx["sma_terminal"] = await asyncio.to_thread(sma_facts)
    return ctx
