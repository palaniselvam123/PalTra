"""Compare pick times for the strong-trend pick backtest (practice money; the Scalp page's Backtest tab).

The owner picks the Trend quality card's strong trends at a time of day (10:00, 11:00, 12:00…) and backtests the top N.
Which time works best? A comparison plays the same strong-trend pick backtest (`trend_picks.py`: same days, same stocks,
same bot and settings, same direction / top N / candle) once per chosen time, one after another on that bot's replay
player, and sets the results side by side: P&L before charges (the screens' basis) and after, trades, win rate, green
days, the worst drop from a high, and, day by day, which time did best.

Each time is an ordinary backtest run (Backtests tab, with its picks). The comparison only remembers which runs belong
together (`trend_compare.json` next to the SMA DB, `TREND_COMPARE_FILE`, the last `KEEP` comparisons). Nothing here
places an order, arms a stock or changes a bot's settings.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
from pathlib import Path

log = logging.getLogger("sma.trend_compare")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KEEP = 20
MIN_TIMES = 2
MAX_TIMES = 8
POLL_SEC = 3.0
BUSY = ("LOADING", "PLAYING", "PAUSED")
# Settings keys that differ between the runs of one comparison on purpose (the pick time, the picks).
_OWN_KEYS = ("scalp_pick", "start_time", "picks")


def _path() -> Path:
    explicit = os.environ.get("TREND_COMPARE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("trend_compare.json")
    return Path("trend_compare.json")


def load_all() -> list[dict]:
    try:
        raw = json.loads(_path().read_text())
    except (OSError, ValueError):
        return []
    return [c for c in raw if isinstance(c, dict)] if isinstance(raw, list) else []


def _save_all(items: list[dict]) -> None:
    path = _path()
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(items[-KEEP:]))
    tmp.replace(path)


def get(cid: int) -> dict | None:
    return next((c for c in load_all() if c.get("id") == cid), None)


def put(item: dict) -> None:
    items = [c for c in load_all() if c.get("id") != item["id"]]
    items.append(item)
    items.sort(key=lambda c: c["id"])
    _save_all(items)


def delete(cid: int) -> bool:
    items = load_all()
    kept = [c for c in items if c.get("id") != cid]
    if len(kept) == len(items):
        return False
    _save_all(kept)
    return True


def clean_times(raw: list[str]) -> list[str]:
    """Sorted, unique HH:MM times between 09:20 and 14:30. Raises ValueError."""
    out: set[str] = set()
    for value in raw or []:
        try:
            h, m = str(value).strip().split(":")
            t = dt.time(int(h), int(m))
        except (ValueError, TypeError):
            raise ValueError(f"{value!r} is not a time (HH:MM).") from None
        if not (dt.time(9, 20) <= t <= dt.time(14, 30)):
            raise ValueError("Each pick time must be between 09:20 and 14:30 IST.")
        out.add(t.strftime("%H:%M"))
    times = sorted(out)
    if not (MIN_TIMES <= len(times) <= MAX_TIMES):
        raise ValueError(f"Pick {MIN_TIMES} to {MAX_TIMES} different times to compare.")
    return times


def new(start_date: str, end_date: str, times: list[str], top_n: int, direction: str, minutes: int, bot: int,
        universe: int, lists: list[str] | None = None, now: dt.datetime | None = None) -> dict:
    """A new comparison, saved, with one waiting entry per time."""
    items = load_all()
    cid = max((int(c.get("id") or 0) for c in items), default=0) + 1
    item = {
        "id": cid,
        "created_at": (now or dt.datetime.now(IST)).isoformat(timespec="seconds"),
        "status": "RUNNING",
        "start_date": start_date,
        "end_date": end_date,
        "times": times,
        "top_n": top_n,
        "direction": direction,
        "minutes": minutes,
        "bot": bot,
        "universe": universe,
        "lists": list(lists or []),
        "runs": [{"time": t, "run_id": None, "status": "WAITING", "note": ""} for t in times],
        "note": "",
    }
    put(item)
    return item


def interrupt_running() -> None:
    """At boot: a comparison that was playing when the process stopped is marked INTERRUPTED (its finished runs stay)."""
    items = load_all()
    changed = False
    for c in items:
        if c.get("status") == "RUNNING":
            c["status"] = "INTERRUPTED"
            c["note"] = "The server restarted while it ran. The finished times are kept; start it again for the rest."
            for r in c.get("runs") or []:
                if r.get("status") in ("WAITING", "RUNNING"):
                    r["status"] = "INTERRUPTED"
            changed = True
    if changed:
        _save_all(items)


async def play(
    item: dict, start_fn, info_fn, stop_fn=None, poll: float = POLL_SEC, should_stop=lambda: False, key: str = "time",
    save=None,
) -> dict:
    """Play each entry of `item["runs"]` one after another. Never raises (a refusal is noted on that entry).

    `start_fn(entry[key])` starts that entry's backtest (awaitable, returns the run id; raises on a refusal);
    `info_fn()` gives the bot's replay player's (status, run_id) now; `save(item)` keeps progress (default `put`).
    Shared with stock_rank.py (one backtest per batch of stocks)."""
    put = save or globals()["put"]
    try:
        for entry in item["runs"]:
            if entry["status"] not in ("WAITING",):
                continue
            # Someone else's replay on this bot: wait for it rather than stop it.
            while info_fn()[0] in BUSY:
                if should_stop():
                    raise asyncio.CancelledError
                entry["note"] = "waiting for the bot's replay player to be free"
                await asyncio.sleep(poll)
            if should_stop():
                raise asyncio.CancelledError
            try:
                run_id = await start_fn(entry[key])
            except Exception as exc:  # noqa: BLE001 - a refusal for one time never stops the rest
                detail = getattr(exc, "detail", None) or str(exc)
                entry.update(status="SKIPPED", note=f"not started: {detail}")
                put(item)
                continue
            entry.update(run_id=run_id, status="RUNNING", note="")
            put(item)
            while True:
                await asyncio.sleep(poll)
                status, now_id = info_fn()
                if now_id != run_id:
                    entry.update(status="INTERRUPTED", note="another replay took over the bot's player")
                    break
                if status == "FINISHED":
                    entry["status"] = "DONE"
                    break
                if status not in BUSY:
                    entry.update(status="STOPPED" if status != "ERROR" else "ERROR", note=status.lower())
                    break
                if should_stop():
                    raise asyncio.CancelledError
            put(item)
        item["status"] = "DONE"
    except asyncio.CancelledError:
        item["status"] = "STOPPED"
        for entry in item["runs"]:
            if entry["status"] in ("WAITING", "RUNNING"):
                entry["status"] = "STOPPED"
        if stop_fn is not None:
            try:
                await stop_fn()
            except Exception:  # noqa: BLE001
                log.exception("stopping the comparison's replay failed")
    except Exception as exc:  # noqa: BLE001
        log.exception("pick-time comparison failed")
        item["status"] = "ERROR"
        item["note"] = str(exc)
    put(item)
    return item


# ---- the side-by-side result ---------------------------------------------------------------------


def _drawdown(day_gross: list[float]) -> float:
    peak = running = worst = 0.0
    for g in day_gross:
        running += g
        peak = max(peak, running)
        worst = min(worst, running - peak)
    return round(worst, 2)


def _strategy(settings: dict) -> dict:
    return {k: v for k, v in (settings or {}).items() if k not in _OWN_KEYS}


def summarize(item: dict, runs: dict[int, dict | None]) -> dict:
    """The comparison's result. `runs` maps run id → `replay.get_run(id)` (None when deleted)."""
    rows = []
    by_day: dict[str, dict[str, float]] = {}
    settings_seen: list[dict] = []
    for entry in item.get("runs") or []:
        run = runs.get(entry.get("run_id")) if entry.get("run_id") is not None else None
        row = {"time": entry["time"], "run_id": entry.get("run_id"), "status": entry.get("status"),
               "note": entry.get("note") or "", "has_result": run is not None}
        if run is not None:
            days = run.get("days") or []
            tot = run.get("totals") or {}
            gross_days = [float(d.get("gross") or 0) for d in days]
            trades = int(tot.get("trades") or 0)
            gross = round(sum(gross_days), 2)
            picks = ((run.get("settings") or {}).get("scalp_pick") or {}).get("picks") or {}
            row.update(
                days_played=len(days),
                days_done=run.get("days_done"),
                days_total=run.get("days_total"),
                trades=trades,
                wins=int(tot.get("wins") or 0),
                win_rate=float(tot.get("win_rate") or 0),
                gross=gross,
                net=round(float(tot.get("net") or 0), 2),
                charges=round(float(tot.get("charges") or 0), 2),
                per_trade=round(gross / trades, 2) if trades else 0.0,
                per_day=round(gross / len(days), 2) if days else 0.0,
                green_days=sum(1 for g in gross_days if g > 0),
                red_days=sum(1 for g in gross_days if g < 0),
                best_day=round(max(gross_days), 2) if gross_days else 0.0,
                worst_day=round(min(gross_days), 2) if gross_days else 0.0,
                drawdown=_drawdown(gross_days),
                stocks_picked=len({p.get("symbol") for ps in picks.values() for p in (ps or []) if isinstance(p, dict)}),
            )
            for d in days:
                by_day.setdefault(d["date"], {})[entry["time"]] = round(float(d.get("gross") or 0), 2)
            settings_seen.append(_strategy(run.get("settings") or {}))
        rows.append(row)

    done = [r for r in rows if r["has_result"]]
    # Each day's best time (ties: none); a time that had no trade that day counts as 0.
    day_rows = []
    day_wins: dict[str, int] = {r["time"]: 0 for r in done}
    for date in sorted(by_day):
        values = {r["time"]: by_day[date].get(r["time"], 0.0) for r in done}
        best = max(values.values()) if values else 0.0
        leaders = [t for t, v in values.items() if v == best]
        winner = leaders[0] if len(leaders) == 1 and best != 0 else None
        if winner:
            day_wins[winner] += 1
        day_rows.append({"date": date, "by_time": values, "best": winner})
    for r in done:
        r["best_days"] = day_wins.get(r["time"], 0)

    def lead(key: str, high: bool = True) -> str | None:
        if not done:
            return None
        pick = max(done, key=lambda r: r[key]) if high else min(done, key=lambda r: r[key])
        same = [r for r in done if r[key] == pick[key]]
        return pick["time"] if len(same) == 1 else None

    best = {
        "gross": lead("gross"),
        "net": lead("net"),
        "win_rate": lead("win_rate"),
        "green_days": lead("green_days"),
        "drawdown": lead("drawdown"),  # the least negative = the smallest drop
        "best_days": lead("best_days"),
    }
    ranked = sorted(done, key=lambda r: (-r["gross"], -r["green_days"], r["time"]))
    for place, r in enumerate(ranked, 1):
        r["rank"] = place
    differ = any(s != settings_seen[0] for s in settings_seen[1:]) if settings_seen else False
    return {
        "rows": rows,
        "days": day_rows,
        "best": best,
        "settings_differ": differ,
        "answer": _answer(ranked, best, len(day_rows), differ),
    }


def _money(v: float) -> str:
    sign = "+" if v > 0 else "−" if v < 0 else ""
    return f"{sign}₹{abs(v):,.0f}"


def _answer(ranked: list[dict], best: dict, days: int, differ: bool) -> list[str]:
    """Plain-words lines for the top of the result."""
    if not ranked:
        return ["No time has a result yet."]
    lines = []
    top = ranked[0]
    lines.append(
        f"Best total: picking at {top['time']} made {_money(top['gross'])} before charges ({_money(top['net'])} after) "
        f"over {top['days_played']} day(s), {top['trades']} trades, {top['win_rate']:.0f}% winners."
    )
    if len(ranked) > 1:
        worst = ranked[-1]
        lines.append(f"Weakest: {worst['time']} with {_money(worst['gross'])} before charges.")
    if best.get("green_days"):
        r = next(x for x in ranked if x["time"] == best["green_days"])
        lines.append(f"Most green days: {r['time']} ({r['green_days']} of {r['days_played']}).")
    if best.get("best_days"):
        r = next(x for x in ranked if x["time"] == best["best_days"])
        lines.append(f"Best time on the most days: {r['time']} (top on {r['best_days']} of {days} day(s)).")
    if best.get("drawdown"):
        r = next(x for x in ranked if x["time"] == best["drawdown"])
        lines.append(f"Smallest drop from a high: {r['time']} ({_money(r['drawdown'])}).")
    if days < 10:
        lines.append(f"Only {days} day(s): a few lucky days can decide this. Use 10 or more days before trusting it.")
    if differ:
        lines.append("The bot's settings changed between these runs, so the times were not tested on equal terms.")
    return lines


class Comparer:
    """One comparison at a time per process."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.current: dict | None = None
        self._stop = False

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    def start(self, item: dict, start_fn, info_fn, stop_fn=None, key: str = "time", save=None) -> None:
        self.current, self._stop = item, False
        self.task = asyncio.create_task(
            play(item, start_fn, info_fn, stop_fn=stop_fn, should_stop=lambda: self._stop, key=key, save=save)
        )

    def stop(self) -> None:
        self._stop = True


_COMPARER = Comparer()


def comparer() -> Comparer:
    return _COMPARER
