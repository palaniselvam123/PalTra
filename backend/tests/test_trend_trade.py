"""Trade on the trend reading (trend_trade.py): entry / exit calls, which stocks, and "only the trend opens trades"."""

from types import SimpleNamespace

import trend_trade as tt


def cfg(**kw):
    base = dict(tq_entry=True, tq_entry_score=70, tq_entry_phase=True, tq_only=True, tq_exit=True, tq_exit_score=40,
                tq_exit_phase=True, tq_all=False)
    base.update(kw)
    return SimpleNamespace(**base)


def read(side="BULLISH", score=86, phase="BULL", early=False):
    return {"trend_side": side, "trend_score": score, "phase": phase, "phase_since": "10:30", "phase_early": early}


def test_entry_needs_score_and_phase():
    assert tt.entry_call(read(), cfg())[0] == "LONG"
    assert tt.entry_call(read("BEARISH", 71, "BEAR"), cfg())[0] == "SHORT"
    side, note = tt.entry_call(read(score=57), cfg())
    assert side is None and "needs 70" in note
    assert tt.entry_call(read(phase="CHOP"), cfg())[0] is None
    assert tt.entry_call(read(phase="BEAR"), cfg())[0] is None
    assert tt.entry_call(read(early=True), cfg())[0] is None
    # Phase check off: the score alone decides.
    assert tt.entry_call(read(phase="CHOP"), cfg(tq_entry_phase=False))[0] == "LONG"
    assert tt.entry_call(None, cfg())[0] is None


def test_exit_on_phase_turn_or_score_drop():
    c = cfg()
    assert tt.exit_call(read(), "LONG", c)[0] is False
    hit, note = tt.exit_call(read(phase="CHOP"), "LONG", c)
    assert hit and "choppy" in note
    assert tt.exit_call(read(phase="BEAR"), "LONG", c)[0] is True
    hit, note = tt.exit_call(read(score=29), "LONG", c)
    assert hit and "under 40" in note
    # The SMAs on the other side: the trade's score counts as 0.
    hit, note = tt.exit_call(read("BEARISH", 86, "BULL"), "LONG", c)
    assert hit and "point down" in note
    # Not enough candles: the phase never closes the trade.
    assert tt.exit_call(read(phase="CHOP", early=True), "LONG", c)[0] is False
    # Phase check off: only the score.
    assert tt.exit_call(read(phase="CHOP"), "LONG", cfg(tq_exit_phase=False))[0] is False
    # A short closes when the trend turns up.
    assert tt.exit_call(read("BEARISH", 86, "BEAR"), "SHORT", c)[0] is False
    assert tt.exit_call(read("BEARISH", 86, "BULL"), "SHORT", c)[0] is True


def test_which_stocks():
    assert tt.applies(cfg(), "Auto-arm: Trend quality")
    assert not tt.applies(cfg(), "Auto-arm: Most active")
    assert not tt.applies(cfg(), "Manual")
    assert not tt.applies(cfg(), None)
    assert tt.applies(cfg(tq_all=True), "Manual")


def test_only_the_trend_opens_trades():
    src = "Auto-arm: Trend quality"
    assert tt.blocks(cfg(), src, "SMA_CROSS")
    assert tt.blocks(cfg(), src, "CANDLE_COLOUR")
    assert tt.blocks(cfg(), src, "SMA_CROSS@B2")
    assert tt.blocks(cfg(), src, tt.ENTRY_REASON) is None
    assert tt.blocks(cfg(), src, "FORCE_ORDER") is None
    assert tt.blocks(cfg(), src, "NEWS") is None
    assert tt.blocks(cfg(), "Manual", "SMA_CROSS") is None  # not a Trend quality stock
    assert tt.blocks(cfg(tq_only=False), src, "SMA_CROSS") is None
    assert tt.blocks(cfg(tq_entry=False), src, "SMA_CROSS") is None


def test_defaults_off_and_bounds():
    empty = SimpleNamespace()
    assert not tt.entry_on(empty) and not tt.exit_on(empty) and not tt.every_stock(empty)
    assert tt.entry_score(empty) == 70 and tt.exit_score(empty) == 40
    assert tt.entry_phase(empty) and tt.exit_phase(empty) and tt.only_trend(empty)
    assert tt.entry_score(SimpleNamespace(tq_entry_score=250)) == 100


# ---- In the engine ----

import datetime as dt  # noqa: E402
import json  # noqa: E402

import pytest  # noqa: E402

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def _engine(monkeypatch, tmp_path):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/t.db")
    import database
    from strategy_engine import StrategyEngine
    from tests.test_sma_atr_terminal import _FakeBroker

    database.reset_engine()
    database.init_db()
    eng = StrategyEngine(broker=_FakeBroker())
    eng.status = "RUNNING"
    monkeypatch.setattr(eng, "_still_armed", lambda s: True)
    monkeypatch.setattr("strategy_engine.market_is_open", lambda *_: True)
    monkeypatch.setattr("strategy_engine._cfg_for", lambda c, s: c)
    monkeypatch.setattr("strategy_engine.enrich", lambda frame, *a, **k: frame)
    monkeypatch.setattr(eng, "_exit_minutes", lambda symbol, cfg: 15)
    return eng


def _cfg(source, **kw):
    fields = {"symbol": "ACME", "entry_cutoff_time": "15:00", "square_off_time": "15:15", "sma_fast": 9, "sma_slow": 21,
              "atr_period": 14, "trade_sources": json.dumps({"ACME": source}), "stock_settings": None,
              "tq_entry": True, "tq_entry_score": 70, "tq_entry_phase": True, "tq_only": True, "tq_exit": True,
              "tq_exit_score": 40, "tq_exit_phase": True, "tq_all": False}
    fields.update(kw)
    return type("C", (), fields)()


def _weekday(hour, minute):
    now = dt.datetime.now(IST).replace(hour=hour, minute=minute, second=5, microsecond=0)
    while now.weekday() >= 5:
        now -= dt.timedelta(days=1)
    return now


@pytest.mark.asyncio
@pytest.mark.parametrize("source,got,signals", [
    ("Auto-arm: Trend quality", read(), ["BULLISH"]),
    ("Auto-arm: Trend quality", read("BEARISH", 86, "BEAR"), ["BEARISH"]),
    ("Auto-arm: Trend quality", read(score=43), []),          # score under 70: waits
    ("Auto-arm: Trend quality", read(phase="CHOP"), []),      # phase choppy: waits
    ("Manual", read(), []),                                   # not a Trend quality stock
])
async def test_entry_pass_orders_on_the_trend_once_a_candle(monkeypatch, tmp_path, source, got, signals):
    import database

    eng = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "_trend_read", lambda s, c: got)
    sent = []

    async def fake_apply(signal, frame, cfg, now, alert=True, pullback_at=None, why="SMA_CROSS"):
        assert why == "TREND_QUALITY" and alert is False
        sent.append(signal)
        return f"opened {signal}"

    monkeypatch.setattr(eng, "apply_signal", fake_apply)
    now = _weekday(10, 31)
    await eng._trend_entry_pass("ACME", object(), _cfg(source), now)
    await eng._trend_entry_pass("ACME", object(), _cfg(source), now)  # same candle: asked once
    assert sent == signals
    database.reset_engine()


@pytest.mark.asyncio
async def test_entry_pass_skips_outside_hours_and_while_held(monkeypatch, tmp_path):
    import database

    eng = _engine(monkeypatch, tmp_path)
    monkeypatch.setattr(eng, "_trend_read", lambda s, c: read())
    sent = []

    async def fake_apply(signal, *a, **k):
        sent.append(signal)
        return "opened"

    monkeypatch.setattr(eng, "apply_signal", fake_apply)
    cfg = _cfg("Auto-arm: Trend quality")
    await eng._trend_entry_pass("ACME", object(), cfg, _weekday(9, 16))   # before 09:20
    await eng._trend_entry_pass("ACME", object(), cfg, _weekday(15, 5))   # after the cut-off
    eng.positions["ACME"] = object()
    await eng._trend_entry_pass("ACME", object(), cfg, _weekday(11, 1))   # already holding
    del eng.positions["ACME"]
    await eng._trend_entry_pass("ACME", object(), _cfg("Auto-arm: Trend quality", tq_entry=False), _weekday(11, 16))
    assert sent == []
    database.reset_engine()


@pytest.mark.asyncio
async def test_exit_closes_once_the_trend_fades(monkeypatch, tmp_path):
    import database

    eng = _engine(monkeypatch, tmp_path)
    reads = {"now": read()}
    monkeypatch.setattr(eng, "_trend_read", lambda s, c: reads["now"])
    closed = []

    async def fake_exit(cfg, price, reason):
        closed.append(reason)

    monkeypatch.setattr(eng, "_exit_now", fake_exit)
    opened = _weekday(10, 30).replace(second=10)
    pos = type("P", (), {"entry_time": opened, "trade_id": 7, "signal_direction": "LONG", "mode": "PAPER",
                         "stop_active": True, "sl_order_id": None})()
    eng.positions["ACME"] = pos
    eng.position = pos
    eng.ltp = 100.0
    cfg = _cfg("Auto-arm: Trend quality")

    async def at(hour, minute):
        now = opened.replace(hour=hour, minute=minute, second=20)
        eng._now = lambda: now
        await eng._watch_trend_exit(cfg)

    await at(10, 40)                     # no candle closed since the entry yet
    await at(10, 46)                     # 10:45 candle: trend holds
    assert closed == []
    reads["now"] = read(phase="CHOP")
    await at(10, 50)                     # same candle already judged
    assert closed == []
    await at(11, 1)                      # 11:00 candle: phase choppy → close
    assert closed == ["TREND_FADE"]
    assert "Trend quality exit" in eng._signals["ACME"]
    database.reset_engine()


def test_api_saves_the_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app
    from strategy_engine import STOCK_FIELDS

    names = {"tq_entry", "tq_entry_score", "tq_entry_phase", "tq_only", "tq_exit", "tq_exit_score", "tq_exit_phase", "tq_all"}
    assert names <= set(STOCK_FIELDS)
    with TestClient(app) as client:
        got = client.get("/api/config").json()
        assert got["tq_entry"] is False and got["tq_exit"] is False and got["tq_entry_score"] == 70
        assert got["tq_exit_score"] == 40 and got["tq_only"] is True and got["tq_all"] is False
        body = client.put("/api/config", json={"tq_entry": True, "tq_entry_score": 85, "tq_exit": True, "tq_all": True}).json()
        assert body["tq_entry"] is True and body["tq_entry_score"] == 85 and body["tq_exit"] is True and body["tq_all"] is True
        assert client.put("/api/config", json={"tq_exit_score": 101}).status_code == 422
        assert got["tq_skip_doji"] is False and got["tq_doji_pct"] == 10
        body = client.put("/api/config", json={"tq_skip_doji": True, "tq_doji_pct": 15}).json()
        assert body["tq_skip_doji"] is True and body["tq_doji_pct"] == 15
        assert client.put("/api/config", json={"tq_doji_pct": 0}).status_code == 422
    database.reset_engine()


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "ts": 0}


def test_skip_doji_waits_on_a_candle_that_went_nowhere():
    doji = dict(read(), last_bar=_bar(2663.2, 2668.0, 2660.0, 2663.2))  # GLENMARK 10:20, 18 Nov
    assert tt.entry_call(doji, cfg())[0] == "LONG"                     # off by default: the old behaviour
    side, note = tt.entry_call(doji, cfg(tq_skip_doji=True))
    assert side is None and "doji (body 0% of its range, needs 10%)" in note and note.endswith("— waiting")
    small = dict(read(), last_bar=_bar(100, 110, 100, 100.5))          # body 5% of the range
    assert tt.entry_call(small, cfg(tq_skip_doji=True))[0] is None
    assert tt.entry_call(small, cfg(tq_skip_doji=True, tq_doji_pct=5))[0] == "LONG"
    real = dict(read(), last_bar=_bar(100, 110, 99, 108))
    assert tt.entry_call(real, cfg(tq_skip_doji=True))[0] == "LONG"
    flat = dict(read(), last_bar=_bar(100, 100, 100, 100))             # no range at all counts as a doji
    assert tt.entry_call(flat, cfg(tq_skip_doji=True))[0] is None
    assert tt.entry_call(read(), cfg(tq_skip_doji=True))[0] == "LONG"  # no candle known: nothing to skip on


def test_doji_pct_bounds():
    assert tt.doji_pct(cfg()) == 10 and tt.doji_pct(cfg(tq_doji_pct=0)) == 10
    assert tt.doji_pct(cfg(tq_doji_pct=200)) == 90 and tt.doji_pct(cfg(tq_doji_pct="x")) == 10


def test_regime_read_carries_the_newest_closed_candle():
    import datetime as dt
    import pandas as pd
    import stock_regime

    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    start = int(dt.datetime(2026, 11, 18, 9, 15, tzinfo=ist).timestamp())
    n = 200
    frame = pd.DataFrame({"ts": [start + 60 * i for i in range(n)], "open": [100 + i * 0.1 for i in range(n)],
                          "high": [100.5 + i * 0.1 for i in range(n)], "low": [99.5 + i * 0.1 for i in range(n)],
                          "close": [100.2 + i * 0.1 for i in range(n)], "volume": [1000 * (i + 1) for i in range(n)]})
    now = dt.datetime.fromtimestamp(start + 60 * n, ist)
    got = stock_regime.read(frame, 9, 21, 5, now)
    bar = got["last_bar"]
    assert set(bar) == {"open", "high", "low", "close", "ts"} and bar["ts"] == start + 60 * (n - 5)
