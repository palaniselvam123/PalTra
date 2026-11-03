"""Cumulative delta check and divergence exit (cum_delta.py).

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import cum_delta
from strategy_engine import _entry_block, _short_block_label
from tests.test_counter_candles import DAY, _frame
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
OPEN = int(dt.datetime(2026, 9, 22, 9, 15, tzinfo=IST).timestamp())


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _rows(bars, start=OPEN):
    """bars: (open, high, low, close, shares) per candle; minute_volume carries the shares."""
    return pd.DataFrame(
        [
            {"ts": start + i * 60, "open": o, "high": h, "low": lo, "close": c, "minute_volume": v}
            for i, (o, h, lo, c, v) in enumerate(bars)
        ]
    )


def test_delta_is_volume_times_body_share_and_resets_each_session():
    bars = [(100, 102, 99, 102, 1000), (102, 103, 101, 101, 600), (101, 101, 101, 101, 500)]
    frame = _rows(bars)
    d = cum_delta.deltas(frame)
    assert d.tolist() == pytest.approx([1000 * 2 / 3, -600 * 1 / 2, 0.0])  # a candle with no range adds 0
    # A second day starts again from zero.
    two = pd.concat([frame, _rows(bars, start=OPEN + 86_400)], ignore_index=True)
    cvd = cum_delta.series(two)
    assert cvd.iloc[2] == pytest.approx(1000 * 2 / 3 - 300)
    assert cvd.iloc[3] == pytest.approx(1000 * 2 / 3)
    # Missing volume adds nothing (never a made-up number).
    frame.loc[1, "minute_volume"] = float("nan")
    assert cum_delta.series(frame).iloc[-1] == pytest.approx(1000 * 2 / 3)


def test_entry_needs_the_delta_moving_the_trades_way():
    cfg = Cfg(use_cvd=True, cvd_lookback=3)
    buying = [(100 + i, 101 + i, 99.5 + i, 101 + i, 1000) for i in range(6)] + [(106, 107, 105, 106.5, 0)]
    frame = _rows(buying)  # last row = forming candle, never read
    assert cum_delta.entry_reason(frame, "LONG", cfg) is None
    assert "has not fallen" in cum_delta.entry_reason(frame, "SHORT", cfg)
    assert "rose" in cum_delta.passed_text(frame, "LONG", cfg)
    selling = [(110 - i, 110.5 - i, 108 - i, 109 - i, 1000) for i in range(6)] + [(104, 105, 103, 104, 0)]
    frame = _rows(selling)
    assert cum_delta.entry_reason(frame, "SHORT", cfg) is None
    assert "has not risen" in cum_delta.entry_reason(frame, "LONG", cfg)
    # Fewer candles than the lookback today: read since the open (0).
    assert cum_delta.entry_reason(_rows(buying[:2]), "LONG", cfg) is None
    # Wired through the bot's entry checks, with a short chart label.
    why = _entry_block(_rows(selling), "LONG", cfg, only="cvd")
    assert why and _short_block_label(why) == "CVD"
    assert _entry_block(_rows(selling), "LONG", Cfg(use_cvd=False), only="cvd") is None


def test_bearish_divergence_new_high_without_buyers():
    cfg = Cfg(cvd_div_lookback=5)
    bars = [
        (100, 102, 99.8, 102, 5000),   # strong buying to the first high (102)
        (102, 102, 101, 101.2, 800),
        (101.2, 101.6, 100.8, 101.0, 800),
        (101.0, 101.4, 100.6, 100.8, 3000),  # sellers
        (100.8, 102.4, 100.6, 101.0, 4000),  # new high 102.4 but closes weak: delta falls further
    ]
    closed = _rows(bars)
    hit, note = cum_delta.divergence(closed, "LONG", cfg)
    assert hit and "new high 102.40" in note
    assert cum_delta.divergence(closed, "SHORT", cfg)[0] is False
    # The same new high on real buying is no divergence.
    strong = _rows(bars[:4] + [(100.8, 102.4, 100.7, 102.4, 9000)])
    assert cum_delta.divergence(strong, "LONG", cfg)[0] is False


def test_bullish_divergence_new_low_without_sellers():
    cfg = Cfg(cvd_div_lookback=5)
    bars = [
        (100, 100.2, 98, 98, 5000),
        (98, 99, 98, 98.8, 800),
        (98.8, 99.2, 98.4, 99.0, 800),
        (99.0, 99.4, 98.6, 99.2, 3000),
        (99.2, 99.4, 97.6, 99.0, 4000),  # new low 97.6, but buyers took it back
    ]
    hit, note = cum_delta.divergence(_rows(bars), "SHORT", cfg)
    assert hit and "new low 97.60" in note


def test_settings_are_clamped():
    assert cum_delta.lookback(Cfg()) == 5 and cum_delta.div_lookback(Cfg()) == 10
    assert cum_delta.lookback(Cfg(cvd_lookback=999)) == cum_delta.MAX_LOOKBACK
    assert cum_delta.div_lookback(Cfg(cvd_div_lookback=1)) == cum_delta.MIN_DIV_LOOKBACK


@pytest.mark.asyncio
async def test_cvd_settings_run_in_a_replay(db):  # noqa: F811
    from sma_research.replayer import replay_symbol

    async def trades(**over):
        return await replay_symbol(_frame(), "SIMV", [DAY], _settings(db, **over))

    plain = await trades()
    # The entry check never adds a trade; it can only refuse some. The exit never opens one.
    assert len(await trades(use_cvd=True)) <= len(plain)
    exited = await trades(cvd_div_exit=True, cvd_div_lookback=5)
    assert len(exited) >= 1
    assert all(t["exit_reason"] for t in exited)


def test_api_saves_the_cvd_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"use_cvd", "cvd_lookback", "cvd_div_exit", "cvd_div_lookback"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["use_cvd"] is False and first["cvd_div_exit"] is False
        assert (first["cvd_lookback"], first["cvd_div_lookback"]) == (5, 10)
        ok = client.put("/api/config", json={"use_cvd": True, "cvd_lookback": 8, "cvd_div_exit": True, "cvd_div_lookback": 20})
        body = ok.json()
        assert ok.status_code == 200 and body["use_cvd"] and body["cvd_div_exit"]
        assert (body["cvd_lookback"], body["cvd_div_lookback"]) == (8, 20)
        assert client.put("/api/config", json={"cvd_div_lookback": 1}).status_code == 422
        own = client.put("/api/config/stock/SBIN", json={"use_cvd": False})
        assert own.status_code == 200 and own.json()["use_cvd"] is False
    database.reset_engine()
