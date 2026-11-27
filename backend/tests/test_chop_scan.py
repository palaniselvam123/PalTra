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
        # The wall-clock time the pass ended, for the card's "Last scan HH:MM".
        assert snap["finished_at"] and snap["finished_at"].endswith("+05:30")
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
    # Every scan size has a place (30 minutes and 1 hour too); two days are too few for an hour candle's SMA 21.
    assert set(row.crosses_by_minutes) == set(chop_scan.SCAN_MINUTES)
    assert row.crosses_by_minutes[60] is None and row.side_by_minutes[60] is None
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
    assert set(row.last_cross_ts_by_minutes) == set(chop_scan.SCAN_MINUTES)
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


def test_rows_keep_when_each_stock_was_first_quiet_and_alerted_today(monkeypatch):
    """The table's "First alert": the first scan that read a stock as quiet, and when its Telegram went."""
    import asyncio

    monkeypatch.setattr(chop_scan, "_ALERTED_TODAY", {})
    monkeypatch.setattr(chop_scan, "_FIRST_QUIET", {})
    monkeypatch.setattr(chop_scan, "market_is_open", lambda now: True)
    quiet = {"EARLY"}

    async def job(symbol, start, end):
        return chop_scan.ChopRow(
            symbol=symbol, minutes=5, crosses_today=1, minutes_since_last_cross=90.0, avg_minutes_between=None,
            avg_move_pct=None, last_cross_direction="BULLISH", score=chop_scan.TRENDING,
            quiet_runner=symbol in quiet, ltp=100.0, candle_ts=0,
        )

    sent: list[str] = []

    async def go(when):
        scanner = chop_scan.ChopScanner()
        scanner.start(["EARLY", "LATE"], None, job=job, quiet_min=60, alert_on_quiet=True, alert_cb=sent.append, force=True, now=when)
        await scanner._task
        return {r["symbol"]: r for r in scanner.snapshot()["rows"]}

    first = asyncio.run(go(_at(11, 0)))
    assert first["EARLY"]["first_quiet_at"].startswith(f"{DAY.isoformat()}T11:00")
    assert first["EARLY"]["alerted_at"].startswith(f"{DAY.isoformat()}T11:00")
    assert first["LATE"]["first_quiet_at"] is None and first["LATE"]["alerted_at"] is None
    quiet.add("LATE")
    second = asyncio.run(go(_at(11, 30)))
    assert second["EARLY"]["first_quiet_at"].startswith(f"{DAY.isoformat()}T11:00")  # kept, not moved to 11:30
    assert second["LATE"]["first_quiet_at"].startswith(f"{DAY.isoformat()}T11:30")
    assert second["LATE"]["alerted_at"].startswith(f"{DAY.isoformat()}T11:30")


def test_strong_trend_is_read_on_every_candle_size():
    """A steady rise reads a strong bullish trend on the short candles; a fall reads bearish.

    The 15-minute slow SMA reaches back into yesterday, under today's VWAP, so it fails the VWAP check.
    """
    yesterday = [100 + i * 0.05 for i in range(375)]
    up = pd.concat([_minutes(yesterday, dt.date(2026, 10, 7)),
                    _minutes([yesterday[-1] + i * 0.05 for i in range(200)])], ignore_index=True)
    row = chop_scan.analyze(up, 9, 21, 5, _at(12, 40), symbol="UP")
    assert row is not None
    assert row.trend_by_minutes[5] == row.trend == "STRONG_BULLISH"
    assert all(row.trend_by_minutes[m] == "STRONG_BULLISH" for m in (1, 2, 3, 5)), row.trend_by_minutes
    assert row.trend_by_minutes[15] is None and row.side_by_minutes[15] == "BULLISH"
    fall = [200 - i * 0.05 for i in range(375)]
    down = pd.concat([_minutes(fall, dt.date(2026, 10, 7)),
                      _minutes([fall[-1] - i * 0.05 for i in range(200)])], ignore_index=True)
    row = chop_scan.analyze(down, 9, 21, 5, _at(12, 40), symbol="DOWN")
    assert all(row.trend_by_minutes[m] == "STRONG_BEARISH" for m in (1, 2, 3, 5)), row.trend_by_minutes
    short = chop_scan.analyze(_minutes([100 + i * 0.1 for i in range(60)]), 9, 21, 1, _at(10, 20), symbol="NEW")
    assert short.trend_by_minutes[15] is None


def test_heikin_ashi_bars_keep_the_shape_and_smooth_the_colours():
    """`ha_bars`: HA open / close, high and low widened to hold them; a pullback candle inside a rise stays green."""
    import heikin_ashi

    closes = [100, 101, 102, 103, 102.6, 104, 105]
    opens = [99.5, 100, 101, 102, 103, 102.6, 104]
    bars = pd.DataFrame({
        "ts": [1_792_035_900 + 300 * i for i in range(len(closes))], "open": opens, "close": closes,
        "high": [max(o, c) + 0.2 for o, c in zip(opens, closes)], "low": [min(o, c) - 0.2 for o, c in zip(opens, closes)],
    })
    out = chop_scan.ha_bars(bars)
    ha_open, ha_close = heikin_ashi.candles(bars)
    assert list(out["open"]) == list(ha_open) and list(out["close"]) == list(ha_close)
    assert (out["high"] >= out[["open", "close"]].max(axis=1)).all() and (out["low"] <= out[["open", "close"]].min(axis=1)).all()
    assert bars["close"].iloc[4] < bars["open"].iloc[4]  # the real candle is red
    assert out["close"].iloc[4] > out["open"].iloc[4]  # its Heikin Ashi candle is still green
    assert list(bars["close"]) == closes  # the input is not changed


def test_scanner_rejects_an_unknown_candle_kind():
    with pytest.raises(ValueError):
        chop_scan.ChopScanner().start(["SBIN"], None, candles="RENKO")


def test_a_heikin_ashi_scan_reads_the_candles_not_the_smas_and_says_which_candles_it_read():
    """The same choppy tape: with Heikin Ashi the score comes from colour flips (ha_trend), the price stays real."""
    async def go(candles):
        tape = _minutes([100 + ((i // 10) % 2) * 2 for i in range(300)])

        async def fetch(symbol, start, end):
            return tape

        s = chop_scan.ChopScanner()
        s.start(["CHOP"], fetch, minutes=5, now=_at(14, 20), force=True, candles=candles)
        await s._task
        return s.snapshot(), float(tape["close"].iloc[-1])

    normal, last = asyncio.run(go(chop_scan.NORMAL))
    ha, _ = asyncio.run(go(chop_scan.HEIKIN_ASHI))
    assert normal["candles"] == "NORMAL" and ha["candles"] == "HA"
    row = ha["rows"][0]
    assert row["trend_mode"] == "HEIKIN_ASHI" and row["score"] == chop_scan.CHOPPY
    assert row["crosses_today"] >= 5  # colour flips today, not SMA crosses
    assert row["ltp"] == normal["rows"][0]["ltp"] == round(last, 2)


def test_candle_kind_and_read_flags():
    assert chop_scan.candle_kind("colour") == chop_scan.COLOUR and chop_scan.candle_kind("x") == chop_scan.NORMAL
    assert chop_scan.read_flags("NORMAL") == (False, False)
    assert chop_scan.read_flags("COLOUR") == (False, True)
    assert chop_scan.read_flags("HA") == (True, True)
    assert chop_scan.read_flags(True) == (True, True) and chop_scan.read_flags(False) == (False, False)


def test_colour_mode_reads_the_real_candles_by_colour():
    # Five-minute candles that turn colour every candle: flips pile up, and the SMA cross count stays low.
    tape: list[float] = []
    for i in range(300):  # 09:15 to 14:15
        block = i // 5
        tape.append(100 + 0.02 * (i % 5) * (1 if block % 2 == 0 else -1) + (0.1 if block % 2 == 0 else 0.0))
    frame = _minutes(tape)
    now = _at(14, 20)
    normal = chop_scan.analyze(frame, 9, 21, 5, now, symbol="X")
    colour = chop_scan.analyze(frame, 9, 21, 5, now, symbol="X", colour=True)
    ha = chop_scan.analyze(frame, 9, 21, 5, now, symbol="X", ha=True)
    assert normal is not None and colour is not None and ha is not None
    assert colour.trend_mode == chop_scan.COLOUR_MODE and ha.trend_mode == "HEIKIN_ASHI"
    assert normal.trend_mode != chop_scan.COLOUR_MODE
    assert colour.crosses_today >= 5 and colour.score == chop_scan.CHOPPY  # colour flips, not SMA crosses
    assert colour.crosses_by_minutes[5] == colour.crosses_today


def test_a_holiday_scan_reads_the_last_session_not_an_empty_day():
    """Owner's case (24 Nov holiday): with no candles on the calendar day every stock read TRENDING with 0 crosses.
    The read now uses the session of the last closed candle."""
    tape = [100 + ((i // 10) % 2) * 2 for i in range(360)]  # a choppy session, 09:15 to 15:15
    frame = _minutes(tape)
    same_day = chop_scan.analyze(frame, 9, 21, 5, _at(15, 20), symbol="CHOP")
    holiday = chop_scan.analyze(frame, 9, 21, 5, _at(13, 30, DAY + dt.timedelta(days=1)), symbol="CHOP")
    assert holiday is not None and same_day is not None
    assert holiday.crosses_today == same_day.crosses_today >= chop_scan.CHOP_MIN_CROSSES
    assert holiday.score == chop_scan.CHOPPY
    assert holiday.crosses_by_minutes[5] == same_day.crosses_by_minutes[5]
    # Minutes since the last cross count to the session's last candle, not across the night.
    assert holiday.minutes_since_last_cross is not None and holiday.minutes_since_last_cross < 60


def _drop_then_flat() -> pd.DataFrame:
    """Yesterday quiet at 100; today a 6-point fall in the first 15 minutes, then flat around 94."""
    import math

    def day(d, closes):
        start = dt.datetime.combine(d, dt.time(9, 15), tzinfo=IST)
        rows, prev, run = [], closes[0], 0
        for i, c in enumerate(closes):
            run += 1000 * (5 if i < 15 else 1)
            rows.append({"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": prev,
                         "high": max(prev, c) + 0.05, "low": min(prev, c) - 0.05, "close": c, "volume": run})
            prev = c
        return rows

    rows = day(DAY - dt.timedelta(days=1), [100 + 0.05 * math.sin(i / 7) for i in range(375)])
    rows += day(DAY, [100 - 6 * i / 15 for i in range(15)] + [94 + 0.1 * ((i // 3) % 2) for i in range(120)])
    return pd.DataFrame(rows)


def test_skipping_the_opening_candles_drops_a_trend_made_only_by_them():
    frame = _drop_then_flat()
    plain = chop_scan.analyze(frame, 9, 21, 5, _at(9, 55), symbol="GAP")
    skipped = chop_scan.analyze(frame, 9, 21, 5, _at(9, 55), symbol="GAP", skip=chop_scan.skip_minutes(3, 5))
    assert plain.trend == "STRONG_BEARISH"  # the opening fall alone reads as a strong down trend
    assert skipped.trend is None and skipped.trend_skip_min == 15
    assert "not a trend" in skipped.trend_notes["skip"]
    assert skipped.vwap is not None and plain.vwap is not None and skipped.vwap < plain.vwap  # VWAP after the skip
    assert skipped.trend_by_minutes[5] is None
    assert chop_scan.skip_minutes(99, 5) == chop_scan.MAX_SKIP_CANDLES * 5 and chop_scan.skip_minutes(None, 5) == 0


def test_the_pick_backtests_read_the_trend_with_the_opening_candles_skipped():
    """Old days can be backtested with the skip: the strong-trend pick read and the auto-arm read follow the card."""
    import auto_arm
    import trend_picks

    frame = _drop_then_flat()
    at = dt.time(9, 55)
    plain = trend_picks.read_at(frame, DAY, trend_picks.TrendPickRule(pick_time=at, minutes=5), "GAP")
    skipped = trend_picks.read_at(frame, DAY, trend_picks.TrendPickRule(pick_time=at, minutes=5, skip_candles=3), "GAP")
    assert plain["trend"] == "STRONG_BEARISH" and skipped["trend"] is None
    assert trend_picks.TrendPickRule(skip_candles=3).as_dict()["skip_candles"] == 3

    read = auto_arm.strong_read(frame, DAY, at, 5, 9, 21)
    read_skip = auto_arm.strong_read(frame, DAY, at, 5, 9, 21, skip=15)
    assert read["trend"] == "STRONG_BEARISH"
    assert read_skip["trend"] is None and read_skip["trend_skip_min"] == 15 and "skip" in read_skip["trend_notes"]

    import scalp_picks

    rule = scalp_picks.rule_from_dict({"kind": "AUTOARM", "skip_candles": 3, "candles": "NORMAL"})
    assert rule.skip_candles == 3
    assert scalp_picks.rule_from_dict({"kind": "TREND", "skip_candles": 2}).skip_candles == 2


# 30-minute and 1-hour candles, and the scan's own SMA periods (owner's request).
def _days(n: int, step: float = 0.02, last_minutes: int = 375) -> pd.DataFrame:
    """`n` sessions ending on DAY (weekends skipped), one steady rise; the last one `last_minutes` long."""
    days, day = [], DAY
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day -= dt.timedelta(days=1)
    frames, price = [], 100.0
    for i, d in enumerate(reversed(days)):
        count = last_minutes if i == n - 1 else 375
        closes = [price + (k + 1) * step for k in range(count)]
        price = closes[-1]
        frames.append(_minutes(closes, d))
    return pd.concat(frames, ignore_index=True)


def test_an_hour_candle_scan_reads_a_steady_rise_as_trending():
    row = chop_scan.analyze(_days(6), 9, 21, 60, _at(15, 40), symbol="UP")
    assert row is not None and row.minutes == 60
    assert row.crosses_today == 0 and row.score == chop_scan.TRENDING
    assert row.side_by_minutes[60] == "BULLISH" and row.side_by_minutes[30] == "BULLISH"


def test_the_days_last_short_candle_closes_at_1530():
    """The 15:15 candle of 30 minutes or 1 hour is only 15 minutes long: it is closed once the session has ended."""
    from cross_scan import closed_bars

    frame = _days(1)
    for size, count in ((30, 13), (60, 7)):
        assert len(closed_bars(frame, size, _at(15, 29))) == count - 1
        assert len(closed_bars(frame, size, _at(15, 30))) == count
    # A size that divides the session is unchanged: 15:15-15:30 closes at 15:30 as before.
    assert len(closed_bars(frame, 15, _at(15, 29))) == 24 and len(closed_bars(frame, 15, _at(15, 30))) == 25


def test_the_windows_grow_with_the_candle_and_the_sma():
    from cross_scan import WARMUP_DAYS, download_days, window_days

    # SMA 21 on candles up to 15 minutes reads the same four days as always.
    assert all(window_days(m, 21) == WARMUP_DAYS for m in (1, 2, 3, 5, 10, 15))
    assert window_days(60, 21) > window_days(30, 21) >= WARMUP_DAYS
    assert window_days(60, 50) > window_days(60, 21)
    assert window_days(60, 200) == 29  # never more than one download allows
    # A 5-minute pass downloads enough for a row's 1-hour read, up to 15 days.
    assert download_days(5, 21, chop_scan.SCAN_MINUTES) == window_days(60, 21)
    assert download_days(5, 200, chop_scan.SCAN_MINUTES) == 15
    assert download_days(60, 200, chop_scan.SCAN_MINUTES) == 29


def test_a_longer_download_does_not_change_the_scans_own_read():
    """The pass downloads more days for the 1-hour read; the 5-minute read keeps its own window."""
    now = _at(12, 40)
    wide = _days(9, last_minutes=205)
    frame, same = chop_scan.scan_frames(wide, 5, 50, now)
    assert same is wide and len(frame) < len(wide)
    plain = chop_scan.analyze(frame, 20, 50, 5, now, symbol="UP")
    with_wide = chop_scan.analyze(frame, 20, 50, 5, now, symbol="UP", wide=wide)
    assert plain is not None and with_wide is not None
    for m in (1, 2, 3, 5, 10, 15):
        assert plain.side_by_minutes[m] == with_wide.side_by_minutes[m] == "BULLISH"
        assert plain.gap_pct_by_minutes[m] == with_wide.gap_pct_by_minutes[m]
    assert plain.trend == with_wide.trend and plain.rvol == with_wide.rvol
    # Only the longer download has the 51 hour candles SMA 50 needs.
    assert plain.side_by_minutes[60] is None and with_wide.side_by_minutes[60] == "BULLISH"


def test_sma_periods_asked_for_or_the_desks():
    from cross_scan import pick_smas

    assert pick_smas(0, 0, 9, 21) == (9, 21)
    assert pick_smas(None, None, 5, 13) == (5, 13)
    assert pick_smas(20, 50, 9, 21) == (20, 50)
    for fast, slow in ((50, 20), (20, 20), (20, 0), (0, 50), (1, 50), (20, 201)):
        with pytest.raises(ValueError):
            pick_smas(fast, slow, 9, 21)


def test_a_scan_on_20_and_50_reads_those_averages():
    """A long fall then a rise: SMA 9 is over SMA 21 long before SMA 20 is over SMA 50."""
    fall = [200 - i * 0.05 for i in range(375)]
    rise = [fall[-1] + i * 0.05 for i in range(15)]
    frame = pd.concat([_minutes(fall, dt.date(2026, 10, 7)), _minutes(rise)], ignore_index=True)
    now = _at(9, 30)
    quick = chop_scan.analyze(frame, 9, 21, 1, now, symbol="TURN")
    slow = chop_scan.analyze(frame, 20, 50, 1, now, symbol="TURN")
    assert quick is not None and slow is not None
    assert quick.side_by_minutes[1] == "BULLISH" and slow.side_by_minutes[1] == "BEARISH"
