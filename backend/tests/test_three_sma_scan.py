"""Trend quality with a third SMA: stacked lines and widening gaps (chop_scan.three_read)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

import chop_scan
from groww_client import IST


def _bars(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"ts": [1_800_000_000 + i * 300 for i in range(len(closes))], "close": closes})


def test_a_steady_rise_then_faster_is_stacked_up_and_widening():
    closes = [100.0] * 12 + [100.0 + 0.02 * i * i for i in range(1, 40)]  # accelerating: every gap keeps growing
    out = chop_scan.three_read(_bars(closes), 3, 6, 12)
    assert out["stack3"] == "BULLISH" and out["sma_third"] == 12
    assert out["gap_fast_slow_pct"] > 0 and out["gap_slow_third_pct"] > 0
    assert out["widen_candles"] >= 5 and out["stack3_candles"] >= out["widen_candles"]


def test_a_fall_is_stacked_down_and_a_slowing_one_stops_widening():
    fall = [200.0 - 0.02 * i * i for i in range(40)]
    out = chop_scan.three_read(_bars([200.0] * 12 + fall), 3, 6, 12)
    assert out["stack3"] == "BEARISH" and out["gap_fast_slow_pct"] < 0 and out["widen_candles"] >= 5
    # The fall flattens: the lines are still stacked down, but the fast / slow gap closes, so no widening.
    flat = [fall[-1]] * 4
    out = chop_scan.three_read(_bars([200.0] * 12 + fall + flat), 3, 6, 12)
    assert out["stack3"] == "BEARISH" and out["widen_candles"] == 0 and out["stack3_candles"] > 0


def test_mixed_lines_unformed_third_and_off():
    # Up for long, then a sharp dip: fast under slow while slow is still over the third = not stacked.
    closes = [100.0 + i for i in range(40)] + [136.0, 135.0]
    out = chop_scan.three_read(_bars(closes), 3, 6, 12)
    assert out["stack3"] is None and out["widen_candles"] == 0 and out["gap_fast_slow_pct"] < 0 < out["gap_slow_third_pct"]
    short = chop_scan.three_read(_bars([100.0, 101.0, 102.0]), 3, 6, 12)
    assert short["stack3"] is None and short["gap_slow_third_pct"] is None
    assert chop_scan.three_read(_bars(closes), 3, 6, 0)["sma_third"] == 0


def test_pick_third_bounds():
    assert chop_scan.pick_third(0, 21) == 0 and chop_scan.pick_third(50, 21) == 50
    # A third between the two (a bot on SMA 9 / 50 with 21 in the middle) is fine; the same length is not.
    assert chop_scan.pick_third(21, 50, 9) == 21 and chop_scan.pick_third(10, 21) == 10
    for bad in (21, 1, 500):
        with pytest.raises(ValueError):
            chop_scan.pick_third(bad, 21)
    with pytest.raises(ValueError, match="SMA 9 and SMA 50"):
        chop_scan.pick_third(9, 50, 9)
    assert chop_scan.scan_download_days(60, 21, 50) >= chop_scan.window_days(60, 50)
    assert chop_scan.scan_download_days(5, 21, 0) == chop_scan.download_days(5, 21, chop_scan.SCAN_MINUTES)


def test_analyze_carries_the_three_sma_read_only_when_asked():
    start = dt.datetime(2026, 9, 28, 9, 15, tzinfo=IST)
    rows, price, vol = [], 100.0, 0
    for day in range(3):
        for i in range(375):
            ts = int((start + dt.timedelta(days=day, minutes=i)).timestamp())
            nxt = price + 0.03
            vol = (0 if i == 0 else vol) + 1000
            rows.append({"ts": ts, "open": price, "high": nxt + 0.01, "low": price - 0.01, "close": nxt, "volume": vol})
            price = nxt
    frame = pd.DataFrame(rows)
    now = start + dt.timedelta(days=2, hours=5)
    frame = frame[frame["ts"] <= int(now.timestamp())]
    plain = chop_scan.analyze(frame, 9, 21, 5, now, symbol="ACME")
    assert plain.stack3 is None and plain.sma_third == 0
    row = chop_scan.analyze(frame, 9, 21, 5, now, symbol="ACME", third=50)
    assert row.sma_third == 50 and row.stack3 == "BULLISH" and row.stack3_candles > 0
    assert row.as_dict()["gap_slow_third_pct"] > 0
    # A colour read uses no SMAs: the three-SMA read is left out.
    assert chop_scan.analyze(frame, 9, 21, 5, now, symbol="ACME", third=50, colour=True).stack3 is None


def test_the_start_route_takes_and_checks_the_third_sma(tmp_path, monkeypatch):
    monkeypatch.setenv("SMA_DATABASE_URL", f"sqlite:///{tmp_path}/api.db")
    monkeypatch.setenv("CHOP_AUTO_FILE", str(tmp_path / "auto.json"))
    monkeypatch.delenv("GROWW_ACCESS_TOKEN", raising=False)
    import database

    database.reset_engine()
    database.init_db()
    from fastapi.testclient import TestClient
    from main import app

    with TestClient(app) as client:
        bad = client.post("/api/chop-scan/start", json={"symbols": ["SBIN"], "sma_third": 21})
        assert bad.status_code == 400 and "third SMA" in bad.json()["detail"]
        saved = client.put("/api/chop-scan/auto", json={"sma_third": 50}).json()
        assert saved["sma_third"] == 50
        # The same length as one of the scan's own SMAs is dropped; one in between is kept (put in order).
        assert client.put("/api/chop-scan/auto", json={"sma_fast": 20, "sma_slow": 50, "sma_third": 50}).json()["sma_third"] == 0
        assert client.put("/api/chop-scan/auto", json={"sma_fast": 20, "sma_slow": 50, "sma_third": 30}).json()["sma_third"] == 30
    database.reset_engine()


def test_three_read_puts_the_lengths_in_order():
    """SMA 9 / 50 with a third of 21 reads the same as 9 / 21 / 50 (owner's bot: Fast 9, Medium 21, Slow 50)."""
    closes = [100.0 + i * 0.3 + (i % 4) * 0.05 for i in range(120)]
    a = chop_scan.three_read(_bars(closes), 9, 50, 21)
    b = chop_scan.three_read(_bars(closes), 9, 21, 50)
    keys = ("stack3", "gap_fast_slow_pct", "gap_slow_third_pct", "stack3_candles", "widen_candles")
    assert a["stack3"] == "BULLISH" and {k: a[k] for k in keys} == {k: b[k] for k in keys}
    assert a["sma_third"] == 21
    assert chop_scan.three_read(_bars(closes), 9, 21, 21)["stack3"] is None
