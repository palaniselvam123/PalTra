"""SMA 9 turn alert and entry (turn_entry.py): act on the fast SMA's V before the 9/21 cross.

PAPER only: the replay engine fills locally (no Groww order path).
"""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd
import pytest

import turn_entry
from tests.test_join_trend import _day

DAY = dt.date(2026, 9, 22)
from tests.test_gap_mode import _settings, db  # noqa: F401  (db is a fixture)


def _closed(closes) -> pd.DataFrame:
    return pd.DataFrame({"close": [float(c) for c in closes]})


CFG = SimpleNamespace()  # every V-turn number at its default


def test_detect_a_turn_down_once_on_the_first_candle():
    up = [100 + i * 0.1 for i in range(30)]  # rising: SMA 9 slope about +0.1 %/candle
    down = [up[-1] - (i + 1) * 0.4 for i in range(6)]
    found = [turn_entry.detect(_closed(up + down[: n + 1]), 9, CFG) for n in range(len(down))]
    hits = [(n, f[0]) for n, f in enumerate(found) if f]
    assert len(hits) == 1 and hits[0][1] == "SHORT"
    assert "turned down" in next(f for f in found if f)[1]


def test_detect_a_turn_up_after_a_fall():
    fall = [100 - i * 0.1 for i in range(30)]
    rise = [fall[-1] + (i + 1) * 0.4 for i in range(6)]
    hits = [turn_entry.detect(_closed(fall + rise[: n + 1]), 9, CFG) for n in range(len(rise))]
    assert [h[0] for h in hits if h] == ["LONG"]


def test_no_turn_from_flat_or_a_slow_drift():
    flat = [100 + (0.01 if i % 2 else -0.01) for i in range(30)]
    drop = [100 - (i + 1) * 0.4 for i in range(6)]
    # Flat then falling: no rise came before it, so it is not a V.
    assert all(turn_entry.detect(_closed(flat + drop[: n + 1]), 9, CFG) is None for n in range(len(drop)))
    drift = [100 + i * 0.002 for i in range(40)]
    assert turn_entry.detect(_closed(drift), 9, CFG) is None
    assert turn_entry.detect(_closed([100] * 5), 9, CFG) is None


def test_alert_text_says_whether_it_orders():
    msg = turn_entry.alert_text("Bot 3", "ASHOKLEY", "SHORT", "SMA 9 turned down", 142.97, entry=False)
    assert "▼ ASHOKLEY" in msg and "₹142.97" in msg and "Alert only" in msg
    assert "orders a buy" in turn_entry.alert_text("Bot 3", "X", "LONG", "n", None, entry=True)
    # Already on the turn's side: the bot keeps the trade and orders nothing, and the alert says so.
    same = turn_entry.alert_text("Bot 4", "DABUR", "SHORT", "n", 360.4, entry=True, held="SHORT")
    assert "already holds a short" in same and "orders" not in same
    rev = turn_entry.alert_text("Bot 4", "DABUR", "SHORT", "n", 360.4, entry=True, held="LONG")
    assert "closes its long and orders a short" in rev


def _v_day() -> pd.DataFrame:
    """Flat, a steady rise from 10:15 (SMA 9 about +0.025 %/candle), then a sharp fall from 12:15."""
    def path(i, p):
        if i < 60:
            return 1000.0 + (0.02 if i % 2 else -0.02)
        return p + 0.25 if i < 180 else p - 0.5

    flat = _day(dt.date(2026, 9, 21), lambda i, p: 1000.0 + (0.02 if i % 2 else -0.02), 1000.0)
    return pd.DataFrame(flat + _day(DAY, path, 1000.0))


async def _trades(database, **over):
    from sma_research.replayer import replay_symbol

    return await replay_symbol(_v_day(), "SIMT", [DAY], _settings(database, **over))


PEAK = dt.datetime(2026, 9, 22, 12, 15)


@pytest.mark.asyncio
async def test_turn_entry_shorts_at_the_v_before_the_cross(db):  # noqa: F811
    plain = await _trades(db)
    cross_short = next(t for t in plain if t["direction"] == "SHORT" and t["entry_time"] >= PEAK)
    trades = await _trades(db, turn_entry=True)
    turned = [t for t in trades if t["entry_reason"] == turn_entry.ENTRY_REASON]
    assert len(turned) == 1 and turned[0]["direction"] == "SHORT"
    assert PEAK < turned[0]["entry_time"] < cross_short["entry_time"]
    # The rise out of the flat morning is not a V (nothing fell first), so no long is taken on a turn.
    held = next(t for t in trades if t["exit_time"] == turned[0]["entry_time"] and t["direction"] == "LONG")
    assert held["entry_reason"] == "SMA_CROSS"  # the open long was closed and reversed, like an opposite cross


@pytest.mark.asyncio
async def test_off_by_default_and_the_alert_never_trades(db):  # noqa: F811
    key = lambda t: (t["direction"], t["entry_time"], t["exit_time"], t["exit_reason"])  # noqa: E731
    plain = [key(t) for t in await _trades(db)]
    assert [key(t) for t in await _trades(db, turn_entry=False, turn_alert=False)] == plain
    assert [key(t) for t in await _trades(db, turn_alert=True)] == plain


def test_engine_alerts_once_per_turn_and_not_an_old_one(db):  # noqa: F811
    from strategy_engine import StrategyEngine

    eng = StrategyEngine()
    sent: list[str] = []
    eng._alert = lambda message, critical=False, call=False: sent.append(message)
    cfg = SimpleNamespace(turn_alert=True, turn_entry=False, sma_fast=9, bot_name="Bot 3")
    up = [100 + i * 0.1 for i in range(30)]
    down = [up[-1] - (i + 1) * 0.4 for i in range(6)]
    closes = up + down
    frame = lambda n: pd.DataFrame({"ts": [60 * i for i in range(n + 1)], "close": closes[:n] + [closes[n - 1]]})  # noqa: E731
    seen = []
    for n in range(25, len(closes) + 1):
        hit = eng._turn_now("ASHOKLEY", frame(n), cfg)
        eng._turn_now("ASHOKLEY", frame(n), cfg)  # the same candle again: no second alert
        seen.append(hit)
    assert len(sent) == 1 and "ASHOKLEY" in sent[0] and "Alert only" in sent[0]
    assert sum(1 for h in seen if h) == 1
    # A stock first read on the turn candle itself is not alerted (it may be an old turn).
    fresh = StrategyEngine()
    fresh._alert = lambda message, critical=False, call=False: sent.append(message)
    turn_at = next(n for n in range(25, len(closes) + 1) if turn_entry.detect(pd.DataFrame({"close": closes[:n]}), 9, cfg))
    assert fresh._turn_now("ASHOKLEY", frame(turn_at), cfg) is None and len(sent) == 1


def test_api_saves_the_turn_switches_per_bot_and_per_stock(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/3/config").json()
        assert got["turn_alert"] is False and got["turn_entry"] is False
        ok = client.put("/api/bots/3/config", json={"turn_alert": True, "turn_entry": True})
        assert ok.status_code == 200 and ok.json()["turn_alert"] is True and ok.json()["turn_entry"] is True
        one = client.put("/api/bots/3/config/stock/ASHOKLEY", json={"turn_entry": False})
        assert one.status_code == 200
        assert client.get("/api/bots/3/config/stock/ASHOKLEY").json()["turn_entry"] is False
    database.reset_engine()


# ---- rounded U (uturn_on) ------------------------------------------------------------------------

import math  # noqa: E402

WAVE = [100 + 2 * math.sin(i / 12) for i in range(80)]  # a slow, rounded top then bottom


def _hits(closes, cfg, start=25):
    return [(n, h) for n in range(start, len(closes) + 1) if (h := turn_entry.detect(_closed(closes[:n]), 9, cfg))]


def test_rounded_u_is_caught_only_with_its_switch_and_before_the_v():
    plain = _hits(WAVE, SimpleNamespace())
    with_u = _hits(WAVE, SimpleNamespace(uturn_on=True))
    assert all(h[1].startswith("V:") for _, h in plain)
    u = [(n, h) for n, h in with_u if h[1].startswith("U:")]
    assert u and u[0][1][0] == "LONG" and "rounded bottom" in u[0][1][1]
    v_bottom = next(n for n, h in plain if h[0] == "LONG")
    assert u[0][0] < v_bottom  # the U shows a candle or more before the V would


def test_u_needs_a_real_run_first():
    drift = [100 + (0.001 if i % 2 else -0.001) for i in range(60)]  # wiggles under the 0.005 %/candle floor
    assert _hits(drift, SimpleNamespace(uturn_on=True)) == []
    short_run = [100] * 30 + [100 + i * 0.01 for i in range(1, 4)] + [100.03 - i * 0.01 for i in range(1, 10)]
    assert _hits(short_run, SimpleNamespace(uturn_on=True, uturn_run=8)) == []


def test_a_u_then_its_v_alerts_once(db):  # noqa: F811
    from strategy_engine import StrategyEngine

    eng = StrategyEngine()
    sent: list[str] = []
    eng._alert = lambda message, critical=False, call=False: sent.append(message)
    cfg = SimpleNamespace(turn_alert=True, turn_entry=False, sma_fast=9, bot_name="Bot 3", uturn_on=True)
    frame = lambda n: pd.DataFrame({"ts": [60 * i for i in range(n + 1)], "close": WAVE[:n] + [WAVE[n - 1]]})  # noqa: E731
    for n in range(40, len(WAVE) + 1):
        eng._turn_now("ASHOKLEY", frame(n), cfg)
    ups = [m for m in sent if m.startswith("▲")]
    assert len(ups) == 1 and "U: " in ups[0]


def test_api_saves_the_u_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/2/config").json()
        assert got["uturn_on"] is False and got["uturn_run"] == 5 and got["uturn_min_pct"] == 0.005
        ok = client.put("/api/bots/2/config", json={"uturn_on": True, "uturn_run": 8, "uturn_min_pct": 0.01})
        assert ok.status_code == 200 and ok.json()["uturn_run"] == 8
        assert client.put("/api/bots/2/config", json={"uturn_run": 1}).status_code == 422
    database.reset_engine()


# ---- only toward the cross, and closing on a candle of the other colour ------------------------------------------


def _bars(rows):
    """[(open, close, sma_fast, sma_slow), ...] plus one forming candle, one a minute."""
    out = [{"ts": 60 * i, "open": o, "close": c, "sma_fast": f, "sma_slow": s} for i, (o, c, f, s) in enumerate(rows)]
    out.append({**out[-1], "ts": out[-1]["ts"] + 60})
    return pd.DataFrame(out)


def test_toward_cross_and_candle_colour():
    above = _bars([(100, 101, 101.0, 100.0)]).iloc[:-1]
    assert turn_entry.toward_cross(above, "SHORT") and not turn_entry.toward_cross(above, "LONG")
    below = _bars([(100, 101, 99.0, 100.0)]).iloc[:-1]
    assert turn_entry.toward_cross(below, "LONG") and not turn_entry.toward_cross(below, "SHORT")
    assert not turn_entry.toward_cross(pd.DataFrame({"close": [1.0]}), "LONG")  # SMAs unknown: not toward
    green, red = {"open": 100, "close": 101}, {"open": 101, "close": 100}
    assert turn_entry.candle_against(red, "LONG") and not turn_entry.candle_against(green, "LONG")
    assert turn_entry.candle_against(green, "SHORT") and not turn_entry.candle_against(red, "SHORT")


def test_alert_says_away_and_wait():
    away = turn_entry.alert_text("Bot 4", "X", "LONG", "n", None, entry=True, away=True)
    assert "No order" in away and "away from the 9/21 cross" in away
    waits = turn_entry.alert_text("Bot 4", "X", "SHORT", "n", None, entry=True, held="LONG", waits=True)
    assert "keeps its long until a red candle closes" in waits


class _Eng:
    """The engine with apply_signal / _exit_now recorded instead of trading."""

    def __init__(self, held=None):
        from strategy_engine import StrategyEngine

        self.eng = StrategyEngine()
        self.calls: list = []
        if held:
            self.eng.positions["KPITTECH"] = SimpleNamespace(signal_direction=held, direction=held)

        async def apply(sig, frame, cfg, now, alert=True, pullback_at=None, why="SMA_CROSS"):
            self.calls.append(("apply", sig, why))
            return "reversed to SHORT" if sig == "BEARISH" else "opened LONG"

        async def exit_now(cfg, price, reason):
            self.calls.append(("exit", reason))
            self.eng.positions.pop("KPITTECH", None)

        self.eng.apply_signal = apply
        self.eng._exit_now = exit_now
        self.eng._exit_price = lambda symbol, pos: 544.0


CFG_T = SimpleNamespace(turn_entry=True, turn_toward_cross=True, turn_exit_candle=True, sma_fast=9, cross_exit=True)
NOW = dt.datetime(2026, 10, 28, 14, 20)


@pytest.mark.asyncio
async def test_an_up_turn_above_the_slow_sma_does_not_buy():
    e = _Eng()
    frame = _bars([(549, 550, 549.8, 543.8)])  # SMA 9 above SMA 21: an up turn points away from the cross
    text = await e.eng._turn_minute("KPITTECH", (0, "LONG", "V up"), frame, CFG_T, NOW)
    assert e.calls == [] and "away from the 9/21 cross" in text
    # A down turn there points toward the cross and sells.
    e2 = _Eng()
    await e2.eng._turn_minute("KPITTECH", (0, "SHORT", "V down"), frame, CFG_T, NOW)
    assert e2.calls == [("apply", "BEARISH", turn_entry.ENTRY_REASON)]


@pytest.mark.asyncio
async def test_a_turn_on_a_green_candle_waits_for_a_red_one_to_close_a_long():
    e = _Eng(held="LONG")
    green = _bars([(544.3, 544.4, 549.8, 543.8)])  # the turn down came on a green candle
    text = await e.eng._turn_minute("KPITTECH", (0, "SHORT", "V down"), green, CFG_T, NOW)
    assert e.calls == [] and "closes on the next red candle" in text and "KPITTECH" in e.eng._turn_wait
    # The same candle again: still waiting, nothing done.
    assert await e.eng._turn_wait_minute("KPITTECH", green, CFG_T, NOW) is None and e.calls == []
    # Another green candle (SMA 9 still falling): keeps waiting.
    falling = [549.9 - i * 0.2 for i in range(30)]
    more = pd.DataFrame(
        [{"ts": 60 * i, "open": 544.0, "close": 544.2, "sma_fast": 549.0, "sma_slow": 543.8} for i in range(30)]
    )
    more["close"] = falling  # closes fall, so the slope stays against the long
    more["open"] = more["close"] - 0.1  # green candles
    more["ts"] = more["ts"] + 120
    text = await e.eng._turn_wait_minute("KPITTECH", more, CFG_T, NOW)
    assert e.calls == [] and "still waiting for a red candle" in text
    # A red candle closes: the long is closed and reversed (the down turn points toward the cross).
    red = more.copy()
    red.loc[red.index[-2], "open"] = red.loc[red.index[-2], "close"] + 0.3
    red["ts"] = red["ts"] + 60
    await e.eng._turn_wait_minute("KPITTECH", red, CFG_T, NOW)
    assert e.calls == [("apply", "BEARISH", turn_entry.ENTRY_REASON)] and "KPITTECH" not in e.eng._turn_wait


@pytest.mark.asyncio
async def test_a_turn_away_from_the_cross_closes_without_a_reverse():
    e = _Eng(held="SHORT")
    above = _bars([(549, 550, 549.8, 543.8)])  # holding a short, SMA 9 above 21; a green candle goes against the short
    await e.eng._turn_minute("KPITTECH", (0, "LONG", "V up"), above, CFG_T, NOW)
    assert e.calls == [("exit", turn_entry.EXIT_REASON)]


@pytest.mark.asyncio
async def test_off_by_default_the_turn_acts_at_once():
    e = _Eng(held="LONG")
    cfg = SimpleNamespace(turn_entry=True, sma_fast=9, cross_exit=True)
    green = _bars([(544.3, 544.4, 549.8, 543.8)])
    await e.eng._turn_minute("KPITTECH", (0, "SHORT", "V down"), green, cfg, NOW)
    assert e.calls == [("apply", "BEARISH", turn_entry.ENTRY_REASON)]


def test_api_saves_the_new_turn_switches(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient

    import main

    with TestClient(main.app) as client:
        got = client.get("/api/bots/4/config").json()
        assert got["turn_toward_cross"] is False and got["turn_exit_candle"] is False
        ok = client.put("/api/bots/4/config", json={"turn_toward_cross": True, "turn_exit_candle": True}).json()
        assert ok["turn_toward_cross"] is True and ok["turn_exit_candle"] is True
    database.reset_engine()
