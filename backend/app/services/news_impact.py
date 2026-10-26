"""News impact: is a stock's news good or bad for it, and what to do about a position.

Each headline from `stock_news` (NSE announcements + Google News) is read two ways:

* **Rules** (always available, no key): a lexicon of Indian corporate-news phrases
  ("bags order", "Q2 profit jumps", "SEBI bars", "USFDA warning letter", "CEO resigns" …),
  each with a weight. A headline's score is -1..+1; |score| sets the impact
  (LOW < 0.35 ≤ MEDIUM < 0.7 ≤ HIGH). A few phrases are **critical** (fraud, raids,
  defaults, insolvency, trading bans, top-management exits, plant accidents, takeovers).
* **AI** (when an OpenAI key is saved in Settings → AI Trading Expert and `use_ai` is on):
  the same headlines go to OpenAI (no web search, a JSON answer) for a per-headline
  sentiment / impact / critical flag and an overall view. Cached per set of headlines; a
  failure falls back to the rules and says why (`ai_error`).

The stock's overall view weights recent headlines more (`HALF_LIFE_H`). `action` turns it
into a plain suggestion given the position: KEEP, EXIT (sell / cover), BUY or SHORT
(watch for an entry), AVOID, NO_ACTION. Information only: nothing here places, closes or
changes an order; it is not investment advice.

**All F&O** (`all_fno`, on by default): each pass also reads NSE's market-wide announcements in ONE
request (`stock_news.nse_market_announcements`), keeps the F&O stocks' filings scored by the rules in
`NewsWatcher.market` (the News feed, `GET /api/news-impact/feed`), and for a HIGH-impact or critical
filing on a stock that is not armed or held saves a pop-up `NewsAlert` (Telegram only with
`fno_telegram`; never a call, never AI). Google News stays per stock, for the armed / held ones only.

`NewsWatcher` (started in the desk lifespan) reads the news of every armed or held stock
(plus `extra_symbols`) every `every_min` minutes on trading days, and for each headline it
has not alerted before, published within `ALERT_MAX_AGE_H`, with at least `min_impact`, saves a
`NewsAlert` row (the screens' pop-up reads it), sends Telegram (`telegram`), and for a
**critical** one on a held stock (critical phrase, or HIGH impact against the position) rings
through the phone-call channel (`call_critical`; market hours and the 5-minute cooldown apply).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from app.core.market_clock import IST, is_trading_day
from app.models.database import NewsAlert, NewsWatchSettings, async_session

log = logging.getLogger(__name__)

HALF_LIFE_H = 24.0
MAX_AGE_DAYS = 3  # the News feed keeps filings this long (shown as expired once past their validity)
VALID_CLOSE = dt.time(15, 30)  # a headline counts until this time on the next trading day (valid_until)
ALERT_MAX_AGE_H = 6.0  # a headline older than this when first seen is not alerted
IMPACT_LEVELS = ("LOW", "MEDIUM", "HIGH")
WATCH_START = dt.time(7, 30)
WATCH_END = dt.time(20, 0)
MAX_WATCH = 120  # stocks read in full each pass: the bots' armed / held first, then extra, then the watch lists
MARKET_KEEP = 300  # all-F&O feed items kept in memory for the News feed
AI_TTL_SEC = 1800
START_DELAY_SEC = 30
ENTRY_POLL_SEC = 60  # NSE's market-wide filings while a bot takes news entries (news_entry.py)
ENTRY_MAX_AGE_MIN = 240  # older headlines are not handed to the news-entry bot
ENTRY_START = dt.time(9, 15)
ENTRY_END = dt.time(15, 30)
AI_TIMEOUT_SEC = 90.0
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"

# (pattern, weight). Patterns are matched on lower-case "title. detail".
POSITIVE: list[tuple[str, float]] = [
    (r"\b(bags?|wins?|won|secures?|receives?|gets?|bagged|secured|received)\b.{0,40}\b(orders?|contracts?|deal|mandate|project|tender)", 0.6),
    (r"\border (book|inflow|win)s?\b", 0.4),
    (r"\b(profit|pat|net income|earnings|ebitda|revenue|sales|income)\b.{0,30}\b(jumps?|surges?|soars?|rises?|grows?|up|climbs?|increases?|doubles?|beats?|higher|record)", 0.6),
    (r"\b(beats?|tops?|exceeds?)\b.{0,25}\b(estimates?|expectations?|forecasts?|street)", 0.6),
    (r"\b(upgrades?|upgraded|raises? (target|price target|tp)|target (raised|hiked)|buy rating|outperform|overweight|initiates? .{0,20}\bbuy)", 0.55),
    (r"\b(buy ?back|bonus issue|bonus shares|special dividend|stock split|dividend)\b", 0.4),
    (r"\b(approval|approves?|approved|nod|clearance|licen[cs]e)\b.{0,40}\b(usfda|fda|dcgi|rbi|sebi|cci|government|board|ministry)", 0.45),
    (r"\b(usfda|fda|dcgi)\b.{0,30}\b(approval|nod|clears?|eir|zero observations?)", 0.55),
    (r"\b(acquires?|acquisition|to acquire|merger|amalgamation|partnership|joint venture|ties? up|collaborat\w+|expands?|expansion|capacity addition|new plant|launch(es|ed)?)\b", 0.3),
    (r"\b(stake (hike|increase)|raises? stake|promoters? (buy|increase|raise)|block deal.{0,20}\bbuy)", 0.4),
    (r"\b(rating upgrade|upgrades? .{0,20}rating|outlook (to|revised to) positive)", 0.5),
    (r"\b(record high|all[- ]time high|52[- ]week high|rallies|rally|surges?|soars?|zooms?|jumps?|skyrockets?)\b", 0.3),
    (r"\b(open offer|takeover bid|to be acquired by)\b", 0.7),
    (r"\b(debt[- ]free|deleverag\w+|repays? debt|reduces? debt)\b", 0.35),
    # The share price itself moving up (the stock, its shares or its ADRs), or up by a stated %.
    (r"\b(shares?|stocks?|scrip|adrs?)\b.{0,40}\b(rises?|rose|gains?|gained|climbs?|climbed|advances?|advanced|up)\b"
     r"|\b(rises?|rose|gains?|gained|climbs?|climbed|up)\b.{0,15}\b\d+(\.\d+)?\s?(%|per ?cent)", 0.3),
]
NEGATIVE: list[tuple[str, float]] = [
    (r"\b(profit|pat|net income|earnings|ebitda|revenue|sales|margins?)\b.{0,30}\b(falls?|fell|drops?|declines?|slumps?|plunges?|dips?|down|lower|shrinks?|halves|misses?|contracts?)", -0.6),
    (r"\b(net )?loss\b.{0,20}\b(widens?|reports?|posts?|swings?)|\bswings? to (a )?loss|\bposts? (a )?loss", -0.6),
    (r"\b(misses?|below)\b.{0,25}\b(estimates?|expectations?|forecasts?|street)", -0.6),
    (r"\b(downgrades?|downgraded|cuts? (target|price target|tp)|target (cut|lowered)|sell rating|underperform|underweight|reduce rating)", -0.55),
    (r"\b(penalty|fined?|fines|show[- ]cause|notice from|tax demand|gst demand|demand notice)\b", -0.4),
    (r"\b(probe|investigation|inquiry|enquiry|scrutiny|whistle ?blower|forensic audit)\b", -0.5),
    (r"\b(usfda|fda)\b.{0,40}\b(warning letter|import alert|observations?|form 483|oai)", -0.6),
    (r"\b(pledge[ds]?|pledging)\b.{0,20}\b(shares?|stake)|\bpromoters? (sell|offload|cut|reduce)|\bstake sale\b|\bofs\b|block deal.{0,20}\bsell", -0.4),
    (r"\b(strike|lockout|shutdown|shuts? down|halts? (production|operations)|suspends? (production|operations)|disruption)\b", -0.45),
    (r"\b(rating downgrade|downgrades? .{0,20}rating|outlook (to|revised to) negative|credit watch negative)", -0.55),
    (r"\b(recall|recalls)\b", -0.35),
    (r"\b(slumps?|plunges?|tanks?|crashes?|tumbles?|sinks?|slides?|52[- ]week low|lower circuit|sell[- ]off)\b", -0.35),
    (r"\b(lawsuit|sued|litigation|arbitration award against|court (order|rejects))\b", -0.35),
    (r"\b(delay(s|ed)?|postpone[ds]?|deferr?ed|cancel(s|led)?)\b.{0,30}\b(results?|project|order|launch|ipo|listing|deal)", -0.3),
    # The share price itself moving down (the stock, its shares or its ADRs), or down by a stated %.
    (r"\b(shares?|stocks?|scrip|adrs?)\b.{0,40}\b(falls?|fell|drops?|dropped|slips?|slipped|declines?|declined|"
     r"down|lose|loses|lost|sheds?|weaken\w*)\b"
     r"|\b(falls?|fell|drops?|dropped|slips?|slipped|declines?|declined|down|lower|sheds?)\b.{0,15}\b\d+(\.\d+)?\s?(%|per ?cent)", -0.3),
    (r"\bunder pressure\b|\bweak (session|trade|trading|show|quarter|guidance|outlook|demand)\b"
     r"|\b(cuts?|lowers?|trims?) (\w+ ){0,2}(guidance|outlook|forecast)\b", -0.3),
    # A regulator or government acting against the company (not a production halt, matched above).
    (r"\bcrackdown\b|\bsuspend(s|ed)?\b.{0,40}\b(from|programme|program|licen[cs]e|registration)\b|(?<!lifts )(?<!lifted )(?<!removes )\bban(s|ned)? on\b", -0.35),
]
# Critical phrases: always HIGH impact, and a phone call when the stock is held.
CRITICAL_NEG: list[str] = [
    r"\bfraud\b", r"\bscam\b", r"\b(it|income tax|ed|cbi|dri|gst) (raids?|searches?)\b", r"\braids?\b",
    r"\bsebi\b.{0,30}\b(bars?|bans?|barred|banned|order against|interim order|impounds?)",
    r"\b(defaults?|defaulted)\b.{0,20}\b(on|payment|repayment|interest|loan|debt|ncd|bond)",
    r"\b(insolvency|bankruptcy|nclt admits|cirp|liquidation)\b",
    r"\b(ceo|md|managing director|cfo|chairman|chairperson|auditor)\b.{0,25}\b(resigns?|resignation|quits?|steps? down|sacked|arrested)",
    r"\b(trading|shares?) suspended\b|\bsuspension of trading\b|\bdelist(ed|ing)?\b",
    r"\b(fire|explosion|blast|accident|gas leak)\b.{0,30}\b(plant|factory|unit|facility|site)",
    r"\bimport alert\b", r"\bforensic audit\b",
]
CRITICAL_POS: list[str] = [r"\bopen offer\b", r"\btakeover\b", r"\bto be acquired\b", r"\bacquired by\b"]
SOFTENERS = re.compile(r"\b(denies|denied|refutes?|clarifies|clarification|baseless|no impact|not material|rumou?r|seeks? clarification)\b")

_POS = [(re.compile(p), w) for p, w in POSITIVE]
_NEG = [(re.compile(p), w) for p, w in NEGATIVE]
_CNEG = [re.compile(p) for p in CRITICAL_NEG]
_CPOS = [re.compile(p) for p in CRITICAL_POS]


def impact_of(score: float) -> str:
    a = abs(score)
    return "HIGH" if a >= 0.7 else "MEDIUM" if a >= 0.35 else "LOW"


def sentiment_of(score: float) -> str:
    return "POSITIVE" if score >= 0.15 else "NEGATIVE" if score <= -0.15 else "NEUTRAL"


def score_text(title: str, detail: str | None = None) -> dict:
    """One headline by the rules: score -1..+1, sentiment, impact, critical, the phrases that matched."""
    text = f"{title or ''}. {detail or ''}".lower()
    hits: list[str] = []
    score = 0.0
    for rx, w in _POS + _NEG:
        m = rx.search(text)
        if m:
            score += w
            hits.append(m.group(0).strip()[:60])
    critical = None
    for rx in _CNEG:
        m = rx.search(text)
        if m:
            critical, score = "NEGATIVE", min(score, 0.0) - 0.9
            hits.append(m.group(0).strip()[:60])
            break
    if critical is None:
        for rx in _CPOS:
            m = rx.search(text)
            if m:
                critical, score = "POSITIVE", max(score, 0.0) + 0.8
                hits.append(m.group(0).strip()[:60])
                break
    if SOFTENERS.search(text):
        score *= 0.4  # a denial or clarification: the story is weaker than its words
        critical = None
    score = max(-1.0, min(1.0, score))
    return {
        "score": round(score, 3),
        "sentiment": sentiment_of(score),
        "impact": "HIGH" if critical else impact_of(score),
        "critical": critical is not None,
        "why": ", ".join(dict.fromkeys(hits)) or "no market-moving phrase",
    }


def _age_hours(at: str | None, now: dt.datetime) -> float | None:
    if not at:
        return None
    try:
        when = dt.datetime.fromisoformat(at)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    return max((now - when).total_seconds() / 3600, 0.0)


def valid_until(at: str | None) -> dt.datetime | None:
    """When a headline stops counting: 15:30 IST on the next trading day (Mon-Fri) after the day it came out.

    Out today, valid till tomorrow's close; out on Friday (or the weekend), valid till Monday's close. NSE
    holidays are not known here (like `market_clock`), so a holiday counts as a trading day.
    """
    if not at:
        return None
    try:
        when = dt.datetime.fromisoformat(at)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=IST)
    day = when.astimezone(IST).date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    return dt.datetime.combine(day, VALID_CLOSE, tzinfo=IST)


def is_valid(at: str | None, now: dt.datetime) -> bool:
    """Still inside its validity (an undated headline counts as valid)."""
    until = valid_until(at)
    return until is None or now <= until


def with_validity(item: dict, now: dt.datetime) -> dict:
    until = valid_until(item.get("at"))
    return {**item, "valid_until": until.isoformat(timespec="minutes") if until else None, "valid": until is None or now <= until}


# A headline that does not name the stock (Google matched the name somewhere in the article, e.g. a TCS results
# story for Wipro): this share of its score counts in the overall view.
OFF_TARGET_WEIGHT = 0.25
_NAME_SUFFIX = re.compile(r"\b(limited|ltd\.?|india ltd|\(india\))\s*$", re.I)


def about_stock(item: dict, symbol: str, name: str | None) -> bool:
    """True when the headline names the stock (its symbol or company name). NSE filings are always its own."""
    if item.get("kind") == "announcement":
        return True
    text = f"{item.get('title') or ''} {item.get('detail') or ''}".lower()
    terms = {symbol.lower()}
    core = _NAME_SUFFIX.sub("", (name or "").strip()).strip(" .,").lower()
    if core:
        terms.add(core)
        first = core.split()[0]
        if len(first) >= 4:
            terms.add(first)
    return any(re.search(rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])", text) for t in terms if t)


def mark_relevance(items: list[dict], symbol: str, name: str | None) -> list[dict]:
    out = []
    for it in items:
        about = about_stock(it, symbol, name)
        if not about:
            it = {**it, "why": f"{it.get('why') or 'no market-moving phrase'} · mainly about another company"}
        out.append({**it, "about": about})
    return out


def overall(items: list[dict], now: dt.datetime | None = None) -> dict:
    """Recency-weighted view of a stock's analysed headlines (each needs `score`, `at`). A headline marked
    `about: False` (mark_relevance) counts OFF_TARGET_WEIGHT of its score."""
    now = now or dt.datetime.now(IST)
    num = den = 0.0
    counted = 0
    critical = False
    for it in items:
        age = _age_hours(it.get("at"), now)
        if age is None:
            age = 24.0  # undated: count it, as a day old
        if not is_valid(it.get("at"), now):
            continue  # past its validity (the next trading day's close)
        off = it.get("about") is False
        weight = 0.5 ** (age / HALF_LIFE_H) * (1.5 if it.get("impact") == "HIGH" and not off else 1.0)
        # Another company's headline still counts as a headline, but its score only a quarter: it pulls the view
        # toward neutral rather than either way.
        part = OFF_TARGET_WEIGHT if off else 1.0
        num += weight * part * float(it.get("score") or 0)
        den += weight
        counted += 1
        critical = critical or (bool(it.get("critical")) and age <= 24 and it.get("about") is not False)
    score = num / den if den else 0.0
    # Headlines pulling both ways read as mixed rather than a weak average.
    signs = {
        it.get("sentiment")
        for it in items
        if is_valid(it.get("at"), now) and it.get("about") is not False
    }
    label = sentiment_of(score)
    if label == "NEUTRAL" and {"POSITIVE", "NEGATIVE"} <= signs:
        label = "MIXED"
    return {"score": round(score, 3), "sentiment": label, "impact": impact_of(score), "critical": critical, "counted": counted}


ACTIONS = {
    "KEEP": "Keep",
    "EXIT": "Sell / exit",
    "COVER": "Buy back (cover the short)",
    "TIGHTEN": "Keep, but tighten the stop",
    "BUY": "Buy candidate: wait for an entry signal",
    "SHORT": "Short candidate: wait for an entry signal",
    "AVOID": "Avoid a new buy",
    "PREFER_LONG": "Lean long: favour buy signals",
    "PREFER_SHORT": "Lean short: favour short signals",
    "NO_ACTION": "No news bias: trade signals both ways",
}


def action(view: dict, side: str | None) -> dict:
    """KEEP / EXIT / … for the position (LONG, SHORT or None for flat), with the reason in words."""
    s = float(view.get("score") or 0)
    if side == "LONG":
        if s <= -0.5 or (view.get("critical") and s < 0):
            code, why = "EXIT", "Strong negative news against your long position."
        elif s <= -0.2:
            code, why = "TIGHTEN", "Negative news against your long; protect the profit or cut the risk."
        else:
            code, why = "KEEP", "The news supports or does not hurt your long." if s >= 0.15 else "No news strong enough to act on."
    elif side == "SHORT":
        if s >= 0.5 or (view.get("critical") and s > 0):
            code, why = "COVER", "Strong positive news against your short position."
        elif s >= 0.2:
            code, why = "TIGHTEN", "Positive news against your short; protect it."
        else:
            code, why = "KEEP", "The news supports or does not hurt your short." if s <= -0.15 else "No news strong enough to act on."
    else:
        # Flat: which direction the news favours for a NEW entry, long or short, never only buying.
        if s >= 0.5:
            code, why = "BUY", (
                "Strong positive news: take buy signals, skip short signals. Do not chase a gap up: wait for an entry signal."
            )
        elif s >= 0.2:
            code, why = "PREFER_LONG", "Positive news: buy signals are favoured; be careful with shorts."
        elif s <= -0.5:
            code, why = "SHORT", (
                "Strong negative news: take short signals, skip buy signals. Do not chase a gap down: wait for an entry signal."
            )
            if view.get("critical"):
                why += " Critical news can gap the stock or put it in the F&O ban: keep the size small and the stop on."
        elif s <= -0.2:
            code, why = "PREFER_SHORT", "Negative news: short signals are favoured; avoid fresh buys."
        else:
            code, why = "NO_ACTION", "No news strong enough to favour a side: trade the signals both ways."
    return {"code": code, "label": ACTIONS[code], "why": why}


def plan(view: dict) -> dict:
    """What to do in every case: a new entry while flat, and an open long or short."""
    return {"flat": action(view, None), "long": action(view, "LONG"), "short": action(view, "SHORT")}


FRESH_HOURS = 6.0  # strong news newer than this can be traded now; older news has usually been priced in

TIMING = {
    "BUY_NOW": "Buy now",
    "BUY_LATER": "Buy later",
    "NO_BUY": "Don't buy on news",
    "DONT_BUY": "Don't buy",
    "SHORT_NOW": "Short now",
    "SHORT_LATER": "Short later",
    "NO_SHORT": "Don't short on news",
    "DONT_SHORT": "Don't short",
}


def _fresh_hours(items: list[dict], now: dt.datetime) -> float | None:
    """Age of the newest headline that moves the stock (|score| >= 0.2, about this company)."""
    ages = [
        a for it in items
        if it.get("about") is not False and abs(float(it.get("score") or 0)) >= 0.2
        and (a := _age_hours(it.get("at"), now)) is not None and a >= 0
    ]
    return min(ages) if ages else None


def timing(view: dict, items: list[dict], now: dt.datetime) -> dict:
    """When to act on the news while flat: buy now, buy later, or don't buy (and the same for a short).

    Strong news (|score| >= 0.5) newer than FRESH_HOURS is "now"; strong but older news, or a milder lean, is
    "later" (on a pullback or the bot's next signal: the first move is often already in the price); no clear
    lean is "don't X on news"; news the other way is "don't X". Information only."""
    s = float(view.get("score") or 0)
    fresh = _fresh_hours(items, now)
    is_fresh = fresh is not None and fresh <= FRESH_HOURS
    age = f"{fresh:.0f} h old" if fresh is not None else "undated"

    def side(sign: int, now_code: str, later_code: str, none_code: str, against_code: str, word: str, dip: str) -> dict:
        v = s * sign
        if v >= 0.5 and is_fresh:
            code, why = now_code, (
                f"Strong {'good' if sign > 0 else 'bad'} news, fresh ({age}): {word} now with a stop, or on the first "
                f"{word} signal. Don't chase a gap of more than about 2%."
            )
        elif v >= 0.5:
            code, why = later_code, (
                f"Strong {'good' if sign > 0 else 'bad'} news but {age}: the first move is probably in the price. "
                f"{word.capitalize()} later, on a {dip} or the bot's next {word} signal."
            )
        elif v >= 0.2:
            code, why = later_code, f"Mild lean ({age}): {word} later, only on the bot's {word} signal."
        elif v > -0.2:
            code, why = none_code, f"No news strong enough to {word} on; trade the signals as usual."
        else:
            code, why = against_code, f"The news points the other way: don't {word}."
            if view.get("critical"):
                why += " Critical news: the stock can gap or enter the F&O ban."
        return {"code": code, "label": TIMING[code], "why": why}

    return {
        "buy": side(1, "BUY_NOW", "BUY_LATER", "NO_BUY", "DONT_BUY", "buy", "dip"),
        "short": side(-1, "SHORT_NOW", "SHORT_LATER", "NO_SHORT", "DONT_SHORT", "short", "bounce"),
        "fresh_hours": round(fresh, 1) if fresh is not None else None,
    }


# ---- AI ----------------------------------------------------------------------

AI_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["items", "overall"],
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["index", "sentiment", "impact", "critical", "why"],
                "properties": {
                    "index": {"type": "integer"},
                    "sentiment": {"type": "string", "enum": ["POSITIVE", "NEGATIVE", "NEUTRAL"]},
                    "impact": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
                    "critical": {"type": "boolean"},
                    "why": {"type": "string"},
                },
            },
        },
        "overall": {
            "type": "object",
            "additionalProperties": False,
            "required": ["sentiment", "score", "summary"],
            "properties": {
                "sentiment": {"type": "string", "enum": ["POSITIVE", "NEGATIVE", "NEUTRAL", "MIXED"]},
                "score": {"type": "number"},
                "summary": {"type": "string"},
            },
        },
    },
}
AI_INSTRUCTIONS = (
    "You are an equity news analyst for an Indian intraday trader (NSE). For each numbered headline about "
    "the stock, judge its likely effect on the stock price over the next few sessions: sentiment POSITIVE / "
    "NEGATIVE / NEUTRAL, impact LOW / MEDIUM / HIGH, and critical = true only for events that can move the "
    "stock sharply at once (fraud, raids, regulatory bans, defaults, insolvency, top-management exits, "
    "plant accidents, takeovers, very large surprises in results). Old, generic or market-wide headlines are "
    "LOW. In `why` give one short sentence. Then give an overall sentiment, a score from -1 (very negative) "
    "to +1 (very positive) weighting recent and important headlines more, and a two-sentence summary. Use "
    "only the headlines given; do not invent facts or prices."
)
SENT_SCORE = {"POSITIVE": 1.0, "NEGATIVE": -1.0, "NEUTRAL": 0.0}
IMPACT_SCALE = {"LOW": 0.25, "MEDIUM": 0.55, "HIGH": 0.85}

_ai_cache: dict[tuple[str, str], tuple[float, dict]] = {}


async def _openai_key() -> str | None:
    from app.services.ai_advisor import ai_advisor

    return await ai_advisor._api_key(required=False)  # noqa: SLF001


async def _ai_model() -> str:
    from app.services.ai_advisor import ai_advisor

    return (await ai_advisor.config()).model


async def ai_read(symbol: str, name: str | None, items: list[dict], now: dt.datetime | None = None) -> dict:
    """OpenAI's read of the headlines (cached per set of headlines). Raises on any failure."""
    from app.services.ai_advisor import supports_reasoning

    digest = hashlib.sha1("|".join(i.get("title", "") for i in items).encode()).hexdigest()
    now = now or dt.datetime.now(IST)
    digest = hashlib.sha1(f"{digest}|{now:%Y-%m-%d}".encode()).hexdigest()
    hit = _ai_cache.get((symbol, digest))
    if hit and time.monotonic() - hit[0] < AI_TTL_SEC:
        return hit[1]
    key = await _openai_key()
    if not key:
        raise RuntimeError("no OpenAI key saved (Settings → AI Trading Expert)")
    model = await _ai_model()
    lines = [
        f"{n}. [{i.get('source') or ''} {i.get('at') or 'undated'}] {i.get('title', '')}"
        + (f" — {i['detail'][:300]}" if i.get("detail") else "")
        for n, i in enumerate(items)
    ]
    payload: dict = {
        "model": model,
        "instructions": AI_INSTRUCTIONS,
        "input": f"Stock: {symbol} ({name or symbol}). Today: {now:%Y-%m-%d %H:%M} IST.\n\n" + "\n".join(lines),
        "max_output_tokens": 6000,
        "text": {"format": {"type": "json_schema", "name": "news_impact", "strict": True, "schema": AI_SCHEMA}},
    }
    if supports_reasoning(model):
        payload["reasoning"] = {"effort": "low"}
    async with httpx.AsyncClient(timeout=AI_TIMEOUT_SEC) as client:
        res = await client.post(OPENAI_RESPONSES_URL, headers={"Authorization": f"Bearer {key}"}, json=payload)
    if res.status_code >= 400:
        raise RuntimeError(f"OpenAI answered {res.status_code}")
    data = res.json()
    text = "".join(
        c.get("text", "")
        for o in data.get("output", [])
        if o.get("type") == "message"
        for c in o.get("content", [])
        if c.get("type") == "output_text"
    )
    parsed = json.loads(text)
    _ai_cache[(symbol, digest)] = (time.monotonic(), parsed)
    return parsed


def merge_ai(items: list[dict], ai: dict, now: dt.datetime | None = None) -> tuple[list[dict], dict]:
    """Per-headline AI calls replace the rule scores; the overall view is the AI's."""
    by_index = {int(x["index"]): x for x in ai.get("items", []) if isinstance(x, dict) and "index" in x}
    out = []
    for n, it in enumerate(items):
        x = by_index.get(n)
        if x:
            score = SENT_SCORE.get(x["sentiment"], 0.0) * IMPACT_SCALE.get(x["impact"], 0.25)
            it = {**it, "score": round(score, 3), "sentiment": x["sentiment"], "impact": x["impact"],
                  "critical": bool(x["critical"]), "why": x["why"], "by": "ai"}
        out.append(it)
    o = ai.get("overall") or {}
    view = overall(out, now)
    score = max(-1.0, min(1.0, float(o.get("score", view["score"]))))
    view.update({"score": round(score, 3), "sentiment": o.get("sentiment") or view["sentiment"],
                 "impact": impact_of(score), "summary": o.get("summary") or ""})
    return out, view


# ---- one stock ---------------------------------------------------------------


async def analyze(
    symbol: str, *, use_ai: bool = True, side: str | None = None, force: bool = False, day: dt.date | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """The stock's news, each headline scored, the overall view and what to do with `side` (LONG/SHORT/None),
    plus `plan` for every case. With `day` (a past date), that day's news as of its 15:30 close."""
    from app.services import stock_news

    news = await stock_news.stock_news(symbol, force=force, day=day)
    past = news.get("day") is not None
    now = stock_news.window(day)[1] if past and day else (now or dt.datetime.now(IST))
    items = [{**i, **score_text(i.get("title", ""), i.get("detail")), "by": "rules"} for i in news.get("items", [])]
    items = mark_relevance(items, news["symbol"], news.get("name"))
    items = [with_validity(i, now) for i in items]
    view = overall(items, now)
    method, ai_error = "rules", None
    if use_ai and items:
        try:
            ai = await ai_read(news["symbol"], news.get("name"), items[:25], now)
            head, view = merge_ai(items[:25], ai, now)
            items = head + items[25:]
            method = "ai"
        except Exception as exc:  # noqa: BLE001 - the rules answer stands
            ai_error = str(exc)[:200]
    return {
        "symbol": news["symbol"],
        "name": news.get("name"),
        "items": items,
        "overall": view,
        "action": action(view, side),
        "plan": plan(view),
        "timing": timing(view, items, now),
        "side": side,
        "day": news.get("day"),
        "as_of": now.isoformat(timespec="minutes"),
        "method": method,
        "ai_error": ai_error,
        "errors": news.get("errors", {}),
        "fetched_at": news.get("fetched_at"),
        "disclaimer": "Information only, not investment advice. Nothing is traded from this.",
    }


# ---- a past day, every armed stock -------------------------------------------

DAY_CONCURRENCY = 4
DAY_ITEMS = 6


async def day_report(day: dt.date, symbols: dict[str, dict], *, use_ai: bool = True) -> dict:
    """Every stock's news for `day` (as of its close): the view and what to do flat, long or short."""
    sem = asyncio.Semaphore(DAY_CONCURRENCY)

    async def one(symbol: str, entry: dict) -> dict:
        async with sem:
            try:
                r = await analyze(symbol, use_ai=use_ai, side=None, day=day)
            except Exception as exc:  # noqa: BLE001 - one stock's failure stays on its row
                return {"symbol": symbol, "armed": entry.get("armed", []), "error": str(exc)[:200]}
        items = [
            {k: it.get(k) for k in ("title", "source", "at", "url", "sentiment", "impact", "critical", "why")}
            for it in sorted(r["items"], key=lambda i: abs(float(i.get("score") or 0)), reverse=True)[:DAY_ITEMS]
        ]
        return {
            "symbol": symbol,
            "name": r.get("name"),
            "armed": entry.get("armed", []),
            "overall": r["overall"],
            "plan": r["plan"],
            "timing": r.get("timing"),
            "count": len(r["items"]),
            "items": items,
            "method": r["method"],
            "errors": r.get("errors", {}),
        }

    rows = await asyncio.gather(*(one(s, e) for s, e in symbols.items()))
    rank = {"NEGATIVE": 0, "POSITIVE": 0, "MIXED": 1, "NEUTRAL": 2}
    rows.sort(key=lambda r: (rank.get((r.get("overall") or {}).get("sentiment"), 3), -abs((r.get("overall") or {}).get("score") or 0)))
    from app.services.stock_news import window

    start, end = window(day)
    return {
        "day": day.isoformat(),
        "window": {"from": start.isoformat(timespec="minutes"), "to": end.isoformat(timespec="minutes")},
        "stocks": rows,
        "disclaimer": "Information only, not investment advice. Nothing is traded from this.",
    }


# ---- watcher -----------------------------------------------------------------


def item_key(symbol: str, item: dict) -> str:
    raw = f"{symbol}|{item.get('url') or ''}|{item.get('title') or ''}"
    return hashlib.sha1(raw.encode()).hexdigest()


def watch_list(extra: list[str] | None = None, lists: list[dict] | None = None) -> dict[str, dict]:
    """symbol -> {"armed": [bot names], "held": [{bot, name, mode, side, qty}], "lists": [watch-list names]}:
    the SMA bots' stocks, then `extra`, then every watch list's (news_lists), at most MAX_WATCH."""
    out: dict[str, dict] = {}
    try:
        from app.sma_host import load_terminal

        mod = load_terminal()
        if mod is not None and hasattr(mod, "news_watch_list"):
            out = mod.news_watch_list()
    except Exception as exc:  # noqa: BLE001 - the extra symbols still work
        log.warning("news watch: could not read the bots' stocks: %s", exc)
    for s in extra or []:
        out.setdefault(s.upper(), {"armed": [], "held": []})
    for wl in lists or []:
        for s in wl.get("symbols", []):
            out.setdefault(s.upper(), {"armed": [], "held": []}).setdefault("lists", []).append(wl.get("name") or "")
    return dict(list(out.items())[:MAX_WATCH])


def entries_active() -> bool:
    """True while an SMA bot takes news entries (news_entry.py)."""
    try:
        from app.sma_host import load_terminal

        mod = load_terminal()
        return bool(mod is not None and hasattr(mod, "news_entry_active") and mod.news_entry_active())
    except Exception:  # noqa: BLE001
        return False


def side_of(entry: dict) -> str | None:
    sides = {h.get("side") for h in entry.get("held", [])}
    return sides.pop() if len(sides) == 1 else None


def is_critical(item: dict, side: str | None) -> bool:
    """A phone call: a critical headline, or a HIGH one against the position, on a held stock."""
    if side is None or item.get("about") is False:
        return False
    against = (side == "LONG" and item.get("sentiment") == "NEGATIVE") or (
        side == "SHORT" and item.get("sentiment") == "POSITIVE"
    )
    return against and (bool(item.get("critical")) or item.get("impact") == "HIGH")


def alert_text(result: dict, fresh: list[dict], entry: dict, critical: bool, price: float | None = None) -> str:
    view, act = result["overall"], result["action"]
    head = "🚨 CRITICAL NEWS" if critical else "📰 News"
    px = f" · price ₹{price:,.2f}" if price else ""
    lines = [f"{head} · {result['symbol']}{px} · {view['sentiment']} ({view['impact'].lower()} impact)"]
    for it in fresh[:3]:
        until = valid_until(it.get("at"))
        valid = f", valid till {until:%a %d %b %H:%M}" if until else ""
        lines.append(f"• {it['title'][:160]} ({it.get('source') or ''}) → {it['sentiment'].lower()}, {it['impact'].lower()}{valid}")
    held = entry.get("held") or []
    if held:
        lines.append("You hold: " + "; ".join(f"{h['side']} {h['qty']} on {h['name']} ({h['mode']})" for h in held))
    elif entry.get("armed"):
        lines.append("Armed on: " + ", ".join(entry["armed"]))
    if entry.get("lists"):
        lines.append("Watch lists: " + ", ".join(entry["lists"]))
    lines.append(f"What to do: {act['label']}. {act['why']}")
    if not held:
        both = result.get("plan") or plan(view)
        lines.append(f"If you are long: {both['long']['label']} · If you are short: {both['short']['label']}")
        when = result.get("timing")
        if when:
            lines.append(f"New entry: {when['buy']['label']} · {when['short']['label']}")
    if view.get("summary"):
        lines.append(view["summary"][:300])
    lines.append(f"({'AI' if result['method'] == 'ai' else 'rule-based'} read; not advice, nothing was traded.)")
    return "\n".join(lines)


@dataclass
class WatchStatus:
    last_run: str | None = None
    last_error: str | None = None
    watched: int = 0
    alerts_sent: int = 0
    fno_last_run: str | None = None
    fno_error: str | None = None
    fno_items: int = 0


async def load_settings() -> NewsWatchSettings:
    async with async_session() as s:
        row = await s.get(NewsWatchSettings, 1)
        if row is None:
            row = NewsWatchSettings(id=1)
            s.add(row)
            await s.commit()
            await s.refresh(row)
        return row


def settings_out(row: NewsWatchSettings) -> dict:
    return {
        "enabled": row.enabled,
        "every_min": row.every_min,
        "min_impact": row.min_impact,
        "use_ai": row.use_ai,
        "popup": row.popup,
        "telegram": row.telegram,
        "call_critical": row.call_critical,
        "all_fno": bool(row.all_fno),
        "fno_telegram": bool(row.fno_telegram),
        "extra_symbols": [s for s in (row.extra_symbols or "").split(",") if s],
    }


class NewsWatcher:
    def __init__(self) -> None:
        self.status = WatchStatus()
        self.latest: dict[str, dict] = {}  # symbol -> last analysis summary
        self.market: list[dict] = []  # all-F&O NSE filings, scored, newest first (the News feed)
        self._seen: set[str] = set()
        self._task: asyncio.Task | None = None
        self._next_at = 0.0
        self._next_fast = 0.0

    def start(self) -> None:
        # NEWS_WATCH=off (the tests set it) keeps the watcher from touching the desk DB on a test's short-lived loop.
        if os.environ.get("NEWS_WATCH", "on").strip().lower() == "off":
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    def kick(self) -> None:
        self._next_at = 0.0

    async def _loop(self) -> None:
        await asyncio.sleep(START_DELAY_SEC)  # let the app finish starting before the first read
        while True:
            try:
                cfg = await load_settings()
                now = dt.datetime.now(IST)
                due = time.monotonic() >= self._next_at
                if cfg.enabled and due and is_trading_day(now) and WATCH_START <= now.time() <= WATCH_END:
                    self._next_at = time.monotonic() + max(cfg.every_min, 5) * 60
                    self._next_fast = time.monotonic() + ENTRY_POLL_SEC
                    await self.run_once(cfg, now)
                elif time.monotonic() >= self._next_fast and is_trading_day(now) and ENTRY_START <= now.time() <= ENTRY_END:
                    # A bot enters on news: NSE's filings for every company, once a minute (one request).
                    self._next_fast = time.monotonic() + ENTRY_POLL_SEC
                    if await asyncio.to_thread(entries_active):
                        from app.services import news_lists

                        lists = await news_lists.lists_or_none()
                        watched = await asyncio.to_thread(watch_list, settings_out(cfg)["extra_symbols"], lists)
                        try:
                            await self._market_pass(cfg, now, watched, alerts=cfg.enabled and cfg.all_fno)
                            self.status.fno_error = None
                        except Exception as exc:  # noqa: BLE001 - next minute
                            self.status.fno_error = str(exc)[:200] or type(exc).__name__
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - keep watching
                self.status.last_error = str(exc)[:200]
                log.exception("news watch pass failed")
            await asyncio.sleep(30)

    async def run_once(self, cfg: NewsWatchSettings, now: dt.datetime | None = None) -> list[NewsAlert]:
        now = now or dt.datetime.now(IST)
        settings = settings_out(cfg)
        from app.services import news_lists

        lists = await news_lists.lists_or_none()
        watched = await asyncio.to_thread(watch_list, settings["extra_symbols"], lists)
        self.status.watched = len(watched)
        min_level = IMPACT_LEVELS.index(cfg.min_impact) if cfg.min_impact in IMPACT_LEVELS else 1
        made: list[NewsAlert] = []
        for symbol, entry in watched.items():
            side = side_of(entry)
            try:
                result = await analyze(symbol, use_ai=cfg.use_ai, side=side, now=now)
            except Exception as exc:  # noqa: BLE001 - next stock
                self.status.last_error = f"{symbol}: {exc}"[:200]
                continue
            self.latest[symbol] = {
                "symbol": symbol, "overall": result["overall"], "action": result["action"], "method": result["method"],
                "armed": entry.get("armed", []), "held": entry.get("held", []), "at": now.isoformat(timespec="seconds"),
                "top": result["items"][0]["title"] if result["items"] else None,
                "count": len(result["items"]), "errors": result.get("errors") or {},
            }
            await self._entries(symbol, [{**it, "key": item_key(symbol, it)} for it in result["items"]], now)
            fresh = []
            for it in result["items"]:
                key = item_key(symbol, it)
                if key in self._seen:
                    continue
                self._seen.add(key)
                age = _age_hours(it.get("at"), now)
                if age is None or age > ALERT_MAX_AGE_H or it.get("about") is False or not is_valid(it.get("at"), now):
                    continue
                if IMPACT_LEVELS.index(it["impact"]) < min_level and not is_critical(it, side):
                    continue
                fresh.append({**it, "key": key})
            if not fresh:
                continue
            async with async_session() as s:
                known = set((await s.execute(
                    select(NewsAlert.key).where(NewsAlert.key.in_([f["key"] for f in fresh]))
                )).scalars())
                fresh = [f for f in fresh if f["key"] not in known]
                if not fresh:
                    continue
                critical = any(is_critical(f, side) for f in fresh)
                price = (await news_lists.prices([symbol])).get(symbol)
                text = alert_text(result, fresh, entry, critical, price)
                row = NewsAlert(
                    symbol=symbol,
                    key=fresh[0]["key"],
                    sentiment=result["overall"]["sentiment"],
                    impact=max((f["impact"] for f in fresh), key=IMPACT_LEVELS.index),
                    critical=critical,
                    action=result["action"]["code"],
                    title=fresh[0]["title"][:300],
                    body=json.dumps({
                        "text": text, "items": [{k: f.get(k) for k in ("title", "url", "source", "at", "sentiment", "impact", "why")} for f in fresh],
                        "action": result["action"], "overall": result["overall"], "method": result["method"],
                        "held": entry.get("held", []), "armed": entry.get("armed", []),
                        "lists": entry.get("lists", []), "price": price,
                        "valid_until": fresh[0].get("valid_until"),
                    }),
                    popup=cfg.popup,
                )
                s.add(row)
                for extra in fresh[1:]:  # remember the other headlines too, so they are not alerted again
                    s.add(NewsAlert(symbol=symbol, key=extra["key"], sentiment=extra["sentiment"], impact=extra["impact"],
                                    critical=False, action=result["action"]["code"], title=extra["title"][:300],
                                    body=None, popup=False, grouped=True))
                await s.commit()
                await s.refresh(row)
            made.append(row)
            self.status.alerts_sent += 1
            await self._deliver(cfg, text, critical)
        if cfg.all_fno:
            try:
                made.extend(await self._market_pass(cfg, now, watched))
                self.status.fno_error = None
            except Exception as exc:  # noqa: BLE001 - the armed / held alerts above still went
                self.status.fno_error = str(exc)[:200] or type(exc).__name__
        self.status.last_run = now.isoformat(timespec="seconds")
        self.status.last_error = None if made or self.status.last_error is None else self.status.last_error
        return made

    async def _market_pass(
        self, cfg: NewsWatchSettings, now: dt.datetime, watched: dict, alerts: bool = True
    ) -> list[NewsAlert]:
        """Every F&O stock's NSE filings in one request: the News feed, news entries, and a pop-up for HIGH / critical ones."""
        from app.services import stock_news

        async with httpx.AsyncClient(timeout=stock_news.TIMEOUT, follow_redirects=True) as client:
            raw = await stock_news.nse_market_announcements(client)
        fno = await asyncio.to_thread(_fno_set)
        made: list[NewsAlert] = []
        known = {it["key"] for it in self.market}
        fresh_feed: list[dict] = []
        for it in raw:
            symbol = it["symbol"]
            if fno and symbol not in fno:
                continue
            key = item_key(symbol, it)
            scored = with_validity({**it, **score_text(it.get("title", ""), it.get("detail")), "by": "rules", "about": True,
                                    "key": key, "watched": symbol in watched}, now)
            if key not in known:
                fresh_feed.append(scored)
                known.add(key)
            await self._entries(symbol, [scored], now)
            if not alerts or symbol in watched or key in self._seen:
                continue  # an armed / held stock was read in full above
            self._seen.add(key)
            age = _age_hours(scored.get("at"), now)
            if age is None or age > ALERT_MAX_AGE_H or not scored["valid"]:
                continue
            if scored["impact"] != "HIGH" and not scored.get("critical"):
                continue
            row = await self._market_alert(cfg, symbol, scored, now)
            if row is not None:
                made.append(row)
        cutoff = now - dt.timedelta(days=MAX_AGE_DAYS)
        merged = fresh_feed + self.market
        merged = [it for it in merged if (_when(it.get("at")) or now) >= cutoff]
        merged.sort(key=lambda it: it.get("at") or "", reverse=True)
        self.market = merged[:MARKET_KEEP]
        self.status.fno_last_run = now.isoformat(timespec="seconds")
        self.status.fno_items = len(self.market)
        return made

    async def _entries(self, symbol: str, items: list[dict], now: dt.datetime) -> None:
        """Hand fresh headlines to the SMA terminal's news-entry bot (news_entry.py). Never raises."""
        def young(it: dict) -> bool:
            age = _age_hours(it.get("at"), now)
            return age is not None and age * 60 <= ENTRY_MAX_AGE_MIN

        recent = [it for it in items if it.get("about") is not False and young(it)]
        if not recent:
            return
        try:
            from app.sma_host import load_terminal

            mod = load_terminal()
            consider = getattr(mod, "news_entry_consider", None) if mod is not None else None
            if consider is None:
                return
            for it in recent:
                await consider(symbol, it, now)
        except Exception:  # noqa: BLE001 - alerts go on
            log.exception("news entry hand-off failed for %s", symbol)

    async def _market_alert(self, cfg: NewsWatchSettings, symbol: str, item: dict, now: dt.datetime) -> NewsAlert | None:
        view = overall([item], now)
        act = action(view, None)
        result = {"symbol": symbol, "overall": view, "action": act, "method": "rules", "plan": plan(view),
                  "timing": timing(view, [item], now)}
        entry = {"armed": [], "held": []}
        from app.services import news_lists

        price = (await news_lists.prices([symbol])).get(symbol)
        text = alert_text(result, [item], entry, False, price).replace("📰 News", "📰 F&O news", 1)
        async with async_session() as s:
            if (await s.execute(select(NewsAlert.id).where(NewsAlert.key == item["key"]))).first():
                return None
            row = NewsAlert(
                symbol=symbol, key=item["key"], sentiment=view["sentiment"], impact=item["impact"], critical=False,
                action=act["code"], title=item["title"][:300],
                body=json.dumps({
                    "text": text, "scope": "fno",
                    "items": [{k: item.get(k) for k in ("title", "url", "source", "at", "sentiment", "impact", "why")}],
                    "action": act, "overall": view, "method": "rules", "held": [], "armed": [],
                    "price": price, "valid_until": item.get("valid_until"),
                }),
                popup=cfg.popup,
            )
            s.add(row)
            await s.commit()
            await s.refresh(row)
        self.status.alerts_sent += 1
        if cfg.fno_telegram:
            try:
                from app.services.alert_notifier import alert_notifier

                await alert_notifier.send(text)
            except Exception as exc:  # noqa: BLE001 - the pop-up row is saved either way
                self.status.fno_error = f"alert delivery: {exc}"[:200]
        return row

    async def _deliver(self, cfg: NewsWatchSettings, text: str, critical: bool) -> None:
        from app.services.alert_notifier import alert_notifier

        try:
            if critical and cfg.call_critical:
                await alert_notifier.send_critical(text, call=True)
            elif cfg.telegram or critical:
                await alert_notifier.send(text)
        except Exception as exc:  # noqa: BLE001 - the pop-up row is saved either way
            self.status.last_error = f"alert delivery: {exc}"[:200]


news_watcher = NewsWatcher()


def _fno_set() -> set[str]:
    """The F&O stocks (empty if the instrument list is not loaded: then every filing is kept)."""
    try:
        from app.services.instruments import instrument_master

        return set(instrument_master.fno_stocks())
    except Exception:  # noqa: BLE001
        return set()


def _when(at: str | None) -> dt.datetime | None:
    if not at:
        return None
    try:
        when = dt.datetime.fromisoformat(at)
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=IST)


def alert_out(row: NewsAlert) -> dict:
    body = {}
    if row.body:
        try:
            body = json.loads(row.body)
        except ValueError:
            body = {}
    return {
        "id": row.id, "symbol": row.symbol, "sentiment": row.sentiment, "impact": row.impact,
        "critical": row.critical, "action": row.action, "title": row.title, "popup": row.popup,
        "created_at": row.created_at.replace(tzinfo=dt.timezone.utc).astimezone(IST).isoformat(timespec="seconds")
        if row.created_at else None,
        **{k: body.get(k) for k in ("text", "items", "overall", "method", "held", "armed", "scope", "lists", "price", "valid_until")},
        "action_label": (body.get("action") or {}).get("label"),
        "action_why": (body.get("action") or {}).get("why"),
    }
