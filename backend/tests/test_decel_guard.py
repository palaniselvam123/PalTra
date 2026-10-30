"""SMA 9 deceleration guard (decel_guard.py): exit on the bend of the fast SMA, slope in ATR. PAPER only."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import decel_guard
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)
from tests.test_slope_exit import DAY, _peak_frame


def _cfg(**over):
    return type("C", (), over)()


def _bars(closes: list[float]) -> pd.DataFrame:
    c = np.array(closes, dtype=float)
    o = np.r_[c[0], c[:-1]]
    return pd.DataFrame({"ts": np.arange(len(c)) * 300, "open": o, "high": np.maximum(o, c) + 0.2,
                         "low": np.minimum(o, c) - 0.2, "close": c})


# A steady climb, then the climb slows and rolls over: the bend comes before the line falls.
CLIMB = [100 + 0.5 * i for i in range(40)]
ROLL = CLIMB + [CLIMB[-1] + x for x in (0.3, 0.4, 0.3, 0.0, -0.5, -1.2, -2.0, -2.8, -3.6, -4.4)]


def test_slope_is_in_atr_so_price_level_does_not_matter():
    cheap = decel_guard.slopes(_bars(CLIMB))
    dear = decel_guard.slopes(_bars([x * 40 for x in CLIMB]))
    # 40x the price, with candles 40x as tall, but the same 0.2 wicks: compare the shape, not the level.
    assert cheap["slope_atr"].iloc[-1] > 0 and dear["slope_atr"].iloc[-1] > 0
    same = decel_guard.slopes(_bars([x * 40 for x in CLIMB]).assign(
        high=lambda d: d[["open", "close"]].max(axis=1) + 8, low=lambda d: d[["open", "close"]].min(axis=1) - 8))
    assert same["slope_atr"].iloc[-1] == pytest.approx(cheap["slope_atr"].iloc[-1], rel=1e-6)


def test_warning_comes_before_the_slope_turns_negative():
    out = decel_guard.early_warning(_bars(ROLL), "LONG", 30, _cfg(decel_confirm="OFF"))
    first = int(np.argmax(out["early_warning_exit"].to_numpy()))
    assert out["early_warning_exit"].any()
    turned = int(np.argmax((out["slope_atr"] < 0).to_numpy()))
    assert first < turned  # the bend is flagged while the SMA 9 is still rising
    assert out["signed_slope"].iloc[first] > 0
    # A short on the same tape is never warned of a bend in its favour.
    assert not decel_guard.early_warning(_bars(ROLL), "SHORT", 30, _cfg(decel_confirm="OFF"))["early_warning_exit"].any()


def test_no_look_ahead():
    full = decel_guard.early_warning(_bars(ROLL), "LONG", 30, _cfg())["early_warning_exit"]
    for n in range(32, len(ROLL) + 1):
        part = decel_guard.early_warning(_bars(ROLL[:n]), "LONG", 30, _cfg())["early_warning_exit"]
        assert part.iloc[-1] == full.iloc[n - 1]  # adding later candles never changes an earlier flag


def test_steady_trend_and_unarmed_drift_never_warn():
    assert not decel_guard.early_warning(_bars(CLIMB), "LONG", 25, _cfg(decel_confirm="OFF"))["early_warning_exit"].any()
    drift = [100 + 0.01 * i for i in range(40)] + [100.4, 100.41, 100.39, 100.35]
    assert not decel_guard.early_warning(_bars(drift), "LONG", 25, _cfg(decel_confirm="OFF"))["early_warning_exit"].any()


def test_close_confirmation_waits_for_the_close_through_the_sma():
    off = decel_guard.early_warning(_bars(ROLL), "LONG", 30, _cfg(decel_confirm="OFF"))["early_warning_exit"]
    close = decel_guard.early_warning(_bars(ROLL), "LONG", 30, _cfg(decel_confirm="CLOSE"))
    first_off = int(np.argmax(off.to_numpy()))
    first_close = int(np.argmax(close["early_warning_exit"].to_numpy()))
    assert first_close >= first_off
    assert close["close"].iloc[first_close] < close["sma9"].iloc[first_close] if "close" in close else True


async def _long(database, frame, **over):
    from sma_research.replayer import replay_symbol

    trades = await replay_symbol(frame, "SIMV", [DAY], _settings(database, candle_minutes=5, **over))
    return next(t for t in trades if t["direction"] == "LONG" and t["entry_time"] >= dt.datetime(2026, 9, 22, 10, 0))


@pytest.mark.asyncio
async def test_guard_exits_before_the_vturn_and_the_cross(db):  # noqa: F811
    # 5-minute bot, the V's top at 10:55: the guard sells earlier and higher than the V-turn and the cross.
    cross = await _long(db, _peak_frame(1))
    vturn = await _long(db, _peak_frame(1), vturn_exit=True)
    guard = await _long(db, _peak_frame(1), decel_exit=True)
    assert guard["exit_reason"] == decel_guard.EXIT_REASON
    assert guard["entry_time"] == vturn["entry_time"] == cross["entry_time"]
    assert guard["exit_time"] < vturn["exit_time"] < cross["exit_time"]
    assert guard["exit_price"] > vturn["exit_price"] > cross["exit_price"]


@pytest.mark.asyncio
async def test_guard_reads_a_rounded_top_earlier_than_the_vturn(db):  # noqa: F811
    vturn = await _long(db, _peak_frame(20), vturn_exit=True)
    guard = await _long(db, _peak_frame(20), decel_exit=True)
    assert guard["exit_reason"] == decel_guard.EXIT_REASON
    assert guard["exit_time"] < vturn["exit_time"] and guard["exit_price"] > vturn["exit_price"]


def test_api_saves_the_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"decel_exit", "decel_arm_atr", "decel_drop_pct", "decel_confirm"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["decel_exit"] is False and got["decel_drop_pct"] == 50 and got["decel_confirm"] == "CLOSE"
        ok = client.put("/api/config", json={"decel_exit": True, "decel_drop_pct": 40, "decel_confirm": "OFF"})
        assert ok.status_code == 200 and ok.json()["decel_exit"] is True and ok.json()["decel_drop_pct"] == 40
        assert client.put("/api/config", json={"decel_confirm": "WICK"}).status_code == 422
    database.reset_engine()


def test_peak_from_the_run_warns_a_late_entry_on_its_first_candle():
    # Bought while the climb is already slowing: counted from the entry there is no peak to fall from yet.
    def first(entry, **over):
        flags = decel_guard.early_warning(_bars(ROLL), "LONG", entry, _cfg(decel_confirm="OFF", **over))["early_warning_exit"]
        assert not flags.iloc[:entry].any()  # never flags a candle before the entry
        return int(np.argmax(flags.to_numpy())) if flags.any() else None

    assert first(44, decel_peak_from="RUN") == 44 and first(44) == 45  # one candle sooner
    assert first(46, decel_peak_from="RUN") == 46 and first(46) is None  # from the entry it never warns at all
    assert first(30, decel_peak_from="RUN") == first(30)  # an early entry: the same as before


def test_run_start_stays_in_the_entry_session():
    df = _bars(ROLL)
    signed = decel_guard.slopes(df)["slope_atr"]
    assert decel_guard.run_start(df, signed, 41) < 41  # the climb started well before the entry
    day = df.assign(ts=df["ts"] + np.where(np.arange(len(df)) >= 38, 86400, 0))  # rows from 38 on are the next day
    assert decel_guard.run_start(day, signed, 41) == 38
    assert decel_guard.settings(_cfg()).get("peak_from") == "ENTRY"
    assert decel_guard.settings(_cfg(decel_peak_from="nonsense"))["peak_from"] == "ENTRY"


def test_api_saves_where_the_peak_is_measured_from(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "decel_peak_from" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["decel_peak_from"] == "ENTRY"
        ok = client.put("/api/config", json={"decel_peak_from": "RUN"})
        assert ok.status_code == 200 and ok.json()["decel_peak_from"] == "RUN"
        assert client.put("/api/config", json={"decel_peak_from": "DAY"}).status_code == 422
    database.reset_engine()
