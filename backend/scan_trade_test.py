"""Trade a saved Most active scan (practice arithmetic on real candles; read-only, no order, nothing armed).

The owner's question: on that day, at that scan's time, would the stocks the scan pointed at really have made money?
One saved Most active scan (`scan_history`, kind `active`) gives the time and the stocks. Each picked stock is entered
at the open of the first 1-minute candle after the scan, its way (`picks`), and followed on that day's real Groww
1-minute candles three ways (`simulate`):

* **Normal candles**: out on the first closed candle of `minutes` of the other colour (a doji is not a turn);
* **Heikin Ashi candles**: the same on the Heikin Ashi colours (`heikin_ashi.candles`); prices stay real;
* **Hold**: out at the square-off time.

Whichever comes first, the square-off closes the trade. Only candles that start after the entry are judged (a candle
bought partway into is not counted), and the exit is the open of the minute after that candle closes. P&L is before
charges: points × qty.

**Which stocks, which way** (`pick`): `PRESSURE` = buyers at least `pct` % of the waiting orders buys, sellers at least
that sells; `BIAS` = the row's bias (LONG buys, SHORT sells); `MOVE` = up on the day buys, down sells. The busiest
`top_n` by money traded are taken. A bot's own strategy on the same stocks and start time is the replay
(`/api/replay/start` with `symbols`, `start`, `bot`), started from the same screen.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

import heikin_ashi
from candles import resample

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
PICKS = ("PRESSURE", "BIAS", "MOVE")
MAX_STOCKS = 40
MINUTES = (1, 3, 5, 10, 15)
WAYS = ("normal", "ha", "hold")
WAY_LABEL = {"normal": "Normal candles", "ha": "Heikin Ashi candles", "hold": "Hold to square-off"}


def _f(v) -> float | None:
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def picks(rows: list[dict], pick: str, pct: float, top_n: int) -> list[dict]:
    """The scan's stocks to trade and their side, busiest first."""
    need = pct / 100.0
    out = []
    for r in rows or []:
        symbol, price = r.get("symbol"), _f(r.get("ltp"))
        if not symbol or not price:
            continue
        share, why = _f(r.get("buy_share")), ""
        side = None
        if pick == "PRESSURE":
            if share is not None and share >= need:
                side, why = "LONG", f"buyers {share * 100:.0f}%"
            elif share is not None and 1.0 - share >= need:
                side, why = "SHORT", f"sellers {(1.0 - share) * 100:.0f}%"
        elif pick == "BIAS":
            if r.get("bias") in ("LONG", "SHORT"):
                side, why = r["bias"], f"bias {str(r['bias']).lower()}"
        else:
            move = _f(r.get("change_pct"))
            if move:
                side, why = ("LONG" if move > 0 else "SHORT"), f"day {move:+.2f}%"
        if side:
            out.append({"symbol": symbol, "side": side, "why": why, "scan_price": price, "value_cr": _f(r.get("value_cr")) or 0.0,
                        "buy_share": share})
    out.sort(key=lambda p: -p["value_cr"])
    return out[: max(1, min(MAX_STOCKS, int(top_n)))]


def _clock(ts: int) -> str:
    return dt.datetime.fromtimestamp(int(ts), IST).strftime("%H:%M")


def simulate(tape: pd.DataFrame | None, at: dt.datetime, side: str, minutes: int, square_off: dt.time) -> dict | None:
    """One stock's three results from `at` on that day's 1-minute candles. None when the day has no candle to enter on."""
    if tape is None or getattr(tape, "empty", True):
        return None
    day = at.astimezone(IST).date()
    start = int(dt.datetime.combine(day, dt.time(9, 15), tzinfo=IST).timestamp())
    end = int(dt.datetime.combine(day, dt.time(15, 30), tzinfo=IST).timestamp())
    frame = tape[(tape["ts"] >= start) & (tape["ts"] < end)].sort_values("ts").reset_index(drop=True)
    if frame.empty:
        return None
    ts = frame["ts"].to_numpy(dtype="int64")
    opens = frame["open"].to_numpy(dtype=float)
    highs = frame["high"].to_numpy(dtype=float)
    lows = frame["low"].to_numpy(dtype=float)
    closes = frame["close"].to_numpy(dtype=float)
    sq = int(dt.datetime.combine(day, square_off, tzinfo=IST).timestamp())
    # In at the open of the first minute that starts after the scan was taken.
    i = int(np.searchsorted(ts, int(at.timestamp()) + 1, side="left"))
    if i >= len(ts) or ts[i] >= sq:
        return None
    entry, entry_ts, sign = float(opens[i]), int(ts[i]), (1 if side == "LONG" else -1)
    k = int(np.searchsorted(ts, sq, side="left"))  # first minute at or after the square-off
    last = min(k, len(ts) - 1)
    sq_price = float(opens[k]) if k < len(ts) else float(closes[-1])
    sq_ts = int(ts[k]) if k < len(ts) else int(ts[-1]) + 60

    def result(exit_price: float, exit_ts: int, reason: str, candles: int) -> dict:
        j = int(np.searchsorted(ts, exit_ts, side="left"))
        hi = float(highs[i:max(j, i + 1)].max())
        lo = float(lows[i:max(j, i + 1)].min())
        best = (hi - entry) if sign > 0 else (entry - lo)
        worst = (lo - entry) if sign > 0 else (entry - hi)
        points = (exit_price - entry) * sign
        return {
            "exit": round(exit_price, 2), "exit_at": _clock(exit_ts), "reason": reason, "candles": candles,
            "points": round(points, 2), "pct": round(points / entry * 100, 3),
            "best_points": round(best, 2), "worst_points": round(worst, 2),
            "minutes_held": max(0, int((exit_ts - entry_ts) // 60)),
        }

    bars = resample(frame, minutes) if minutes > 1 else frame
    b_ts = bars["ts"].to_numpy(dtype="int64")
    b_open = bars["open"].to_numpy(dtype=float)
    b_close = bars["close"].to_numpy(dtype=float)
    ha_open, ha_close = heikin_ashi.candles(bars)
    span = minutes * 60

    def colour_exit(o: np.ndarray, c: np.ndarray) -> dict:
        held = 0
        for n in range(len(b_ts)):
            closes_at = int(b_ts[n]) + span
            if b_ts[n] < entry_ts or closes_at > sq:
                continue
            held += 1
            against = (c[n] < o[n]) if sign > 0 else (c[n] > o[n])
            if against:
                j = int(np.searchsorted(ts, closes_at, side="left"))
                if j < len(ts) and ts[j] < sq:
                    return result(float(opens[j]), int(ts[j]), "candle turned", held)
                break
        return result(sq_price, sq_ts, "square-off", held)

    return {
        "entry": round(entry, 2), "entry_at": _clock(entry_ts),
        "normal": colour_exit(b_open, b_close),
        "ha": colour_exit(ha_open.to_numpy(dtype=float), ha_close.to_numpy(dtype=float)),
        "hold": result(sq_price, sq_ts, "square-off", 0),
        "last_minute": _clock(int(ts[last])),
    }


def summarize(rows: list[dict], qty: int) -> dict:
    """Totals per way of trading, the best one, and the answer in plain words."""
    done = [r for r in rows if r.get("entry") is not None]
    ways = []
    for way in WAYS:
        got = [r[way] for r in done]
        points = [g["points"] for g in got]
        rupees = [p * qty for p in points]
        ways.append({
            "way": way, "label": WAY_LABEL[way], "trades": len(got),
            "winners": sum(p > 0 for p in points), "losers": sum(p < 0 for p in points),
            "win_pct": round(sum(p > 0 for p in points) / len(points) * 100, 1) if points else None,
            "rupees": round(sum(rupees), 2), "avg_pct": round(sum(g["pct"] for g in got) / len(got), 3) if got else None,
            "best_rupees": round(max(rupees), 2) if rupees else None, "worst_rupees": round(min(rupees), 2) if rupees else None,
            "avg_minutes": round(sum(g["minutes_held"] for g in got) / len(got)) if got else None,
        })
    best = max(ways, key=lambda w: w["rupees"]) if done else None
    answer = []
    if not done:
        answer.append("No stock could be traded: the scan picked none your way, or its candles could not be read.")
    else:
        for w in ways:
            answer.append(
                f"{w['label']}: {'made' if w['rupees'] >= 0 else 'lost'} ₹{abs(w['rupees']):,.0f} on {w['trades']} trades of {qty} shares "
                f"({w['winners']} won, {w['losers']} lost), before charges."
            )
        answer.append(f"Best of the three here: {best['label']}." if best["rupees"] > 0 else "None of the three made money on this scan.")
        answer.append("One scan on one day proves little: run several days and times before trusting it.")
    return {"ways": ways, "best": best["way"] if best else None, "answer": answer}


async def run(scan: dict, load, *, pick: str = "PRESSURE", pct: float = 75.0, top_n: int = 20, minutes: int = 5,
              square_off: dt.time = dt.time(15, 15), qty: int = 100) -> dict:
    """Trade one saved scan. `load(symbol, day)` gives that day's 1-minute candles (awaitable)."""
    at = dt.datetime.fromisoformat(scan["as_of"])
    at = (at if at.tzinfo else at.replace(tzinfo=IST)).astimezone(IST)
    chosen = picks(scan.get("rows") or [], pick, pct, top_n)
    rows, errors = [], []
    for p in chosen:
        try:
            got = simulate(await load(p["symbol"], at.date()), at, p["side"], minutes, square_off)
        except Exception as exc:  # noqa: BLE001 - one stock never stops the test
            errors.append({"symbol": p["symbol"], "error": str(exc)[:200]})
            continue
        if got is None:
            errors.append({"symbol": p["symbol"], "error": "no candle to enter on after the scan"})
            continue
        for way in WAYS:
            got[way]["rupees"] = round(got[way]["points"] * qty, 2)
        rows.append({**p, **got})
    return {
        "scan_id": scan.get("id"), "day": at.date().isoformat(), "time": at.strftime("%H:%M"), "pick": pick, "pct": pct,
        "minutes": minutes, "square_off": square_off.strftime("%H:%M"), "qty": qty, "picked": len(chosen),
        "rows": rows, "errors": errors, **summarize(rows, qty),
    }
