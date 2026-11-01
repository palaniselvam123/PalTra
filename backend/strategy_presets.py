"""Saved strategies and a history of saves, so a setting tried last week can be found again.

Two things, both kept in ``strategy_presets.json`` next to the SMA database
(``STRATEGY_PRESETS_FILE`` to move it):

* **Presets**: a named copy of a desk's strategy settings ("Save current as…" on the
  Settings page), at most ``MAX_PRESETS``. Loading one puts its numbers into the form;
  the usual Save then writes them, with every check Save makes.
* **History**: every Save of a desk's settings (``main._put_config``) keeps a snapshot,
  the last ``MAX_HISTORY`` per desk, skipping a save that changed nothing, so "what was
  it before?" always has an answer.

Only the strategy fields are kept (``strategy_only``: the update fields minus
``COPY_SKIP``: never the Trade list, mode, name, per-stock settings or the all-bots
rows). Nothing here places an order or changes a bot by itself.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
from pathlib import Path

MAX_PRESETS = 50
MAX_HISTORY = 30
DESKS = ("1", "2", "3", "4", "5", "research")

_lock = threading.Lock()


def settings_path() -> Path:
    explicit = os.environ.get("STRATEGY_PRESETS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("strategy_presets.json")
    return Path("strategy_presets.json")


def desk_key(desk: object) -> str:
    """"1".."5" or "research"; anything else reads as the main desk."""
    key = str(desk or "1").strip().lower()
    return key if key in DESKS else "1"


def strategy_only(settings: dict, allowed: set[str], skip: frozenset[str] | set[str]) -> dict:
    """The strategy fields of a settings dict: known update fields, never the skipped bookkeeping ones."""
    return {k: v for k, v in (settings or {}).items() if k in allowed and k not in skip and not k.startswith("_")}


def _now() -> str:
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d %H:%M:%S")


def _empty() -> dict:
    return {"presets": [], "history": {}, "next_id": 1}


def load() -> dict:
    path = settings_path()
    try:
        raw = json.loads(path.read_text())
    except (OSError, ValueError):
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    out = _empty()
    out["presets"] = [p for p in raw.get("presets") or [] if isinstance(p, dict) and p.get("id") is not None]
    out["history"] = {k: v for k, v in (raw.get("history") or {}).items() if isinstance(v, list)}
    out["next_id"] = int(raw.get("next_id") or (max((int(p["id"]) for p in out["presets"]), default=0) + 1))
    return out


def _write(data: dict) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=1, sort_keys=True))
    os.replace(tmp, path)


def listing(desk: object) -> dict:
    """Every preset (any desk) and this desk's history, newest first."""
    key = desk_key(desk)
    data = load()
    history = list(reversed(data["history"].get(key, [])))
    return {"desk": key, "presets": list(reversed(data["presets"])), "history": history}


def save_preset(name: str, desk: object, settings: dict) -> dict:
    name = " ".join(str(name or "").split())[:60] or "Untitled"
    key = desk_key(desk)
    with _lock:
        data = load()
        # The same name on the same desk is replaced, so "Save current as" updates a preset in place.
        data["presets"] = [p for p in data["presets"] if not (p.get("name") == name and p.get("desk") == key)]
        pid = int(data["next_id"])
        data["next_id"] = pid + 1
        data["presets"].append({"id": pid, "name": name, "desk": key, "saved_at": _now(), "settings": settings})
        data["presets"] = data["presets"][-MAX_PRESETS:]
        _write(data)
    return listing(key)


def delete_preset(pid: int, desk: object = "1") -> dict:
    with _lock:
        data = load()
        before = len(data["presets"])
        data["presets"] = [p for p in data["presets"] if int(p.get("id", -1)) != int(pid)]
        if len(data["presets"]) != before:
            _write(data)
    return listing(desk)


def remember(desk: object, settings: dict) -> None:
    """Keep a save's strategy fields in the desk's history; a save that changed nothing is not repeated."""
    key = desk_key(desk)
    with _lock:
        data = load()
        rows = data["history"].setdefault(key, [])
        if rows and rows[-1].get("settings") == settings:
            return
        rows.append({"saved_at": _now(), "settings": settings})
        del rows[:-MAX_HISTORY]
        _write(data)
