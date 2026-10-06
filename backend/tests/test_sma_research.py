"""Stock-selection research: same trades as the app's replay, no lookahead, right maths."""
from __future__ import annotations

import datetime as dt
import random

import pandas as pd
import pytest

from groww_client import IST


def _walk(days: list[dt.date], seed: int = 1, trend: float = 0.25, noise: float = 0.6) -> pd.DataFrame:
    rng = random.Random(seed)
    rows, price = [], 1000.0
    for day in days:
        cum, drift = 0, 0.0
        t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        while t.time() < dt.time(15, 30):
            if rng.random() < 0.03:
                drift = rng.uniform(-trend, trend)
            o = price
            price += drift + rng.gauss(0, noise)
            cum += rng.randint(800, 3000)
            rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, price) + 0.2,
                         "low": min(o, price) - 0.2, "close": price, "volume": cum})
            t += dt.timedelta(minutes=1)
    return pd.DataFrame(rows)


DAYS = [dt.date(2026, 9, 21), dt.date(2026, 9, 22), dt.date(2026, 9, 23)]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/research.db")
    import database

    database.reset_engine()
    database.init_db()
    yield database
    database.reset_engine()


def _settings(database) -> dict:
    from models import BotConfig
    from sma_research.replayer import baseline_settings

    with database.session_factory()() as s:
        row = s.get(BotConfig, 1)
        row.use_vwap = True  # the baseline must switch extras off
        return baseline_settings({c.name: getattr(row, c.name) for c in BotConfig.__table__.columns})


def test_baseline_turns_the_extras_off(db):
    s = _settings(db)
    assert s["use_vwap"] is False and s["bb_exit"] == "OFF" and s["stock_settings"] == "{}"
    assert s["max_daily_loss"] >= 1e12 and s["sma_fast"] == 9 and s["sma_slow"] == 21


@pytest.mark.asyncio
async def test_minute_steps_while_flat_give_the_same_trades_as_10_second_steps(db):
    from sma_research.replayer import replay_symbol

    frame = _walk(DAYS)
    settings = _settings(db)
    fast = await replay_symbol(frame, "SIMA", DAYS[1:], settings)
    full = await replay_symbol(frame, "SIMA", DAYS[1:], settings, every_10s=True)

    def key(t):
        return (t["date"], t["direction"], t["entry_time"], t["exit_time"], t["entry_price"], t["exit_price"], t["exit_reason"])

    assert len(fast) > 5
    assert [key(t) for t in fast] == [key(t) for t in full]
    assert {t["date"] for t in fast} <= {d.isoformat() for d in DAYS[1:]}


def test_efficiency_and_crosses_tell_a_trend_from_a_zigzag():
    from sma_research.features import _efficiency, prepare

    straight = pd.DataFrame({"open": [100 + i for i in range(30)], "close": [101 + i for i in range(30)]})
    zigzag = pd.DataFrame({"open": [100.0] * 30, "close": [101.0 if i % 2 else 99.0 for i in range(30)]})
    assert _efficiency(straight) == pytest.approx(1.0)
    assert _efficiency(zigzag) < 0.1
    trend = prepare(_walk(DAYS[:1], seed=3, trend=0.6, noise=0.1))
    chop = prepare(_walk(DAYS[:1], seed=3, trend=0.0, noise=0.8))
    assert trend["cross"].sum() < chop["cross"].sum()


def test_before_the_open_readings_never_see_the_day_itself():
    from sma_research.features import prepare, stock_days

    frame = _walk(DAYS, seed=5)
    changed = frame.copy()
    last_day = int(dt.datetime.combine(DAYS[-1], dt.time(0, 0), tzinfo=IST).timestamp())
    changed.loc[changed["ts"] >= last_day, "close"] *= 1.5  # rewrite the judged day only
    a = stock_days(prepare(frame), "X", [DAYS[-1]])[0]
    b = stock_days(prepare(changed), "X", [DAYS[-1]])[0]
    pre = {k: v for k, v in a.items() if k.startswith("pre_")}
    assert pre and pre == {k: v for k, v in b.items() if k.startswith("pre_")}


def test_gap_change_is_measured_in_the_trade_direction():
    from sma_research.features import prepare, trade_features

    frame = _walk(DAYS[:1], seed=9)
    prepared = prepare(frame)
    i = 120
    entry = dt.datetime.fromtimestamp(int(prepared["ts"].iloc[i + 1]), IST).replace(tzinfo=None)
    long = trade_features(prepared, {"direction": "LONG", "entry_time": entry, "exit_time": None})
    short = trade_features(prepared, {"direction": "SHORT", "entry_time": entry, "exit_time": None})
    assert long["cross_bar_ts"] == int(prepared["ts"].iloc[i])
    assert long["gap_d1"] == pytest.approx(-short["gap_d1"])
    assert long["gap_entry"] == pytest.approx(-short["gap_entry"])


def _trades(rows):
    t = pd.DataFrame(rows, columns=["symbol", "date", "direction", "net"])
    t["gross"] = t["net"] + 10
    t["charges"] = 10.0
    t["exit_time"] = pd.date_range("2026-09-01 10:00", periods=len(t), freq="h")
    t["minutes_held"] = 5.0
    t["tid"] = range(len(t))
    return t


def test_metrics_profit_factor_drawdown_and_what_a_filter_removed():
    from sma_research.analysis import metrics

    base = _trades([("A", "d1", "LONG", 100.0), ("A", "d1", "SHORT", -40.0), ("B", "d1", "LONG", -60.0), ("A", "d2", "LONG", 50.0)])
    m = metrics(base)
    assert (m["trades"], m["wins"], m["losses"], m["long"], m["short"]) == (4, 2, 2, 3, 1)
    assert m["net"] == 50.0 and m["profit_factor"] == pytest.approx(150 / 100)
    assert m["max_drawdown"] == -100.0 and m["largest_loss"] == -60.0
    kept = metrics(base[base["symbol"] == "A"], base)
    assert (kept["avoided"], kept["losers_filtered_out"], kept["winners_filtered_out"]) == (1, 1, 0)


def test_experiments_choose_on_development_and_judge_on_hold_out():
    from sma_research import analysis as A

    days = [f"2026-09-{d:02d}" for d in range(1, 21)]
    rows, sd = [], []
    for i, day in enumerate(days):
        for sym, liquid, pnl in (("GOOD", 50.0, 80.0), ("BAD", 1.0, -70.0), ("MID", 20.0, 10.0)):
            for k in range(3):
                rows.append((sym, day, "LONG" if k % 2 else "SHORT", pnl + (k - 1) * 5))
            sd.append({"symbol": sym, "date": day, "pre_turnover_cr_med": liquid + i * 0.01})
    trades, table = _trades(rows), pd.DataFrame(sd)
    dev, hold = A.split_days(days, 0.6)
    out = A.greedy_experiments(trades, table, dev, hold, "pre")
    a = out[1]
    assert a["id"] == "A" and "pre_turnover_cr_med >=" in a["added"]
    assert a["holdout"]["losers_filtered_out"] > 0 and a["holdout"]["net"] > out[0]["holdout"]["net"]
    assert A.supported(a, out[0]["holdout"]) is True
    assert len(dev) == 12 and len(hold) == 8 and max(dev) < min(hold)
