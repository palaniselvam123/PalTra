"""News impact: per-stock analysis, the news-alert watcher's settings, and its alerts (pop-ups).

Information only: nothing here places, closes or changes an order.
"""
from __future__ import annotations

import datetime as dt
import re

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models.database import NewsAlert, async_session
from app.services import news_impact
from app.services.news_impact import news_watcher

router = APIRouter(prefix="/api/news-impact", tags=["news"])
_SYMBOL = re.compile(r"^[A-Z0-9&._-]{1,25}$")


def _clean(symbol: str) -> str:
    s = symbol.strip().upper()
    if not _SYMBOL.match(s):
        raise HTTPException(400, f"Not a stock symbol: {symbol!r}")
    return s


MAX_PAST_DAYS = 365
MAX_DAY_SYMBOLS = 40


def _day(date: str | None) -> dt.date | None:
    """A past date (YYYY-MM-DD) for the news of that day; None or today = the live news."""
    if not date:
        return None
    try:
        day = dt.date.fromisoformat(date)
    except ValueError as exc:
        raise HTTPException(400, "date must be YYYY-MM-DD") from exc
    today = dt.datetime.now(news_impact.IST).date()
    if day > today:
        raise HTTPException(400, "That date is in the future.")
    if (today - day).days > MAX_PAST_DAYS:
        raise HTTPException(400, f"Pick a date within the last {MAX_PAST_DAYS} days.")
    return None if day == today else day


@router.get("/stock/{symbol}")
async def stock(
    symbol: str,
    force: bool = False,
    ai: bool = True,
    side: str | None = Query(None, pattern="^(LONG|SHORT)$"),
    date: str | None = None,
):
    """The stock's headlines scored, its overall view, what to do for `side`, and `plan` (flat / long / short).
    With `date` (YYYY-MM-DD, a past day) that day's news as of its close."""
    s = _clean(symbol)
    day = _day(date)
    if side is None and day is None:
        entry = (await _watch()).get(s)
        side = news_impact.side_of(entry) if entry else None
    cfg = await news_impact.load_settings()
    return await news_impact.analyze(s, use_ai=ai and cfg.use_ai, side=side, force=force, day=day)


@router.get("/day")
async def day_news(date: str, symbols: str | None = None, ai: bool = True):
    """Every armed / held stock's news on a date (or the `symbols` given, comma-separated): its view and
    what to do for a new entry either way and for an open long or short. Read-only."""
    day = _day(date) or dt.datetime.now(news_impact.IST).date()
    if symbols:
        names = [_clean(x) for x in symbols.split(",") if x.strip()]
        watched = {n: {"armed": [], "held": []} for n in dict.fromkeys(names)}
    else:
        watched = await _watch()
    if not watched:
        raise HTTPException(400, "No armed stocks: arm stocks on a bot or type the symbols.")
    if len(watched) > MAX_DAY_SYMBOLS:
        watched = dict(list(watched.items())[:MAX_DAY_SYMBOLS])
    cfg = await news_impact.load_settings()
    return await news_impact.day_report(day, watched, use_ai=ai and cfg.use_ai)


async def _watch() -> dict:
    import asyncio

    cfg = await news_impact.load_settings()
    return await asyncio.to_thread(news_impact.watch_list, news_impact.settings_out(cfg)["extra_symbols"])


class SettingsBody(BaseModel):
    enabled: bool | None = None
    every_min: int | None = Field(None, ge=5, le=120)
    min_impact: str | None = Field(None, pattern="^(LOW|MEDIUM|HIGH)$")
    use_ai: bool | None = None
    popup: bool | None = None
    telegram: bool | None = None
    call_critical: bool | None = None
    extra_symbols: list[str] | None = Field(None, max_length=30)


@router.get("/settings")
async def get_settings():
    cfg = await news_impact.load_settings()
    has_key = bool(await news_impact._openai_key())  # noqa: SLF001
    return {**news_impact.settings_out(cfg), "ai_key": has_key, "status": news_watcher.status.__dict__}


@router.put("/settings")
async def put_settings(body: SettingsBody):
    from app.models.database import NewsWatchSettings

    patch = body.model_dump(exclude_none=True)
    if "extra_symbols" in patch:
        patch["extra_symbols"] = ",".join(dict.fromkeys(_clean(s) for s in patch["extra_symbols"] if s.strip()))
    async with async_session() as s:
        row = await s.get(NewsWatchSettings, 1)
        if row is None:
            row = NewsWatchSettings(id=1)
            s.add(row)
        for k, v in patch.items():
            setattr(row, k, v)
        await s.commit()
        await s.refresh(row)
    news_watcher.kick()
    return news_impact.settings_out(row)


@router.get("/watch")
async def watch():
    """The watched stocks (armed / held / extra) with their latest news view."""
    watched = await _watch()
    return {
        "stocks": [{"symbol": s, **e, "latest": news_watcher.latest.get(s)} for s, e in watched.items()],
        "status": news_watcher.status.__dict__,
    }


@router.post("/run")
async def run_now():
    """One watcher pass now (whatever the hour), for a check. Sends alerts like a scheduled pass."""
    cfg = await news_impact.load_settings()
    made = await news_watcher.run_once(cfg)
    return {"alerts": [news_impact.alert_out(r) for r in made], "status": news_watcher.status.__dict__}


@router.get("/alerts")
async def alerts(after_id: int = 0, limit: int = Query(30, ge=1, le=200)):
    """News alerts, newest first; `after_id` gives only the ones after it (the pop-up's poll)."""
    async with async_session() as s:
        q = select(NewsAlert).where(NewsAlert.grouped.is_(False))
        if after_id:
            q = q.where(NewsAlert.id > after_id)
        rows = (await s.execute(q.order_by(NewsAlert.id.desc()).limit(limit))).scalars().all()
    return {"alerts": [news_impact.alert_out(r) for r in rows]}


@router.post("/test")
async def test_alert():
    """A sample alert: a pop-up row and a Telegram message (no phone call)."""
    from app.services.alert_notifier import alert_notifier

    text = (
        "📰 News · TEST · NEGATIVE (high impact)\n• This is a test of PalTra's news alerts.\n"
        "What to do: No action. Nothing was traded."
    )
    async with async_session() as s:
        row = NewsAlert(symbol="TEST", key=f"test-{__import__('time').time()}", sentiment="NEGATIVE", impact="HIGH",
                        critical=False, action="NO_ACTION", title="This is a test of PalTra's news alerts.",
                        body=__import__("json").dumps({"text": text, "action": {"label": "No action", "why": "Test only."}}))
        s.add(row)
        await s.commit()
        await s.refresh(row)
    sent = await alert_notifier.send(text)
    return {"alert": news_impact.alert_out(row), "telegram": getattr(sent, "ok", None), "skipped": getattr(sent, "skipped_reason", None)}
