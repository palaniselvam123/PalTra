"""The blotter loads a whole book (up to 20,000 trades), not the newest 200."""
from __future__ import annotations

import datetime as dt
import json

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/book.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    from config import get_settings

    get_settings.cache_clear()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app, engine

    # Earlier tests may leave the shared engine's stub broker in LIVE; these run in PAPER.
    monkeypatch.setattr(engine.broker, "mode", "PAPER")
    monkeypatch.setattr(engine.broker, "token", "")
    monkeypatch.setattr(engine.broker, "adopt_saved_session", lambda **_: None)
    with TestClient(app) as http:
        yield http, engine
    assert engine.broker.mode == "PAPER"
    database.reset_engine()
    get_settings.cache_clear()


def _add(n: int, mode: str | None, strategy: dict | None = None) -> None:
    from database import session_factory
    from models import TradeLog

    start = dt.datetime(2026, 9, 29, 9, 30)
    with session_factory()() as s:
        s.add_all(
            [
                TradeLog(
                    date="2026-09-29", symbol="TCS", direction="LONG", qty=1,
                    entry_time=start, entry_price=100.0 + i, ma_cross_price=100.0 + i,
                    atr_at_entry=1.0, sl_trigger_price=99.0 + i,
                    exit_time=start + dt.timedelta(minutes=5), exit_price=101.0 + i,
                    exit_reason="MA_CROSS", gross_pnl=1.0, net_pnl=0.5, mode=mode,
                    strategy=json.dumps(strategy) if strategy else None,
                )
                for i in range(n)
            ]
        )
        s.commit()


def test_a_book_returns_far_more_than_200_trades(client):
    http, _ = client
    _add(450, "REPLAY", {"stop_type": "ATR", "qty": 1})
    _add(30, "PAPER")
    res = http.get("/api/trades/book", params={"mode": "REPLAY"})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 450
    assert len(body["rows"]) == 450
    assert all(r["mode"] == "REPLAY" for r in body["rows"])
    # Newest first, like /api/trades.
    ids = [r["id"] for r in body["rows"]]
    assert ids == sorted(ids, reverse=True)
    # One shared strategy is sent once and referenced from every row.
    assert body["strategies"] == [{"stop_type": "ATR", "qty": 1}]
    assert {r["strategy_ref"] for r in body["rows"]} == {0}
    assert "strategy" not in body["rows"][0]


def test_the_practice_book_keeps_rows_with_no_mode_and_says_how_many_it_holds(client):
    http, engine = client
    _add(5, "PAPER")
    _add(3, None)
    _add(4, "REPLAY")
    body = http.get("/api/trades/book", params={"mode": "paper", "limit": 6}).json()
    assert body["total"] == 8
    assert len(body["rows"]) == 6
    assert body["rows"][0]["strategy_ref"] is None
    assert http.get("/api/trades/counts").json() == {"PAPER": 8, "LIVE": 0, "REPLAY": 4}
    # The poll endpoint is unchanged: newest 200 across books.
    assert len(http.get("/api/trades").json()) == 12


def test_a_book_is_capped_at_20000_and_refuses_an_unknown_mode(client):
    http, engine = client
    from strategy_engine import BOOK_LIMIT

    assert BOOK_LIMIT == 20000
    _add(3, "LIVE")
    assert len(engine.book("LIVE", limit=10**9)["rows"]) == 3
    assert http.get("/api/trades/book", params={"mode": "SOMETHING"}).status_code == 422


def test_the_csv_holds_the_whole_book(client):
    http, _ = client
    _add(250, "REPLAY")
    text = http.get("/api/trades.csv", params={"mode": "REPLAY"}).text
    assert len(text.strip().splitlines()) == 251  # header + every trade


def test_a_big_book_is_sent_gzipped(client):
    http, _ = client
    _add(300, "REPLAY", {"stop_type": "TSL", "qty": 1})
    res = http.get("/api/trades/book", params={"mode": "REPLAY"}, headers={"Accept-Encoding": "gzip"})
    assert res.headers.get("content-encoding") == "gzip"
    assert len(res.json()["rows"]) == 300
