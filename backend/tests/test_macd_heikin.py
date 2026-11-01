"""MACD check / exit (macd.py) and Heikin Ashi colours (heikin_ashi.py).

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import heikin_ashi
import macd
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


def test_macd_entry_follows_the_histogram_side():
    cfg = Cfg(macd_fast=3, macd_slow=6, macd_signal=3)
    rising = _closes([100 + i * (1 + i * 0.05) for i in range(30)])  # speeding up: MACD above its signal
    assert macd.entry_reason(rising, "LONG", cfg) is None
    assert "not below" in macd.entry_reason(rising, "SHORT", cfg)
    falling = _closes([300 - i * (1 + i * 0.05) for i in range(30)])
    assert macd.entry_reason(falling, "SHORT", cfg) is None
    assert "not above" in macd.entry_reason(falling, "LONG", cfg)
    assert "not ready" in macd.entry_reason(_closes([100.0] * 5), "LONG", cfg)


def test_macd_cross_against_is_read_once():
    cfg = Cfg(macd_fast=3, macd_slow=6, macd_signal=3)
    values = [100 + i for i in range(20)] + [119 - 2 * i for i in range(1, 15)]
    closed = pd.DataFrame({"close": values})
    hits = [i for i in range(10, len(values) + 1) if macd.crossed_against(closed.iloc[:i], "LONG", cfg)[0]]
    assert len(hits) == 1
    assert macd.lengths(Cfg()) == (12, 26, 9)
    assert macd.lengths(Cfg(macd_fast=0, macd_slow=9999, macd_signal=0)) == (12, macd.MAX_LEN, 9)


def test_heikin_ashi_smooths_a_lone_counter_candle_and_keeps_the_close():
    # A rise with one small red candle in it: real colours G G G R G, Heikin Ashi all green.
    rows = []
    for i, (o, c) in enumerate([(100, 101), (101, 102), (102, 103), (103, 102.6), (102.6, 104), (104, 104)]):
        rows.append({"ts": i * 60, "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "close": c})
    frame = pd.DataFrame(rows)
    off = heikin_ashi.view(frame, Cfg(ha_colours=False))
    assert off is frame
    on = heikin_ashi.view(frame, Cfg(ha_colours=True))
    assert list(on["close"]) == list(frame["close"])  # prices never change
    # (The first Heikin Ashi candle has no candle before it to smooth with, so it is left out.)
    real = ["G" if c > o else "R" for o, c in zip(frame["open"].iloc[1:5], frame["close"].iloc[1:5])]
    ha = ["G" if c > o else "R" for o, c in zip(on["open"].iloc[1:5], on["close"].iloc[1:5])]
    assert real == ["G", "G", "R", "G"] and ha == ["G"] * 4
    ha_open, ha_close = heikin_ashi.candles(frame)
    assert abs(ha_close.iloc[1] - (101 + 102.2 + 100.8 + 102) / 4) < 1e-9
    assert abs(ha_open.iloc[1] - (ha_open.iloc[0] + ha_close.iloc[0]) / 2) < 1e-9


def test_colour_entry_reads_heikin_ashi_when_on():
    from candle_patterns import colour_call

    rows = []
    for i, (o, c) in enumerate([(100, 101), (101, 102), (102, 103), (103, 102.6), (103, 103)]):
        rows.append({"ts": 1_700_000_000 + i * 60, "open": o, "high": max(o, c) + 0.2, "low": min(o, c) - 0.2, "close": c})
    frame = pd.DataFrame(rows)
    assert colour_call(frame, Cfg(pattern_tf=1)).side == "SHORT"  # the real last closed candle is red
    assert colour_call(frame, Cfg(pattern_tf=1, ha_colours=True)).side == "LONG"  # smoothed: still green


@pytest.mark.asyncio
async def test_macd_exit_and_entry_in_a_replay(db):  # noqa: F811
    plain = _first(await _trades(db), "SHORT")
    assert plain["exit_reason"] == "MA_CROSS"
    # A fast MACD turns up before SMA 9 / 21 does: the short closes on the MACD cross, earlier.
    out = _first(await _trades(db, macd_exit=True, macd_fast=3, macd_slow=6, macd_signal=3), "SHORT")
    assert out["entry_time"] == plain["entry_time"]
    assert out["exit_reason"] == "MACD_CROSS"
    assert out["exit_time"] < plain["exit_time"]
    # The entry check never adds a trade; it can only refuse some.
    gated = await _trades(db, use_macd=True)
    assert len(gated) <= len(await _trades(db))


def test_api_saves_the_macd_and_heikin_ashi_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_macd", "macd_exit", "macd_fast", "macd_slow", "macd_signal", "ha_colours"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_macd"] is False and first["macd_exit"] is False and first["ha_colours"] is False
        assert (first["macd_fast"], first["macd_slow"], first["macd_signal"]) == (12, 26, 9)
        ok = client.put("/api/config", json={"use_macd": True, "macd_exit": True, "macd_fast": 8, "macd_slow": 21, "macd_signal": 5, "ha_colours": True})
        body = ok.json()
        assert ok.status_code == 200 and body["use_macd"] and body["macd_exit"] and body["ha_colours"]
        assert (body["macd_fast"], body["macd_slow"], body["macd_signal"]) == (8, 21, 5)
        assert client.put("/api/config", json={"macd_slow": 0}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"ha_colours": False})
        assert own.status_code == 200 and own.json()["ha_colours"] is False
    database.reset_engine()
