"""AI trading expert.

Calls OpenAI's Responses API and asks it to behave like a discretionary
trading desk analyst rather than a chartist. The prompt deliberately forbids
price-behaviour analysis — support/resistance, moving averages, candlestick
reading — because the ORB engine already owns price structure. What the model
adds is the thing the engine is blind to: news flow, earnings and guidance,
analyst actions, sector read-across, macro and policy, flows, and the tone of
retail sentiment. Feeding it the chart too would just launder the same signal
through a second, non-deterministic layer.

Two things about the plumbing matter for safety:

* The tick loop must never await a network call. Web search takes tens of
  seconds; blocking `on_candle_close` on it would stall the price feed and the
  bracket enforcement that rides on the same loop. So the bot gate reads a TTL
  cache only, and misses are filled by a background task.
* The gate can only ever *veto*. There is no path from a model response to an
  order — `place_paper_entry` still owns every entry, and the risk manager
  still gates it afterwards.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import time
from dataclasses import dataclass, field, asdict

import httpx
from sqlalchemy import select

from app.core.encryption import get_vault
from app.models.database import AiAnalysis, AiConfig, AiCredential, async_session

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_MODELS_URL = "https://api.openai.com/v1/models"

# Web search is slow by nature — it is doing real retrieval. This ceiling is
# generous on purpose; the call is off the tick path.
REQUEST_TIMEOUT_SEC = 180.0
MAX_CONCURRENT_CALLS = 3

# Reasoning models spend output tokens on internal reasoning BEFORE emitting a
# single visible character, and that spend counts against max_output_tokens. A
# budget sized for the answer alone gets consumed entirely by reasoning and the
# response comes back `incomplete` with nothing in it. These prefixes get an
# explicit effort setting and a budget large enough to cover both.
REASONING_MODEL_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def supports_reasoning(model: str) -> bool:
    return model.startswith(REASONING_MODEL_PREFIXES)

SYSTEM_PROMPT = """You are a senior sell-side equity trading analyst covering Indian equities (NSE/BSE), \
advising an intraday desk. You are being consulted as a discretionary expert, not as a technical analyst.

HARD CONSTRAINT — what you must NOT do:
Do not analyse the stock's own price behaviour. No chart patterns, no support/resistance levels, no moving \
averages, no RSI/MACD/Bollinger/Supertrend, no candlestick reading, no volume-profile or breakout-level \
commentary, no price targets derived from the chart. The desk's own systematic engine already handles price \
structure and your job is explicitly to cover what it cannot see. If you catch yourself describing what the \
price has been doing, stop and replace it with a fundamental or sentiment observation.

WHAT TO BASE YOUR VIEW ON (these are the parameters you are hired for):
1. News flow and headline sentiment — last 7 days, weighted heavily to the last 24-48 hours.
2. Earnings, results, guidance and management commentary; any upcoming results date.
3. Analyst actions: upgrades, downgrades, target revisions, initiations, and the tone of desk notes.
4. Corporate actions and disclosures: block/bulk deals, promoter pledging or stake changes, buybacks, \
fundraises, insider activity, regulatory filings, litigation, credit rating moves.
5. Sector and peer read-across — what is happening to close comparables and to the sector index.
6. Macro, policy and regulatory backdrop: RBI, government policy, budget/tax measures, tariffs, commodity \
and currency moves that hit this company's inputs or exports.
7. Institutional flows and positioning: FII/DII behaviour, index inclusion/exclusion, delivery trends \
described qualitatively, F&O ban-period status.
8. Retail and social sentiment tone, plus how crowded or contrarian the current narrative feels.
9. Known event risk in the next 24 hours that could reprice the name.

HONESTY RULES — these override any desire to sound useful:
- If your search turns up nothing material on this name, say so plainly, return stance NEUTRAL with low \
conviction, and set recency_note accordingly. An invented narrative is worse than no view.
- Never state a rumour as fact. Attribute claims to the source you found them in.
- Distinguish clearly between what you verified from a source today and what you are recalling from \
background knowledge that may be stale.
- Conviction is your confidence in the sentiment read, not a promise about the outcome. Reserve values \
above 75 for cases with concrete, recent, corroborated catalysts.
- You are advising a virtual-money paper-trading sandbox. Provide analysis, not personalised investment \
advice, and do not tell the user how much to invest."""

ANALYSIS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "stance",
        "conviction",
        "sentiment_label",
        "sentiment_score",
        "thesis",
        "catalysts",
        "risks",
        "invalidation",
        "recency_note",
    ],
    "properties": {
        "stance": {
            "type": "string",
            "enum": ["BULLISH", "BEARISH", "NEUTRAL"],
            "description": "Directional lean for the next trading session, from sentiment and fundamentals only.",
        },
        "conviction": {
            "type": "integer",
            "description": "0-100 confidence in the stance. Above 75 requires recent corroborated catalysts.",
        },
        "sentiment_label": {
            "type": "string",
            "enum": ["VERY_NEGATIVE", "NEGATIVE", "NEUTRAL", "POSITIVE", "VERY_POSITIVE"],
        },
        "sentiment_score": {
            "type": "number",
            "description": "-1.0 (max bearish) to 1.0 (max bullish) news/sentiment score.",
        },
        "thesis": {
            "type": "string",
            "description": "3-6 sentences. Why the stance, citing what was found. No price-action commentary.",
        },
        "catalysts": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete near-term drivers, each one line. Empty array if none found.",
        },
        "risks": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What would hurt this stance, each one line.",
        },
        "invalidation": {
            "type": "string",
            "description": "The news or event that would make you abandon this stance.",
        },
        "recency_note": {
            "type": "string",
            "description": "How current the evidence is, and what you could not verify.",
        },
    },
}


class AiUnavailable(Exception):
    """Raised when the expert cannot be consulted (no key, bad key, API error)."""

    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass
class AdvisorConfig:
    model: str = "gpt-5"
    web_search_enabled: bool = True
    gate_enabled: bool = False
    min_conviction: int = 60
    require_agreement: bool = True
    cache_ttl_sec: int = 900


@dataclass
class ExpertView:
    symbol: str
    stance: str
    conviction: int
    sentiment_label: str
    sentiment_score: float
    thesis: str
    catalysts: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    invalidation: str = ""
    recency_note: str = ""
    sources: list[dict] = field(default_factory=list)
    model: str = ""
    web_search_used: bool = False
    latency_ms: int = 0
    created_at: str = ""
    analysis_id: int | None = None
    age_sec: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateDecision:
    allowed: bool
    reason: str
    pending: bool = False
    view: ExpertView | None = None


class AiAdvisor:
    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, ExpertView]] = {}
        self._inflight: set[str] = set()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_CALLS)
        self._tasks: set[asyncio.Task] = set()
        self._config: AdvisorConfig | None = None
        self.last_error: str | None = None

    # ----- config and credentials -------------------------------------

    async def config(self, refresh: bool = False) -> AdvisorConfig:
        if self._config is not None and not refresh:
            return self._config
        async with async_session() as session:
            row = await session.get(AiConfig, 1)
            self._config = (
                AdvisorConfig(
                    model=row.model,
                    web_search_enabled=row.web_search_enabled,
                    gate_enabled=row.gate_enabled,
                    min_conviction=row.min_conviction,
                    require_agreement=row.require_agreement,
                    cache_ttl_sec=row.cache_ttl_sec,
                )
                if row
                else AdvisorConfig()
            )
        return self._config

    def invalidate_config(self) -> None:
        self._config = None

    async def has_key(self) -> bool:
        return await self._api_key(required=False) is not None

    async def _api_key(self, required: bool = True) -> str | None:
        async with async_session() as session:
            row = await session.scalar(select(AiCredential).where(AiCredential.provider == "openai"))
        if row is None:
            if required:
                raise AiUnavailable("No OpenAI API key saved. Add one in Settings → AI Trading Expert.", 428)
            return None
        try:
            return get_vault().decrypt(row.api_key_encrypted)
        except Exception as exc:  # noqa: BLE001
            if required:
                raise AiUnavailable(
                    "Stored OpenAI key could not be decrypted — the ENCRYPTION_KEY in backend/.env "
                    "has changed. Re-enter the key in Settings.",
                    500,
                ) from exc
            return None

    # ----- provider call ----------------------------------------------

    async def test_connection(self, model: str | None = None) -> dict:
        """Validates the key and that the chosen model is reachable. Uses the
        models endpoint rather than a generation so a sanity check costs
        nothing.
        """
        key = await self._api_key()
        model = model or (await self.config()).model
        async with httpx.AsyncClient(timeout=30.0) as client:
            res = await client.get(OPENAI_MODELS_URL, headers={"Authorization": f"Bearer {key}"})
            if res.status_code == 401:
                raise AiUnavailable("OpenAI rejected the key (401). Check it was pasted in full.", 401)
            if res.status_code >= 400:
                raise AiUnavailable(f"OpenAI returned {res.status_code}: {res.text[:300]}", 502)
            available = {m["id"] for m in res.json().get("data", [])}

        return {
            "ok": True,
            "model": model,
            "model_available": model in available,
            "detail": (
                f"Key is valid and '{model}' is available on this account."
                if model in available
                else f"Key is valid, but '{model}' is not in this account's model list. "
                f"Pick one of: {', '.join(sorted(m for m in available if m.startswith('gpt'))[:8])}"
            ),
        }

    def _payload(self, symbol: str, model: str, web_search: bool, search_tool: str) -> dict:
        today = dt.date.today().isoformat()
        prompt = (
            f"Today is {today}. Give me your expert view on the NSE-listed Indian equity {symbol} "
            f"for the next intraday session.\n\n"
            f"Search for current news, results, analyst actions, sector moves, flows and any macro or "
            f"regulatory development touching {symbol}. Report what you actually find. If the name is "
            f"quiet with no material news, say that and return NEUTRAL with low conviction.\n\n"
            f"Remember: no price-action, chart or technical commentary of any kind."
        )
        payload: dict = {
            "model": model,
            "instructions": SYSTEM_PROMPT,
            "input": prompt,
            "max_output_tokens": 16000,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "expert_view",
                    "strict": True,
                    "schema": ANALYSIS_SCHEMA,
                }
            },
        }
        if supports_reasoning(model):
            payload["reasoning"] = {"effort": "medium"}
        if web_search:
            payload["tools"] = [{"type": search_tool}]
        return payload

    @staticmethod
    def _extract(data: dict) -> tuple[str, list[dict], bool]:
        """Pulls the JSON text, its URL citations, and whether a search ran."""
        text_parts: list[str] = []
        sources: list[dict] = []
        searched = False
        for item in data.get("output", []):
            if item.get("type") in ("web_search_call", "web_search_preview_call"):
                searched = True
            if item.get("type") != "message":
                continue
            for chunk in item.get("content", []):
                if chunk.get("type") != "output_text":
                    continue
                text_parts.append(chunk.get("text", ""))
                for note in chunk.get("annotations", []) or []:
                    if note.get("type") == "url_citation" and note.get("url"):
                        sources.append({"title": note.get("title") or note["url"], "url": note["url"]})
        # De-duplicate citations while preserving the order they were cited in.
        seen: set[str] = set()
        unique = [s for s in sources if not (s["url"] in seen or seen.add(s["url"]))]
        return "".join(text_parts), unique, searched

    async def _call_openai(self, symbol: str, cfg: AdvisorConfig) -> tuple[dict, list[dict], bool, int]:
        key = await self._api_key()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        started = time.perf_counter()

        # The hosted search tool has been shipped under two type names; try the
        # current one, fall back rather than failing the whole analysis.
        attempts: list[tuple[bool, str]] = (
            [(True, "web_search"), (True, "web_search_preview"), (False, "")]
            if cfg.web_search_enabled
            else [(False, "")]
        )

        last_error = ""
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC) as client:
            for use_search, tool_name in attempts:
                res = await client.post(
                    OPENAI_RESPONSES_URL,
                    headers=headers,
                    json=self._payload(symbol, cfg.model, use_search, tool_name),
                )
                if res.status_code == 401:
                    raise AiUnavailable("OpenAI rejected the key (401). Re-check it in Settings.", 401)
                if res.status_code == 429:
                    raise AiUnavailable("OpenAI rate limit or quota exhausted (429).", 429)
                if res.status_code >= 400:
                    last_error = f"{res.status_code}: {res.text[:400]}"
                    continue

                data = res.json()
                if data.get("status") == "incomplete":
                    reason = (data.get("incomplete_details") or {}).get("reason", "unknown")
                    raise AiUnavailable(f"OpenAI returned an incomplete response ({reason}).", 502)

                text, sources, searched = self._extract(data)
                if not text.strip():
                    last_error = "empty response body"
                    continue
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    last_error = "response was not valid JSON"
                    continue

                latency_ms = int((time.perf_counter() - started) * 1000)
                return parsed, sources, searched, latency_ms

        raise AiUnavailable(f"OpenAI call failed — {last_error}", 502)

    # ----- analysis ----------------------------------------------------

    async def analyze(self, symbol: str, requested_by: str = "USER") -> ExpertView:
        symbol = symbol.upper()
        cfg = await self.config()
        async with self._semaphore:
            try:
                parsed, sources, searched, latency_ms = await self._call_openai(symbol, cfg)
            except AiUnavailable as exc:
                self.last_error = exc.message
                raise

        self.last_error = None
        view = ExpertView(
            symbol=symbol,
            stance=parsed.get("stance", "NEUTRAL"),
            conviction=int(parsed.get("conviction", 0)),
            sentiment_label=parsed.get("sentiment_label", "NEUTRAL"),
            sentiment_score=float(parsed.get("sentiment_score", 0.0)),
            thesis=parsed.get("thesis", ""),
            catalysts=list(parsed.get("catalysts") or []),
            risks=list(parsed.get("risks") or []),
            invalidation=parsed.get("invalidation", ""),
            recency_note=parsed.get("recency_note", ""),
            sources=sources,
            model=cfg.model,
            web_search_used=searched,
            latency_ms=latency_ms,
            created_at=dt.datetime.utcnow().isoformat(),
        )
        view.analysis_id = await self._persist(view, requested_by)
        self._cache[symbol] = (time.monotonic(), view)
        return view

    async def _persist(self, view: ExpertView, requested_by: str) -> int:
        async with async_session() as session:
            row = AiAnalysis(
                symbol=view.symbol,
                stance=view.stance,
                conviction=view.conviction,
                sentiment_label=view.sentiment_label,
                sentiment_score=view.sentiment_score,
                thesis=view.thesis,
                catalysts=json.dumps(view.catalysts),
                risks=json.dumps(view.risks),
                invalidation=view.invalidation,
                sources=json.dumps(view.sources),
                recency_note=view.recency_note,
                model=view.model,
                web_search_used=view.web_search_used,
                latency_ms=view.latency_ms,
                requested_by=requested_by,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id

    async def record_gate_outcome(
        self, analysis_id: int | None, side: str, passed: bool, reason: str, trade_id: int | None = None
    ) -> None:
        """Stamps the gate's verdict onto the analysis it was based on, so a
        skipped entry can be explained months later.
        """
        if analysis_id is None:
            return
        async with async_session() as session:
            row = await session.get(AiAnalysis, analysis_id)
            if row is None:
                return
            row.gate_side = side
            row.gate_passed = passed
            row.gate_reason = reason
            if trade_id is not None:
                row.trade_id = trade_id
            await session.commit()

    # ----- cache and background prefetch --------------------------------

    def cached(self, symbol: str, ttl_sec: int) -> ExpertView | None:
        entry = self._cache.get(symbol.upper())
        if entry is None:
            return None
        stamped, view = entry
        age = time.monotonic() - stamped
        if age > ttl_sec:
            return None
        view.age_sec = int(age)
        return view

    def prefetch(self, symbol: str, requested_by: str = "PREFETCH") -> None:
        """Fire-and-forget warm-up. Deduplicated per symbol so a burst of
        candles cannot queue the same expensive call ten times.
        """
        symbol = symbol.upper()
        if symbol in self._inflight:
            return
        self._inflight.add(symbol)

        async def run() -> None:
            from app.services.broadcaster import broadcaster

            try:
                view = await self.analyze(symbol, requested_by=requested_by)
                await broadcaster.publish(
                    "log",
                    {
                        "level": "INFO",
                        "message": f"[AI] {symbol}: {view.stance} (conviction {view.conviction}, "
                        f"sentiment {view.sentiment_label})",
                    },
                )
                await broadcaster.publish("ai_view", view.to_dict())
            except AiUnavailable as exc:
                await broadcaster.publish(
                    "log", {"level": "WARN", "message": f"[AI] {symbol} analysis unavailable: {exc.message}"}
                )
            except Exception as exc:  # noqa: BLE001
                await broadcaster.publish(
                    "log", {"level": "ERROR", "message": f"[AI] {symbol} analysis failed: {exc}"}
                )
            finally:
                self._inflight.discard(symbol)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def prefetch_many(self, symbols: list[str]) -> None:
        for symbol in symbols:
            self.prefetch(symbol)

    # ----- the bot gate --------------------------------------------------

    async def gate(self, symbol: str, side: str) -> GateDecision:
        """Consulted by the strategy runner before an entry. Reads the cache
        only — never awaits the provider — so the tick loop keeps running at
        full speed whatever the network is doing.
        """
        cfg = await self.config()
        if not cfg.gate_enabled:
            return GateDecision(allowed=True, reason="AI gate disabled")

        if not await self.has_key():
            return GateDecision(
                allowed=False,
                reason="AI gate is enabled but no OpenAI key is saved — entry blocked. "
                "Add a key in Settings or turn the gate off.",
            )

        view = self.cached(symbol, cfg.cache_ttl_sec)
        if view is None:
            self.prefetch(symbol, requested_by="BOT_GATE")
            return GateDecision(
                allowed=False,
                pending=True,
                reason=f"No fresh expert view for {symbol} yet — requesting one, will re-check next candle.",
            )

        wanted = "BULLISH" if side == "BUY" else "BEARISH"
        if cfg.require_agreement and view.stance != wanted:
            return GateDecision(
                allowed=False,
                view=view,
                reason=f"Expert is {view.stance} on {symbol}, signal wanted {side} ({wanted}).",
            )

        if view.conviction < cfg.min_conviction:
            return GateDecision(
                allowed=False,
                view=view,
                reason=f"Expert conviction {view.conviction} is below the {cfg.min_conviction} minimum.",
            )

        return GateDecision(
            allowed=True,
            view=view,
            reason=f"Expert {view.stance} on {symbol} with conviction {view.conviction} "
            f"(sentiment {view.sentiment_label}).",
        )

    async def status(self) -> dict:
        cfg = await self.config()
        return {
            "configured": await self.has_key(),
            "provider": "openai",
            "model": cfg.model,
            "web_search_enabled": cfg.web_search_enabled,
            "gate_enabled": cfg.gate_enabled,
            "min_conviction": cfg.min_conviction,
            "require_agreement": cfg.require_agreement,
            "cache_ttl_sec": cfg.cache_ttl_sec,
            "cached_symbols": sorted(self._cache.keys()),
            "in_flight": sorted(self._inflight),
            "last_error": self.last_error,
        }


ai_advisor = AiAdvisor()
