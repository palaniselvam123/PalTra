"""How exits combine (exit_join.py): any one closes (OR, default) or the ticked group must agree (AND).

PAPER only: a fake broker fills locally; no Groww order path is reached.
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pytest

import exit_join
from tests.test_reverse_exit import _arm, _session
from tests.test_sma_atr_terminal import IST, _ohlcv, engine  # noqa: F401  (engine is a fixture)


def _cfg(**kw):
    base = dict(exit_join="AND", exit_group="COUNTER,VTURN", exit_need=0, exit_within=3)
    base.update(kw)
    return SimpleNamespace(**base)


def test_group_keys_and_mode():
    assert exit_join.clean_group("vturn, cross") == "CROSS,VTURN"
    assert exit_join.clean_group("") == ""
    with pytest.raises(ValueError):
        exit_join.clean_group("STOP")
    assert exit_join.active(_cfg())
    assert not exit_join.active(_cfg(exit_join="OR"))
    assert not exit_join.active(_cfg(exit_group="VTURN"))  # one exit alone is just that exit
    assert exit_join.in_group(_cfg(), "SMA9_VTURN") and not exit_join.in_group(_cfg(), "SUPERTREND")
    assert not exit_join.in_group(_cfg(), "TSL_HIT")  # stops never vote
    assert exit_join.need(_cfg()) == 2 and exit_join.need(_cfg(exit_need=1)) == 1 and exit_join.need(_cfg(exit_need=9)) == 2


def test_votes_must_land_within_the_window():
    votes: dict = {}
    done, text = exit_join.vote(votes, _cfg(), "COUNTER_CANDLES", 1000.0, 5)
    assert not done and "counter candles ✓" in text and "SMA 9 V-turn" in text
    # 3 candles of 5 minutes = 900 s: a V-turn 15 minutes and 1 second later no longer agrees ...
    late = dict(votes)
    done_late, _ = exit_join.vote(late, _cfg(), "SMA9_VTURN", 1000.0 + 901, 5)
    assert not done_late and "COUNTER" not in late  # the old vote expired
    fresh = dict(votes)
    done_ok, text_ok = exit_join.vote(fresh, _cfg(), "SMA9_VTURN", 1000.0 + 600, 5)
    assert done_ok and "exits agree" in text_ok
    # "any 1 of 2" closes on the first vote.
    assert exit_join.vote({}, _cfg(exit_need=1), "SMA9_VTURN", 0.0, 5)[0]


def _held(engine, monkeypatch, **cfg_kw):  # noqa: F811
    _session(monkeypatch)
    engine._frames["SHIPROCKET"] = _ohlcv([100.0 + i * 0.1 for i in range(40)])
    engine._ltps["SHIPROCKET"] = 104.0
    engine.data_source = "GROWW"
    _arm("SHIPROCKET")
    return cfg_kw


@pytest.mark.asyncio
async def test_and_holds_until_the_group_agrees_and_or_closes_at_once(engine, monkeypatch):  # noqa: F811
    _held(engine, monkeypatch)
    await engine.force_order("SHIPROCKET")
    assert "SHIPROCKET" in engine.positions
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    engine._focus = "SHIPROCKET"
    engine.ltp = 104.0
    cfg.exit_join, cfg.exit_group, cfg.exit_need, cfg.exit_within = "AND", "COUNTER,VTURN", 0, 3
    await engine._exit_now(cfg, 104.0, "COUNTER_CANDLES")
    assert "SHIPROCKET" in engine.positions  # one vote: held
    assert "waiting for SMA 9 V-turn" in engine._signals["SHIPROCKET"]
    # An exit outside the group and every stop still close on their own.
    await engine._exit_now(cfg, 104.0, "SMA9_VTURN")
    assert "SHIPROCKET" not in engine.positions
    row = engine.trades()[0]
    assert row["exit_reason"] == "SMA9_VTURN"


@pytest.mark.asyncio
async def test_exits_outside_the_group_and_stops_close_at_once(engine, monkeypatch):  # noqa: F811
    _held(engine, monkeypatch)
    await engine.force_order("SHIPROCKET")
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    engine._focus = "SHIPROCKET"
    engine.ltp = 104.0
    cfg.exit_join, cfg.exit_group = "AND", "COUNTER,VTURN"
    await engine._exit_now(cfg, 104.0, "SUPERTREND")
    assert "SHIPROCKET" not in engine.positions
    await engine.force_order("SHIPROCKET")
    await engine._exit_now(cfg, 104.0, "TSL_HIT")
    assert "SHIPROCKET" not in engine.positions
    # OR (the default): a grouped exit closes at once.
    await engine.force_order("SHIPROCKET")
    cfg.exit_join = "OR"
    await engine._exit_now(cfg, 104.0, "COUNTER_CANDLES")
    assert "SHIPROCKET" not in engine.positions


@pytest.mark.asyncio
async def test_an_opposite_cross_votes_and_holds(engine, monkeypatch):  # noqa: F811
    _held(engine, monkeypatch)
    await engine.force_order("SHIPROCKET")
    cfg = engine.load_config()
    cfg.symbol = "SHIPROCKET"
    cfg.use_adx_filter = False
    engine._focus = "SHIPROCKET"
    engine.ltp = 104.0
    engine.status = "RUNNING"
    cfg.exit_join, cfg.exit_group = "AND", "CROSS,COUNTER"
    from indicators import enrich

    frame = enrich(engine._frames["SHIPROCKET"], cfg.sma_fast, cfg.sma_slow, cfg.atr_period)
    now = dt.datetime(2026, 9, 29, 13, 0, tzinfo=IST)
    out = await engine.apply_signal("BEARISH", frame, cfg, now, alert=False)
    assert "SMA cross noted" in out and engine.positions["SHIPROCKET"].direction == "LONG"
    # The counter-candle exit completes the group: closed under its own reason, no reverse.
    await engine._exit_now(cfg, 104.0, "COUNTER_CANDLES")
    assert "SHIPROCKET" not in engine.positions
    assert engine.trades()[0]["exit_reason"] == "COUNTER_CANDLES"


def test_api_saves_the_exit_group(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    assert {"exit_join", "exit_group", "exit_need", "exit_within"} <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["exit_join"] == "OR" and got["exit_group"] == "" and got["exit_within"] == 3
        ok = client.put("/api/config", json={"exit_join": "AND", "exit_group": "vturn,cross,colour", "exit_need": 2, "exit_within": 4})
        body = ok.json()
        assert ok.status_code == 200 and body["exit_group"] == "CROSS,COLOUR,VTURN" and body["exit_need"] == 2
        assert client.put("/api/config", json={"exit_group": "STOP"}).status_code == 422
        assert client.put("/api/config", json={"exit_join": "MAYBE"}).status_code == 422
    database.reset_engine()
