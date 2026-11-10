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
    assert reverse_exit.clean("small,vturn") == "VTURN,SMALL"
    assert reverse_exit.clean("") == ""
    with pytest.raises(ValueError):
        reverse_exit.clean("VTURN,STOP")


def test_on_matches_the_exit_reason():
    cfg = Cfg(reverse_on_exit="VTURN,CANDLES")
    assert reverse_exit.on(cfg, "SMA9_VTURN")
    assert reverse_exit.on(cfg, "COUNTER_CANDLES")
    assert not reverse_exit.on(cfg, "SMA9_TURN")
    assert not reverse_exit.on(Cfg(reverse_on_exit=""), "SMA9_VTURN")
    assert reverse_exit.on(Cfg(reverse_on_exit="SMALL"), "SMALL_CANDLE_EXIT")
    assert not reverse_exit.on(cfg, "SMALL_CANDLE_EXIT")
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


@pytest.mark.asyncio
async def test_a_reversed_trade_closes_on_the_first_candle_of_the_other_colour():
    import turn_entry
    from types import SimpleNamespace

    from tests.test_turn_entry import _colour_frame, _held_engine

    cfg = SimpleNamespace(symbol="X", reverse_colour_exit=True, candle_minutes=1)
    eng, exits = _held_engine("X", why="EXIT_REVERSE", direction="SHORT")
    eng._frames["X"] = _colour_frame(["r"])
    await eng._watch_turn_colour(cfg)
    assert exits == []  # a red candle keeps the short
    eng._frames["X"] = _colour_frame(["r", "g"])
    await eng._watch_turn_colour(cfg)
    assert exits == [turn_entry.COLOUR_EXIT]
    assert "reversed trade closed on a green candle" in eng._signals["X"]
    # Off by default; a manual Reverse and a cross trade are left alone; the turn switch alone does not cover it.
    for why, setting in (
        ("EXIT_REVERSE", SimpleNamespace(symbol="X", candle_minutes=1)),
        ("MANUAL_REVERSE", cfg),
        ("SMA_CROSS", cfg),
        ("EXIT_REVERSE", SimpleNamespace(symbol="X", turn_colour_exit=True, candle_minutes=1)),
    ):
        other, got = _held_engine("X", why=why, direction="SHORT")
        other._frames["X"] = _colour_frame(["r", "g"])
        await other._watch_turn_colour(setting)
        assert got == [], why


def test_api_saves_the_reversed_trade_colour_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api2.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "reverse_colour_exit" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["reverse_colour_exit"] is False
        ok = client.put("/api/config", json={"reverse_colour_exit": True})
        assert ok.status_code == 200 and ok.json()["reverse_colour_exit"] is True
    database.reset_engine()


def test_reverse_settings_overlay_and_groups():
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        use_vwap=True, bb_exit="BOTH", counter_exit_count=0, cross_exit=True,
        reverse_settings='{"use_vwap":false,"bb_exit":"OFF","counter_exit_count":2}',
    )
    over = reverse_exit.overlay(cfg)
    assert over.use_vwap is False and over.bb_exit == "OFF" and over.counter_exit_count == 2
    assert cfg.use_vwap is True  # the bot's own settings are not changed
    only_exits = reverse_exit.overlay(cfg, groups=("exits",))
    assert only_exits.bb_exit == "OFF" and only_exits.use_vwap is True and only_exits.counter_exit_count == 0
    assert reverse_exit.overlay(SimpleNamespace(use_vwap=True, reverse_settings="")).use_vwap is True
    assert reverse_exit.parse_settings('{"symbol":"X","use_rsi":true}') == {"use_rsi": True}
    with pytest.raises(ValueError):
        reverse_exit.clean_settings({"trading_mode": "LIVE"})


@pytest.mark.asyncio
async def test_reverse_uses_its_own_entry_checks_and_exits(engine, monkeypatch):  # noqa: F811
    _session(monkeypatch)
    engine._frames["SHIPROCKET"] = _ohlcv([100.0 + i * 0.1 for i in range(40)])
    engine._ltps["SHIPROCKET"] = 104.0
    engine.status = "RUNNING"
    _arm("SHIPROCKET")
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    # The bot's own RSI check refuses every short.
    cfg.use_rsi = True
    cfg.rsi_short_min = 0.0
    cfg.rsi_short_max = 0.0
    engine._focus = "SHIPROCKET"
    engine.ltp = 104.0
    await engine._reverse_after_exit(cfg, "SHIPROCKET", "LONG", "SMA9_VTURN")
    assert "SHIPROCKET" not in engine.positions  # refused by the bot's RSI check
    # The reversed trades' own settings switch the RSI check off: the reverse opens.
    cfg.reverse_settings = '{"use_rsi":false,"bb_exit":"MIDDLE"}'
    await engine._reverse_after_exit(cfg, "SHIPROCKET", "LONG", "SMA9_VTURN")
    assert engine.positions["SHIPROCKET"].direction == "SHORT"
    # While it is open its own exits apply; a normal trade keeps the bot's.
    from strategy_engine import _cfg_for

    cfg.bb_exit = "OFF"
    assert engine._trade_cfg(cfg, "SHIPROCKET").bb_exit == "MIDDLE"
    assert engine._trade_cfg(cfg, "SHIPROCKET", groups=("exits",)).use_rsi is True
    engine.positions["SHIPROCKET"].entry_why = "SMA_CROSS"
    assert engine._trade_cfg(cfg, "SHIPROCKET").bb_exit == "OFF"
    assert _cfg_for(cfg, "SHIPROCKET").bb_exit == "OFF"


def test_api_saves_reverse_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api3.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert "reverse_settings" in STOCK_FIELDS
    with TestClient(app) as client:
        assert client.get("/api/config").json()["reverse_settings"] == {}
        ok = client.put("/api/config", json={"reverse_settings": {"use_vwap": False, "counter_exit_count": 3}})
        assert ok.status_code == 200 and ok.json()["reverse_settings"] == {"use_vwap": False, "counter_exit_count": 3}
        assert client.put("/api/config", json={"reverse_settings": {"trading_mode": "LIVE"}}).status_code == 422
        assert client.put("/api/config", json={"reverse_settings": {"counter_exit_count": 99}}).status_code == 422
        cleared = client.put("/api/config", json={"reverse_settings": {}})
        assert cleared.json()["reverse_settings"] == {}
    database.reset_engine()
