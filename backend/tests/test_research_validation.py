"""Dataset validator tests.

Two failure modes are equally bad here: missing a real defect, and crying wolf
on normal market structure. A validator that flags every weekend and overnight
gap trains you to ignore it, at which point it stops catching the gap that
matters.
"""
from __future__ import annotations

import datetime as dt

from app.research.validator import expected_bars_per_session, validate
from app.services.indicators import OHLCV

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


def session_bars(day: str, n: int = 75, start_hour: int = 9, start_min: int = 15) -> list[OHLCV]:
    """A normal trading session: n consecutive 5-minute bars from 09:15 IST."""
    base = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(
        hour=start_hour, minute=start_min, tzinfo=IST).timestamp())
    return [OHLCV(base + i * 300, 100.0, 101.0, 99.0, 100.5, 1000) for i in range(n)]


class TestCleanData:
    def test_a_full_session_is_ready(self):
        res = validate({"AAA": session_bars("2026-06-01")}, "5m")
        assert res.status == "READY"
        assert res.duplicates == 0 and res.invalid_ohlc == 0 and res.missing_intervals == 0

    def test_counts_are_reported(self):
        res = validate({"AAA": session_bars("2026-06-01"), "BBB": session_bars("2026-06-01")}, "5m")
        assert res.symbols == 2 and res.total_candles == 150 and res.trading_days == 1

    def test_full_nse_session_is_75_five_minute_bars(self):
        assert expected_bars_per_session("5m") == 75


class TestNoFalsePositives:
    def test_overnight_gap_is_not_a_missing_bar(self):
        """15:30 to next 09:15 is a 17-hour hole that is not a defect."""
        bars = session_bars("2026-06-01") + session_bars("2026-06-02")
        res = validate({"AAA": bars}, "5m")
        assert res.missing_intervals == 0
        assert res.status == "READY"

    def test_weekend_is_not_a_missing_day(self):
        # 2026-06-05 is a Friday; next session is Monday 2026-06-08
        bars = session_bars("2026-06-05") + session_bars("2026-06-08")
        res = validate({"AAA": bars}, "5m")
        assert res.holidays_detected == []
        assert res.status == "READY"

    def test_market_wide_absence_is_reported_as_a_holiday(self):
        """A weekday where EVERY symbol is absent is a market holiday, not a
        simultaneous per-symbol gap. Inferred from the cross-section because
        the app has no NSE holiday calendar."""
        # 2026-06-02 (Tuesday) missing for both symbols
        bars = {
            "AAA": session_bars("2026-06-01") + session_bars("2026-06-03"),
            "BBB": session_bars("2026-06-01") + session_bars("2026-06-03"),
        }
        res = validate(bars, "5m")
        assert res.holidays_detected == ["2026-06-02"]
        assert res.missing_intervals == 0, "a holiday must not be counted as missing bars"


class TestDefectDetection:
    def test_duplicate_timestamp_is_caught(self):
        bars = session_bars("2026-06-01")
        bars.append(OHLCV(bars[0].ts, 100.0, 101.0, 99.0, 100.5, 1000))
        bars.sort(key=lambda b: b.ts)
        res = validate({"AAA": bars}, "5m")
        assert res.duplicates == 1
        assert res.status == "FAILED"

    def test_missing_bars_inside_a_session_are_caught(self):
        bars = session_bars("2026-06-01")[:40]  # half a session
        res = validate({"AAA": bars}, "5m")
        assert res.missing_intervals == 35
        assert res.status == "WARNING"

    def test_high_below_open_is_invalid(self):
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts, 100.0, 99.0, 98.0, 98.5, 1000)  # high < open
        res = validate({"AAA": bars}, "5m")
        assert res.invalid_ohlc == 1 and res.status == "FAILED"

    def test_low_above_close_is_invalid(self):
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts, 100.0, 101.0, 100.5, 100.2, 1000)  # low > close
        res = validate({"AAA": bars}, "5m")
        assert res.invalid_ohlc == 1

    def test_low_above_high_is_invalid(self):
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts, 100.0, 99.0, 101.0, 100.0, 1000)
        res = validate({"AAA": bars}, "5m")
        assert res.invalid_ohlc == 1

    def test_negative_volume_is_flagged(self):
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts, 100.0, 101.0, 99.0, 100.5, -50)
        res = validate({"AAA": bars}, "5m")
        assert res.invalid_volume == 1 and res.status == "FAILED"

    def test_zero_volume_is_allowed(self):
        """An illiquid bar with no trades is normal, not corrupt."""
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts, 100.0, 101.0, 99.0, 100.5, 0)
        assert validate({"AAA": bars}, "5m").invalid_volume == 0

    def test_out_of_order_timestamps_are_caught(self):
        bars = session_bars("2026-06-01")
        bars[10], bars[11] = bars[11], bars[10]
        res = validate({"AAA": bars}, "5m")
        assert res.timestamp_issues >= 1 and res.status == "FAILED"

    def test_unaligned_timestamp_is_caught(self):
        """A 5-minute bar opening at 09:17 means the feed or the bucketing is
        wrong, and every indicator downstream inherits it."""
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts + 120, 100.0, 101.0, 99.0, 100.5, 1000)
        assert validate({"AAA": bars}, "5m").timestamp_issues >= 1


class TestReporting:
    def test_issues_are_recorded_not_discarded(self):
        """Nothing is silently dropped — a defect must leave a trace naming
        the symbol and the bar."""
        bars = session_bars("2026-06-01")
        bars[5] = OHLCV(bars[5].ts, 100.0, 99.0, 98.0, 98.5, 1000)
        res = validate({"AAA": bars}, "5m")
        assert any(i.kind == "invalid_ohlc" and i.symbol == "AAA" for i in res.issues)

    def test_report_renders_the_headline_numbers(self):
        from app.research.validator import format_report

        text = format_report(validate({"AAA": session_bars("2026-06-01")}, "5m"))
        assert "DATASET QUALITY REPORT" in text
        assert "STATUS: READY" in text
        assert "Total candles:        75" in text

    def test_empty_dataset_is_failed_not_ready(self):
        assert validate({}, "5m").status == "FAILED"
