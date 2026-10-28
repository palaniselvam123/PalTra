"""Fast-move alerts (fast_move.py): a Telegram (and optionally a call) when a stock's price moves fast.

Alerts only: nothing here trades, and nothing in the strategy reads it. Off by default; settings
in `fast_moves.json` next to the SMA DB (`FAST_MOVE_FILE`).

* **Armed and held stocks** (`on`, desk only): every `POLL_SEC` (5 s) while NSE is open, the last
  price of every stock armed or held on bots 1-4 (the prices the bots already fetch, no extra Groww
  call) goes into a short history; a stock whose price moved at least `pct` (0.5) % within
  `minutes` (3) is alerted.
* **All F&O stocks** (`scan_on`): after every finished Trend quality pass in market hours (manual or
  auto re-scan, on any server that scans), stocks whose speed over their last 10 minutes
  (`speed_pct_per_min`, `cross_scan.activity`) is at least `scan_speed` (0.10) %/min either way are
  alerted, in one message.

Each stock is alerted at most once per `cooldown_min` (15) minutes, whichever side found it. One
Telegram per pass (`alert_notifier.send`); with `call` a phone call too (market hours, 5-minute call
cooldown, `place_call`).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import time
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Awaitable, Callable

from groww_client import IST, market_is_open

log = logging.getLogger("sma.fast_move")

POLL_SEC = 5.0
MAX_MINUTES = 30
MAX_PCT = 20.0
MAX_SPEED = 5.0


@dataclass
class Settings:
    on: bool = False
    pct: float = 0.5
    minutes: int = 3
    scan_on: bool = False
    scan_speed: float = 0.10
    cooldown_min: int = 15
    call: bool = False


def settings_path() -> Path:
    explicit = os.environ.get("FAST_MOVE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("fast_moves.json")
    return Path("fast_moves.json")


def clean(raw: dict) -> Settings:
    s = Settings()

    def num(key, cast, low, high):
        value = raw.get(key)
        if value is None:
            return getattr(s, key)
        try:
            return max(low, min(high, cast(value)))
        except (TypeError, ValueError):
            return getattr(s, key)

    s.on = bool(raw.get("on", s.on))
    s.scan_on = bool(raw.get("scan_on", s.scan_on))
    s.call = bool(raw.get("call", s.call))
    s.pct = num("pct", float, 0.05, MAX_PCT)
    s.minutes = num("minutes", int, 1, MAX_MINUTES)
    s.scan_speed = num("scan_speed", float, 0.01, MAX_SPEED)
    s.cooldown_min = num("cooldown_min", int, 1, 240)
    return s


def load() -> Settings:
    try:
        return clean(json.loads(settings_path().read_text()))
    except FileNotFoundError:
        return Settings()
    except Exception:  # noqa: BLE001
        log.exception("could not read the fast-move alert settings")
        return Settings()


def save(raw: dict) -> Settings:
    s = clean({**asdict(load()), **{k: v for k, v in raw.items() if v is not None}})
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s)))
    return s


class Watcher:
    """Price history per stock and the alert bookkeeping (memory only)."""

    def __init__(self) -> None:
        self.hist: dict[str, deque] = {}
        self.alerted: dict[str, float] = {}
        self.recent: list[dict] = []
        self._task: asyncio.Task | None = None

    def _cool(self, symbol: str, now: float, s: Settings) -> bool:
        last = self.alerted.get(symbol)
        return last is None or now - last >= s.cooldown_min * 60

    def _mark(self, hits: list[dict], now: float, source: str) -> None:
        stamp = dt.datetime.fromtimestamp(now, IST).isoformat(timespec="seconds")
        for h in hits:
            self.alerted[h["symbol"]] = now
            self.recent.insert(0, {"at": stamp, "source": source, **h})
        del self.recent[50:]

    def note(self, prices: dict[str, float], s: Settings, now: float | None = None) -> list[dict]:
        """Add one round of prices; the stocks that moved at least `pct` within `minutes` (not cooling down)."""
        now = time.time() if now is None else now
        window = s.minutes * 60
        hits = []
        for symbol, price in prices.items():
            if not price or price <= 0:
                continue
            q = self.hist.setdefault(symbol, deque())
            q.append((now, float(price)))
            while q and now - q[0][0] > window:
                q.popleft()
            if len(q) < 2 or not self._cool(symbol, now, s):
                continue
            t0, p0 = q[0]
            move = (float(price) / p0 - 1) * 100
            if abs(move) >= s.pct:
                hits.append(
                    {"symbol": symbol, "move_pct": round(move, 2), "from": round(p0, 2), "price": round(float(price), 2),
                     "secs": int(now - t0)}
                )
        for gone in [k for k in self.hist if k not in prices]:
            self.hist.pop(gone, None)
        self._mark(hits, now, "armed")
        return hits

    def scan_hits(self, rows: list[dict], s: Settings, now: float | None = None) -> list[dict]:
        """Scan rows whose 10-minute speed is at least `scan_speed` %/min either way (not cooling down)."""
        now = time.time() if now is None else now
        hits = []
        for r in rows or []:
            speed = r.get("speed_pct_per_min")
            symbol = r.get("symbol")
            if symbol is None or speed is None or abs(float(speed)) < s.scan_speed or not self._cool(symbol, now, s):
                continue
            hits.append(
                {"symbol": symbol, "speed": round(float(speed), 3), "move_pct": r.get("move_pct"),
                 "price": r.get("ltp"), "minutes": r.get("window_min")}
            )
        hits.sort(key=lambda h: -abs(h["speed"]))
        self._mark(hits, now, "scan")
        return hits

    async def run(
        self,
        prices: Callable[[], dict[str, float]],
        send: Callable[[str], Awaitable[None]],
        call: Callable[[str], Awaitable[None]],
    ) -> None:
        while True:
            try:
                s = load()
                if s.on and market_is_open(dt.datetime.now(IST)):
                    hits = self.note(prices(), s)
                    if hits:
                        await send(text(hits, s))
                        if s.call:
                            await call(call_text(hits))
                else:
                    self.hist.clear()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never let the loop die
                log.exception("fast-move alert pass")
            await asyncio.sleep(POLL_SEC)

    def start(self, prices, send, call) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self.run(prices, send, call))

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None


def _arrow(move) -> str:
    return "▲" if (move or 0) >= 0 else "▼"


def text(hits: list[dict], s: Settings) -> str:
    lines = [f"⚡ Fast move ({s.pct:g}% within {s.minutes} min): {len(hits)} stock(s)"]
    for h in hits[:15]:
        lines.append(
            f"{_arrow(h['move_pct'])} {h['symbol']} {h['move_pct']:+.2f}% in {max(1, round(h['secs'] / 60))} min "
            f"({h['from']:.2f} → {h['price']:.2f})"
        )
    lines.append("Alert only: nothing is traded.")
    return "\n".join(lines)


def scan_text(hits: list[dict], s: Settings) -> str:
    lines = [f"⚡ Fast movers in the scan (speed ≥ {s.scan_speed:g}%/min): {len(hits)} stock(s)"]
    for h in hits[:15]:
        move = h.get("move_pct")
        extra = f", {move:+.2f}% in {h.get('minutes') or 10} min" if move is not None else ""
        price = f" ₹{h['price']:.2f}" if h.get("price") else ""
        lines.append(f"{_arrow(h['speed'])} {h['symbol']}{price} · {h['speed']:+.3f}%/min{extra}")
    if len(hits) > 15:
        lines.append(f"and {len(hits) - 15} more")
    lines.append("Alert only: nothing is traded.")
    return "\n".join(lines)


def call_text(hits: list[dict]) -> str:
    top = hits[0]
    way = "up" if (top.get("move_pct") or top.get("speed") or 0) >= 0 else "down"
    more = f", and {len(hits) - 1} more" if len(hits) > 1 else ""
    return f"Fast move. {top['symbol']} moving {way} quickly{more}."


watcher = Watcher()
