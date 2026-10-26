"""News entries (news_entry.py): a strong, fresh headline enters at once on the picked bot."""
from __future__ import annotations

import datetime as dt

import pytest

from tests.test_sma_atr_terminal import IST, _tape, api, engine  # noqa: F401

NOW = dt.datetime(2026, 9, 29, 11, 0, tzinfo=IST)


def _item(sentiment="POSITIVE", impact="HIGH", minutes=3, **extra) -> dict:
    return {
        "title": "Acme bags ₹2,000 crore order", "url": "https://example.com/a", "sentiment": sentiment,
        "impact": impact, "at": (NOW - dt.timedelta(minutes=minutes)).isoformat(), "key": f"k-{sentiment}-{impact}-{minutes}",
        **extra,
    }


def _settings(**over):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.news_entry_bot = 1
        row.trading_mode = "PAPER"
        for k, v in over.items():
            setattr(row, k, v)
        db.commit()


@pytest.fixture(autouse=True)
def _fresh_memory():
    import news_entry

    news_entry._taken.clear()
    news_entry._today.clear()
    news_entry.recent.clear()
    yield


def test_why_not_reads_impact_age_direction_and_relevance():
    import news_entry

    conf = {"bot": 1, "impact": "MEDIUM", "scope": "FNO", "max_age_min": 15, "max_day": 5}
    assert news_entry.why_not(_item(), conf, NOW) is None
    assert news_entry.why_not(_item(impact="MEDIUM"), conf, NOW) is None
    assert "below medium" in news_entry.why_not(_item(impact="LOW"), conf, NOW)
    assert news_entry.why_not(_item(impact="LOW", critical=True), conf, NOW) is None  # critical counts as HIGH
    assert "min old" in news_entry.why_not(_item(minutes=40), conf, NOW)
    assert "neutral" in news_entry.why_not(_item(sentiment="NEUTRAL"), conf, NOW)
    assert "another company" in news_entry.why_not(_item(about=False), conf, NOW)
    assert news_entry.signal_of(_item(sentiment="NEGATIVE")) == "BEARISH"


@pytest.mark.asyncio
async def test_good_news_buys_at_once_on_any_fno_stock_without_a_cross(engine, monkeypatch):
    import news_entry
    from strategy_engine import trade_names, trade_sources

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: NOW)
    _settings(trade_symbols="TCS", use_vwap=True)  # VWAP would refuse a cross; news ignores it
    engine._frames["ACME"] = _tape([100.0 - i * 0.2 for i in range(40)])  # falling: SMAs bearish
    engine._ltps["ACME"] = 50.0
    engine.status = "RUNNING"
    result = await news_entry.consider("ACME", _item(), engine.load_config(), engine, NOW)
    assert result == "opened LONG"
    pos = engine.positions["ACME"]
    assert pos.direction == "LONG"
    cfg = engine.load_config()
    assert "ACME" in trade_names(cfg)
    assert trade_sources(cfg)["ACME"] == "News"
    # The same headline is never traded twice.
    assert await news_entry.consider("ACME", _item(), cfg, engine, NOW) is None
    assert news_entry.recent[0]["result"] == "opened LONG"


@pytest.mark.asyncio
async def test_bad_news_reverses_a_long_and_the_flip_setting_is_not_applied(engine, monkeypatch):
    import news_entry

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: NOW)
    _settings(trade_symbols="ACME", flip_orders=True)
    engine._frames["ACME"] = _tape([100.0 + i * 0.2 for i in range(40)])
    engine._ltps["ACME"] = 108.0
    engine.status = "RUNNING"
    first = await news_entry.consider("ACME", _item(), engine.load_config(), engine, NOW)
    assert first == "opened LONG" and engine.positions["ACME"].direction == "LONG"
    second = await news_entry.consider("ACME", _item(sentiment="NEGATIVE", minutes=1), engine.load_config(), engine, NOW)
    assert second == "reversed to SHORT"
    assert engine.positions["ACME"].direction == "SHORT"


@pytest.mark.asyncio
async def test_refusals_scope_stopped_bot_closed_market_and_daily_cap(engine, monkeypatch):
    import news_entry

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: NOW)
    market = {"open": True}
    monkeypatch.setattr("groww_client.market_is_open", lambda now=None: market["open"])
    _settings(trade_symbols="TCS", news_entry_scope="ARMED", news_entry_max_day=1)
    engine._frames["ACME"] = _tape([100.0 + i * 0.2 for i in range(40)])
    engine._ltps["ACME"] = 108.0
    engine.status = "RUNNING"
    out = await news_entry.consider("ACME", _item(minutes=1), engine.load_config(), engine, NOW)
    assert "not armed" in out and "ACME" not in engine.positions

    engine.status = "STOPPED"
    out = await news_entry.consider("TCS", _item(minutes=2), engine.load_config(), engine, NOW)
    assert "not RUNNING" in out

    engine.status = "RUNNING"
    market["open"] = False
    out = await news_entry.consider("TCS", _item(minutes=4), engine.load_config(), engine, NOW)
    assert "market is closed" in out

    market["open"] = True
    engine._frames["TCS"] = _tape([100.0 + i * 0.2 for i in range(40)])
    engine._ltps["TCS"] = 108.0
    assert await news_entry.consider("TCS", _item(minutes=5), engine.load_config(), engine, NOW) == "opened LONG"
    _settings(trade_symbols="TCS,ACME", news_entry_scope="ARMED", news_entry_max_day=1)
    out = await news_entry.consider("ACME", _item(minutes=6), engine.load_config(), engine, NOW)
    assert "1 news entries already today" in out
    assert engine.broker.events.count("ENTRY BUY") == 1


@pytest.mark.asyncio
async def test_off_by_default_and_weak_news_is_ignored(engine, monkeypatch):
    import news_entry

    cfg = engine.load_config()
    assert news_entry.settings(cfg) == {"bot": 0, "impact": "HIGH", "scope": "FNO", "max_age_min": 15, "max_day": 5}
    assert await news_entry.consider("ACME", _item(), cfg, engine, NOW) is None
    _settings()
    assert await news_entry.consider("ACME", _item(impact="MEDIUM"), engine.load_config(), engine, NOW) is None
    assert news_entry.recent == []


def test_settings_route_needs_confirm_for_a_live_bot(api):
    import database
    from models import BotConfig

    got = api.get("/api/bots/news-entry").json()
    assert got["bot"] == 0 and got["impact"] == "HIGH"
    saved = api.put("/api/bots/news-entry", json={"bot": 1, "impact": "LOW", "scope": "ARMED", "max_age_min": 10, "max_day": 3})
    assert saved.status_code == 200
    assert saved.json()["impact"] == "LOW" and saved.json()["mode"] == "PAPER"
    with database.session_factory()() as db:
        db.get(BotConfig, 1).trading_mode = "LIVE"
        db.commit()
    refused = api.put("/api/bots/news-entry", json={"bot": 1, "impact": "HIGH"})
    assert refused.status_code == 409
    with database.session_factory()() as db:
        db.get(BotConfig, 1).trading_mode = "PAPER"
        db.commit()
