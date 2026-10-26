"""Scanner process (scan_worker.py): the Scalp page's scans run outside the bots' process.

The desk, every bot, replays and all page requests share one Python process and
one event loop. A Cross scan or Trend quality scan downloads and crunches 4 days
of 1-minute candles for up to ~250 stocks (resample, SMAs, ATR, trend checks),
and that work used to run in the same process, so the bots and the pages waited
for it. Here each stock's download *and* analysis run in a separate process
(one worker, started on the first scan, at a lower CPU priority); the main
process only keeps the scan's progress and gets back the finished row.

Groww's rate limit is per account, so the workers also pace themselves: together at
most one candle request every `MIN_GAP_SEC` (each of `WORKERS` processes waits
`WORKERS` × that), and a "rate limit" answer is retried after 2, 4 and 8 seconds
instead of failing the stock. Sleeping in the worker costs the bots nothing.
`WORKERS` follows `CROSS_SCAN_CONCURRENCY` (1 by default, 3 on the scanner server),
so Groww's answer time for one stock overlaps the next ones.

Earlier days never change, so the Cross scan and Trend quality keep each stock's
candles before today in the main process (`_PAST`, memory only, today's only) after its
first scan of the day; later scans download just today (`run_cached`).

Read-only: candles only, no order path. The Groww access token is handed to the
worker over the process pipe for its own SDK session; it is never logged.
`SCAN_WORKER=off` runs scans in-process as before (tests, or a fallback).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import multiprocessing
import os
import time
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

log = logging.getLogger("sma.scanworker")

MIN_GAP_SEC = 0.35  # between two Groww candle requests, over all workers together
RATE_RETRY_SEC = (2, 4, 8)  # waits after a "rate limit" answer
JOB_TIMEOUT_SEC = 90  # one stock: pacing + retries + a 4-day download
NICE = 10  # the worker yields the CPU to the bots


class AuthFailed(Exception):
    """Groww refused the session; the main process may switch to another saved token."""


class ScanFailed(Exception):
    """Groww did not send this stock's candles."""


def enabled() -> bool:
    return os.environ.get("SCAN_WORKER", "on").strip().lower() not in ("off", "0", "false", "no")


# ---- worker side (runs in the scanner process) ---------------------------------------------

_sdk_cache: dict = {"token": None, "sdk": None}
_last_call = [0.0]
_workers = [1]  # workers in the pool: each spaces its own calls MIN_GAP_SEC × this


def _init_worker(workers: int = 1) -> None:
    _workers[0] = max(1, int(workers))
    try:
        os.nice(NICE)
    except (AttributeError, OSError):
        pass


def _sdk(token: str):
    if _sdk_cache["token"] != token or _sdk_cache["sdk"] is None:
        from growwapi import GrowwAPI

        # Same as GrowwClient._require_sdk: the SDK's changelog call has no HTTP timeout.
        GrowwAPI._get_changelog = lambda _self: {}
        _sdk_cache["sdk"] = GrowwAPI(token)
        _sdk_cache["token"] = token
    return _sdk_cache["sdk"]


def _rate_limited(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "rate limit" in text or "too many requests" in text or "429" in text


class _Paced:
    """The SDK's candle call, spaced out and retried on a rate-limit answer."""

    def __init__(self, sdk) -> None:
        self._sdk = sdk

    def get_historical_candle_data(self, **kwargs):
        waits = list(RATE_RETRY_SEC)
        while True:
            gap = MIN_GAP_SEC * _workers[0] - (time.monotonic() - _last_call[0])
            if gap > 0:
                time.sleep(gap)
            _last_call[0] = time.monotonic()
            try:
                return self._sdk.get_historical_candle_data(**kwargs)
            except Exception as exc:  # noqa: BLE001
                if waits and _rate_limited(exc):
                    time.sleep(waits.pop(0))
                    continue
                raise


def _frame(token: str, symbol: str, start: dt.datetime, end: dt.datetime):
    from candle_history import _download
    from groww_client import _is_auth_error

    try:
        return _download(_Paced(_sdk(token)), symbol, start, end)
    except Exception as exc:  # noqa: BLE001
        detail = (str(exc).strip() or type(exc).__name__)[:200]
        if _is_auth_error(exc):
            _sdk_cache["sdk"] = None
            raise AuthFailed(detail) from None
        raise ScanFailed(f"Groww did not send candles: {detail}"[:240]) from None


def _midnight(end: dt.datetime) -> dt.datetime:
    return end.replace(hour=0, minute=0, second=0, microsecond=0)


def _frame_with_past(token, symbol, start, end, past):
    """(candles start..end, the days before today to keep). With `past` only today is downloaded."""
    import pandas as pd

    from groww_client import IST

    if past is not None and not past.empty:
        today = _frame(token, symbol, _midnight(end), end)
        frame = pd.concat([past, today]).sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
        return frame, None
    frame = _frame(token, symbol, start, end)
    cut = int(_midnight(end).replace(tzinfo=IST).timestamp())
    return frame, frame[frame["ts"].astype("int64") < cut].reset_index(drop=True)


def cross_job(token, symbol, start, end, sma_fast, sma_slow, minutes, now, past=None):
    """(row, earlier days to keep); see `run_cached`."""
    from cross_scan import scan_one

    frame, keep = _frame_with_past(token, symbol, start, end, past)
    return scan_one(symbol, frame, sma_fast, sma_slow, minutes, now), keep


def chop_job(token, symbol, start, end, sma_fast, sma_slow, minutes, now, quiet_min, past=None):
    """(row, earlier days to keep); see `run_cached`."""
    from chop_scan import analyze

    frame, keep = _frame_with_past(token, symbol, start, end, past)
    return analyze(frame, sma_fast, sma_slow, minutes, now, symbol=symbol, quiet_min=quiet_min), keep


def morning_job(token, symbol, start, end, sma_fast, sma_slow, minutes, now):
    from morning_scan import analyze, download_start

    # The worker keeps each stock's opening-volume baseline for the day, so it picks the start itself.
    return analyze(_frame(token, symbol, download_start(symbol, now), end), sma_fast, sma_slow, now, symbol=symbol)


def check_job(token, symbol, start, end, kind, row, as_of, minutes, sma_fast, sma_slow):
    from scan_accuracy import evaluate

    return evaluate(kind, row, _frame(token, symbol, start, end), as_of, minutes, sma_fast, sma_slow)


# ---- main-process side ---------------------------------------------------------------------

_pool: ProcessPoolExecutor | None = None
# Each stock's candles before today, from its first scan today: {(symbol, window start): frame}.
_PAST: dict = {}
_PAST_DAY: list = [None]


def workers() -> int:
    from cross_scan import CONCURRENCY

    return max(1, int(CONCURRENCY))


def _executor() -> ProcessPoolExecutor:
    global _pool
    if _pool is None:
        n = workers()
        _pool = ProcessPoolExecutor(
            max_workers=n, mp_context=multiprocessing.get_context("spawn"), initializer=_init_worker, initargs=(n,)
        )
    return _pool


async def run_cached(broker, fn, symbol: str, start: dt.datetime, end: dt.datetime, *args):
    """`run` for a job that returns (row, earlier days): the earlier days are kept for today's next scans."""
    day = end.date()
    if _PAST_DAY[0] != day:
        _PAST.clear()
        _PAST_DAY[0] = day
    key = (symbol, start)
    row, keep = await run(broker, fn, symbol, start, end, *args, _PAST.get(key))
    if keep is not None and not keep.empty:
        _PAST[key] = keep
    return row


def shutdown() -> None:
    """Stop the worker (app shutdown). Safe to call when it never started."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


async def run(broker, fn, symbol: str, start: dt.datetime, end: dt.datetime, *args):
    """Run one stock's scan job in the worker with the desk's Groww session."""
    from candle_history import HistoryError

    global _pool
    if not broker.token:
        broker.adopt_saved_session()
    if not broker.token:
        raise HistoryError(getattr(broker, "no_login_text", "Past candles come from Groww. Log in to Groww on the desk Settings page first."))
    import groww_pressure

    # Bots first: while a bot's Groww call was refused for the rate limit lately, wait.
    await groww_pressure.wait_for_bots()
    loop = asyncio.get_running_loop()
    for attempt in (0, 1):
        try:
            future = loop.run_in_executor(_executor(), fn, broker.token, symbol, start, end, *args)
            return await asyncio.wait_for(future, timeout=JOB_TIMEOUT_SEC)
        except AuthFailed as exc:
            if attempt == 0 and broker._recover_from_auth_failure():  # noqa: SLF001 - same recovery as fetch_frame
                continue
            raise HistoryError(f"Groww did not send candles: {exc}"[:240]) from None
        except ScanFailed as exc:
            if groww_pressure.is_rate_limited(exc):
                groww_pressure.note_scan_rate_limit()
            raise HistoryError(str(exc)) from None
        except BrokenProcessPool:
            log.warning("scanner process stopped; it restarts on the next stock")
            _pool = None
            raise HistoryError("The scanner process stopped. Press Scan now again.") from None
        except asyncio.TimeoutError:
            raise HistoryError(f"Groww took longer than {JOB_TIMEOUT_SEC} s for {symbol}") from None
    raise HistoryError("Groww did not send candles")
