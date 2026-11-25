"""Trend hold test (trend_hold.py): the reads match the auto-arm's, the first strong read is followed to the square-off."""
import asyncio
import datetime as dt

import numpy as np
import pandas as pd

import auto_arm
import trend_hold

IST = trend_hold.IST
DAYS = [dt.date(2026, 11, 2) + dt.timedelta(days=i) for i in range(5)]  # Mon-Fri
DAY = DAYS[-1]


def _tape(turn_at: str | None = None) -> pd.DataFrame:
    """Four quiet sessions, then a day that climbs steadily from 09:15 (and falls from `turn_at`)."""
    rows = []
    rng = np.random.default_rng(1)
    for day in DAYS:
        price, total = 100.0, 0
        t = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
        for i in range(375):
            hhmm = t.strftime("%H:%M")
            if day == DAY:
                step = 0.05 if not (turn_at and hhmm >= turn_at) else -0.08
            else:
                step = float(rng.normal(0, 0.03))
            o = price
            price = max(1.0, price + step)
            total += 1000 if day != DAY else 3000
            rows.append({"ts": int(t.timestamp()), "open": o, "high": max(o, price) + 0.02, "low": min(o, price) - 0.02,
                         "close": price, "volume": total})
            t += dt.timedelta(minutes=1)
    return pd.DataFrame(rows)


def test_reads_match_the_auto_arm_strong_read():
    tape = _tape()
    rule = trend_hold.Rule(minutes=5).clean()
    times = [dt.time(10, 30), dt.time(12, 0)]
    reads = trend_hold.day_reads(tape, DAY, times, rule)
    for t in times:
        want = auto_arm.strong_read(tape, DAY, t, 5, 9, 21)
        got = reads[t.strftime("%H:%M")]
        assert got["trend"] == want["trend"]
        assert got["passed"] == sum(1 for v in want["trend_checks"].values() if v)


def test_strong_uptrend_is_bought_and_held_to_the_square_off():
    tape = _tape()
    rule = trend_hold.Rule(kind="TREND", min_checks=6, minutes=5, first=dt.time(10, 0), last=dt.time(14, 0),
                           every_min=15, qty=10).clean()
    rows = trend_hold.analyze_stock("UPCO", tape, [DAY], rule)
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "LONG" and "10:00" <= r["at"] <= "11:00" and r["passed"] >= 6
    assert r["held_to_end"] and r["lost_at"] is None
    assert r["points"] > 0 and r["pnl"] == round(r["points"] * 10, 2) and r["exit_at"] == "15:15"
    assert len(r["curve"]) == r["minutes"] and r["curve"][-1] > 0
    report = trend_hold.summarize(rows, rule)
    assert report["trades"] == 1 and report["held_pct"] == 100.0 and report["answer"]


def test_a_trend_that_turns_is_marked_lost_and_the_price_range_filters():
    tape = _tape(turn_at="12:00")
    rule = trend_hold.Rule(kind="TREND", min_checks=6, minutes=5, first=dt.time(10, 0), last=dt.time(14, 0),
                           every_min=15, qty=10).clean()
    r = trend_hold.analyze_stock("TURN", tape, [DAY], rule)[0]
    assert not r["held_to_end"] and "12:00" < r["lost_at"] <= "13:30"
    assert r["lost_pts"] is not None and r["lost_pts"] > r["points"]
    priced = trend_hold.Rule(minutes=5, price_min=500, first=dt.time(10, 0), every_min=15).clean()
    assert trend_hold.analyze_stock("TURN", tape, [DAY], priced) == []


def test_phase_rule_and_the_job():
    tape = _tape()
    rule = trend_hold.Rule(kind="PHASE", phase_min=5, minutes=5, first=dt.time(10, 30), every_min=15, qty=1).clean()
    rows = trend_hold.analyze_stock("UPCO", tape, [DAY], rule)
    assert rows and rows[0]["phase"] == "BULL" and rows[0]["side"] == "LONG"

    async def go():
        job = trend_hold.TrendHold.__new__(trend_hold.TrendHold)
        job.__init__ = None
        job.status, job.done, job.total, job.errors, job.params = "IDLE", 0, 0, [], {}
        job.started_at = job.finished_at = None
        job.report, job.rows, job._task, job._stop = None, [], None, False
        job._save = lambda: None

        async def fetch(name, a, b):
            return tape

        import strategy_lab

        async def load_tape(name, first, last, fetch, today=None):
            return tape

        orig = strategy_lab.load_tape
        strategy_lab.load_tape = load_tape
        try:
            job.start(["UPCO"], fetch, [DAY], rule)
            await job._task
        finally:
            strategy_lab.load_tape = orig
        return job

    job = asyncio.run(go())
    assert job.status == "DONE" and job.report["trades"] == 1 and job.rows[0]["symbol"] == "UPCO"
