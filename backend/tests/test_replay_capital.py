"""Money a replay run needed: the peak value of trades open at the same moment."""
from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from replay import _capital


def _trade(day: str, start: str, end: str | None, price: float, qty: int, symbol: str = "AAA"):
    d = dt.date.fromisoformat(day)
    at = lambda hhmm: dt.datetime.combine(d, dt.time(*map(int, hhmm.split(":"))))  # noqa: E731
    return SimpleNamespace(
        date=day, symbol=symbol, entry_time=at(start), exit_time=at(end) if end else None,
        entry_price=price, qty=qty,
    )


def test_overlapping_trades_add_up_and_the_peak_names_its_moment():
    cap = _capital([
        _trade("2026-10-05", "09:30", "10:00", 100.0, 10),          # 1,000
        _trade("2026-10-05", "09:45", "10:30", 200.0, 10, "BBB"),   # 2,000 → 3,000 together at 09:45
        _trade("2026-10-05", "10:15", "10:45", 500.0, 2, "CCC"),    # 1,000 + 2,000 = 3,000, not higher
    ])
    assert cap["peak_value"] == 3000.0
    assert cap["peak_positions"] == 2
    assert cap["peak_at"].startswith("2026-10-05T09:45")


def test_a_close_and_an_entry_at_the_same_minute_do_not_double_count():
    cap = _capital([
        _trade("2026-10-05", "09:30", "10:00", 100.0, 10),
        _trade("2026-10-05", "10:00", "10:30", 100.0, 10),   # stop-and-reverse at 10:00
    ])
    assert cap["peak_value"] == 1000.0 and cap["peak_positions"] == 1


def test_each_day_has_its_own_peak_and_the_run_keeps_the_largest():
    cap = _capital([
        _trade("2026-10-05", "09:30", "10:00", 100.0, 10),
        _trade("2026-10-06", "09:30", "10:00", 300.0, 10),
    ])
    assert [d["peak_value"] for d in cap["days"]] == [1000.0, 3000.0]
    assert cap["peak_value"] == 3000.0


def test_a_short_needs_money_too_and_an_open_trade_counts_to_the_days_end():
    cap = _capital([
        _trade("2026-10-05", "09:30", None, 100.0, -5),       # qty sign never lowers the money
        _trade("2026-10-05", "14:00", "14:30", 100.0, 5, "BBB"),
    ])
    assert cap["peak_value"] == 1000.0 and cap["peak_positions"] == 2


def test_no_trades_needs_no_money():
    assert _capital([]) == {"peak_value": 0.0, "peak_at": None, "peak_positions": 0, "days": []}
