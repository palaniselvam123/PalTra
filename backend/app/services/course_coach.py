"""Course coach — teaches the user's own trading curriculum.

This is a fourth explanation layer, and it is worth being precise about why it
is not a duplicate of the three that already exist:

* `indicators.py` / `patterns.py` own **price structure**. They decide what a
  candle is and what the trend was. Deterministic, testable, authoritative.
* `explain.py` turns those numbers into **plain English**. "EMA9 crossed below
  EMA21, 4 paise apart — a coin flip, not a trend change."
* `ai_advisor.py` covers **news, earnings and macro** — what the price engine
  is structurally blind to. It is deliberately forbidden from chart analysis.
* This module covers **the user's own uploaded course material**: what *their*
  curriculum teaches about the pattern the engine just found — the entry rule,
  where the stop goes and why, what invalidates it — cited back to the page.

`patterns.py` already says a hammer is "a small body with a long lower wick
after a decline". True, and generic. What it cannot say is what the user's
specific course teaches about trading it. That is the gap this fills.

Two design rules, both load-bearing:

1. **The RAG never re-detects.** `patterns.py` owns detection; the hit is sent
   over as ground truth. Two detectors over the same bars would eventually
   disagree, and the engine's answer is the one the charts and backtests are
   built on.
2. **Advisory only.** Nothing here can open, size, veto, or block a position.
   Unlike `ai_advisor`, this layer does not even have a veto — it talks to the
   user, not to the order path. Every call degrades to None on failure.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.indicators import OHLCV
from app.services.patterns import PatternHit

logger = logging.getLogger(__name__)

# Generous, because retrieval plus generation is genuinely slow — but this is
# never on the tick path, so latency here costs nothing but the user's wait.
EXPLAIN_TIMEOUT_SEC = 90.0
ASK_TIMEOUT_SEC = 90.0
SEARCH_TIMEOUT_SEC = 15.0

MAX_CANDLES = 60

# patterns.py reports BULLISH / BEARISH / INDECISION; the RAG speaks lowercase.
_BIAS_TO_DIRECTION = {
    "BULLISH": "bullish",
    "BEARISH": "bearish",
    "INDECISION": "neutral",
}


@dataclass
class CoachStatus:
    configured: bool
    reachable: bool
    documents_ready: int
    model: str | None
    error: str | None


class CourseCoach:
    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = (settings.course_rag_url or "").rstrip("/")
        self.api_key = settings.course_rag_api_key or ""
        self.enabled = settings.course_rag_enabled and bool(self.base_url) and bool(self.api_key)

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def _post(self, path: str, payload: dict[str, Any], timeout: float) -> dict | None:
        if not self.enabled:
            return None
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(f"{self.base_url}{path}", json=payload, headers=self._headers)
            if res.status_code >= 400:
                logger.warning("course coach %s -> %s: %s", path, res.status_code, res.text[:300])
                return None
            return res.json()
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            logger.warning("course coach %s unreachable: %s", path, exc)
            return None
        except Exception as exc:  # noqa: BLE001 — advisory layer, never propagate
            logger.exception("course coach error on %s: %s", path, exc)
            return None

    async def status(self) -> CoachStatus:
        if not self.enabled:
            return CoachStatus(
                False, False, 0, None,
                "COURSE_RAG_URL / COURSE_RAG_API_KEY not set (see .env.example).",
            )
        try:
            async with httpx.AsyncClient(timeout=SEARCH_TIMEOUT_SEC) as client:
                res = await client.get(f"{self.base_url}/api/v1/health", headers=self._headers)
            if res.status_code >= 400:
                return CoachStatus(True, False, 0, None, f"HTTP {res.status_code}: {res.text[:200]}")
            data = res.json()
            return CoachStatus(
                True, True, data.get("documents", {}).get("ready", 0), data.get("model"), None
            )
        except Exception as exc:  # noqa: BLE001
            return CoachStatus(True, False, 0, None, str(exc))

    @staticmethod
    def _candles_payload(candles: list[OHLCV]) -> list[dict]:
        return [
            {"t": c.ts, "o": c.open, "h": c.high, "l": c.low, "c": c.close, "v": c.volume}
            for c in candles[-MAX_CANDLES:]
        ]

    async def explain_pattern(
        self,
        *,
        symbol: str,
        candles: list[OHLCV],
        hit: PatternHit | None,
        timeframe: str | None = None,
        indicators: dict[str, Any] | None = None,
        entry: float | None = None,
        stop_loss: float | None = None,
        target: float | None = None,
        question: str | None = None,
    ) -> dict | None:
        """Ask the course material to explain a pattern this engine detected.

        `hit` comes from `patterns.py`. When it is None the RAG is told plainly
        that no named pattern formed, so it explains what the material says to
        wait for rather than inventing one.
        """
        market: dict[str, Any] = {
            "symbol": symbol,
            "exchange": "NSE",
            "candles": self._candles_payload(candles),
        }
        if timeframe:
            market["timeframe"] = timeframe
        if candles:
            market["lastPrice"] = candles[-1].close
        if indicators:
            market["indicators"] = indicators

        payload: dict[str, Any] = {"market": market}

        if hit is not None:
            levels: dict[str, float] = {}
            if entry is not None:
                levels["entry"] = round(entry, 2)
            if stop_loss is not None:
                levels["stopLoss"] = round(stop_loss, 2)
            if target is not None:
                levels["target"] = round(target, 2)

            payload["pattern"] = {
                "name": hit.name,
                "label": hit.label,
                "direction": _BIAS_TO_DIRECTION.get(hit.bias, "neutral"),
                "trend": hit.trend,
                # patterns.py only emits a hit when the context qualifies, so a
                # hit that exists is a hit whose trend context was satisfied.
                "trendValid": True,
                "note": hit.note,
                **({"levels": levels} if levels else {}),
            }
            payload["question"] = question or (
                f"Our engine detected a {hit.label} on {symbol} after a {hit.trend.lower()} trend. "
                "What does the course material teach about this pattern — the entry rule, where the "
                "stop belongs and why, and what would invalidate it?"
            )
        else:
            market["notes"] = (
                "Our pattern engine found no named candlestick pattern on the latest bar. "
                "Do not invent one."
            )
            payload["question"] = question or (
                "No named pattern has formed. What does the course material say to wait for here?"
            )

        return await self._post("/api/v1/coach", payload, EXPLAIN_TIMEOUT_SEC)

    async def ask(self, question: str, conversation_id: str | None = None) -> dict | None:
        payload: dict[str, Any] = {"question": question}
        if conversation_id:
            payload["conversationId"] = conversation_id
        return await self._post("/api/v1/ask", payload, ASK_TIMEOUT_SEC)

    async def search(self, query: str, top_k: int = 5) -> dict | None:
        """Retrieval only — no LLM, so it is fast and costs nothing."""
        return await self._post("/api/v1/search", {"query": query, "topK": top_k}, SEARCH_TIMEOUT_SEC)


course_coach = CourseCoach()
