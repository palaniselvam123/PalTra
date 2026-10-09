"""Stall tracer: say what the app was doing when its event loop stopped answering.

The desk, all the SMA bots, replays and every page request share one Python
event loop. When something blocks it, pages time out and nothing in the log says
why. This watches for that and writes down what every thread was doing.

How it works (logging only: it never touches an order, a stop or a setting):

* a heartbeat task on the loop stamps the time every `BEAT_SEC` seconds;
* a watchdog thread, which does not need the loop, checks the stamp every second
  and, when it is `STALL_SEC` old, logs one summary line (memory, CPU, threads)
  and the stack of the thread running the loop: the line that is blocking it;
* Python's `faulthandler` timer is re-armed on every beat, so if the loop stops
  beating it dumps the stack of every thread to stderr, even when the stall
  holds the interpreter lock and the watchdog thread itself cannot run;
* one more line says how long the stall lasted when the loop comes back.

Look for "STALL" in the Fly log. `STALL_TRACE=off` turns it off, and
`STALL_TRACE_SEC` changes the 5-second threshold. `snapshot()` is what
`GET /api/stall` returns.
"""
from __future__ import annotations

import asyncio
import faulthandler
import logging
import os
import sys
import threading
import time
import traceback

log = logging.getLogger("sma.stall")

BEAT_SEC = 0.5  # how often the loop stamps the time
LAG_LOG_SEC = 1.5  # a beat this late is logged as "lagged" (a stall in the making)
WATCH_SEC = 1.0  # how often the watchdog thread looks
REDUMP_SEC = 45.0  # while a stall goes on, dump every thread again this often
MAX_REDUMPS = 4
LAG_LOG_EVERY_SEC = 10.0
HISTORY = 20


def _float_env(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except ValueError:
        return default
    return value if value > 0 else default


def _rss_mb() -> float | None:
    """Resident memory of this process, from /proc (Linux); None elsewhere."""
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError, IndexError):
        pass
    return None


def format_stack(frame, limit: int = 25) -> str:
    """The last `limit` lines of a thread's stack, newest last."""
    lines = traceback.format_stack(frame)
    return "".join(lines[-limit:]).rstrip()


class StallTracer:
    def __init__(
        self,
        stall_sec: float | None = None,
        beat_sec: float = BEAT_SEC,
        watch_sec: float = WATCH_SEC,
        use_faulthandler: bool = True,
    ) -> None:
        self.stall_sec = stall_sec if stall_sec is not None else _float_env("STALL_TRACE_SEC", 5.0)
        self.beat_sec = beat_sec
        self.watch_sec = watch_sec
        self.use_faulthandler = use_faulthandler
        self.enabled = os.environ.get("STALL_TRACE", "on").strip().lower() not in ("off", "0", "false", "no")
        self._last = time.monotonic()
        self._loop_thread: int | None = None
        self._beat_task: asyncio.Task | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stalled_since: float | None = None
        self._redumps = 0
        self._last_dump_at = 0.0
        self._last_lag_log = 0.0
        self.stalls: list[dict] = []  # newest last
        self.stall_count = 0
        self.longest_stall = 0.0
        self.max_lag = 0.0
        self.started_at: float | None = None

    # ---- on the event loop -------------------------------------------------
    def start(self) -> bool:
        """Start watching. Call from inside the running loop. Safe to call twice."""
        if not self.enabled or self._thread is not None:
            return False
        loop = asyncio.get_running_loop()
        self._loop_thread = threading.get_ident()
        self._last = time.monotonic()
        self.started_at = time.time()
        self._stop.clear()
        self._beat_task = loop.create_task(self._beat())
        self._thread = threading.Thread(target=self._watch, name="stall-watchdog", daemon=True)
        self._thread.start()
        log.info("stall tracer on: a loop stall of %.1fs or more is logged with the stacks", self.stall_sec)
        return True

    def stop(self) -> None:
        self._stop.set()
        if self._beat_task is not None and not self._beat_task.done():
            self._beat_task.cancel()
        self._beat_task = None
        if self.use_faulthandler:
            faulthandler.cancel_dump_traceback_later()
        self._thread = None

    async def _beat(self) -> None:
        while True:
            now = time.monotonic()
            late = now - self._last - self.beat_sec
            self._last = now
            if late > self.max_lag:
                self.max_lag = late
            if late >= LAG_LOG_SEC and now - self._last_lag_log >= LAG_LOG_EVERY_SEC:
                self._last_lag_log = now
                log.warning("STALL warning: the event loop was %.1fs late (rss %s MB)", late, _rss_mb())
            self._arm()
            await asyncio.sleep(self.beat_sec)

    def _arm(self) -> None:
        """(Re)start faulthandler's countdown: it dumps every thread if no beat comes in `stall_sec`."""
        if not self.use_faulthandler:
            return
        try:
            faulthandler.dump_traceback_later(self.stall_sec, repeat=False, file=sys.__stderr__, exit=False)
        except (RuntimeError, ValueError, OSError):  # no usable stderr: the watchdog thread still logs
            self.use_faulthandler = False

    # ---- on the watchdog thread --------------------------------------------
    def _watch(self) -> None:
        while not self._stop.wait(self.watch_sec):
            try:
                self.check()
            except Exception:  # noqa: BLE001 - the tracer must never raise into the app
                log.exception("stall tracer check failed")

    def check(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        age = now - self._last
        if age >= self.stall_sec:
            if self._stalled_since is None:
                self._stalled_since = self._last
                self._redumps = 0
                self._last_dump_at = now
                self.stall_count += 1
                self._log_stall(age, first=True)
            elif self._redumps < MAX_REDUMPS and now - self._last_dump_at >= REDUMP_SEC:
                self._redumps += 1
                self._last_dump_at = now
                self._log_stall(age, first=False)
        elif self._stalled_since is not None:
            lasted = self._last - self._stalled_since  # from the last beat before it to the first one after
            self.longest_stall = max(self.longest_stall, lasted)
            self.stalls.append({"at": time.time() - lasted, "seconds": round(lasted, 1)})
            del self.stalls[:-HISTORY]
            self._stalled_since = None
            log.warning("STALL over: the event loop was blocked for %.1fs and is answering again", lasted)

    def _log_stall(self, age: float, first: bool) -> None:
        frames = sys._current_frames()
        loop_frame = frames.get(self._loop_thread) if self._loop_thread is not None else None
        stack = format_stack(loop_frame) if loop_frame is not None else "(the loop thread's stack is not available)"
        try:
            load = "%.2f %.2f %.2f" % os.getloadavg()
        except (OSError, AttributeError):
            load = "n/a"
        log.warning(
            "STALL%s: the event loop has not answered for %.1fs | rss %s MB | cpu %.0fs | threads %d | load %s\n"
            "The thread running the loop is here (newest call last):\n%s",
            "" if first else " (still blocked)",
            age,
            _rss_mb(),
            time.process_time(),
            threading.active_count(),
            load,
            stack,
        )
        if not first and self.use_faulthandler:
            # The first dump of every thread comes from faulthandler's own timer; repeat it by hand.
            try:
                faulthandler.dump_traceback(file=sys.__stderr__, all_threads=True)
            except (RuntimeError, ValueError, OSError):
                pass

    # ---- for /api/stall ----------------------------------------------------
    def snapshot(self) -> dict:
        now = time.monotonic()
        return {
            "on": self._thread is not None,
            "stall_seconds_threshold": self.stall_sec,
            "last_beat_age_s": round(now - self._last, 2),
            "stalled_now": self._stalled_since is not None,
            "stalls_since_start": self.stall_count,
            "longest_stall_s": round(self.longest_stall, 1),
            "worst_lag_s": round(max(self.max_lag, 0.0), 2),
            "recent": list(self.stalls),
            "rss_mb": _rss_mb(),
            "threads": threading.active_count(),
            "since": self.started_at,
        }


tracer = StallTracer()
