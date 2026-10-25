"""Entry-candle colour check (entry_candle.py): the order waits until its own candle moves the trade's way.

PAPER only: the replay engine fills locally (no Groww order path). The replays use 5-minute candles,
so the minute steps of the replayer fall inside a forming candle.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import entry_candle
from strategy_engine import IST
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)
from tests.test_extreme_entry import DAY, JUMP, _frame as _jump_frame
from tests.test_join_trend import _day


def _fall() -> pd.DataFrame:
    """Flat, then a steady fall from 10:15: a bearish cross whose candles stay red."""

    def path(i, p):
        if i < 60:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        return p - 0.15

    flat = _day(dt.date(2026, 9, 21), lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02), 1000.0)
    return pd.DataFrame(flat + _day(DAY, path, 1000.0))


async def _replay(database, frame, symbol="SIMC", **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(frame, symbol, [DAY], _settings(database, candle_minutes=5, **over))


def _candle_open(frame: pd.DataFrame, at: dt.datetime) -> float:
    """Open of the 5-minute candle (from 09:15) a trade was entered in; `frame` holds 1-minute rows."""
    since = (at.hour * 60 + at.minute) - (9 * 60 + 15)
    start = at.replace(hour=9, minute=15, second=0, microsecond=0, tzinfo=IST) + dt.timedelta(minutes=since - since % 5)
    return float(frame.loc[frame["ts"].astype("int64") == int(start.timestamp()), "open"].iloc[0])


@pytest.mark.asyncio
async def test_the_sell_waits_until_its_candle_is_red(db):  # noqa: F811
    frame = _fall()
    plain = [t for t in await _replay(db, frame) if t["direction"] == "SHORT" and t["entry_time"] >= JUMP]
    waited = [t for t in await _replay(db, frame, entry_candle_confirm=True, entry_candle_pct=0.01)
              if t["direction"] == "SHORT" and t["entry_time"] >= JUMP]
    assert plain and waited
    first = waited[0]
    assert first["entry_reason"].endswith(":CANDLE_CONFIRM")
    assert first["entry_time"] > plain[0]["entry_time"]  # not at the candle's open
    # Filled only once the candle traded below its open by the margin: a red candle so far.
    assert first["entry_price"] <= _candle_open(frame, first["entry_time"]) * (1 - 0.0001) + 0.05


@pytest.mark.asyncio
async def test_no_order_while_the_candle_goes_against_the_buy(db):  # noqa: F811
    # The tall 10:15 cross candle is followed by a straight fall: every candle after it is red.
    frame = _jump_frame(lambda i, p: p - 0.2)
    plain = [t for t in await _replay(db, frame, symbol="SIMX") if t["direction"] == "LONG" and t["entry_time"] >= JUMP]
    waited = [t for t in await _replay(db, frame, symbol="SIMX", entry_candle_confirm=True, entry_candle_pct=0.01)
              if t["direction"] == "LONG" and t["entry_time"] >= JUMP]
    assert plain  # plain mode buys after the cross
    assert not waited  # every candle after it fell: no green candle, so no buy


@pytest.mark.asyncio
async def test_off_by_default_changes_nothing(db):  # noqa: F811
    frame = _fall()
    plain = await _replay(db, frame)
    same = await _replay(db, frame, entry_candle_confirm=False)
    key = lambda t: (t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"])  # noqa: E731
    assert [key(t) for t in plain] == [key(t) for t in same]


def test_level_and_confirmation():
    assert entry_candle.level(100.0, "LONG", 0.05) == pytest.approx(100.05)
    assert entry_candle.level(100.0, "SHORT", 0.05) == pytest.approx(99.95)
    assert entry_candle.confirmed("LONG", 100.05, 100.05) and not entry_candle.confirmed("LONG", 100.04, 100.05)
    assert entry_candle.confirmed("SHORT", 99.9, 99.95) and not entry_candle.confirmed("SHORT", 99.96, 99.95)
    assert not entry_candle.confirmed("LONG", None, 100.0) and not entry_candle.confirmed("LONG", "x", 100.0)

    class C:
        pass

    assert not entry_candle.on(C) and entry_candle.margin_pct(C) == entry_candle.PCT_DEFAULT
    assert entry_candle.max_candles(C) == entry_candle.CANDLES_DEFAULT
    C.entry_candle_pct, C.entry_candle_max = 99, 99
    assert entry_candle.margin_pct(C) == entry_candle.MAX_PCT and entry_candle.max_candles(C) == entry_candle.MAX_CANDLES


def test_api_saves_the_entry_candle_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"entry_candle_confirm", "entry_candle_pct", "entry_candle_max"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["entry_candle_confirm"] is False and first["entry_candle_pct"] == 0.05 and first["entry_candle_max"] == 2
        ok = client.put("/api/config", json={"entry_candle_confirm": True, "entry_candle_pct": 0.1, "entry_candle_max": 3})
        assert ok.status_code == 200 and ok.json()["entry_candle_confirm"] is True and ok.json()["entry_candle_max"] == 3
        assert client.put("/api/config", json={"entry_candle_pct": 2}).status_code == 422
        assert client.put("/api/config", json={"entry_candle_max": 0}).status_code == 422
    database.reset_engine()
