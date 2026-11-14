"""Second candle test (second_candle.py): the owner's Most active routine, measured on every stock and past day.

The routine: sort the Most active card by Volume vs usual, look at the day's second candle (5 minutes by default), and
enter its way at its close: green = buy, red = sell. His hand checks say it usually gives at least ₹10. Checking every
stock by hand is slow, so this reads each stock's past days (the same cached 1-minute candles the Strategy lab uses) and,
for each stock and day, gives:

* the side (the Nth candle's colour, a doji skipped; or with `side_rule` the way most of candles 1..N went), the entry (the open of the candle after it), the stock's
  volume vs usual at that moment (`active_picks._cum_at`: today's running volume against the same minutes of the
  earlier sessions) and its rank by it among the stocks read that day;
* how far the price went **with** the trade (`best_pts`, in ₹ and % of the entry) and when (`best_at`), how far it
  went **against** it (`worst_pts`, `worst_at`), whether it reached the target (`hit`, `target_at`) and how far it had
  gone against the trade **before** reaching it (`against_before_target`: the stop distance that would have survived),
  and where it stood at the square-off (`close_pts`);
* a **trailing-stop sweep** (`trail`): for each distance in % of the entry, the ₹ the trade makes when the stop follows
  the best price at that distance (judged on the 1-minute candles: the stop in force when a minute starts is tested on
  that minute's low / high, then the best moves, the cautious order; else the square-off).

`summarize` gives the hit rate, the averages, the split by side and by rank, each day, and the trailing-stop table
(total ₹, average, win %, worst trade per distance; the best distance), with plain-words `answer` lines. `SecondCandle`
runs one test at a time (one stock after another, `strategy_lab.load_tape` so days read before cost nothing) and keeps
the last report in `second_candle.json` next to the SMA DB. Read-only research: practice arithmetic, no order path,
nothing armed, nothing in the strategy reads it.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from active_picks import _cum_at, _day_of
from candles import resample
import heikin_ashi
from nth_candle import DIR_RULES, candle_moves
from types import SimpleNamespace
from strategy_lab import load_tape

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SESSION_OPEN = dt.time(9, 15)
SIZES = (1, 3, 5, 10, 15)
MAX_CANDLE_NO = 6
MIN_DAYS, MAX_DAYS = 1, 30
MAX_STOCKS = 250
WARMUP_DAYS = 5
MIN_SESSIONS = 1
DEFAULT_TRAILS = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0)
MAX_TRAILS = 8
#: ₹ levels each trade keeps the minute it first reached with (`fav_min`) and against (`adv_min`) the trade, so any
#: target / stop pair from this grid can be judged later without reading the candles again (target_stop_table).
LEVELS = (2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0)
DEFAULT_STOPS = (3.0, 5.0, 8.0, 10.0, 15.0, 20.0)
#: Stop levels (₹) the stop-and-reverse leg is measured for: after the first trade's stop, the other side is entered at the
#: stop price and followed to the square-off (`rev`).
REVERSE_STOPS = DEFAULT_STOPS
HALF_HOURS = ("09:15", "09:45", "10:15", "10:45", "11:15", "11:45", "12:15", "12:45", "13:15", "13:45", "14:15", "14:45", "15:15")
SIDE_RULES = ("NTH", "MAJORITY", "AGREE")
MAJORITY_BY = ("COUNT", "SIZE")
RANK_BUCKETS = ((1, 5, "Top 5"), (6, 10, "6-10"), (11, 20, "11-20"), (21, 10**6, "21 and after"))


def majority(candles: pd.DataFrame, upto: int, by: str = "COUNT", dir_rule: str = "COLOUR",
             prev_close: float | None = None) -> tuple[str | None, int, int, float]:
    """The side most of the first `upto` candles went: (LONG / SHORT / None for a tie, ups, downs, net move in ₹).
    COUNT compares up vs down candles (dojis count for neither); SIZE adds the moves up, signed. Each candle is read
    by `dir_rule` (nth_candle.candle_moves: COLOUR green / red, CLOSES close vs the previous close, BOTH both)."""
    part = candles.iloc[:upto]
    body = np.asarray(candle_moves(part["open"].astype(float), part["close"].astype(float), prev_close, dir_rule), dtype=float)
    greens, reds, net = int((body > 0).sum()), int((body < 0).sum()), float(np.nansum(body))
    score = (greens - reds) if by == "COUNT" else net
    side = "LONG" if score > 0 else "SHORT" if score < 0 else None
    return side, greens, reds, net


@dataclass
class Rule:
    minutes: int = 5
    candle_no: int = 2
    #: Several candles read in one run (each its own trade, ranked within its own candle); empty = (candle_no,).
    candle_nos: tuple[int, ...] = ()
    top_n: int = 20
    target_points: float = 10.0
    square_off: dt.time = dt.time(15, 15)
    trails: tuple[float, ...] = DEFAULT_TRAILS
    #: Skip a signal candle whose body is under this % of its open (0 = any body counts; a doji is always skipped).
    min_body_pct: float = 0.0
    #: How the side is picked (`SIDE_RULES`): NTH = the Nth candle's colour; MAJORITY = the colour most of candles 1..N
    #: closed (a tie is skipped); AGREE = the Nth candle's colour, only when most of candles 1..N agree with it.
    side_rule: str = "NTH"
    #: How "most" is counted for MAJORITY / AGREE: COUNT = green vs red candles; SIZE = the bodies added up (signed).
    majority_by: str = "COUNT"
    #: What makes a candle up / down (nth_candle.DIR_RULES): COLOUR = green / red; CLOSES = its close vs the previous
    #: candle's close (candle 1 against the session before); BOTH = green and a higher close (red and a lower one).
    dir_rule: str = "COLOUR"
    #: Read Heikin Ashi candles for the colour, body and "most" (the bot's `ha_colours`); prices and closes stay real.
    ha: bool = False

    def clean(self) -> "Rule":
        trails = tuple(sorted({round(float(t), 3) for t in self.trails if 0.05 <= float(t) <= 10}))[:MAX_TRAILS] or DEFAULT_TRAILS
        nos = tuple(sorted({max(1, min(MAX_CANDLE_NO, int(n))) for n in (self.candle_nos or (self.candle_no,))}))
        return Rule(
            minutes=self.minutes if self.minutes in SIZES else 5,
            candle_no=nos[0],
            candle_nos=nos,
            top_n=max(0, int(self.top_n)),
            target_points=max(0.05, float(self.target_points)),
            square_off=self.square_off,
            trails=trails,
            min_body_pct=max(0.0, float(self.min_body_pct)),
            side_rule=self.side_rule if self.side_rule in SIDE_RULES else "NTH",
            majority_by=self.majority_by if self.majority_by in MAJORITY_BY else "COUNT",
            dir_rule=self.dir_rule if self.dir_rule in DIR_RULES else "COLOUR",
            ha=bool(self.ha),
        )

    def view(self) -> dict:
        return {
            "minutes": self.minutes,
            "candle_no": self.candle_no,
            "candle_nos": list(self.candle_nos or (self.candle_no,)),
            "top_n": self.top_n,
            "target_points": self.target_points,
            "square_off": self.square_off.strftime("%H:%M"),
            "trails": list(self.trails),
            "min_body_pct": self.min_body_pct,
            "side_rule": self.side_rule,
            "majority_by": self.majority_by,
            "dir_rule": self.dir_rule,
            "ha": self.ha,
        }


def _hhmm(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def _trail_points(side: str, entry: float, high: np.ndarray, low: np.ndarray, m0: int, m_end: int, pct: float, close_pts: float) -> float:
    """₹ per share a trailing stop `pct` % of the entry behind the best price would have made (cautious order)."""
    long = side == "LONG"
    gap = entry * pct / 100.0
    best = entry
    for m in range(m0, m_end):
        stop = best - gap if long else best + gap
        if long and low[m] <= stop:
            return float(stop - entry)
        if not long and high[m] >= stop:
            return float(entry - stop)
        best = max(best, float(high[m])) if long else min(best, float(low[m]))
    return float(close_pts)


def _reverse_legs(side: str, entry: float, ts: np.ndarray, high: np.ndarray, low: np.ndarray, opn: np.ndarray, cls: np.ndarray,
                  m0: int, m_sq: int, adv: np.ndarray) -> dict[str, list]:
    """Stop and reverse: for each REVERSE_STOPS level the first trade's stop reached, the other side entered at the stop
    price on that minute and followed from the next minute to the square-off.

    Each value is [minute of the reverse entry after the first entry, best ₹ with the reverse, worst ₹ against it, ₹ at the
    square-off, then for every LEVELS target the minutes after the reverse entry it was first reached with the reverse
    (None = never), then the minutes it first went the stop's ₹ against the reverse (None = never)]."""
    out: dict[str, list] = {}
    long = side == "LONG"
    if m_sq < len(ts):
        close_price = float(opn[m_sq])
    else:
        close_price = float(cls[m_sq - 1])
    for stop in REVERSE_STOPS:
        hit = adv >= stop
        if not bool(hit.any()):
            continue
        k = int(np.argmax(hit))
        rev_entry = entry - stop if long else entry + stop  # a stopped buy sells there, a stopped sell buys
        start, end = m0 + k + 1, m_sq
        at = int((ts[m0 + k] - ts[m0]) // 60)
        close_pts = (rev_entry - close_price) if long else (close_price - rev_entry)
        if start >= end:
            out[str(stop)] = [at, 0.0, 0.0, round(close_pts, 2), *([None] * len(LEVELS)), None]
            continue
        fav = (rev_entry - low[start:end]) if long else (high[start:end] - rev_entry)
        bad = (high[start:end] - rev_entry) if long else (rev_entry - low[start:end])
        mins = (ts[start:end] - ts[m0 + k]) // 60
        fav_m = [int(mins[int(np.argmax(fav >= lv))]) if bool((fav >= lv).any()) else None for lv in LEVELS]
        bad_m = int(mins[int(np.argmax(bad >= stop))]) if bool((bad >= stop).any()) else None
        out[str(stop)] = [at, round(float(max(fav.max(), 0.0)), 2), round(float(max(bad.max(), 0.0)), 2), round(close_pts, 2), *fav_m, bad_m]
    return out


def reverse_outcome(row: dict, target: float, stop: float) -> tuple[str, float] | None:
    """The reverse leg after the first trade's stop: ("target" | "stop" | "close", ₹ per share), the reverse taking the same
    target and stop. None = no reverse (the first trade did not stop out first, or the stop is not a REVERSE_STOPS level)."""
    first, _ = target_stop_outcome(row, target, stop)
    if first != "stop":
        return None
    s_lv = _nearest_level(stop)
    leg = (row.get("rev") or {}).get(str(s_lv))
    if not leg:
        return None
    t_lv = _nearest_level(target)
    t_min = leg[4 + LEVELS.index(t_lv)]
    s_min = leg[4 + len(LEVELS)]
    if t_min is not None and (s_min is None or t_min < s_min):
        return "target", float(t_lv)
    if s_min is not None:
        return "stop", -float(s_lv)
    return "close", float(leg[3])


def stop_reverse_table(rows: list[dict], target: float, stops: tuple[float, ...] = REVERSE_STOPS) -> list[dict]:
    """For the target and each stop: of the trades that stopped out first, how the reverse did, and the total with it."""
    out = []
    for stop in stops:
        plain = [target_stop_outcome(r, target, stop) for r in rows]
        revs = [reverse_outcome(r, target, stop) for r in rows]
        taken = [x for x in revs if x is not None]
        if not plain:
            continue
        plain_total = sum(p for _, p in plain)
        rev_total = sum(p for _, p in taken)
        best = [((r.get("rev") or {}).get(str(_nearest_level(stop))) or [0, 0])[1] for r, x in zip(rows, revs) if x is not None]
        out.append(
            {
                "target": _nearest_level(target),
                "stop": _nearest_level(stop),
                "stopped": len(taken),
                "rev_target": sum(1 for k, _ in taken if k == "target"),
                "rev_stop": sum(1 for k, _ in taken if k == "stop"),
                "rev_neither": sum(1 for k, _ in taken if k == "close"),
                "rev_total": round(rev_total, 2),
                "rev_avg_best": _mean(best),
                "plain_total": round(plain_total, 2),
                "total": round(plain_total + rev_total, 2),
            }
        )
    return out


def analyze_day(symbol: str, tape: pd.DataFrame, day: dt.date, rule: Rule, candle_no: int | None = None) -> dict | None:
    """One stock on one day: the Nth candle's call and what the price did after it. None = no trade that day."""
    if tape is None or getattr(tape, "empty", True):
        return None
    days = _day_of(tape["ts"])
    today = tape[days.values == day].reset_index(drop=True)
    if len(today) < 5:
        return None
    candles = resample(today, rule.minutes) if rule.minutes > 1 else today
    candle_no = int(candle_no or rule.candle_no)
    n = candle_no - 1
    if candles is None or len(candles) <= n + 1:
        return None
    nxt = candles.iloc[n + 1]
    if rule.ha:
        # Heikin Ashi colours: `open` moved so close − open is the HA body (the close stays real), seeded on the session.
        candles = heikin_ashi.view(candles, SimpleNamespace(ha_colours=True))
    sig = candles.iloc[n]
    o, c = float(sig["open"]), float(sig["close"])
    if not (np.isfinite(o) and np.isfinite(c)) or o <= 0:
        return None
    earlier = tape[days.values < day]
    prev_close = float(earlier["close"].iloc[-1]) if not earlier.empty else None
    moves = candle_moves(candles["open"].iloc[:candle_no].astype(float), candles["close"].iloc[:candle_no].astype(float),
                         prev_close, rule.dir_rule)
    move = float(moves[n])
    colour = "LONG" if move > 0 else "SHORT" if move < 0 else None
    most, greens, reds, net_body = majority(candles, candle_no, rule.majority_by, rule.dir_rule, prev_close)
    base = {"symbol": symbol, "day": day.isoformat(), "candle_no": candle_no, "greens": greens, "reds": reds}
    if rule.side_rule == "MAJORITY":
        if most is None:
            return {**base, "side": "TIE", "skipped": "no majority"}
        side = most
    else:
        if colour is None:
            return {**base, "side": "DOJI", "skipped": "doji" if rule.dir_rule == "COLOUR" else "no direction"}
        side = colour
        if rule.side_rule == "AGREE" and most != colour:
            return {**base, "side": side, "skipped": "against the majority"}
    body_pct = abs(move if rule.dir_rule == "CLOSES" else c - o) / o * 100
    if rule.min_body_pct > 0 and body_pct < rule.min_body_pct:
        return {**base, "side": side, "skipped": "small body", "body_pct": round(body_pct, 3)}
    entry_ts = int(nxt["ts"])
    entry = float(nxt["open"])
    sign = 1.0 if side == "LONG" else -1.0

    # Volume vs usual at the signal candle's close (Groww's volume is the running session total).
    known = tape[tape["ts"].astype("int64") < entry_ts].copy()
    known["_day"] = _day_of(known["ts"])
    todays = known[known["_day"] == day]
    earlier = known[known["_day"] < day]
    cum = pd.to_numeric(todays["volume"], errors="coerce").dropna()
    shares = float(cum.iloc[-1]) if not cum.empty else 0.0
    minutes_in = (entry_ts - int(dt.datetime.combine(day, SESSION_OPEN, tzinfo=IST).timestamp())) / 60
    usual = [v for _, g in earlier.groupby("_day") if (v := _cum_at(g, minutes_in))]
    rvol = shares / (sum(usual) / len(usual)) if len(usual) >= MIN_SESSIONS and sum(usual) > 0 else None
    prev_close = float(earlier["close"].iloc[-1]) if not earlier.empty else None
    change_pct = (c - prev_close) / prev_close * 100 if prev_close else None

    ts = today["ts"].to_numpy(dtype="int64")
    high = today["high"].to_numpy(dtype=float)
    low = today["low"].to_numpy(dtype=float)
    opn = today["open"].to_numpy(dtype=float)
    cls = today["close"].to_numpy(dtype=float)
    m0 = int(np.searchsorted(ts, entry_ts, side="left"))
    sq_min = rule.square_off.hour * 60 + rule.square_off.minute
    m_sq = m0
    while m_sq < len(ts) and ((ts[m_sq] + 19800) % 86400) // 60 < sq_min:
        m_sq += 1
    if m0 >= m_sq:
        return None
    if m_sq < len(ts):
        close_ts, close_price = int(ts[m_sq]), float(opn[m_sq])
    else:
        close_ts, close_price = int(ts[m_sq - 1]) + 60, float(cls[m_sq - 1])
    fav = (high[m0:m_sq] - entry) if side == "LONG" else (entry - low[m0:m_sq])
    adv = (entry - low[m0:m_sq]) if side == "LONG" else (high[m0:m_sq] - entry)
    b = int(np.argmax(fav))
    w = int(np.argmax(adv))
    best_pts = float(fav[b])
    worst_pts = float(adv[w])
    hit_idx = int(np.argmax(fav >= rule.target_points)) if bool((fav >= rule.target_points).any()) else None
    hit = hit_idx is not None
    against_before = float(adv[: hit_idx + 1].max()) if hit else None
    close_pts = sign * (close_price - entry)
    trail = {str(t): round(_trail_points(side, entry, high, low, m0, m_sq, t, close_pts), 2) for t in rule.trails}
    minutes_from_entry = (ts[m0:m_sq] - entry_ts) // 60
    fav_min = {str(lv): int(minutes_from_entry[int(np.argmax(fav >= lv))]) if bool((fav >= lv).any()) else None for lv in LEVELS}
    adv_min = {str(lv): int(minutes_from_entry[int(np.argmax(adv >= lv))]) if bool((adv >= lv).any()) else None for lv in LEVELS}
    return {
        "symbol": symbol,
        "day": day.isoformat(),
        "candle_no": candle_no,
        "side": side,
        "greens": greens,
        "reds": reds,
        "net_body": round(net_body, 2),
        "rvol": round(rvol, 2) if rvol is not None else None,
        "change_pct": round(change_pct, 3) if change_pct is not None else None,
        "body_pct": round(body_pct, 3),
        "signal_at": _hhmm(int(sig["ts"])),
        "entry_at": _hhmm(entry_ts),
        "entry": round(entry, 2),
        "best_pts": round(best_pts, 2),
        "best_pct": round(best_pts / entry * 100, 3),
        "best_at": _hhmm(int(ts[m0 + b])),
        "worst_pts": round(worst_pts, 2),
        "worst_pct": round(worst_pts / entry * 100, 3),
        "worst_at": _hhmm(int(ts[m0 + w])),
        "hit": hit,
        "target_at": _hhmm(int(ts[m0 + hit_idx])) if hit else None,
        "minutes_to_target": int((ts[m0 + hit_idx] - entry_ts) // 60) if hit else None,
        "against_before_target": round(against_before, 2) if hit else None,
        "close_pts": round(close_pts, 2),
        "close_pct": round(close_pts / entry * 100, 3),
        "close_at": _hhmm(close_ts),
        "trail": trail,
        "fav_min": fav_min,
        "adv_min": adv_min,
        "rev": _reverse_legs(side, entry, ts, high, low, opn, cls, m0, m_sq, adv),
    }


def analyze_stock(symbol: str, tape: pd.DataFrame | None, days: list[dt.date], rule: Rule) -> list[dict]:
    if tape is None or getattr(tape, "empty", True):
        return []
    tape = tape.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    out = []
    for day in days:
        for no in rule.candle_nos or (rule.candle_no,):
            row = analyze_day(symbol, tape, day, rule, candle_no=no)
            if row is not None:
                out.append(row)
    return out


def rank_days(rows: list[dict]) -> list[dict]:
    """Each day's trades ranked by volume vs usual (1 = the most), as the card sorted by it would show them; with several
    candles, each candle is ranked on its own (the volume at its own close)."""
    by_day: dict[tuple, list[dict]] = {}
    for r in rows:
        if r.get("skipped"):
            continue
        by_day.setdefault((r["day"], r.get("candle_no")), []).append(r)
    for day_rows in by_day.values():
        day_rows.sort(key=lambda r: -(r.get("rvol") or 0.0))
        for i, r in enumerate(day_rows, start=1):
            r["rank"] = i
            r["candidates"] = len(day_rows)
    return rows


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _group(rows: list[dict], rule: Rule) -> dict:
    n = len(rows)
    hits = [r for r in rows if r["hit"]]
    close = [r["close_pts"] for r in rows]
    return {
        "trades": n,
        "hits": len(hits),
        "hit_pct": round(len(hits) / n * 100, 1) if n else 0.0,
        "avg_best": _mean([r["best_pts"] for r in rows]),
        "median_best": round(statistics.median([r["best_pts"] for r in rows]), 2) if rows else None,
        "avg_worst": _mean([r["worst_pts"] for r in rows]),
        "avg_against_before_target": _mean([r["against_before_target"] for r in hits]),
        "max_against_before_target": round(max((r["against_before_target"] for r in hits), default=0.0), 2),
        "avg_minutes_to_target": _mean([r["minutes_to_target"] for r in hits]),
        "avg_close": _mean(close),
        "close_win_pct": round(sum(1 for v in close if v > 0) / n * 100, 1) if n else 0.0,
        "total_close": round(sum(close), 2),
        "went_against_target_pct": round(sum(1 for r in rows if r["worst_pts"] >= rule.target_points) / n * 100, 1) if n else 0.0,
    }


def _trail_table(rows: list[dict], rule: Rule) -> list[dict]:
    out = []
    for t in rule.trails:
        key = str(t)
        pts = [r["trail"][key] for r in rows if key in r.get("trail", {})]
        if not pts:
            continue
        out.append(
            {
                "pct": t,
                "total": round(sum(pts), 2),
                "avg": _mean(pts),
                "win_pct": round(sum(1 for v in pts if v > 0) / len(pts) * 100, 1),
                "worst": round(min(pts), 2),
                "best": round(max(pts), 2),
                "trades": len(pts),
            }
        )
    return out


def _nearest_level(value: float) -> float:
    return min(LEVELS, key=lambda lv: (abs(lv - value), lv))


def target_stop_outcome(row: dict, target: float, stop: float) -> tuple[str, float]:
    """("target" | "stop" | "close", ₹ per share) for a fixed target and stop, from the minutes each level was reached
    (the nearest grid level to each). The same minute for both counts as the stop (the cautious reading)."""
    t_lv, s_lv = _nearest_level(target), _nearest_level(stop)
    t_min = (row.get("fav_min") or {}).get(str(t_lv))
    s_min = (row.get("adv_min") or {}).get(str(s_lv))
    if t_min is not None and (s_min is None or t_min < s_min):
        return "target", float(t_lv)
    if s_min is not None:
        return "stop", -float(s_lv)
    return "close", float(row.get("close_pts") or 0.0)


def target_stop_table(rows: list[dict], target: float, stops: tuple[float, ...] = DEFAULT_STOPS) -> list[dict]:
    """For the target and each stop: how many trades hit the target first, the stop first, or neither, and the total."""
    out = []
    for stop in stops:
        results = [target_stop_outcome(r, target, stop) for r in rows]
        if not results:
            continue
        pts = [p for _, p in results]
        out.append(
            {
                "target": _nearest_level(target),
                "stop": _nearest_level(stop),
                "trades": len(results),
                "hit_target": sum(1 for k, _ in results if k == "target"),
                "hit_stop": sum(1 for k, _ in results if k == "stop"),
                "neither": sum(1 for k, _ in results if k == "close"),
                "total": round(sum(pts), 2),
                "avg": _mean(pts),
                "win_pct": round(sum(1 for v in pts if v > 0) / len(pts) * 100, 1),
            }
        )
    return out


def _half_hour(hhmm: str | None) -> str | None:
    if not hhmm:
        return None
    h, m = hhmm.split(":")
    minutes = int(h) * 60 + int(m)
    label = None
    for slot in HALF_HOURS:
        sh, sm = slot.split(":")
        if minutes >= int(sh) * 60 + int(sm):
            label = slot
    return label


def by_half_hour(rows: list[dict]) -> list[dict]:
    """When the target was reached and when the best price came, counted per half hour of the day."""
    out = []
    for slot in HALF_HOURS:
        target = sum(1 for r in rows if r.get("hit") and _half_hour(r.get("target_at")) == slot)
        best = sum(1 for r in rows if _half_hour(r.get("best_at")) == slot)
        worst = sum(1 for r in rows if _half_hour(r.get("worst_at")) == slot)
        if target or best or worst:
            out.append({"slot": slot, "target": target, "best": best, "worst": worst})
    return out


def summarize(rows: list[dict], rule: Rule) -> dict:
    """The report over the ranked rows inside the top N (every ranked row when top_n is 0)."""
    skipped = [r for r in rows if r.get("skipped")]
    ranked = [r for r in rows if not r.get("skipped") and "rank" in r]
    kept = [r for r in ranked if rule.top_n == 0 or r["rank"] <= rule.top_n]
    all_g = _group(kept, rule)
    sides = {s: _group([r for r in kept if r["side"] == s], rule) for s in ("LONG", "SHORT")}
    buckets = []
    for lo, hi, label in RANK_BUCKETS:
        part = [r for r in ranked if lo <= r["rank"] <= hi]
        if part:
            buckets.append({"label": label, **_group(part, rule)})
    days: dict[str, list[dict]] = {}
    for r in kept:
        days.setdefault(r["day"], []).append(r)
    by_day = [{"day": d, **_group(part, rule)} for d, part in sorted(days.items())]
    trail = _trail_table(kept, rule)
    best_trail = max(trail, key=lambda t: t["total"]) if trail else None
    target_stop = target_stop_table(kept, rule.target_points)
    best_stop = max(target_stop, key=lambda t: t["total"]) if target_stop else None
    stop_reverse = stop_reverse_table(kept, rule.target_points)
    best_reverse = max(stop_reverse, key=lambda t: t["total"]) if stop_reverse else None
    nos = list(rule.candle_nos or (rule.candle_no,))
    by_candle = [{"candle_no": no, **_group([r for r in kept if r.get("candle_no", rule.candle_no) == no], rule)} for no in nos]
    hold = all_g["total_close"]
    skip_counts: dict[str, int] = {}
    for r in skipped:
        skip_counts[r["skipped"]] = skip_counts.get(r["skipped"], 0) + 1
    answer: list[str] = []
    if rule.ha:
        answer.append("Candles read as Heikin Ashi (colour and body from the smoothed candle; prices stay real).")
    if rule.dir_rule != "COLOUR":
        answer.append(
            "A candle counts as up / down by "
            + ("its close against the previous candle's close" if rule.dir_rule == "CLOSES" else "colour and close both (green and a higher close, red and a lower one)")
            + "."
        )
    if rule.side_rule != "NTH" and skip_counts:
        how = "most candles" if rule.side_rule == "MAJORITY" else "the candle agreeing with most candles"
        answer.append(
            "Side from " + how + f" ({'green vs red count' if rule.majority_by == 'COUNT' else 'bodies added up'}): skipped "
            + ", ".join(f"{n} {why}" for why, n in sorted(skip_counts.items())) + "."
        )
    if kept:
        answer.append(
            f"{all_g['hits']} of {all_g['trades']} trades ({all_g['hit_pct']}%) reached ₹{rule.target_points:g} a share; "
            f"on average the price went ₹{all_g['avg_best']} with the trade and ₹{all_g['avg_worst']} against it."
        )
        if all_g["hits"]:
            answer.append(
                f"Before reaching the target the trades went against you ₹{all_g['avg_against_before_target']} on average and "
                f"₹{all_g['max_against_before_target']} at most, after about {all_g['avg_minutes_to_target']:g} minutes: a stop "
                f"nearer than that cuts a winner."
            )
        answer.append(
            f"Held to the square-off: ₹{hold} a share in all, {all_g['close_win_pct']}% of trades closed in profit."
        )
        if best_trail:
            answer.append(
                f"Best trailing stop: {best_trail['pct']:g}% of the entry behind the best price, ₹{best_trail['total']} a share in all "
                f"({best_trail['win_pct']}% winners, worst trade ₹{best_trail['worst']}), against ₹{hold} holding to the square-off."
            )
        if best_stop:
            answer.append(
                f"Target ₹{best_stop['target']:g} with a stop: the best stop is ₹{best_stop['stop']:g} a share ({best_stop['hit_target']} hit the target "
                f"first, {best_stop['hit_stop']} the stop, {best_stop['neither']} neither), ₹{best_stop['total']} a share in all."
            )
        if best_reverse and best_reverse["stopped"]:
            answer.append(
                f"Stop and reverse (target ₹{best_reverse['target']:g}, stop ₹{best_reverse['stop']:g}): {best_reverse['stopped']} trades stopped out, "
                f"the reverse reached the target on {best_reverse['rev_target']} and stopped on {best_reverse['rev_stop']} "
                f"(best ₹{best_reverse['rev_avg_best']} on average); ₹{best_reverse['total']} a share in all with the reverse, "
                f"against ₹{best_reverse['plain_total']} without it."
            )
        if len(nos) > 1:
            for g in by_candle:
                if g["trades"]:
                    answer.append(
                        f"Candle {g['candle_no']}: {g['hits']} of {g['trades']} reached the target ({g['hit_pct']}%), ₹{g['total_close']} a share held to the close."
                    )
        for s in ("LONG", "SHORT"):
            g = sides[s]
            if g["trades"]:
                answer.append(f"{'Green candle (buy)' if s == 'LONG' else 'Red candle (sell)'}: {g['hits']} of {g['trades']} reached the target, ₹{g['total_close']} a share held to the close.")
        if len(by_day) < 5:
            answer.append(f"Only {len(by_day)} day(s): run it on 10-20 days before trusting a percentage.")
    else:
        answer.append("No trades: no stock had a readable candle on those days, or every one was skipped.")
    return {
        "rule": rule.view(),
        "trades": len(kept),
        "ranked": len(ranked),
        "skipped": len(skipped),
        "stocks": len({r["symbol"] for r in kept}),
        "days": len(by_day),
        "all": all_g,
        "by_side": sides,
        "by_rank": buckets,
        "by_day": by_day,
        "trail": trail,
        "best_trail": best_trail,
        "target_stop": target_stop,
        "best_stop": best_stop,
        "stop_reverse": stop_reverse,
        "best_reverse": best_reverse,
        "by_candle": by_candle,
        "skipped_by": skip_counts,
        "reverse_stops": list(REVERSE_STOPS),
        "by_half_hour": by_half_hour(kept),
        "levels": list(LEVELS),
        "stops": list(DEFAULT_STOPS),
        "answer": answer,
    }


def by_stock(rows: list[dict], rule: Rule) -> list[dict]:
    """Each stock across its days inside the top N: how often it reached the target and what it made."""
    kept = [r for r in rows if not r.get("skipped") and "rank" in r and (rule.top_n == 0 or r["rank"] <= rule.top_n)]
    stocks: dict[str, list[dict]] = {}
    for r in kept:
        stocks.setdefault(r["symbol"], []).append(r)
    out = []
    for symbol, part in stocks.items():
        g = _group(part, rule)
        best_trail = max(_trail_table(part, rule), key=lambda t: t["total"], default=None)
        out.append({"symbol": symbol, **g, "best_trail_pct": best_trail["pct"] if best_trail else None, "best_trail_total": best_trail["total"] if best_trail else None})
    out.sort(key=lambda s: (-s["hit_pct"], -s["total_close"]))
    return out


def report_path() -> Path:
    explicit = os.environ.get("SECOND_CANDLE_FILE", "").strip()
    if explicit:
        return Path(explicit)
    try:
        from database import database_url

        url = database_url()
    except Exception:  # noqa: BLE001
        url = ""
    if url.startswith("sqlite:///"):
        return Path(url[len("sqlite:///") :]).with_name("second_candle.json")
    return Path("second_candle.json")


class SecondCandle:
    """One test at a time: read each stock's days (cached candles), rank each day, summarize. Memory plus disk."""

    def __init__(self) -> None:
        self.status = "IDLE"
        self.done = 0
        self.total = 0
        self.errors: list[dict] = []
        self.params: dict = {}
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.report: dict | None = None
        self.rows: list[dict] = []
        self.stocks: list[dict] = []
        self._task: asyncio.Task | None = None
        self._stop = False
        self._rows_json: bytes | None = None
        self._rows_key: tuple | None = None
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(report_path().read_text())
        except (OSError, ValueError):
            return
        if isinstance(raw, dict) and raw.get("report"):
            self.report = raw.get("report")
            self.rows = raw.get("rows") or []
            self.stocks = raw.get("stocks") or []
            self.params = raw.get("params") or {}
            self.finished_at = raw.get("finished_at")
            self.status = "DONE"

    def _save(self) -> None:
        try:
            report_path().write_text(
                json.dumps({"report": self.report, "rows": self.rows, "stocks": self.stocks, "params": self.params, "finished_at": self.finished_at})
            )
        except OSError as exc:
            log.warning("could not save the second candle test: %s", exc)

    @property
    def running(self) -> bool:
        return self.status == "RUNNING"

    def view(self, with_rows: bool = True) -> dict:
        return {
            "status": self.status,
            "done": self.done,
            "total": self.total,
            "errors": self.errors[-20:],
            "params": self.params,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "report": self.report,
            "stocks": self.stocks,
            "rows": self.rows if with_rows else [],
            "sizes": list(SIZES),
            "default_trails": list(DEFAULT_TRAILS),
        }

    async def view_json(self, with_rows: bool = True) -> bytes:
        """The view as JSON bytes. The rows of a finished run can be tens of MB, so the encoding runs in a worker
        thread and is kept until the next run finishes: a page polling the test never stalls the shared event loop."""
        if not with_rows or not self.rows:
            return json.dumps(self.view(with_rows=False), default=str).encode()
        key = (self.finished_at, len(self.rows))
        if self._rows_key != key or self._rows_json is None:
            rows = self.rows
            self._rows_json = await asyncio.to_thread(lambda: json.dumps(rows, default=str).encode())
            self._rows_key = key
        head = json.dumps(self.view(with_rows=False), default=str).encode()
        # Splice the cached rows into the view: {..., "rows": []} → {..., "rows": <cached>}.
        marker = b'"rows": []'
        at = head.rfind(marker)
        if at < 0:
            return json.dumps(self.view(with_rows=True), default=str).encode()
        return head[:at] + b'"rows": ' + self._rows_json + head[at + len(marker):]

    def start(self, symbols: list[str], fetch, days: int, rule: Rule, wait=None) -> dict:
        if self.running:
            raise RuntimeError("A second candle test is already running.")
        names = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))[:MAX_STOCKS]
        if not names:
            raise ValueError("Pick at least one stock.")
        days = max(MIN_DAYS, min(MAX_DAYS, int(days)))
        rule = rule.clean()
        self.status = "RUNNING"
        self.done, self.total = 0, len(names)
        self.errors = []
        self._stop = False
        self.params = {"symbols": len(names), "days": days, **rule.view()}
        self.started_at = dt.datetime.now(IST).isoformat(timespec="seconds")
        self.finished_at = None
        self._task = asyncio.create_task(self._run(names, fetch, days, rule, wait))
        return self.view(with_rows=False)

    def stop(self) -> dict:
        self._stop = True
        return self.view(with_rows=False)

    async def _run(self, names: list[str], fetch, days: int, rule: Rule, wait) -> None:
        today = dt.datetime.now(IST).date()
        day_list = _last_trading_days(today, days)
        first = day_list[0] - dt.timedelta(days=WARMUP_DAYS)
        last = day_list[-1]
        rows: list[dict] = []
        finished = False
        try:
            for name in names:
                if self._stop:
                    break
                try:
                    if wait is not None:
                        await wait()
                    tape = await load_tape(name, first, last, fetch, today=today)
                    rows.extend(await asyncio.to_thread(analyze_stock, name, tape, day_list, rule))
                except Exception as exc:  # noqa: BLE001 - one stock never stops the test
                    self.errors.append({"symbol": name, "error": str(exc)[:200]})
                self.done += 1
                await asyncio.sleep(0)
            rank_days(rows)
            self.rows = rows
            self.report = await asyncio.to_thread(summarize, rows, rule)
            self.stocks = await asyncio.to_thread(by_stock, rows, rule)
            self.report["stopped"] = self._stop
            finished = True
        except Exception as exc:  # noqa: BLE001
            log.exception("second candle test failed")
            self.errors.append({"symbol": "", "error": str(exc)[:200]})
            self.status = "FAILED"
        finally:
            self.finished_at = dt.datetime.now(IST).isoformat(timespec="seconds")
            if finished:
                # DONE only once the report is on disk: a fresh instance (a restart, the next poll) that sees the run
                # finished must also find its saved report. Set in a finally, so a cancelled save never leaves RUNNING.
                try:
                    await asyncio.to_thread(self._save)
                finally:
                    self.status = "DONE"


def _last_trading_days(today: dt.date, count: int) -> list[dt.date]:
    """The last `count` weekdays before today (a holiday simply has no candles)."""
    out: list[dt.date] = []
    day = today
    while len(out) < count:
        day -= dt.timedelta(days=1)
        if day.weekday() < 5:
            out.append(day)
    return sorted(out)


_TEST: SecondCandle | None = None


def test() -> SecondCandle:
    global _TEST
    if _TEST is None:
        _TEST = SecondCandle()
    return _TEST
