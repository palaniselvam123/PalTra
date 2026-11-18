"""Buyers / sellers fade exit for Most active stocks (pressure_exit.py)."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import pressure_exit
from strategy_engine import IST
from tests.test_gap_mode import db  # noqa: F401


@pytest.fixture(autouse=True)
def _clean():
    pressure_exit.reset()
    yield
    pressure_exit.reset()


def _scan(at: dt.datetime, **shares: float) -> dict:
    return {
        "as_of": at.isoformat(),
        "rows": [{"symbol": s, "buy_share": v, "read_at": at.isoformat(timespec="seconds")} for s, v in shares.items()],
    }


BASE = dt.datetime(2026, 11, 16, 10, 0, tzinfo=IST)


def test_settings_and_source():
    assert pressure_exit.on(type("C", (), {"pressure_exit": True})())
    assert not pressure_exit.on(type("C", (), {})())
    assert pressure_exit.pct(type("C", (), {"pressure_exit_pct": None})()) == 55.0
    assert pressure_exit.pct(type("C", (), {"pressure_exit_pct": 10})()) == 30.0
    assert pressure_exit.from_most_active("Auto-arm: Most active")
    assert pressure_exit.from_most_active("Most active")
    assert not pressure_exit.from_most_active("Trend quality")
    assert not pressure_exit.from_most_active(None)


def test_long_closes_once_buyers_fade():
    entry = BASE.timestamp()
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=5), ACME=0.78))  # the read that armed it
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=5), ACME=0.66))
    hit, note = pressure_exit.judge("ACME", "LONG", entry, entry + 600, 55)
    assert not hit and "buyers 66%" in note
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=10), ACME=0.52))
    hit, note = pressure_exit.judge("ACME", "LONG", entry, entry + 660, 55)
    assert hit and "faded to 52%" in note and "was 78%" in note


def test_short_reads_the_sellers():
    entry = BASE.timestamp()
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=1), ACME=0.20))   # sellers 80 %
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=3), ACME=0.40))   # sellers 60 %
    assert not pressure_exit.judge("ACME", "SHORT", entry, entry + 300, 55)[0]
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=8), ACME=0.50))   # sellers 50 %
    hit, note = pressure_exit.judge("ACME", "SHORT", entry, entry + 600, 55)
    assert hit and "sellers faded to 50%" in note


def test_needs_a_fresh_read_after_entry_and_a_peak_above():
    entry = BASE.timestamp()
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=3), ACME=0.40))
    assert not pressure_exit.judge("ACME", "LONG", entry, entry + 60, 55)[0]          # nothing since the entry
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=2), ACME=0.45))
    hit, note = pressure_exit.judge("ACME", "LONG", entry, entry + 180, 55)
    assert not hit and "never above" in note                                           # never had the pressure
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=4), ACME=0.70))
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=6), ACME=0.50))
    assert pressure_exit.judge("ACME", "LONG", entry, entry + 400, 55)[0]
    # Too old a read is not acted on; a past clock (a replay) sees no read at all.
    assert not pressure_exit.judge("ACME", "LONG", entry, entry + 6 * 60 + 25 * 60, 55)[0]
    assert pressure_exit.judge("ACME", "LONG", entry - 86400, entry - 86000, 55)[1].startswith("no Most active read")


@pytest.mark.asyncio
@pytest.mark.parametrize("source,on,closes", [
    ("Auto-arm: Most active", True, True),
    ("Trend quality", True, False),
    ("Auto-arm: Most active", False, False),
])
async def test_engine_closes_only_most_active_stocks(monkeypatch, tmp_path, source, on, closes):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    import database
    from strategy_engine import OpenPosition, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    # A fixed mid-session clock: reads count only within one IST day, so the wall clock just after midnight IST
    # (18:30 UTC) put the read before the entry on the day before.
    now = dt.datetime(2026, 11, 16, 11, 0, tzinfo=IST)
    monkeypatch.setattr(eng, "_now", lambda: now)
    entry = now - dt.timedelta(minutes=10)
    pressure_exit.note_scan(_scan(entry - dt.timedelta(minutes=2), ACME=0.80))
    pressure_exit.note_scan(_scan(now - dt.timedelta(minutes=1), ACME=0.50))
    eng._focus = "ACME"
    eng.position = OpenPosition(
        direction="LONG", qty=10, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=0.0,
        sl_order_id="", entry_order_id="E", entry_time=entry, trade_id=0, mode="PAPER", stop_active=False,
    )
    eng.status, eng.ltp = "RUNNING", 101.0
    exits = []

    async def fake_exit(cfg, price, reason):
        exits.append(reason)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    cfg = type("C", (), {"symbol": "ACME", "pressure_exit": on, "pressure_exit_pct": 55,
                         "trade_sources": json.dumps({"ACME": source})})()
    await eng._watch_pressure_fade(cfg)
    assert exits == (["PRESSURE_FADE"] if closes else [])
    database.reset_engine()


def test_api_saves_the_setting(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app, pressure_note
    from strategy_engine import STOCK_FIELDS

    assert "pressure_exit" in STOCK_FIELDS and "pressure_exit_pct" in STOCK_FIELDS
    with TestClient(app) as client:
        cfg = client.get("/api/config").json()
        assert cfg["pressure_exit"] is False and cfg["pressure_exit_pct"] == 55.0
        ok = client.put("/api/config", json={"pressure_exit": True, "pressure_exit_pct": 60})
        assert ok.status_code == 200 and ok.json()["pressure_exit"] is True and ok.json()["pressure_exit_pct"] == 60.0
        assert client.put("/api/config", json={"pressure_exit_pct": 99}).status_code == 422
    assert pressure_note(_scan(BASE, ACME=0.7)) == 1
    database.reset_engine()


def test_series_joins_saved_scans_and_live_reads(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "sh.db"))
    import scan_history

    scan_history._ACTIVE_CACHE.clear()
    scan_history._ACTIVE_CHECKED.clear()
    day = BASE.date().isoformat()
    assert scan_history.save("active", _scan(BASE - dt.timedelta(minutes=30), ACME=0.62, OTHER=0.4))
    assert scan_history.save("active", _scan(BASE - dt.timedelta(minutes=20), ACME=0.71))
    assert [round(s, 2) for _, s in scan_history.active_shares(day)["ACME"]] == [0.62, 0.71]
    # A run saved later is read on the next call after ACTIVE_RECHECK_SEC (only the new ones).
    scan_history.save("active", _scan(BASE - dt.timedelta(minutes=10), ACME=0.55))
    assert len(scan_history.active_shares(day)["ACME"]) == 2        # within the re-check time: the cached reads
    scan_history._ACTIVE_CHECKED.clear()
    assert len(scan_history.active_shares(day)["ACME"]) == 3
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=5), ACME=0.30))
    start = (BASE - dt.timedelta(hours=1)).timestamp()
    out = pressure_exit.series("acme", start, (BASE + dt.timedelta(hours=1)).timestamp())
    assert [p["buy_pct"] for p in out] == [62.0, 71.0, 55.0, 30.0]
    assert out == sorted(out, key=lambda p: p["time"])
    # A replay's clock sees only the reads up to then.
    early = pressure_exit.series("ACME", start, (BASE - dt.timedelta(minutes=15)).timestamp())
    assert [p["buy_pct"] for p in early] == [62.0, 71.0]
    assert pressure_exit.series("NONE", start, BASE.timestamp()) == []
    scan_history._ACTIVE_CACHE.clear()


def test_series_and_entry_read_carry_volume_and_money(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "sh.db"))
    import scan_history

    scan_history._ACTIVE_CACHE.clear()
    pressure_exit.reset()
    first = _scan(BASE - dt.timedelta(minutes=30), ACME=0.62)
    first["rows"][0].update(rvol=2.4, value_cr=31.5)
    assert scan_history.save("active", first)
    assert scan_history.save("active", _scan(BASE - dt.timedelta(minutes=20), ACME=0.71))
    live = _scan(BASE + dt.timedelta(minutes=5), ACME=0.30)
    live["rows"][0].update(rvol=3.0, value_cr=40)
    pressure_exit.note_scan(live)
    out = pressure_exit.series("ACME", (BASE - dt.timedelta(hours=1)).timestamp(), (BASE + dt.timedelta(hours=1)).timestamp())
    assert out[0]["rvol"] == 2.4 and out[0]["value_cr"] == 31.5
    assert "rvol" not in out[1] and "value_cr" not in out[1]
    assert (out[2]["rvol"], out[2]["value_cr"]) == (3.0, 40.0)
    # A backtest entry at 09:45 takes the 09:40 read (the latest before it); none before the day's first read.
    got = pressure_exit.read_before("ACME", (BASE - dt.timedelta(minutes=15)).timestamp())
    assert got["buy_pct"] == 71.0
    assert pressure_exit.read_before("ACME", (BASE - dt.timedelta(minutes=25)).timestamp())["rvol"] == 2.4
    assert pressure_exit.read_before("ACME", (BASE - dt.timedelta(minutes=45)).timestamp()) is None
    scan_history._ACTIVE_CACHE.clear()
    pressure_exit.reset()


def test_one_sided_reads_are_left_out_of_the_fade_exit_and_the_chart(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "sh.db"))
    import scan_history

    scan_history._ACTIVE_CACHE.clear()
    pressure_exit.reset()
    assert pressure_exit.usable_share(0.0) is None and pressure_exit.usable_share(1) is None
    assert pressure_exit.usable_share("x") is None and pressure_exit.usable_share(0.4) == 0.4
    scan_history.save("active", _scan(BASE - dt.timedelta(minutes=30), ACME=0.0))
    scan_history.save("active", _scan(BASE - dt.timedelta(minutes=20), ACME=0.6))
    assert pressure_exit.note_scan(_scan(BASE, ACME=1.0)) == 0
    out = pressure_exit.series("ACME", (BASE - dt.timedelta(hours=1)).timestamp(), (BASE + dt.timedelta(hours=1)).timestamp())
    assert [p["buy_pct"] for p in out] == [60.0]
    scan_history._ACTIVE_CACHE.clear()


def test_judge_reads_the_days_saved_scans(tmp_path, monkeypatch):
    """A replay of a saved day (and a desk whose scan runs elsewhere) sees the saved Most active reads."""
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "sh.db"))
    import scan_history

    scan_history._ACTIVE_CACHE.clear()
    scan_history._ACTIVE_CHECKED.clear()
    day = BASE - dt.timedelta(days=3)       # an earlier day: nothing in the live memory
    entry = day.timestamp()
    scan_history.save("active", _scan(day - dt.timedelta(minutes=5), ACME=0.80))
    scan_history.save("active", _scan(day + dt.timedelta(minutes=10), ACME=0.50))
    hit, note = pressure_exit.judge("ACME", "LONG", entry, entry + 15 * 60, 55)
    assert hit and "faded to 50%" in note
    # The replay clock before the second read does not see it.
    assert not pressure_exit.judge("ACME", "LONG", entry, entry + 5 * 60, 55)[0]
    scan_history._ACTIVE_CACHE.clear()


def test_entry_check_and_side():
    entry = BASE.timestamp()
    assert pressure_exit.entry_mode(type("C", (), {"pressure_entry": "at_arm"})()) == "AT_ARM"
    assert pressure_exit.entry_mode(type("C", (), {})()) == "OFF"
    assert pressure_exit.entry_pct(type("C", (), {"pressure_entry_pct": None})()) == 65.0
    assert "no Most active read" in pressure_exit.entry_refusal("ACME", "LONG", entry, 65)
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=2), ACME=0.72))
    assert pressure_exit.entry_refusal("ACME", "LONG", entry, 65) is None
    assert "sellers 28%" in pressure_exit.entry_refusal("ACME", "SHORT", entry, 65)
    assert pressure_exit.entry_side("ACME", entry, 65)[:2] == ("LONG", 72.0)
    assert pressure_exit.entry_side("ACME", entry, 75) is None
    assert pressure_exit.entry_side("ACME", entry + 30 * 60, 65) is None        # the read is too old
    pressure_exit.note_scan(_scan(BASE, ACME=0.6))
    assert "buyers 60%" in pressure_exit.entry_refusal("ACME", "LONG", entry, 65)  # the demand went: no buy


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,source,share,orders", [
    ("AT_ARM", "Auto-arm: Most active", 0.80, ["BULLISH"]),
    ("AT_ARM", "Auto-arm: Most active", 0.20, ["BEARISH"]),
    ("AT_ARM", "Auto-arm: Most active", 0.55, []),     # neither side holds 65 %
    ("AT_ARM", "Trend quality", 0.80, []),             # not a Most active stock
    ("CHECK", "Auto-arm: Most active", 0.80, []),      # the check alone never orders
])
async def test_entry_at_arm_orders_once(monkeypatch, tmp_path, mode, source, share, orders):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/e.db")
    import database
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    now = dt.datetime.now(IST).replace(hour=10, minute=30, second=0, microsecond=0)
    while now.weekday() >= 5:
        now -= dt.timedelta(days=1)
    pressure_exit.note_scan(_scan(now - dt.timedelta(minutes=1), ACME=share))
    eng.status = "RUNNING"
    sent = []

    async def fake_order(symbol, signal, why, note=""):
        sent.append(signal)
        assert why == "MOST_ACTIVE" and symbol == "ACME"
        return f"opened {symbol}"

    monkeypatch.setattr(eng, "direct_order", fake_order)
    monkeypatch.setattr(eng, "_still_armed", lambda s: True)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda *_: True)
    cfg = type("C", (), {"symbol": "ACME", "pressure_entry": mode, "pressure_entry_pct": 65, "entry_cutoff_time": "15:00",
                         "square_off_time": "15:15", "trade_sources": json.dumps({"ACME": source}),
                         "stock_settings": None})()
    monkeypatch.setattr("strategy_engine._cfg_for", lambda c, s: c)
    await eng._pressure_entry_pass("ACME", cfg, now)
    await eng._pressure_entry_pass("ACME", cfg, now)      # once a day
    assert sent == orders
    database.reset_engine()


def test_flip_side_and_level():
    entry = BASE.timestamp()
    assert not pressure_exit.flip_on(type("C", (), {})())
    assert pressure_exit.flip_pct(type("C", (), {"pressure_flip_pct": None})()) == 65.0
    assert pressure_exit.flip_pct(type("C", (), {"pressure_flip_pct": 20})()) == 55.0
    under = (429.0, 432.0)                                                     # close under the VWAP
    assert pressure_exit.flip_side("ACME", "LONG", entry, 65, *under) is None  # no read: the colour decides
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=4), ACME=0.29))  # sellers 71 %
    side, note = pressure_exit.flip_side("ACME", "LONG", entry, 65, *under)
    assert side == "SHORT" and "sellers 71%" in note and "under VWAP 432.00" in note
    assert pressure_exit.flip_side("ACME", "LONG", entry, 65, 433.0, 432.0) is None  # the price is over the VWAP
    assert pressure_exit.flip_side("ACME", "LONG", entry, 65) is None          # VWAP unknown: never flips
    assert pressure_exit.flip_side("ACME", "LONG", entry, 75, *under) is None  # under the level
    assert pressure_exit.flip_side("ACME", "SHORT", entry, 65, 433.0, 432.0) is None  # buyers only 29 %
    assert pressure_exit.flip_side("ACME", "LONG", entry + 30 * 60, 65, *under) is None  # too old
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=2), ACME=0.79))  # buyers 79 % (owner's 12:30 case)
    assert pressure_exit.flip_side("ACME", "SHORT", entry, 65, *under) is None  # but the price is under the VWAP
    assert pressure_exit.flip_side("ACME", "SHORT", entry, 65, 433.0, 432.0)[0] == "LONG"


@pytest.mark.asyncio
@pytest.mark.parametrize("on,source,share,base,want", [
    (True, "Auto-arm: Most active", 0.29, 995.0, ("SHORT", "PRESSURE_FLIP")),   # sellers 71 %, under the VWAP
    (True, "Auto-arm: Most active", 0.29, 1001.0, ("LONG", "CANDLE_COLOUR")),   # over the VWAP: the price disagrees
    (True, "Auto-arm: Most active", 0.50, 995.0, ("LONG", "CANDLE_COLOUR")),    # nobody holds 65 %
    (True, "Trend quality", 0.29, 995.0, ("LONG", "CANDLE_COLOUR")),            # not a Most active stock
    (False, "Auto-arm: Most active", 0.29, 995.0, ("LONG", "CANDLE_COLOUR")),   # switch off
])
async def test_colour_entry_flips_to_the_side_holding_the_orders(db, on, source, share, base, want):  # noqa: F811
    import pandas as pd
    from sma_research.replayer import replay_symbol
    from tests.test_candle_patterns import DAY, DAY0, _quiet, _rows
    from tests.test_gap_mode import _settings

    pressure_exit.note_scan(_scan(dt.datetime.combine(DAY, dt.time(9, 40), tzinfo=IST), COL=share))
    greens = [(base, base + 0.3), (base + 0.3, base + 0.6), (base + 0.6, base + 0.9)]
    frame = pd.DataFrame(_rows(DAY0, _quiet(375)) + _rows(DAY, _quiet(30) + greens + _quiet(342, base + 0.9)))
    trades = await replay_symbol(frame, "COL", [DAY], _settings(
        db, entry_mode="COLOUR", pattern_tf=1, qty=1, use_stop=False, pressure_flip=on, pressure_flip_pct=65,
        trade_sources=json.dumps({"COL": source})))
    assert (trades[0]["direction"], trades[0]["entry_reason"]) == want


def test_replay_marks_most_active_picks():
    from types import SimpleNamespace

    import replay

    picked = [{"symbol": "acme", "source": "Most active"}, {"symbol": "BETA", "source": "Trend quality"}]
    got = replay.pick_sources(SimpleNamespace(kind="AUTOARM"), picked)
    assert got == {"ACME": "Auto-arm: Most active", "BETA": "Auto-arm: Trend quality"}
    assert pressure_exit.from_most_active(got["ACME"]) and not pressure_exit.from_most_active(got["BETA"])
    assert pressure_exit.from_most_active(replay.pick_sources(SimpleNamespace(kind="ACTIVE"), [{"symbol": "X"}])["X"])
    assert replay.pick_sources(SimpleNamespace(kind="TREND"), [{"symbol": "X"}]) == {}


@pytest.mark.asyncio
async def test_entry_at_arm_waits_for_the_executed_volume_check(monkeypatch, tmp_path):
    # Owner's KAYNES case: sellers held 78 % of the waiting orders, so "enter at once" sold, while the ticked executed
    # volume check (buyers did most of the trading) was never read. Now the at-once entry waits for that check too.
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/e.db")
    import database
    import trade_flow
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    now = dt.datetime.now(IST).replace(hour=10, minute=30, second=0, microsecond=0)
    while now.weekday() >= 5:
        now -= dt.timedelta(days=1)
    pressure_exit.note_scan(_scan(now - dt.timedelta(minutes=1), ACME=0.22))       # sellers 78 % waiting
    eng.status = "RUNNING"
    sent = []

    async def fake_order(symbol, signal, why, note=""):
        sent.append(signal)
        return f"opened {symbol}"

    flow = {"buy": 710, "sell": 290}
    monkeypatch.setattr(eng, "direct_order", fake_order)
    monkeypatch.setattr(eng, "_still_armed", lambda s: True)
    monkeypatch.setattr(eng, "_exit_minutes", lambda s, c: 1)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda *_: True)
    monkeypatch.setattr(trade_flow, "minutes", lambda *a, **k: [{"time": 0, "buy": flow["buy"], "sell": flow["sell"], "src": "C"}] * 3)
    cfg = type("C", (), {"symbol": "ACME", "pressure_entry": "AT_ARM", "pressure_entry_pct": 65, "entry_cutoff_time": "15:00",
                         "square_off_time": "15:15", "trade_sources": json.dumps({"ACME": "Auto-arm: Most active"}),
                         "stock_settings": None, "flow_entry": True, "flow_entry_pct": 52, "flow_entry_window": 3})()
    monkeypatch.setattr("strategy_engine._cfg_for", lambda c, s: c)
    await eng._pressure_entry_pass("ACME", cfg, now)
    assert sent == [] and "sellers did only 29%" in eng._signals["ACME"]
    flow.update(buy=300, sell=700)                                                  # sellers now do the trading
    await eng._pressure_entry_pass("ACME", cfg, now)
    assert sent == ["BEARISH"]
    database.reset_engine()


@pytest.mark.asyncio
@pytest.mark.parametrize("sell_share, level, closes", [
    (0.7, 60, True),    # sellers did 70% of the shares traded: the executed volume agrees, the long closes
    (0.4, 60, False),   # sellers did only 40%: held
    (0.4, 0, True),     # check off: the waiting orders alone close it, as before
    (None, 60, True),   # too little volume to read: the exit goes
])
async def test_fade_exit_waits_for_the_executed_volume(monkeypatch, tmp_path, sell_share, level, closes):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    import database
    import pandas as pd

    import trade_flow
    from strategy_engine import OpenPosition, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    now = dt.datetime(2026, 11, 16, 11, 0, 30, tzinfo=IST)
    monkeypatch.setattr(eng, "_now", lambda: now)
    entry = now - dt.timedelta(minutes=10)
    pressure_exit.note_scan(_scan(entry - dt.timedelta(minutes=2), ACME=0.80))
    pressure_exit.note_scan(_scan(now - dt.timedelta(minutes=1), ACME=0.50))
    eng._focus = "ACME"
    eng._tapes["ACME"] = pd.DataFrame({"ts": [int(now.timestamp()) - 60]})

    def fake_minutes(symbol, tape, start, end):
        if sell_share is None:
            return []
        return [{"time": t, "buy": 100 * (1 - sell_share), "sell": 100 * sell_share, "src": "T"} for t in range(start, end, 60)]

    monkeypatch.setattr(trade_flow, "minutes", fake_minutes)
    eng.position = OpenPosition(
        direction="LONG", qty=10, entry_price=100.0, ma_cross_price=100.0, atr_at_entry=1.0, sl_trigger=0.0,
        sl_order_id="", entry_order_id="E", entry_time=entry, trade_id=0, mode="PAPER", stop_active=False,
    )
    eng.status, eng.ltp = "RUNNING", 101.0
    exits = []

    async def fake_exit(cfg, price, reason):
        exits.append(reason)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    cfg = type("C", (), {"symbol": "ACME", "pressure_exit": True, "pressure_exit_pct": 55, "candle_minutes": 15,
                         "pressure_exit_flow": level, "pressure_exit_flow_window": 5,
                         "trade_sources": json.dumps({"ACME": "Most active"})})()
    await eng._watch_pressure_fade(cfg)
    assert exits == (["PRESSURE_FADE"] if closes else [])
    if not closes:
        assert "held" in eng._signals["ACME"]
    database.reset_engine()


def test_fade_exit_flow_settings():
    from main import ConfigUpdate

    assert pressure_exit.exit_flow_pct(type("C", (), {})()) == 0.0
    assert pressure_exit.exit_flow_pct(type("C", (), {"pressure_exit_flow": 30})()) == 50.0
    assert pressure_exit.exit_flow_window(type("C", (), {"pressure_exit_flow_window": 0})()) == 0
    assert pressure_exit.exit_flow_window(type("C", (), {})()) == 3
    assert ConfigUpdate(pressure_exit_flow=60, pressure_exit_flow_window=15).pressure_exit_flow_window == 15


def test_watch_from_the_entry_closes_a_short_that_never_had_the_pressure():
    """Owner's BSE case: a short opened with sellers at 63 %, 67 % at 10:00 and a 70 % exit level."""
    entry = BASE.timestamp()
    pressure_exit.note_scan(_scan(BASE - dt.timedelta(minutes=2), BSE=0.37))   # sellers 63 % before the entry
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=11), BSE=0.33))  # sellers 67 %
    hit, note = pressure_exit.judge("BSE", "SHORT", entry, entry + 12 * 60, 70)
    assert not hit and "not armed" in note                                      # off: needs sellers above 70 % first
    hit, note = pressure_exit.judge("BSE", "SHORT", entry, entry + 12 * 60, 70, from_entry=True)
    assert hit and "sellers at 67%" in note and "never above 70%" in note
    # Still above the level: no exit either way.
    assert not pressure_exit.judge("BSE", "SHORT", entry, entry + 12 * 60, 60, from_entry=True)[0]
    # A side that was above it reads as a fade, as before.
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=13), BSE=0.20))  # sellers 80 %
    pressure_exit.note_scan(_scan(BASE + dt.timedelta(minutes=15), BSE=0.32))  # sellers 68 %
    hit, note = pressure_exit.judge("BSE", "SHORT", entry, entry + 16 * 60, 70, from_entry=True)
    assert hit and "faded to 68%" in note


@pytest.mark.asyncio
@pytest.mark.parametrize("any_, flow_buy, closes", [
    (True, 0.59, True),    # watched from the entry, buyers did 59 % (≥ 55): closed
    (True, 0.45, False),   # buyers did 45 %: held by the executed volume check
    (False, 0.59, False),  # off: sellers were never above 70 %, nothing happens
])
async def test_engine_watch_from_entry_with_the_executed_check(monkeypatch, tmp_path, any_, flow_buy, closes):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/p.db")
    import database
    import pandas as pd

    import trade_flow
    from strategy_engine import OpenPosition, StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    now = dt.datetime(2026, 11, 6, 10, 15, 30, tzinfo=IST)
    monkeypatch.setattr(eng, "_now", lambda: now)
    entry = dt.datetime(2026, 11, 6, 9, 30, 5, tzinfo=IST)
    pressure_exit.note_scan(_scan(entry - dt.timedelta(minutes=3), BSE=0.37))
    pressure_exit.note_scan(_scan(dt.datetime(2026, 11, 6, 10, 11, tzinfo=IST), BSE=0.33))
    eng._focus = "BSE"
    eng._tapes["BSE"] = pd.DataFrame({"ts": [int(now.timestamp()) - 60]})
    monkeypatch.setattr(
        trade_flow, "minutes",
        lambda symbol, tape, start, end: [
            {"time": t, "buy": 100 * flow_buy, "sell": 100 * (1 - flow_buy), "src": "T"} for t in range(start, end, 60)
        ],
    )
    eng.position = OpenPosition(
        direction="SHORT", qty=10, entry_price=3763.0, ma_cross_price=3763.0, atr_at_entry=16.0, sl_trigger=0.0,
        sl_order_id="", entry_order_id="E", entry_time=entry, trade_id=0, mode="PAPER", stop_active=False,
    )
    eng.status, eng.ltp = "RUNNING", 3776.0
    exits = []

    async def fake_exit(cfg, price, reason):
        exits.append(reason)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    cfg = type("C", (), {"symbol": "BSE", "pressure_exit": True, "pressure_exit_pct": 70, "pressure_exit_any": any_,
                         "candle_minutes": 15, "pressure_exit_flow": 55, "pressure_exit_flow_window": 0,
                         "trade_sources": json.dumps({"BSE": "Most active"})})()
    await eng._watch_pressure_fade(cfg)
    assert exits == (["PRESSURE_FADE"] if closes else [])
    database.reset_engine()


def test_watch_from_entry_setting():
    from main import ConfigUpdate

    assert not pressure_exit.from_entry(type("C", (), {})())
    assert pressure_exit.from_entry(type("C", (), {"pressure_exit_any": True})())
    assert ConfigUpdate(pressure_exit_any=True).pressure_exit_any is True
