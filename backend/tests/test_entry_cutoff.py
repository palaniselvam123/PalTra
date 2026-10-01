"""No new entries from 15:00. Open positions can still close (PAPER only)."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pytest

IST = ZoneInfo("Asia/Kolkata")


def _at(hh: int, mm: int) -> dt.datetime:
    return dt.datetime(2026, 9, 29, hh, mm, tzinfo=IST)


@pytest.fixture
def eng(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/sma.db")
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", lambda _msg: None)
    monkeypatch.setattr("groww_client.desk_session_token", lambda: "")
    monkeypatch.setattr("groww_client._env_access_token", lambda: "")
    import database

    database.reset_engine()
    database.init_db()
    from groww_client import GrowwClient
    from strategy_engine import StrategyEngine

    engine = StrategyEngine(broker=GrowwClient(mode="PAPER"))
    engine._focus = "RELIANCE"
    engine._ltps["RELIANCE"] = 100.0
    yield engine
    database.reset_engine()


def _cfg(eng):
    cfg = eng.load_config()
    cfg.symbol = "RELIANCE"
    cfg.qty = 1
    return cfg


async def _signal(eng, signal: str, when: dt.datetime) -> str:
    return await eng._apply_locked(
        signal=signal, cross_price=100.0, atr=1.0, adx_blocks_entry=False, cfg=_cfg(eng), now=when
    )


def test_default_cutoff_is_1500(eng):
    assert eng.load_config().entry_cutoff_time == "15:00"


@pytest.mark.asyncio
async def test_entry_before_the_cutoff_opens(eng):
    assert await _signal(eng, "BULLISH", _at(14, 59)) == "opened LONG"


@pytest.mark.asyncio
async def test_no_new_entry_from_1500(eng):
    result = await _signal(eng, "BULLISH", _at(15, 0))
    assert "no new entries after 15:00" in result
    assert eng.positions == {}
    assert eng.trades_today == 0


@pytest.mark.asyncio
async def test_a_cross_after_the_cutoff_closes_but_does_not_reverse(eng):
    await _signal(eng, "BULLISH", _at(14, 30))
    result = await _signal(eng, "BEARISH", _at(15, 5))
    assert result.startswith("closed on BEARISH") and "no new entries" in result
    assert eng.positions == {}
    assert eng.trades_today == 1


@pytest.mark.asyncio
async def test_force_order_is_refused_after_the_cutoff(eng, monkeypatch):
    from strategy_engine import ForceRefused

    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: True)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: _at(15, 2))
    with pytest.raises(ForceRefused, match="No new entries after 15:00"):
        await eng.force_order("RELIANCE")


def test_cutoff_is_never_later_than_square_off(eng):
    from strategy_engine import _entry_cutoff

    cfg = _cfg(eng)
    cfg.entry_cutoff_time = "15:25"
    cfg.square_off_time = "15:10"
    assert _entry_cutoff(cfg) == dt.time(15, 10)


def test_api_saves_and_validates_the_cutoff(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/config").json()["entry_cutoff_time"] == "15:00"
        ok = client.put("/api/config", json={"entry_cutoff_time": "14:45"})
        assert ok.status_code == 200 and ok.json()["entry_cutoff_time"] == "14:45"
        bad = client.put("/api/config", json={"entry_cutoff_time": "3pm"})
        assert bad.status_code == 400 and "entry_cutoff_time" in bad.json()["detail"]
    database.reset_engine()


def test_old_database_gets_the_column(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute(
        "create table bot_config (id integer primary key, symbol text, qty integer, sma_fast integer, "
        "sma_slow integer, atr_period integer, atr_multiplier float, use_adx_filter boolean, "
        "adx_threshold float, max_daily_loss float, max_trades_per_day integer, square_off_time text, "
        "trading_mode text, exchange text)"
    )
    con.execute(
        "insert into bot_config values (1,'X',1,9,21,14,1.5,0,20,5000,40,'15:15','PAPER','NSE')"
    )
    con.commit()
    con.close()
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{path}")
    import database

    database.reset_engine()
    row = database.init_db()
    assert row.entry_cutoff_time == "15:00"
    database.reset_engine()


def test_no_entry_heads_up_after_the_cutoff(eng, monkeypatch):
    import pandas as pd

    notes: list[str] = []
    monkeypatch.setattr("strategy_engine._schedule_whatsapp", notes.append)
    rows = []
    for i, gap in enumerate([-1.5, -1.0, -0.5, -0.5]):
        rows.append({"ts": i, "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1,
                     "sma_9": 100 + gap, "sma_21": 100.0, "atr_14": 1.0})
    frame = pd.DataFrame(rows)
    eng._warn_upcoming("RELIANCE", frame, _cfg(eng), _at(14, 50))
    assert any("may be ordered" in n for n in notes)
    notes.clear()
    eng._warned.clear()
    eng._warn_upcoming("RELIANCE", frame, _cfg(eng), _at(15, 1))
    assert not any("may be ordered" in n for n in notes)
