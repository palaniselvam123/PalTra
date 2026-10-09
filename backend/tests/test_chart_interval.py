"""Chart candle size beyond the bot's own: chart_payload(interval=) draws the 1-minute tape.

A 5-minute bot can show 1- and 3-minute candles (finer than it trades on) and 4-hour
candles. Display only: the bot's own frames, signals and refused crosses are untouched.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from candles import resample
from indicators import enrich

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
SYMBOL = "TCS"


def _tape(days: int = 3, per_day: int = 375) -> pd.DataFrame:
    rows, price, cum = [], 1000.0, 0
    day = dt.date(2026, 10, 5)  # a Monday
    made = 0
    while made < days:
        if day.weekday() < 5:
            start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
            cum = 0
            for i in range(per_day):
                o = price
                price = price + (0.3 if (i // 20) % 2 == 0 else -0.25) + (0.05 if i % 3 == 0 else 0)
                cum += 1000
                rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": o, "high": max(o, price) + 0.1,
                             "low": min(o, price) - 0.1, "close": price, "volume": cum})
            made += 1
        day += dt.timedelta(days=1)
    return pd.DataFrame(rows)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/chart.db")
    import database

    database.reset_engine()
    database.init_db()
    from models import BotConfig
    from strategy_engine import StrategyEngine

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.symbol, row.candle_minutes = SYMBOL, 5
        s.commit()
    e = StrategyEngine()
    cfg = e.load_config()
    tape = _tape()
    e._tapes[SYMBOL] = tape
    e._focus = SYMBOL
    e.candles = enrich(resample(tape, 5), cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
    yield e
    database.reset_engine()


def _minute_of_day(t: int) -> int:
    return ((int(t) + 19_800) % 86_400) // 60


def test_default_is_the_bots_own_candle_and_unchanged(eng):
    out = eng.chart_payload(limit=100000)
    assert out["candle_minutes"] == 5
    assert all((_minute_of_day(c["time"]) - 555) % 5 == 0 for c in out["candles"])


def test_a_finer_size_than_the_bot_is_drawn_from_the_one_minute_tape(eng):
    one = eng.chart_payload(limit=100000, interval=1)
    assert one["candle_minutes"] == 1 and one["blocked"] == []
    native = eng.chart_payload(limit=100000)
    assert len(one["candles"]) > 4 * len(native["candles"])  # about five times as many
    times = [c["time"] for c in one["candles"]]
    assert times == sorted(times) and len(set(times)) == len(times)
    three = eng.chart_payload(limit=100000, interval=3)
    assert three["candle_minutes"] == 3
    assert all((_minute_of_day(c["time"]) - 555) % 3 == 0 for c in three["candles"])
    # SMA 9 / 21 are on the drawn candles (formed after 21 of them), from closed candles only.
    sma = [c["sma9"] for c in three["candles"][:-1] if c["sma9"] is not None]
    assert len(sma) > 0 and three["candles"][-1]["sma9"] is None  # the forming candle's line is blanked


def test_four_hour_candles_start_at_0915_and_1315(eng):
    four = eng.chart_payload(limit=100000, interval=240)
    assert four["candle_minutes"] == 240
    assert {_minute_of_day(c["time"]) for c in four["candles"]} <= {555, 795}
    # Two buckets a day over three sessions.
    assert len(four["candles"]) == 6
    first = four["candles"][0]
    tape = eng._tapes[SYMBOL]
    morning = tape[(tape["ts"] >= first["time"]) & (tape["ts"] < first["time"] + 240 * 60)]
    assert first["open"] == pytest.approx(morning["open"].iloc[0]) and first["close"] == pytest.approx(morning["close"].iloc[-1])
    assert first["high"] == pytest.approx(morning["high"].max()) and first["low"] == pytest.approx(morning["low"].min())


def test_an_unknown_size_or_no_tape_falls_back_to_the_bots_candle(eng):
    assert eng.chart_payload(limit=100000, interval=7)["candle_minutes"] == 5
    assert eng.chart_payload(limit=100000, interval=5)["candle_minutes"] == 5
    eng._tapes.clear()
    assert eng.chart_payload(limit=100000, interval=1)["candle_minutes"] == 5


def test_drawing_another_size_leaves_the_bots_own_state_alone(eng):
    before = eng.candles.copy()
    eng.chart_payload(limit=100000, interval=1)
    eng.chart_payload(limit=100000, interval=240)
    assert eng.candles.equals(before)
    assert eng.chart_payload(limit=100000)["candle_minutes"] == 5


def test_the_endpoints_accept_interval_and_history_offers_four_hours(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import candle_history
    import database

    assert 240 in candle_history.INTERVALS
    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        assert client.get("/api/chart?interval=3").status_code == 200
        assert client.get("/api/research/chart?interval=1").status_code == 200
        assert client.get("/api/bots/2/chart?interval=240").status_code == 200
    database.reset_engine()
