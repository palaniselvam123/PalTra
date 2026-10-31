"""Pull the backtest server's scan history into the desk's (scan_sync.py).

The backtest server copies every scan it finishes to the desk as it goes (`backtest_server.push_scan`), but
a scan made before that link was set, or while the desk was down, never arrives. `pull` asks the backtest
server for its saved scans (`GET /api/link/backtest/scan-export`, `BACKTEST_LINK_TOKEN`), and copies each
one the desk does not have yet (`scan_history.find` by kind and time) with its rows and accuracy check.
Desk only; read-only on the backtest server; nothing in the strategy reads it.
"""
from __future__ import annotations

import logging
from typing import Callable

import scan_history
import server_role

EXPORT_PATH = "/api/link/backtest/scan-export"
MAX_DAYS = 90
log = logging.getLogger(__name__)

SNAPSHOT_KEYS = ("as_of", "finished_at", "minutes", "total", "failed", "error", "rows")


def export_runs(days: int) -> list[dict]:
    """What the backtest server hands out: every saved scan of the last `days` days (ids, kind, time)."""
    out: list[dict] = []
    for kind in ("cross", "chop", "active"):
        for day in scan_history.days(kind, limit=max(1, min(MAX_DAYS, days))):
            for run in scan_history.runs(day["day"], kind):
                out.append({"id": run["id"], "kind": run["kind"], "as_of": run["as_of"], "day": run["day"]})
    return out


def snapshot_of(detail: dict) -> dict:
    """A saved run (`scan_history.get`) as the snapshot `scan_history.save` takes."""
    return {k: detail.get(k) for k in SNAPSHOT_KEYS}


def pull(fetch: Callable[[str], dict], days: int = 14) -> dict:
    """Copy the backtest server's scans the desk lacks. `fetch(path)` GETs a JSON object from that server.

    Returns {"copied", "checks", "already", "errors"}; never raises on one bad run.
    """
    listing = fetch(f"{EXPORT_PATH}?days={max(1, min(MAX_DAYS, int(days)))}")
    runs = listing.get("runs") if isinstance(listing, dict) else None
    if not isinstance(runs, list):
        raise RuntimeError("the backtest server sent no scan list")
    copied = checks = already = 0
    errors: list[str] = []
    for run in runs:
        try:
            kind, as_of, run_id = str(run.get("kind")), str(run.get("as_of")), int(run.get("id"))
        except (TypeError, ValueError, AttributeError):
            continue
        if scan_history.find(kind, as_of) is not None:
            already += 1
            continue
        try:
            detail = fetch(f"{EXPORT_PATH}/{run_id}")
            meta = {**(detail.get("meta") if isinstance(detail.get("meta"), dict) else {}), "server": "backtest"}
            new_id = scan_history.save_copy(kind, snapshot_of(detail), meta)
            if new_id is None:
                already += 1
                continue
            copied += 1
            if isinstance(detail.get("check"), dict):
                scan_history.save_check(new_id, detail["check"])
                checks += 1
        except Exception as exc:  # noqa: BLE001 - one bad run must not stop the rest
            log.warning("could not copy scan %s from the backtest server: %s", run_id, exc)
            errors.append(f"{kind} {as_of}: {exc}")
    return {"copied": copied, "checks": checks, "already": already, "errors": errors[:10]}


def http_fetch(path: str) -> dict:
    """GET `path` on the backtest server with the link token (worker thread)."""
    import httpx

    base = server_role.backtest_url()
    token = server_role.backtest_link_token()
    if not base:
        raise RuntimeError("BACKTEST_URL is not set on this desk.")
    if not token:
        raise RuntimeError("BACKTEST_LINK_TOKEN is not set on this desk.")
    with httpx.Client(timeout=60) as client:
        resp = client.get(f"{base}{path}", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        raise RuntimeError(f"the backtest server answered {resp.status_code}")
    return resp.json()
