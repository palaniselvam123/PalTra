"""Reverse instead of only closing, and the manual Reverse button (reverse_exit.py).

PAPER only: a fake broker fills locally; no Groww order path is reached.
"""
from __future__ import annotations

import datetime as dt

import pytest

import reverse_exit
from tests.test_sma_atr_terminal import IST, _ohlcv, engine  # noqa: F401  (engine is a fixture)


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_clean_keeps_known_keys_in_order():
    assert reverse_exit.clean("candles, vturn") == "VTURN,CANDLES"
    assert reverse_exit.clean(["TURN"]) == "TURN"
    assert reverse_exit.clean("") == ""
    with pytest.raises(ValueError):
        reverse_exit.clean("VTURN,STOP")


def test_on_matches_the_exit_reason():
    cfg = Cfg(reverse_on_exit="VTURN,CANDLES")
    assert reverse_exit.on(cfg, "SMA9_VTURN")
    assert reverse_exit.on(cfg, "COUNTER_CANDLES")
    assert not reverse_exit.on(cfg, "SMA9_TURN")
    assert not reverse_exit.on(Cfg(reverse_on_exit=""), "SMA9_VTURN")
    assert reverse_exit.opposite_signal("LONG") == "BEARISH"
    assert reverse_exit.opposite_signal("SHORT") == "BULLISH"


def _arm(symbol: str) -> None:
    from models import BotConfig
    import database

    with database.session_factory()() as db:
        db.get(BotConfig, 1).trade_symbols = symbol
        db.commit()


def _session(monkeypatch, open_=True):
    monkeypatch.setattr("strategy_engine.market_is_open", lambda now=None: open_)
    monkeypatch.setattr("strategy_engine._ist_now", lambda: dt.datetime(2026, 9, 29, 13, 0, tzinfo=IST))


@pytest.mark.asyncio
async def test_manual_reverse_closes_and_opens_the_other_side(engine, monkeypatch):  # noqa: F811
    _session(monkeypatch)
    engine._frames["SHIPROCKET"] = _ohlcv([100.0 + i * 0.1 for i in range(40)])
    engine._ltps["SHIPROCKET"] = 104.0
    engine.data_source = "GROWW"
    _arm("SHIPROCKET")
    await engine.force_order("SHIPROCKET")
    assert engine.positions["SHIPROCKET"].direction == "LONG"
    result = await engine.reverse_position("SHIPROCKET")
    assert "closed and reversed" in result and "opened SHORT" in result
    assert engine.positions["SHIPROCKET"].direction == "SHORT"
    rows = engine.trades()
    closed = next(r for r in rows if r["direction"] == "LONG")
    opened = next(r for r in rows if r["direction"] == "SHORT")
    assert closed["exit_reason"] == "MANUAL_REVERSE"
    assert opened["entry_reason"] == "MANUAL_REVERSE" and opened["exit_time"] is None


@pytest.mark.asyncio
async def test_manual_reverse_refused_after_hours_leaves_the_trade(engine, monkeypatch):  # noqa: F811
    _session(monkeypatch)
    engine._frames["SHIPROCKET"] = _ohlcv([100.0 + i * 0.1 for i in range(40)])
    engine._ltps["SHIPROCKET"] = 104.0
    engine.data_source = "GROWW"
    _arm("SHIPROCKET")
    await engine.force_order("SHIPROCKET")
    _session(monkeypatch, open_=False)
    from strategy_engine import ForceRefused

    with pytest.raises(ForceRefused, match="Market is closed"):
        await engine.reverse_position("SHIPROCKET")
    assert engine.positions["SHIPROCKET"].direction == "LONG"
    with pytest.raises(ForceRefused, match="no open position"):
        await engine.reverse_position("TCS")


@pytest.mark.asyncio
async def test_reverse_after_an_exit_opens_the_other_side(engine, monkeypatch):  # noqa: F811
    _session(monkeypatch)
    engine._frames["SHIPROCKET"] = _ohlcv([100.0 + i * 0.1 for i in range(40)])
    engine._ltps["SHIPROCKET"] = 104.0
    engine.status = "RUNNING"
    _arm("SHIPROCKET")
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    engine._focus = "SHIPROCKET"
    engine.ltp = 104.0
    # A long that a V-turn exit just closed: the reverse asks for a short.
    await engine._reverse_after_exit(cfg, "SHIPROCKET", "LONG", "SMA9_VTURN")
    assert engine.positions["SHIPROCKET"].direction == "SHORT"
    assert engine.trades()[0]["entry_reason"] == "EXIT_REVERSE"
    # Already holding: nothing more.
    await engine._reverse_after_exit(cfg, "SHIPROCKET", "SHORT", "SMA9_VTURN")
    assert engine.positions["SHIPROCKET"].direction == "SHORT"


def test_api_saves_the_setting_and_guards_live(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from models import BotConfig
    from strategy_engine import STOCK_FIELDS

    assert "reverse_on_exit" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["reverse_on_exit"] == ""
        ok = client.put("/api/config", json={"reverse_on_exit": "candles,vturn"})
        assert ok.status_code == 200 and ok.json()["reverse_on_exit"] == "VTURN,CANDLES"
        assert client.put("/api/config", json={"reverse_on_exit": "STOP"}).status_code == 422
        # PAPER: no position, so the reverse is refused with a reason (no order).
        flat = client.post("/api/bot/reverse", json={"symbol": "TCS"})
        assert flat.status_code == 400
        # LIVE without the confirm: refused before anything is sent.
        with database.session_factory()() as db:
            db.get(BotConfig, 1).trading_mode = "LIVE"
            db.commit()
        live = client.post("/api/bot/reverse", json={"symbol": "TCS"})
        assert live.status_code == 409
        with database.session_factory()() as db:
            db.get(BotConfig, 1).trading_mode = "PAPER"
            db.commit()
    database.reset_engine()
