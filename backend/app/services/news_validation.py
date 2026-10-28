"""News check (news_validation.py): did a headline actually move the stock that day? Read-only.

For a day (after the close: the whole session; in market hours: the move so far), each scored headline
(the news alerts saved that day, plus the news of the stocks asked for, `news_impact.analyze`) is set
against the stock's 1-minute candles of that day (the SMA terminal's `news_candles`: the bots' own tape for
today's armed stocks, else Groww's candle history):

* **Start price**: news out before 09:15 (overnight, pre-open) is measured from the previous session's close,
  so the opening gap counts; news in the session from the open of the minute it came out. News after 15:30
  belongs to the next day (`AFTER_CLOSE`), and news newer than the last candle waits (`PENDING`).
* **Moves**: 15 / 30 / 60 minutes later and at the close (or the last candle so far), the best and worst
  move after it, and how many minutes the best move took. Positive news is judged on the move up, negative
  on the move down (`signed`); neutral news only shows the size of the move.
* **Verdict** (`min_move` %, default 0.5): `WITH` (closed at least that far the news' way), `AGAINST`
  (that far the other way), `FADED` (got there, then gave it back), `NO_EFFECT`.

`summarize` groups the measured headlines by sentiment and impact (positive / negative × high / medium /
low): how many moved with the news, the average signed move after 30 minutes and at the close, the average
best move and the minutes to it. Nothing here trades or changes a setting.
"""
from __future__ import annotations

import datetime as dt
import json

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OPEN = dt.time(9, 15)
CLOSE = dt.time(15, 30)
DEFAULT_MIN_MOVE = 0.5
MAX_STOCKS = 40
STEPS = (15, 30, 60)
SIGN = {"POSITIVE": 1, "NEGATIVE": -1}
IMPACTS = ("HIGH", "MEDIUM", "LOW")


def _when(at) -> dt.datetime | None:
    if not at:
        return None
    try:
        when = at if isinstance(at, dt.datetime) else dt.datetime.fromisoformat(str(at))
    except ValueError:
        return None
    return when.astimezone(IST) if when.tzinfo else when.replace(tzinfo=IST)


def prev_session_close(day: dt.date) -> dt.datetime:
    """15:30 IST of the weekday before `day` (no holiday list): news after it counts for `day`."""
    back = day - dt.timedelta(days=1)
    while back.weekday() >= 5:
        back -= dt.timedelta(days=1)
    return dt.datetime.combine(back, CLOSE, tzinfo=IST)


def for_day(at, day: dt.date) -> bool:
    """News out after the previous session's close and by this day's close (or after it, flagged later)."""
    when = _when(at)
    return when is not None and prev_session_close(day) < when <= dt.datetime.combine(day, dt.time(23, 59), tzinfo=IST)


def _pct(price: float, base: float) -> float:
    return round((price / base - 1) * 100, 3)


def measure(item: dict, rows: list, prev_close: float | None, day: dt.date, min_move: float = DEFAULT_MIN_MOVE,
            now: dt.datetime | None = None) -> dict:
    """The price move after one headline. `rows` are the day's 1-minute candles [[ts, o, h, l, c], ...]."""
    when = _when(item.get("at"))
    sign = SIGN.get((item.get("sentiment") or "").upper(), 0)
    out = {"at": when.isoformat(timespec="minutes") if when else None, "sign": sign}
    if when is None or not rows:
        return {**out, "verdict": "NO_DATA"}
    close_at = dt.datetime.combine(day, CLOSE, tzinfo=IST)
    if when >= close_at:
        return {**out, "verdict": "AFTER_CLOSE"}
    at_ts = when.timestamp()
    if when.time() < OPEN or when.date() < day:
        base = prev_close if prev_close else rows[0][1]
        start, timing = 0, "BEFORE_OPEN"
        out["gap_pct"] = _pct(rows[0][1], prev_close) if prev_close else None
        from_ts = rows[0][0]
    else:
        start = next((i for i, r in enumerate(rows) if r[0] + 60 > at_ts), None)
        if start is None:
            return {**out, "verdict": "PENDING"}
        base, timing, from_ts = rows[start][1], "IN_SESSION", rows[start][0]
    after = rows[start:]
    last = after[-1]
    moves = {}
    for m in STEPS:
        hit = next((r for r in after if r[0] >= from_ts + m * 60), None)
        moves[f"move_{m}m"] = _pct(hit[4], base) if hit else None
    high = max(after, key=lambda r: r[2])
    low = min(after, key=lambda r: r[3])
    up, down = _pct(high[2], base), _pct(low[3], base)
    to_close = _pct(last[4], base)
    session_over = (now or dt.datetime.now(IST)) >= close_at
    res = {
        **out,
        "timing": timing,
        "base": round(base, 2),
        "last": round(last[4], 2),
        **moves,
        "move_close": to_close,
        "best_up": up,
        "worst_down": down,
        "so_far": not session_over,
    }
    if sign == 0:
        return {**res, "verdict": "NEUTRAL", "range_pct": round(up - down, 3)}
    best = up if sign > 0 else -down
    peak = high if sign > 0 else low
    signed_close = to_close * sign
    res.update(
        signed_close=round(signed_close, 3),
        signed_30m=round(moves["move_30m"] * sign, 3) if moves["move_30m"] is not None else None,
        best_with=round(best, 3),
        worst_against=round(-(down if sign > 0 else -up), 3),
        peak_after_min=int(max(0, peak[0] - from_ts) // 60),
    )
    if signed_close >= min_move:
        verdict = "WITH"
    elif signed_close <= -min_move:
        verdict = "AGAINST"
    elif best >= min_move:
        verdict = "FADED"
    else:
        verdict = "NO_EFFECT"
    return {**res, "verdict": verdict}


def _avg(values) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _group(label: str, rows: list[dict]) -> dict:
    judged = [r for r in rows if r.get("verdict") in ("WITH", "AGAINST", "FADED", "NO_EFFECT")]
    count = {v: sum(1 for r in judged if r["verdict"] == v) for v in ("WITH", "AGAINST", "FADED", "NO_EFFECT")}
    return {
        "group": label,
        "headlines": len(judged),
        **{k.lower(): v for k, v in count.items()},
        "hit_rate": round(count["WITH"] / len(judged) * 100, 1) if judged else None,
        "avg_signed_30m": _avg(r.get("signed_30m") for r in judged),
        "avg_signed_close": _avg(r.get("signed_close") for r in judged),
        "avg_best": _avg(r.get("best_with") for r in judged),
        "avg_worst": _avg(r.get("worst_against") for r in judged),
        "avg_peak_min": _avg(r.get("peak_after_min") for r in judged),
    }


def summarize(rows: list[dict]) -> list[dict]:
    """Hit rates and average moves per sentiment × impact, then per sentiment, then overall."""
    out = []

    def add(label: str, part: list[dict], sent: str | None, imp: str | None) -> None:
        group = _group(label, part)
        if group["headlines"]:  # only headlines that were measured (not after the close / pending / no data)
            out.append({**group, "sentiment": sent, "impact": imp})

    for sent in ("POSITIVE", "NEGATIVE"):
        for imp in IMPACTS:
            add(f"{sent.title()} · {imp.title()}", [r for r in rows if r.get("sentiment") == sent and r.get("impact") == imp], sent, imp)
        add(f"All {sent.lower()}", [r for r in rows if r.get("sentiment") == sent], sent, None)
    add("All news", rows, None, None)
    return out


def from_alert(row) -> list[dict]:
    """The headlines inside one saved NewsAlert row (its own items, else the row itself)."""
    body = {}
    if getattr(row, "body", None):
        try:
            body = json.loads(row.body)
        except ValueError:
            body = {}
    created = row.created_at.replace(tzinfo=dt.timezone.utc).astimezone(IST) if row.created_at else None
    items = body.get("items") or [{"title": row.title, "sentiment": row.sentiment, "impact": row.impact}]
    return [
        {
            "symbol": row.symbol,
            "title": it.get("title") or row.title,
            "url": it.get("url"),
            "source": it.get("source"),
            "at": it.get("at") or (created.isoformat() if created else None),
            "sentiment": (it.get("sentiment") or row.sentiment or "").upper(),
            "impact": (it.get("impact") or row.impact or "").upper(),
            "critical": bool(row.critical),
            "from": "alert",
        }
        for it in items
    ]


def dedupe(items: list[dict]) -> list[dict]:
    seen, out = set(), []
    for it in items:
        key = (it.get("symbol"), (it.get("title") or "").strip().lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


async def collect(day: dt.date, symbols: list[str] | None, alerts: bool = True) -> list[dict]:
    """The day's scored headlines: the news alerts saved that day and, for `symbols`, their own news."""
    import asyncio

    from sqlalchemy import select

    from app.models.database import NewsAlert, async_session
    from app.services import news_impact

    items: list[dict] = []
    if alerts:
        start = prev_session_close(day).astimezone(dt.timezone.utc).replace(tzinfo=None)
        stop = dt.datetime.combine(day, dt.time(23, 59), tzinfo=IST).astimezone(dt.timezone.utc).replace(tzinfo=None)
        async with async_session() as s:
            rows = (
                await s.execute(
                    select(NewsAlert).where(NewsAlert.created_at > start, NewsAlert.created_at <= stop, NewsAlert.grouped.is_(False))
                )
            ).scalars().all()
        for row in rows:
            items.extend(from_alert(row))
    sem = asyncio.Semaphore(news_impact.DAY_CONCURRENCY)

    async def one(symbol: str) -> list[dict]:
        async with sem:
            try:
                r = await news_impact.analyze(symbol, use_ai=False, day=day)
            except Exception:  # noqa: BLE001 - a stock without news just adds nothing
                return []
        return [
            {
                "symbol": r["symbol"], "title": it.get("title"), "url": it.get("url"), "source": it.get("source"),
                "at": it.get("at"), "sentiment": (it.get("sentiment") or "").upper(),
                "impact": (it.get("impact") or "").upper(), "critical": bool(it.get("critical")), "from": "news",
            }
            for it in r.get("items", [])
            if it.get("about", True) is not False
        ]

    for found in await asyncio.gather(*(one(s) for s in symbols or [])):
        items.extend(found)
    return dedupe([it for it in items if for_day(it.get("at"), day)])


async def validate(day: dt.date, symbols: list[str] | None, candles, *, alerts: bool = True,
                   min_move: float = DEFAULT_MIN_MOVE, now: dt.datetime | None = None) -> dict:
    """Every headline of `day` with its price move, and the summary. `candles(symbols, day)` gives the
    1-minute rows (the terminal's `news_candles`)."""
    now = now or dt.datetime.now(IST)
    items = await collect(day, symbols, alerts=alerts)
    stocks = list(dict.fromkeys(it["symbol"] for it in items))
    skipped = stocks[MAX_STOCKS:]
    stocks = stocks[:MAX_STOCKS]
    data = await candles(stocks, day) if stocks else {}
    rows = []
    for it in items:
        if it["symbol"] not in stocks:
            continue
        got = data.get(it["symbol"]) or {}
        if got.get("error"):
            rows.append({**it, "verdict": "NO_DATA", "error": got["error"]})
            continue
        rows.append({**it, **measure(it, got.get("rows") or [], got.get("prev_close"), day, min_move, now)})
    rows.sort(key=lambda r: (r.get("at") or "", r["symbol"]))
    in_session = now.date() == day and now < dt.datetime.combine(day, CLOSE, tzinfo=IST)
    return {
        "day": day.isoformat(),
        "as_of": now.isoformat(timespec="minutes"),
        "so_far": in_session,
        "min_move": min_move,
        "rows": rows,
        "summary": summarize(rows),
        "stocks": len(stocks),
        "skipped_stocks": skipped,
        "disclaimer": "Information only: how prices moved after the news. Nothing is traded from this.",
    }
