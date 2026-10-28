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
