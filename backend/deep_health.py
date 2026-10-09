"""Deep health: is the app really working, not just answering?

`/api/health` says the web server replies. A frozen bot loop still replies. This
looks at what a watcher cares about: the event loop is beating, and every bot's
1-second loop has ticked lately. Read-only, no secrets, safe to call without
signing in (it says nothing about money, stocks or mode).

`evaluate` is a pure function so the rules can be tested; `snapshot` reads the
live engines.
"""
from __future__ import annotations

import time

TICK_FAIL_SEC = 30.0  # a running bot (or any bot in market hours) that has not ticked this long is stuck
IDLE_TICK_FAIL_SEC = 180.0  # the loop sleeps 5 s after the close; this long means it is gone
BEAT_FAIL_SEC = 10.0  # the stall tracer's heartbeat this old means the event loop is blocked


def evaluate(*, market_open: bool, tracer: dict, bots: list[dict]) -> dict:
    """`bots`: one dict per bot with `bot`, `status`, `last_tick_age_s` (None = never) and `loop_alive`."""
    reasons: list[str] = []
    if tracer.get("on"):
        if tracer.get("stalled_now") or (tracer.get("last_beat_age_s") or 0) > BEAT_FAIL_SEC:
            reasons.append("The app's event loop is blocked right now: every page and bot is frozen.")
    for b in bots:
        n = b["bot"]
        if not b.get("loop_alive", True):
            reasons.append(f"Bot {n}'s loop has stopped running.")
            continue
        age = b.get("last_tick_age_s")
        limit = TICK_FAIL_SEC if (market_open or b.get("status") == "RUNNING") else IDLE_TICK_FAIL_SEC
        if age is None:
            if market_open or b.get("status") == "RUNNING":
                reasons.append(f"Bot {n} has not ticked since the app started.")
        elif age > limit:
            reasons.append(f"Bot {n} has not ticked for {int(age)} s.")
    return {
        "ok": not reasons,
        "reasons": reasons,
        "market_open": market_open,
        "loop": {
            "beat_age_s": tracer.get("last_beat_age_s"),
            "stalled_now": tracer.get("stalled_now"),
            "stalls_since_start": tracer.get("stalls_since_start"),
            "worst_lag_s": tracer.get("worst_lag_s"),
        },
        "bots": bots,
        "checked_at": time.time(),
    }


def bot_row(bot: int, eng, task, now: float | None = None) -> dict:
    now = time.monotonic() if now is None else now
    stamp = getattr(eng, "last_tick_at", None)
    return {
        "bot": bot,
        "status": getattr(eng, "status", None),
        "last_tick_age_s": None if stamp is None else round(now - stamp, 1),
        "loop_alive": task is not None and not task.done(),
    }
