"""NSE delivery % reader (app.services.delivery): parsing, walking back to published days, averages."""
import datetime as dt

import pytest

from app.services import delivery

HEAD = "SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER\n"


def _file(rows):
    return HEAD + "".join(
        f"{s}, {ser}, 04-Nov-2026, 100, 100, 101, 99, 100, {c}, 100, {t}, 1, 1, {d}, {p}\n" for s, ser, c, t, d, p in rows
    )


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setenv("DELIVERY_CACHE_DIR", str(tmp_path))
    delivery.clear_memory()
    yield
    delivery.clear_memory()


def test_parse_keeps_eq_rows_and_handles_dashes():
    rows = delivery.parse(_file([
        ("SBIN", "EQ", 800.5, 1000, 400, 40.0),
        ("SBIN", "BE", 1, 10, 5, 50.0),
        ("NEWCO", "EQ", 50, 2000, "-", "-"),
    ]))
    assert rows["SBIN"] == {"traded": 1000, "delivered": 400, "pct": 40.0, "close": 800.5}
    assert rows["NEWCO"]["delivered"] is None and rows["NEWCO"]["pct"] is None


def test_report_walks_back_over_weekend_and_unpublished_day():
    # Thu 5 Nov (today, not published yet), Wed 4, Tue 3, Mon 2, (weekend), Fri 30 Oct
    files = {
        dt.date(2026, 11, 4): _file([("SBIN", "EQ", 1, 1000, 600, 60.0), ("TCS", "EQ", 1, 100, 10, 10.0)]),
        dt.date(2026, 11, 3): _file([("SBIN", "EQ", 1, 1000, 400, 40.0)]),
        dt.date(2026, 11, 2): _file([("SBIN", "EQ", 1, 1000, 300, 30.0)]),
        dt.date(2026, 10, 30): _file([("SBIN", "EQ", 1, 1000, 200, 20.0)]),
    }
    asked = []

    def fetch(day):
        asked.append(day)
        return files.get(day)

    out = delivery.report(["sbin", "TCS", "NOPE"], days=4, today=dt.date(2026, 11, 5), fetch=fetch)
    assert out["day"] == "2026-11-04"
    assert out["days"] == ["2026-11-04", "2026-11-03", "2026-11-02", "2026-10-30"]
    sbin = out["rows"]["SBIN"]
    assert sbin["pct"] == 60.0 and sbin["intraday"] == 400
    assert sbin["avg_pct"] == 30.0 and sbin["vs_avg"] == 30.0
    assert out["rows"]["TCS"]["avg_pct"] is None
    assert "NOPE" not in out["rows"]
    assert dt.date(2026, 10, 31) not in asked and dt.date(2026, 11, 1) not in asked  # weekend skipped

    # second call: everything from memory, the missing day not asked again yet
    asked.clear()
    delivery.report(["SBIN"], days=4, today=dt.date(2026, 11, 5), fetch=fetch)
    assert asked == []


def test_saved_file_is_reused_after_restart(tmp_path):
    day = dt.date(2026, 11, 4)
    delivery.load_day(day, lambda d: _file([("SBIN", "EQ", 1, 100, 50, 50.0)]))
    assert (tmp_path / "delivery_04112026.csv").exists()
    delivery.clear_memory()

    def boom(d):
        raise AssertionError("should read the saved file")

    assert delivery.load_day(day, boom)["SBIN"]["pct"] == 50.0


def test_download_error_is_reported_not_raised():
    def fail(day):
        raise OSError("blocked")

    out = delivery.report(None, days=2, today=dt.date(2026, 11, 5), fetch=fail)
    assert out["day"] is None and out["rows"] == {}
    assert any("blocked" in e for e in out["errors"])
