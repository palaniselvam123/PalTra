"""Trend rank and trend alerts for the Trend quality scan (read-only; information only, never trades).

The card's "Trend on" filter keeps stocks whose SMAs point the same way on every ticked candle size. A trend
that began hours ago may be nearly over by the time it is seen, so each such stock also gets:

* **age**: how long all ticked sizes have pointed that way, in market minutes: the shortest of
  `run_candles x size` over the sizes (`chop_scan._run_read`; a run can only be as long as the download);
* **gap**: the average |SMA fast - slow| / slow % over the sizes, now;
* **fading**: on any size the gap is under `FADE_SHARE` of the widest it has been in this run;
* **rank**: the stocks are placed once by age (youngest first) and once by gap (widest first); the rank is the
  average of the two places (ties: the wider gap first). Fresh and wide = rank 1.

`frontend/src/lib/trendRank.ts` does the same arithmetic for the table. **Alerts** (`AlertSettings`, saved in
`trend_alerts.json` next to the SMA DB, off by default): after every finished Trend quality pass in market
hours (`after_scan`, manual or auto re-scan), the top `top_n` ranked stocks no older than `max_age_min`, with a
gap of at least `min_gap_pct` (and not fading, with `skip_fading`) are sent once per stock and side per IST day:
one Telegram per pass through the quiet-runner channel (`telegram`), and / or a phone call (`call`, market hours
and the 5-minute call cooldown apply).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from candles import CANDLE_MINUTES
from groww_client import IST, market_is_open

log = logging.getLogger("sma.trend_rank")

FADE_SHARE = 0.5
DIRECTIONS = ("UP", "DOWN", "SAME")
KINDS = ("SIDE", "STRONG")
MAX_TOP = 20
# Stocks already alerted today: {IST day: {(symbol, side): "HH:MM"}}.
_SENT: dict[dt.date, dict[tuple[str, str], str]] = {}
# The last alerts sent (newest first), for the card.
recent: list[dict] = []


def _at(book, m: int):
    """A per-size value from a row dict (int keys in memory, string keys after JSON)."""
    if not isinstance(book, dict):
        return None
    value = book.get(m)
    return book.get(str(m)) if value is None else value


def _side_on(row: dict, m: int, kind: str) -> str | None:
    if kind == "STRONG":
        t = _at(row.get("trend_by_minutes"), m)
        return "BULLISH" if t == "STRONG_BULLISH" else "BEARISH" if t == "STRONG_BEARISH" else None
    return _at(row.get("side_by_minutes"), m)


def read(row: dict, sizes: list[int], direction: str = "SAME", kind: str = "SIDE") -> dict | None:
    """The stock's aligned trend on these sizes ({side, age_min, since_ts, gap_pct, fading}), or None."""
    if not sizes:
        return None
    sides = [_side_on(row, m, kind) for m in sizes]
    if any(s is None for s in sides) or len(set(sides)) != 1:
        return None
    side = sides[0]
    if (direction == "UP" and side != "BULLISH") or (direction == "DOWN" and side != "BEARISH"):
        return None
    ages, starts, gaps, fading = [], [], [], False
    for m in sizes:
        candles = _at(row.get("run_candles_by_minutes"), m)
        since = _at(row.get("run_since_ts_by_minutes"), m)
        gap = _at(row.get("gap_pct_by_minutes"), m)
        peak = _at(row.get("gap_peak_pct_by_minutes"), m)
        if candles is None or since is None or gap is None:
            return None
        ages.append(int(candles) * int(m))
        starts.append(int(since))
        gaps.append(abs(float(gap)))
        if peak and abs(float(gap)) < FADE_SHARE * float(peak):
            fading = True
    return {
        "side": side,
        "age_min": min(ages),
        "since_ts": max(starts),
        "gap_pct": round(sum(gaps) / len(gaps), 4),
        "fading": fading,
    }


def ranked(rows: list[dict], sizes: list[int], direction: str = "SAME", kind: str = "SIDE") -> list[dict]:
    """Every row with an aligned trend, best first, each with its read and `rank` (1 = fresh and wide)."""
    picks = []
    for row in rows or []:
        got = read(row, sizes, direction, kind)
        if got is not None:
            picks.append({"symbol": row.get("symbol"), "ltp": row.get("ltp"), **got})
    by_age = sorted(range(len(picks)), key=lambda i: picks[i]["age_min"])
    by_gap = sorted(range(len(picks)), key=lambda i: -picks[i]["gap_pct"])
    place = {i: 0.0 for i in range(len(picks))}
    for n, i in enumerate(by_age):
        place[i] += n + 1
    for n, i in enumerate(by_gap):
        place[i] += n + 1
    order = sorted(range(len(picks)), key=lambda i: (place[i], -picks[i]["gap_pct"], picks[i]["age_min"]))
    out = []
    for n, i in enumerate(order):
        out.append({**picks[i], "rank": n + 1, "score": place[i] / 2})
    return out


# ---- alerts --------------------------------------------------------------------------------


@dataclass
class AlertSettings:
    on: bool = False
    sizes: list = field(default_factory=list)
    direction: str = "SAME"
    kind: str = "SIDE"
    top_n: int = 5
    max_age_min: int = 60
    min_gap_pct: float = 0.10
    skip_fading: bool = True
    telegram: bool = True
    call: bool = False


def settings_path() -> Path:
    """TREND_ALERT_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("TREND_ALERT_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("trend_alerts.json")
    return Path("trend_alerts.json")


def clean(raw: dict) -> AlertSettings:
    s = AlertSettings()
    s.on = bool(raw.get("on", s.on))
    s.sizes = sorted({int(m) for m in raw.get("sizes", []) or [] if int(m) in CANDLE_MINUTES})
    s.direction = raw.get("direction") if raw.get("direction") in DIRECTIONS else s.direction
    s.kind = raw.get("kind") if raw.get("kind") in KINDS else s.kind
    s.top_n = max(1, min(MAX_TOP, int(raw.get("top_n", s.top_n) or s.top_n)))
    s.max_age_min = max(0, min(24 * 60, int(raw.get("max_age_min", s.max_age_min) or 0)))
    s.min_gap_pct = max(0.0, min(5.0, float(raw.get("min_gap_pct", s.min_gap_pct) or 0.0)))
    s.skip_fading = bool(raw.get("skip_fading", s.skip_fading))
    s.telegram = bool(raw.get("telegram", s.telegram))
    s.call = bool(raw.get("call", s.call))
    return s


def load() -> AlertSettings:
    try:
        return clean(json.loads(settings_path().read_text()))
    except FileNotFoundError:
        return AlertSettings()
    except Exception:  # noqa: BLE001
        log.exception("could not read the trend alert settings")
        return AlertSettings()


def save(raw: dict) -> AlertSettings:
    s = clean({**asdict(load()), **{k: v for k, v in raw.items() if v is not None}})
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s)))
    return s


def due(rows: list[dict], s: AlertSettings, now: dt.datetime) -> list[dict]:
    """The ranked stocks to alert now: top N of those inside the age / gap limits, not sent before today."""
    if not s.on or not s.sizes:
        return []
    sent = _SENT.get(now.date(), {})
    eligible = [
        r for r in ranked(rows, s.sizes, s.direction, s.kind)
        if (s.max_age_min <= 0 or r["age_min"] <= s.max_age_min)
        and r["gap_pct"] >= s.min_gap_pct
        and not (s.skip_fading and r["fading"])
    ]
    return [r for r in eligible[: s.top_n] if (r["symbol"], r["side"]) not in sent]


def _age(minutes: int) -> str:
    return f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"


def text(picks: list[dict], s: AlertSettings) -> str:
    sizes = "/".join(f"{m}m" for m in s.sizes)
    lines = [f"Trend rank ({sizes}, {'strong trend' if s.kind == 'STRONG' else 'SMA side'}): {len(picks)} new"]
    for r in picks:
        arrow = "▲" if r["side"] == "BULLISH" else "▼"
        price = f" ₹{r['ltp']:.2f}" if r.get("ltp") else ""
        lines.append(f"#{r['rank']} {r['symbol']} {arrow}{price} · {_age(r['age_min'])} old · gap {r['gap_pct']:.2f}%")
    lines.append("Information only: nothing is traded.")
    return "\n".join(lines)


def call_text(picks: list[dict]) -> str:
    top = picks[0]
    way = "up" if top["side"] == "BULLISH" else "down"
    more = f", and {len(picks) - 1} more" if len(picks) > 1 else ""
    return f"Trend alert. {top['symbol']} trending {way} for {top['age_min']} minutes{more}."


def mark(picks: list[dict], now: dt.datetime) -> None:
    for day in [d for d in _SENT if d != now.date()]:
        _SENT.pop(day, None)
    book = _SENT.setdefault(now.date(), {})
    for r in picks:
        book[(r["symbol"], r["side"])] = now.strftime("%H:%M")
        recent.insert(0, {"at": now.isoformat(timespec="seconds"), **{k: r[k] for k in ("symbol", "side", "rank", "age_min", "gap_pct")}})
    del recent[50:]


async def after_scan(snapshot: dict, send_telegram, place_call, now: dt.datetime | None = None) -> list[dict]:
    """Send the trend alert for a finished Trend quality pass. Returns the stocks alerted. Never raises."""
    try:
        now = now or dt.datetime.now(IST)
        if not market_is_open(now):
            return []
        s = load()
        picks = due(snapshot.get("rows") or [], s, now)
        if not picks or not (s.telegram or s.call):
            return []
        mark(picks, now)
        if s.telegram:
            await send_telegram(text(picks, s))
        if s.call:
            await place_call(call_text(picks))
        return picks
    except Exception:  # noqa: BLE001
        log.exception("trend alert failed")
        return []
