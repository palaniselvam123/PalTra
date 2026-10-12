"""Chop scan (chop_scan.py): count today's SMA crosses, label TRENDING / MIXED / CHOPPY.

Read-only. The fetch is stubbed with synthetic 1-minute candles; no network, no
Groww call, and no broker path is touched.
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pandas as pd
import pytest

import chop_scan

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
# A weekday with the market open; the activity reader uses it to pick "today".
DAY = dt.date(2026, 10, 8)


def _minutes(closes_by_minute: list[float], day: dt.date = DAY) -> pd.DataFrame:
    start = dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST)
    rows, prev = [], closes_by_minute[0]
    for i, c in enumerate(closes_by_minute):
        rows.append(
            {
                "ts": int((start + dt.timedelta(minutes=i)).timestamp()),
                "open": prev,
                "high": max(prev, c) + 0.05,
                "low": min(prev, c) - 0.05,
                "close": c,
                "volume": 1000 * (i + 1),
            }
        )
        prev = c
    return pd.DataFrame(rows)


def _at(hh: int, mm: int, day: dt.date = DAY) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hh, mm), tzinfo=IST)


# Score rule: a few plain cases that pin the TRENDING / MIXED / CHOPPY boundary.
@pytest.mark.parametrize(
    "crosses,minutes_since,avg_gap,expected",
    [
        (0, None, None, chop_scan.TRENDING),           # no crosses yet today
        (1, 90.0, None, chop_scan.TRENDING),           # one cross, long run since
        (1, 30.0, None, chop_scan.MIXED),              # one cross, still too fresh
        (2, 20.0, 45.0, chop_scan.MIXED),              # two crosses, well spaced
        (3, 10.0, 10.0, chop_scan.CHOPPY),             # short gaps between crosses
        (4, 5.0, 20.0, chop_scan.CHOPPY),              # count alone pushes it to CHOPPY
        (2, 10.0, 7.0, chop_scan.CHOPPY),              # two very close crosses -> CHOPPY
    ],
)
def test_score_rule_puts_each_stock_in_the_right_bucket(crosses, minutes_since, avg_gap, expected):
    assert chop_scan.score_for(crosses, minutes_since, avg_gap) == expected


def test_analyze_counts_todays_crosses_on_the_five_minute_candle_and_reads_trending():
    # A clean uptrend: price slopes up all morning, SMA 9 crosses SMA 21 once around 10:00
    # and then runs away. One cross, 60+ minutes since = TRENDING.
    tape: list[float] = []
    for i in range(300):  # 09:15 to 14:15 IST
        tape.append(100 + i * 0.1)
    frame = _minutes(tape)
    now = _at(14, 20)
    row = chop_scan.analyze(frame, 9, 21, 5, now, symbol="TRND")
    assert row is not None
    assert row.symbol == "TRND" and row.minutes == 5
    assert row.crosses_today <= 1 and row.score == chop_scan.TRENDING
    assert row.ltp == pytest.approx(100 + 299 * 0.1, abs=0.01)


def test_analyze_labels_a_wiggly_tape_as_choppy():
    # Zigzag price, swings every 10 one-minute candles. Many SMA crosses in the day.
    tape: list[float] = []
    for i in range(360):  # 09:15 to 15:15 IST
        tape.append(100 + ((i // 10) % 2) * 2)  # 100, 100, …, 102, 102, …, 100, …
    frame = _minutes(tape)
    now = _at(15, 20)
    row = chop_scan.analyze(frame, 9, 21, 5, now, symbol="CHOP")
    assert row is not None
    assert row.crosses_today >= chop_scan.CHOP_MIN_CROSSES
    assert row.score == chop_scan.CHOPPY


def test_analyze_returns_none_when_the_tape_is_too_short_for_the_slow_sma():
    frame = _minutes([100 + i * 0.1 for i in range(30)])
    assert chop_scan.analyze(frame, 9, 21, 5, _at(9, 45), symbol="SHORT") is None


def test_scanner_runs_once_at_a_time_and_sorts_rows_by_score():
    async def go():
        trend = _minutes([100 + i * 0.1 for i in range(300)])
        chop = _minutes([100 + ((i // 10) % 2) * 2 for i in range(300)])
        tapes = {"TRND": trend, "CHOP": chop}

        async def fetch(symbol, start, end):
            return tapes[symbol]

        s = chop_scan.ChopScanner()
        s.start(["TRND", "CHOP"], fetch, minutes=5, now=_at(14, 20), force=True)
        for _ in range(400):
            if not s.status.running:
                break
            await asyncio.sleep(0.01)
        snap = s.snapshot()
        assert not snap["running"] and snap["done"] == 2
        kinds = [row["score"] for row in snap["rows"]]
        # TRENDING comes before CHOPPY in the sort.
        assert kinds and kinds.index(chop_scan.TRENDING) < kinds.index(chop_scan.CHOPPY)
        return snap

    asyncio.run(go())


def test_scanner_reports_what_broke_when_nothing_can_be_read():
    async def go():
        async def fetch(symbol, start, end):
            raise RuntimeError("no Groww session")

        s = chop_scan.ChopScanner()
        s.start(["A", "B", "C", "D", "E", "F", "G"], fetch, minutes=5, now=_at(14, 20), force=True)
        for _ in range(500):
            if not s.status.running:
                break
            await asyncio.sleep(0.01)
        snap = s.snapshot()
        assert not snap["running"] and snap["rows"] == []
        assert snap["error"] and "Groww" in snap["error"]

    asyncio.run(go())


# --- quiet-runner alert -------------------------------------------------------


def test_analyze_marks_a_stock_quiet_only_when_it_had_crosses_and_then_went_silent():
    # No crosses at all: not a quiet runner.
    tape = _minutes([100 + i * 0.1 for i in range(300)])
    row = chop_scan.analyze(tape, 9, 21, 5, _at(14, 20), symbol="TRND", quiet_min=60)
    assert row is not None and row.crosses_today <= 1
    # If there was no cross, the stock is not a quiet runner (it never ran).
    if row.crosses_today == 0:
        assert row.quiet_runner is False
    # A choppy day: many crosses, the last one is recent, so not quiet either.
    chop = _minutes([100 + ((i // 10) % 2) * 2 for i in range(300)])
    chop_row = chop_scan.analyze(chop, 9, 21, 5, _at(14, 20), symbol="CHOP", quiet_min=60)
    assert chop_row is not None and chop_row.quiet_runner is False


def test_quiet_runner_text_names_the_stock_and_time_and_the_side():
    text = chop_scan.quiet_runner_text("TCS", 85.0, "BULLISH", 2110.5)
    assert "TCS" in text and "1h 25m" in text and "Bullish" in text and "2110.50" in text


def test_scanner_fires_the_alert_once_per_quiet_stock_per_day(monkeypatch):
    import datetime as dt2

    import pytest as _pytest

    class _Open:
        def __call__(self, _now):
            return True

    monkeypatch.setattr(chop_scan, "market_is_open", _Open())
    # No cross and so flagged as a quiet runner only when its minutes-since reads over the threshold.
    # Build a quiet runner by hand so the test does not depend on the SMA walk.
    sample_row = chop_scan.ChopRow(
        symbol="QUIET",
        minutes=5,
        crosses_today=1,
        minutes_since_last_cross=90.0,
        avg_minutes_between=None,
        avg_move_pct=None,
        last_cross_direction="BULLISH",
        score=chop_scan.TRENDING,
        quiet_runner=True,
        ltp=100.0,
        candle_ts=1,
    )
    chop_scan._ALERTED_TODAY.clear()

    now = dt.datetime(2026, 10, 8, 11, 0, tzinfo=IST)
    sent: list[str] = []
    # Simulate the end-of-run alert path.
    for row in [sample_row]:
        if row.quiet_runner and not chop_scan._already_alerted(row.symbol, now):
            sent.append(chop_scan.quiet_runner_text(row.symbol, row.minutes_since_last_cross or 0, row.last_cross_direction, row.ltp))
            chop_scan._mark_alerted(row.symbol, now)
    # Second time: no new alert for the same stock today.
    for row in [sample_row]:
        if row.quiet_runner and not chop_scan._already_alerted(row.symbol, now):
            sent.append(chop_scan.quiet_runner_text(row.symbol, row.minutes_since_last_cross or 0, row.last_cross_direction, row.ltp))
            chop_scan._mark_alerted(row.symbol, now)
    assert len(sent) == 1 and "QUIET" in sent[0]
    # A fresh day: the alert fires again.
    assert not chop_scan._already_alerted(sample_row.symbol, now + dt2.timedelta(days=1))


def test_crosses_are_counted_on_every_candle_size_for_the_no_cross_filter():
    """A steady rise all day crosses on no candle size; a wobbling one crosses on the short ones."""
    import math

    from candles import CANDLE_MINUTES

    yesterday = [100 + i * 0.05 for i in range(375)]
    steady = _minutes(yesterday, dt.date(2026, 10, 7)).pipe(
        lambda y: pd.concat([y, _minutes([yesterday[-1] + i * 0.05 for i in range(200)])], ignore_index=True)
    )
    row = chop_scan.analyze(steady, 9, 21, 5, _at(12, 40), symbol="UP")
    assert row is not None
    assert set(row.crosses_by_minutes) == set(CANDLE_MINUTES)
    assert all(row.crosses_by_minutes[m] == 0 for m in CANDLE_MINUTES)
    assert all(row.side_by_minutes[m] == "BULLISH" for m in CANDLE_MINUTES)
    assert row.as_dict()["crosses_by_minutes"][5] == row.crosses_today == 0

    wobble = [100 + 0.05 * i + 0.6 * math.sin(i / 3) for i in range(200)]  # fast wiggles, slow drift up
    row = chop_scan.analyze(_minutes(wobble), 9, 21, 5, _at(12, 40), symbol="WOB")
    assert row is not None
    assert row.crosses_by_minutes[1] > 0
    assert row.crosses_by_minutes[1] >= row.crosses_by_minutes[5]


def test_a_size_with_too_few_candles_reads_none():
    row = chop_scan.analyze(_minutes([100 + i * 0.1 for i in range(60)]), 9, 21, 1, _at(10, 20), symbol="NEW")
    assert row is not None
    assert row.crosses_by_minutes[15] is None and row.side_by_minutes[15] is None


def test_a_single_cross_today_carries_its_time_and_direction_on_each_size():
    """Fell all of yesterday, turned up at 10:30 today and kept rising: one bullish cross per size."""
    import datetime as _dt

    from candles import CANDLE_MINUTES

    yesterday = [200 - i * 0.05 for i in range(375)]
    start = yesterday[-1]
    today = [start - i * 0.05 for i in range(75)] + [start - 3.7 + i * 0.12 for i in range(125)]  # 09:15-10:30 down, then up
    frame = pd.concat([_minutes(yesterday, dt.date(2026, 10, 7)), _minutes(today)], ignore_index=True)
    row = chop_scan.analyze(frame, 9, 21, 5, _at(12, 35), symbol="TURN")
    assert row is not None
    for m in (1, 5, 15):
        assert row.crosses_by_minutes[m] == 1, (m, row.crosses_by_minutes)
        assert row.last_cross_side_by_minutes[m] == "BULLISH"
        when = _dt.datetime.fromtimestamp(row.last_cross_ts_by_minutes[m], IST).time()
        assert _dt.time(10, 30) <= when <= _dt.time(12, 15), (m, when)  # longer candles cross later
    assert set(row.last_cross_ts_by_minutes) == set(CANDLE_MINUTES)
    assert row.minutes_since_last_cross is not None  # the 5-minute candle is the card's own


def test_no_cross_means_no_cross_time():
    row = chop_scan.analyze(_minutes([100 + i * 0.1 for i in range(200)]), 9, 21, 5, _at(12, 40), symbol="UP")
    assert row is not None and row.last_cross_ts_by_minutes[5] is None and row.last_cross_side_by_minutes[5] is None


def test_many_quiet_runners_send_one_summary_alert_once_a_day(monkeypatch):
    """A low "quiet for" setting can flag hundreds of stocks: one Telegram per scan, not one per stock."""
    import asyncio

    monkeypatch.setattr(chop_scan, "_ALERTED_TODAY", {})
    monkeypatch.setattr(chop_scan, "market_is_open", lambda now: True)
    names = [f"S{i}" for i in range(40)]

    async def job(symbol, start, end):
        return chop_scan.ChopRow(
            symbol=symbol, minutes=5, crosses_today=1, minutes_since_last_cross=90.0 + int(symbol[1:]),
            avg_minutes_between=None, avg_move_pct=None, last_cross_direction="BULLISH", score=chop_scan.TRENDING,
            quiet_runner=True, ltp=100.0, candle_ts=0,
        )

    sent: list[str] = []

    async def go(scanner, when):
        scanner.start(names, None, job=job, quiet_min=60, alert_on_quiet=True, alert_cb=sent.append, force=True, now=when)
        await scanner._task

    asyncio.run(go(chop_scan.ChopScanner(), _at(12, 0)))
    assert len(sent) == 1
    assert sent[0].startswith("QUIET RUNNERS · 40 stocks") and "S39 ↑" in sent[0] and "and 25 more" in sent[0]
    asyncio.run(go(chop_scan.ChopScanner(), _at(12, 30)))
    assert len(sent) == 1  # every one of them was already alerted today


def test_in_price_range_bounds_and_zero_means_open():
    assert chop_scan.in_price_range(None)
    assert chop_scan.in_price_range(150.0, 100, 200)
    assert not chop_scan.in_price_range(99.0, 100, 200)
    assert not chop_scan.in_price_range(201.0, 100, 200)
    assert chop_scan.in_price_range(5000.0, 100, 0)
    assert chop_scan.in_price_range(50.0, 0, 200)
    assert not chop_scan.in_price_range(None, 100, 0)


def test_quiet_alert_keeps_only_stocks_in_the_price_range(monkeypatch):
    """Only quiet runners priced inside the range reach Telegram; the others stay unalerted for today."""
    import asyncio

    monkeypatch.setattr(chop_scan, "_ALERTED_TODAY", {})
    monkeypatch.setattr(chop_scan, "market_is_open", lambda now: True)
    prices = {"CHEAP": 40.0, "MID": 250.0, "DEAR": 3000.0}

    async def job(symbol, start, end):
        return chop_scan.ChopRow(
            symbol=symbol, minutes=5, crosses_today=1, minutes_since_last_cross=90.0,
            avg_minutes_between=None, avg_move_pct=None, last_cross_direction="BULLISH", score=chop_scan.TRENDING,
            quiet_runner=True, ltp=prices[symbol], candle_ts=0,
        )

    sent: list[str] = []

    async def go():
        scanner = chop_scan.ChopScanner()
        scanner.start(
            list(prices), None, job=job, quiet_min=60, alert_on_quiet=True, alert_cb=sent.append,
            force=True, now=_at(12, 0), price_min=100, price_max=1000,
        )
        await scanner._task

    asyncio.run(go())
    assert len(sent) == 1 and "MID" in sent[0] and "CHEAP" not in sent[0] and "DEAR" not in sent[0]
    assert not chop_scan._already_alerted("CHEAP", _at(12, 0))
