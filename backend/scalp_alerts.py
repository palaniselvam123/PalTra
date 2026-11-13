"""Two Telegram alerts for the Scalp page (information only: never trades, arms or changes a setting).

* **Strong trend** (`strong_on`): after every finished Trend quality pass in market hours, each stock the scan
  reads as a strong up / down trend (`trend_strength`: STRONG_BULLISH / STRONG_BEARISH) with at least
  `strong_min_checks` (6 or 7) of its seven checks passed goes out with its price and the checks that passed
  and missed. `strong_side` keeps BOTH / UP / DOWN.
* **Buyers or sellers in control** (`pressure_on`): after every finished Most active scan in market hours, each
  stock whose waiting buy orders are at least `pressure_pct` (50-95, default 70) % of the order book (buyers) or
  whose sell orders are (sellers) goes out with its price, the share, the day's move and volume vs usual.
  `pressure_side` keeps BOTH / BUY / SELL; `pressure_min_value_cr` drops thinly traded stocks.

Each stock and side is sent at most once per IST day per alert (`_SENT`, memory only: a restart may send again),
at most `MAX_NAMES` per message, through the quiet-runner Telegram channel (the trade channel when that one is
not set). Settings in `scalp_alerts.json` next to the SMA DB (`SCALP_ALERTS_FILE`), both off by default.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from groww_client import IST, market_is_open

log = logging.getLogger("sma.scalp_alerts")

SIDES = ("BOTH", "UP", "DOWN")
PRESSURE_SIDES = ("BOTH", "BUY", "SELL")
MAX_NAMES = 15
# Short names for the seven strong-trend checks (trend_strength.CHECKS / MORNING_CHECKS).
CHECK_WORDS = {
    "stack": "price and averages in order",
    "vwap": "right side of the day's average price",
    "slope": "averages sloping its way",
    "fan": "averages spreading apart",
    "hold": "closes holding its side",
    "structure": "higher highs / lower lows",
    "run": "no recent cross",
    "volume": "volume above usual",
}
# {alert: {IST day: {(symbol, side): "HH:MM"}}}
_SENT: dict[str, dict[dt.date, dict[tuple[str, str], str]]] = {"strong": {}, "pressure": {}}
# The last alerts sent (newest first), for the card. Kept in `scalp_alerts_log.json` next to the settings with `last`,
# so a deploy or restart after the close does not hide what happened during the day.
recent: list[dict] = []
# {alert: what the last scan in market hours found and whether Telegram took it} (`_note`).
last: dict[str, dict] = {}
_log_loaded = False


@dataclass
class Settings:
    strong_on: bool = False
    strong_min_checks: int = 6
    strong_side: str = "BOTH"
    pressure_on: bool = False
    pressure_pct: float = 70.0
    pressure_side: str = "BOTH"
    pressure_min_value_cr: float = 0.0
    # Auto-arm (auto_arm.py): arm the stocks these thresholds pick on a PAPER bot. Off by default.
    arm_strong_on: bool = False
    arm_strong_bot: int = 1
    arm_pressure_on: bool = False
    arm_pressure_bot: int = 1
    arm_max_day: int = 10
    # Auto-arm only stocks priced inside this range (₹, 0 = no bound), live and in the auto-arm replay.
    arm_price_min: float = 0.0
    arm_price_max: float = 0.0


def settings_path() -> Path:
    """SCALP_ALERTS_FILE, else next to the SMA terminal's SQLite file (the Fly volume), else here."""
    explicit = os.environ.get("SCALP_ALERTS_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///"):]).with_name("scalp_alerts.json")
    return Path("scalp_alerts.json")


def clean(raw: dict) -> Settings:
    s = Settings()
    s.strong_on = bool(raw.get("strong_on", s.strong_on))
    s.strong_min_checks = max(5, min(7, int(raw.get("strong_min_checks", s.strong_min_checks) or s.strong_min_checks)))
    s.strong_side = raw.get("strong_side") if raw.get("strong_side") in SIDES else s.strong_side
    s.pressure_on = bool(raw.get("pressure_on", s.pressure_on))
    s.pressure_pct = max(50.0, min(95.0, float(raw.get("pressure_pct", s.pressure_pct) or s.pressure_pct)))
    s.pressure_side = raw.get("pressure_side") if raw.get("pressure_side") in PRESSURE_SIDES else s.pressure_side
    s.pressure_min_value_cr = max(0.0, min(100000.0, float(raw.get("pressure_min_value_cr", 0) or 0)))
    s.arm_strong_on = bool(raw.get("arm_strong_on", s.arm_strong_on))
    s.arm_pressure_on = bool(raw.get("arm_pressure_on", s.arm_pressure_on))
    s.arm_strong_bot = _bot(raw.get("arm_strong_bot"), s.arm_strong_bot)
    s.arm_pressure_bot = _bot(raw.get("arm_pressure_bot"), s.arm_pressure_bot)
    try:
        s.arm_max_day = max(1, min(50, int(raw.get("arm_max_day", s.arm_max_day) or s.arm_max_day)))
        lo = max(0.0, min(1_000_000.0, float(raw.get("arm_price_min", s.arm_price_min) or 0.0)))
        hi = max(0.0, min(1_000_000.0, float(raw.get("arm_price_max", s.arm_price_max) or 0.0)))
        if hi and lo > hi:
            lo, hi = hi, lo
        s.arm_price_min, s.arm_price_max = lo, hi
    except (TypeError, ValueError):
        pass
    return s


def _bot(raw, default: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return n if 1 <= n <= 5 else default


def load() -> Settings:
    try:
        return clean(json.loads(settings_path().read_text()))
    except FileNotFoundError:
        return Settings()
    except Exception:  # noqa: BLE001
        log.exception("could not read the scalp alert settings")
        return Settings()


def save(raw: dict) -> Settings:
    s = clean({**asdict(load()), **{k: v for k, v in raw.items() if v is not None}})
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(s)))
    return s


def log_path() -> Path:
    return settings_path().with_name("scalp_alerts_log.json")


def _load_log() -> None:
    """Read the saved `recent` / `last` once (a restart keeps the day's story). Never raises."""
    global _log_loaded
    if _log_loaded:
        return
    _log_loaded = True
    try:
        data = json.loads(log_path().read_text())
        recent[:] = list(data.get("recent") or [])[:50] + [r for r in recent if r not in (data.get("recent") or [])]
        for kind, row in (data.get("last") or {}).items():
            last.setdefault(kind, row)
    except FileNotFoundError:
        return
    except Exception:  # noqa: BLE001
        log.exception("could not read the scalp alert log")


def _save_log() -> None:
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"recent": recent[:50], "last": last}, default=str))
    except Exception:  # noqa: BLE001
        log.exception("could not save the scalp alert log")


def _delivery(result) -> tuple[bool, str | None]:
    """(sent?, why not) from alert_notifier's DeliveryResult; anything without `ok` counts as sent (tests)."""
    ok = getattr(result, "ok", True)
    if ok:
        return True, None
    why = getattr(result, "skipped_reason", None) or getattr(result, "error", None) or "Telegram refused the message"
    return False, str(why)[:200]


def _note(kind: str, now: dt.datetime, **row) -> None:
    last[kind] = {"at": now.isoformat(timespec="seconds"), **row}
    _save_log()


def _sent(kind: str, now: dt.datetime) -> dict[tuple[str, str], str]:
    book = _SENT[kind]
    for day in [d for d in book if d != now.date()]:
        book.pop(day, None)
    return book.setdefault(now.date(), {})


def _mark(kind: str, picks: list[dict], now: dt.datetime, failed: str | None = None) -> None:
    """Note the picks in `recent`; only a message Telegram took counts as sent for the day (`_SENT`), so a failed
    one is tried again on the next scan."""
    book = _sent(kind, now)
    for p in picks:
        if failed is None:
            book[(p["symbol"], p["side"])] = now.strftime("%H:%M")
        recent.insert(0, {"at": now.isoformat(timespec="seconds"), "kind": kind, **p, **({"failed": failed} if failed else {})})
    del recent[50:]


def _num(value) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def strong_picks(rows: list[dict], s: Settings, now: dt.datetime, alerting: bool = True) -> list[dict]:
    """The strong-trend stocks to alert now: enough checks passed, the side wanted, not sent before today.
    `alerting=False` (auto_arm.py): every stock passing the thresholds, whether the alert is on or sent already."""
    if alerting and not s.strong_on:
        return []
    sent = _sent("strong", now) if alerting else {}
    out = []
    for r in rows:
        trend = r.get("trend")
        if trend not in ("STRONG_BULLISH", "STRONG_BEARISH"):
            continue
        side = "UP" if trend == "STRONG_BULLISH" else "DOWN"
        if s.strong_side != "BOTH" and s.strong_side != side:
            continue
        checks = r.get("trend_checks") or {}
        passed = [k for k, ok in checks.items() if ok]
        missed = [k for k, ok in checks.items() if not ok]
        if len(passed) < s.strong_min_checks or (r.get("symbol"), side) in sent:
            continue
        out.append({
            "symbol": r.get("symbol"), "side": side, "price": _num(r.get("ltp")), "passed": len(passed),
            "of": len(checks), "missed": missed, "minutes": r.get("minutes"), "move_pct": _num(r.get("day_move_pct")),
        })
    out.sort(key=lambda p: (-p["passed"], -abs(p["move_pct"] or 0.0)))
    return out[:MAX_NAMES] if alerting else out


def strong_text(picks: list[dict]) -> str:
    lines = [f"📈 Strong trend ({len(picks)} stock{'' if len(picks) == 1 else 's'}) — Scalp, Trend quality"]
    for p in picks:
        arrow = "▲ UP" if p["side"] == "UP" else "▼ DOWN"
        price = f"₹{p['price']:.2f}" if p["price"] is not None else "price n/a"
        move = f" · day {p['move_pct']:+.2f}%" if p["move_pct"] is not None else ""
        missed = ", ".join(CHECK_WORDS.get(k, k) for k in p["missed"]) or "none"
        lines.append(f"{p['symbol']} {arrow} · {price}{move} · {p['passed']} of {p['of']} checks · missed: {missed}")
    lines.append("Information only: no order was placed.")
    return "\n".join(lines)


def pressure_picks(rows: list[dict], s: Settings, now: dt.datetime, alerting: bool = True) -> list[dict]:
    """The stocks whose buyers (or sellers) hold at least `pressure_pct` % of the waiting orders, not sent today.
    `alerting=False` (auto_arm.py): every stock passing the thresholds, whether the alert is on or sent already."""
    if alerting and not s.pressure_on:
        return []
    sent = _sent("pressure", now) if alerting else {}
    need = s.pressure_pct / 100.0
    out = []
    for r in rows:
        share = _num(r.get("buy_share"))
        if share is None:
            continue
        if share >= need:
            side, pct = "BUY", share * 100.0
        elif 1.0 - share >= need:
            side, pct = "SELL", (1.0 - share) * 100.0
        else:
            continue
        if s.pressure_side != "BOTH" and s.pressure_side != side:
            continue
        if (_num(r.get("value_cr")) or 0.0) < s.pressure_min_value_cr or (r.get("symbol"), side) in sent:
            continue
        out.append({
            "symbol": r.get("symbol"), "side": side, "pct": round(pct, 1), "price": _num(r.get("ltp")),
            "change_pct": _num(r.get("change_pct")), "rvol": _num(r.get("rvol")),
        })
    out.sort(key=lambda p: -p["pct"])
    return out[:MAX_NAMES] if alerting else out


def pressure_text(picks: list[dict], s: Settings) -> str:
    lines = [f"⚖️ Buyers / sellers in control, {s.pressure_pct:g}% or more ({len(picks)}) — Scalp, Most active"]
    for p in picks:
        who = f"🟢 Buyers {p['pct']:.0f}%" if p["side"] == "BUY" else f"🔴 Sellers {p['pct']:.0f}%"
        price = f"₹{p['price']:.2f}" if p["price"] is not None else "price n/a"
        move = f" · day {p['change_pct']:+.2f}%" if p["change_pct"] is not None else ""
        rvol = f" · volume {p['rvol']:.1f}× usual" if p["rvol"] is not None else ""
        lines.append(f"{p['symbol']} · {who} · {price}{move}{rvol}")
    lines.append("Share of the orders waiting in the order book right now. Information only: no order was placed.")
    return "\n".join(lines)


async def after_trend_scan(snapshot: dict, send, now: dt.datetime | None = None) -> list[dict]:
    """Strong-trend alert for a finished Trend quality pass. Returns the stocks alerted. Never raises."""
    try:
        now = now or dt.datetime.now(IST)
        if not market_is_open(now):
            return []
        _load_log()
        s, rows = load(), snapshot.get("rows") or []
        matched = len(strong_picks(rows, s, now, alerting=False))
        picks = strong_picks(rows, s, now)
        sent, why = True, None
        if picks:
            sent, why = _delivery(await send(strong_text(picks)))
            _mark("strong", picks, now, failed=why)
        _note("strong", now, on=s.strong_on, rows=len(rows), matched=matched, new=len(picks), sent=bool(picks) and sent, error=why)
        return picks if sent else []
    except Exception:  # noqa: BLE001
        log.exception("strong-trend alert failed")
        return []


async def after_active_scan(snapshot: dict, send, now: dt.datetime | None = None) -> list[dict]:
    """Buyer / seller alert for a finished Most active scan. Returns the stocks alerted. Never raises."""
    try:
        now = now or dt.datetime.now(IST)
        if not market_is_open(now):
            return []
        _load_log()
        s, rows = load(), snapshot.get("rows") or []
        matched = len(pressure_picks(rows, s, now, alerting=False))
        picks = pressure_picks(rows, s, now)
        sent, why = True, None
        if picks:
            sent, why = _delivery(await send(pressure_text(picks, s)))
            _mark("pressure", picks, now, failed=why)
        _note("pressure", now, on=s.pressure_on, rows=len(rows), matched=matched, new=len(picks), sent=bool(picks) and sent, error=why)
        return picks if sent else []
    except Exception:  # noqa: BLE001
        log.exception("buyer / seller alert failed")
        return []


def view() -> dict:
    import auto_arm

    _load_log()
    return {**asdict(load()), "recent": recent[:20], "last": last, "max_names": MAX_NAMES, "auto_arm": auto_arm.view()}
