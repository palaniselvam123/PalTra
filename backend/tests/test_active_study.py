"""Most active trend test (active_study.py): which column's top stocks kept trending, from saved scans."""
from __future__ import annotations

import datetime as dt

import pytest

import active_study as st

IST = st.IST


def _row(symbol, ltp, change, rvol=1.0, vwap=0.0, bias="NONE", value=10.0):
    return {"symbol": symbol, "ltp": ltp, "change_pct": change, "rvol": rvol, "vwap_dist_pct": vwap, "bias": bias,
            "value_cr": value, "buy_share": 0.5, "speed_pct_min": None, "range_pct": 1.0}


class FakeHistory:
    """Days of saved scans: at 10:00 every stock is up; the high-volume ones keep rising, the rest fall back."""

    def __init__(self, days=("2026-10-26", "2026-10-27", "2026-10-28"), names=12, close=True):
        self.store: dict[int, dict] = {}
        self.meta: dict[str, list[dict]] = {}
        rid = 0
        for day in days:
            self.meta[day] = []
            stamps = [("10:02", 0.0), ("11:01", 0.5), *([("15:25", 1.0)] if close else [])]
            for clock, step in stamps:
                rid += 1
                rows = []
                for i in range(names):
                    hot = i < names // 2
                    price = 100 * (1 + (0.02 if hot else -0.02) * step)
                    rows.append(_row(f"S{i:02d}", price, 1.0 + i * 0.01, rvol=3.0 if hot else 0.8,
                                     vwap=0.5, bias="LONG"))
                as_of = dt.datetime.fromisoformat(f"{day}T{clock}:00+05:30").isoformat()
                self.store[rid] = {"id": rid, "as_of": as_of, "rows": rows}
                self.meta[day].append({"id": rid, "as_of": as_of})

    def days(self, kind, limit=120):
        return [{"day": d, "runs": len(r)} for d, r in sorted(self.meta.items(), reverse=True)][:limit]

    def runs(self, day, kind):
        return self.meta.get(day, [])

    def get(self, rid):
        return self.store.get(rid)


@pytest.fixture(autouse=True)
def _fresh_cache():
    st._cache.clear()


def test_check_times_are_cleaned():
    assert st.clean_times(["11:00", "10:00", "10:00"]) == ["10:00", "11:00"]
    for bad in (["09:00"], ["15:00"], ["x"], []):
        with pytest.raises(ValueError):
            st.clean_times(bad)


def test_the_nearest_scan_and_the_close():
    runs = [{"id": 1, "as_of": "2026-10-26T10:07:00+05:30"}, {"id": 2, "as_of": "2026-10-26T15:12:00+05:30"},
            {"id": 3, "as_of": "2026-10-26T15:24:00+05:30"}]
    at = dt.datetime(2026, 10, 26, 10, 0, tzinfo=IST)
    assert st.nearest(runs, at)["id"] == 1
    assert st.nearest(runs, at.replace(hour=12)) is None
    assert st.close_run(runs)["id"] == 3
    assert st.close_run(runs[:1]) is None


def test_volume_vs_usual_is_found_as_the_trend_column():
    out = st.run(10, ["10:00"], top=3, history=FakeHistory(days=[f"2026-10-{d:02d}" for d in range(1, 13)]))
    assert out["samples"] == 10 * 12  # the last 10 of 12 days
    overall = next(t for t in out["tables"] if t["time"] == "ALL" and t["side"] == "BOTH")
    assert overall["base"]["close_hit"] == 50.0
    first = overall["params"][0]
    assert first["key"] in ("rvol", "combined")
    rvol = next(p for p in overall["params"] if p["key"] == "rvol")
    assert rvol["top"]["close_hit"] == 100.0 and rvol["bottom"]["close_hit"] == 0.0
    assert rvol["edge_close"] == 50.0 and rvol["spread_close"] == 100.0 and rvol["verdict"] == "strong"
    assert rvol["top"]["hour_hit"] == 100.0
    # The bias agrees for every stock: no "weak" group, so its top is the whole side (the plain rate).
    bias = next(p for p in overall["params"] if p["key"] == "bias")
    assert bias["edge_close"] == 0.0
    # Every stock has the same buyer share: that column picks nobody.
    pressure = next(p for p in overall["params"] if p["key"] == "pressure")
    assert pressure["top"]["n"] == 0 and pressure["verdict"] == "few"
    assert any("Best column overall" in line for line in out["answer"])
    assert any("Best time to look: 10:00" in line for line in out["answer"])


def test_bearish_moves_are_signed_their_way():
    hist = FakeHistory(days=["2026-10-26"], names=4)
    for run in hist.store.values():
        for r in run["rows"]:
            r["change_pct"] = -abs(r["change_pct"])
    out = st.run(5, ["10:00"], top=1, history=hist)
    short = next(t for t in out["tables"] if t["time"] == "ALL" and t["side"] == "SHORT")
    # Falling stocks that fell further are "kept going": the cold half (−2%) wins for a bearish read.
    assert short["base"]["close_n"] == 4 and short["base"]["close_hit"] == 50.0
    assert next(t for t in out["tables"] if t["side"] == "LONG" and t["time"] == "ALL")["base"]["n"] == 0


def test_a_day_without_a_closing_scan_has_no_close_result_and_few_says_so():
    out = st.run(5, ["10:00"], top=3, history=FakeHistory(close=False))
    overall = next(t for t in out["tables"] if t["time"] == "ALL" and t["side"] == "BOTH")
    assert overall["base"]["close_n"] == 0 and overall["base"]["hour_n"] > 0
    assert out["answer"][0].startswith("No saved Most active scans")
    assert all(not d["closed"] for d in out["days"])


def test_desk_route_reads_saved_scans(tmp_path, monkeypatch):
    monkeypatch.setenv("SCAN_HISTORY_FILE", str(tmp_path / "hist.db"))
    import scan_history

    hist = FakeHistory(days=["2026-10-26"], names=6)
    for run in hist.store.values():
        scan_history.save("active", {"as_of": run["as_of"], "rows": run["rows"]}, {"source": "test"})

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api import routes_scalp

    app = FastAPI()
    app.include_router(routes_scalp.router)
    with TestClient(app) as client:
        got = client.get("/api/scalp/active/study", params={"days": 5, "times": "10:00,11:00", "top": 3}).json()
        assert got["samples"] == 12 and got["days"][0]["closed"] is True
        assert client.get("/api/scalp/active/study", params={"times": "16:00"}).status_code == 400
