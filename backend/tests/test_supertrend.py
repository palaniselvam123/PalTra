"""Supertrend (supertrend.py): entry only on its side, exit when it flips against the trade.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import supertrend
from tests.test_counter_candles import DAY, TURN, _first, _frame
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _bars(closes, spread=0.5):
    rows = []
    prev = closes[0]
    for i, c in enumerate(closes):
        rows.append({"ts": i * 60, "open": prev, "high": max(prev, c) + spread, "low": min(prev, c) - spread, "close": c})
        prev = c
    return pd.DataFrame(rows)


def _reference(frame, period, factor):
    """Plain re-implementation of TradingView's Supertrend (RMA ATR seeded with the SMA)."""
    h, l, c = frame["high"].tolist(), frame["low"].tolist(), frame["close"].tolist()
    trs = [max(h[i] - l[i], abs(h[i] - (c[i - 1] if i else c[i])), abs(l[i] - (c[i - 1] if i else c[i]))) for i in range(len(c))]
    out, atr, up, dn, d = [], None, None, None, 1
    for i in range(len(c)):
        if i + 1 < period:
            out.append(None)
            continue
        atr = sum(trs[: period]) / period if atr is None else (atr * (period - 1) + trs[i]) / period
        mid = (h[i] + l[i]) / 2
        bu, bd = mid + factor * atr, mid - factor * atr
        if up is None:
            up, dn, d = bu, bd, (1 if c[i] >= mid else -1)
        else:
            up = bu if (bu < up or c[i - 1] > up) else up
            dn = bd if (bd > dn or c[i - 1] < dn) else dn
            d = 1 if (d == -1 and c[i] > up) else (-1 if (d == 1 and c[i] < dn) else d)
        out.append(d)
    return out


def test_compute_matches_the_reference_and_flips_with_the_trend():
    closes = [100 + i for i in range(30)] + [129 - 2 * i for i in range(1, 30)] + [71 + 2 * i for i in range(1, 30)]
    frame = _bars(closes)
    line, trend = supertrend.compute(frame, 10, 3.0)
    assert trend == _reference(frame, 10, 3.0)
    assert trend[9] == 1 and trend[25] == 1  # the rise
    assert -1 in trend[30:60] and trend[58] == -1  # the fall
    assert trend[-1] == 1  # back up
    # The line sits under the price while up and above it while down.
    assert line[25] < closes[25] and line[58] > closes[58]
    assert trend[:9] == [None] * 9 and line[8] is None


def test_entry_reason_and_flip():
    cfg = Cfg(st_period=10, st_mult=3)
    rising = _bars([100 + i for i in range(40)])
    rising = pd.concat([rising, rising.tail(1)], ignore_index=True)  # forming candle
    assert supertrend.entry_reason(rising, "LONG", cfg) is None
    assert "down" not in supertrend.entry_reason(rising, "SHORT", cfg) and "up" in supertrend.entry_reason(rising, "SHORT", cfg)
    assert "not ready" in supertrend.entry_reason(_bars([100.0] * 5), "LONG", cfg)
    closes = [100 + i for i in range(30)] + [129 - 2 * i for i in range(1, 20)]
    frame = _bars(closes)
    flips = [i for i in range(12, len(closes) + 1) if supertrend.flipped_against(frame.iloc[:i], "LONG", cfg)[0]]
    assert len(flips) == 1  # one flip, read once
    assert not any(supertrend.flipped_against(frame.iloc[:i], "SHORT", cfg)[0] for i in range(12, len(closes) + 1))


def test_settings_are_clamped():
    assert supertrend.settings(Cfg()) == (10, 3.0)
    assert supertrend.settings(Cfg(st_period=0, st_mult=99)) == (10, supertrend.MAX_FACTOR)
    assert supertrend.settings(Cfg(st_period=500, st_mult=0.1)) == (supertrend.MAX_PERIOD, supertrend.MIN_FACTOR)


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_frame(), "SIMS", [DAY], _settings(database, **over))


@pytest.mark.asyncio
async def test_supertrend_exit_closes_the_short_on_the_flip(db):  # noqa: F811
    plain = _first(await _trades(db), "SHORT")
    assert plain["exit_reason"] == "MA_CROSS"
    out = _first(await _trades(db, st_exit=True, st_period=5, st_mult=1.5), "SHORT")
    assert out["entry_time"] == plain["entry_time"]
    assert out["exit_reason"] == "SUPERTREND"
    assert TURN < out["exit_time"] < plain["exit_time"]


@pytest.mark.asyncio
async def test_supertrend_entry_check_skips_crosses_against_it(db):  # noqa: F811
    plain = await _trades(db)
    gated = await _trades(db, use_supertrend=True, st_period=10, st_mult=3)
    # The flat morning crosses back and forth; the Supertrend lets far fewer through.
    morning = lambda ts: [t for t in ts if t["entry_time"] < dt.datetime(2026, 9, 22, 10, 15)]  # noqa: E731
    assert len(morning(gated)) < len(morning(plain))
    assert all(t["direction"] in ("LONG", "SHORT") for t in gated)


def test_chart_rows_and_filters():
    from strategy_engine import candle_rows, chart_filters

    frame = _bars([100 + i for i in range(30)] + [129 - 2 * i for i in range(1, 20)]).assign(volume=1000)
    assert "stUp" not in candle_rows(frame)[-1]
    rows = candle_rows(frame, None, (10, 3.0))
    assert rows[5]["stUp"] is None and rows[5]["stDown"] is None
    assert rows[20]["stUp"] is not None and rows[20]["stDown"] is None
    assert rows[-1]["stDown"] is not None and rows[-1]["stUp"] is None
    on = chart_filters(Cfg(st_exit=True, st_period=7, st_mult=2.5))
    assert on["st_line"] is True and (on["st_period"], on["st_mult"]) == (7, 2.5)
    assert chart_filters(Cfg())["st_line"] is False


def test_api_saves_the_supertrend_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_supertrend", "st_exit", "st_period", "st_mult"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_supertrend"] is False and first["st_exit"] is False
        assert (first["st_period"], first["st_mult"]) == (10, 3.0)
        ok = client.put("/api/config", json={"use_supertrend": True, "st_exit": True, "st_period": 7, "st_mult": 2.5})
        body = ok.json()
        assert ok.status_code == 200 and body["use_supertrend"] and body["st_exit"] and (body["st_period"], body["st_mult"]) == (7, 2.5)
        assert client.put("/api/config", json={"st_mult": 0}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"st_exit": False})
        assert own.status_code == 200 and own.json()["st_exit"] is False
    database.reset_engine()


def test_stop_on_and_stop_level():
    assert supertrend.stop_on(Cfg(use_stop=True, stop_type="SUPERTREND"))
    assert not supertrend.stop_on(Cfg(use_stop=False, stop_type="SUPERTREND"))
    assert not supertrend.stop_on(Cfg(use_stop=True, stop_type="TSL"))
    cfg = Cfg(st_period=5, st_mult=1.5)
    up = _bars([100 + i * 0.5 for i in range(40)])
    level = supertrend.stop_level(up, "LONG", cfg)
    assert level is not None and level < float(up["close"].iloc[-1])  # under a long in an uptrend
    assert supertrend.stop_level(up, "SHORT", cfg) is None  # a short is not guarded by a rising line
    down = _bars([140 - i * 0.5 for i in range(40)])
    assert supertrend.stop_level(down, "SHORT", cfg) > float(down["close"].iloc[-1])
    assert supertrend.stop_level(up.iloc[:1], "LONG", cfg) is None


@pytest.mark.asyncio
async def test_supertrend_stop_trails_and_closes_the_short_on_the_turn(db):  # noqa: F811
    plain = _first(await _trades(db), "SHORT")
    stopped = _first(await _trades(db, use_stop=True, stop_type="SUPERTREND", st_period=5, st_mult=1.5), "SHORT")
    assert stopped["entry_time"] == plain["entry_time"]
    assert stopped["exit_reason"] == "ST_SL_HIT"
    # The line followed the fall down, so the turn hits it well before the SMAs cross back.
    assert TURN <= stopped["exit_time"] < plain["exit_time"]
    assert stopped["exit_price"] < stopped["entry_price"]
