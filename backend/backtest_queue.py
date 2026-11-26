"""Backtest queue and schedule (owner's request: two auto-arm backtests playing at once froze the backtest server).

Practice money only: every item is an ordinary auto-arm replay (`POST /api/replay/auto-arm`, auto_arm.py) started
through the same route, so it never orders and never changes a Trade list. Nothing here touches the live bots.

**Queue**: items (`Item`, at most `MAX_ITEMS` kept) each carry the full auto-arm start body (bot, days, source TREND /
ACTIVE / BOTH, thresholds). `Runner.step` (every `CHECK_SEC` while the process runs, `run`) starts the first QUEUED
item only while no replay player of any bot is LOADING / PLAYING / PAUSED, so two backtests never play together; the
item is PLAYING until that bot's player is done: FINISHED → DONE, ERROR → FAILED, stopped → STOPPED. A refusal of the
start (a LIVE bot, no saved Most active scans, no Groww login) makes it FAILED with the reason, and the queue moves on.
A restart leaves a PLAYING item to the run's own state (`run_status`): FINISHED → DONE, else INTERRUPTED after
`RESTART_GRACE_SEC` (the backtest server resumes a cut-short run by itself; the Backtests tab has Resume).

**Schedule** (`Schedule`): on weekdays once a day after `at` (15:31-23:59 IST), one item per ticked bot with today's
date and the saved `template` (the auto-arm form's choices) is added to the queue (`due`, `last_day`). The backtest
server stops itself when idle, so the desk wakes it (`wake_loop`, needs BACKTEST_URL and BACKTEST_LINK_TOKEN on the
desk): from `WAKE_FROM` it asks the backtest server's `GET /api/link/backtest/queue-due` (which starts the machine),
and again at the time that answer gives, until the day's run is queued. Kept in `backtest_queue.json` next to the SMA
DB (`BACKTEST_QUEUE_FILE`). The queue keeps the backtest server awake while it holds work (`busy`).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Awaitable, Callable
from zoneinfo import ZoneInfo

log = logging.getLogger("sma.backtest_queue")

IST = ZoneInfo("Asia/Kolkata")
CHECK_SEC = 10
MAX_ITEMS = 60
MAX_QUEUED = 30
RESTART_GRACE_SEC = 180
BUSY = ("LOADING", "PLAYING", "PAUSED")
AT_FROM, AT_TO = dt.time(15, 31), dt.time(23, 59)
WAKE_FROM = dt.time(15, 31)
WAKE_POLL_SEC = 60
WAKE_PATH = "/api/link/backtest/queue-due"
# Fields of the template the schedule keeps (the auto-arm start body without the day and the bot).
TEMPLATE_SKIP = ("date", "end_date", "bot")


@dataclass
class Item:
    id: str
    body: dict
    label: str = ""
    origin: str = "manual"  # manual | schedule
    #: AUTOARM = an auto-arm backtest (AutoArmStart body); REPLAY = an ordinary replay (ReplayStart body), e.g. a run
    #: queued again from the Backtests tab.
    kind: str = "AUTOARM"
    status: str = "QUEUED"  # QUEUED | PLAYING | DONE | FAILED | STOPPED | INTERRUPTED | CANCELLED
    added_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    run_id: int | None = None
    note: str = ""


@dataclass
class Schedule:
    on: bool = False
    at: str = "15:45"
    bots: list = field(default_factory=lambda: [1])
    template: dict = field(default_factory=dict)
    last_day: str | None = None
    last_note: str = ""


@dataclass
class State:
    items: list = field(default_factory=list)
    schedule: Schedule = field(default_factory=Schedule)
    paused: bool = False


def _path() -> Path:
    explicit = os.environ.get("BACKTEST_QUEUE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("backtest_queue.json")
    return Path("backtest_queue.json")


def hhmm(raw, low: dt.time = AT_FROM, high: dt.time = AT_TO) -> str | None:
    try:
        h, m = str(raw).strip().split(":")
        t = dt.time(int(h), int(m))
    except (ValueError, TypeError):
        return None
    return t.strftime("%H:%M") if low <= t <= high else None


def _bots(raw) -> list[int]:
    out: list[int] = []
    for b in raw if isinstance(raw, list) else []:
        try:
            n = int(b)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 4 and n not in out:
            out.append(n)
    return sorted(out)


def load() -> State:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    items: list[Item] = []
    for r in raw.get("items") or []:
        if isinstance(r, dict) and isinstance(r.get("body"), dict) and r.get("id"):
            items.append(Item(**{k: r[k] for k in Item.__dataclass_fields__ if k in r}))
    sraw = raw.get("schedule") if isinstance(raw.get("schedule"), dict) else {}
    s = Schedule(**{k: sraw[k] for k in Schedule.__dataclass_fields__ if k in sraw})
    s.on = bool(s.on)
    s.at = hhmm(s.at) or "15:45"
    s.bots = _bots(s.bots) or [1]
    s.template = s.template if isinstance(s.template, dict) else {}
    return State(items=items, schedule=s, paused=bool(raw.get("paused")))


def save(state: State) -> None:
    path = _path()
    state.items = state.items[-MAX_ITEMS:]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"items": [asdict(i) for i in state.items], "schedule": asdict(state.schedule),
                               "paused": state.paused}))
    tmp.replace(path)


def _days(body: dict) -> str:
    day, end = body.get("date"), body.get("end_date")
    return day if not end or end == day else f"{day} → {end}"


def label_for(body: dict, kind: str = "AUTOARM") -> str:
    if kind == "REPLAY":
        if body.get("as_armed"):
            what = "as armed live"
        else:
            names = [str(x) for x in body.get("symbols") or []]
            what = ", ".join(names[:4]) or "armed stocks"
            if len(names) > 4:
                what += f" +{len(names) - 4}"
        note = f" · {body['note']}" if body.get("note") else ""
        return f"Bot {body.get('bot', 1)} · replay · {_days(body)} · {what}{note}"
    src = {"TREND": "Trend quality", "ACTIVE": "Most active", "BOTH": "Trend quality + Most active"}.get(
        str(body.get("source", "BOTH")), "Auto-arm")
    day, end = body.get("date"), body.get("end_date")
    days = day if not end or end == day else f"{day} → {end}"
    return f"Bot {body.get('bot', 1)} · {src} auto-arm · {days}"


def add(state: State, body: dict, origin: str = "manual", kind: str = "AUTOARM") -> Item:
    """Raises ValueError when the queue is full or the body has no day."""
    if not body.get("date"):
        raise ValueError("Pick the days to backtest.")
    if sum(1 for i in state.items if i.status == "QUEUED") >= MAX_QUEUED:
        raise ValueError(f"The queue holds at most {MAX_QUEUED} waiting backtests.")
    item = Item(id=uuid.uuid4().hex[:10], body=dict(body), label=label_for(body, kind), origin=origin, kind=kind,
                added_at=time.time())
    state.items.append(item)
    return item


def due(now: dt.datetime, s: Schedule) -> bool:
    local = now.astimezone(IST)
    if not s.on or local.weekday() >= 5 or s.last_day == local.date().isoformat() or not s.template:
        return False
    at = hhmm(s.at)
    return at is not None and local.strftime("%H:%M") >= at


def due_at(now: dt.datetime, s: Schedule) -> str | None:
    """Today's schedule time while today's run is still to be queued, else None (for the desk's wake-up)."""
    local = now.astimezone(IST)
    if not s.on or local.weekday() >= 5 or s.last_day == local.date().isoformat() or not s.template:
        return None
    return hhmm(s.at)


def schedule_today(state: State, now: dt.datetime) -> list[Item]:
    """The schedule's items for today (one per ticked bot), added to the queue; marks the day done."""
    s = state.schedule
    day = now.astimezone(IST).date().isoformat()
    added: list[Item] = []
    notes: list[str] = []
    for bot in s.bots:
        body = {k: v for k, v in s.template.items() if k not in TEMPLATE_SKIP}
        body.update(date=day, end_date=day, bot=bot)
        try:
            added.append(add(state, body, origin="schedule"))
        except ValueError as exc:
            notes.append(f"Bot {bot}: {exc}")
    s.last_day = day
    s.last_note = (f"{day}: queued today's auto-arm backtest on " + ", ".join(f"Bot {b}" for b in s.bots)) if added else ""
    if notes:
        s.last_note = (s.last_note + ". " if s.last_note else "") + "; ".join(notes)
    return added


StartFn = Callable[[dict, str], Awaitable[None]]
SessionFn = Callable[[int], object]
RunStatusFn = Callable[[int], "str | None"]


class Runner:
    def __init__(self) -> None:
        self.booted_at = time.time()
        self._lock = asyncio.Lock()
        self.starting = False

    def busy(self) -> bool:
        """Work waiting or playing: keeps the backtest server awake."""
        try:
            st = load()
        except Exception:  # noqa: BLE001
            return False
        return not st.paused and any(i.status in ("QUEUED", "PLAYING") for i in st.items)

    async def step(self, start: StartFn, session_of: SessionFn, run_status: RunStatusFn,
                   now: dt.datetime | None = None) -> None:
        async with self._lock:
            now = now or dt.datetime.now(IST)
            state = load()
            changed = False
            if due(now, state.schedule):
                schedule_today(state, now)
                changed = True
            for item in state.items:
                if item.status == "PLAYING":
                    changed |= self._follow(item, session_of, run_status)
            playing = any(i.status == "PLAYING" for i in state.items)
            any_busy = any(getattr(session_of(b), "status", "IDLE") in BUSY for b in (1, 2, 3, 4))
            nxt = next((i for i in state.items if i.status == "QUEUED"), None)
            if nxt is not None and not playing and not any_busy and not state.paused:
                save(state)
                changed = False
                await self._start(nxt, start, session_of)
                state = load()
                for i, it in enumerate(state.items):
                    if it.id == nxt.id:
                        state.items[i] = nxt
                changed = True
            if changed:
                save(state)

    def _follow(self, item: Item, session_of: SessionFn, run_status: RunStatusFn) -> bool:
        session = session_of(int(item.body.get("bot", 1)))
        status = getattr(session, "status", "IDLE")
        rid = getattr(session, "run_id", None)
        mine = item.run_id is None or rid is None or rid == item.run_id
        if status in BUSY and mine:
            if item.run_id is None and rid is not None:
                item.run_id = int(rid)
                return True
            return False
        if mine and status == "FINISHED":
            item.status, item.note = "DONE", item.note or "finished"
        elif mine and status == "ERROR" and item.run_id in (None, rid):
            item.status, item.note = "FAILED", str(getattr(session, "error", "") or "the replay stopped with an error")
        else:
            saved = run_status(item.run_id) if item.run_id is not None else None
            if saved == "FINISHED":
                item.status, item.note = "DONE", item.note or "finished"
            elif saved == "INTERRUPTED" and time.time() - self.booted_at < RESTART_GRACE_SEC:
                return False  # the backtest server resumes a cut-short run by itself shortly after a restart
            elif saved == "INTERRUPTED":
                item.status, item.note = "INTERRUPTED", "cut short by a restart: Resume it from the Backtests tab"
            elif saved == "RUNNING" and status in BUSY:
                return False
            else:
                item.status, item.note = "STOPPED", "stopped before the end"
        item.finished_at = time.time()
        return True

    async def _start(self, item: Item, start: StartFn, session_of: SessionFn) -> None:
        item.started_at = time.time()
        self.starting = True
        try:
            await start(dict(item.body), item.kind)
            item.status, item.note = "PLAYING", ""
            item.run_id = getattr(session_of(int(item.body.get("bot", 1))), "run_id", None)
        except Exception as exc:  # noqa: BLE001 - a refusal fails this item; the queue moves on
            detail = getattr(exc, "detail", None) or str(exc) or type(exc).__name__
            item.status, item.note, item.finished_at = "FAILED", f"not started: {detail}", time.time()
        finally:
            self.starting = False

    async def run(self, start: StartFn, session_of: SessionFn, run_status: RunStatusFn, poll: float = CHECK_SEC) -> None:
        """Loop; never raises."""
        while True:
            try:
                await self.step(start, session_of, run_status)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("backtest queue pass failed")
            await asyncio.sleep(poll)


runner = Runner()


def view(state: State | None = None, now: dt.datetime | None = None) -> dict:
    state = state or load()
    now = now or dt.datetime.now(IST)
    return {
        "items": [asdict(i) for i in reversed(state.items)],
        "paused": state.paused,
        "schedule": {**asdict(state.schedule), "due_at": due_at(now, state.schedule)},
        "waiting": sum(1 for i in state.items if i.status == "QUEUED"),
        "playing": next((asdict(i) for i in state.items if i.status == "PLAYING"), None),
    }


# ---- The desk wakes the backtest server for its schedule ----

async def wake_loop(fetch: Callable[[str], dict], now_fn=lambda: dt.datetime.now(IST), poll: float = WAKE_POLL_SEC) -> None:
    """Desk only. Each weekday from WAKE_FROM, ask the backtest server when its scheduled run is due (the request
    itself starts a stopped machine) and ask again at that time, until it says nothing is due today. Never raises."""
    next_at: dt.datetime | None = None
    done_day: str | None = None
    last_due = WAKE_FROM.strftime("%H:%M")  # remembered across days, so the machine is woken near the right time
    while True:
        try:
            local = now_fn().astimezone(IST)
            day = local.date().isoformat()
            if local.weekday() < 5 and done_day != day:
                if next_at is None or next_at.date() != local.date():
                    h, m = map(int, last_due.split(":"))
                    next_at = local.replace(hour=h, minute=m, second=0, microsecond=0)
                if local >= next_at:
                    body = await asyncio.to_thread(fetch, WAKE_PATH)
                    when = body.get("due_at") if isinstance(body, dict) else None
                    if not when:
                        done_day = day
                    else:
                        last_due = str(when)
                        h, m = map(int, last_due.split(":"))
                        at = local.replace(hour=h, minute=m, second=0, microsecond=0)
                        # Due already: the server is awake now and queues it itself; look again in 10 minutes.
                        next_at = at if at > local else local + dt.timedelta(minutes=10)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.info("backtest queue wake-up: %s", exc)
            next_at = now_fn().astimezone(IST) + dt.timedelta(minutes=10)
        await asyncio.sleep(poll)
