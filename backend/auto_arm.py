"""Auto-arm from the Scalp page's scans (owner's request): put the stocks the alerts pick on a bot's Trade list by
themselves, and test the same rule on past days.

**Live** (`scalp_alerts.json`, next to the alert settings; both off by default):

* **Trend quality** (`arm_strong_on`, `arm_strong_bot`): after every finished Trend quality pass in market hours, each
  stock read as a strong up / down trend with at least the alert's `strong_min_checks` (6 or 7) of its seven checks
  passed, on the alert's `strong_side`, is armed on that bot (source chip "Auto-arm: Trend quality").
* **Most active** (`arm_pressure_on`, `arm_pressure_bot`): after every finished Most active scan in market hours, each
  stock whose buyers (or sellers) hold at least the alert's `pressure_pct` % of the waiting orders, on the alert's
  `pressure_side`, with at least `pressure_min_value_cr` traded, is armed (source "Auto-arm: Most active").

The alert's own on / off switch does not matter: only its thresholds are read. Each stock is armed at most once a day
per source (`_DONE`, memory only), at most `arm_max_day` (10) stocks a day per source; a stock already on the bot's list
is left as it is, nothing is ever unarmed. **PAPER bots only**: a LIVE bot is refused and the run says so (arming a
LIVE bot means real Groww orders on its next signal, so that stays a person's click). Arming only: no order is placed;
the bot trades the stock on its own next signal, with its own settings. One Telegram per run that armed something
(quiet-runner channel).

**Backtest** (`AutoArmRule`, kind `AUTOARM`, `picks_for_day`; the Scalp page's Backtest tab, "Auto-arm replay"): a past day
played like a live one. Each stock is armed at the minute the rule would have armed it live and traded only from then
on (`replay.ReplaySession` gives the replay engine these arm windows, the "as armed" path, so a stock armed mid-day
waits for a fresh signal as it would live):

* Trend quality: every `every_min` minutes from `first` to `last`, each stock's strong-trend read on the candles closed
  before that minute (`strong_read`: the same `trend_strength.read` the Trend quality card's Trend column shows).
* Most active: the day's saved Most active scans (the order book is not kept anywhere else, so only days the scan ran
  can play this source), taken by `active_hits`; on the backtest server they are read from the desk over the link.

Nothing here places, closes or changes an order.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field

import pandas as pd

log = logging.getLogger("sma.auto_arm")

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
KIND = "AUTOARM"
SOURCES = ("TREND", "ACTIVE", "BOTH")
SOURCE_CHIP = {"strong": "Auto-arm: Trend quality", "pressure": "Auto-arm: Most active"}
MAX_DAY = 50
STEPS = (1, 2, 3, 5, 10, 15, 30)
MAX_UNIVERSE = 250

# {source: {IST day: [symbols armed, in order]}}
_DONE: dict[str, dict[dt.date, list[str]]] = {"strong": {}, "pressure": {}}
# {source: {IST day: [symbols found already armed]}}: remembered once a day like _DONE, but not counted against
# arm_max_day (they used to use up the day's limit without a single stock being armed).
_SEEN: dict[str, dict[dt.date, list[str]]] = {"strong": {}, "pressure": {}}
# The last runs (newest first), for the Alerts card.
recent: list[dict] = []


def _done(kind: str, now: dt.datetime, store: dict | None = None) -> list[str]:
    book = (store if store is not None else _DONE)[kind]
    for day in [d for d in book if d != now.date()]:
        book.pop(day, None)
    return book.setdefault(now.date(), [])


def price_ok(price, lo: float, hi: float) -> bool:
    """Inside the auto-arm price range (₹; 0 = no bound). An unknown price passes only when no range is set."""
    lo, hi = float(lo or 0.0), float(hi or 0.0)
    if not lo and not hi:
        return True
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    return (not lo or p >= lo) and (not hi or p <= hi)


def candidates(kind: str, rows: list[dict], s, now: dt.datetime) -> list[dict]:
    """The stocks the alert's thresholds pick in this scan (whether the alert itself is on or not), inside the
    auto-arm price range (`arm_price_min` / `arm_price_max`)."""
    import scalp_alerts

    if kind == "strong":
        found = scalp_alerts.strong_picks(rows, s, now, alerting=False)
    else:
        found = scalp_alerts.pressure_picks(rows, s, now, alerting=False)
    lo, hi = getattr(s, "arm_price_min", 0.0), getattr(s, "arm_price_max", 0.0)
    return [p for p in found if price_ok(p.get("price"), lo, hi)]


def wanted(kind: str, s) -> tuple[bool, int]:
    if kind == "strong":
        return bool(s.arm_strong_on), int(s.arm_strong_bot)
    return bool(s.arm_pressure_on), int(s.arm_pressure_bot)


async def after_scan(kind: str, snapshot: dict, *, bots, arm, notify=None, now: dt.datetime | None = None,
                     check_market: bool = True) -> dict | None:
    """Arm the stocks a finished scan picks. `bots()` → {bot: {"mode", "armed", "name"}}, `arm(bot, symbol, source)`
    → None when armed else why not. Returns the run's record, or None when nothing was to be done. Never raises."""
    import scalp_alerts
    from groww_client import market_is_open

    try:
        now = now or dt.datetime.now(IST)
        s = scalp_alerts.load()
        on, bot = wanted(kind, s)
        if not on or (check_market and not market_is_open(now)):
            return None
        found = candidates(kind, snapshot.get("rows") or [], s, now)
        done = _done(kind, now)
        seen = _done(kind, now, _SEEN)
        fresh = [p for p in found if p["symbol"] not in done and p["symbol"] not in seen]
        if not fresh:
            return None
        record: dict = {
            "at": now.isoformat(timespec="seconds"), "source": kind, "bot": bot, "matched": len(found),
            "armed": [], "already": [], "skipped": [], "error": None,
        }
        try:
            book = await bots()
            info = book.get(bot)
            if info is None:
                raise RuntimeError(f"Bot {bot} was not found.")
            record["bot_name"] = info.get("name") or f"Bot {bot}"
            if (info.get("mode") or "PAPER").upper() == "LIVE":
                raise RuntimeError(
                    f"{record['bot_name']} is LIVE. Auto-arm only arms PAPER bots: arming a LIVE bot means real "
                    "Groww orders, so that stays your own click."
                )
            armed = {str(x).upper() for x in info.get("armed") or []}
            room = max(0, int(s.arm_max_day) - len(done))
            for p in fresh:
                sym = str(p["symbol"]).upper()
                if sym in armed:
                    record["already"].append(sym)
                    seen.append(sym)
                    continue
                if room <= 0:
                    record["skipped"].append({"symbol": sym, "why": f"today's limit of {s.arm_max_day} reached"})
                    continue
                why = await arm(bot, sym, SOURCE_CHIP[kind])
                if why is None:
                    record["armed"].append(sym)
                    armed.add(sym)
                    done.append(sym)
                    room -= 1
                else:
                    record["skipped"].append({"symbol": sym, "why": str(why)[:200]})
        except Exception as exc:  # noqa: BLE001
            record["error"] = (str(exc).strip() or type(exc).__name__)[:300]
        if not (record["armed"] or record["error"] or record["skipped"]):
            return record
        recent.insert(0, record)
        del recent[30:]
        log.info("auto-arm %s bot %s: armed %s, skipped %s, error %s", kind, bot, record["armed"],
                 len(record["skipped"]), record["error"])
        if notify is not None and (record["armed"] or record["error"]):
            try:
                await notify(text(record))
            except Exception:  # noqa: BLE001
                log.exception("auto-arm message failed")
        return record
    except Exception:  # noqa: BLE001
        log.exception("auto-arm failed")
        return None


def text(record: dict) -> str:
    where = "Trend quality" if record["source"] == "strong" else "Most active"
    name = record.get("bot_name") or f"Bot {record['bot']}"
    if record.get("error"):
        return f"🤖 Auto-arm ({where}) could not arm on {name}: {record['error']}"
    lines = [f"🤖 Auto-arm ({where}): armed {', '.join(record['armed'])} on {name}."]
    if record["skipped"]:
        lines.append("Not armed: " + "; ".join(f"{x['symbol']} ({x['why']})" for x in record["skipped"][:8]))
    lines.append("Arming only: the bot trades them on its own next signal.")
    return "\n".join(lines)


def view() -> dict:
    today = dt.datetime.now(IST)
    return {
        "recent": recent[:20],
        "today": {k: list(_done(k, today)) for k in ("strong", "pressure")},
    }


# ---- backtest: arm as the rule would have, minute by minute -------------------------------------


@dataclass
class AutoArmRule:
    source: str = "BOTH"
    first: dt.time = dt.time(9, 30)
    last: dt.time = dt.time(14, 30)
    every_min: int = 5
    minutes: int = 5
    sma_fast: int = 9
    sma_slow: int = 21
    min_checks: int = 6
    side: str = "BOTH"
    pct: float = 70.0
    pressure_side: str = "BOTH"
    min_value_cr: float = 0.0
    max_day: int = 10
    #: Only stocks priced inside this range when armed (₹, 0 = no bound): the tape's last close before the arm minute.
    price_min: float = 0.0
    price_max: float = 0.0
    #: {IST day ISO: [(HH:MM, symbol, BUY/SELL, pct)]} from the saved Most active scans, filled before the run.
    active_hits: dict = field(default_factory=dict)
    kind: str = KIND
    #: Called with "HH:MM" as the Trend quality reads reach each minute (the replay bar's progress). Not saved.
    on_step: object = field(default=None, repr=False, compare=False)

    @property
    def pick_time(self) -> dt.time:
        return self.first

    def as_dict(self) -> dict:
        return {
            "kind": KIND, "source": self.source, "pick_time": self.first.strftime("%H:%M"),
            "first": self.first.strftime("%H:%M"), "last": self.last.strftime("%H:%M"), "every_min": self.every_min,
            "minutes": self.minutes, "min_checks": self.min_checks, "side": self.side, "pct": self.pct,
            "pressure_side": self.pressure_side, "min_value_cr": self.min_value_cr, "max_day": self.max_day,
            "price_min": self.price_min, "price_max": self.price_max,
            "active_days": sorted(self.active_hits),
        }


def strong_read(frame: pd.DataFrame, day: dt.date, at: dt.time, minutes: int, fast: int, slow: int) -> dict | None:
    """One stock's strong-trend read as the Trend quality card had it at `at` (candles closed before it)."""
    import trend_strength
    from chop_scan import _vwap_today, closed_bars
    from indicators import enrich

    cut = int(dt.datetime.combine(day, at, tzinfo=IST).timestamp())
    part = frame[frame["ts"].astype("int64") < cut]
    if part.empty:
        return None
    last_day = dt.datetime.fromtimestamp(int(part["ts"].iloc[-1]), IST).date()
    if last_day != day:
        return None
    now = dt.datetime.combine(day, at, tzinfo=IST)
    bars = closed_bars(part, minutes, now)
    if len(bars) < max(int(slow), 2) + 1:
        return None
    enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), fast, slow)
    return trend_strength.read(enriched, _vwap_today(part, now), now=now, tape=part)


def _strong_ok(read: dict | None, rule: AutoArmRule) -> str | None:
    """'UP' / 'DOWN' when the read passes the rule, else None."""
    if not read:
        return None
    trend = read.get("trend")
    if trend not in ("STRONG_BULLISH", "STRONG_BEARISH"):
        return None
    side = "UP" if trend == "STRONG_BULLISH" else "DOWN"
    if rule.side != "BOTH" and rule.side != side:
        return None
    passed = sum(1 for ok in (read.get("trend_checks") or {}).values() if ok)
    return side if passed >= rule.min_checks else None


def _times(rule: AutoArmRule) -> list[dt.time]:
    out, t = [], dt.datetime.combine(dt.date(2000, 1, 1), rule.first)
    end = dt.datetime.combine(dt.date(2000, 1, 1), rule.last)
    while t <= end:
        out.append(t.time())
        t += dt.timedelta(minutes=max(1, int(rule.every_min)))
    return out


def picks_for_day(frames: dict[str, pd.DataFrame], day: dt.date, rule: AutoArmRule) -> list[dict]:
    """Each stock the rule would have armed that day, with the minute (`at`, HH:MM), oldest first."""
    found: dict[str, dict] = {}
    events: list[tuple[str, str, dict]] = []
    if rule.source in ("ACTIVE", "BOTH"):
        for at, symbol, side, pct in rule.active_hits.get(day.isoformat(), []):
            if symbol in frames and rule.first.strftime("%H:%M") <= at <= rule.last.strftime("%H:%M"):
                events.append((at, symbol, {"source": "Most active", "side": "LONG" if side == "BUY" else "SHORT",
                                            "why": f"{'buyers' if side == 'BUY' else 'sellers'} {pct:.0f}%"}))
    if rule.source in ("TREND", "BOTH"):
        # Only the candles up to that day's end are ever read: cut each tape once, not on every read.
        end = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), tzinfo=IST).timestamp())
        tapes = {s: f[f["ts"].astype("int64") < end] for s, f in frames.items()}
        for t in _times(rule):
            at = t.strftime("%H:%M")
            if callable(rule.on_step):
                try:
                    rule.on_step(at)
                except Exception:  # noqa: BLE001 - progress only
                    pass
            if len({e[1] for e in events if e[0] < at}) >= rule.max_day:
                # The day's picks are the first `max_day` by time: nothing armed later can make the list.
                break
            for symbol, frame in tapes.items():
                if symbol in found or any(e[1] == symbol and e[0] <= at for e in events):
                    continue
                try:
                    read = strong_read(frame, day, t, rule.minutes, rule.sma_fast, rule.sma_slow)
                except Exception:  # noqa: BLE001 - one bad tape never stops the day
                    log.exception("auto-arm trend read failed for %s", symbol)
                    continue
                side = _strong_ok(read, rule)
                if side and (rule.price_min or rule.price_max):
                    cut = int(dt.datetime.combine(day, t, tzinfo=IST).timestamp())
                    before = frame[frame["ts"].astype("int64") < cut]
                    if before.empty or not price_ok(float(before["close"].iloc[-1]), rule.price_min, rule.price_max):
                        side = None
                if side:
                    passed = sum(1 for ok in (read.get("trend_checks") or {}).values() if ok)
                    found[symbol] = {"at": at}
                    events.append((at, symbol, {"source": "Trend quality", "side": "LONG" if side == "UP" else "SHORT",
                                                "why": f"strong {side.lower()}, {passed} of 7 checks"}))
    out: list[dict] = []
    seen: set[str] = set()
    for at, symbol, info in sorted(events, key=lambda e: (e[0], e[1])):
        if symbol in seen:
            continue
        if len(out) >= rule.max_day:
            break
        seen.add(symbol)
        out.append({"symbol": symbol, "at": at, **info})
    return out


def arm_windows(day: dt.date, picks: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Each picked stock armed from its minute to the end of the day (epoch seconds, as `arm_log.windows`)."""
    end = int(dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0, 0), tzinfo=IST).timestamp())
    out = {}
    for p in picks:
        hh, mm = str(p["at"]).split(":")
        start = int(dt.datetime.combine(day, dt.time(int(hh), int(mm)), tzinfo=IST).timestamp())
        out[p["symbol"]] = [(start, end)]
    return out


def active_hits(scans: list[dict], pct: float, side: str, min_value_cr: float, price_min: float = 0.0,
                price_max: float = 0.0) -> list[tuple[str, str, str, float]]:
    """(HH:MM armed, symbol, BUY/SELL, %) from one day's saved Most active scans (each {as_of, rows}), first per stock.

    A scan read at 10:04:30 arms at 10:05 (the minute after the read), like a live scan that finished then."""
    from pressure_study import in_session

    need = float(pct) / 100.0
    out: list[tuple[str, str, str, float]] = []
    seen: set[str] = set()
    for scan in sorted(scans, key=lambda x: str(x.get("as_of") or "")):
        if not in_session(scan):
            continue
        try:
            when = dt.datetime.fromisoformat(str(scan["as_of"])).astimezone(IST)
        except (KeyError, ValueError):
            continue
        at = (when.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)).strftime("%H:%M")
        for r in scan.get("rows") or []:
            sym = str(r.get("symbol") or "").upper()
            share = r.get("buy_share")
            if not sym or sym in seen or share is None:
                continue
            try:
                share = float(share)
                value = float(r.get("value_cr") or 0.0)
            except (TypeError, ValueError):
                continue
            if value < min_value_cr or not price_ok(r.get("ltp"), price_min, price_max):
                continue
            if share >= need:
                s, p = "BUY", share * 100
            elif 1.0 - share >= need:
                s, p = "SELL", (1.0 - share) * 100
            else:
                continue
            if side != "BOTH" and side != s:
                continue
            seen.add(sym)
            out.append((at, sym, s, round(p, 1)))
    return out


def saved_scans(day: dt.date) -> list[dict]:
    """The day's saved Most active scans on this server (scan_history), each {as_of, rows}."""
    import scan_history

    out = []
    for run in scan_history.runs(day.isoformat(), "active"):
        full = scan_history.get(int(run["id"]))
        if full:
            out.append({"as_of": full.get("as_of"), "rows": full.get("rows") or [], "meta": full.get("meta") or {}})
    return out
