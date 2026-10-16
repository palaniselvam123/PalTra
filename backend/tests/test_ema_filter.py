"""EMA cross check (ema_filter.py): entry only on the EMA side, exit on an EMA cross against the trade.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import ema_filter
from tests.test_counter_candles import DAY, TURN, _first, _frame
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_frame(), "SIME", [DAY], _settings(database, **over))


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _closes(values):
    rows = [{"ts": i * 60, "open": v, "high": v, "low": v, "close": v} for i, v in enumerate(values)]
    return pd.DataFrame(rows + [rows[-1]])  # last row = the forming candle (never read)


def test_entry_reason_follows_the_ema_side():
    cfg = Cfg(ema_fast=3, ema_slow=10)
    rising = _closes([100 + i for i in range(30)])
    assert ema_filter.entry_reason(rising, "LONG", cfg) is None
    assert "not below" in ema_filter.entry_reason(rising, "SHORT", cfg)
    falling = _closes([200 - i for i in range(30)])
    assert ema_filter.entry_reason(falling, "SHORT", cfg) is None
    assert "not above" in ema_filter.entry_reason(falling, "LONG", cfg)
    short = _closes([100.0] * 5)
    assert "not ready" in ema_filter.entry_reason(short, "LONG", cfg)


def test_crossed_against_only_on_the_cross_candle():
    cfg = Cfg(ema_fast=3, ema_slow=10)
    values = [100 + i for i in range(20)] + [119 - 3 * i for i in range(1, 15)]
    closed = pd.DataFrame({"close": values})
    hits = [i for i in range(12, len(values) + 1) if ema_filter.crossed_against(closed.iloc[:i], "LONG", cfg)[0]]
    assert len(hits) == 1  # one cross, read once
    assert not any(ema_filter.crossed_against(closed.iloc[:i], "SHORT", cfg)[0] for i in range(12, len(values) + 1))


def test_lengths_are_clamped_and_default():
    assert ema_filter.lengths(Cfg()) == (9, 200)
    assert ema_filter.lengths(Cfg(ema_fast=0, ema_slow=9999)) == (9, ema_filter.MAX_LEN)


@pytest.mark.asyncio
async def test_ema_entry_skips_the_buy_against_the_slow_ema(db):  # noqa: F811
    plain = await _trades(db)
    assert _first(plain, "LONG", since=TURN)["entry_time"] < TURN + dt.timedelta(minutes=30)
    # EMA 200 is still far above the price right after the fall: the buys at the turn are skipped.
    gated = await _trades(db, use_ema=True, ema_fast=9, ema_slow=200)
    early = [t for t in gated if t["direction"] == "LONG" and TURN <= t["entry_time"] < TURN + dt.timedelta(minutes=30)]
    assert early == []
    # The short in the fall (fast EMA under the slow one) still goes.
    assert _first(gated, "SHORT")["entry_time"] == _first(plain, "SHORT")["entry_time"]


@pytest.mark.asyncio
async def test_ema_exit_closes_the_short_when_the_emas_cross_up(db):  # noqa: F811
    plain = _first(await _trades(db), "SHORT")
    assert plain["exit_reason"] == "MA_CROSS"
    # EMA 3 / 8 turns up before SMA 9 / 21 does: the short closes on the EMA cross, earlier.
    out = _first(await _trades(db, ema_exit=True, ema_fast=3, ema_slow=8), "SHORT")
    assert out["entry_time"] == plain["entry_time"]
    assert out["exit_reason"] == "EMA_CROSS"
    assert TURN < out["exit_time"] < plain["exit_time"]


def test_api_saves_the_ema_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_ema", "ema_exit", "ema_fast", "ema_slow"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_ema"] is False and first["ema_exit"] is False
        assert (first["ema_fast"], first["ema_slow"]) == (9, 200)
        ok = client.put("/api/config", json={"use_ema": True, "ema_exit": True, "ema_fast": 12, "ema_slow": 50})
        body = ok.json()
        assert ok.status_code == 200 and body["use_ema"] and body["ema_exit"] and (body["ema_fast"], body["ema_slow"]) == (12, 50)
        assert client.put("/api/config", json={"ema_slow": 0}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"ema_exit": False})
        assert own.status_code == 200 and own.json()["ema_exit"] is False
    database.reset_engine()


def test_chart_rows_carry_the_ema_lines_only_when_asked():
    from strategy_engine import candle_rows, chart_filters

    frame = _closes([100 + i * 0.5 for i in range(40)]).assign(volume=1000)
    plain = candle_rows(frame)
    assert "emaFast" not in plain[-1]
    rows = candle_rows(frame, (3, 10))
    assert rows[1]["emaFast"] is None and rows[2]["emaFast"] is not None  # 3 candles before EMA 3 shows
    assert rows[8]["emaSlow"] is None and rows[9]["emaSlow"] is not None
    fast, slow = ema_filter.emas(frame, 3, 10)
    assert abs(rows[-2]["emaFast"] - float(fast.iloc[-2])) < 1e-9 and abs(rows[-2]["emaSlow"] - float(slow.iloc[-2])) < 1e-9

    off = chart_filters(Cfg(use_ema=False, ema_exit=False))
    assert off["ema_lines"] is False and (off["ema_fast"], off["ema_slow"]) == (9, 200)
    assert chart_filters(Cfg(use_ema=False, ema_exit=True, ema_fast=5, ema_slow=50))["ema_lines"] is True
