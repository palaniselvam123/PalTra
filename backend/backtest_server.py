"""Backtest server (``PALTRA_ROLE=backtest``): replays on their own machine, started on demand.

The desk (``paltra``) keeps the live bots; backtests (replays) run on a second Fly app
(``paltra-backtest``, ``fly.backtest.toml``, setup in ``BACKTEST.md``) so a heavy backtest
never slows the bots. Same image; this role:

* **Never orders** (``server_role.no_orders``: every Groww order path refuses, the LIVE switch
  answers 403) and runs **no live bots, scans or alerts**: ``main.boot_engine`` only prepares
  the settings rows and the replay players, and the bots' Start answers 409.
* **Copies the bots' settings from the desk** (``sync``): every ``BotConfig`` row (bots 1-4,
  research) with its Trade list and per-stock settings, read from the desk's
  ``GET /api/link/backtest/settings``, written here with PAPER forced. At boot and on
  "Sync from desk". Nothing is ever written back to the desk.
* **Uses the desk's Groww session** for past candles (``remote_token``): the desk's access
  token comes from ``GET /api/link/backtest/groww`` every ``TOKEN_EVERY_SEC`` and is kept in
  memory only, never logged or saved. Logging in again with the same API key here could end
  the desk's session, so this server never logs in to Groww itself.
* **Stops itself when idle** (``run``): no backtest loading or playing and no page request for
  ``idle_minutes()`` (default 30, 10-240, saved next to the SMA DB) → SIGTERM → the process
  exits → Fly stops the machine (``auto_start_machines`` starts it on the next visit). A stopped
  machine is not billed for CPU or memory.

Both link calls need ``Authorization: Bearer <BACKTEST_LINK_TOKEN>`` (24+ characters, set on the
desk and the backtest server only; not the scanner's token, since this link carries the Groww
session).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Callable

import httpx

import server_role

SETTINGS_PATH = "/api/link/backtest/settings"
GROWW_PATH = "/api/link/backtest/groww"
TOKEN_EVERY_SEC = 600
CHECK_EVERY_SEC = 30
IDLE_DEFAULT_MIN = 30
IDLE_MIN, IDLE_MAX = 10, 240
BUSY_STATUSES = ("LOADING", "PLAYING")
QUIET_PATHS = ("/api/health", "/api/health/deep", "/api/role")

log = logging.getLogger("sma.backtest")


class _State:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started = time.monotonic()
        self.last_request = time.monotonic()
        self.last_busy = time.monotonic()
        self.token = ""
        self.token_at = 0.0
        self.token_error: str | None = None
        self.synced_at: float | None = None
        self.synced: list[str] = []
        self.sync_error: str | None = None
        self.stopping = False


state = _State()


# ---- idle time ---------------------------------------------------------------


def note_request(path: str) -> None:
    """A page request keeps the server up (health checks and the role probe do not)."""
    if path not in QUIET_PATHS:
        state.last_request = time.monotonic()


def _settings_path() -> Path:
    explicit = os.environ.get("BACKTEST_SETTINGS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("backtest_server.json")
    return Path("backtest_server.json")


def idle_minutes() -> int:
    try:
        value = int(json.loads(_settings_path().read_text()).get("idle_min", IDLE_DEFAULT_MIN))
    except (OSError, ValueError, TypeError, AttributeError):
        return IDLE_DEFAULT_MIN
    return min(IDLE_MAX, max(IDLE_MIN, value))


def set_idle_minutes(minutes: int) -> int:
    value = min(IDLE_MAX, max(IDLE_MIN, int(minutes)))
    path = _settings_path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"idle_min": value}))
    tmp.replace(path)
    return value


def busy(replays: dict) -> bool:
    return any(getattr(r, "status", "") in BUSY_STATUSES for r in replays.values())


def idle_seconds_left(replays: dict, now: float | None = None) -> float:
    now = time.monotonic() if now is None else now
    if busy(replays):
        state.last_busy = now
    last = max(state.last_request, state.last_busy)
    return max(0.0, idle_minutes() * 60 - (now - last))


def stop_now(reason: str) -> None:
    """Ask uvicorn to shut down; the process exits and Fly stops the machine."""
    if state.stopping:
        return
    state.stopping = True
    log.warning("backtest server stopping: %s", reason)
    signal.raise_signal(signal.SIGTERM)


# ---- the desk link -----------------------------------------------------------


def remote_token() -> str:
    """The desk's Groww access token, as last fetched (memory only)."""
    return state.token if server_role.is_backtest() else ""


async def _desk_get(path: str, client: httpx.AsyncClient | None = None) -> dict:
    token = server_role.backtest_link_token()
    if not token:
        raise RuntimeError("BACKTEST_LINK_TOKEN is not set on this server.")
    url = f"{server_role.desk_url()}{path}"
    headers = {"Authorization": f"Bearer {token}"}
    if client is None:
        async with httpx.AsyncClient(timeout=15) as own:
            resp = await own.get(url, headers=headers)
    else:
        resp = await client.get(url, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"the desk answered {resp.status_code}")
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("the desk sent an unexpected answer")
    return body


async def refresh_token(client: httpx.AsyncClient | None = None) -> bool:
    """Fetch the desk's Groww session. Keeps the old token when the desk cannot be reached."""
    try:
        body = await _desk_get(GROWW_PATH, client)
    except Exception as exc:  # noqa: BLE001
        state.token_error = str(exc)[:200]
        return False
    token = str(body.get("token") or "").strip()
    changed = token != state.token
    state.token, state.token_at = token, time.monotonic()
    state.token_error = None if token else "The desk has no Groww session today: log in to Groww on the desk."
    if changed:
        try:
            from groww_client import note_fresh_desk_token

            note_fresh_desk_token()
        except Exception:  # noqa: BLE001
            pass
    return bool(token)


async def sync(apply: Callable[[dict], list[str]], client: httpx.AsyncClient | None = None) -> dict:
    """Copy the desk's bot settings here (PAPER forced by `apply`). Raises with a reason on failure."""
    try:
        body = await _desk_get(SETTINGS_PATH, client)
        rows = body.get("rows")
        if not isinstance(rows, dict) or not rows:
            raise RuntimeError("the desk sent no settings")
        done = await asyncio.to_thread(apply, rows)
    except Exception as exc:  # noqa: BLE001
        state.sync_error = str(exc)[:200]
        raise
    state.synced_at, state.synced, state.sync_error = time.time(), done, None
    return {"synced": done}


def status(replays: dict) -> dict:
    left = idle_seconds_left(replays)
    return {
        "role": server_role.role(),
        "desk_url": server_role.desk_url(),
        "link": bool(server_role.backtest_link_token()),
        "idle_min": idle_minutes(),
        "idle_left_sec": round(left),
        "busy": busy(replays),
        "groww": bool(state.token),
        "groww_error": state.token_error,
        "synced_at": state.synced_at,
        "synced": state.synced,
        "sync_error": state.sync_error,
        "up_sec": round(time.monotonic() - state.started),
    }


async def run(apply: Callable[[dict], list[str]], replays: Callable[[], dict]) -> None:
    """Sync once, then keep the Groww session fresh and stop the server once it has been idle."""
    if not server_role.is_backtest():
        return
    async with httpx.AsyncClient(timeout=15) as client:
        await refresh_token(client)
        try:
            await sync(apply, client)
        except Exception:  # noqa: BLE001 - shown on the page; the last synced settings stay
            pass
        while True:
            await asyncio.sleep(CHECK_EVERY_SEC)
            if time.monotonic() - state.token_at >= TOKEN_EVERY_SEC or not state.token:
                await refresh_token(client)
            if idle_seconds_left(replays()) <= 0:
                stop_now(f"idle for {idle_minutes()} minutes")
                return
