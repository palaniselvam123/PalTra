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
from app.services import news_impact, news_validation
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


def _news_candles():
    """The SMA terminal's 1-minute candle reader for the news check (main.news_candles), or None."""
    from app.sma_host import load_terminal

    mod = load_terminal()
    return getattr(mod, "news_candles", None) if mod is not None else None


@router.get("/validate")
async def validate(
    date: str | None = None,
    symbols: str | None = None,
    alerts: bool = True,
    armed: bool = False,
    min_move: float = Query(news_validation.DEFAULT_MIN_MOVE, ge=0.05, le=10),
):
    """Did the news move the stock? Each headline of the day (the news alerts saved that day, plus the news of
    `symbols` and, with `armed`, every armed / held stock) against that day's 1-minute candles: the move 15 /
    30 / 60 minutes later and at the close (in market hours: so far), the best and worst move, and a verdict,
    summed up per positive / negative × high / medium / low impact. Read-only."""
    day = _day(date) or dt.datetime.now(news_impact.IST).date()
    names = [_clean(x) for x in (symbols or "").split(",") if x.strip()]
    if armed:
        names += list((await _watch()).keys())
    names = list(dict.fromkeys(names))[:MAX_DAY_SYMBOLS]
    if not alerts and not names:
        raise HTTPException(400, "Nothing to check: keep the news alerts ticked, tick the armed stocks or type symbols.")
    candles = _news_candles()
    if candles is None:
        raise HTTPException(503, "Candles come from the SMA terminal, which is not running in this process.")
    return await news_validation.validate(day, names, candles, alerts=alerts, min_move=min_move)


async def _watch() -> dict:
    import asyncio

    from app.services import news_lists

    cfg = await news_impact.load_settings()
    lists = await news_lists.all_lists()
    return await asyncio.to_thread(news_impact.watch_list, news_impact.settings_out(cfg)["extra_symbols"], lists)


class SettingsBody(BaseModel):
    enabled: bool | None = None
    every_min: int | None = Field(None, ge=5, le=120)
    min_impact: str | None = Field(None, pattern="^(LOW|MEDIUM|HIGH)$")
    use_ai: bool | None = None
    popup: bool | None = None
    telegram: bool | None = None
    call_critical: bool | None = None
    all_fno: bool | None = None
    fno_telegram: bool | None = None
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


@router.get("/feed")
async def feed(limit: int = Query(100, ge=1, le=news_impact.MARKET_KEEP), valid_only: bool = False, prices: bool = False):
    """Every F&O stock's latest NSE filings from the watcher's last passes, scored, newest first.
    Each carries `valid` / `valid_until` (the next trading day's close); `valid_only` drops expired ones,
    `prices` adds each stock's last price."""
    now = dt.datetime.now(news_impact.IST)
    items = [news_impact.with_validity(it, now) for it in news_watcher.market]
    if valid_only:
        items = [it for it in items if it["valid"]]
    items = items[:limit]
    if prices and items:
        from app.services import news_lists

        px = await news_lists.prices([it["symbol"] for it in items])
        items = [{**it, "price": px.get(it["symbol"])} for it in items]
    return {"items": items, "status": news_watcher.status.__dict__}


# ---- watch lists (the News page) ----------------------------------------------------


class ListBody(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    symbols: list[str] = Field(default_factory=list, max_length=200)


class ListPatch(BaseModel):
    name: str | None = Field(None, max_length=40)
    symbols: list[str] | None = Field(None, max_length=200)
    add: list[str] | None = Field(None, max_length=200)
    remove: list[str] | None = Field(None, max_length=200)


def _list_call(exc: Exception) -> HTTPException:
    from app.services.news_lists import ListError

    if isinstance(exc, LookupError):
        return HTTPException(404, str(exc))
    if isinstance(exc, ListError):
        return HTTPException(400, str(exc))
    return HTTPException(500, str(exc))


@router.get("/lists")
async def lists():
    """Every news watch list (name and stocks)."""
    from app.services import news_lists

    return {"lists": await news_lists.all_lists(), "max_lists": news_lists.MAX_LISTS, "max_per_list": news_lists.MAX_PER_LIST}


@router.post("/lists")
async def create_list(body: ListBody):
    from app.services import news_lists

    try:
        row = await news_lists.create(body.name, body.symbols)
    except (LookupError, ValueError) as exc:
        raise _list_call(exc) from exc
    news_watcher.kick()
    return row


@router.put("/lists/{list_id}")
async def update_list(list_id: int, body: ListPatch):
    """Rename, replace the stocks, or add / remove some."""
    from app.services import news_lists

    try:
        row = await news_lists.update(list_id, name=body.name, symbols=body.symbols, add=body.add, remove=body.remove)
    except (LookupError, ValueError) as exc:
        raise _list_call(exc) from exc
    news_watcher.kick()
    return row


@router.delete("/lists/{list_id}")
async def delete_list(list_id: int):
    from app.services import news_lists

    try:
        await news_lists.delete(list_id)
    except (LookupError, ValueError) as exc:
        raise _list_call(exc) from exc
    return {"ok": True}


@router.get("/prices")
async def stock_prices(symbols: str):
    """Last prices for up to 100 stocks (bots' quotes, the live feed, else one Groww LTP call)."""
    from app.services import news_lists

    names = [_clean(x) for x in symbols.split(",") if x.strip()][:100]
    return {"prices": await news_lists.prices(names)}


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
