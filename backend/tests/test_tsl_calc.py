"""Trailing stop / target sized from the candle, per candle size (tsl_calc.py, GET /api/tsl-calc)."""
from __future__ import annotations

import datetime as dt

import pandas as pd

import tsl_calc
from groww_client import IST


def _tape(minutes: int = 400, step: float = 0.8) -> pd.DataFrame:
    start = dt.datetime(2026, 10, 29, 9, 15, tzinfo=IST)
    rows = []
    price = 1000.0
    for i in range(minutes):
        o = price
        c = price + (step if i % 3 else -step * 1.5)
        rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": o, "high": max(o, c) + 0.6, "low": min(o, c) - 0.6, "close": c, "volume": 100})
        price = c
    return pd.DataFrame(rows)


def test_bigger_candles_give_wider_stops_in_rupees_and_tick_rounded():
    out = tsl_calc.suggest("TCS", _tape(), sizes=(1, 5, 15), stop_mult=1.5, trail_mult=0.5, target_mult=3.0)
    assert out["symbol"] == "TCS" and out["price"] > 0 and out["source"] if "source" in out else True
    by = {row["minutes"]: row for row in out["sizes"]}
    assert set(by) == {1, 5, 15}
    assert by[1]["atr"] < by[5]["atr"] < by[15]["atr"]
    assert by[5]["stop"] == round(by[5]["stop"] / 0.05) * 0.05 or abs(by[5]["stop"] - round(by[5]["stop"] / 0.05) * 0.05) < 1e-9
    assert by[5]["target"] > by[5]["stop"] > by[5]["trail"] > 0
    assert abs(by[5]["stop_pct"] - by[5]["stop"] / out["price"] * 100) < 0.01
    # A size with too few closed candles for the ATR says so instead of guessing.
    short = tsl_calc.suggest("TCS", _tape(minutes=40), sizes=(1, 15))
    assert next(r for r in short["sizes"] if r["minutes"] == 15)["atr"] is None
    assert tsl_calc.suggest("TCS", None)["error"]


def test_route_reads_the_bots_tape_and_checks_its_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/calc.db")
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    import main

    with TestClient(main.app) as client:
        main.engine._tapes["TCS"] = _tape()  # after boot: the start-up resets the engine's tapes
        res = client.get("/api/tsl-calc", params={"symbol": "tcs", "sizes": "1,5,99", "stop_mult": 2})
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["source"] == "bot tape" and [r["minutes"] for r in body["sizes"]] == [1, 5]
        assert body["multiples"]["stop"] == 2.0
        assert client.get("/api/tsl-calc", params={"symbol": "TCS", "atr_period": 1}).status_code == 400
        assert client.get("/api/tsl-calc", params={"symbol": "TCS", "stop_mult": 99}).status_code == 400
    main.engine._tapes.pop("TCS", None)
    database.reset_engine()
