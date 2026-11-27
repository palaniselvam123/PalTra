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
per source (`_DONE`, memory only), at most `arm_max_day` (10) stocks a day per source (PAPER and LIVE counted apart, `_DONE_LIVE`); a stock already on the bot's list
is left as it is, nothing is ever unarmed. **PAPER bots by default**: a LIVE bot is refused and the run says so (arming a
LIVE bot means real Groww orders on its next signal), unless "Also arm a LIVE bot" (`arm_live`, off by default; turning it
on needs `confirm_live`) is ticked; then at most `arm_live_max_day` (3) stocks a day per source go on the LIVE bot, through
the same arm route as a person's click (one LIVE bot per stock still applies). Arming only: no order is placed;
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
# The same for a LIVE bot: PAPER and LIVE arms are counted apart, so stocks armed while the bot was in PAPER earlier
# in the day never use up the LIVE limit (owner's case: the bot went LIVE and auto-arm armed nothing on it).
_DONE_LIVE: dict[str, dict[dt.date, list[str]]] = {"strong": {}, "pressure": {}}
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
        stack, widen = bool(getattr(s, "arm_strong_stack", False)), int(getattr(s, "arm_strong_widen", 0) or 0)
        found = [p for p in found if scalp_alerts.stack_refusal(p, p.get("side"), stack, widen) is None]
    else:
        found = scalp_alerts.pressure_picks(rows, s, now, alerting=False)
    lo, hi = getattr(s, "arm_price_min", 0.0), getattr(s, "arm_price_max", 0.0)
    return [p for p in found if price_ok(p.get("price"), lo, hi)]


def forced(kind: str, s) -> bool:
    """"Force order when armed" for this source (scalp_alerts arm_strong_force / arm_pressure_force)."""
    return bool(s.arm_strong_force if kind == "strong" else s.arm_pressure_force)


def wanted(kind: str, s) -> tuple[bool, int]:
    if kind == "strong":
        return bool(s.arm_strong_on), int(s.arm_strong_bot)
    return bool(s.arm_pressure_on), int(s.arm_pressure_bot)


async def after_scan(kind: str, snapshot: dict, *, bots, arm, notify=None, now: dt.datetime | None = None,
                     check_market: bool = True, force=None) -> dict | None:
    """Arm the stocks a finished scan picks. `bots()` → {bot: {"mode", "armed", "name"}}, `arm(bot, symbol, source)`
    → None when armed else why not. With "Force order when armed" on for this source, `force(bot, symbol, live)` →
    (placed, note) runs right after each new arm (the bot's entry rule picks the side). Returns the run's record, or
    None when nothing was to be done. Never raises."""
    import scalp_alerts
    from groww_client import market_is_open

    try:
        now = now or dt.datetime.now(IST)
        s = scalp_alerts.load()
        on, bot = wanted(kind, s)
        if not on or (check_market and not market_is_open(now)):
            return None
        found = candidates(kind, snapshot.get("rows") or [], s, now)
        paper_done = _done(kind, now)
        live_done = _done(kind, now, _DONE_LIVE)
        seen = _done(kind, now, _SEEN)
        # New to this scan in either mode (the bot's mode is read below, only when there is something to arm).
        fresh = [p for p in found if p["symbol"] not in seen
                 and (p["symbol"] not in paper_done or p["symbol"] not in live_done)]
        if not fresh:
            return None
        record: dict = {
            "at": now.isoformat(timespec="seconds"), "source": kind, "bot": bot, "matched": len(found),
            "armed": [], "already": [], "skipped": [], "error": None, "forced": [],
        }
        do_force = force is not None and forced(kind, s)
        try:
            book = await bots()
            info = book.get(bot)
            if info is None:
                raise RuntimeError(f"Bot {bot} was not found.")
            record["bot_name"] = info.get("name") or f"Bot {bot}"
            live = (info.get("mode") or "PAPER").upper() == "LIVE"
            record["live"] = live
            if live and not s.arm_live:
                raise RuntimeError(
                    f"{record['bot_name']} is LIVE. Auto-arm arms PAPER bots; a LIVE bot only with \"Also arm a LIVE bot\" "
                    "ticked on the Alerts tab (arming a LIVE bot means real Groww orders on its next signal)."
                )
            done = live_done if live else paper_done
            fresh = [p for p in fresh if p["symbol"] not in done]
            if not fresh:
                return None
            armed = {str(x).upper() for x in info.get("armed") or []}
            limit = min(int(s.arm_max_day), int(s.arm_live_max_day)) if live else int(s.arm_max_day)
            room = max(0, limit - len(done))
            for p in fresh:
                sym = str(p["symbol"]).upper()
                if sym in armed:
                    record["already"].append(sym)
                    seen.append(sym)
                    continue
                if room <= 0:
                    record["skipped"].append({"symbol": sym, "why": f"today's limit of {limit}{' (LIVE)' if live else ''} reached"})
                    continue
                why = await (arm(bot, sym, SOURCE_CHIP[kind], live=True) if live else arm(bot, sym, SOURCE_CHIP[kind]))
                if why is None:
                    record["armed"].append(sym)
                    armed.add(sym)
                    done.append(sym)
                    room -= 1
                    if do_force:
                        try:
                            placed, note = await force(bot, sym, live)
                        except Exception as exc:  # noqa: BLE001
                            placed, note = False, str(exc) or type(exc).__name__
                        record["forced"].append({"symbol": sym, "placed": bool(placed), "note": str(note)[:200]})
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
    lines = [f"🤖 Auto-arm ({where}): armed {', '.join(record['armed'])} on {name}"
             + (" — LIVE: real Groww orders on its next signal." if record.get("live") else ".")]
    if record["skipped"]:
        lines.append("Not armed: " + "; ".join(f"{x['symbol']} ({x['why']})" for x in record["skipped"][:8]))
    done = record.get("forced") or []
    if done:
        placed = [x for x in done if x["placed"]]
        if placed:
            lines.append("Force order: " + "; ".join(f"{x['symbol']} {x['note']}" for x in placed[:8])
                         + (" — LIVE: real Groww orders." if record.get("live") else "."))
        missed = [x for x in done if not x["placed"]]
        if missed:
            lines.append("No force order: " + "; ".join(f"{x['symbol']} ({x['note']})" for x in missed[:8]))
    else:
        lines.append("Arming only: the bot trades them on its own next signal.")
    return "\n".join(lines)


def view() -> dict:
    today = dt.datetime.now(IST)
    return {
        "recent": recent[:20],
        # Today's arms, PAPER and LIVE together (each counted against its own limit).
        "today": {k: list(dict.fromkeys([*_done(k, today), *_done(k, today, _DONE_LIVE)]))
                  for k in ("strong", "pressure")},
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
    #: NORMAL = the real candles; HA = the Trend quality read on Heikin Ashi candles (chop_scan.ha_bars).
    candles: str = "NORMAL"
    #: Today's first N candles left out of the strong-trend read (0 = none; the Trend quality card's "Skip today's first
    #: candles", strong_read `skip`).
    skip_candles: int = 0
    min_checks: int = 6
    side: str = "BOTH"
    #: The three SMAs (sma_fast / sma_slow / `third`) stacked the trend's way, and with `widen` N > 0 both gaps widening
    #: for N closed candles (scalp_alerts arm_strong_stack / arm_strong_widen / arm_strong_third). Trend quality picks only.
    stack: bool = False
    widen: int = 0
    third: int = 50
    pct: float = 70.0
    pressure_side: str = "BOTH"
    min_value_cr: float = 0.0
    #: Volume vs usual at least (×, 0 = any).
    min_rvol: float = 0.0
    #: Buyers / sellers judged by WAITING orders, EXECUTED volume or BOTH (scalp_alerts.control_side).
    basis: str = "WAITING"
    flow_pct: float = 60.0
    max_day: int = 10
    #: Trading stops here each day (positions squared off, the day ends); None = the usual 15:30 close.
    until: dt.time | None = None
    #: Only stocks priced inside this range when armed (₹, 0 = no bound): the tape's last close before the arm minute.
    price_min: float = 0.0
    price_max: float = 0.0
    #: "Force order when armed" per source (scalp_alerts arm_strong_force / arm_pressure_force): at its arm minute
    #: the replayed bot enters at once, its entry rule picking the side (StrategyEngine.force_order(auto=True)).
    force_strong: bool = False
    force_pressure: bool = False
    #: {IST day ISO: [(HH:MM, symbol, BUY/SELL, pct)]} from the saved Most active scans, filled before the run.
    active_hits: dict = field(default_factory=dict)
    kind: str = KIND
    #: Called with "HH:MM" as the Trend quality reads reach each minute (the replay bar's progress). Not saved.
    on_step: object = field(default=None, repr=False, compare=False)
    #: Called with (HH:MM, symbol, LONG / SHORT) as each Trend quality pick is found (the replay bar). Not saved.
    on_found: object = field(default=None, repr=False, compare=False)

    @property
    def pick_time(self) -> dt.time:
        return self.first

    def as_dict(self) -> dict:
        return {
            "kind": KIND, "source": self.source, "pick_time": self.first.strftime("%H:%M"),
            "first": self.first.strftime("%H:%M"), "last": self.last.strftime("%H:%M"), "every_min": self.every_min,
            "minutes": self.minutes, "sma_fast": self.sma_fast, "sma_slow": self.sma_slow, "candles": self.candles,
            "skip_candles": self.skip_candles, "min_checks": self.min_checks, "side": self.side, "pct": self.pct,
            "stack": self.stack, "widen": self.widen, "third": self.third,
            "pressure_side": self.pressure_side, "min_value_cr": self.min_value_cr, "min_rvol": self.min_rvol,
            "basis": self.basis, "flow_pct": self.flow_pct,
            "max_day": self.max_day,
            "price_min": self.price_min, "price_max": self.price_max,
            "force_strong": self.force_strong, "force_pressure": self.force_pressure,
            "until": self.until.strftime("%H:%M") if self.until else None,
            "active_days": sorted(self.active_hits),
        }


def force_symbols(rule, picked: list[dict]) -> set[str]:
    """The picked stocks the replayed bot forces an entry on at their arm minute ("Force order when armed")."""
    want = {"Trend quality": bool(getattr(rule, "force_strong", False)),
            "Most active": bool(getattr(rule, "force_pressure", False))}
    return {str(p.get("symbol") or "").upper() for p in picked if want.get(str(p.get("source") or ""))} - {""}


def strong_read(frame: pd.DataFrame, day: dt.date, at: dt.time, minutes: int, fast: int, slow: int,
                ha: bool = False, skip: int = 0, third: int = 0) -> dict | None:
    """One stock's strong-trend read as the Trend quality card had it at `at` (candles closed before it).

    The tape is cut to what the live scan downloads (`WARMUP_DAYS` calendar days from midnight, chop_scan
    `ChopScanner._run`), so the morning volume check compares today with the same earlier sessions and the Wilder ATR
    starts from the same candle: a longer tape (a multi-day run carries every earlier day) read differently.

    `skip` (minutes after 09:15, 0 = off): today's candles before 09:15 + skip are left out of the checks and the VWAP,
    and a strong trend must have moved 1 ATR its way since them, as the Trend quality card reads it (chop_scan.analyze).

    `third` (> slow, 0 = off): adds the card's three-SMA read (chop_scan.three_read: stack3, widen_candles…) on the same
    candles, from as many days as that SMA needs."""
    import trend_strength
    from chop_scan import WARMUP_DAYS, _moved_since_skip, _vwap_today, closed_bars, ha_bars
    from indicators import enrich

    cut = int(dt.datetime.combine(day, at, tzinfo=IST).timestamp())
    start = int(dt.datetime.combine(day - dt.timedelta(days=WARMUP_DAYS), dt.time(0, 0), tzinfo=IST).timestamp())
    ts = frame["ts"].astype("int64")
    part = frame[(ts >= start) & (ts < cut)]
    if part.empty:
        return None
    last_day = dt.datetime.fromtimestamp(int(part["ts"].iloc[-1]), IST).date()
    if last_day != day:
        return None
    now = dt.datetime.combine(day, at, tzinfo=IST)
    bars = closed_bars(part, minutes, now)
    if len(bars) < max(int(slow), 2) + 1:
        return None
    # The last closed candle's close, before any Heikin Ashi change (chop_scan.analyze): the price VWAP is compared with.
    real_close = float(bars["close"].iloc[-1])
    today_start = int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST).timestamp())
    skip_from = today_start + int(skip) * 60 if skip else None
    vwap = _vwap_today(part, now, skip_from)
    if ha:  # as the Trend quality card reads them: the Heikin Ashi candles' own trend (ha_trend), VWAP on real prices
        import ha_trend

        bars = ha_bars(bars)
        enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), fast, slow)
        view = _skip_view(enriched, today_start, skip_from)
        read = ha_trend.checks(view, vwap, real_close)
    else:
        enriched = enrich(bars.drop(columns=["volume"], errors="ignore"), fast, slow)
        view = _skip_view(enriched, today_start, skip_from)
        read = trend_strength.read(view, vwap, now=now, tape=part)
    if third and int(third) not in (int(fast), int(slow)) and isinstance(read, dict):
        read = {**read, **_three_at(frame, day, now, cut, minutes, fast, slow, int(third), ha, bars)}
    if skip_from is None or not isinstance(read, dict):
        return read
    kept, note = _moved_since_skip(enriched, today_start, skip_from, read.get("trend"))
    notes = {**(read.get("trend_notes") or {}), **({"skip": note} if note else {})}
    return {**read, "trend": kept, "trend_notes": notes, "trend_skip_min": int(skip)}


def _three_at(frame: pd.DataFrame, day: dt.date, now: dt.datetime, cut: int, minutes: int, fast: int, slow: int,
              third: int, ha: bool, bars: pd.DataFrame) -> dict:
    """The Trend quality card's three-SMA read at `now`, on enough days for the third SMA (chop_scan.analyze)."""
    from chop_scan import WARMUP_DAYS, closed_bars, cut_window, ha_bars, three_read, window_days

    need = window_days(minutes, max(third, slow))
    if need > WARMUP_DAYS:
        before = frame[frame["ts"].astype("int64") < cut]
        bars = closed_bars(cut_window(before, now, need), minutes, now)
        if ha:
            bars = ha_bars(bars)
    return three_read(bars, fast, slow, third)


def _skip_view(enriched: pd.DataFrame, today_start: int, skip_from: int | None) -> pd.DataFrame:
    """The candles the checks read: without today's skipped opening ones (chop_scan._skipped), else all."""
    if skip_from is None:
        return enriched
    from chop_scan import _skipped

    view = _skipped(enriched, today_start, skip_from)
    return view if len(view) >= 2 else enriched


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
    if passed < rule.min_checks:
        return None
    if getattr(rule, "stack", False) or int(getattr(rule, "widen", 0) or 0) > 0:
        import scalp_alerts

        if scalp_alerts.stack_refusal(read, side, rule.stack, rule.widen) is not None:
            return None
    return side


def strong_why(read: dict, side: str, rule: AutoArmRule) -> str:
    """Why a Trend quality pick was armed, with the candle and rules it was read on, so it can be set against a scan's
    History row ("strong down, 7 of 7 checks on 15-min candles (morning rules); missed: volume")."""
    checks = {k: v for k, v in (read.get("trend_checks") or {}).items() if isinstance(v, bool)}
    passed = sum(1 for ok in checks.values() if ok)
    kind = "Heikin Ashi " if rule.candles == "HA" else ""
    text = f"strong {side.lower()}, {passed} of {len(checks) or 7} checks on {rule.minutes}-min {kind}candles"
    if read.get("trend_mode") == "MORNING":
        text += " (morning rules, before 10:00)"
    if rule.skip_candles:
        text += f", first {rule.skip_candles} candle(s) skipped"
    if read.get("stack3") and (getattr(rule, "stack", False) or getattr(rule, "widen", 0)):
        lines = " / ".join(str(n) for n in sorted({rule.sma_fast, rule.sma_slow, int(read.get("sma_third") or 0)} - {0}))
        text += (f"; SMA {lines} stacked "
                 f"{'up' if read['stack3'] == 'BULLISH' else 'down'}, gaps widening {read.get('widen_candles') or 0} candle(s)")
    missed = [k for k, ok in checks.items() if not ok]
    if missed:
        text += "; missed: " + ", ".join(missed)
    return text


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
                    read = strong_read(frame, day, t, rule.minutes, rule.sma_fast, rule.sma_slow, ha=rule.candles == "HA",
                                       skip=rule.skip_candles * rule.minutes,
                                       third=rule.third if (rule.stack or rule.widen) else 0)
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
                    found[symbol] = {"at": at}
                    if callable(rule.on_found):
                        try:
                            rule.on_found(at, symbol, "LONG" if side == "UP" else "SHORT")
                        except Exception:  # noqa: BLE001 - progress only
                            pass
                    events.append((at, symbol, {"source": "Trend quality", "side": "LONG" if side == "UP" else "SHORT",
                                                "why": strong_why(read, side, rule)}))
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
                price_max: float = 0.0, min_rvol: float = 0.0, basis: str = "WAITING",
                flow_pct: float = 60.0) -> list[tuple[str, str, str, float]]:
    """(HH:MM armed, symbol, BUY/SELL, %) from one day's saved Most active scans (each {as_of, rows}), first per stock.

    A stock is armed the minute after its own order-book read (`read_at`; a scan saved before rows carried it falls
    back to the scan's start): a read at 10:04:30 arms at 10:05, like a live scan that read it then. A pass takes
    minutes, so the scan's start would arm stocks read late in it before anyone could have seen them. A one-sided
    order book (no buy or no sell orders waiting) is never a hit (pressure_exit.usable_share).

    `basis` WAITING / EXECUTED / BOTH reads the waiting orders, the row's executed buy % (`flow_buy`, saved on scans
    since it was added; older rows never pass EXECUTED / BOTH) or both (scalp_alerts.control_side); the % returned is
    the executed one with EXECUTED, else the waiting one."""
    import scalp_alerts
    from pressure_study import in_session

    first: dict[str, tuple[dt.datetime, str, float]] = {}
    for scan in sorted(scans, key=lambda x: str(x.get("as_of") or "")):
        if not in_session(scan):
            continue
        try:
            start = dt.datetime.fromisoformat(str(scan["as_of"])).astimezone(IST)
        except (KeyError, ValueError):
            continue
        for r in scan.get("rows") or []:
            sym = str(r.get("symbol") or "").upper()
            if not sym:
                continue
            try:
                value = float(r.get("value_cr") or 0.0)
            except (TypeError, ValueError):
                continue
            if value < min_value_cr or not price_ok(r.get("ltp"), price_min, price_max):
                continue
            if not scalp_alerts.rvol_ok(r.get("rvol"), min_rvol):
                continue
            got = scalp_alerts.control_side(r, basis, float(pct), float(flow_pct))
            if got is None:
                continue
            s, wait, done = got
            p = done if basis == "EXECUTED" else wait
            if side != "BOTH" and side != s:
                continue
            when = start
            if r.get("read_at"):
                try:
                    read = dt.datetime.fromisoformat(str(r["read_at"]))
                    when = (read if read.tzinfo else read.replace(tzinfo=IST)).astimezone(IST)
                except ValueError:
                    pass
            if sym not in first or when < first[sym][0]:
                first[sym] = (when, s, round(p, 1))
    out = [
        ((when.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)).strftime("%H:%M"), sym, s, p)
        for sym, (when, s, p) in first.items()
    ]
    out.sort(key=lambda h: (h[0], h[1]))
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
