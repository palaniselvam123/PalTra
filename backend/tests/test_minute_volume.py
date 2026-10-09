"""Minute volume, session VWAP, and the volume filter. No broker calls."""
from __future__ import annotations

import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from indicators import (
    _session_vwap,
    closed_candle_cross,
    closed_technical_snapshot,
    derive_minute_volume,
    enrich,
    entry_filter_reason,
    minute_volume_stats,
)

IST = ZoneInfo("Asia/Kolkata")


def ts_at(day: str, hm: str) -> int:
    return int(dt.datetime.strptime(f"{day} {hm}", "%Y-%m-%d %H:%M").replace(tzinfo=IST).timestamp())


def frame_from(rows: list[tuple[int, int]]) -> pd.DataFrame:
    """rows are (ts, cumulative volume). Prices are flat so only volume matters."""
    out = []
    for ts, vol in rows:
        out.append({"ts": ts, "open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": vol})
    return pd.DataFrame(out)


def test_vwap_weights_traded_volume_not_the_running_total():
    day = "2026-09-30"
    # Typical prices 100, 110, 120. Minute volumes are supplied directly so
    # this checks the formula, including the first print.
    df = pd.DataFrame(
        {
            "ts": [ts_at(day, hm) for hm in ("09:15", "09:16", "09:17")],
            "open": [100.0, 110.0, 120.0],
            "high": [100.0, 110.0, 120.0],
            "low": [100.0, 110.0, 120.0],
            "close": [100.0, 110.0, 120.0],
            "minute_volume": [100.0, 200.0, 300.0],
        }
    )
    assert _session_vwap(df) == (100 * 100 + 110 * 200 + 120 * 300) / 600


def test_first_session_candle_has_no_minute_volume():
    day = "2026-09-30"
    stamps = ["09:15", "09:16", "09:17", "09:18"]
    cumulative = [1000, 1500, 2200, 3000]
    got = derive_minute_volume(frame_from([(ts_at(day, hm), vol) for hm, vol in zip(stamps, cumulative)]))
    assert pd.isna(got.iloc[0])
    assert list(got.iloc[1:]) == [500.0, 700.0, 800.0]


def test_a_flat_counter_is_zero_volume():
    day = "2026-09-30"
    got = derive_minute_volume(
        frame_from([(ts_at(day, "10:00"), 1000), (ts_at(day, "10:01"), 1000)])
    )
    assert pd.isna(got.iloc[0])
    assert got.iloc[1] == 0.0


def test_session_reset_does_not_subtract_the_previous_day():
    rows = [
        (ts_at("2026-09-29", "15:29"), 5_000_000),
        (ts_at("2026-09-29", "15:30"), 5_100_000),
        (ts_at("2026-09-30", "09:15"), 10_000),
        (ts_at("2026-09-30", "09:16"), 25_000),
    ]
    got = derive_minute_volume(frame_from(rows))
    assert pd.isna(got.iloc[0])
    assert got.iloc[1] == 100_000
    assert pd.isna(got.iloc[2])
    assert got.iloc[3] == 15_000


def test_a_falling_cumulative_volume_is_rejected():
    day = "2026-09-30"
    rows = [
        (ts_at(day, "10:29"), 4_800_000),
        (ts_at(day, "10:30"), 5_000_000),
        (ts_at(day, "10:31"), 4_900_000),
        (ts_at(day, "10:32"), 4_950_000),
    ]
    got = derive_minute_volume(frame_from(rows))
    assert got.iloc[1] == 200_000
    assert pd.isna(got.iloc[2])
    # The next bar would otherwise difference against the reset baseline.
    assert pd.isna(got.iloc[3])
    assert (got.dropna() < 0).sum() == 0


def test_duplicate_timestamps_are_not_differenced():
    day = "2026-09-30"
    stamp = ts_at(day, "10:30")
    rows = [
        (ts_at(day, "10:29"), 1_000),
        (stamp, 1_500),
        (stamp, 1_800),
        (ts_at(day, "10:31"), 2_000),
    ]
    got = derive_minute_volume(frame_from(rows))
    assert pd.isna(got.iloc[2])
    assert got.iloc[3] == 500


def test_unsorted_candles_are_ordered_before_the_difference():
    day = "2026-09-30"
    rows = [
        (ts_at(day, "09:17"), 2200),
        (ts_at(day, "09:15"), 1000),
        (ts_at(day, "09:16"), 1500),
    ]
    frame = frame_from(rows)
    got = derive_minute_volume(frame)
    by_ts = dict(zip(frame["ts"], got))
    assert pd.isna(by_ts[ts_at(day, "09:15")])
    assert by_ts[ts_at(day, "09:16")] == 500
    assert by_ts[ts_at(day, "09:17")] == 700


def test_volume_ratio_uses_minute_volume():
    day = "2026-09-30"
    # 1 opening print, 20 quiet minutes, one thin minute, then a forming bar.
    volumes = [1000] + [1000] * 20 + [100, 5000]
    running = 0
    rows = []
    start = ts_at(day, "09:15")
    for i, traded in enumerate(volumes):
        running += traded
        rows.append((start + i * 60, running))
    closed = enrich(frame_from(rows)).iloc[:-1]
    current, average, ratio = minute_volume_stats(closed, 20)
    assert current == 100
    assert average == 1000
    assert ratio == 0.1
    assert entry_filter_reason(enrich(frame_from(rows)), "LONG", use_volume=True, volume_min_ratio=1)
    loud = volumes[:-2] + [1500, 5000]
    running = 0
    rows = []
    for i, traded in enumerate(loud):
        running += traded
        rows.append((start + i * 60, running))
    assert entry_filter_reason(enrich(frame_from(rows)), "LONG", use_volume=True, volume_min_ratio=1.5) is None


def test_a_short_history_does_not_invent_a_volume_average():
    day = "2026-09-30"
    rows = [(ts_at(day, f"09:{15 + i:02d}"), (i + 1) * 100) for i in range(5)]
    current, average, ratio = minute_volume_stats(enrich(frame_from(rows)), 20)
    assert average is None
    assert ratio is None
    reason = entry_filter_reason(enrich(frame_from(rows)), "LONG", use_volume=True)
    assert reason is not None
    assert "0" != reason


def test_suntv_10_31_minute_volume_and_ratio():
    """The 30 Sep 2026 SUNTV cross, from the Groww running totals measured that day.

    10:30 cumulative 4,887,109 and 10:31 cumulative 4,941,124 traded 54,015 shares.
    The previous 20 minutes averaged 61,475. That is 0.88x, so a volume
    multiple of 1.0 fails. The same candle is not blocked when the filter is off.

    SMA, RSI, ATR, ADX and the corrected VWAP need the whole session tape.
    The saved Groww session had expired, so this test locks the volume facts
    that were measured, not a second copy of the indicator formulas.
    """
    day = "2026-09-30"
    start = ts_at(day, "10:10")
    # Index 0 is the first print of this fixture (minute volume unavailable).
    # Indexes 1..20 are the 20 minutes through 10:30. Index 21 is 10:31.
    prior = [61_475] * 20
    traded = [3_657_609] + prior + [54_015, 1_000]
    running = 0
    rows = []
    for i, shares in enumerate(traded):
        running += shares
        is_cross = i == 21
        close = 605.90 if is_cross else 605.0
        rows.append(
            {
                "ts": start + i * 60,
                "open": 605.50 if is_cross else close,
                "high": 606.00 if is_cross else close,
                "low": 604.95 if is_cross else close,
                "close": close,
                "volume": running,
            }
        )
    assert rows[20]["volume"] == 4_887_109
    assert rows[21]["volume"] == 4_941_124
    assert rows[21]["volume"] - rows[20]["volume"] == 54_015
    full = enrich(pd.DataFrame(rows))
    snap = closed_technical_snapshot(full)
    assert snap is not None
    assert snap.minute_volume == 54_015
    assert snap.average_previous_20_volume == 61_475
    assert snap.volume_ratio == pytest.approx(54_015 / 61_475)
    assert snap.close == 605.90
    blocked = entry_filter_reason(full, "LONG", use_volume=True, volume_min_ratio=1)
    assert blocked is not None
    assert "54015" in blocked.replace(",", "")
    assert entry_filter_reason(full, "LONG", use_volume=False) is None


def test_the_forming_bar_does_not_move_vwap_or_the_cross():
    day = "2026-09-30"
    start = ts_at(day, "09:15")
    rising = []
    running = 0
    for i in range(40):
        running += 1000
        close = 100 + i * 0.2
        rising.append(
            {
                "ts": start + i * 60,
                "open": close,
                "high": close + 0.1,
                "low": close - 0.1,
                "close": close,
                "volume": running,
            }
        )
    frame = enrich(pd.DataFrame(rising))
    before = _session_vwap(frame.iloc[:-1])
    cross_before = closed_candle_cross(frame)
    frame.loc[frame.index[-1], "close"] = 1.0
    frame.loc[frame.index[-1], "volume"] = frame.iloc[-2]["volume"] + 5_000_000
    assert _session_vwap(frame.iloc[:-1]) == before
    assert closed_candle_cross(frame) == cross_before
    snap = closed_technical_snapshot(frame)
    assert snap is not None
    assert snap.close == float(frame.iloc[-2]["close"])
    assert snap.timestamp == int(frame.iloc[-2]["ts"])


def _reset_volume_warnings(monkeypatch, clock):
    import indicators

    indicators._VOLUME_WARNED.clear()
    indicators._volume_window[:] = [0.0, 0, 0]
    monkeypatch.setattr(indicators.time, "monotonic", lambda: clock[0])
    return indicators


def test_bad_volume_prints_are_remembered_not_forgotten_all_at_once(monkeypatch, caplog):
    """A full list used to be cleared, so every print was logged again on the next quote."""
    clock = [1000.0]
    indicators = _reset_volume_warnings(monkeypatch, clock)
    with caplog.at_level("WARNING", logger="sma.indicators"):
        for i in range(600):  # more distinct prints than the old 400-entry list held
            indicators._warn_volume_once("cumulative volume fell", 1_790_000_000 + i * 60, 5000.0, 0.0)
        first = len(caplog.records)
        for _ in range(3):  # the same prints come round on the next quotes
            for i in range(600):
                indicators._warn_volume_once("cumulative volume fell", 1_790_000_000 + i * 60, 5000.0, 0.0)
    assert len(caplog.records) == first


def test_a_burst_of_bad_prints_logs_a_few_lines_then_one_summary(monkeypatch, caplog):
    clock = [1000.0]
    indicators = _reset_volume_warnings(monkeypatch, clock)
    with caplog.at_level("WARNING", logger="sma.indicators"):
        for i in range(500):
            indicators._warn_volume_once("cumulative volume fell", 1_790_000_000 + i * 60, 5000.0, 0.0)
        assert len(caplog.records) == indicators._VOLUME_WARN_PER_MIN
        clock[0] += 61  # the next minute: one line says how many were held back, then logging resumes
        indicators._warn_volume_once("cumulative volume fell", 1_791_000_000, 5000.0, 0.0)
    messages = [r.getMessage() for r in caplog.records]
    assert any("480 more bad prints were not logged" in m for m in messages)
    assert "ts=1791000000" in messages[-1]
