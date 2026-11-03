"""9:45 market regime (market_regime.py). Paper only: synthetic candles, no Groww, no orders."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import market_regime as regime
from groww_client import IST


@pytest.fixture
def regime_file(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKET_REGIME_FILE", str(tmp_path / "market_regime.json"))
    regime.reset_state()
    yield tmp_path
    regime.reset_state()


def _bars(closes: list[float], volume: float = 1_000) -> pd.DataFrame:
    rows = []
    for close in closes:
        rows.append(
            {
                "open": close - 0.4,
                "high": close + 0.2,
                "low": close - 0.5,
                "close": close,
                "volume": volume,
            }
        )
    return pd.DataFrame(rows)


def _ohlc(n: int, wave: bool) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 100 + (1 if i % 2 else 0) if wave else 100 + i * 0.8
        rows.append({"open": close, "high": close + (0.8 if wave else 0.2), "low": close - (0.8 if wave else 0.05), "close": close})
    return pd.DataFrame(rows)


def test_cpr_is_wide_when_the_close_sits_at_the_extreme():
    # Close at the high of a wide day: the pivot sits away from the midpoint.
    width = regime.cpr_width_pct(110, 90, 108)
    assert width is not None and width > regime.CPR_NARROW
    # Close on the midpoint: the two pivots meet, so the range is narrow.
    assert regime.cpr_width_pct(100, 99, 99.5) == pytest.approx(0.0, abs=0.01)
    assert regime.cpr_width_pct(0, 0, 0) is None


def test_choppiness_and_adx_separate_a_trend_from_a_chop():
    trend, chop = _ohlc(80, wave=False), _ohlc(80, wave=True)
    assert regime.choppiness(trend) < regime.CHOP_MAX
    assert regime.choppiness(chop) > regime.CHOP_HARD
    assert regime.adx_value(trend) > regime.adx_value(chop)
    assert regime.choppiness(_ohlc(5, wave=False)) is None


def test_route_and_the_six_checks():
    assert regime.route(6) == ("TREND_DAY", 1.0)
    assert regime.route(5) == ("TREND_DAY", 1.0)
    assert regime.route(4) == ("SELECTIVE_STOCK_SPECIFIC", 0.5)
    assert regime.route(3) == ("SELECTIVE_STOCK_SPECIFIC", 0.5)
    assert regime.route(2) == ("SIDEWAYS_CHOP", 0.0)

    history = pd.concat([_bars([90 + i * 0.4 for i in range(40)]), _bars([108.3, 109, 110, 110.5, 111, 112])], ignore_index=True)
    today = _bars([108.3, 109, 110, 110.5, 111, 112])
    out = regime.evaluate(
        history,
        today,
        {"high": 108, "low": 90, "close": 108},
        advances=40,
        declines=10,
        sector_changes_pct=[0.8, 0.6],
    )
    assert out.breakdown["outside_prev_range_or_narrow_cpr"]
    assert out.breakdown["breadth_aligned"]
    assert out.breakdown["sector_confluence"]
    assert out.breakdown["vwap_separation"]
    assert out.breakdown["moderate_opening_gap"]
    assert out.score == sum(out.breakdown.values())
    assert out.regime == regime.route(out.score)[0]
    assert out.score >= 5 and out.regime == "TREND_DAY" and out.position_size_multiplier == 1.0

    # An empty sector list is not confluence (all([]) would otherwise pass).
    quiet = regime.evaluate(
        history,
        today,
        {"high": 130, "low": 90, "close": 128},
        advances=10,
        declines=10,
        sector_changes_pct=[],
    )
    assert quiet.breakdown["sector_confluence"] is False
    assert "no sector moves" in quiet.notes["sector_confluence"]


def test_a_wide_gap_and_a_split_tape_score_as_sideways():
    flat = _bars([100, 100.2, 99.8, 100.1, 99.9, 100])
    out = regime.evaluate(
        flat,
        flat,
        {"high": 110, "low": 90, "close": 108},
        advances=11,
        declines=10,
        sector_changes_pct=[0.1, -0.8],
    )
    assert out.breakdown["moderate_opening_gap"] is False  # open 99.6 vs close 108
    assert out.breakdown["breadth_aligned"] is False
    assert out.breakdown["sector_confluence"] is False
    assert out.breakdown["outside_prev_range_or_narrow_cpr"] is False
    assert out.regime == "SIDEWAYS_CHOP" and out.position_size_multiplier == 0.0


def test_checkpoints_fire_once_and_a_late_start_takes_the_latest():
    day = dt.datetime(2026, 10, 2, tzinfo=IST)
    assert regime.checkpoint_due(day.replace(hour=9, minute=44), set()) is None
    assert regime.checkpoint_due(day.replace(hour=9, minute=45), set()) == "09:45"
    assert regime.checkpoint_due(day.replace(hour=10, minute=5), {"09:45"}) is None
    assert regime.checkpoint_due(day.replace(hour=11, minute=16), {"09:45"}) == "11:15"
    assert regime.checkpoint_due(day.replace(hour=14, minute=0), set()) == "13:30"
    assert regime.checkpoint_due(day.replace(hour=15, minute=31), set()) is None


def _epoch(day: dt.date, hour: int, minute: int) -> int:
    return int(dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST).timestamp())


def _session(day: dt.date, start: tuple[int, int], minutes: int, price, volume: float) -> pd.DataFrame:
    rows = []
    hour, minute = start
    for i in range(minutes):
        total = hour * 60 + minute + i
        close = price(i)
        rows.append(
            {
                "ts": _epoch(day, total // 60, total % 60),
                "open": close - 0.2,
                "high": close + 0.3,
                "low": close - 0.3,
                "close": close,
                "minute_volume": volume,
            }
        )
    return pd.DataFrame(rows)


def test_basket_reads_breadth_sectors_and_the_gap(regime_file):
    day1, day2 = dt.date(2026, 10, 1), dt.date(2026, 10, 2)
    names = list(regime.BANKS[:3] + regime.IT[:3] + regime.OTHERS[:4])
    frames = {}
    for name in names:
        yesterday = _session(day1, (9, 15), 180, lambda i: 100 + i * 0.05, 1_000)
        # Opens a fraction above yesterday's close, then leaves yesterday's high.
        today = _session(day2, (9, 15), 30, lambda i: 109.15 + i * 0.04, 1_000)
        frames[name] = pd.concat([yesterday, today], ignore_index=True)
    now = dt.datetime(2026, 10, 2, 9, 45, tzinfo=IST)
    output, used = regime.from_frames(frames, now)
    assert used == sorted(names)
    assert output.breakdown["breadth_aligned"]  # every name is up
    assert output.breakdown["sector_confluence"]
    assert output.breakdown["moderate_opening_gap"]
    assert output.breakdown["outside_prev_range_or_narrow_cpr"]
    assert output.score == sum(output.breakdown.values())
    with pytest.raises(ValueError, match="2 names have both"):
        regime.from_frames({names[0]: frames[names[0]], names[1]: frames[names[1]]}, now)


def test_relative_volume_and_the_selective_floor():
    day = dt.date(2026, 10, 6)
    frames = []
    for offset, volume in ((0, 100), (1, 100), (2, 300)):
        # 90 minutes gives the 5-minute ATR enough closed bars.
        frames.append(_session(day + dt.timedelta(days=offset), (9, 15), 90, lambda i: 100 + i * 0.05, volume))
    tape = pd.concat(frames, ignore_index=True)
    now = dt.datetime(2026, 10, 8, 11, 0, tzinfo=IST)
    assert regime.relative_volume(tape, now) == pytest.approx(3.0)
    # One earlier session is not enough.
    assert regime.relative_volume(frames[-1], now) is None
    settings = regime.Settings(on=True, rvol_min=1.5, atr_pct_min=0.05)
    assert regime.selective_reason(tape, now, settings) is None
    tight = regime.Settings(on=True, rvol_min=4, atr_pct_min=0.05)
    assert "relative volume" in (regime.selective_reason(tape, now, tight) or "")
    wide = regime.Settings(on=True, rvol_min=1.5, atr_pct_min=5)
    assert "ATR" in (regime.selective_reason(tape, now, wide) or "")


class _Clock:
    def __init__(self, now, tape=None):
        self.now = now
        self._tapes = {} if tape is None else {"SBIN": tape}

    def _now(self):
        return self.now


def _reading(now, **overrides) -> dict:
    base = {
        "day": now.date().isoformat(),
        "checkpoint": "09:45",
        "error": "",
        "regime": "TREND_DAY",
        "score": 6,
        "max_score": 6,
        "position_size_multiplier": 1.0,
        "chop_value": 40.0,
        "adx_value": 28.0,
        "breakdown": {},
    }
    base.update(overrides)
    return base


def test_the_gate_pauses_trend_entries_and_halves_a_selective_one(regime_file):
    now = dt.datetime(2026, 10, 2, 10, 0, tzinfo=IST)
    engine = _Clock(now)
    regime.save({"on": False})
    regime.publish(_reading(now, regime="SIDEWAYS_CHOP", score=1, position_size_multiplier=0.0))
    assert regime.block_entry(engine, "SBIN", "SMA_CROSS") is None  # switch off

    regime.save({"on": True})
    assert "sideways" in (regime.block_entry(engine, "SBIN", "SMA_CROSS:PULLBACK") or "")
    assert regime.block_entry(engine, "SBIN", "NEWS") is None
    assert regime.block_entry(engine, "SBIN", "FORCE_ORDER") is None
    assert regime.scaled_qty(engine, "NEWS", 8) == 8

    regime.publish(_reading(now, chop_value=70))
    assert "basket chop" in (regime.block_entry(engine, "SBIN", "BREAKOUT_ARRIVAL") or "")

    regime.publish(_reading(now, regime="SELECTIVE_STOCK_SPECIFIC", score=4, position_size_multiplier=0.5, chop_value=45))
    assert "relative volume is not ready" in (regime.block_entry(engine, "SBIN", "JOIN_TREND") or "")
    assert regime.scaled_qty(engine, "SMA_CROSS", 5) == 3  # half of 5, rounded half up
    assert regime.scaled_qty(engine, "SMA_CROSS", 1) == 1  # never a zero-quantity order
    assert regime.scaled_qty(engine, "HANDOFF", 5) == 5

    # A failed later pass keeps the morning reading.
    regime.publish({"day": now.date().isoformat(), "error": "groww down", "checkpoint": "11:15"})
    assert regime.current(now)["regime"] == "SELECTIVE_STOCK_SPECIFIC"


def test_a_missing_reading_does_not_change_size(regime_file):
    now = dt.datetime(2026, 10, 2, 10, 0, tzinfo=IST)
    regime.save({"on": True})
    engine = _Clock(now)
    assert regime.block_entry(engine, "SBIN", "SMA_CROSS") is None
    assert regime.scaled_qty(engine, "SMA_CROSS", 4) == 4
    # Yesterday's reading does not size today.
    regime.publish(_reading(now - dt.timedelta(days=1), regime="SIDEWAYS_CHOP", score=0, position_size_multiplier=0.0))
    assert regime.block_entry(engine, "SBIN", "SMA_CROSS") is None
    assert regime.view(now)["reading"] is None  # yesterday is not shown as today's call


@pytest.mark.asyncio
async def test_apply_locked_refuses_before_the_order(regime_file):
    from strategy_engine import StrategyEngine

    engine = StrategyEngine()
    opened: list[str] = []

    async def fake_open(*_args, **kwargs):
        opened.append(kwargs.get("why"))

    engine._open = fake_open  # type: ignore[method-assign]
    engine.regime_block = regime.block_entry
    engine.regime_qty = regime.scaled_qty
    now = dt.datetime(2026, 10, 2, 10, 0, tzinfo=IST)
    engine._now = lambda: now  # type: ignore[method-assign]
    regime.save({"on": True})
    regime.publish(_reading(now, regime="SIDEWAYS_CHOP", score=1, position_size_multiplier=0.0, chop_value=40))
    cfg = SimpleNamespace(
        symbol="SBIN",
        max_trades_per_day=10,
        max_trades_per_stock=0,
        entry_cutoff_time="15:00",
        square_off_time="15:15",
    )
    text = await engine._apply_locked(
        signal="BULLISH", cross_price=100, atr=1, adx_blocks_entry=False, cfg=cfg, now=now, why="SMA_CROSS"
    )
    assert opened == []
    assert text.startswith("BULLISH ignored — sideways")

    news = await engine._apply_locked(
        signal="BULLISH", cross_price=100, atr=1, adx_blocks_entry=False, cfg=cfg, now=now, why="NEWS"
    )
    assert news == "opened LONG" and opened == ["NEWS"]


def test_settings_round_trip_and_the_alert_line(regime_file):
    saved = regime.save({"on": True, "rvol_min": 99, "atr_pct_min": 0.01})
    assert saved.on and saved.rvol_min == 10 and saved.atr_pct_min == 0.05
    again = regime.save({"rvol_min": 2})
    assert again.on and again.rvol_min == 2  # the switch stays on
    now = dt.datetime(2026, 10, 2, 9, 46, tzinfo=IST)
    regime.publish(_reading(now))
    text = regime.alert_text(regime.current(now))
    assert "Trend Day" in text and "full size" in text and "not the official Nifty" in text
    view = regime.view(now)
    assert view["on"] and view["applies"] and view["rvol_min"] == 2


@pytest.mark.asyncio
async def test_a_failed_download_names_the_reason():
    async def fetch(symbol, start, end):  # noqa: ARG001
        raise RuntimeError("no Groww login")

    now = dt.datetime(2026, 10, 2, 9, 45, tzinfo=IST)
    reading = await regime.collect(fetch, now, "09:45", symbols=("HDFCBANK", "TCS"), pause=0)
    assert "no Groww login" in reading["error"]
    assert "0 names have both" in reading["error"]
    assert "regime" not in reading


def test_api_stays_off_and_a_live_bot_must_be_confirmed(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("MARKET_REGIME_FILE", str(tmp_path / "market_regime.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    monkeypatch.setattr(regime, "market_is_open", lambda now=None: False)
    regime.reset_state()
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    from main import app, engine
    from models import BotConfig

    with TestClient(app) as client:
        got = client.get("/api/market-regime").json()
        assert got["on"] is False and got["applies"] is False and got["reading"] is None
        saved = client.put("/api/market-regime", json={"on": True, "rvol_min": 2, "atr_pct_min": 0.4}).json()
        assert saved["on"] is True and saved["rvol_min"] == 2 and saved["atr_pct_min"] == 0.4
        with database.session_factory()() as db:
            row = db.get(BotConfig, 1)
            row.trading_mode = "LIVE"
            db.commit()
        refused = client.put("/api/market-regime", json={"on": True, "rvol_min": 2.5, "atr_pct_min": 0.4})
        assert refused.status_code == 409 and "LIVE" in refused.json()["detail"]
        assert client.get("/api/market-regime").json()["rvol_min"] == 2  # the refused save did not land
        confirmed = client.put(
            "/api/market-regime",
            json={"on": True, "rvol_min": 2.5, "atr_pct_min": 0.4, "confirm_live": True},
        )
        assert confirmed.status_code == 200 and confirmed.json()["rvol_min"] == 2.5
        # Turning it off needs no second confirmation.
        assert client.put("/api/market-regime", json={"on": False, "rvol_min": 2.5, "atr_pct_min": 0.4}).status_code == 200
        with database.session_factory()() as db:
            row = db.get(BotConfig, 1)
            row.trading_mode = "PAPER"
            db.commit()
        engine.load_config()
    database.reset_engine()
    regime.reset_state()
