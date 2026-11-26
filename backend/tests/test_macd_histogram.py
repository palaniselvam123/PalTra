"""MACD histogram growing entry check and shrinking exit (macd.py)."""
from __future__ import annotations

import pytest

import macd
from tests.test_macd_heikin import Cfg, _closes, _first, _trades
from tests.test_gap_mode import db  # noqa: F401  (db is a fixture)


def _hist(closes, cfg):
    return list(macd.series(_closes(closes).iloc[:-1], *macd.lengths(cfg))[2])


def test_growing_check_needs_the_histogram_to_move_the_trades_way_n_candles():
    cfg = Cfg(macd_fast=3, macd_slow=6, macd_signal=3, macd_grow_candles=2)
    speeding = [100 * 1.02**i for i in range(30)]  # rising faster and faster: histogram keeps rising
    h = _hist(speeding, cfg)
    assert h[-1] > h[-2] > h[-3]
    assert macd.grow_reason(_closes(speeding), "LONG", cfg) is None
    assert "not falling" in macd.grow_reason(_closes(speeding), "SHORT", cfg)
    # Then it slows down: the bars shrink, so a buy waits.
    slowing = speeding + [speeding[-1] * 1.001, speeding[-1] * 1.0015]
    assert "not rising" in macd.grow_reason(_closes(slowing), "LONG", cfg)
    assert "not ready" in macd.grow_reason(_closes([100.0] * 8), "LONG", cfg)
    assert macd.grow_candles(Cfg()) == 2 and macd.grow_candles(Cfg(macd_grow_candles=99)) == macd.MAX_RUN


def test_shrinking_exit_counts_only_candles_closed_after_the_entry():
    cfg = Cfg(macd_fast=3, macd_slow=6, macd_signal=3, macd_shrink_candles=2)
    up = [100 * 1.02**i for i in range(30)]
    fade = up + [up[-1] * 1.001, up[-1] * 1.0015, up[-1] * 1.0017]  # still rising, but slower: the bars shrink
    closed = _closes(fade).iloc[:-1]
    hit, note = macd.shrunk_against(closed, "LONG", cfg, after=3)
    assert hit and "fell 2 candles in a row" in note
    # Only one candle has closed since the entry: not enough to judge two steps.
    assert macd.shrunk_against(closed, "LONG", cfg, after=1) == (False, "")
    # A short is not hurt by a falling histogram.
    assert macd.shrunk_against(closed, "SHORT", cfg, after=3)[0] is False
    # While the bars keep growing the buy stays.
    assert macd.shrunk_against(_closes(up).iloc[:-1], "LONG", cfg, after=5)[0] is False


@pytest.mark.asyncio
async def test_shrinking_exit_closes_earlier_in_a_replay(db):  # noqa: F811
    plain = _first(await _trades(db), "SHORT")
    out = _first(await _trades(db, macd_shrink_exit=True, macd_shrink_candles=2, macd_fast=3, macd_slow=6, macd_signal=3), "SHORT")
    assert out["entry_time"] == plain["entry_time"]
    assert out["exit_reason"] == "MACD_SHRINK"
    assert out["exit_time"] <= plain["exit_time"]
    # The entry check never adds a trade; it can only refuse some.
    gated = await _trades(db, use_macd_grow=True, macd_fast=3, macd_slow=6, macd_signal=3)
    assert len(gated) <= len(await _trades(db))


def test_api_saves_the_histogram_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_macd_grow", "macd_grow_candles", "macd_shrink_exit", "macd_shrink_candles"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_macd_grow"] is False and first["macd_shrink_exit"] is False
        assert first["macd_grow_candles"] == 2 and first["macd_shrink_candles"] == 2
        ok = client.put("/api/config", json={"use_macd_grow": True, "macd_grow_candles": 3, "macd_shrink_exit": True, "macd_shrink_candles": 1})
        body = ok.json()
        assert ok.status_code == 200 and body["use_macd_grow"] and body["macd_shrink_exit"]
        assert body["macd_grow_candles"] == 3 and body["macd_shrink_candles"] == 1
        assert client.put("/api/config", json={"macd_shrink_candles": 9}).status_code == 422
    database.reset_engine()
