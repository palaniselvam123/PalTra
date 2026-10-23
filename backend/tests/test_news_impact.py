"""News impact: rule scores, overall view, action per position, AI merge, and the alert watcher."""
from __future__ import annotations

import datetime as dt
import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.market_clock import IST
from app.models.database import NewsAlert, NewsWatchSettings
from app.services import news_impact as ni

NOW = dt.datetime(2026, 10, 23, 10, 0, tzinfo=IST)


def at(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).isoformat()


@pytest.mark.parametrize(
    "title,sentiment,critical",
    [
        ("L&T bags mega order worth Rs 10,000 crore", "POSITIVE", False),
        ("Infosys Q2 net profit rises 8%, beats estimates", "POSITIVE", False),
        ("Vedanta profit falls 40%, misses street estimates", "NEGATIVE", False),
        ("Income tax raids at offices of XYZ Ltd", "NEGATIVE", True),
        ("SEBI bars promoters of ABC from securities market", "NEGATIVE", True),
        ("HDFC Bank CEO resigns citing personal reasons", "NEGATIVE", True),
        ("Open offer by Adani for Ambuja", "POSITIVE", True),
        ("Reliance Industries to hold AGM on Aug 29", "NEUTRAL", False),
    ],
)
def test_rules(title, sentiment, critical):
    r = ni.score_text(title)
    assert r["sentiment"] == sentiment and r["critical"] is critical
    if critical:
        assert r["impact"] == "HIGH"


def test_denial_softens_and_is_not_critical():
    r = ni.score_text("Company denies reports of income tax raids")
    assert not r["critical"] and r["impact"] != "HIGH" and r["score"] < 0


def test_overall_weights_recent_and_marks_mixed():
    items = [
        {**ni.score_text("Profit falls 40%, misses estimates"), "at": at(1)},
        {**ni.score_text("Bags order worth Rs 500 crore"), "at": at(60)},
    ]
    v = ni.overall(items, NOW)
    assert v["sentiment"] == "NEGATIVE"
    old = [{"score": 0.9, "sentiment": "POSITIVE", "impact": "HIGH", "at": at(24 * 5)}]
    assert ni.overall(old, NOW)["counted"] == 0
    mixed = [
        {"score": 0.5, "sentiment": "POSITIVE", "impact": "MEDIUM", "at": at(2)},
        {"score": -0.5, "sentiment": "NEGATIVE", "impact": "MEDIUM", "at": at(2)},
    ]
    assert ni.overall(mixed, NOW)["sentiment"] == "MIXED"


@pytest.mark.parametrize(
    "score,critical,side,code",
    [
        (-0.8, False, "LONG", "EXIT"),
        (-0.3, False, "LONG", "TIGHTEN"),
        (0.4, False, "LONG", "KEEP"),
        (0.8, False, "SHORT", "COVER"),
        (-0.6, False, "SHORT", "KEEP"),
        (0.7, False, None, "BUY"),
        (-0.7, False, None, "SHORT"),
        (-0.7, True, None, "AVOID"),
        (0.05, False, None, "NO_ACTION"),
        (-0.3, True, "LONG", "EXIT"),
    ],
)
def test_action(score, critical, side, code):
    assert ni.action({"score": score, "critical": critical}, side)["code"] == code


def test_critical_needs_a_held_position_against_it():
    bad = {"sentiment": "NEGATIVE", "impact": "HIGH", "critical": False}
    assert ni.is_critical(bad, "LONG") and not ni.is_critical(bad, "SHORT") and not ni.is_critical(bad, None)
    assert ni.is_critical({"sentiment": "POSITIVE", "impact": "MEDIUM", "critical": True}, "SHORT")


def test_merge_ai_replaces_scores_and_overall():
    items = [{"title": "a", **ni.score_text("a")}, {"title": "b", **ni.score_text("b")}]
    ai = {
        "items": [{"index": 1, "sentiment": "NEGATIVE", "impact": "HIGH", "critical": True, "why": "bad"}],
        "overall": {"sentiment": "NEGATIVE", "score": -0.7, "summary": "Bad news."},
    }
    out, view = ni.merge_ai(items, ai)
    assert out[1]["by"] == "ai" and out[1]["critical"] and out[1]["score"] < 0
    assert view["score"] == -0.7 and view["summary"] == "Bad news." and view["impact"] == "HIGH"


@pytest.mark.asyncio
async def test_analyze_falls_back_to_rules_without_a_key(monkeypatch):
    from app.services import stock_news

    async def news(symbol, force=False):
        return {"symbol": "XYZ", "name": "XYZ Ltd", "errors": {},
                "items": [{"title": "XYZ CEO resigns", "at": at(1), "source": "NSE", "url": "u1"}]}

    async def no_key():
        return None

    monkeypatch.setattr(stock_news, "stock_news", news)
    monkeypatch.setattr(ni, "_openai_key", no_key)
    r = await ni.analyze("XYZ", side="LONG")
    assert r["method"] == "rules" and "OpenAI key" in r["ai_error"]
    assert r["action"]["code"] == "EXIT" and r["overall"]["critical"]


@pytest_asyncio.fixture
async def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(NewsAlert.__table__.create)
        await conn.run_sync(NewsWatchSettings.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(ni, "async_session", maker)
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_watcher_alerts_once_and_rings_only_for_critical(db, monkeypatch):
    from app.services import stock_news
    from app.services.alert_notifier import alert_notifier

    news = {
        "HELD": [{"title": "HELD Ltd: income tax raids at offices", "at": at(1), "source": "Google", "url": "h1"},
                 {"title": "HELD to hold AGM", "at": at(1), "source": "NSE", "url": "h2"}],
        "ARMED": [{"title": "ARMED bags order worth Rs 900 crore", "at": at(2), "source": "NSE", "url": "a1"},
                  {"title": "ARMED profit rises, beats estimates", "at": at(30), "source": "NSE", "url": "a2"}],
    }

    async def fake_news(symbol, force=False):
        return {"symbol": symbol, "name": symbol, "errors": {}, "items": news[symbol]}

    async def no_key():
        return None

    sent, calls = [], []

    async def send(msg):
        sent.append(msg)

    async def send_critical(msg, call=False, call_any_time=False):
        calls.append((msg, call))

    monkeypatch.setattr(stock_news, "stock_news", fake_news)
    monkeypatch.setattr(ni, "_openai_key", no_key)
    monkeypatch.setattr(ni, "watch_list", lambda extra=None: {
        "HELD": {"armed": ["Bot 1"], "held": [{"bot": 1, "name": "Bot 1", "mode": "PAPER", "side": "LONG", "qty": 10}]},
        "ARMED": {"armed": ["Bot 2"], "held": []},
    })
    monkeypatch.setattr(alert_notifier, "send", send)
    monkeypatch.setattr(alert_notifier, "send_critical", send_critical)

    watcher = ni.NewsWatcher()
    cfg = NewsWatchSettings(id=1, enabled=True, every_min=15, min_impact="MEDIUM", use_ai=True, popup=True,
                            telegram=True, call_critical=True, extra_symbols="")
    made = await watcher.run_once(cfg, NOW)
    by = {r.symbol: r for r in made}
    assert set(by) == {"HELD", "ARMED"}
    assert by["HELD"].critical and by["HELD"].action == "EXIT"
    assert not by["ARMED"].critical  # armed, not held: Telegram only
    assert len(calls) == 1 and calls[0][1] is True and "CRITICAL" in calls[0][0]
    assert len(sent) == 1 and "ARMED" in sent[0] and "bags order" in sent[0]
    body = json.loads(by["ARMED"].body)
    assert [i["title"] for i in body["items"]] == ["ARMED bags order worth Rs 900 crore"]  # 30 h old one is not alerted

    # A second pass, and a fresh watcher after a restart, alert nothing again.
    assert await watcher.run_once(cfg, NOW) == []
    assert await ni.NewsWatcher().run_once(cfg, NOW) == []
    assert len(calls) == 1 and len(sent) == 1


def test_routes_mounted():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert {"/api/news-impact/stock/{symbol}", "/api/news-impact/alerts", "/api/news-impact/settings"} <= paths
