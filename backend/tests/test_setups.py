"""Technical setups as entries (setups.py) and the two loss guards (loss_guard.py)."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace as NS

import pandas as pd
import pytest

import loss_guard
import setups
from groww_client import IST
from indicators import enrich
from tests.test_gap_mode import db  # noqa: F401

DAY = dt.date(2026, 9, 29)
PREV = dt.date(2026, 9, 28)


def _bars(day: dt.date, start: dt.time, ohlc: list[tuple[float, float, float, float]], vols: list[float] | None = None,
          minutes: int = 5, base_volume: float = 0.0) -> list[dict]:
    """Candles of `minutes` from `start`; `vols` = shares traded in each (Groww's volume is the running total)."""
    t0 = dt.datetime.combine(day, start, tzinfo=IST)
    rows, total = [], base_volume
    for i, (o, h, l, c) in enumerate(ohlc):
        total += (vols[i] if vols else 1000.0)
        rows.append({"ts": int((t0 + dt.timedelta(minutes=minutes * i)).timestamp()), "open": o, "high": h, "low": l,
                     "close": c, "volume": total})
    return rows


def _flat(n: int, price: float = 100.0, wiggle: float = 0.2) -> list[tuple[float, float, float, float]]:
    return [(price, price + wiggle, price - wiggle, price) for _ in range(n)]


def _frame(today: list[tuple[float, float, float, float]], vols: list[float] | None = None, yesterday: int = 40) -> pd.DataFrame:
    """Yesterday's quiet candles, today's candles from 09:15, and a forming candle last (never read); enriched."""
    rows = _bars(PREV, dt.time(11, 0), _flat(yesterday))
    rows += _bars(DAY, dt.time(9, 15), [*today, today[-1]], [*(vols or [1000.0] * len(today)), 10.0])
    return enrich(pd.DataFrame(rows), 3, 6, 14)


def _cfg(**over):
    base = dict(setup_entry="ORB", setup_only=False, setup_side="BOTH", setup_trend=False, setup_vol_mult=0.0,
                setup_orb_minutes=15, setup_break_candles=20, setup_squeeze_candles=30, bb_period=20, bb_std=2.0,
                sma_fast=3, sma_slow=6)
    return NS(**{**base, **over})


def test_settings_are_cleaned_and_bounded():
    assert setups.clean("inside, orb;nonsense,ORB") == "ORB,INSIDE"
    assert setups.picked(NS(setup_entry="SQUEEZE,ORB")) == ("ORB", "SQUEEZE") and not setups.enabled(NS())
    assert setups.orb_minutes(NS(setup_orb_minutes=999)) == setups.ORB_MAX and setups.break_candles(NS(setup_break_candles="x")) == 20
    assert setups.vol_mult(NS(setup_vol_mult=0)) == 0 and setups.vol_mult(NS()) == 1.5 and setups.with_trend(NS()) is True
    assert setups.side_rule(NS(setup_side="up")) == "BOTH"
    assert setups.longest(NS(setup_entry="")) == 0 and setups.longest(_cfg(setup_entry="SQUEEZE")) == 52
    assert set(setups.ENTRY_REASONS) == {"SETUP_ORB", "SETUP_VWAP_PULL", "SETUP_SQUEEZE", "SETUP_BREAKOUT", "SETUP_INSIDE"}


def test_opening_range_breakout_fires_on_the_first_close_beyond_the_range():
    opening = [(100, 101, 99.5, 100.5), (100.5, 101.5, 100, 101), (101, 101.4, 100.2, 100.8)]  # 09:15-09:30: 99.5-101.5
    inside = [(100.8, 101.2, 100.4, 101.0)]
    side, key, event, note = setups.call(_frame(opening + inside), _cfg())
    assert side is None and "no fresh close" in note
    up = inside + [(101.0, 102.2, 100.9, 102.0)]
    side, key, event, note = setups.call(_frame(opening + up), _cfg())
    assert (side, key) == ("LONG", "ORB") and "above the first 15 min high 101.50" in note and event == f"ORB:LONG:{DAY}"
    # The next candle is still above: not a fresh breakout any more.
    assert setups.call(_frame(opening + up + [(102.0, 102.6, 101.8, 102.4)]), _cfg())[0] is None
    down = inside + [(101.0, 101.1, 99.0, 99.2)]
    assert setups.call(_frame(opening + down), _cfg())[0] == "SHORT"
    # Inside the window itself nothing is read.
    assert "not complete" in setups.call(_frame(opening[:2]), _cfg())[3]
    # Only buys kept.
    assert "only buys" in setups.call(_frame(opening + down), _cfg(setup_side="LONG"))[3]
    # A 30-minute range takes the first six 5-minute candles.
    assert "not complete" in setups.call(_frame(opening + up), _cfg(setup_orb_minutes=30))[3]


def test_volume_and_trend_confirm_a_breakout():
    opening = [(100, 101, 99.5, 100.5), (100.5, 101.5, 100, 101), (101, 101.4, 100.2, 100.8)]
    up = [(100.8, 102.2, 100.7, 102.0)]
    quiet = _frame(opening + up, vols=[1000.0] * 4)
    side, _, _, note = setups.call(quiet, _cfg(setup_vol_mult=1.5))
    assert side is None and "volume 1.0x the usual (needs 1.5x)" in note
    busy = _frame(opening + up, vols=[1000.0, 1000.0, 1000.0, 3000.0])
    assert setups.call(busy, _cfg(setup_vol_mult=1.5))[0] == "LONG"
    # With the day's trend: a buy needs the close above today's VWAP (it is, here); a sell under it.
    assert setups.call(busy, _cfg(setup_trend=True))[0] == "LONG"
    down = [(100.8, 100.9, 99.0, 99.2)]
    assert setups.call(_frame(opening + down), _cfg(setup_trend=True))[0] == "SHORT"
    assert setups.trend_refusal(busy.iloc[:-1], "SHORT", _cfg(setup_trend=True)).startswith("price")
    assert setups.trend_refusal(busy.iloc[:-1], "SHORT", _cfg(setup_trend=False)) is None


def test_vwap_pullback_buys_the_dip_in_an_uptrend():
    # A steady rise from the open (closes above the VWAP), then a candle that dips to the VWAP and closes green.
    rise = [(100 + i, 101.2 + i, 99.8 + i, 101 + i) for i in range(7)]  # closes 101..107
    frame = _frame(rise)
    vwap = setups.session_vwap_series(frame.iloc[:-1]).iloc[-1]
    dip = (107.0, 108.2, vwap + 0.05, 108.0)
    side, key, _, note = setups.call(_frame(rise + [dip]), _cfg(setup_entry="VWAP_PULL"))
    assert (side, key) == ("LONG", "VWAP_PULL") and "dipped to the VWAP" in note
    # No dip to the VWAP: no call. A red close: no call.
    assert setups.call(_frame(rise + [(107.0, 108.2, 106.9, 108.0)]), _cfg(setup_entry="VWAP_PULL"))[0] is None
    assert setups.call(_frame(rise + [(107.5, 108.2, vwap + 0.05, 107.2)]), _cfg(setup_entry="VWAP_PULL"))[0] is None
    # The mirror: a steady fall, a rise back to the VWAP and a red close sells.
    fall = [(200 - i, 200.2 - i, 198.8 - i, 199 - i) for i in range(7)]
    vwap = setups.session_vwap_series(_frame(fall).iloc[:-1]).iloc[-1]
    rally = (193.0, vwap - 0.05, 191.8, 192.0)
    assert setups.call(_frame(fall + [rally]), _cfg(setup_entry="VWAP_PULL"))[0] == "SHORT"
    # The volume check never applies to a pullback.
    assert setups.call(_frame(rise + [dip]), _cfg(setup_entry="VWAP_PULL", setup_vol_mult=9.0))[0] == "LONG"


def test_squeeze_breakout_needs_narrow_bands_first():
    wide = [(100, 102, 98, 100 + (1.5 if i % 2 else -1.5)) for i in range(30)]
    tight = [(100, 100.1, 99.9, 100 + (0.02 if i % 2 else -0.02)) for i in range(26)]
    burst = (100.0, 101.3, 99.95, 101.2)
    cfg = _cfg(setup_entry="SQUEEZE")
    side, key, _, note = setups.call(_frame(wide + tight + [burst]), cfg)
    assert (side, key) == ("LONG", "SQUEEZE") and "after a squeeze" in note
    assert setups.call(_frame(wide + tight + [(100.0, 100.05, 98.7, 98.8)]), cfg)[0] == "SHORT"
    # Still inside the bands: nothing. Wide bands before a big candle: no squeeze.
    assert "no close outside" in setups.call(_frame(wide + tight), cfg)[3]
    widening = [(100, 100.1 + 0.2 * i, 99.9 - 0.2 * i, 100 + (0.15 * i if i % 2 else -0.15 * i)) for i in range(40)]
    assert "no squeeze" in setups.call(_frame(wide + widening + [(100.0, 112.0, 99.0, 111.0)]), cfg)[3]
    assert "too few" in setups.call(_frame(tight[:5], yesterday=10), cfg)[3]


def test_high_low_breakout_is_the_first_close_beyond_n_candles():
    base = _flat(25, 100.0, 0.5)  # highs 100.5, lows 99.5
    cfg = _cfg(setup_entry="BREAKOUT", setup_break_candles=20)
    assert setups.call(_frame(base + [(100.0, 100.4, 99.8, 100.3)]), cfg)[0] is None
    side, key, _, note = setups.call(_frame(base + [(100.0, 101.2, 99.9, 101.0)]), cfg)
    assert (side, key) == ("LONG", "BREAKOUT") and "above the 20-candle high 100.50" in note
    # The candle after: the level now includes the breakout candle's high (101.2), so no second call at 101.1.
    assert setups.call(_frame(base + [(100.0, 101.2, 99.9, 101.0), (101.0, 101.15, 100.8, 101.1)]), cfg)[0] is None
    assert setups.call(_frame(base + [(100.0, 100.1, 98.8, 99.0)]), cfg)[0] == "SHORT"


def test_inside_bar_breakout_needs_a_real_mother_candle():
    base = _flat(20, 100.0, 0.5)
    mother, inner = (100.0, 102.0, 99.0, 101.5), (101.2, 101.8, 100.5, 101.0)
    cfg = _cfg(setup_entry="INSIDE")
    side, key, _, note = setups.call(_frame(base + [mother, inner, (101.0, 102.6, 100.9, 102.4)]), cfg)
    assert (side, key) == ("LONG", "INSIDE") and "mother candle's high 102.00" in note
    assert setups.call(_frame(base + [mother, inner, (101.0, 101.1, 98.6, 98.8)]), cfg)[0] == "SHORT"
    assert "no close beyond" in setups.call(_frame(base + [mother, inner, (101.0, 101.9, 100.2, 101.5)]), cfg)[3]
    # The candle before is not inside its mother: nothing.
    assert "no inside bar" in setups.call(_frame(base + [mother, (101.2, 102.5, 100.5, 102.2), (102.2, 103.0, 102.0, 102.9)]), cfg)[3]
    # A tiny mother candle (under 0.75 ATR) does not matter.
    tiny, tinier = (100.0, 100.2, 99.9, 100.1), (100.05, 100.15, 99.95, 100.1)
    assert "too small" in setups.call(_frame(base + [tiny, tinier, (100.1, 100.6, 100.0, 100.5)]), cfg)[3]


def test_two_setups_that_disagree_give_no_order_and_agreeing_ones_are_named():
    base = _flat(22, 100.0, 0.5)
    mother, inner = (100.0, 102.0, 99.0, 101.5), (101.2, 101.8, 100.5, 101.0)
    both = _cfg(setup_entry="BREAKOUT,INSIDE")
    side, key, _, note = setups.call(_frame(base + [mother, inner, (101.0, 102.6, 100.9, 102.4)]), both)
    assert side == "LONG" and key == "BREAKOUT" and "also inside-bar breakout" in note
    # An inside-bar buy while the opening range breaks down cannot happen on one close, so force a disagreement.
    real = setups._READERS["INSIDE"]
    setups._READERS["INSIDE"] = lambda closed, cfg: ("SHORT", "INSIDE:x", "made up")
    try:
        side, _, _, note = setups.call(_frame(base + [mother, inner, (101.0, 102.6, 100.9, 102.4)]), both)
    finally:
        setups._READERS["INSIDE"] = real
    assert side is None and "setups disagree" in note


def test_only_the_setups_open_trades():
    assert setups.blocks(_cfg(), "SMA_CROSS") is None  # the switch is off
    only = _cfg(setup_only=True)
    assert setups.blocks(only, "SMA_CROSS") and setups.blocks(only, None) and setups.blocks(only, "STACK_WIDEN")
    for passes in ("SETUP_ORB", "SETUP_INSIDE:PULLBACK", "FORCE_ORDER", "NEWS", "HANDOFF"):
        assert setups.blocks(only, passes) is None
    assert setups.blocks(_cfg(setup_entry="", setup_only=True), "SMA_CROSS") is None


# ------------------------------------------------------------------ loss guards
def test_time_stop_closes_only_a_trade_that_went_nowhere():
    cfg = NS(time_stop_candles=3, time_stop_atr=0.5)
    assert loss_guard.time_verdict(cfg, 2, 0.0, 2.0) == (False, "")  # too early
    hit, note = loss_guard.time_verdict(cfg, 3, 0.4, 2.0)
    assert hit and "3 candle(s) since the entry" in note and "under 0.5 ATR (1.00)" in note
    assert loss_guard.time_verdict(cfg, 5, 1.0, 2.0)[0] is False  # it once ran 0.5 ATR: left to the other exits
    assert loss_guard.time_verdict(cfg, 5, 0.0, None)[0] is False  # no ATR: never guessed
    assert loss_guard.time_verdict(NS(time_stop_candles=0), 99, 0.0, 2.0)[0] is False
    assert loss_guard.best_move("LONG", 100, 101.5, 99) == 1.5 and loss_guard.best_move("SHORT", 100, 101.5, 99) == 1.0
    assert loss_guard.best_move("LONG", 100, None, None) == 0.0
    assert loss_guard.time_candles(NS(time_stop_candles=999)) == 60 and loss_guard.time_atr(NS(time_stop_atr=0)) == 0.1


def test_pause_after_losses_in_a_row():
    at = lambda h, m: dt.datetime(2026, 9, 29, h, m, tzinfo=IST)  # noqa: E731
    cfg = NS(loss_streak_max=3, loss_streak_pause_min=30)
    assert loss_guard.next_run(0, -5.0) == 1 and loss_guard.next_run(2, 4.0) == 0 and loss_guard.next_run(2, 0.0) == 2
    assert loss_guard.refusal(cfg, 2, at(10, 0), at(10, 1)) is None
    assert loss_guard.refusal(cfg, 3, at(10, 0), at(10, 1)) == "3 losing trades in a row: no new entries until 10:30"
    assert loss_guard.refusal(cfg, 3, at(10, 0), at(10, 30)) is None and loss_guard.pause_over(cfg, 3, at(10, 0), at(10, 30))
    day = NS(loss_streak_max=3, loss_streak_pause_min=0)
    assert "rest of the day" in loss_guard.refusal(day, 3, at(10, 0), at(15, 0)) and not loss_guard.pause_over(day, 3, at(10, 0), at(15, 0))
    assert loss_guard.refusal(NS(loss_streak_max=0), 9, at(10, 0), at(10, 1)) is None
    assert not loss_guard.counts("NOT_ON_GROWW") and loss_guard.counts("ATR_SL_HIT")
    # A restart: three losses, the pause ended, then one more loss = a run of 1, not 4.
    rows = [(at(9, 40), -1.0), (at(9, 50), -1.0), (at(10, 0), -1.0), (at(10, 45), -1.0)]
    assert loss_guard.run_from_rows(cfg, rows) == (1, at(10, 45))
    assert loss_guard.run_from_rows(cfg, rows[:3]) == (3, at(10, 0))
    assert loss_guard.run_from_rows(cfg, [*rows[:2], (at(10, 0), 5.0)]) == (0, None)
    assert loss_guard.run_from_rows(day, rows) == (4, at(10, 45))


# ------------------------------------------------------------------ the engine
@pytest.fixture
def eng(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/s.db")
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    import database
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker, _instant

    database.reset_engine()
    database.init_db()
    engine = StrategyEngine(broker=_FakeBroker())
    engine._sleep = _instant
    engine.clock = dt.datetime.combine(DAY, dt.time(10, 0), tzinfo=IST)
    engine._session_date = DAY.isoformat()
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: engine.clock)
    yield engine
    database.reset_engine()


def _set(**fields):
    import database
    from models import BotConfig

    with database.session_factory()() as db:
        row = db.get(BotConfig, 1)
        row.trade_symbols = "ACME"
        row.sma_fast, row.sma_slow = 3, 6
        for name, value in fields.items():
            setattr(row, name, value)
        db.commit()


def _put(engine, frame):
    engine._frames["ACME"] = frame
    engine._tapes["ACME"] = frame
    engine._ltps["ACME"] = float(frame["close"].iloc[-1])
    return frame


OPENING = [(100, 101, 99.5, 100.5), (100.5, 101.5, 100, 101), (101, 101.4, 100.2, 100.8)]


def _raw(today):
    rows = _bars(PREV, dt.time(11, 0), _flat(40)) + _bars(DAY, dt.time(9, 15), [*today, today[-1]])
    return pd.DataFrame(rows)


@pytest.mark.asyncio
async def test_engine_enters_on_a_setup_once(eng):
    import database
    from models import TradeLog
    from strategy_engine import SNAPSHOT_FIELDS, STOCK_FIELDS

    assert {"setup_entry", "setup_only", "setup_vol_mult", "time_stop_candles"} <= set(STOCK_FIELDS)
    assert "loss_streak_max" in SNAPSHOT_FIELDS and "loss_streak_max" not in STOCK_FIELDS  # the bot's, not a stock's
    _set(setup_entry="ORB", setup_only=True, setup_trend=False, setup_vol_mult=0.0)
    cfg = eng.load_config()
    eng.status = "RUNNING"
    now = eng.clock
    frame = _put(eng, _raw(OPENING + [(100.8, 101.2, 100.4, 101.0)]))
    await eng._setup_entry_pass("ACME", frame, cfg, now)
    assert "ACME" not in eng.positions and "waits" in eng._signals["ACME"]
    up = OPENING + [(100.8, 101.2, 100.4, 101.0), (101.0, 102.2, 100.9, 102.0)]
    frame = _put(eng, _raw(up))
    await eng._setup_entry_pass("ACME", frame, cfg, now)
    assert eng.positions["ACME"].direction == "LONG" and "opening-range breakout" in eng._signals["ACME"]
    with database.session_factory()() as db:
        assert db.query(TradeLog).order_by(TradeLog.id.desc()).first().entry_reason == "SETUP_ORB"
    # The trade closes, the price drops back inside and breaks out again: the buy side was already traded today.
    eng.positions.pop("ACME")
    again = up + [(102.0, 102.1, 100.9, 101.0), (101.0, 102.5, 100.9, 102.3)]
    frame = _put(eng, _raw(again))
    await eng._setup_entry_pass("ACME", frame, cfg, now)
    assert "ACME" not in eng.positions
    # The bot's own cross opens nothing while only the setups may.
    assert setups.blocks(eng.load_config(), "SMA_CROSS")


@pytest.mark.asyncio
async def test_engine_time_stop_and_loss_pause(eng):
    import database
    from models import TradeLog
    from strategy_engine import _cfg_for

    _set(setup_entry="ORB", setup_trend=False, setup_vol_mult=0.0, time_stop_candles=2, time_stop_atr=0.5,
         loss_streak_max=2, loss_streak_pause_min=30)
    cfg = eng.load_config()
    eng.status = "RUNNING"
    up = OPENING + [(100.8, 101.2, 100.4, 101.0), (101.0, 102.2, 100.9, 102.0)]
    frame = _put(eng, _raw(up))
    eng.clock = dt.datetime.combine(DAY, dt.time(9, 40, 5), tzinfo=IST)
    await eng._setup_entry_pass("ACME", frame, cfg, eng.clock)
    pos = eng.positions["ACME"]
    symbol_cfg = _cfg_for(cfg, "ACME")
    eng._focus, eng.ltp = "ACME", 102.0
    # One candle closed since the entry: too early.
    _put(eng, _raw(up + [(102.0, 102.1, 101.9, 102.0)]))
    await eng._watch_time_stop(symbol_cfg)
    assert "ACME" in eng.positions
    # Two closed and the price never moved 0.5 ATR its way: closed as a time stop.
    _put(eng, _raw(up + [(102.0, 102.1, 101.9, 102.0), (102.0, 102.1, 101.9, 102.0)]))
    pos.high, pos.low = pos.entry_price + 0.01, pos.entry_price - 0.2
    eng.ltp = pos.entry_price - 0.1
    await eng._watch_time_stop(symbol_cfg)
    assert "ACME" not in eng.positions and eng._loss_run == 1
    with database.session_factory()() as db:
        assert db.query(TradeLog).order_by(TradeLog.id.desc()).first().exit_reason == "TIME_STOP"
    # One more loss makes two in a row: no new entry for 30 minutes, then the count starts again.
    assert eng._loss_pause_hit(cfg) is None
    eng._loss_run, eng._loss_run_at = 2, eng.clock
    assert "2 losing trades in a row" in eng._loss_pause_hit(cfg)
    eng._setup_done.clear()
    eng._setup_tried.clear()
    frame = _put(eng, _raw(up))
    await eng._setup_entry_pass("ACME", frame, cfg, eng.clock)
    assert "ACME" not in eng.positions and "losing trades in a row" in eng._signals["ACME"]
    eng.clock += dt.timedelta(minutes=31)
    assert eng._loss_pause_hit(cfg) is None and eng._loss_run == 0
    # A restart reads the run back from today's closed trades.
    eng._loss_run = 0
    eng.restore_trades_today()
    assert eng._loss_run == 1


def test_api_saves_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        first = client.get("/api/config").json()
        assert first["setup_entry"] == "" and first["setup_only"] is False and first["setup_side"] == "BOTH"
        assert first["setup_trend"] is True and first["setup_vol_mult"] == 1.5 and first["setup_orb_minutes"] == 15
        assert first["time_stop_candles"] == 0 and first["time_stop_atr"] == 0.5
        assert first["loss_streak_max"] == 0 and first["loss_streak_pause_min"] == 30
        body = client.put("/api/config", json={"setup_entry": "inside, orb", "setup_only": True, "setup_side": "LONG",
                                               "setup_vol_mult": 2, "setup_orb_minutes": 30, "time_stop_candles": 4,
                                               "loss_streak_max": 3, "loss_streak_pause_min": 0}).json()
        assert body["setup_entry"] == "ORB,INSIDE" and body["setup_only"] and body["setup_side"] == "LONG"
        assert body["setup_orb_minutes"] == 30 and body["time_stop_candles"] == 4
        assert body["loss_streak_max"] == 3 and body["loss_streak_pause_min"] == 0
        assert client.put("/api/config", json={"setup_entry": "ORB,MAGIC"}).status_code == 422
        assert client.put("/api/config", json={"setup_orb_minutes": 2}).status_code == 422
        assert client.put("/api/config", json={"time_stop_candles": 61}).status_code == 422
        assert client.put("/api/config", json={"setup_entry": ""}).json()["setup_entry"] == ""
    database.reset_engine()


@pytest.mark.asyncio
async def test_a_replayed_day_trades_the_opening_range_breakout_and_the_time_stop(db):  # noqa: F811
    """The whole path on a replayed day: tick -> armed loop -> setup entry -> held loop -> time stop."""
    from sma_research.replayer import BASELINE_OFF, replay_symbol
    from tests.test_candle_patterns import DAY as RDAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    assert BASELINE_OFF["setup_entry"] == "" and BASELINE_OFF["time_stop_candles"] == 0 and BASELINE_OFF["loss_streak_max"] == 0
    # 09:15-09:30 swings 1000-1003; quiet inside it to 10:00; 10:00 closes at 1006 (the breakout); then flat at 1006.
    opening = [(1000.0, 1003.0), (1003.0, 1000.5), (1000.5, 1002.0)] + [(1002.0, 1002.0)] * 12
    day = opening + _quiet(30, 1002.0) + [(1002.0, 1006.0)] + _quiet(329, 1006.0)
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(RDAY, day))
    base = dict(setup_entry="ORB", setup_only=True, setup_trend=False, setup_vol_mult=0.0, qty=1)
    trades = await replay_symbol(frame, "ORBX", [RDAY], _settings(db, **base))
    assert len(trades) == 1, trades
    only = trades[0]
    assert only["direction"] == "LONG" and only["entry_time"].strftime("%H:%M") == "10:01"
    assert only["exit_reason"] == "EOD_SQUARE_OFF"  # nothing else closes it: it sits flat to the square-off
    # With the time stop: five candles later it has gone nowhere, so it is closed then.
    timed = await replay_symbol(frame, "ORBX", [RDAY], _settings(db, **base, time_stop_candles=5, time_stop_atr=0.5))
    assert len(timed) == 1 and timed[0]["exit_reason"] == "TIME_STOP", timed
    assert dt.timedelta(minutes=4) <= timed[0]["exit_time"] - timed[0]["entry_time"] <= dt.timedelta(minutes=7)
    # Off (the baseline): the same day opens nothing on the setup.
    plain = await replay_symbol(frame, "ORBX", [RDAY], _settings(db))
    assert not any((t.get("entry_reason") or "").startswith("SETUP_") for t in plain)
