"""Bots first on Groww (groww_pressure.py): the bots' own threads, and scans that yield.

Groww's request limit is per account, and the bots, the Scalp page's scans and
the desk's monitor all share one account. Two things here keep a busy Scalp
page from starving the bots during market hours:

1. **The bots' own threads.** Every blocking Groww SDK call of the SMA bots
   (quotes, candles, orders, stops) runs on `BOT_POOL`, a thread pool only the
   bots use (`run_bot_call`). Scans and the desk use asyncio's default pool, so
   slow scan downloads can never queue in front of a bot's price or order call.
2. **Scans yield on a rate limit.** When a bot's Groww call is refused for the
   rate limit (`note_bot_rate_limit`), scans wait (`wait_for_bots`) until no
   bot has been refused for `HOLD_SEC`, then carry on. The scans check before
   each stock, so a pass slows down instead of failing.

Counters for /api/stall: rate limits seen by the bots and by scans, how long
scans have waited, and how busy the bots' pool is. Read-only bookkeeping: no
order is placed, changed or held back here.
"""
from __future__ import annotations

import asyncio
import functools
import os
import time
from concurrent.futures import ThreadPoolExecutor

HOLD_SEC = 30.0  # scans wait until no bot was rate-limited for this long
MAX_WAIT_SEC = 120.0  # never hold one scan step longer than this
BOT_THREADS = int(os.environ.get("BOT_GROWW_THREADS", "12") or 12)

BOT_POOL = ThreadPoolExecutor(max_workers=max(2, BOT_THREADS), thread_name_prefix="bot-groww")

_state = {
    "bot_rate_limits": 0,
    "scan_rate_limits": 0,
    "last_bot_rate_limit": 0.0,  # time.monotonic(); 0 = never
    "scan_waits": 0,
    "scan_wait_sec": 0.0,
    "bot_calls_running": 0,
    "bot_calls_peak": 0,
    "feed_rate_limits": 0,
}


def is_rate_limited(exc: BaseException | str) -> bool:
    text = str(exc).lower()
    return "rate limit" in text or "too many requests" in text or "429" in text


def note_bot_rate_limit() -> None:
    _state["bot_rate_limits"] += 1
    _state["last_bot_rate_limit"] = time.monotonic()


def note_feed_rate_limit() -> None:
    """The desk's live price feed (Dashboard "Live") was refused: scans yield as for a bot."""
    _state["feed_rate_limits"] += 1
    _state["last_bot_rate_limit"] = time.monotonic()


def note_scan_rate_limit() -> None:
    _state["scan_rate_limits"] += 1


def bots_under_pressure(now: float | None = None) -> bool:
    last = _state["last_bot_rate_limit"]
    return bool(last) and (now if now is not None else time.monotonic()) - last < HOLD_SEC


async def wait_for_bots(max_wait: float = MAX_WAIT_SEC) -> float:
    """Before a scan's Groww call: wait while a bot was rate-limited lately. Returns seconds waited."""
    if not bots_under_pressure():
        return 0.0
    started = time.monotonic()
    _state["scan_waits"] += 1
    while bots_under_pressure() and time.monotonic() - started < max_wait:
        await asyncio.sleep(1.0)
    waited = time.monotonic() - started
    _state["scan_wait_sec"] += waited
    return waited


async def run_bot_call(fn, *args, **kwargs):
    """Run one blocking Groww SDK call of a bot on the bots' own threads.

    A rate-limit refusal is counted (scans then yield) and raised as before.
    """
    loop = asyncio.get_running_loop()
    _state["bot_calls_running"] += 1
    _state["bot_calls_peak"] = max(_state["bot_calls_peak"], _state["bot_calls_running"])
    try:
        return await loop.run_in_executor(BOT_POOL, functools.partial(fn, *args, **kwargs))
    except Exception as exc:
        if is_rate_limited(exc):
            note_bot_rate_limit()
        raise
    finally:
        _state["bot_calls_running"] -= 1


def snapshot() -> dict:
    last = _state["last_bot_rate_limit"]
    return {
        "bot_threads": BOT_POOL._max_workers,  # noqa: SLF001 - reporting only
        "bot_calls_running": _state["bot_calls_running"],
        "bot_calls_peak": _state["bot_calls_peak"],
        "bot_rate_limits": _state["bot_rate_limits"],
        "feed_rate_limits": _state["feed_rate_limits"],
        "last_bot_rate_limit_s_ago": round(time.monotonic() - last, 1) if last else None,
        "scans_yielding_now": bots_under_pressure(),
        "scan_rate_limits": _state["scan_rate_limits"],
        "scan_waits": _state["scan_waits"],
        "scan_wait_sec": round(_state["scan_wait_sec"], 1),
    }


def reset_for_tests() -> None:
    for key in _state:
        _state[key] = 0.0 if isinstance(_state[key], float) else 0
