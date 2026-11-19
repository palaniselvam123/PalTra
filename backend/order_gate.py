"""Simple order-flow check (owner's request: the waiting / executed settings were hard to set up together; one plain card
with four tick boxes and typed numbers, next to the old ones and independent of them). Per bot and per stock, PAPER and
LIVE, replays too; any stock.

Two numbers are read for the trade's real side (a flipped order is judged on the side it really goes):

- **Executed**: that side's share of the shares traded at market in the last *closed* candle the bot exits on (its own
  candle, or the colour / pattern candle; the chart's candle at that size), estimated by trade_flow.py.
- **Waiting**: that side's share of the orders waiting, from the latest Most active read of the stock at most
  pressure_exit.MAX_AGE_SEC (20 min) old. A stock the Most active scan has not read has no waiting number.

**Entry** (`og_entry_exec` + `og_entry_exec_pct`, `og_entry_wait` + `og_entry_wait_pct`): every ticked number must be at
least its %, else the entry is refused like the other entry checks (it waits; late entry can retry it). A number that
cannot be read (too little volume, no fresh read) refuses too.

**Exit** (`og_exit_exec` + `og_exit_exec_pct`, `og_exit_wait` + `og_exit_wait_pct`): once per closed exit candle that
opened at or after the entry, the trade closes (`ORDER_FLOW_EXIT`, via StrategyEngine._exit_now, which cancels a LIVE
stop first) when every ticked number has fallen to or below its %. Above it = the trade continues. A number that cannot
be read never closes. Stops, targets and every other exit stay; whichever comes first closes.

Each judged candle is noted for the chart (StrategyEngine._og_log), so the hover line says why an entry was refused or
why the trade was not closed on that candle. No order path of its own.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

EXIT_REASON = "ORDER_FLOW_EXIT"
MIN_PCT = 1.0
MAX_PCT = 99.0
DEFAULTS = {
    "og_entry_exec_pct": 60.0,
    "og_entry_wait_pct": 60.0,
    "og_exit_exec_pct": 45.0,
    "og_exit_wait_pct": 45.0,
}


def _flag(cfg, name: str) -> bool:
    return bool(getattr(cfg, name, False))


def _pct(cfg, name: str) -> float:
    value = getattr(cfg, name, None)
    try:
        value = float(DEFAULTS[name] if value is None else value)
    except (TypeError, ValueError):
        value = DEFAULTS[name]
    return min(MAX_PCT, max(MIN_PCT, value))


def entry_exec(cfg) -> bool:
    return _flag(cfg, "og_entry_exec")


def entry_wait(cfg) -> bool:
    return _flag(cfg, "og_entry_wait")


def exit_exec(cfg) -> bool:
    return _flag(cfg, "og_exit_exec")


def exit_wait(cfg) -> bool:
    return _flag(cfg, "og_exit_wait")


def entry_exec_pct(cfg) -> float:
    return _pct(cfg, "og_entry_exec_pct")


def entry_wait_pct(cfg) -> float:
    return _pct(cfg, "og_entry_wait_pct")


def exit_exec_pct(cfg) -> float:
    return _pct(cfg, "og_exit_exec_pct")


def exit_wait_pct(cfg) -> float:
    return _pct(cfg, "og_exit_wait_pct")


def entry_on(cfg) -> bool:
    return entry_exec(cfg) or entry_wait(cfg)


def exit_on(cfg) -> bool:
    return exit_exec(cfg) or exit_wait(cfg)


def candle_span(now_ts: float, exit_minutes: int) -> tuple[int, int]:
    """(start, end) epoch seconds of the last closed candle of `exit_minutes` (from 09:15 IST) at `now_ts`."""
    import flow_exit

    return flow_exit.entry_span(now_ts, 0, exit_minutes)


def executed(points: list[dict], side: str, minutes: int) -> float | None:
    """The side's % of the executed volume over the points, or None with too little volume (under half the minutes)."""
    with_volume = [p for p in points if p["buy"] + p["sell"] > 0]
    if not with_volume or len(with_volume) < max(1, minutes // 2):
        return None
    buy = sum(p["buy"] for p in with_volume)
    sell = sum(p["sell"] for p in with_volume)
    return round(100.0 * (buy if side == "LONG" else sell) / (buy + sell), 1)


def waiting(symbol: str, side: str, now_ts: float) -> tuple[float, float] | None:
    """(the side's % of the waiting orders, read time) from a Most active read at most MAX_AGE_SEC old, else None."""
    import pressure_exit

    read = pressure_exit.fresh_read(symbol, now_ts)
    if read is None:
        return None
    at, share = read
    return pressure_exit.side_share(share, side), at


@dataclass
class Reading:
    side: str
    start: int
    end: int
    exec_pct: float | None = None
    wait_pct: float | None = None
    wait_at: float | None = None


def read(symbol: str, side: str, tape, now_ts: float, exit_minutes: int, span: tuple[int, int] | None = None) -> Reading:
    """Both numbers for `side` at `now_ts` (the executed one over the last closed exit candle, or `span`)."""
    import trade_flow

    start, end = span or candle_span(now_ts, exit_minutes)
    out = Reading(side=side, start=start, end=end)
    try:
        out.exec_pct = executed(trade_flow.minutes(symbol, tape, start, end), side, (end - start) // 60)
    except Exception:  # noqa: BLE001 - a bad tape reads as unknown
        out.exec_pct = None
    got = waiting(symbol, side, now_ts)
    if got is not None:
        out.wait_pct, out.wait_at = got
    return out


def _clock(ts: float) -> str:
    return dt.datetime.fromtimestamp(ts, IST).strftime("%H:%M")


def _who(side: str) -> str:
    return "buyers" if side == "LONG" else "sellers"


def _exec_words(r: Reading) -> str:
    span = f"{_clock(r.start)}–{_clock(r.end)}"
    if r.exec_pct is None:
        return f"executed: too little volume to read in {span}"
    return f"executed {_who(r.side)} {r.exec_pct:g}% ({span})"


def _wait_words(r: Reading) -> str:
    if r.wait_pct is None:
        return "waiting: no Most active read in the last 20 min"
    return f"waiting {_who(r.side)} {r.wait_pct:g}% (read {_clock(r.wait_at or 0)})"


def entry_verdict(cfg, r: Reading) -> tuple[bool, str]:
    """(allowed?, plain note): every ticked number at least its %; an unknown number refuses."""
    parts: list[str] = []
    ok = True
    if entry_exec(cfg):
        need = entry_exec_pct(cfg)
        good = r.exec_pct is not None and r.exec_pct >= need
        ok = ok and good
        parts.append(f"{_exec_words(r)}, needs {need:g}% {'✓' if good else '✗'}")
    if entry_wait(cfg):
        need = entry_wait_pct(cfg)
        good = r.wait_pct is not None and r.wait_pct >= need
        ok = ok and good
        parts.append(f"{_wait_words(r)}, needs {need:g}% {'✓' if good else '✗'}")
    return ok, "; ".join(parts)


def entry_refusal(cfg, r: Reading) -> str | None:
    """None when the entry may go, else the refusal joined into the entry checks' reasons."""
    if not entry_on(cfg):
        return None
    ok, note = entry_verdict(cfg, r)
    return None if ok else f"order-flow check: {note}"


def exit_verdict(cfg, r: Reading) -> tuple[bool, str]:
    """(close?, plain note): closes only when every ticked number is known and at or below its %."""
    parts: list[str] = []
    close = True
    if exit_exec(cfg):
        limit = exit_exec_pct(cfg)
        weak = r.exec_pct is not None and r.exec_pct <= limit
        close = close and weak
        state = "unknown, held" if r.exec_pct is None else ("at or under, ✗" if weak else "above, ✓ holds")
        parts.append(f"{_exec_words(r)}; exit at ≤ {limit:g}% ({state})")
    if exit_wait(cfg):
        limit = exit_wait_pct(cfg)
        weak = r.wait_pct is not None and r.wait_pct <= limit
        close = close and weak
        state = "unknown, held" if r.wait_pct is None else ("at or under, ✗" if weak else "above, ✓ holds")
        parts.append(f"{_wait_words(r)}; exit at ≤ {limit:g}% ({state})")
    return close, "; ".join(parts)
