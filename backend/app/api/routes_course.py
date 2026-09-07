"""Course coach endpoints — the user's own trading curriculum, surfaced in the UI.

Read-only with respect to trading. Nothing in this router can open, close,
size, veto or block a position; it reads candles that already exist, asks
`patterns.py` what it sees, and asks the course material to teach it.
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services import indicators as ind
from app.services import patterns as candle_patterns
from app.services.backfill import ensure_backfilled
from app.services.candle_store import INTERVALS, candle_store
from app.services.course_coach import course_coach
from app.services.market_data import market_data

router = APIRouter(prefix="/api/course", tags=["course"])


class AskRequest(BaseModel):
    question: str
    conversation_id: str | None = None


@router.get("/status")
async def status():
    return asdict(await course_coach.status())


@router.get("/explain")
async def explain(
    symbol: str = Query(...),
    interval: str = Query("5m"),
    limit: int = Query(120, ge=20, le=500),
    question: str | None = Query(None),
):
    """Explain the latest closed bar for a symbol using the course material.

    Detection is done by `patterns.py` — the same engine that draws the chart
    markers — and the hit is handed to the RAG as ground truth. The forming bar
    is excluded, matching /api/chart/patterns: its shape can still change.
    """
    symbol = symbol.upper()
    if interval not in INTERVALS:
        raise HTTPException(400, f"interval must be one of {list(INTERVALS)}")

    await ensure_backfilled(symbol, interval)
    source = market_data.source.value
    bars = candle_store.get(symbol, interval, source, limit)
    closed = bars[:-1] if bars else []
    if len(closed) < 10:
        raise HTTPException(409, f"Not enough closed candles for {symbol} at {interval} yet.")

    hit = candle_patterns.latest(closed)

    # A small, relevant indicator snapshot — enough for the material to reason
    # about context without turning the prompt into a data dump.
    # Series entries are `float | None` — the warm-up period is unpopulated, so
    # every read has to be guarded before rounding.
    snapshot: dict[str, float] = {}
    try:
        for label, series in (("atr14", ind.atr(closed, 14)), ("rsi14", ind.rsi(closed, 14))):
            if series and series[-1] is not None:
                snapshot[label] = round(series[-1], 2)
    except Exception:  # noqa: BLE001 — indicators are optional context here
        pass

    result = await course_coach.explain_pattern(
        symbol=symbol,
        candles=closed,
        hit=hit,
        timeframe=interval,
        indicators=snapshot or None,
        question=question,
    )
    if result is None:
        raise HTTPException(503, "Course material service is unavailable. Check COURSE_RAG_URL / COURSE_RAG_API_KEY.")

    # Echo what our own engine found, so the UI can show the two side by side
    # and the user can see the RAG did not invent the pattern.
    result["engine_pattern"] = (
        {
            "name": hit.name,
            "label": hit.label,
            "bias": hit.bias,
            "trend": hit.trend,
            "note": hit.note,
            "close": round(hit.close, 2),
        }
        if hit
        else None
    )
    result["engine_description"] = candle_patterns.describe(closed, len(closed) - 1)
    return result


@router.post("/ask")
async def ask(body: AskRequest):
    question = body.question.strip()
    if not question:
        raise HTTPException(400, "question is required")
    result = await course_coach.ask(question, body.conversation_id)
    if result is None:
        raise HTTPException(503, "Course material service is unavailable.")
    return result


@router.get("/search")
async def search(q: str = Query(..., min_length=2), top_k: int = Query(5, ge=1, le=20)):
    """Retrieval only — no LLM call, no token cost."""
    result = await course_coach.search(q, top_k)
    if result is None:
        raise HTTPException(503, "Course material service is unavailable.")
    return result
