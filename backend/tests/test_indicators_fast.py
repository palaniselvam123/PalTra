"""The faster true range / minute volume (replay speed-up) give exactly what the old pandas code gave."""
import numpy as np
import pandas as pd

import indicators


def _old_true_range(df):
    prev_close = df["close"].shift(1)
    ranges = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1)
    return ranges.max(axis=1)


def _old_minute_volume(df):
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    work = pd.DataFrame({"_idx": df.index, "_ts": pd.to_numeric(df["ts"]), "_vol": pd.to_numeric(df["volume"])})
    work = work.sort_values(["_ts", "_idx"], kind="mergesort")
    dates = pd.to_datetime(work["_ts"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.date
    prev_cum = prev_ts = prev_day = None
    reject = False
    found = {}
    for idx, ts, vol, day in zip(work["_idx"], work["_ts"], work["_vol"], dates):
        if day != prev_day:
            prev_day, prev_cum, prev_ts, reject = day, None, None, False
        if pd.isna(vol):
            prev_cum = prev_ts = None
            reject = False
            continue
        if prev_ts is not None and ts == prev_ts:
            continue
        if prev_cum is None or reject:
            prev_cum, prev_ts, reject = float(vol), ts, False
            continue
        if vol < prev_cum:
            prev_cum, prev_ts, reject = float(vol), ts, True
            continue
        found[idx] = float(vol) - prev_cum
        prev_cum, prev_ts = float(vol), ts
    if found:
        out.loc[list(found)] = list(found.values())
    return out


def _frames():
    rng = np.random.default_rng(7)
    for k in range(200):
        n = int(rng.integers(0, 50))
        ts = 1794800000 + 60 * np.arange(n) + 86400 * (np.arange(n) > n // 2)
        if n and k % 3 == 0:
            ts = rng.permutation(ts)
        if n > 3 and k % 4 == 0:
            ts[2] = ts[1]
        c = 100 + rng.normal(0, 1, n).cumsum()
        vol = np.cumsum(rng.integers(0, 1000, n)).astype(float)
        if n > 5 and k % 5 == 0:
            vol[n // 3] = 0
        if n > 5 and k % 7 == 0:
            vol[n // 4] = np.nan
        if n > 5 and k % 6 == 0:
            c[1] = np.nan
        index = None if k % 2 else rng.permutation(n) + 3
        yield pd.DataFrame({"ts": ts, "open": c, "high": c + rng.random(n), "low": c - rng.random(n), "close": c, "volume": vol}, index=index)


def test_true_range_matches_the_pandas_way():
    for df in _frames():
        pd.testing.assert_series_equal(indicators.true_range(df), _old_true_range(df), check_exact=True, check_names=False)


def test_minute_volume_matches_the_old_loop_including_ties_and_resets():
    for df in _frames():
        pd.testing.assert_series_equal(indicators.derive_minute_volume(df), _old_minute_volume(df), check_exact=True)
