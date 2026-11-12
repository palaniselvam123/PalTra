"""News check (news_validation.py): did the news move the stock? Read-only, no Groww call in tests."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from app.core.market_clock import IST
from app.services import news_validation as nv
from tests.test_news_impact import db  # noqa: F401

DAY = dt.date(2026, 10, 28)  # a Wednesday
OPEN = dt.datetime(2026, 10, 28, 9, 15, tzinfo=IST)
AFTER = dt.datetime(2026, 10, 28, 18, 0, tzinfo=IST)


def _rows(path):
    """[[ts, o, h, l, c], ...] from a list of closes, one a minute from 09:15 (open = previous close)."""
    out, prev = [], path[0]
    for i, c in enumerate(path):
        ts = int((OPEN + dt.timedelta(minutes=i)).timestamp())
        out.append([ts, prev, max(prev, c) + 0.1, min(prev, c) - 0.1, c])
        prev = c
    return out


RISE = _rows([100 + i * 0.02 for i in range(375)])  # +7.5 over the day


def test_positive_news_in_session_is_with_the_move():
    at = (OPEN + dt.timedelta(minutes=60)).isoformat()
    r = nv.measure({"at": at, "sentiment": "POSITIVE"}, RISE, 99.0, DAY, 0.5, AFTER)
    assert r["timing"] == "IN_SESSION" and r["verdict"] == "WITH"
    assert r["move_30m"] == pytest.approx(0.61, abs=0.01) and r["signed_close"] > 5
    assert r["so_far"] is False and r["peak_after_min"] > 250
    # The same move after negative news is against it.
    neg = nv.measure({"at": at, "sentiment": "NEGATIVE"}, RISE, 99.0, DAY, 0.5, AFTER)
    assert neg["verdict"] == "AGAINST" and neg["signed_close"] < -5


def test_overnight_news_counts_the_opening_gap_and_late_news_waits():
    gap = _rows([103.0] * 375)  # opened 3% up on the previous close and went nowhere
    r = nv.measure({"at": "2026-10-27T20:00:00+05:30", "sentiment": "POSITIVE"}, gap, 100.0, DAY, 0.5, AFTER)
    assert r["timing"] == "BEFORE_OPEN" and r["gap_pct"] == 3.0 and r["verdict"] == "WITH"
    late = nv.measure({"at": "2026-10-28T16:00:00+05:30", "sentiment": "NEGATIVE"}, gap, 100.0, DAY)
    assert late["verdict"] == "AFTER_CLOSE"
    now = OPEN + dt.timedelta(minutes=30)
    pending = nv.measure({"at": "2026-10-28T10:30:00+05:30", "sentiment": "NEGATIVE"}, gap[:30], 100.0, DAY, 0.5, now)
    assert pending["verdict"] == "PENDING"


def test_faded_and_no_effect_and_neutral():
    spike = _rows([100.0] * 10 + [101.0] * 20 + [100.0] * 345)  # up 1% for 20 minutes, then back
    at = (OPEN + dt.timedelta(minutes=5)).isoformat()
    assert nv.measure({"at": at, "sentiment": "POSITIVE"}, spike, 100.0, DAY, 0.5, AFTER)["verdict"] == "FADED"
    flat = _rows([100.0] * 375)
    assert nv.measure({"at": at, "sentiment": "POSITIVE"}, flat, 100.0, DAY, 0.5, AFTER)["verdict"] == "NO_EFFECT"
    n = nv.measure({"at": at, "sentiment": "NEUTRAL"}, spike, 100.0, DAY, 0.5, AFTER)
    assert n["verdict"] == "NEUTRAL" and n["range_pct"] > 1
    # During the session the move is "so far".
    mid = nv.measure({"at": at, "sentiment": "POSITIVE"}, spike[:60], 100.0, DAY, 0.5, OPEN + dt.timedelta(minutes=61))
    assert mid["so_far"] is True


def test_summary_groups_by_sentiment_and_impact():
    rows = [
        {"sentiment": "POSITIVE", "impact": "HIGH", "verdict": "WITH", "signed_close": 2.0, "signed_30m": 1.0, "best_with": 2.5},
        {"sentiment": "POSITIVE", "impact": "HIGH", "verdict": "AGAINST", "signed_close": -1.0, "signed_30m": -0.5, "best_with": 0.2},
        {"sentiment": "NEGATIVE", "impact": "LOW", "verdict": "NO_EFFECT", "signed_close": 0.1, "signed_30m": 0.0, "best_with": 0.3},
        {"sentiment": "POSITIVE", "impact": "LOW", "verdict": "AFTER_CLOSE"},
    ]
    out = {g["group"]: g for g in nv.summarize(rows)}
    hi = out["Positive · High"]
    assert hi["headlines"] == 2 and hi["with"] == 1 and hi["hit_rate"] == 50.0 and hi["avg_signed_close"] == 0.5
    assert out["Negative · Low"]["no_effect"] == 1
    assert "Positive · Low" not in out  # only an after-close headline: nothing measured
    assert out["All news"]["headlines"] == 3


def test_previous_session_close_skips_the_weekend():
    assert nv.prev_session_close(dt.date(2026, 10, 26)) == dt.datetime(2026, 10, 23, 15, 30, tzinfo=IST)  # Mon -> Fri
    assert nv.for_day("2026-10-23T18:00:00+05:30", dt.date(2026, 10, 26))
    assert not nv.for_day("2026-10-23T15:00:00+05:30", dt.date(2026, 10, 26))


@pytest.mark.asyncio
async def test_validate_reads_the_days_alerts(db, monkeypatch):  # noqa: F811
    from app.models.database import NewsAlert, async_session

    utc = dt.datetime(2026, 10, 28, 10, 15, tzinfo=IST).astimezone(dt.timezone.utc).replace(tzinfo=None)
    body = {"items": [{"title": "ACME bags big order", "at": "2026-10-28T10:15:00+05:30", "sentiment": "POSITIVE", "impact": "HIGH"}]}
    async with async_session() as s:
        s.add(NewsAlert(symbol="ACME", key="k1", sentiment="POSITIVE", impact="HIGH", title="ACME bags big order",
                        body=json.dumps(body), created_at=utc))
        s.add(NewsAlert(symbol="ACME", key="k2", sentiment="POSITIVE", impact="LOW", title="extra", grouped=True, created_at=utc))
        await s.commit()
    asked = []

    async def candles(symbols, day):
        asked.append((list(symbols), day))
        return {"ACME": {"rows": RISE, "prev_close": 99.0}}

    out = await nv.validate(DAY, [], candles, now=AFTER)
    assert asked == [(["ACME"], DAY)]
    assert len(out["rows"]) == 1 and out["rows"][0]["verdict"] == "WITH" and out["rows"][0]["from"] == "alert"
    assert out["summary"][0]["group"] == "Positive · High" and out["so_far"] is False


def test_terminal_day_rows_take_the_day_and_the_previous_close():
    import pandas as pd

    import main

    prev = int(dt.datetime(2026, 10, 27, 15, 29, tzinfo=IST).timestamp())
    frame = pd.DataFrame(
        [[prev, 99, 99, 99, 98.5, 0]] + [r + [0] for r in RISE[:3]], columns=["ts", "open", "high", "low", "close", "volume"]
    )
    rows, prev_close = main._day_rows(frame, DAY)
    assert prev_close == 98.5 and len(rows) == 3 and rows[0][0] == RISE[0][0]
    assert main._day_rows(None, DAY) == ([], None)


def test_route_checks_the_day_through_the_terminal(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_news
    from app.main import app

    async def candles(symbols, day):
        return {s: {"rows": RISE, "prev_close": 99.0} for s in symbols}

    async def collect(day, symbols, alerts=True):
        return [{"symbol": "ACME", "title": "ACME wins order", "at": "2026-10-28T10:15:00+05:30", "sentiment": "POSITIVE",
                 "impact": "HIGH", "from": "news"}]

    monkeypatch.setattr(nv, "collect", collect)
    with TestClient(app) as client:
        monkeypatch.setattr(routes_news, "_news_candles", lambda: None)
        assert client.get("/api/news-impact/validate?symbols=ACME").status_code == 503
        monkeypatch.setattr(routes_news, "_news_candles", lambda: candles)
        out = client.get("/api/news-impact/validate?date=2026-10-28&symbols=acme&min_move=0.5").json()
        assert out["rows"][0]["verdict"] == "WITH" and out["summary"][0]["group"] == "Positive · High"
        assert client.get("/api/news-impact/validate?alerts=false").status_code == 400


def test_a_date_range_reads_each_weekday_and_reuses_the_candles():
    import asyncio

    seen = []

    async def candles(symbols, day, until=None, cache=None):
        seen.append((day, until, cache is not None))
        return {s: {"rows": RISE, "prev_close": 99.0} for s in symbols}

    async def collect(day, symbols, alerts=True):
        at = dt.datetime.combine(day, dt.time(10, 15), tzinfo=IST).isoformat()
        return [{"symbol": "ACME", "title": f"ACME wins order {day}", "at": at, "sentiment": "POSITIVE", "impact": "HIGH", "from": "news"}]

    orig = nv.collect
    nv.collect = collect
    try:
        out = asyncio.run(nv.validate(dt.date(2026, 10, 23), ["ACME"], candles, end=dt.date(2026, 10, 28), now=AFTER))
    finally:
        nv.collect = orig
    days = [d for d, _, _ in seen]
    assert days == [dt.date(2026, 10, 23), dt.date(2026, 10, 26), dt.date(2026, 10, 27), dt.date(2026, 10, 28)]  # no weekend
    assert all(until == dt.date(2026, 10, 28) and cached for _, until, cached in seen)
    assert out["days"] == 4 and out["end"] == "2026-10-28" and out["stocks"] == 1
    assert [r["day"] for r in out["rows"]] == ["2026-10-23", "2026-10-26", "2026-10-27", "2026-10-28"]
    # A reader without the range options still works, one day at a time.
    async def plain(symbols, day):
        return {s: {"rows": RISE, "prev_close": 99.0} for s in symbols}

    nv.collect = collect
    try:
        assert asyncio.run(nv.validate(dt.date(2026, 10, 27), ["ACME"], plain, end=dt.date(2026, 10, 28), now=AFTER))["days"] == 2
    finally:
        nv.collect = orig


def test_route_refuses_a_backwards_or_too_long_range(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import routes_news
    from app.main import app

    async def candles(symbols, day):
        return {}

    with TestClient(app) as client:
        monkeypatch.setattr(routes_news, "_news_candles", lambda: candles)
        today = dt.datetime.now(IST).date()
        a, b = (today - dt.timedelta(days=5)).isoformat(), (today - dt.timedelta(days=10)).isoformat()
        assert client.get(f"/api/news-impact/validate?date={a}&end={b}&symbols=ACME").status_code == 400
        far = (today - dt.timedelta(days=60)).isoformat()
        r = client.get(f"/api/news-impact/validate?date={far}&end={a}&symbols=ACME")
        assert r.status_code == 400 and "31 days" in r.json()["detail"]


def test_terminal_news_candles_download_a_range_once(monkeypatch):
    import asyncio

    import pandas as pd

    import main

    def day_frame(day):
        base = int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST).timestamp())
        return [[base + i * 60, 100, 100.2, 99.8, 100.1, 0] for i in range(3)]

    frame = pd.DataFrame(
        sum((day_frame(d) for d in (dt.date(2026, 10, 22), dt.date(2026, 10, 23), dt.date(2026, 10, 26))), []),
        columns=["ts", "open", "high", "low", "close", "volume"],
    )
    calls = []

    async def fetch(broker, symbol, start, end):
        calls.append((symbol, start.date(), end.date()))
        return frame

    async def no_wait():
        return None

    monkeypatch.setattr(main, "fetch_frame", fetch)
    monkeypatch.setattr(main, "_scan_broker", lambda: None)
    monkeypatch.setattr(main.groww_pressure, "wait_for_bots", no_wait)
    monkeypatch.setattr(main.scan_worker, "MIN_GAP_SEC", 0)
    cache: dict = {}
    first = asyncio.run(main.news_candles(["ACME"], dt.date(2026, 10, 23), until=dt.date(2026, 10, 26), cache=cache))
    second = asyncio.run(main.news_candles(["ACME"], dt.date(2026, 10, 26), until=dt.date(2026, 10, 26), cache=cache))
    assert len(calls) == 1 and calls[0][2] == dt.date(2026, 10, 26)
    assert len(first["ACME"]["rows"]) == 3 and first["ACME"]["prev_close"] == 100.1
    assert len(second["ACME"]["rows"]) == 3 and second["ACME"]["rows"][0][0] > first["ACME"]["rows"][-1][0]
