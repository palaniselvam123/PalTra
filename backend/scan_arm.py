"""Scan & arm: run the Trend quality scan and arm what it finds on one SMA bot, now or at set times.

A **preset** is a saved recipe: which stocks to scan (`lists`: F&O, Nifty 50 / 100 / Bank, armed),
which trend to look for and on which bot to arm the matches. The trend test is the Trend quality card's
"Trend on" filter and rank (`trend_rank.read` / `ranked`): every ticked candle size (`sizes`, e.g.
1 / 5 / 15 minutes) must point the same way (`direction` UP / DOWN / SAME), either by the SMA side
(`kind` SIDE) or as a strong trend (`kind` STRONG: the card's Strong bullish / bearish, 7 checks);
optional limits on the trend's age, the SMA gap and fading; the best `top_n` by rank are armed.

A preset runs from the terminal's Scan & arm button or MCP (`run`), or by itself at its `times` (IST,
e.g. 10:00 and 10:15) on trading days while NSE is open (`Scheduler`), once per time per day; a time
missed by more than `GRACE_MIN` (a restart) is skipped. One run at a time.

**Arming only**: a match goes on the bot's Trade list (source chip "Scan & arm"), through the same
route as the Arm button (the desk's terminal in-process, or the desk link from the scanner / backtest
server). No order is placed here; the bot trades it on its next SMA cross, or at once with that bot's
"Join the morning trend" on. A **LIVE** bot is armed only from the button after the person confirms;
the schedule and MCP refuse a LIVE bot. Stocks already on the Trade list are left as they are, and
nothing is ever unarmed.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import re
import secrets
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Awaitable, Callable

import trend_rank
from candles import CANDLE_MINUTES
from groww_client import IST, market_is_open

log = logging.getLogger("sma.scan_arm")

SCAN_LISTS = ("armed", "fno", "nifty50", "nifty100", "niftybank")
MAX_PRESETS = 20
MAX_TIMES = 12
MAX_TOP = 20
GRACE_MIN = 15
CHECK_SEC = 20
SOURCE = "Scan & arm"
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


@dataclass
class Preset:
    id: str = ""
    name: str = "Scan & arm"
    bot: int = 3
    lists: list = field(default_factory=lambda: ["fno"])
    # The scan's own candle (its crosses and Trend columns); every size's trend is read in the same pass.
    minutes: int = 5
    sizes: list = field(default_factory=lambda: [5])
    direction: str = "SAME"
    kind: str = "STRONG"
    max_age_min: int = 0
    min_gap_pct: float = 0.0
    skip_fading: bool = False
    top_n: int = 5
    # Run by itself at these IST times on trading days ("HH:MM"), while `on`.
    times: list = field(default_factory=list)
    on: bool = False
    # time -> the IST day it last fired, so a time runs once a day (kept through restarts).
    fired: dict = field(default_factory=dict)
    last: dict | None = None


def _clean_times(raw) -> list[str]:
    out: list[str] = []
    for t in raw or []:
        t = str(t).strip()
        if len(t) == 4 and t[1] == ":":
            t = "0" + t
        if _TIME.match(t) and "09:15" <= t <= "15:30" and t not in out:
            out.append(t)
    return sorted(out)[:MAX_TIMES]


def clean(raw: dict, base: Preset | None = None) -> Preset:
    """A valid preset from a request or the file (unknown or bad values fall back to `base` / defaults)."""
    p = Preset(**asdict(base)) if base is not None else Preset()
    if raw.get("id"):
        p.id = re.sub(r"[^a-z0-9]", "", str(raw["id"]).lower())[:16]
    if raw.get("name") is not None:
        p.name = (str(raw["name"]).strip() or p.name)[:40]
    if raw.get("bot") is not None and int(raw["bot"]) in (1, 2, 3, 4):
        p.bot = int(raw["bot"])
    if raw.get("lists") is not None:
        lists = [k for k in dict.fromkeys(str(x).lower() for x in raw["lists"]) if k in SCAN_LISTS]
        p.lists = lists or ["fno"]
    if raw.get("minutes") is not None and int(raw["minutes"]) in CANDLE_MINUTES:
        p.minutes = int(raw["minutes"])
    if raw.get("sizes") is not None:
        sizes = sorted({int(m) for m in raw["sizes"] if int(m) in CANDLE_MINUTES})
        p.sizes = sizes or p.sizes
    if raw.get("direction") in trend_rank.DIRECTIONS:
        p.direction = raw["direction"]
    if raw.get("kind") in trend_rank.KINDS:
        p.kind = raw["kind"]
    if raw.get("max_age_min") is not None:
        p.max_age_min = max(0, min(24 * 60, int(raw["max_age_min"] or 0)))
    if raw.get("min_gap_pct") is not None:
        p.min_gap_pct = max(0.0, min(5.0, float(raw["min_gap_pct"] or 0.0)))
    if raw.get("skip_fading") is not None:
        p.skip_fading = bool(raw["skip_fading"])
    if raw.get("top_n") is not None:
        p.top_n = max(1, min(MAX_TOP, int(raw["top_n"] or 1)))
    if raw.get("times") is not None:
        p.times = _clean_times(raw["times"])
    if raw.get("on") is not None:
        p.on = bool(raw["on"])
    if isinstance(raw.get("fired"), dict):
        p.fired = {str(k): str(v) for k, v in raw["fired"].items() if _TIME.match(str(k))}
    if "last" in raw and (raw["last"] is None or isinstance(raw["last"], dict)):
        p.last = raw["last"]
    if not p.times:
        p.on = False
    return p


def describe(p: Preset) -> str:
    """'Strong bullish on 1m/5m/15m' and the limits, in plain words."""
    sizes = "/".join(f"{m}m" for m in p.sizes)
    way = {"UP": "bullish", "DOWN": "bearish", "SAME": "bullish or bearish"}[p.direction]
    what = f"{'Strong' if p.kind == 'STRONG' else 'SMA side'} {way} on {sizes}"
    extra = []
    if p.max_age_min:
        extra.append(f"trend ≤ {p.max_age_min} min old")
    if p.min_gap_pct:
        extra.append(f"SMA gap ≥ {p.min_gap_pct:g}%")
    if p.skip_fading:
        extra.append("not fading")
    return what + (f" ({', '.join(extra)})" if extra else "") + f", top {p.top_n}"


def picks(rows: list[dict], p: Preset) -> list[dict]:
    """The scan rows that match the preset, best rank first, at most `top_n`."""
    out = [
        r for r in trend_rank.ranked(rows, p.sizes, p.direction, p.kind)
        if (p.max_age_min <= 0 or r["age_min"] <= p.max_age_min)
        and r["gap_pct"] >= p.min_gap_pct
        and not (p.skip_fading and r["fading"])
    ]
    return out[: p.top_n]


def due_time(p: Preset, now: dt.datetime) -> str | None:
    """The preset's time to run now, if any: reached within GRACE_MIN today and not yet run today."""
    if not p.on or not p.times or not market_is_open(now):
        return None
    today = now.date().isoformat()
    for t in p.times:
        hh, mm = (int(x) for x in t.split(":"))
        at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if at <= now < at + dt.timedelta(minutes=GRACE_MIN) and p.fired.get(t) != today:
            return t
    return None


def settings_path() -> Path:
    """SCAN_ARM_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("SCAN_ARM_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("scan_arm.json")
    return Path("scan_arm.json")


def load() -> list[Preset]:
    try:
        raw = json.loads(settings_path().read_text())
    except FileNotFoundError:
        return []
    except Exception:  # noqa: BLE001
        log.exception("could not read the scan & arm presets")
        return []
    return [clean(r) for r in (raw.get("presets") or []) if isinstance(r, dict)][:MAX_PRESETS]


def _write(presets: list[Preset]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"presets": [asdict(p) for p in presets]}))


def save(raw: dict) -> Preset:
    """Add a preset (no / unknown id) or change one. Raises ValueError when there are too many."""
    presets = load()
    pid = re.sub(r"[^a-z0-9]", "", str(raw.get("id") or "").lower())
    for i, old in enumerate(presets):
        if old.id == pid:
            p = clean({k: v for k, v in raw.items() if k not in ("fired", "last")}, base=old)
            if p.times != old.times:
                p.fired = {t: d for t, d in old.fired.items() if t in p.times}
            presets[i] = p
            _write(presets)
            return p
    if len(presets) >= MAX_PRESETS:
        raise ValueError(f"At most {MAX_PRESETS} presets; delete one first")
    p = clean({k: v for k, v in raw.items() if k not in ("id", "fired", "last")})
    p.id = secrets.token_hex(4)
    presets.append(p)
    _write(presets)
    return p


def delete(pid: str) -> bool:
    presets = load()
    keep = [p for p in presets if p.id != pid]
    if len(keep) == len(presets):
        return False
    _write(keep)
    return True


def get(pid: str) -> Preset | None:
    return next((p for p in load() if p.id == pid), None)


def _update(pid: str, **changes) -> None:
    presets = load()
    for p in presets:
        if p.id == pid:
            for k, v in changes.items():
                setattr(p, k, v)
    _write(presets)


# ---- running ----------------------------------------------------------------------------------

# The hooks main.py hands in: the stocks of the lists, one finished Trend quality pass, the bots'
# modes and Trade lists ({bot: {"name", "mode", "armed"}}), and arming one stock (None = done, else why not).
Symbols = Callable[[list[str]], Awaitable[list[str]]]
Scan = Callable[[list[str], int, Callable[[str], None]], Awaitable[dict]]
Bots = Callable[[], Awaitable[dict[int, dict]]]
Arm = Callable[[int, str, str, bool], Awaitable[str | None]]


class LiveRefused(RuntimeError):
    pass


class Runner:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.stage: str | None = None
        self.current: str | None = None
        self.recent: list[dict] = []
        self._tasks: set[asyncio.Task] = set()
        self._loop: asyncio.Task | None = None

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def run(
        self,
        p: Preset,
        *,
        symbols: Symbols,
        scan: Scan,
        bots: Bots,
        arm: Arm,
        trigger: str = "manual",
        at: str | None = None,
        confirm_live: bool = False,
        now: Callable[[], dt.datetime] = lambda: dt.datetime.now(IST),
    ) -> dict:
        """Scan, pick and arm. Returns the run's record (also kept on the preset). Raises LiveRefused
        before scanning when the bot is LIVE without the person's confirmation."""
        async with self._lock:
            self.current, self.stage = p.name, "Checking the bot"
            started = now()
            record: dict = {
                "at": started.isoformat(timespec="seconds"),
                "trigger": trigger,
                "time": at,
                "preset": p.id,
                "name": p.name,
                "bot": p.bot,
                "rule": describe(p),
                "scanned": 0,
                "matched": [],
                "armed": [],
                "already": [],
                "skipped": [],
                "error": None,
            }
            try:
                book = await bots()
                bot = book.get(p.bot)
                if bot is None:
                    raise RuntimeError(f"Bot {p.bot} was not found.")
                record["bot_name"] = bot.get("name") or f"Bot {p.bot}"
                live = (bot.get("mode") or "PAPER").upper() == "LIVE"
                record["mode"] = "LIVE" if live else "PAPER"
                if live and not confirm_live:
                    raise LiveRefused(
                        f"{record['bot_name']} is LIVE. Scheduled and outside (MCP) runs only arm PAPER bots; "
                        "run it from the terminal's Scan & arm button and confirm the LIVE arm."
                    )
                self.stage = "Reading the stock lists"
                names = await symbols(p.lists)
                if not names:
                    raise RuntimeError("The scan lists are empty, so there is nothing to scan.")
                record["scanned"] = len(names)

                def progress(text: str) -> None:
                    self.stage = text

                self.stage = f"Scanning {len(names)} stocks"
                snap = await scan(names, p.minutes, progress)
                rows = snap.get("rows") or []
                if not rows and snap.get("error"):
                    raise RuntimeError(f"The scan failed: {snap['error']}")
                record["as_of"] = snap.get("as_of")
                found = picks(rows, p)
                record["matched"] = [
                    {k: r.get(k) for k in ("symbol", "side", "rank", "age_min", "gap_pct", "ltp", "fading")} for r in found
                ]
                armed_now = {str(s).upper() for s in bot.get("armed") or []}
                source = f"{SOURCE}: {p.name}"[:40]
                self.stage = f"Arming {len(found)} on {record['bot_name']}"
                for r in found:
                    sym = str(r["symbol"]).upper()
                    if sym in armed_now:
                        record["already"].append(sym)
                        continue
                    why = await arm(p.bot, sym, source, live and confirm_live)
                    if why is None:
                        record["armed"].append(sym)
                        armed_now.add(sym)
                    else:
                        record["skipped"].append({"symbol": sym, "why": why[:200]})
            except LiveRefused:
                raise
            except Exception as exc:  # noqa: BLE001
                record["error"] = (str(exc).strip() or type(exc).__name__)[:300]
                log.warning("scan & arm %s failed: %s", p.name, record["error"])
            finally:
                self.current, self.stage = None, None
            record["finished_at"] = now().isoformat(timespec="seconds")
            self.recent.insert(0, record)
            del self.recent[30:]
            if p.id:
                try:
                    await asyncio.to_thread(_update, p.id, last=record)
                except OSError:
                    log.exception("could not save the scan & arm result")
            log.info(
                "scan & arm %s (%s) bot %s: %d matched, armed %s, already %s, skipped %d",
                p.name, trigger, p.bot, len(record["matched"]), record["armed"], record["already"], len(record["skipped"]),
            )
            return record

    def start(self, p: Preset, **kwargs) -> None:
        """Run in the background (a scan takes minutes); the record lands in `recent`."""
        task = asyncio.get_running_loop().create_task(self._safe(p, **kwargs))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _safe(self, p: Preset, notify: Callable[[str], Awaitable[None]] | None = None, **kwargs) -> None:
        try:
            record = await self.run(p, **kwargs)
        except LiveRefused as exc:
            record = {
                "at": dt.datetime.now(IST).isoformat(timespec="seconds"), "trigger": kwargs.get("trigger"),
                "time": kwargs.get("at"), "preset": p.id, "name": p.name, "bot": p.bot, "rule": describe(p),
                "scanned": 0, "matched": [], "armed": [], "already": [], "skipped": [], "error": str(exc),
            }
            self.recent.insert(0, record)
            del self.recent[30:]
            if p.id:
                await asyncio.to_thread(_update, p.id, last=record)
        except Exception:  # noqa: BLE001
            log.exception("scan & arm run")
            return
        if notify is not None:
            try:
                await notify(text(record))
            except Exception:  # noqa: BLE001
                log.exception("scan & arm alert")

    async def tick(self, start_run: Callable[[Preset, str], None], now: dt.datetime | None = None) -> list[str]:
        """Start every preset whose time has come (marked as fired first). Returns their ids."""
        now = now or dt.datetime.now(IST)
        started: list[str] = []
        for p in await asyncio.to_thread(load):
            t = due_time(p, now)
            if t is None:
                continue
            await asyncio.to_thread(_update, p.id, fired={**p.fired, t: now.date().isoformat()})
            start_run(p, t)
            started.append(p.id)
        return started

    def start_loop(self, start_run: Callable[[Preset, str], None], enabled: Callable[[], bool]) -> None:
        if self._loop is not None and not self._loop.done():
            return

        async def loop() -> None:
            while True:
                try:
                    if enabled():
                        await self.tick(start_run)
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 — never let the loop die
                    log.exception("scan & arm schedule")
                await asyncio.sleep(CHECK_SEC)

        self._loop = asyncio.create_task(loop())

    def stop(self) -> None:
        if self._loop is not None:
            self._loop.cancel()
            self._loop = None


def text(record: dict) -> str:
    """The Telegram after a run."""
    when = f" ({record['time']})" if record.get("time") else ""
    bot = record.get("bot_name") or f"Bot {record.get('bot')}"
    lines = [f"Scan & arm '{record.get('name')}'{when} → {bot}", record.get("rule") or ""]
    if record.get("error"):
        lines.append(f"Not done: {record['error']}")
        return "\n".join(lines)
    by_sym = {m["symbol"]: m for m in record.get("matched") or []}

    def one(sym: str) -> str:
        m = by_sym.get(sym) or {}
        arrow = "▲" if m.get("side") == "BULLISH" else "▼" if m.get("side") == "BEARISH" else ""
        bits = [f"{sym} {arrow}".strip()]
        if m.get("age_min") is not None:
            bits.append(f"{m['age_min']}m old")
        if m.get("gap_pct") is not None:
            bits.append(f"gap {m['gap_pct']:.2f}%")
        return " · ".join(bits)

    if record.get("armed"):
        lines.append("Armed: " + "; ".join(one(s) for s in record["armed"]))
    if record.get("already"):
        lines.append("Already armed: " + ", ".join(record["already"]))
    if record.get("skipped"):
        lines.append("Not armed: " + "; ".join(f"{s['symbol']} ({s['why']})" for s in record["skipped"]))
    if not record.get("matched"):
        lines.append(f"No match among {record.get('scanned', 0)} stocks.")
    lines.append("No order was placed: the bot trades them on its next signal.")
    return "\n".join(lines)


def preset_dict(p: Preset) -> dict:
    return {**asdict(p), "rule": describe(p)}


PRESET_FIELDS = tuple(f.name for f in fields(Preset))
