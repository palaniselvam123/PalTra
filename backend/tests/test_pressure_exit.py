"""Buyers / sellers fade exit for Most active stocks (pressure_exit.py)."""
from __future__ import annotations

import datetime as dt
import json

import pytest

import pressure_exit
from strategy_engine import IST


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
    now = dt.datetime.now(IST)
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
    day = BASE.date().isoformat()
    assert scan_history.save("active", _scan(BASE - dt.timedelta(minutes=30), ACME=0.62, OTHER=0.4))
    assert scan_history.save("active", _scan(BASE - dt.timedelta(minutes=20), ACME=0.71))
    assert [round(s, 2) for _, s in scan_history.active_shares(day)["ACME"]] == [0.62, 0.71]
    # A run saved later is read on the next call (only the new ones).
    scan_history.save("active", _scan(BASE - dt.timedelta(minutes=10), ACME=0.55))
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
