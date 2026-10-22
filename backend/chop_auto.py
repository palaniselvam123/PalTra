"""Trend quality auto re-scan, run by the server (no browser needed).

Every `every_min` minutes (5-60, the owner's choice) while NSE is open, the
server starts the Trend quality scan (`chop_scan`) itself with the card's last
choices: candle size, Armed only (read fresh each time), quiet minutes, price
range and the quiet-runner Telegram. The settings are saved next to the SMA
terminal's database, so a restart or deploy keeps them. A manual Scan now
counts as a run, so the next automatic one waits a full interval.

Read-only: candles only, no order, no bot setting is read or changed here.
On by default only on the scanner server (PALTRA_ROLE=scanner); a desk with
SCANS=off never runs it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Awaitable, Callable

import server_role
from chop_scan import QUIET_MIN_DEFAULT
from groww_client import IST, market_is_open

log = logging.getLogger("sma.chop_auto")

EVERY_CHOICES = (5, 10, 15, 20, 30, 45, 60)
DEFAULT_EVERY = 30
CANDLE_CHOICES = (1, 2, 3, 5, 10, 15)
CHECK_SEC = 20


@dataclass
class AutoSettings:
    on: bool = False
    every_min: int = DEFAULT_EVERY
    minutes: int = 5
    armed_only: bool = True
    quiet_min: int = QUIET_MIN_DEFAULT
    alert_on_quiet: bool = True
    price_min: float = 0.0
    price_max: float = 0.0
    # The card's scan lists ("armed", "fno", "nifty50", "nifty100", "niftybank"); empty = armed_only decides.
    lists: list = field(default_factory=list)


SCAN_LISTS = ("armed", "fno", "nifty50", "nifty100", "niftybank")


def settings_path() -> Path:
    """CHOP_AUTO_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("CHOP_AUTO_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        db = Path(url[len("sqlite:///"):])
        return db.with_name("chop_auto.json")
    return Path("chop_auto.json")


def _clean(raw: dict) -> AutoSettings:
    base = AutoSettings(on=server_role.is_scanner())
    known = {f.name for f in fields(AutoSettings)}
    for key, value in raw.items():
        if key in known:
            setattr(base, key, value)
    base.on = bool(base.on)
    base.armed_only = bool(base.armed_only)
    base.alert_on_quiet = bool(base.alert_on_quiet)
    base.every_min = int(base.every_min) if int(base.every_min) in EVERY_CHOICES else DEFAULT_EVERY
    base.minutes = int(base.minutes) if int(base.minutes) in CANDLE_CHOICES else 5
    base.quiet_min = min(375, max(1, int(base.quiet_min)))
    base.price_min = max(0.0, float(base.price_min))
    base.price_max = max(0.0, float(base.price_max))
    raw_lists = base.lists if isinstance(base.lists, (list, tuple)) else []
    base.lists = [k for k in dict.fromkeys(str(x).lower() for x in raw_lists) if k in SCAN_LISTS]
    return base


def next_open(after: dt.datetime) -> dt.datetime:
    """The next 09:15 IST on a weekday at or after `after` (holidays are not known here)."""
    day = after.astimezone(IST)
    opening = day.replace(hour=9, minute=15, second=0, microsecond=0)
    if day.weekday() < 5 and day <= opening:
        return opening
    opening += dt.timedelta(days=1)
    while opening.weekday() >= 5:
        opening += dt.timedelta(days=1)
    return opening


Starter = Callable[[AutoSettings], Awaitable[None]]


class ChopAuto:
    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self.settings = self._load()
        self.last_run_at: dt.datetime | None = None
        self.last_error: str | None = None
        self._task: asyncio.Task | None = None

    def reload(self) -> None:
        """Read the saved settings again (at boot, once the database path is known)."""
        self.settings = self._load()

    @property
    def path(self) -> Path:
        return self._path or settings_path()

    def _load(self) -> AutoSettings:
        try:
            raw = json.loads(self.path.read_text())
            return _clean(raw if isinstance(raw, dict) else {})
        except FileNotFoundError:
            return _clean({})
        except Exception as exc:  # noqa: BLE001
            log.warning("chop auto settings unreadable (%s); using defaults", exc)
            return _clean({})

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(asdict(self.settings)))
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not save chop auto settings: %s", exc)

    def update(self, **changes) -> AutoSettings:
        was_on = self.settings.on
        merged = {**asdict(self.settings), **{k: v for k, v in changes.items() if v is not None}}
        self.settings = _clean(merged)
        if self.settings.on and not was_on:
            # Switched on: scan at the next check rather than a whole interval later.
            self.last_run_at = None
            self.last_error = None
        self._save()
        return self.settings

    def note_run(self, now: dt.datetime | None = None) -> None:
        """A scan just started (manual or automatic): the next automatic one waits a full interval."""
        self.last_run_at = now or dt.datetime.now(IST)

    def _due_at(self, now: dt.datetime) -> dt.datetime:
        if self.last_run_at is None:
            return now
        return self.last_run_at + dt.timedelta(minutes=self.settings.every_min)

    def next_at(self, now: dt.datetime | None = None) -> dt.datetime | None:
        """When the next automatic scan should start: never before the market opens."""
        if not self.settings.on:
            return None
        now = now or dt.datetime.now(IST)
        due = self._due_at(now)
        when = max(due, now)
        return when if market_is_open(when) else next_open(when)

    def due(self, now: dt.datetime) -> bool:
        if not self.settings.on or not server_role.scans_enabled() or not market_is_open(now):
            return False
        return now >= self._due_at(now)

    async def tick(self, start: Starter, now: dt.datetime | None = None) -> bool:
        """Start a scan when one is due. True when it started one."""
        now = now or dt.datetime.now(IST)
        if not self.due(now):
            return False
        self.note_run(now)
        try:
            await start(self.settings)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = (str(exc).strip() or type(exc).__name__)[:200]
            log.warning("auto re-scan did not start: %s", self.last_error)
            return False
        return True

    def start_loop(self, start: Starter) -> None:
        if self._task is not None and not self._task.done():
            return

        async def loop() -> None:
            while True:
                try:
                    await self.tick(start)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — never let the loop die
                    log.exception("chop auto loop")
                await asyncio.sleep(CHECK_SEC)

        self._task = asyncio.create_task(loop())

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    def snapshot(self, now: dt.datetime | None = None) -> dict:
        nxt = self.next_at(now)
        return {
            **asdict(self.settings),
            "every_choices": list(EVERY_CHOICES),
            "last_run_at": self.last_run_at.isoformat(timespec="seconds") if self.last_run_at else None,
            "next_at": nxt.isoformat(timespec="seconds") if nxt else None,
            "error": self.last_error,
            "scans_enabled": server_role.scans_enabled(),
        }
