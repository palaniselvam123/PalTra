"""30-minute and 1-hour bot candles: the history they need, the live download and the replay warm-up."""
from __future__ import annotations

import datetime as dt

import pandas as pd

import candles
import replay
from groww_client import IST, GrowwClient


def test_candles_up_to_15_minutes_keep_the_usual_five_days():
    for minutes in (1, 2, 3, 5, 10, 15):
        for slow in (21, 50, 300):
            assert candles.history_need(minutes, slow) == (candles.BASE_HISTORY_DAYS, candles.BASE_TAPE_BARS)


def test_long_candles_ask_for_more_days():
    d30, b30 = candles.history_need(30, 21)
    d60, b60 = candles.history_need(60, 21)
    assert candles.BASE_HISTORY_DAYS < d30 < d60
    assert b30 == b60 == candles.BASE_TAPE_BARS  # SMA 21 still fits the usual tape
    d, b = candles.history_need(60, 50)
    assert d > d60 and b > candles.BASE_TAPE_BARS
    assert candles.history_need(60, 300) == (candles.MAX_HISTORY_DAYS, candles.MAX_TAPE_BARS)
    assert candles.history_need("x", None) == (candles.BASE_HISTORY_DAYS, candles.BASE_TAPE_BARS)
    assert 30 in candles.CANDLE_MINUTES and 60 in candles.CANDLE_MINUTES
    assert candles.candle_minutes(type("C", (), {"candle_minutes": 60, "entry_mode": "SMA"})()) == 60


def test_the_most_any_engine_asks_for_wins_and_a_dropped_ask_goes():
    candles._NEEDS.clear()
    assert candles.need_of("SBIN") == (candles.BASE_HISTORY_DAYS, candles.BASE_TAPE_BARS)
    candles.note_need(1, "SBIN", 30, 21)
    candles.note_need(2, "sbin", 60, 50)
    assert candles.need_of("SBIN") == candles.history_need(60, 50)
    candles.note_need(2, "SBIN", 5, 50)  # bot 2 went back to 5-minute candles
    assert candles.need_of("SBIN") == candles.history_need(30, 21)
    candles.note_need(1, "SBIN", 15, 21)
    assert candles.need_of("SBIN") == (candles.BASE_HISTORY_DAYS, candles.BASE_TAPE_BARS) and "SBIN" not in candles._NEEDS


class _Sdk:
    """Answers every 1-minute candle request with one candle a minute, 09:15-15:29, on weekdays."""

    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.fail_older = False

    def get_ltp(self, **_):
        return {"NSE_SBIN": 500.0}

    def get_historical_candle_data(self, *, start_time, end_time, **_):
        self.calls.append((start_time, end_time))
        start = dt.datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
        end = dt.datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S")
        if self.fail_older and end < dt.datetime.now() - dt.timedelta(days=4):
            raise RuntimeError("Groww did not answer")
        rows, day = [], start.date()
        while day <= end.date():
            if day.weekday() < 5:
                for i in range(375):
                    at = dt.datetime.combine(day, dt.time(9, 15)) + dt.timedelta(minutes=i)
                    if start <= at <= end:
                        rows.append([int(at.replace(tzinfo=IST).timestamp()), 500, 501, 499, 500, 1000 * (i + 1)])
            day += dt.timedelta(days=1)
        return {"candles": rows}


def _client(sdk) -> GrowwClient:
    client = GrowwClient.__new__(GrowwClient)
    client._quotes, client._candle_frames, client._candle_at, client._history_tried, client._older = {}, {}, {}, {}, {}
    client._require_sdk = lambda: sdk
    return client


def test_the_live_download_is_unchanged_for_a_stock_nobody_trades_on_long_candles():
    candles._NEEDS.clear()
    sdk = _Sdk()
    _, frame = _client(sdk)._load_quote("SBIN")
    assert len(sdk.calls) == 1 and len(frame) <= candles.BASE_TAPE_BARS


def test_a_long_candle_stock_gets_its_older_days_once():
    candles._NEEDS.clear()
    candles.note_need(1, "SBIN", 60, 50)
    days, bars = candles.need_of("SBIN")
    sdk = _Sdk()
    client = _client(sdk)
    _, frame = client._load_quote("SBIN")
    first_calls = len(sdk.calls)
    assert first_calls >= 3  # the usual five days, then the older days seven at a time
    assert candles.BASE_TAPE_BARS < len(frame) <= bars + 1  # plus the forming minute
    ts = frame["ts"].astype("int64")
    assert ts.is_monotonic_increasing and ts.is_unique
    span_days = (int(ts.iloc[-1]) - int(ts.iloc[0])) / 86_400
    assert candles.BASE_HISTORY_DAYS < span_days <= days + 1
    # The minute-by-minute refresh downloads only the last five days again: the older days are kept.
    client._candle_at.clear()
    _, again = client._load_quote("SBIN")
    assert len(sdk.calls) == first_calls + 1 and len(again) == len(frame)
    # An hour candle now has its 51 closed candles for SMA 50.
    assert len(candles.resample(frame, 60)) > 51
    candles._NEEDS.clear()


def test_a_failed_older_download_keeps_the_usual_candles_and_is_asked_again():
    candles._NEEDS.clear()
    candles.note_need(1, "SBIN", 60, 21)
    sdk = _Sdk()
    sdk.fail_older = True
    client = _client(sdk)
    _, frame = client._load_quote("SBIN")
    assert 0 < len(frame) <= candles.BASE_TAPE_BARS and "SBIN" not in client._older
    sdk.fail_older = False
    client._candle_at.clear()
    client._load_quote("SBIN")
    assert "SBIN" in client._older
    candles._NEEDS.clear()


def test_a_replay_loads_more_days_before_a_long_candle_run():
    assert replay.warmup_days({}) == replay.WARMUP_DAYS
    assert replay.warmup_days({"candle_minutes": 15, "sma_slow": 50, "entry_mode": "SMA"}) == replay.WARMUP_DAYS
    hour = replay.warmup_days({"candle_minutes": 60, "sma_slow": 21, "entry_mode": "SMA"})
    assert hour == candles.history_need(60, 21)[0] > replay.WARMUP_DAYS
    # One stock on its own 1-hour candle is enough, also when the settings arrive as text.
    own = {"candle_minutes": 5, "sma_slow": 21, "entry_mode": "SMA", "stock_settings": {"SBIN": {"candle_minutes": 60}}}
    assert replay.warmup_days(own) == hour
    assert replay.warmup_days({**own, "stock_settings": '{"SBIN": {"candle_minutes": 60}}'}) == hour
    # Candle-colour mode trades its own colour candles: the bot's candle is 1 minute there.
    assert replay.warmup_days({"candle_minutes": 60, "sma_slow": 21, "entry_mode": "COLOUR"}) == replay.WARMUP_DAYS


def test_an_hour_candle_closes_on_the_hour_from_0915():
    start = dt.datetime(2026, 10, 13, 9, 15, tzinfo=IST)
    frame = pd.DataFrame(
        [{"ts": int((start + dt.timedelta(minutes=i)).timestamp()), "open": 100, "high": 101, "low": 99, "close": 100 + i * 0.01, "volume": i} for i in range(375)]
    )
    bars = candles.resample(frame, 60)
    times = [dt.datetime.fromtimestamp(int(t), IST).strftime("%H:%M") for t in bars["ts"]]
    assert times == ["09:15", "10:15", "11:15", "12:15", "13:15", "14:15", "15:15"]
    assert len(candles.resample(frame, 30)) == 13
