"""Groww candle parsing (groww_client._parse_candles): a minute with missing prices is skipped.

Groww sometimes sends a 1-minute candle whose prices are None. That used to raise
(float(None)) and fail the whole stock's download, e.g. "4 stock(s) could not be
read" on the Scalp scans. Pure parsing; no network, no broker path.
"""
from __future__ import annotations

import groww_client


def test_a_minute_with_no_close_is_skipped_and_the_rest_kept():
    raw = {
        "candles": [
            [1760000000, 100.0, 101.0, 99.5, 100.5, 1000],
            [1760000060, None, None, None, None, None],
            [1760000120, 100.5, 102.0, 100.0, 101.5, 2500],
        ]
    }
    frame = groww_client._parse_candles(raw, limit=None)
    assert list(frame["ts"]) == [1760000000, 1760000120]
    assert list(frame["close"]) == [100.5, 101.5]


def test_missing_open_high_low_or_volume_fall_back_to_the_close_and_zero():
    raw = {"payload": {"candles": [[1760000000, None, "", "nan", 100.25, None], ["2026-10-13 09:16:00", 1, 2, 0.5, "1.5", "300.0"]]}}
    frame = groww_client._parse_candles(raw, limit=None)
    first = frame.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (100.25, 100.25, 100.25, 100.25, 0)
    assert frame.iloc[1]["close"] == 1.5 and frame.iloc[1]["volume"] == 300


def test_dict_rows_with_a_null_close_are_skipped():
    raw = {"data": [{"timestamp": 1760000000, "open": 1, "high": 1, "low": 1, "close": None, "volume": 5}]}
    assert groww_client._parse_candles(raw).empty
