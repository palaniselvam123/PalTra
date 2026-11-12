"""Hold for N days (hold_test.py): days to the profit %, drawdown before it, sessions skipped over weekends."""
import asyncio
import datetime as dt

import pandas as pd

import hold_test

IST = hold_test.IST


def _tape(days: dict[dt.date, list[tuple[str, float, float, float]]]) -> pd.DataFrame:
    rows = []
    for day, bars in days.items():
        for hhmm, high, low, close in bars:
            h, m = hhmm.split(":")
            ts = int(dt.datetime.combine(day, dt.time(int(h), int(m)), tzinfo=IST).timestamp())
            rows.append({"ts": ts, "open": close, "high": high, "low": low, "close": close, "volume": 1})
    return pd.DataFrame(rows)


THU, FRI, MON = dt.date(2026, 11, 5), dt.date(2026, 11, 6), dt.date(2026, 11, 9)


def test_buy_reaches_target_on_the_second_session_after_a_weekend():
    tape = _tape({
        THU: [("10:00", 100.2, 99.8, 100), ("10:01", 100.5, 99.0, 99.5)],
        FRI: [("09:15", 100.4, 98.5, 99.0)],
        MON: [("09:15", 100.9, 99.5, 100.8), ("09:16", 101.2, 100.5, 101.1)],
    })
    got = hold_test.follow(tape, THU, "10:00", "BUY", 100.0, 1.0, 5)
    assert got["reached"] and got["day_no"] == 2 and got["on"] == MON.isoformat() and got["hit_at"] == "09:16"
    assert got["worst_before_pct"] == -1.5  # Friday's low 98.5
    assert got["sessions"] == 2  # only two later sessions exist


def test_the_minute_the_scan_saw_it_is_not_counted_and_days_cap_the_hold():
    tape = _tape({
        THU: [("10:00", 105, 95, 100), ("10:01", 100.1, 99.9, 100)],
        FRI: [("09:15", 100.2, 99.9, 100.1)],
        MON: [("09:15", 103, 100, 102)],
    })
    got = hold_test.follow(tape, THU, "10:00", "BUY", 100.0, 1.0, 1)  # held Thursday + Friday only
    assert not got["reached"] and got["best_pct"] == 0.2 and got["end_pct"] == 0.1


def test_sell_side_and_summary():
    tape = _tape({THU: [("11:00", 100, 100, 100), ("11:01", 100.1, 99.4, 99.5)]})
    got = hold_test.follow(tape, THU, "11:00", "SELL", 100.0, 0.5, 3)
    assert got["reached"] and got["day_no"] == 0 and got["worst_before_pct"] == -0.1

    async def load(symbol, first, last):
        return tape

    out = asyncio.run(hold_test.run([{"symbol": "X", "day": THU.isoformat(), "at": "11:00", "side": "SELL", "price": 100}],
                                    load, days=3, target_pct=0.5))
    assert out["reached"] == 1 and out["by_day"][0]["by_then_pct"] == 100.0 and out["answer"]
